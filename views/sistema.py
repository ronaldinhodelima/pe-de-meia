"""Healthcheck e sincronizacao com o Pluggy."""
from flask import Blueprint, Response, jsonify, request

from core import (
    disparar_sincronizacao,
    fechar_recursos_banco,
    get_conn,
    get_ultima_sincronizacao,
    login_required,
    requer,
    topbar_html,
    vincular_pendentes_confirmados,
)

bp = Blueprint("sistema", __name__)


def _status_publico_sincronizacao(status):
    """Expoe somente os campos usados pelo widget, nunca erros internos."""
    campos = ("executado_em", "status", "transacoes_novas", "transacoes_atualizadas")
    return {campo: status.get(campo) for campo in campos if campo in status}


@bp.route("/api/sync-status")
@login_required
def api_sync_status():
    return jsonify(_status_publico_sincronizacao(get_ultima_sincronizacao()))


@bp.route("/api/sync-agora", methods=["POST"])
@requer("sincronizar")
def api_sync_agora():
    ok, _erro = disparar_sincronizacao()
    if not ok:
        return jsonify({"executado_em": None, "status": "erro"}), 502
    # o que acabou de chegar pode ser a confirmacao de um pendente (secao 4.3)
    conn = cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        vincular_pendentes_confirmados(cur)
        conn.commit()
    except Exception:
        fechar_recursos_banco(conn, cur, rollback=True)
        conn = cur = None
    finally:
        fechar_recursos_banco(conn, cur)
    return jsonify(_status_publico_sincronizacao(get_ultima_sincronizacao()))


@bp.route("/health")
def health():
    return jsonify({"status": "ok"})


# Paginas de OUTROS servicos (Node) que exibem a barra do sistema. Lista fechada, e
# o titulo nunca vem da URL: `titulo` entra no HTML sem escape, e aceitar texto
# livre aqui seria XSS refletido na origem principal.
_PAGINAS_DA_BARRA = {"compras-futuras": ("Compras futuras", "compras-futuras")}


@bp.route("/api/topbar")
@requer("lancamentos_ver")
def barra_superior():
    """A barra de menus do sistema, para uma tela servida por outro servico.

    A barra (menus, Desfazer, tema, sincronizar) e UMA so, escrita aqui: quem a
    duplicasse em React teria duas implementacoes do Desfazer e do menu por
    permissao. Vai SEM o <script>: HTML injetado por innerHTML nao executa
    script, entao o outro servico carrega /static/topbar.js por conta propria.
    """
    pagina = _PAGINAS_DA_BARRA.get(request.args.get("pagina", ""))
    if not pagina:
        return jsonify({"ok": False, "erro": "Página desconhecida."}), 404
    return Response(topbar_html(*pagina, com_script=False), mimetype="text/html")
