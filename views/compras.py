"""Compras futuras (sonhos): o que se pretende comprar, com previsao de caixa.

PLANO NAO E FATO. Nada daqui entra em cartao.transacao, na view
cartao.lancamento_financeiro, no DRE ou em qualquer total de periodo - seria o
"dado mascarado que infla o lancamento" que a regra de ouro proibe (secao 1.1).
O que existe e o caminho inverso: quando a compra acontece, o item aponta o
lancamento REAL que a realizou, e ai sim aquele lancamento (e so ele) conta.
"""
from datetime import datetime

import psycopg2
import psycopg2.extras
from flask import Blueprint, jsonify, render_template, request, session

from core import (
    chave_alfa,
    get_conn,
    pode,
    registrar_auditoria,
    requer,
    rotulo_valor_dimensao,
    topbar_html,
)

bp = Blueprint("compras", __name__)

PRIORIDADES = {"alta": "Alta", "media": "Média", "baixa": "Baixa"}
SITUACOES = {"aberta": "Em aberto", "comprada": "Comprada", "cancelada": "Cancelada"}


def _mes_alvo(texto):
    """Aceita 'AAAA-MM' e guarda no primeiro dia do mes.

    O dia nao significa nada aqui - a previsao e mensal. Guardar como date (e
    nao texto) deixa a ordenacao e o agrupamento por mes corretos no banco.
    """
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return datetime.strptime(texto[:7], "%Y-%m").date()
    except ValueError:
        return None


def _carregar_dimensoes(cur):
    cur.execute("SELECT id, nome, obrigatoria FROM cartao.dimensao ORDER BY ordem, nome;")
    dimensoes = [dict(d) for d in cur.fetchall()]
    cur.execute("SELECT id, dimensao_id, nome FROM cartao.dimensao_valor ORDER BY nome;")
    valores = {}
    for v in cur.fetchall():
        valores.setdefault(v["dimensao_id"], []).append(dict(v))
    return dimensoes, valores


@bp.route("/compras-futuras")
@requer("lancamentos_ver")
def compras_futuras():
    situacao = request.args.get("situacao") or "aberta"
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    dimensoes, valores_por_dim = _carregar_dimensoes(cur)

    where = "" if situacao == "todas" else " WHERE c.situacao = %s"
    params = () if situacao == "todas" else (situacao,)
    cur.execute(
        "SELECT c.*, t.descricao AS transacao_descricao, "
        "COALESCE(t.valor_brl, t.valor_original) AS transacao_valor, "
        "COALESCE((SELECT jsonb_object_agg(cd.dimensao_id, cd.valor_id) "
        " FROM cartao.compra_futura_dimensao cd "
        " WHERE cd.compra_id = c.id AND cd.valor_id IS NOT NULL), '{}'::jsonb) AS dims "
        "FROM cartao.compra_futura c "
        "LEFT JOIN cartao.transacao t ON t.transacao_id::text = c.transacao_id"
        + where +
        " ORDER BY c.situacao, c.mes_alvo NULLS LAST, "
        " CASE c.prioridade WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END, c.id;",
        params,
    )
    linhas = [dict(r) for r in cur.fetchall()]

    nomes_valor = {v["id"]: rotulo_valor_dimensao(v)
                   for lista in valores_por_dim.values() for v in lista}
    for linha in linhas:
        dims = linha.pop("dims") or {}
        linha["dims"] = {int(k): v for k, v in dims.items()}
        linha["dims_rotulos"] = {
            int(k): nomes_valor.get(v, "-") for k, v in dims.items()
        }
        linha["prioridade_rotulo"] = PRIORIDADES.get(linha["prioridade"], linha["prioridade"])
        linha["situacao_rotulo"] = SITUACOES.get(linha["situacao"], linha["situacao"])
        linha["mes_alvo_rotulo"] = linha["mes_alvo"].strftime("%m/%Y") if linha["mes_alvo"] else "—"
        linha["mes_alvo_iso"] = linha["mes_alvo"].strftime("%Y-%m") if linha["mes_alvo"] else ""
        # O valor que vale e o real quando ele existe; o previsto continua
        # visivel ao lado para a comparacao nao se perder.
        linha["valor_vigente"] = (
            linha["valor_real"] if linha["valor_real"] is not None else linha["valor_previsto"]
        )
        linha["mostra_previsto"] = (
            linha["valor_real"] is not None and linha["valor_previsto"] is not None
            and linha["valor_real"] != linha["valor_previsto"]
        )

    # Provisionamento: o que interessa e quanto falta desembolsar, entao os
    # totais contam SO o que esta em aberto - item comprado ja virou lancamento
    # de verdade e somar de novo seria contar duas vezes.
    abertas = [l for l in linhas if l["situacao"] == "aberta"]
    total_aberto = sum(float(l["valor_previsto"] or 0) for l in abertas)
    por_mes = {}
    for linha in abertas:
        chave = linha["mes_alvo_rotulo"]
        por_mes[chave] = por_mes.get(chave, 0) + float(linha["valor_previsto"] or 0)
    cur.close()
    conn.close()

    return render_template(
        "compras_futuras.html",
        titulo="Compras futuras",
        topbar=topbar_html("Compras futuras", "compras-futuras"),
        linhas=linhas,
        dimensoes=dimensoes,
        valores_por_dim=valores_por_dim,
        prioridades=PRIORIDADES,
        situacoes=SITUACOES,
        situacao=situacao,
        total_aberto=total_aberto,
        quantidade_aberta=len(abertas),
        por_mes=sorted(por_mes.items(), key=lambda x: (x[0] == "—", x[0][3:] + x[0][:2])),
        pode_editar=pode("lancamentos_manual"),
        mes_atual=datetime.now().strftime("%Y-%m"),
    )


def _gravar_dimensoes(cur, compra_id, dimensoes):
    for dim_id, valor_id in (dimensoes or {}).items():
        if not str(valor_id or "").strip():
            cur.execute(
                "DELETE FROM cartao.compra_futura_dimensao "
                "WHERE compra_id=%s AND dimensao_id=%s;",
                (compra_id, int(dim_id)),
            )
            continue
        cur.execute(
            "INSERT INTO cartao.compra_futura_dimensao (compra_id, dimensao_id, valor_id) "
            "VALUES (%s,%s,%s) ON CONFLICT (compra_id, dimensao_id) "
            "DO UPDATE SET valor_id = EXCLUDED.valor_id;",
            (compra_id, int(dim_id), int(valor_id)),
        )


@bp.route("/api/compra-futura", methods=["POST"])
@requer("lancamentos_manual")
def criar_compra_futura():
    dados = request.get_json(force=True)
    descricao = (dados.get("descricao") or "").strip()
    if not descricao:
        return jsonify({"ok": False, "erro": "Informe o que precisa ser comprado."}), 400
    valor = dados.get("valor_previsto")
    try:
        valor = round(float(str(valor).replace(",", ".")), 2) if str(valor or "").strip() else None
    except ValueError:
        return jsonify({"ok": False, "erro": "Valor previsto inválido."}), 400

    prioridade = dados.get("prioridade") if dados.get("prioridade") in PRIORIDADES else "media"
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO cartao.compra_futura "
            "(descricao, valor_previsto, mes_alvo, prioridade, observacao, criado_por) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id;",
            (descricao, valor, _mes_alvo(dados.get("mes_alvo")), prioridade,
             (dados.get("observacao") or "").strip() or None, session.get("user")),
        )
        compra_id = cur.fetchone()[0]
        _gravar_dimensoes(cur, compra_id, dados.get("dimensoes"))
        conn.commit()
    except Exception:
        conn.rollback()
        cur.close()
        conn.close()
        raise
    cur.close()
    conn.close()
    registrar_auditoria(
        "alteracao", "Compra futura criada",
        detalhes={"id": compra_id, "descricao": descricao, "valor_previsto": valor},
    )
    return jsonify({"ok": True, "id": compra_id})


@bp.route("/api/compra-futura/<int:compra_id>", methods=["POST", "DELETE"])
@requer("lancamentos_manual")
def editar_compra_futura(compra_id):
    conn = get_conn()
    cur = conn.cursor()
    if request.method == "DELETE":
        cur.execute("DELETE FROM cartao.compra_futura WHERE id=%s;", (compra_id,))
        apagou = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if not apagou:
            return jsonify({"ok": False, "erro": "Item não encontrado."}), 404
        registrar_auditoria("exclusao", "Compra futura removida", detalhes={"id": compra_id})
        return jsonify({"ok": True})

    dados = request.get_json(force=True)
    sets, valores = [], []
    if "descricao" in dados:
        descricao = (dados.get("descricao") or "").strip()
        if not descricao:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "erro": "A descrição não pode ficar vazia."}), 400
        sets.append("descricao=%s")
        valores.append(descricao)
    if "valor_real" in dados:
        bruto = str(dados.get("valor_real") or "").strip().replace(",", ".")
        sets.append("valor_real=%s")
        valores.append(round(float(bruto), 2) if bruto else None)
    if "valor_previsto" in dados:
        bruto = str(dados.get("valor_previsto") or "").strip().replace(",", ".")
        sets.append("valor_previsto=%s")
        valores.append(round(float(bruto), 2) if bruto else None)
    if "mes_alvo" in dados:
        sets.append("mes_alvo=%s")
        valores.append(_mes_alvo(dados.get("mes_alvo")))
    if "prioridade" in dados and dados["prioridade"] in PRIORIDADES:
        sets.append("prioridade=%s")
        valores.append(dados["prioridade"])
    if "observacao" in dados:
        sets.append("observacao=%s")
        valores.append((dados.get("observacao") or "").strip() or None)
    if "situacao" in dados and dados["situacao"] in SITUACOES:
        sets.append("situacao=%s")
        valores.append(dados["situacao"])
        # "comprada" sem lancamento vinculado continua valendo: pode ter sido
        # paga em dinheiro e ainda nao lancada. O vinculo vem depois.
        if dados["situacao"] != "comprada":
            # Reabrir desfaz o fato: o valor real e o vinculo deixam de existir,
            # senao a lista mostraria "a comprar" com o preco de uma compra que
            # foi desfeita.
            sets.extend(["transacao_id=NULL", "comprada_em=NULL", "valor_real=NULL"])
        else:
            sets.append("comprada_em=COALESCE(comprada_em, now())")
    if "transacao_id" in dados:
        alvo = (dados.get("transacao_id") or "").strip() or None
        if alvo:
            cur.execute("SELECT 1 FROM cartao.transacao WHERE transacao_id::text=%s;", (alvo,))
            if not cur.fetchone():
                cur.close()
                conn.close()
                return jsonify({"ok": False, "erro": "Lançamento não encontrado."}), 400
        sets.append("transacao_id=%s")
        valores.append(alvo)

    if sets:
        sets.append("atualizado_em=now()")
        cur.execute(
            f"UPDATE cartao.compra_futura SET {', '.join(sets)} WHERE id=%s;",
            valores + [compra_id],
        )
    if "dimensoes" in dados:
        _gravar_dimensoes(cur, compra_id, dados.get("dimensoes"))
    conn.commit()
    cur.close()
    conn.close()
    registrar_auditoria(
        "alteracao", "Compra futura alterada",
        detalhes={"id": compra_id, "campos": sorted(dados.keys())},
    )
    return jsonify({"ok": True})
