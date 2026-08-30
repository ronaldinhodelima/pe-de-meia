"""Tela de Lancamentos e a API que ela usa."""
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import psycopg2
import psycopg2.extras
from flask import Blueprint, request, session, jsonify, render_template

from core import (
    CATEGORIAS_EXTRA,
    CATEGORIAS_OCULTAS,
    CATEGORIA_PT,
    CATEGORIA_PT_DB,
    CONTA_MANUAL_ID,
    DATA_LOCAL_SQL,
    DUPLICADA_OBS_PADRAO,
    FINANCEIRO_DIM_TABELA,
    FINANCEIRO_TABELA,
    JOIN_NATUREZA,
    NATUREZAS,
    NATUREZA_SQL,
    VAL_DESPESA,
    aplicar_regras,
    carregar_origens,
    rotulo_valor_dimensao,
    cat_pt,
    cat_pt_puro,
    chave_alfa,
    chip_filter_html,
    data_hora_local,
    esc,
    FUSO_LOCAL,
    fechar_recursos_banco,
    get_conn,
    intervalo_ano_local,
    intervalo_mes_local,
    json_script,
    pode,
    preencher_classificacao_vazia_parcelas,
    registrar_auditoria,
    registrar_e_calcular_crescimento,
    registrar_mudanca_auditoria,
    requer,
    topbar_html,
)

bp = Blueprint("lancamentos", __name__)

# O Pluggy pode deixar uma transacao PENDING mesmo depois da fatura fechar e
# ser paga - as vezes ele simplesmente nao atualiza o status pra POSTED.
# Bloquear o OK pra sempre nesse caso trava o lancamento sem necessidade;
# passados esses dias (mais que o ciclo normal de uma fatura em aberto),
# PENDING deixa de ser motivo de bloqueio.
JANELA_PENDENTE_DIAS = 35


def _pendente_bloqueia(status, data_transacao_local):
    if (status or "").upper() != "PENDING":
        return False
    if data_transacao_local is None:
        return True
    idade = datetime.now(FUSO_LOCAL) - data_transacao_local
    return idade.days <= JANELA_PENDENTE_DIAS


def _valor_manual(valor, direcao):
    """Normaliza dinheiro manual seguindo o sinal usado pelo Pluggy em contas.

    Entrada fica positiva; saida, negativa. O DRE inverte esse sinal para obter
    VAL_DESPESA (positivo quando dinheiro sai).
    """
    if direcao not in ("entrada", "saida"):
        raise ValueError("direcao invalida")
    try:
        numero = Decimal(str(valor or "0").strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("valor invalido") from exc
    if not numero.is_finite() or numero <= 0:
        raise ValueError("valor invalido")
    numero = numero.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return numero if direcao == "entrada" else -numero


def _normalizar_rateios(valor_pai, partes):
    """Valida um rateio completo e devolve valores com o sinal do lancamento."""
    try:
        total_pai = Decimal(str(valor_pai)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Valor original inválido.") from exc
    if len(partes or []) < 2:
        raise ValueError("O rateio precisa ter pelo menos duas partes.")
    if len(partes) > 20:
        raise ValueError("Use no máximo 20 partes por lançamento.")
    sinal = Decimal("-1") if total_pai < 0 else Decimal("1")
    normalizadas = []
    soma = Decimal("0.00")
    for indice, parte in enumerate(partes):
        try:
            valor = Decimal(str(parte.get("valor") or "0").replace(",", "."))
        except (InvalidOperation, AttributeError) as exc:
            raise ValueError(f"Valor inválido na parte {indice + 1}.") from exc
        if not valor.is_finite() or valor <= 0:
            raise ValueError(f"Informe um valor positivo na parte {indice + 1}.")
        valor = valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        categoria = (parte.get("categoria") or "").strip()
        if not categoria:
            raise ValueError(f"Escolha a categoria da parte {indice + 1}.")
        soma += valor
        normalizadas.append({
            "ordem": indice,
            "valor_brl": valor * sinal,
            "categoria": categoria,
            "observacao": (parte.get("observacao") or "").strip()[:500],
            "dimensoes": parte.get("dimensoes") or {},
        })
    esperado = abs(total_pai)
    if soma != esperado:
        diferenca = esperado - soma
        raise ValueError(
            f"O rateio soma R$ {soma:.2f}, mas o lançamento é R$ {esperado:.2f}. "
            f"Diferença: R$ {diferenca:.2f}."
        )
    return normalizadas


def _estado_rateios(cur, transacao_id):
    cur.execute(
        "SELECT r.id, r.ordem, r.valor_brl, r.categoria, r.observacao, "
        "rd.dimensao_id, rd.valor_id FROM cartao.transacao_rateio r "
        "LEFT JOIN cartao.transacao_rateio_dimensao rd ON rd.rateio_id=r.id "
        "WHERE r.transacao_id=%s ORDER BY r.ordem, r.id, rd.dimensao_id;",
        (transacao_id,),
    )
    partes = {}
    for row in cur.fetchall():
        item = partes.setdefault(row[0], {
            "id": row[0], "ordem": row[1], "valor": float(abs(row[2])),
            "categoria": row[3], "observacao": row[4] or "", "dimensoes": {},
        })
        if row[5] is not None:
            item["dimensoes"][str(row[5])] = row[6]
    return list(partes.values())


@bp.route("/")
@requer("lancamentos_ver")
def index():
    mes = request.args.get("mes") or datetime.now().strftime("%Y-%m")
    periodo = request.args.get("periodo") or "mes"
    if periodo not in ("mes", "ano", "intervalo"):
        periodo = "mes"
    data_inicio_str = request.args.get("data_inicio") or ""
    data_fim_str = request.args.get("data_fim") or ""
    try:
        if periodo == "intervalo" and data_inicio_str and data_fim_str:
            # Fatura de cartao nao fecha no mes civil (ex: 13/jul a 12/ago) -
            # esse periodo existe pra revisar exatamente a janela de uma
            # fatura, sem forcar um recorte por mes que ela nunca respeitou.
            inicio_mes = datetime.strptime(data_inicio_str, "%Y-%m-%d").replace(tzinfo=FUSO_LOCAL)
            fim_mes = datetime.strptime(data_fim_str, "%Y-%m-%d").replace(tzinfo=FUSO_LOCAL) + timedelta(days=1)
            if fim_mes <= inicio_mes:
                raise ValueError("intervalo invalido")
        elif periodo == "ano":
            inicio_mes, fim_mes = intervalo_ano_local(mes[:4])
        else:
            periodo = "mes"
            inicio_mes, fim_mes = intervalo_mes_local(mes)
    except ValueError:
        mes = datetime.now().strftime("%Y-%m")
        periodo = "mes"
        inicio_mes, fim_mes = intervalo_mes_local(mes)
    status = request.args.get("status", "todas")
    if status not in ("todas", "pendente", "conferida", "duplicidade", "duplicada"):
        status = "todas"
    origem_sel = request.args.getlist("origem")

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    regras_resultado = aplicar_regras(cur)
    conn.commit()
    if (
        regras_resultado["lancamentos"] or regras_resultado["dimensoes"]
        or regras_resultado["erro"] or regras_resultado["duplicatas_ignoradas"]
    ):
        registrar_auditoria(
            "regra_automatica",
            "classificacao",
            sucesso=not bool(regras_resultado["erro"]),
            detalhes=regras_resultado,
        )

    crescimento = registrar_e_calcular_crescimento(cur)
    conn.commit()

    contas_by_id, origem_opcoes = carregar_origens(cur)

    # quantos lancamentos cada origem tem NO MES aberto. Nao entra o filtro de
    # origem aqui de proposito: se entrasse, marcar uma origem zeraria a contagem
    # das outras e o numero deixaria de servir para comparar.
    cur.execute(
        f"SELECT account_id, COUNT(*) AS n FROM cartao.transacao t "
        "WHERE t.data_transacao >= %s AND t.data_transacao < %s GROUP BY account_id;",
        (inicio_mes, fim_mes),
    )
    qtd_por_origem = {str(r["account_id"]): r["n"] for r in cur.fetchall()}

    # Possiveis duplicidades do mes: mesma conta, mesmo dia e mesmo valor. O Pluggy
    # ja mandou o mesmo debito duas vezes (Cond Sta Lucia em 21/11/2025), e sem
    # aviso isso vira despesa dobrada sem ninguem notar. Quem ja foi marcado como
    # duplicada fica de fora - a decisao ja foi tomada.
    cur.execute(
        "SELECT array_agg(t.transacao_id::text) AS ids FROM cartao.transacao t "
        "WHERE t.data_transacao >= %s AND t.data_transacao < %s "
        "AND COALESCE(t.duplicada, false) = false "
        f"GROUP BY t.account_id, ({DATA_LOCAL_SQL})::date, "
        "COALESCE(t.valor_brl, t.valor_original), t.descricao "
        "HAVING COUNT(*) > 1;",
        (inicio_mes, fim_mes),
    )
    ids_suspeitos = set()
    for r in cur.fetchall():
        ids_suspeitos.update(r["ids"] or [])

    cur.execute(f"SELECT DISTINCT categoria FROM {FINANCEIRO_TABELA} WHERE categoria IS NOT NULL;")
    categorias_db = {r["categoria"] for r in cur.fetchall()}
    categorias = sorted((categorias_db | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB)) - CATEGORIAS_OCULTAS, key=lambda c: chave_alfa(cat_pt(c)))

    where = ["t.data_transacao >= %s", "t.data_transacao < %s"]
    params = [inicio_mes, fim_mes]
    if origem_sel:
        where.append("t.account_id IN %s")
        params.append(tuple(origem_sel))
    if status == "conferida":
        where.append("t.conferida = true")
    elif status == "pendente":
        where.append("t.conferida = false")
    elif status == "duplicidade":
        if ids_suspeitos:
            where.append("t.transacao_id IN %s")
            params.append(tuple(ids_suspeitos))
        else:
            where.append("false")
    elif status == "duplicada":
        where.append("COALESCE(t.duplicada, false) = true")

    cur.execute(
        "SELECT t.transacao_id, t.account_id, t.data_transacao, t.descricao, t.categoria, "
        "COALESCE(t.valor_brl, t.valor_original) AS valor, t.valor_original, t.moeda_original, "
        "t.status, t.tipo, t.numero_cartao_final, t.parcela_atual, t.parcela_total, "
        "t.conferida, t.observacao, t.conferida_por, t.conferida_em, COALESCE(t.duplicada, false) AS duplicada, "
        "t.substituido_por, COALESCE(t.somente_conciliacao, false) AS somente_conciliacao, "
        "COALESCE(t.importado, false) AS importado, t.natureza, t.sincronizado_em, t.primeiro_sincronizado_em, "
        f"{NATUREZA_SQL} AS natureza_efetiva "
        f"FROM cartao.transacao t {JOIN_NATUREZA} WHERE " + " AND ".join(where) + " ORDER BY t.data_transacao DESC;",
        params,
    )
    rows = cur.fetchall()

    # resumo do mes (nao filtrado por status, sempre do mes inteiro; duplicadas nao contam)
    where_resumo = ["t.data_transacao >= %s", "t.data_transacao < %s", "COALESCE(t.duplicada, false) = false"]
    params_resumo = [inicio_mes, fim_mes]
    if origem_sel:
        where_resumo.append("t.account_id IN %s")
        params_resumo.append(tuple(origem_sel))
    # gasto real = so o que tem natureza de despesa (fatura, transferencia,
    # investimento e compra de bem nao sao gasto - ver NATUREZAS)
    cur.execute(
        "SELECT COUNT(*) total, SUM(CASE WHEN t.conferida THEN 1 ELSE 0 END) conferidas "
        "FROM cartao.transacao t WHERE " + " AND ".join(where_resumo) + ";",
        params_resumo,
    )
    resumo = dict(cur.fetchone())
    cur.execute(
        f"SELECT SUM(CASE WHEN {NATUREZA_SQL} = 'despesa' THEN {VAL_DESPESA} ELSE 0 END) AS gasto_real, "
        f"SUM(CASE WHEN {NATUREZA_SQL} = 'receita' THEN -{VAL_DESPESA} ELSE 0 END) AS receita_mes "
        f"FROM {FINANCEIRO_TABELA} t {JOIN_NATUREZA} WHERE " + " AND ".join(where_resumo) + ";",
        params_resumo,
    )
    resumo.update(dict(cur.fetchone()))

    where_cat = ["t.data_transacao >= %s", "t.data_transacao < %s", f"{NATUREZA_SQL} = 'despesa'",
                 "t.categoria IS NOT NULL", "COALESCE(t.duplicada, false) = false"]
    params_cat = [inicio_mes, fim_mes]
    if origem_sel:
        where_cat.append("t.account_id IN %s")
        params_cat.append(tuple(origem_sel))
    cur.execute(
        f"SELECT t.categoria, SUM({VAL_DESPESA}) AS total "
        f"FROM {FINANCEIRO_TABELA} t {JOIN_NATUREZA} WHERE " + " AND ".join(where_cat) +
        " GROUP BY t.categoria ORDER BY total DESC LIMIT 8;",
        params_cat,
    )
    por_categoria = cur.fetchall()

    cur.execute("SELECT final4, prefixo FROM cartao.cartao_nome;")
    nomes_cartao = {r["final4"]: esc(r["prefixo"]) for r in cur.fetchall()}

    cur.execute("SELECT id, nome, obrigatoria FROM cartao.dimensao ORDER BY ordem, nome;")
    dimensoes = cur.fetchall()

    cur.execute("SELECT id, dimensao_id, nome, icone, portfolio_valor_id FROM cartao.dimensao_valor ORDER BY nome;")
    valores_por_dim = {}
    projeto_portfolio_map = {}
    for v in cur.fetchall():
        valores_por_dim.setdefault(v["dimensao_id"], []).append(v)
        if v["portfolio_valor_id"]:
            projeto_portfolio_map[str(v["id"])] = str(v["portfolio_valor_id"])

    mapa_dim_transacao = {}
    ids_visiveis = [r["transacao_id"] for r in rows]
    if ids_visiveis:
        cur.execute(
            "SELECT transacao_id, dimensao_id, valor_id FROM cartao.transacao_dimensao WHERE transacao_id IN %s;",
            (tuple(ids_visiveis),),
        )
        for m in cur.fetchall():
            mapa_dim_transacao[(str(m["transacao_id"]), m["dimensao_id"])] = m["valor_id"]

    rateios_por_transacao = {}
    if ids_visiveis:
        cur.execute(
            "SELECT r.id, r.transacao_id, r.ordem, r.valor_brl, r.categoria, r.observacao, "
            "rd.dimensao_id, rd.valor_id FROM cartao.transacao_rateio r "
            "LEFT JOIN cartao.transacao_rateio_dimensao rd ON rd.rateio_id=r.id "
            "WHERE r.transacao_id IN %s ORDER BY r.transacao_id, r.ordem, r.id;",
            (tuple(ids_visiveis),),
        )
        rateios_por_id = {}
        for rr in cur.fetchall():
            item = rateios_por_id.setdefault(rr["id"], {
                "id": rr["id"], "transacao_id": str(rr["transacao_id"]),
                "ordem": rr["ordem"], "valor_brl": rr["valor_brl"],
                "categoria": rr["categoria"], "observacao": rr["observacao"] or "", "dims": {},
            })
            if rr["dimensao_id"] is not None:
                item["dims"][rr["dimensao_id"]] = rr["valor_id"]
        for item in rateios_por_id.values():
            rateios_por_transacao.setdefault(item["transacao_id"], []).append(item)

    cur.close()
    conn.close()

    # a tela nao deve oferecer acao que a API vai recusar
    pode_editar = pode("lancamentos_editar")
    pode_conferir = pode("lancamentos_conferir")
    pode_manual = pode("lancamentos_manual")

    def nome_cartao_curto(final4):
        if not final4:
            return "-"
        prefixo = nomes_cartao.get(final4)
        return prefixo if prefixo else f"final {final4}"

    def origem_partes(account_id, final4=None):
        """(selo_html, texto). O selo e HTML montado pelo app; o texto vem do
        apelido do cartao, digitado pelo usuario, e o template escapa."""
        c = contas_by_id.get(str(account_id))
        if not c:
            return "", "-"
        if c["tipo"] == "CREDIT" and final4 and nomes_cartao.get(final4):
            return c["selo"], nomes_cartao[final4]
        return c["selo"], c["label_curto"]

    def origem_completa(account_id, final4=None):
        c = contas_by_id.get(str(account_id))
        if not c:
            return "-"
        if c["tipo"] == "CREDIT" and final4:
            return f'{c["label"]} - {nome_cartao_curto(final4)}'
        return c["label"]

    linhas_tabela = []
    detalhes_js = {}
    nomes_por_dim = {
        d["id"]: {v["id"]: rotulo_valor_dimensao(v) for v in valores_por_dim.get(d["id"], [])}
        for d in dimensoes
    }
    dimensoes_obrigatorias = {d["id"] for d in dimensoes if d["obrigatoria"]}
    for r in rows:
        data_local = data_hora_local(r["data_transacao"])
        data_fmt_full = data_local.strftime("%d/%m/%Y %H:%M")
        rid = r["transacao_id"]
        desc = r["descricao"] or ""

        conta_info = contas_by_id.get(str(r["account_id"]))
        # manual (dinheiro) ou importado de arquivo: pode ser excluido pelo modal
        eh_manual = bool((conta_info and conta_info["tipo"] == "MANUAL") or r["importado"])
        eh_nao_credito = conta_info and conta_info["tipo"] != "CREDIT"
        # cartao de credito: exibicao tradicional (sem sinal). conta corrente/manual: entrada/saida
        if eh_nao_credito:
            sinal = "-" if r["tipo"] == "DEBIT" else "+"
            cor_valor = "color:#c23c34" if r["tipo"] == "DEBIT" else "color:#1f8a53"
            valor_fmt = f'{sinal} R$ {abs(r["valor"]):,.2f}'
            valor_sort = -abs(r["valor"]) if sinal == "-" else abs(r["valor"])
        else:
            cor_valor = ""
            valor_fmt = f'R$ {r["valor"]:,.2f}'
            valor_sort = r["valor"]

        dims_sel = {d["id"]: mapa_dim_transacao.get((str(rid), d["id"])) for d in dimensoes}
        rateios_ui = []
        for parte in rateios_por_transacao.get(str(rid), []):
            dims_parte = {d["id"]: parte["dims"].get(d["id"]) for d in dimensoes}
            valor_parte = parte["valor_brl"]
            if eh_nao_credito:
                sinal_parte = "-" if valor_parte < 0 else "+"
                cor_parte = "color:#c23c34" if valor_parte < 0 else "color:#1f8a53"
                valor_parte_fmt = f'{sinal_parte} R$ {abs(valor_parte):,.2f}'
            else:
                cor_parte = ""
                valor_parte_fmt = f'R$ {abs(valor_parte):,.2f}'
            rateios_ui.append({
                "id": parte["id"],
                "valor": float(abs(valor_parte)),
                "valor_fmt": valor_parte_fmt,
                "cor_valor": cor_parte,
                "categoria": parte["categoria"],
                "categoria_nome": cat_pt_puro(parte["categoria"]),
                "observacao": parte["observacao"],
                "dims": dims_parte,
                "dims_rotulos": {
                    d["id"]: nomes_por_dim[d["id"]].get(dims_parte[d["id"]], "(nao definido)")
                    for d in dimensoes
                },
            })
        soma_rateio = sum(
            (Decimal(str(parte["valor_brl"])) for parte in rateios_por_transacao.get(str(rid), [])),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))
        valor_pai_rateio = Decimal(str(r["valor"] or 0)).quantize(Decimal("0.01"))
        rateio_valido = bool(rateios_ui) and (
            len(rateios_ui) >= 2
            and soma_rateio == valor_pai_rateio
            and all(parte["categoria"] for parte in rateios_ui)
            and all(
                all(parte["dims"].get(dim_id) is not None for dim_id in dimensoes_obrigatorias)
                for parte in rateios_ui
            )
        )
        selo, origem_texto = origem_partes(r["account_id"], r["numero_cartao_final"])
        origem_full = origem_completa(r["account_id"], r["numero_cartao_final"])

        pendente_banco = (r["status"] or "").upper() == "PENDING"
        pendente_bloqueia_ok = _pendente_bloqueia(r["status"], data_local)
        linhas_tabela.append({
            "id": str(rid),
            "substituido_por": str(r["substituido_por"]) if r["substituido_por"] else None,
            # fora_do_resultado: o lancamento existe e continua consultavel, mas
            # nao entra no DRE. Sem marcar isso na tela, dois lancamentos de
            # mesmo valor e data aparecem lado a lado sem nenhuma pista de que
            # so um conta - e quem revisa conclui que ha duplicidade.
            "classes": " ".join(c for c in [
                "conferida" if r["conferida"] else "",
                "duplicada" if r["duplicada"] else "",
                "fora-resultado" if (r["substituido_por"] or r["somente_conciliacao"]) else "",
                "pendente-banco" if pendente_banco else "",
            ] if c),
            "fora_do_resultado": (
                "Mesmo evento que outro lançamento — só o outro conta no resultado."
                if r["substituido_por"] else
                ("Registro de conciliação (compra parcelada inteira) — as parcelas é que contam."
                 if r["somente_conciliacao"] else "")
            ),
            "pendente_banco": pendente_banco,
            "pendente_bloqueia_ok": pendente_bloqueia_ok,
            "data_dia": data_local.strftime("%d/%m/%y"),
            "data_hora": data_local.strftime("%H:%M"),
            "data_full": data_fmt_full,
            "data_sort": data_local.timestamp(),
            "descricao": desc,
            "origem_selo": selo,
            "origem_texto": origem_texto,
            "origem_completa": origem_full,
            "categoria": r["categoria"],
            "categoria_nome": cat_pt_puro(r["categoria"]) if r["categoria"] else "(sem categoria)",
            "dims": dims_sel,
            "dims_rotulos": {
                d["id"]: nomes_por_dim[d["id"]].get(dims_sel[d["id"]], "(nao definido)")
                for d in dimensoes
            },
            "valor_fmt": valor_fmt,
            "valor_sort": valor_sort,
            "cor_valor": cor_valor,
            "observacao": r["observacao"] or "",
            "conferida": r["conferida"],
            "duplicada": r["duplicada"],
            "suspeita_duplicidade": str(rid) in ids_suspeitos,
            "rateios": rateios_ui,
            "rateio_valido": rateio_valido,
            "valor_rateio": float(abs(valor_pai_rateio)),
            "registros_tecnicos": [],
        })

        detalhes = {
            "data": data_fmt_full,
            "descricao": desc,
            "categoria": cat_pt_puro(r["categoria"]),
            "valor": valor_fmt,
            "valor_original": f'{r["valor_original"]:,.2f} {r["moeda_original"] or ""}' if r["valor_original"] is not None else "-",
            "status": r["status"] or "-",
            "tipo": r["tipo"] or "-",
            "origem": origem_full,
            "parcela": f'{r["parcela_atual"]}/{r["parcela_total"]}' if r["parcela_total"] and r["parcela_total"] > 1 else "À vista",
            "conferida": "Sim" if r["conferida"] else "Não",
            "conferida_por": r["conferida_por"] or "-",
            "observacao": r["observacao"] or "-",
            "sincronizado_em": data_hora_local(r["sincronizado_em"]).strftime("%d/%m/%Y %H:%M") if r["sincronizado_em"] else "-",
            "primeiro_sincronizado_em": data_hora_local(r["primeiro_sincronizado_em"]).strftime("%d/%m/%Y %H:%M") if r["primeiro_sincronizado_em"] else "-",
            "_conferida": bool(r["conferida"]),
            "_pendente_banco": pendente_banco,
            "_pendente_bloqueia_ok": pendente_bloqueia_ok,
            "_duplicada": bool(r["duplicada"]),
            "_manual": bool(eh_manual),
            "_natureza": r["natureza"] or "",
            "_natureza_efetiva": NATUREZAS.get(r["natureza_efetiva"], r["natureza_efetiva"]),
            "_valor_rateio": float(abs(r["valor"] or 0)),
            "_rateios": rateios_ui,
            "_rateio_valido": rateio_valido,
        }
        for d in dimensoes:
            detalhes[d["nome"]] = nomes_por_dim[d["id"]].get(dims_sel[d["id"]], "(nao definido)")
        detalhes_js[str(rid)] = detalhes

    # Um registro que foi substituido continua disponivel para auditoria, mas
    # fica recolhido logo abaixo do lancamento que realmente conta. O vinculo
    # vem do banco (`substituido_por`): nunca agrupamos so porque data,
    # descricao ou valor parecem iguais.
    linhas_por_id = {linha["id"]: linha for linha in linhas_tabela}
    linhas_principais = []
    for linha in linhas_tabela:
        alvo = linhas_por_id.get(linha["substituido_por"])
        if alvo is not None and alvo is not linha:
            alvo["registros_tecnicos"].append(linha)
        else:
            linhas_principais.append(linha)
    for linha in linhas_tabela:
        linha.pop("substituido_por", None)
    linhas_tabela = linhas_principais

    gasto_real = resumo["gasto_real"] or 0
    receita_mes = resumo["receita_mes"] or 0
    categorias_template = [{"chave": c, "nome": cat_pt_puro(c)} for c in categorias]
    config_lancamentos = {
        "pode_editar": pode_editar,
        "pode_conferir": pode_conferir,
        "duplicada_obs": DUPLICADA_OBS_PADRAO,
        "dimensoes_cadastro_rapido": {
            str(d["id"]): d["nome"] for d in dimensoes
            if chave_alfa(d["nome"]) in {"projeto", "portfolio"}
        },
        "categorias": categorias_template,
        "dimensoes": {
            str(d["id"]): [
                {"id": v["id"], "rotulo": rotulo_valor_dimensao(v)}
                for v in valores_por_dim.get(d["id"], [])
            ]
            for d in dimensoes
        },
        "dimensoes_nomes": {str(d["id"]): d["nome"] for d in dimensoes},
        "dimensoes_obrigatorias": [str(d["id"]) for d in dimensoes if d["obrigatoria"]],
        # regra automatica Projeto -> Portfolio (cadastrada em /dimensoes):
        # ao escolher um Projeto que tem portfolio padrao, o JS preenche o
        # select de Portfolio sozinho (ver aplicarPortfolioDoProjeto em
        # lancamentos.js). Continua editavel - e' so um ponto de partida.
        "dim_id_portfolio": next(
            (str(d["id"]) for d in dimensoes if chave_alfa(d["nome"]) == "portfolio"), None
        ),
        "projeto_portfolio_map": projeto_portfolio_map,
    }

    return render_template(
        "index.html",
        titulo="Lançamentos",
        topbar=topbar_html("Lançamentos", "inicio"),
        mes=mes,
        periodo=periodo,
        data_inicio=data_inicio_str,
        data_fim=data_fim_str,
        periodo_rotulo="do ano" if periodo == "ano" else ("do período" if periodo == "intervalo" else "do mês"),
        status=status,
        hoje_iso=datetime.now().strftime("%Y-%m-%d"),
        origem_filtro_html=chip_filter_html(
            "origem", "Origem", origem_opcoes, origem_sel,
            onchange="aplicarFiltros()", contagens=qtd_por_origem,
        ),
        pode_editar=pode_editar,
        pode_conferir=pode_conferir,
        pode_manual=pode_manual,
        pode_regras=pode("cadastros"),
        categorias=categorias_template,
        dimensoes=dimensoes,
        valores_por_dim=valores_por_dim,
        naturezas=NATUREZAS,
        linhas=linhas_tabela,
        por_categoria=[
            {"nome": cat_pt_puro(c["categoria"]), "total": float(c["total"])} for c in por_categoria
        ],
        receita_mes=receita_mes,
        gasto_real=gasto_real,
        resultado_mes=receita_mes - gasto_real,
        conf=resumo["conferidas"] or 0,
        total=resumo["total"] or 0,
        crescimento=crescimento,
        detalhes_json=json_script(detalhes_js),
        config_json=json_script(config_lancamentos),
    )


@bp.route("/api/lancamento-manual", methods=["POST"])
@requer("lancamentos_manual")
def lancamento_manual():
    data = request.get_json(force=True)
    conn = cur = None
    transacao_encerrada = False
    try:
        data_str = (data.get("data") or "").strip()
        descricao = (data.get("descricao") or "").strip()
        direcao = data.get("direcao")
        valor = _valor_manual(data.get("valor"), direcao)
        categoria = data.get("categoria") or None
        if not data_str or not descricao:
            return jsonify({"ok": False, "erro": "Preencha data, descrição e um valor válido."}), 400

        tipo = "CREDIT" if direcao == "entrada" else "DEBIT"
        data_transacao = f"{data_str} 12:00:00-03:00"

        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cartao.transacao ("
            "transacao_id, account_id, descricao, descricao_bruta, valor_original, moeda_original, "
            "valor_brl, data_transacao, categoria, categoria_manual, status, tipo, "
            "criado_em, atualizado_em, sincronizado_em"
            ") VALUES (%s,%s,%s,%s,%s,'BRL',%s,%s,%s,%s,'POSTED',%s, now(), now(), now());",
            (
                str(uuid.uuid4()), CONTA_MANUAL_ID, descricao, descricao,
                valor, valor, data_transacao, categoria, bool(categoria), tipo,
            ),
        )
        conn.commit()
        transacao_encerrada = True
        return jsonify({"ok": True})
    except Exception as e:
        print("Aviso: falha ao criar lançamento manual:", e)
        return jsonify({"ok": False, "erro": "Não foi possível salvar o lançamento."}), 400
    finally:
        fechar_recursos_banco(conn, cur, rollback=not transacao_encerrada)


@bp.route("/api/lancamento-manual/<transacao_id>", methods=["DELETE"])
@requer("lancamentos_manual")
def excluir_lancamento_manual(transacao_id):
    """Exclui um lancamento criado manualmente ou importado de arquivo. Transacoes vindas do
    Pluggy nunca sao apagadas (elas voltariam na proxima sincronizacao)."""
    conn = cur = None
    transacao_encerrada = False
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT account_id, COALESCE(importado, false) FROM cartao.transacao "
            "WHERE transacao_id = %s FOR UPDATE;",
            (transacao_id,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"ok": False, "erro": "Lançamento não encontrado."}), 404
        if str(row[0]) != CONTA_MANUAL_ID and not row[1]:
            return jsonify({
                "ok": False,
                "erro": "Só é possível excluir lançamentos manuais ou importados de arquivo. Este veio da sincronização com o banco e voltaria na próxima atualização — marque como duplicada se quiser ignorá-lo.",
            }), 400

        cur.execute("DELETE FROM cartao.transacao_dimensao WHERE transacao_id = %s;", (str(transacao_id),))
        cur.execute("DELETE FROM cartao.transacao WHERE transacao_id = %s;", (transacao_id,))
        conn.commit()
        transacao_encerrada = True
        return jsonify({"ok": True})
    except Exception as e:
        print("Aviso: falha ao excluir lançamento manual:", e)
        return jsonify({"ok": False, "erro": "Não foi possível excluir o lançamento."}), 400
    finally:
        fechar_recursos_banco(conn, cur, rollback=not transacao_encerrada)


@bp.route("/api/transacao/<transacao_id>")
@requer("lancamentos_ver")
def detalhes_transacao(transacao_id):
    """Detalhes de um lancamento, para telas que nao carregam a tabela inteira.

    A tela de Lancamentos ja recebe tudo embutido no HTML; quem usa isto e o modal
    de /categorias, que precisa abrir um lancamento avulso.
    """
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT t.transacao_id, t.account_id, t.data_transacao, t.descricao, t.categoria, "
        "COALESCE(t.valor_brl, t.valor_original) AS valor, t.valor_original, t.moeda_original, "
        "t.status, t.tipo, t.numero_cartao_final, t.parcela_atual, t.parcela_total, "
        "t.conferida, t.observacao, t.conferida_por, t.natureza, "
        f"{NATUREZA_SQL} AS natureza_efetiva "
        f"FROM cartao.transacao t {JOIN_NATUREZA} WHERE t.transacao_id = %s;",
        (transacao_id,),
    )
    r = cur.fetchone()
    if not r:
        cur.close()
        conn.close()
        return jsonify({"ok": False, "erro": "Lançamento não encontrado."}), 404

    contas_by_id, _ = carregar_origens(cur)
    cur.execute("SELECT final4, prefixo FROM cartao.cartao_nome;")
    nomes_cartao = {c["final4"]: c["prefixo"] for c in cur.fetchall()}
    cur.close()
    conn.close()

    conta = contas_by_id.get(str(r["account_id"]))
    if not conta:
        origem = "-"
    elif conta["tipo"] == "CREDIT" and r["numero_cartao_final"]:
        apelido = nomes_cartao.get(r["numero_cartao_final"]) or f'final {r["numero_cartao_final"]}'
        origem = f'{conta["label"]} - {apelido}'
    else:
        origem = conta["label"]

    local = data_hora_local(r["data_transacao"])
    return jsonify({
        "ok": True,
        "transacao_id": str(r["transacao_id"]),
        "data": local.strftime("%d/%m/%Y %H:%M") if local else "-",
        "descricao": r["descricao"] or "-",
        "categoria": r["categoria"] or "",
        "categoria_nome": cat_pt_puro(r["categoria"]),
        "valor": f'R$ {float(r["valor"] or 0):,.2f}',
        "valor_original": (
            f'{float(r["valor_original"]):,.2f} {r["moeda_original"] or ""}'.strip()
            if r["valor_original"] is not None else "-"
        ),
        "status": r["status"] or "-",
        "tipo": r["tipo"] or "-",
        "origem": origem,
        "parcela": (
            f'{r["parcela_atual"]}/{r["parcela_total"]}'
            if r["parcela_total"] and r["parcela_total"] > 1 else "À vista"
        ),
        "conferida": "Sim" if r["conferida"] else "Não",
        "conferida_por": r["conferida_por"] or "-",
        "observacao": r["observacao"] or "-",
        "natureza_efetiva": NATUREZAS.get(r["natureza_efetiva"], r["natureza_efetiva"]),
    })


@bp.route("/api/dimensao/<int:dimensao_id>/valor", methods=["POST"])
@requer("lancamentos_editar")
def criar_valor_dimensao_rapido(dimensao_id):
    """Cria Projeto/Portfólio sem sair da classificação do lançamento."""
    data = request.get_json(force=True)
    nome = (data.get("nome") or "").strip()
    if not nome:
        return jsonify({"ok": False, "erro": "Informe o nome do novo item."}), 400
    if len(nome) > 120:
        return jsonify({"ok": False, "erro": "Use um nome com até 120 caracteres."}), 400
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, nome FROM cartao.dimensao WHERE id=%s;", (dimensao_id,))
    dimensao = cur.fetchone()
    if not dimensao or chave_alfa(dimensao["nome"]) not in {"projeto", "portfolio"}:
        cur.close()
        conn.close()
        return jsonify({"ok": False, "erro": "Cadastro rápido disponível apenas para Projeto e Portfólio."}), 400
    cur.execute(
        "SELECT id, nome FROM cartao.dimensao_valor "
        "WHERE dimensao_id=%s AND lower(nome)=lower(%s);",
        (dimensao_id, nome),
    )
    valor = cur.fetchone()
    criado = False
    if not valor:
        cur.execute(
            "INSERT INTO cartao.dimensao_valor (dimensao_id,nome) VALUES (%s,%s) RETURNING id,nome;",
            (dimensao_id, nome),
        )
        valor = cur.fetchone()
        conn.commit()
        criado = True
        registrar_mudanca_auditoria("Valor de dimensão", None, {
            "id": valor["id"], "dimensao_id": dimensao_id, "dimensao": dimensao["nome"], "nome": valor["nome"],
        })
    cur.close()
    conn.close()
    return jsonify({"ok": True, "id": valor["id"], "nome": valor["nome"], "criado": criado})


@bp.route("/api/transacao/<transacao_id>/rateios", methods=["POST", "DELETE"])
@requer("lancamentos_editar")
def rateios_transacao(transacao_id):
    """Cria, substitui ou remove o rateio interno de um lancamento bancario."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COALESCE(valor_brl,valor_original), conferida FROM cartao.transacao "
            "WHERE transacao_id=%s FOR UPDATE;",
            (transacao_id,),
        )
        transacao = cur.fetchone()
        if not transacao:
            return jsonify({"ok": False, "erro": "Lançamento não encontrado."}), 404
        if transacao[1] and request.method == "DELETE":
            return jsonify({
                "ok": False,
                "erro": "Desmarque o OK antes de desfazer o rateio.",
            }), 409
        antes = _estado_rateios(cur, transacao_id)
        if request.method == "DELETE":
            cur.execute("DELETE FROM cartao.transacao_rateio WHERE transacao_id=%s;", (transacao_id,))
            conn.commit()
            registrar_mudanca_auditoria("Rateio", antes, None)
            return jsonify({"ok": True, "rateios": []})

        data = request.get_json(force=True)
        try:
            partes = _normalizar_rateios(transacao[0], data.get("partes") or [])
        except ValueError as exc:
            return jsonify({"ok": False, "erro": str(exc)}), 400

        categorias_validas = (
            set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB) | set(CATEGORIA_PT)
        ) - CATEGORIAS_OCULTAS
        for parte in partes:
            if parte["categoria"] not in categorias_validas:
                return jsonify({"ok": False, "erro": "Categoria inválida no rateio."}), 400
            dimensoes_ok = {}
            for dim_id_raw, valor_id_raw in parte["dimensoes"].items():
                try:
                    dim_id = int(dim_id_raw)
                    valor_id = int(valor_id_raw) if valor_id_raw not in (None, "") else None
                except (TypeError, ValueError):
                    return jsonify({"ok": False, "erro": "Dimensão inválida no rateio."}), 400
                if valor_id is not None:
                    cur.execute(
                        "SELECT 1 FROM cartao.dimensao_valor WHERE id=%s AND dimensao_id=%s;",
                        (valor_id, dim_id),
                    )
                    if not cur.fetchone():
                        return jsonify({"ok": False, "erro": "Valor de dimensão inválido no rateio."}), 400
                dimensoes_ok[dim_id] = valor_id
            parte["dimensoes"] = dimensoes_ok

        # Editar uma classificação nunca desmarca o OK. Quando ele já existe,
        # só aceitamos o novo conjunto se todas as dimensões obrigatórias
        # continuarem preenchidas; a soma e as categorias já foram validadas
        # acima. Assim não há estado confirmado parcialmente classificado.
        if transacao[1]:
            cur.execute("SELECT id FROM cartao.dimensao WHERE obrigatoria=true;")
            obrigatorias = [row[0] for row in cur.fetchall()]
            if any(
                any(parte["dimensoes"].get(dim_id) is None for dim_id in obrigatorias)
                for parte in partes
            ):
                return jsonify({
                    "ok": False,
                    "erro": "Preencha os campos obrigatórios de todas as partes.",
                }), 400

        cur.execute("DELETE FROM cartao.transacao_rateio WHERE transacao_id=%s;", (transacao_id,))
        for parte in partes:
            cur.execute(
                "INSERT INTO cartao.transacao_rateio "
                "(transacao_id,ordem,valor_brl,categoria,observacao) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING id;",
                (transacao_id, parte["ordem"], parte["valor_brl"],
                 parte["categoria"], parte["observacao"]),
            )
            rateio_id = cur.fetchone()[0]
            for dim_id, valor_id in parte["dimensoes"].items():
                if valor_id is not None:
                    cur.execute(
                        "INSERT INTO cartao.transacao_rateio_dimensao "
                        "(rateio_id,dimensao_id,valor_id) VALUES (%s,%s,%s);",
                        (rateio_id, dim_id, valor_id),
                    )
        conn.commit()
        depois = _estado_rateios(cur, transacao_id)
        registrar_mudanca_auditoria("Rateio", antes or None, depois)
        return jsonify({"ok": True, "rateios": depois})
    except Exception as exc:
        conn.rollback()
        print("Aviso: falha ao salvar rateio:", exc)
        return jsonify({"ok": False, "erro": "Não foi possível salvar o rateio."}), 400
    finally:
        cur.close()
        conn.close()


@bp.route("/api/transacao/<transacao_id>", methods=["POST"])
@requer("lancamentos_editar")
def update_transacao(transacao_id):
    data = request.get_json(force=True)
    conn = get_conn()
    cur = conn.cursor()

    # Serializa edições do mesmo lançamento. Combinado com payloads parciais da
    # tela, duas abas podem alterar campos diferentes sem uma apagar a outra.
    cur.execute(
        "SELECT conferida, conferida_por, COALESCE(duplicada, false), "
        "categoria, observacao, natureza, COALESCE(valor_brl,valor_original), status, data_transacao "
        "FROM cartao.transacao WHERE transacao_id = %s FOR UPDATE;",
        (transacao_id,),
    )
    transacao = cur.fetchone()
    if not transacao:
        cur.close()
        conn.close()
        return jsonify({"ok": False, "erro": "Lançamento não encontrado."}), 404

    if "conferida" in data and not pode("lancamentos_conferir"):
        if bool(data.get("conferida")) != bool(transacao[0]):
            cur.close()
            conn.close()
            return jsonify({"ok": False, "erro": "Sem permissão para conferir lançamentos."}), 403
        # A tela envia o estado atual junto com as demais edicoes. Sem permissao
        # para conferir, ele pode continuar no payload, mas nunca gera UPDATE.
        data.pop("conferida", None)

    if (
        "conferida" in data
        and bool(transacao[0])
        and not bool(data.get("conferida"))
        and data.get("confirmar_desmarcacao") is not True
    ):
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({
            "ok": False,
            "erro": "Confirme a desmarcação do OK nos detalhes do lançamento.",
        }), 409

    if (
        "duplicada" in data
        and not bool(transacao[2])
        and bool(data.get("duplicada"))
        and data.get("confirmar_duplicada") is not True
    ):
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({
            "ok": False,
            "erro": "Confirme a marcação como duplicada nos detalhes do lançamento.",
        }), 409

    dimensoes_enviadas = data.get("dimensoes") or {}
    dimensoes_validadas = []
    for dim_id_str, valor_id in dimensoes_enviadas.items():
        try:
            dim_id = int(dim_id_str)
            valor_id_int = int(valor_id) if valor_id not in (None, "") else None
        except (TypeError, ValueError):
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({"ok": False, "erro": "Dimensão ou valor inválido."}), 400

        if valor_id_int is None:
            cur.execute("SELECT nome FROM cartao.dimensao WHERE id = %s;", (dim_id,))
        else:
            cur.execute(
                "SELECT d.nome, dv.nome FROM cartao.dimensao_valor dv "
                "JOIN cartao.dimensao d ON d.id = dv.dimensao_id "
                "WHERE dv.id = %s AND dv.dimensao_id = %s;",
                (valor_id_int, dim_id),
            )
        dimensao_nova = cur.fetchone()
        if not dimensao_nova:
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({"ok": False, "erro": "O valor não pertence à dimensão informada."}), 400
        nome_dimensao = dimensao_nova[0]
        nome_valor_novo = dimensao_nova[1] if valor_id_int is not None else None
        cur.execute(
            "SELECT td.valor_id, dv.nome FROM cartao.transacao_dimensao td "
            "LEFT JOIN cartao.dimensao_valor dv ON dv.id = td.valor_id "
            "WHERE td.transacao_id = %s AND td.dimensao_id = %s;",
            (transacao_id, dim_id),
        )
        dimensao_antiga = cur.fetchone()
        dimensoes_validadas.append((
            dim_id,
            valor_id_int,
            nome_dimensao,
            dimensao_antiga[0] if dimensao_antiga else None,
            dimensao_antiga[1] if dimensao_antiga else None,
            nome_valor_novo,
        ))

    for dim_id, valor_id_int, _nome_dimensao, _valor_id_antigo, _valor_antigo, _valor_novo in dimensoes_validadas:
        cur.execute(
            "INSERT INTO cartao.transacao_dimensao (transacao_id, dimensao_id, valor_id) VALUES (%s,%s,%s) "
            "ON CONFLICT (transacao_id, dimensao_id) DO UPDATE SET valor_id = EXCLUDED.valor_id;",
            (transacao_id, dim_id, valor_id_int),
        )

    # Trava de confirmacao: lancamento simples valida suas dimensoes; lancamento
    # rateado valida cada parte e tambem exige que a soma feche com o banco.
    cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(valor_brl),0), "
        "COUNT(*) FILTER (WHERE categoria IS NULL OR categoria='') "
        "FROM cartao.transacao_rateio WHERE transacao_id=%s;",
        (transacao_id,),
    )
    qtd_rateios, soma_rateios, rateios_sem_categoria = cur.fetchone()
    rateio_invalido = False
    if qtd_rateios:
        cur.execute(
            "SELECT r.id, d.id FROM cartao.transacao_rateio r CROSS JOIN cartao.dimensao d "
            "LEFT JOIN cartao.transacao_rateio_dimensao rd "
            "ON rd.rateio_id=r.id AND rd.dimensao_id=d.id "
            "WHERE r.transacao_id=%s AND d.obrigatoria=true AND rd.valor_id IS NULL;",
            (transacao_id,),
        )
        faltando = sorted({r[1] for r in cur.fetchall()})
        esperado = Decimal(str(transacao[6] or 0)).quantize(Decimal("0.01"))
        soma_rateios = Decimal(str(soma_rateios or 0)).quantize(Decimal("0.01"))
        rateio_invalido = qtd_rateios < 2 or soma_rateios != esperado or bool(rateios_sem_categoria)
    else:
        cur.execute(
            "SELECT d.id FROM cartao.dimensao d "
            "LEFT JOIN cartao.transacao_dimensao td ON td.dimensao_id = d.id AND td.transacao_id = %s "
            "WHERE d.obrigatoria = true AND (td.valor_id IS NULL);",
            (transacao_id,),
        )
        faltando = [r[0] for r in cur.fetchall()]
    conferida_atual = bool(transacao[0])
    # PENDING e um status provisorio do banco - o Pluggy pode ainda alterar
    # valor/data, ou ate substituir a transacao por outra com id diferente,
    # antes da fatura fechar. Marcar OK num lancamento que ainda pode mudar
    # daria uma assinatura de conferencia sobre um dado que nao e definitivo.
    # Mas o Pluggy as vezes nunca atualiza o status pra POSTED mesmo depois
    # da fatura fechar e paga - por isso so bloqueia enquanto for recente
    # (ver JANELA_PENDENTE_DIAS); passado isso, tratamos como falha do
    # Pluggy em atualizar, nao como dado ainda instavel.
    pendente_banco = _pendente_bloqueia(
        transacao[7], data_hora_local(transacao[8]) if transacao[8] else None
    )
    alterando_conferencia = "conferida" in data
    conferida_solicitada = bool(data.get("conferida")) if alterando_conferencia else conferida_atual
    # Campos obrigatorios (e o status pendente) bloqueiam somente uma NOVA
    # marcacao de OK. Uma edicao de categoria/dimensao/observacao jamais pode
    # desmarcar um OK ja existente.
    bloqueada = (
        bool(faltando or rateio_invalido or pendente_banco)
        and alterando_conferencia and conferida_solicitada
    )
    conferida_final = conferida_solicitada and not bloqueada if alterando_conferencia else conferida_atual

    # natureza especifica deste lancamento ("" = volta a seguir a natureza da categoria)
    natureza = data.get("natureza")
    natureza = natureza if natureza in NATUREZAS else None

    # So altera o que veio no payload. Categoria, dimensao e observacao nunca
    # podem apagar o OK, a duplicidade ou outro ajuste feito anteriormente.
    sets, valores = [], []
    if "conferida" in data:
        sets += [
            "conferida = %s",
            "conferida_por = CASE WHEN %s THEN %s ELSE NULL END",
            "conferida_em = CASE WHEN %s THEN now() ELSE NULL END",
        ]
        valores += [conferida_final, conferida_final, session.get("user"), conferida_final]
    if "duplicada" in data:
        sets.append("duplicada = %s")
        valores.append(bool(data.get("duplicada")))
    if "observacao" in data:
        sets.append("observacao = %s")
        valores.append(data.get("observacao"))
    if "categoria" in data:
        # Uma escolha humana, mesmo antes de marcar OK, não pode ser desfeita
        # por uma regra automática criada posteriormente.
        sets.extend(["categoria = %s", "categoria_manual = true", "regra_aplicada_id = NULL"])
        valores.append(data.get("categoria") or None)
    if "natureza" in data:
        sets.append("natureza = %s")
        valores.append(natureza)

    if sets:
        # os trechos do SET sao literais fixos daqui; so os valores vao por parametro
        cur.execute(
            f"UPDATE cartao.transacao SET {', '.join(sets)} WHERE transacao_id = %s;",
            valores + [transacao_id],
        )
    classificacoes_herdadas = {"categorias": 0, "dimensoes": 0}
    if data.get("categoria") or any(item[1] is not None for item in dimensoes_validadas):
        classificacoes_herdadas = preencher_classificacao_vazia_parcelas(cur)
    conn.commit()
    cur.close()
    conn.close()
    conferida_por = (
        session.get("user") if "conferida" in data and conferida_final
        else (None if "conferida" in data else transacao[1])
    )
    duplicada_final = bool(data.get("duplicada")) if "duplicada" in data else bool(transacao[2])
    if "conferida" in data:
        registrar_mudanca_auditoria("Conferida", bool(transacao[0]), conferida_final)
    if "duplicada" in data:
        registrar_mudanca_auditoria("Duplicada", bool(transacao[2]), duplicada_final)
    if "observacao" in data:
        registrar_mudanca_auditoria("Observação", transacao[4], data.get("observacao"))
    if "categoria" in data:
        categoria_nova = data.get("categoria") or None
        registrar_mudanca_auditoria(
            "Categoria",
            {"chave": transacao[3], "nome": cat_pt_puro(transacao[3])} if transacao[3] else None,
            {"chave": categoria_nova, "nome": cat_pt_puro(categoria_nova)} if categoria_nova else None,
        )
    if "natureza" in data:
        registrar_mudanca_auditoria("Natureza", transacao[5], natureza)
    for _dim_id, valor_id, nome_dimensao, valor_id_antigo, valor_antigo, valor_novo in dimensoes_validadas:
        registrar_mudanca_auditoria(
            nome_dimensao,
            {"id": valor_id_antigo, "nome": valor_antigo} if valor_id_antigo else None,
            {"id": valor_id, "nome": valor_novo} if valor_id else None,
        )
    if classificacoes_herdadas["categorias"] or classificacoes_herdadas["dimensoes"]:
        registrar_mudanca_auditoria(
            "Classificações herdadas por parcelas vinculadas",
            None,
            classificacoes_herdadas,
        )
    return jsonify({
        "ok": True,
        "bloqueada": bloqueada,
        "faltando": faltando,
        "rateio_invalido": rateio_invalido,
        "pendente_banco": pendente_banco,
        # A tela sincroniza estes dois estados depois de QUALQUER edicao. Assim
        # nunca mostra OK/duplicidade diferentes do que esta salvo no banco.
        "conferida": conferida_final,
        "conferida_por": conferida_por,
        "duplicada": duplicada_final,
    })
