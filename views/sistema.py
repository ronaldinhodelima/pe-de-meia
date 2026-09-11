"""Healthcheck e sincronizacao com o Pluggy."""
from flask import Blueprint, jsonify

from core import (
    disparar_sincronizacao,
    fechar_recursos_banco,
    get_conn,
    get_ultima_sincronizacao,
    login_required,
    requer,
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
