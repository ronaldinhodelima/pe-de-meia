"""Ponto de entrada: cria o app Flask, registra os blueprints e sobe o servidor.

Quem faz o trabalho e core.py (constantes e helpers) e views/*.py (as rotas).
"""
import os
import time
from datetime import timedelta
from urllib.parse import urlsplit

from flask import Flask, current_app, jsonify, request, session, g
from werkzeug.middleware.proxy_fix import ProxyFix

from core import (
    _fmt_moeda,
    _barra_html,
    registrar_auditoria,
    rotulo_valor_dimensao,
    sanitizar_dados_auditoria,
)
from views import auth, sistema, lancamentos, relatorios, cadastros, usuarios, logs

app = Flask(__name__)

# O aplicativo so e publicado pelo Traefik do Coolify. Confiamos exatamente no
# ultimo proxy para que IP, protocolo e host usados pelos controles de seguranca
# representem o cliente real, sem aceitar uma cadeia X-Forwarded-* arbitraria.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.secret_key = os.environ["SECRET_KEY"]

# cookie de sessao so trafega por HTTPS (o Traefik do Coolify ja forca https) e
# nunca e enviado em navegacao cross-site - reduz roubo de sessao via rede ou CSRF.
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24),
    SESSION_REFRESH_EACH_REQUEST=True,
    # O PDF original fica no PostgreSQL e e processado pelo pdfplumber. Um
    # limite explicito evita consumo excessivo de memoria/CPU e crescimento
    # acidental do banco por upload incorreto.
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
)


@app.errorhandler(413)
def _arquivo_grande(_erro):
    return "Arquivo muito grande. O limite para envio é de 10 MB.", 413


@app.before_request
def _recusar_corpo_excessivo_antes_de_processar():
    """Falha cedo, antes que a rota ou a auditoria tentem ler o formulário."""
    limite = current_app.config.get("MAX_CONTENT_LENGTH")
    if limite and request.content_length and request.content_length > limite:
        return _arquivo_grande(None)
    return None


@app.route("/favicon.ico")
def favicon():
    """Atende tambem o endereco padrao consultado diretamente pelos navegadores."""
    return app.send_static_file("favicon.png")


@app.before_request
def _iniciar_auditoria_requisicao():
    # Guarda o usuario antes da rota: /logout limpa a sessao antes do after_request.
    g.audit_inicio = time.monotonic()
    g.audit_usuario = session.get("user")


@app.before_request
def _proteger_requisicoes_mutaveis():
    """Bloqueia POST/PUT/PATCH/DELETE originados fora do proprio site."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or current_app.testing:
        return None
    fonte = request.headers.get("Origin") or request.headers.get("Referer")
    if not fonte or urlsplit(fonte).netloc != request.host:
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "erro": "Requisição de origem inválida."}), 403
        return "Requisição de origem inválida.", 403
    return None


@app.after_request
def _cabecalhos_de_seguranca(response):
    """Defesa em profundidade para todas as respostas do aplicativo."""
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # O HTML atual ainda usa alguns scripts e estilos inline. Mantemos apenas
    # essas duas excecoes e bloqueamos origens externas, plugins e iframes.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    if request.path != "/health" and not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
    return response


@app.after_request
def _auditar_requisicao(response):
    """Registra acessos e mutacoes relevantes sem guardar credenciais."""
    caminho = request.path or "/"
    if (
        caminho == "/health"
        or caminho == "/favicon.ico"
        or caminho.startswith("/static/")
        or caminho == "/api/sync-status"
        or (caminho == "/login" and request.method == "GET")
        or current_app.testing
    ):
        return response

    endpoint = request.endpoint or "rota_desconhecida"
    operacao = None
    # Em 413 o corpo excedeu MAX_CONTENT_LENGTH. Tentar acessar request.form
    # de novo dentro do after_request levanta outro RequestEntityTooLarge e
    # transforma a resposta clara em erro 500. O tamanho e o caminho bastam
    # para auditar essa falha; o corpo nunca deve ser relido.
    if response.status_code == 413:
        entrada = {"content_length": request.content_length}
    elif request.form:
        operacao = request.form.get("acao")
        entrada = request.form.to_dict(flat=False)
    else:
        entrada = request.get_json(silent=True)
    if entrada is None and request.args:
        entrada = request.args.to_dict(flat=False)

    if endpoint == "auth.login":
        acao = "autenticacao"
    elif endpoint == "auth.logout":
        acao = "saida"
    elif endpoint == "sistema.api_sync_agora":
        acao = "sincronizacao_solicitada"
    elif request.method == "DELETE" or str(operacao or "").lower() in {"excluir", "remover"}:
        acao = "exclusao"
    elif request.method in {"POST", "PUT", "PATCH"}:
        acao = "alteracao"
    else:
        acao = "acesso"

    view_args = request.view_args or {}
    recurso_id = next(iter(view_args.values()), None)
    usuario = getattr(g, "audit_usuario", None) or session.get("user")
    if endpoint == "auth.login" and not usuario and response.status_code != 413:
        usuario = (request.form.get("usuario") or "").strip() or None
    inicio = getattr(g, "audit_inicio", None)
    duracao_ms = round((time.monotonic() - inicio) * 1000, 1) if inicio else None
    sucesso = response.status_code < 400 and getattr(g, "audit_sucesso", True)
    # Login invalido devolve a mesma tela (HTTP 200), mas deve aparecer como
    # falha no historico. A sessao so contem usuario quando a autenticacao passou.
    if endpoint == "auth.login":
        sucesso = bool(session.get("user")) and sucesso
    registrar_auditoria(
        acao,
        endpoint,
        usuario=usuario,
        recurso_id=recurso_id,
        status_http=response.status_code,
        sucesso=sucesso,
        detalhes={
            "operacao": operacao,
            "entrada": sanitizar_dados_auditoria(entrada or {}),
            "alteracoes": sanitizar_dados_auditoria(
                getattr(g, "audit_alteracoes", {})
            ),
            "duracao_ms": duracao_ms,
        },
    )
    return response


# ---- filtros e globais usados pelos templates ----
@app.template_filter("moeda")
def _filtro_moeda(v):
    return _fmt_moeda(float(v or 0))


@app.template_filter("num")
def _filtro_num(v):
    """Numero sem casas decimais desnecessarias; vazio quando nao ha valor.
    Usado em campo de formulario, onde None tem que virar string vazia."""
    if v is None or v == "":
        return ""
    return f"{float(v):g}"


@app.context_processor
def _globais_template():
    # disponiveis em qualquer template, sem cada view precisar passar
    return {"barra": _barra_html, "rotulo_dim": rotulo_valor_dimensao}


# a ordem nao importa: nenhum blueprint disputa o mesmo caminho
for modulo in (auth, sistema, lancamentos, relatorios, cadastros, usuarios, logs):
    app.register_blueprint(modulo.bp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
