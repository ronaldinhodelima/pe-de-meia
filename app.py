"""Ponto de entrada: cria o app Flask, registra os blueprints e sobe o servidor.

Quem faz o trabalho e core.py (constantes e helpers) e views/*.py (as rotas).
"""
import os
from datetime import timedelta
from urllib.parse import urlsplit

from flask import Flask, current_app, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from core import _fmt_moeda, _barra_html, rotulo_valor_dimensao
from views import auth, sistema, lancamentos, relatorios, cadastros, usuarios

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
)


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
for modulo in (auth, sistema, lancamentos, relatorios, cadastros, usuarios):
    app.register_blueprint(modulo.bp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
