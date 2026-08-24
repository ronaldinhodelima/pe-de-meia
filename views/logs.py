"""Consulta administrativa do historico de auditoria."""
import json
from datetime import datetime
from urllib.parse import urlencode

import psycopg2.extras
from flask import Blueprint, request, render_template

from core import data_hora_local, get_conn, requer, topbar_html

bp = Blueprint("logs", __name__)

ROTULOS_ACAO = {
    "acesso": "Acesso",
    "autenticacao": "Autenticação",
    "saida": "Saída",
    "alteracao": "Alteração",
    "exclusao": "Exclusão",
    "sincronizacao": "Sincronização",
    "sincronizacao_solicitada": "Sincronização solicitada",
    "regra_automatica": "Regra automática",
}


@bp.route("/logs")
@requer("usuarios")
def logs_view():
    acao = (request.args.get("acao") or "").strip()
    usuario = (request.args.get("usuario") or "").strip()
    resultado = (request.args.get("resultado") or "").strip()
    busca = (request.args.get("busca") or "").strip()[:120]
    data_ini = (request.args.get("data_ini") or "").strip()
    data_fim = (request.args.get("data_fim") or "").strip()
    try:
        pagina = max(int(request.args.get("pagina") or 1), 1)
    except ValueError:
        pagina = 1

    def data_valida(valor):
        if not valor:
            return ""
        try:
            datetime.strptime(valor, "%Y-%m-%d")
            return valor
        except ValueError:
            return ""

    data_ini = data_valida(data_ini)
    data_fim = data_valida(data_fim)
    where = ["true"]
    params = []
    if acao:
        where.append("acao = %s")
        params.append(acao)
    if usuario:
        where.append("COALESCE(usuario, '') = %s")
        params.append(usuario)
    if resultado == "sucesso":
        where.append("sucesso = true")
    elif resultado == "falha":
        where.append("sucesso = false")
    if data_ini:
        where.append("(ocorrido_em AT TIME ZONE 'America/Sao_Paulo')::date >= %s::date")
        params.append(data_ini)
    if data_fim:
        where.append("(ocorrido_em AT TIME ZONE 'America/Sao_Paulo')::date <= %s::date")
        params.append(data_fim)
    if busca:
        where.append(
            "(COALESCE(recurso,'') ILIKE %s OR COALESCE(recurso_id,'') ILIKE %s "
            "OR COALESCE(rota,'') ILIKE %s OR detalhes::text ILIKE %s)"
        )
        params.extend([f"%{busca}%"] * 4)
    where_sql = " AND ".join(where)

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT DISTINCT acao FROM cartao.audit_log ORDER BY acao;")
    acoes_db = [r["acao"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT usuario FROM cartao.audit_log WHERE usuario IS NOT NULL ORDER BY usuario;")
    usuarios = [r["usuario"] for r in cur.fetchall()]
    cur.execute(f"SELECT COUNT(*) AS n FROM cartao.audit_log WHERE {where_sql};", params)
    total = cur.fetchone()["n"]

    por_pagina = 100
    total_paginas = max((total + por_pagina - 1) // por_pagina, 1)
    pagina = min(pagina, total_paginas)
    cur.execute(
        "SELECT id, ocorrido_em, usuario, acao, recurso, recurso_id, metodo, rota, "
        "status_http, sucesso, ip_origem, user_agent, detalhes "
        f"FROM cartao.audit_log WHERE {where_sql} "
        "ORDER BY ocorrido_em DESC, id DESC LIMIT %s OFFSET %s;",
        params + [por_pagina, (pagina - 1) * por_pagina],
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    eventos = []
    for r in rows:
        detalhes = r["detalhes"] or {}
        local = data_hora_local(r["ocorrido_em"])
        eventos.append({
            **r,
            "quando": local.strftime("%d/%m/%Y %H:%M:%S") if local else "-",
            "usuario_rotulo": r["usuario"] or "anônimo/sistema",
            "acao_rotulo": ROTULOS_ACAO.get(r["acao"], r["acao"].replace("_", " ").title()),
            "detalhes_json": json.dumps(detalhes, ensure_ascii=False, indent=2, default=str),
        })

    filtros = {
        "acao": acao,
        "usuario": usuario,
        "resultado": resultado,
        "busca": busca,
        "data_ini": data_ini,
        "data_fim": data_fim,
    }

    def url_pagina(numero):
        return "/logs?" + urlencode({**filtros, "pagina": numero})

    return render_template(
        "logs.html",
        titulo="Logs",
        topbar=topbar_html("Logs", "logs"),
        eventos=eventos,
        acoes=[{"valor": a, "rotulo": ROTULOS_ACAO.get(a, a.replace("_", " ").title())} for a in acoes_db],
        usuarios=usuarios,
        filtros=filtros,
        total=total,
        pagina=pagina,
        total_paginas=total_paginas,
        url_anterior=url_pagina(pagina - 1) if pagina > 1 else None,
        url_proxima=url_pagina(pagina + 1) if pagina < total_paginas else None,
    )
