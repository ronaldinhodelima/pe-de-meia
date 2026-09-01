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
    calcular_totais_dre_fatura,
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
    propagar_classificacao_familia_parcelas,
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
    if status not in (
        "todas", "pendente", "conferida", "pendente_banco", "duplicidade",
        "duplicada", "fora_resultado", "somente_conciliacao", "substituido",
        "rateio_incompleto",
    ):
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
    elif status == "pendente_banco":
        where.append("upper(COALESCE(t.status,'')) = 'PENDING'")
    elif status == "fora_resultado":
        where.append("(t.substituido_por IS NOT NULL OR COALESCE(t.somente_conciliacao,false))")
    elif status == "somente_conciliacao":
        where.append("COALESCE(t.somente_conciliacao,false)")
    elif status == "substituido":
        where.append("t.substituido_por IS NOT NULL")
    elif status == "rateio_incompleto":
        where.append(
            "EXISTS (SELECT 1 FROM cartao.transacao_rateio rx WHERE rx.transacao_id=t.transacao_id) "
            "AND ((SELECT COUNT(*) FROM cartao.transacao_rateio rx WHERE rx.transacao_id=t.transacao_id) < 2 "
            "OR (SELECT COALESCE(SUM(rx.valor_brl),0) FROM cartao.transacao_rateio rx "
            "WHERE rx.transacao_id=t.transacao_id) <> COALESCE(t.valor_brl,t.valor_original) "
            "OR EXISTS (SELECT 1 FROM cartao.transacao_rateio rx WHERE rx.transacao_id=t.transacao_id "
            "AND (rx.categoria IS NULL OR rx.categoria='')) "
            "OR EXISTS (SELECT 1 FROM cartao.transacao_rateio rx CROSS JOIN cartao.dimensao dx "
            "LEFT JOIN cartao.transacao_rateio_dimensao rdx ON rdx.rateio_id=rx.id AND rdx.dimensao_id=dx.id "
            "WHERE rx.transacao_id=t.transacao_id AND dx.obrigatoria=true AND rdx.valor_id IS NULL))"
        )

    cur.execute(
        "SELECT t.transacao_id, t.account_id, t.data_transacao, t.descricao, t.categoria, "
        "COALESCE(t.valor_brl, t.valor_original) AS valor, t.valor_original, t.moeda_original, "
        "t.status, t.tipo, t.numero_cartao_final, t.parcela_atual, t.parcela_total, "
        "t.conferida, t.observacao, t.observacao_sistema, t.conferida_por, t.conferida_em, COALESCE(t.duplicada, false) AS duplicada, "
        "t.substituido_por, COALESCE(t.somente_conciliacao, false) AS somente_conciliacao, "
        "COALESCE(t.importado, false) AS importado, t.natureza, t.sincronizado_em, t.primeiro_sincronizado_em, "
        f"{NATUREZA_SQL} AS natureza_efetiva "
        f"FROM cartao.transacao t {JOIN_NATUREZA} WHERE " + " AND ".join(where) + " ORDER BY t.data_transacao DESC;",
        params,
    )
    rows = cur.fetchall()

    # Resumo do periodo, independente do filtro de Status. "Recebidos" conta
    # tudo que chegou ao banco; "reais" conta cada transacao financeira uma
    # vez, mesmo quando o rateio cria varias linhas no DRE.
    where_recebidos = ["t.data_transacao >= %s", "t.data_transacao < %s"]
    params_recebidos = [inicio_mes, fim_mes]
    if origem_sel:
        where_recebidos.append("t.account_id IN %s")
        params_recebidos.append(tuple(origem_sel))
    cur.execute(
        "SELECT COUNT(*) AS total_recebidos FROM cartao.transacao t WHERE "
        + " AND ".join(where_recebidos) + ";",
        params_recebidos,
    )
    resumo = dict(cur.fetchone())

    where_resumo = ["t.data_transacao >= %s", "t.data_transacao < %s", "COALESCE(t.duplicada, false) = false"]
    params_resumo = [inicio_mes, fim_mes]
    if origem_sel:
        where_resumo.append("t.account_id IN %s")
        params_resumo.append(tuple(origem_sel))
    # gasto real = so o que tem natureza de despesa (fatura, transferencia,
    # investimento e compra de bem nao sao gasto - ver NATUREZAS)
    cur.execute(
        f"SELECT COUNT(DISTINCT t.transacao_id) AS total_reais, "
        f"COUNT(DISTINCT t.transacao_id) FILTER (WHERE t.conferida) AS conferidos_reais, "
        f"SUM(CASE WHEN {NATUREZA_SQL} = 'despesa' THEN {VAL_DESPESA} ELSE 0 END) AS gasto_real, "
        f"SUM(CASE WHEN {NATUREZA_SQL} = 'receita' THEN -{VAL_DESPESA} ELSE 0 END) AS receita_mes "
        f"FROM {FINANCEIRO_TABELA} t {JOIN_NATUREZA} WHERE " + " AND ".join(where_resumo) + ";",
        params_resumo,
    )
    resumo.update(dict(cur.fetchone()))

    # Ao abrir exatamente o ciclo de uma fatura de uma unica origem, o PDF e'
    # a autoridade do periodo. Data da compra nao serve para decidir em qual
    # fatura uma parcela caiu; recalcula a despesa sobre as linhas conciliadas.
    if periodo == "intervalo" and len(origem_sel) == 1:
        cur.execute(
            "SELECT id FROM cartao.fatura_importada WHERE account_id=%s "
            "AND periodo_inicio=%s AND periodo_fim=%s ORDER BY id DESC LIMIT 1;",
            (origem_sel[0], inicio_mes.date(), (fim_mes - timedelta(days=1)).date()),
        )
        fatura_do_periodo = cur.fetchone()
        if fatura_do_periodo:
            total_fatura_dre = calcular_totais_dre_fatura(cur, fatura_do_periodo["id"])
            resumo["gasto_real"] = total_fatura_dre["despesas_dre"]

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
    principal_por_registro_conciliacao = {}
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

        # Uma compra marcada como somente_conciliacao pode estar ligada a uma
        # ou mais parcelas geradas pela fatura. Recolhe sob a parcela somente
        # quando ha UM unico destino visivel no filtro atual; em caso ambiguo,
        # mantem a linha separada para revisao humana.
        ids_conciliacao = [r["transacao_id"] for r in rows if r["somente_conciliacao"]]
        if ids_conciliacao:
            cur.execute(
                "SELECT DISTINCT fv.transacao_id, fl.transacao_id_criado "
                "FROM cartao.fatura_vinculo fv "
                "JOIN cartao.fatura_linha fl ON fl.id=fv.fatura_linha_id "
                "WHERE fv.transacao_id IN %s AND fl.transacao_id_criado IN %s "
                "AND fl.transacao_id_criado<>fv.transacao_id;",
                (tuple(ids_conciliacao), tuple(ids_visiveis)),
            )
            destinos = {}
            for vinculo in cur.fetchall():
                destinos.setdefault(str(vinculo["transacao_id"]), set()).add(
                    str(vinculo["transacao_id_criado"])
                )
            principal_por_registro_conciliacao = {
                tecnico_id: next(iter(alvos))
                for tecnico_id, alvos in destinos.items() if len(alvos) == 1
            }

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
        situacoes = []
        if r["conferida"]:
            situacoes.append({"classe": "conferida", "rotulo": "Conferido"})
        if r["duplicada"]:
            situacoes.append({"classe": "duplicada", "rotulo": "Duplicado confirmado — não contabilizado"})
        if str(rid) in ids_suspeitos:
            situacoes.append({"classe": "suspeita", "rotulo": "Possível duplicidade — revisar"})
        if pendente_banco:
            situacoes.append({"classe": "pendente-banco", "rotulo": "Pendente no banco"})
        if r["substituido_por"]:
            situacoes.append({"classe": "fora", "rotulo": "Fora do resultado — substituído por outro lançamento"})
        elif r["somente_conciliacao"]:
            situacoes.append({"classe": "fora", "rotulo": "Fora do resultado — somente conciliação"})
        if rateios_ui and not rateio_valido:
            situacoes.append({"classe": "rateio", "rotulo": "Rateio incompleto"})
        linhas_tabela.append({
            "id": str(rid),
            "substituido_por": str(r["substituido_por"]) if r["substituido_por"] else None,
            "principal_conciliacao": principal_por_registro_conciliacao.get(str(rid)),
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
            "observacao_sistema": r["observacao_sistema"] or "",
            "conferida": r["conferida"],
            "duplicada": r["duplicada"],
            "suspeita_duplicidade": str(rid) in ids_suspeitos,
            "rateios": rateios_ui,
            "rateio_valido": rateio_valido,
            "valor_rateio": float(abs(valor_pai_rateio)),
            "registros_tecnicos": [],
            "situacoes": situacoes,
            "situacoes_texto": " · ".join(s["rotulo"] for s in situacoes) or "Lançamento contabilizado",
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
            "observacao_sistema": r["observacao_sistema"] or "",
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
        alvo_id = linha["substituido_por"] or linha["principal_conciliacao"]
        alvo = linhas_por_id.get(alvo_id)
        if alvo is not None and alvo is not linha:
            alvo["registros_tecnicos"].append(linha)
        else:
            linhas_principais.append(linha)
    for linha in linhas_tabela:
        linha.pop("substituido_por", None)
        linha.pop("principal_conciliacao", None)
    linhas_tabela = linhas_principais

    gasto_real = resumo["gasto_real"] or 0
    receita_mes = resumo["receita_mes"] or 0
    categorias_template = [{"chave": c, "nome": cat_pt_puro(c)} for c in categorias]
    config_lancamentos = {
        "pode_editar": pode_editar,
        "pode_conferir": pode_conferir,
        "origens_credito": [
            aid for aid, _curto, _completo, _texto, _selo in origem_opcoes
            if contas_by_id[aid]["tipo"] == "CREDIT"
        ],
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
        conf=resumo["conferidos_reais"] or 0,
        total=resumo["total_reais"] or 0,
        conf_reais=resumo["conferidos_reais"] or 0,
        total_reais=resumo["total_reais"] or 0,
        total_recebidos=resumo["total_recebidos"] or 0,
        total_fora=max((resumo["total_recebidos"] or 0) - (resumo["total_reais"] or 0), 0),
        crescimento=crescimento,
        detalhes_json=json_script(detalhes_js),
        config_json=json_script(config_lancamentos),
    )


def _eh_pagamento_fatura(descricao):
    texto = (descricao or "").strip().upper()
    return texto.startswith(("PAGAMENTO RECEBIDO", "PAG DE FATURA"))


def _diferenca_valor_linha_fatura(valor_pdf, parcela_total, valor_lancamento):
    """Diferenca real entre a cobranca e o registro financeiro vinculado.

    Na primeira aparicao de um parcelamento, o Pluggy pode guardar a compra
    inteira enquanto o PDF mostra somente a parcela. Isso e uma representacao
    agregada valida, nao uma divergencia. Pequenas diferencas de centavos vêm
    do arredondamento das parcelas; a conciliacao ja adota tolerancia de R$ 1.
    """
    pdf = abs(Decimal(str(valor_pdf or 0)))
    lancamento = abs(Decimal(str(valor_lancamento or 0)))
    if parcela_total and int(parcela_total) > 1:
        esperado_agregado = pdf * int(parcela_total)
        if abs(lancamento - esperado_agregado) <= Decimal("1.00"):
            return Decimal("0")
    return abs(pdf - lancamento)


def _candidatos_fatura_equivalentes(candidatos):
    """Reconhece ecos tecnicos da mesma cobranca sem apagar nenhum registro.

    Quando o PDF possui uma unica linha e o Pluggy entrega duas representacoes
    no mesmo instante, cartão e valor, uma delas pode ser o lançamento
    contabilizado e as demais permanecem para auditoria. Isso não é uma
    divergência financeira nem exige que o usuário escolha entre cópias
    indistinguíveis.
    """
    if len(candidatos) < 2:
        return False
    assinaturas = {
        (
            c.get("data_local"),
            abs(Decimal(str(c.get("valor") or 0))),
            c.get("numero_cartao_final"),
        )
        for c in candidatos
    }
    return len(assinaturas) == 1


def _render_fatura_em_andamento(cur, account_id, contas_credito, contas_by_id):
    """Mostra o ciclo atual do Pluggy sem fingir que ja existe um PDF oficial."""
    cur.execute(
        "SELECT * FROM cartao.fatura_importada WHERE account_id=%s "
        "ORDER BY ano_referencia DESC, mes_referencia DESC, id DESC LIMIT 1;",
        (account_id,),
    )
    ultima = cur.fetchone()
    if not ultima or not ultima["periodo_fim"]:
        return None
    inicio = ultima["periodo_fim"] + timedelta(days=1)
    hoje = datetime.now(FUSO_LOCAL).date()
    mes = ultima["mes_referencia"] + 1
    ano = ultima["ano_referencia"]
    if mes == 13:
        mes, ano = 1, ano + 1

    cur.execute(
        "SELECT t.transacao_id, t.data_transacao, t.descricao, t.descricao_bruta, "
        "COALESCE(t.valor_brl,t.valor_original) AS valor, t.valor_original, t.moeda_original, "
        "t.categoria, t.observacao, t.observacao_sistema, t.conferida, t.conferida_por, "
        "t.conferida_em, t.numero_cartao_final, t.parcela_atual, t.parcela_total, t.status, t.tipo, "
        "t.sincronizado_em, t.primeiro_sincronizado_em, false AS duplicada, NULL AS substituido_por, "
        "false AS somente_conciliacao, " + NATUREZA_SQL + " AS natureza_efetiva "
        "FROM cartao.transacao t " + JOIN_NATUREZA + " WHERE t.account_id=%s "
        "AND (" + DATA_LOCAL_SQL + ")::date >= %s AND (" + DATA_LOCAL_SQL + ")::date <= %s "
        "AND COALESCE(t.duplicada,false)=false AND t.substituido_por IS NULL "
        "AND COALESCE(t.somente_conciliacao,false)=false ORDER BY t.data_transacao, t.transacao_id;",
        (account_id, inicio, hoje),
    )
    transacoes = [dict(r) for r in cur.fetchall()]
    ids = [r["transacao_id"] for r in transacoes]

    cur.execute("SELECT id, nome, obrigatoria FROM cartao.dimensao ORDER BY ordem, nome;")
    dimensoes = cur.fetchall()
    obrigatorias = {d["id"] for d in dimensoes if d["obrigatoria"]}
    nomes_dimensoes = {d["id"]: d["nome"] for d in dimensoes}
    ids_dimensoes = {chave_alfa(d["nome"]): d["id"] for d in dimensoes}
    cur.execute(
        "SELECT id, dimensao_id, nome, icone, portfolio_valor_id "
        "FROM cartao.dimensao_valor ORDER BY nome;"
    )
    valores_por_dim, projeto_portfolio_map = {}, {}
    for valor in cur.fetchall():
        valores_por_dim.setdefault(valor["dimensao_id"], []).append(valor)
        if valor["portfolio_valor_id"]:
            projeto_portfolio_map[str(valor["id"])] = str(valor["portfolio_valor_id"])
    dims_por_tx = {}
    if ids:
        cur.execute(
            "SELECT transacao_id, dimensao_id, valor_id FROM cartao.transacao_dimensao "
            "WHERE transacao_id IN %s;", (tuple(ids),),
        )
        for row in cur.fetchall():
            dims_por_tx.setdefault(str(row["transacao_id"]), {})[row["dimensao_id"]] = row["valor_id"]

    cur.execute(f"SELECT DISTINCT categoria FROM {FINANCEIRO_TABELA} WHERE categoria IS NOT NULL;")
    categorias_db = {r["categoria"] for r in cur.fetchall()}
    categorias = sorted(
        (categorias_db | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB)) - CATEGORIAS_OCULTAS,
        key=lambda c: chave_alfa(cat_pt_puro(c)),
    )
    cur.execute("SELECT final4, prefixo FROM cartao.cartao_nome;")
    nomes_cartao = {r["final4"]: r["prefixo"] for r in cur.fetchall()}

    linhas, total, total_dre, total_fora, classificadas = [], Decimal("0"), Decimal("0"), Decimal("0"), 0
    for indice, tx in enumerate(transacoes, 1):
        tid = str(tx["transacao_id"])
        tx["transacao_id"] = tid
        tx["data_local"] = data_hora_local(tx.pop("data_transacao"))
        tx["conferida_local"] = data_hora_local(tx.pop("conferida_em"))
        tx["sincronizado_local"] = data_hora_local(tx.pop("sincronizado_em"))
        tx["primeiro_sincronizado_local"] = data_hora_local(tx.pop("primeiro_sincronizado_em"))
        tx["elegivel"] = True
        tx["principal"], tx["tecnico"], tx["fonte"], tx["fonte_nome"] = True, False, "P", "Pluggy"
        tx["dims"] = dims_por_tx.get(tid, {})
        tx["rateado"] = False
        faltando = ([] if tx["categoria"] else ["Categoria"]) + [
            nomes_dimensoes[d] for d in obrigatorias if not tx["dims"].get(d)
        ]
        completo = not faltando
        classificadas += int(completo)
        valor = Decimal(str(tx["valor"] or 0))
        total += valor
        if tx["natureza_efetiva"] == "despesa":
            total_dre += valor
        else:
            total_fora += valor
        linhas.append({
            "id": "andamento-" + str(indice), "data": tx["data_local"].date(),
            "descricao": tx["descricao"], "titular": None,
            "parcela_atual": tx["parcela_atual"], "parcela_total": tx["parcela_total"],
            "valor": valor, "pagamento": False, "vinculos": [tx], "principal": tx,
            "multiplos": False, "requer_validacao": False, "validacao_motivos": [],
            "faltando": faltando, "classificada": completo, "conferida": bool(tx["conferida"]),
            "natureza_estado": "dre" if tx["natureza_efetiva"] == "despesa" else "fora",
            "natureza_rotulo": NATUREZAS.get(tx["natureza_efetiva"], tx["natureza_efetiva"]),
            "cartao_final": tx["numero_cartao_final"],
            "cartao_nome": nomes_cartao.get(tx["numero_cartao_final"]),
            "estado": "andamento",
        })

    status = request.args.get("status", "todas")
    if status not in {"todas", "pendente_classificacao", "dre", "fora"}:
        status = "todas"
    linhas_visiveis = [l for l in linhas if (
        status == "todas" or
        (status == "pendente_classificacao" and not l["classificada"]) or
        (status == "dre" and l["natureza_estado"] == "dre") or
        (status == "fora" and l["natureza_estado"] == "fora")
    )]
    fatura = {
        "id": "andamento", "mes_referencia": mes, "ano_referencia": ano,
        "periodo_inicio": inicio, "periodo_fim": hoje, "vencimento": None,
        "em_andamento": True,
    }
    oficiais = []
    cur.execute(
        "SELECT id, mes_referencia, ano_referencia FROM cartao.fatura_importada "
        "WHERE account_id=%s ORDER BY ano_referencia DESC, mes_referencia DESC, id DESC;", (account_id,),
    )
    oficiais = cur.fetchall()
    config = {
        "pode_editar": pode("lancamentos_editar"), "pode_conferir": False,
        "em_andamento": True, "dimensoes_obrigatorias": [str(x) for x in obrigatorias],
        "projeto_portfolio_map": projeto_portfolio_map,
        "dim_id_projeto": str(ids_dimensoes.get("projeto") or ""),
        "dim_id_portfolio": str(ids_dimensoes.get("portfolio") or ""),
    }
    return render_template(
        "lancamentos_fatura.html", titulo="Fatura em andamento",
        topbar=topbar_html("Lançamentos", "inicio"), fatura=fatura,
        fatura_nova=None, fatura_antiga=oficiais[0] if oficiais else None,
        faturas=[fatura] + oficiais, conta=contas_by_id.get(account_id),
        contas_credito=contas_credito, account_id=account_id, linhas=linhas_visiveis,
        categorias=[{"chave": c, "nome": cat_pt_puro(c)} for c in categorias],
        dimensoes=dimensoes, valores_por_dim=valores_por_dim, status=status,
        totais={"pdf": total, "dre": total_dre, "fora": total_fora, "pendente": sum(abs(l["valor"]) for l in linhas if not l["classificada"]), "pendente_ok": Decimal("0"), "sem_vinculo": Decimal("0"), "divergencia": Decimal("0")},
        contagens={"linhas": len(linhas), "vinculadas": len(linhas), "classificadas": classificadas, "conferidas": 0, "multiplos": 0, "pendente_classificacao": len(linhas)-classificadas, "pendente_ok": 0, "divergencias": 0},
        config_json=json_script(config), projeto_portfolio_map=projeto_portfolio_map,
        url_resumida=f"/?periodo=intervalo&data_inicio={inicio.isoformat()}&data_fim={hoje.isoformat()}&origem={account_id}&status=todas",
        pode_editar=pode("lancamentos_editar"), pode_conferir=False,
    )


@bp.route("/lancamentos/fatura")
@requer("lancamentos_ver")
def lancamentos_por_fatura():
    """Revisao contabil de uma fatura, sem confundir data de compra com ciclo.

    Cada linha principal vem do PDF. Os varios registros que explicam a linha
    ficam agrupados no sinal +; somente o registro financeiro escolhido conta
    no DRE e pode ser editado aqui.
    """
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Regras sao do lancamento, nao da tela. Abrir diretamente a visao
    # detalhada precisa aplicar exatamente as mesmas regras da resumida.
    regras_resultado = aplicar_regras(cur)
    conn.commit()
    if (
        regras_resultado["lancamentos"] or regras_resultado["dimensoes"]
        or regras_resultado["erro"] or regras_resultado["duplicatas_ignoradas"]
    ):
        registrar_auditoria(
            "regra_automatica", "classificacao",
            sucesso=not bool(regras_resultado["erro"]), detalhes=regras_resultado,
        )
    contas_by_id, origem_opcoes = carregar_origens(cur)
    contas_credito = [o for o in origem_opcoes if contas_by_id[o[0]]["tipo"] == "CREDIT"]
    account_id = request.args.get("account_id") or ""
    fatura_id = request.args.get("fatura_id", type=int)
    em_andamento = request.args.get("andamento") == "1"

    if not account_id and contas_credito:
        account_id = contas_credito[0][0]
    if em_andamento:
        resposta = _render_fatura_em_andamento(cur, account_id, contas_credito, contas_by_id)
        if resposta is not None:
            cur.close()
            conn.close()
            return resposta
    if not fatura_id and "fatura_id" not in request.args:
        resposta = _render_fatura_em_andamento(cur, account_id, contas_credito, contas_by_id)
        if resposta is not None:
            cur.close()
            conn.close()
            return resposta

    if fatura_id:
        cur.execute(
            "SELECT f.*, c.tipo FROM cartao.fatura_importada f "
            "JOIN cartao.conta c ON c.account_id=f.account_id WHERE f.id=%s;",
            (fatura_id,),
        )
        fatura = cur.fetchone()
        if fatura:
            account_id = str(fatura["account_id"])
    else:
        if not account_id and contas_credito:
            account_id = contas_credito[0][0]
        cur.execute(
            "SELECT f.*, c.tipo FROM cartao.fatura_importada f "
            "JOIN cartao.conta c ON c.account_id=f.account_id "
            "WHERE f.account_id=%s ORDER BY f.ano_referencia DESC, f.mes_referencia DESC, f.id DESC LIMIT 1;",
            (account_id,),
        )
        fatura = cur.fetchone()
        fatura_id = fatura["id"] if fatura else None

    if not fatura_id or not fatura:
        cur.close()
        conn.close()
        return render_template(
            "lancamentos_fatura.html", titulo="Lançamentos por fatura",
            topbar=topbar_html("Lançamentos", "inicio"), fatura=None,
            contas_credito=contas_credito, account_id=account_id, linhas=[],
            erro="Nenhuma fatura importada foi encontrada para este cartão.",
        )

    cur.execute(
        "SELECT id, mes_referencia, ano_referencia, periodo_fim FROM cartao.fatura_importada "
        "WHERE account_id=%s ORDER BY ano_referencia DESC, mes_referencia DESC, id DESC;",
        (account_id,),
    )
    faturas = cur.fetchall()
    ids_faturas = [r["id"] for r in faturas]
    pos = ids_faturas.index(fatura_id)
    fatura_nova = faturas[pos - 1] if pos > 0 else None
    fatura_antiga = faturas[pos + 1] if pos + 1 < len(faturas) else None
    if faturas and faturas[0].get("periodo_fim"):
        prox_mes, prox_ano = faturas[0]["mes_referencia"] + 1, faturas[0]["ano_referencia"]
        if prox_mes == 13:
            prox_mes, prox_ano = 1, prox_ano + 1
        provisoria = {"id": "andamento", "mes_referencia": prox_mes, "ano_referencia": prox_ano, "em_andamento": True}
        if pos == 0:
            fatura_nova = provisoria
        faturas = [provisoria] + faturas

    cur.execute(
        "SELECT fl.* FROM cartao.fatura_linha fl WHERE fl.fatura_id=%s "
        "ORDER BY fl.data, fl.id;",
        (fatura_id,),
    )
    linhas = [dict(r) for r in cur.fetchall()]

    cur.execute(
        f"SELECT v.fatura_linha_id, v.transacao_id, v.origem, v.criado_por, "
        f"t.descricao, t.descricao_bruta, COALESCE(t.valor_brl,t.valor_original) AS valor, "
        f"t.valor_original, t.moeda_original, t.data_transacao, t.categoria, "
        f"t.observacao, t.observacao_sistema, t.conferida, t.conferida_por, t.conferida_em, "
        f"t.numero_cartao_final, t.parcela_atual, t.parcela_total, t.status, t.tipo, "
        f"t.sincronizado_em, t.primeiro_sincronizado_em, "
        f"COALESCE(t.duplicada,false) AS duplicada, t.substituido_por, "
        f"COALESCE(t.somente_conciliacao,false) AS somente_conciliacao, "
        f"{NATUREZA_SQL} AS natureza_efetiva "
        f"FROM cartao.fatura_vinculo v "
        f"JOIN cartao.transacao t ON t.transacao_id=v.transacao_id "
        f"JOIN cartao.fatura_linha fl ON fl.id=v.fatura_linha_id "
        f"{JOIN_NATUREZA} WHERE fl.fatura_id=%s ORDER BY v.id;",
        (fatura_id,),
    )
    vinculos_por_linha = {}
    todos_ids = []
    for r in cur.fetchall():
        item = dict(r)
        transacao_uuid = item["transacao_id"]
        item["transacao_id"] = str(item["transacao_id"])
        item["data_local"] = data_hora_local(item.pop("data_transacao"))
        item["conferida_local"] = data_hora_local(item.pop("conferida_em"))
        item["sincronizado_local"] = data_hora_local(item.pop("sincronizado_em"))
        item["primeiro_sincronizado_local"] = data_hora_local(item.pop("primeiro_sincronizado_em"))
        item["elegivel"] = not (
            item["duplicada"] or item["substituido_por"] or item["somente_conciliacao"]
        )
        vinculos_por_linha.setdefault(item["fatura_linha_id"], []).append(item)
        todos_ids.append(transacao_uuid)

    cur.execute("SELECT id, nome, obrigatoria FROM cartao.dimensao ORDER BY ordem, nome;")
    dimensoes = cur.fetchall()
    obrigatorias = {d["id"] for d in dimensoes if d["obrigatoria"]}
    nomes_dimensoes = {d["id"]: d["nome"] for d in dimensoes}
    ids_dimensoes = {chave_alfa(d["nome"]): d["id"] for d in dimensoes}
    cur.execute(
        "SELECT id, dimensao_id, nome, icone, portfolio_valor_id "
        "FROM cartao.dimensao_valor ORDER BY nome;"
    )
    valores_por_dim = {}
    projeto_portfolio_map = {}
    for v in cur.fetchall():
        valores_por_dim.setdefault(v["dimensao_id"], []).append(v)
        if v["portfolio_valor_id"]:
            projeto_portfolio_map[str(v["id"])] = str(v["portfolio_valor_id"])

    dims_por_tx = {}
    if todos_ids:
        cur.execute(
            "SELECT transacao_id, dimensao_id, valor_id FROM cartao.transacao_dimensao "
            "WHERE transacao_id IN %s;", (tuple(set(todos_ids)),),
        )
        for r in cur.fetchall():
            dims_por_tx.setdefault(str(r["transacao_id"]), {})[r["dimensao_id"]] = r["valor_id"]

    proporcao_dre = {}
    rateados = set()
    rateio_valido = {}
    resumo_rateios = {}
    if todos_ids:
        cur.execute(
            f"SELECT t.transacao_id, COALESCE(SUM(CASE WHEN {NATUREZA_SQL}='despesa' "
            f"THEN ABS(COALESCE(t.valor_brl,t.valor_original)) ELSE 0 END) / "
            f"NULLIF(SUM(ABS(COALESCE(t.valor_brl,t.valor_original))),0),0) AS proporcao "
            f"FROM {FINANCEIRO_TABELA} t {JOIN_NATUREZA} WHERE t.transacao_id IN %s "
            f"AND COALESCE(t.duplicada,false)=false GROUP BY t.transacao_id;",
            (tuple(set(todos_ids)),),
        )
        proporcao_dre = {str(r["transacao_id"]): Decimal(str(r["proporcao"] or 0)) for r in cur.fetchall()}
        cur.execute(
            "SELECT r.transacao_id, r.id, r.valor_brl, r.categoria, "
            "ABS(COALESCE(t.valor_brl,t.valor_original)) AS total, "
            "EXISTS (SELECT 1 FROM cartao.dimensao d LEFT JOIN cartao.transacao_rateio_dimensao rd "
            "ON rd.rateio_id=r.id AND rd.dimensao_id=d.id "
            "WHERE d.obrigatoria=true AND rd.valor_id IS NULL) AS dim_faltando "
            "FROM cartao.transacao_rateio r JOIN cartao.transacao t ON t.transacao_id=r.transacao_id "
            "WHERE r.transacao_id IN %s ORDER BY r.transacao_id, r.ordem, r.id;",
            (tuple(set(todos_ids)),),
        )
        for r in cur.fetchall():
            tid = str(r["transacao_id"])
            item = resumo_rateios.setdefault(tid, {
                "partes": 0, "soma": Decimal("0"), "total": Decimal(str(r["total"] or 0)), "incompleto": False,
            })
            item["partes"] += 1
            item["soma"] += abs(Decimal(str(r["valor_brl"] or 0)))
            item["incompleto"] = item["incompleto"] or not r["categoria"] or r["dim_faltando"]
        for tid, item in resumo_rateios.items():
            rateados.add(tid)
            rateio_valido[tid] = item["partes"] >= 2 and item["soma"] == item["total"] and not item["incompleto"]

    cur.execute(f"SELECT DISTINCT categoria FROM {FINANCEIRO_TABELA} WHERE categoria IS NOT NULL;")
    categorias_db = {r["categoria"] for r in cur.fetchall()}
    categorias = sorted(
        (categorias_db | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB)) - CATEGORIAS_OCULTAS,
        key=lambda c: chave_alfa(cat_pt_puro(c)),
    )
    categorias_template = [{"chave": c, "nome": cat_pt_puro(c)} for c in categorias]
    cur.execute("SELECT final4, prefixo FROM cartao.cartao_nome;")
    nomes_cartao = {r["final4"]: r["prefixo"] for r in cur.fetchall()}

    total_pdf = Decimal("0")
    total_dre = Decimal("0")
    total_fora = Decimal("0")
    total_pendente = Decimal("0")
    total_pendente_ok = Decimal("0")
    total_sem_vinculo = Decimal("0")
    total_divergencia = Decimal("0")
    contagens = {
        "linhas": 0, "vinculadas": 0, "classificadas": 0,
        "conferidas": 0, "multiplos": 0, "pendente_classificacao": 0,
        "pendente_ok": 0, "divergencias": 0,
    }
    tolerancia_valor = Decimal("0.01")
    for linha in linhas:
        linha["pagamento"] = _eh_pagamento_fatura(linha["descricao"])
        vinculos = vinculos_por_linha.get(linha["id"], [])
        criado = str(linha["transacao_id_criado"]) if linha["transacao_id_criado"] else None
        elegiveis = [v for v in vinculos if v["elegivel"]]
        elegiveis.sort(key=lambda v: (
            0 if v["transacao_id"] == criado else 1,
            abs(abs(Decimal(str(v["valor"] or 0))) - abs(Decimal(str(linha["valor"] or 0)))),
        ))
        principal = elegiveis[0] if elegiveis else None
        if linha["pagamento"]:
            # Pagamento recebido quita a fatura anterior; nesta fatura e' uma
            # linha apenas informativa e nenhum vinculo deve virar editavel.
            principal = None
        for v in vinculos:
            v["principal"] = bool(principal and v["transacao_id"] == principal["transacao_id"])
            v["tecnico"] = not v["principal"]
            v["fonte"] = "F" if v["transacao_id"] == criado else "P"
            v["fonte_nome"] = "Fatura em PDF" if v["fonte"] == "F" else "Pluggy"
        linha["vinculos"] = vinculos
        linha["principal"] = principal
        linha["multiplos"] = len(vinculos) > 1
        linha["ambigua"] = bool(
            len(elegiveis) > 1
            and not _candidatos_fatura_equivalentes(elegiveis)
            and not (criado and any(v["transacao_id"] == criado for v in elegiveis))
        )
        linha["requer_validacao"] = False
        linha["validacao_motivos"] = []
        linha["diferenca_valor"] = Decimal("0")
        final_cartao = next(
            (v["numero_cartao_final"] for v in ([principal] if principal else []) + vinculos
             if v and v.get("numero_cartao_final")),
            None,
        )
        linha["cartao_final"] = final_cartao
        linha["cartao_nome"] = (
            nomes_cartao.get(final_cartao) or (f"final {final_cartao}" if final_cartao else None)
        )
        if linha["multiplos"]:
            contagens["multiplos"] += 1
        # Estorno vem negativo no PDF e precisa reduzir tanto a fatura quanto
        # o DRE. `abs` aqui inflaria o total em duas vezes o estorno.
        valor_pdf = Decimal(str(linha["valor"] or 0))
        if linha["pagamento"]:
            linha["estado"] = "pagamento"
            linha["classificada"] = False
            linha["conferida"] = False
            continue
        contagens["linhas"] += 1
        total_pdf += valor_pdf
        if principal:
            contagens["vinculadas"] += 1
            tid = principal["transacao_id"]
            principal["dims"] = dims_por_tx.get(tid, {})
            principal["rateado"] = tid in rateados
            completa = (
                rateio_valido.get(tid, False) if principal["rateado"] else
                bool(principal["categoria"]) and obrigatorias.issubset({k for k, v in principal["dims"].items() if v})
            )
            linha["classificada"] = completa
            linha["conferida"] = bool(principal["conferida"])
            faltando = []
            if principal["rateado"]:
                if not rateio_valido.get(tid, False):
                    faltando.append("Rateio")
            else:
                if not principal["categoria"]:
                    faltando.append("Categoria")
                faltando.extend(
                    nomes_dimensoes[dim_id] for dim_id in obrigatorias
                    if not principal["dims"].get(dim_id)
                )
            linha["faltando"] = faltando
            proporcao = proporcao_dre.get(tid, Decimal("0"))
            linha["valor_dre"] = valor_pdf * proporcao
            linha["valor_fora"] = valor_pdf - linha["valor_dre"]
            total_dre += linha["valor_dre"]
            total_fora += linha["valor_fora"]
            linha["natureza_estado"] = "dre" if proporcao == 1 else ("fora" if proporcao == 0 else "misto")
            linha["natureza_rotulo"] = NATUREZAS.get(
                principal["natureza_efetiva"], principal["natureza_efetiva"]
            )
            valor_base = (
                resumo_rateios[tid]["soma"] if principal["rateado"]
                else abs(Decimal(str(principal["valor"] or 0)))
            )
            linha["diferenca_valor"] = _diferenca_valor_linha_fatura(
                valor_pdf, linha.get("parcela_total"), valor_base
            )
            if linha["ambigua"]:
                linha["validacao_motivos"].append("mais de um lançamento possível")
            if linha["diferenca_valor"] > tolerancia_valor:
                linha["validacao_motivos"].append(
                    "valor difere em " + str(linha["diferenca_valor"].quantize(Decimal("0.01")))
                )
            linha["requer_validacao"] = bool(linha["validacao_motivos"])
            if completa:
                contagens["classificadas"] += 1
                linha["estado"] = linha["natureza_estado"]
            else:
                total_pendente += abs(valor_pdf)
                contagens["pendente_classificacao"] += 1
                linha["estado"] = "classificar"
        else:
            linha["classificada"] = False
            linha["conferida"] = False
            linha["faltando"] = ["Vínculo"]
            total_pendente += abs(valor_pdf)
            contagens["pendente_classificacao"] += 1
            total_sem_vinculo += abs(valor_pdf)
            linha["natureza_estado"] = "pendente"
            linha["estado"] = "sem_vinculo"
            linha["diferenca_valor"] = abs(valor_pdf)
            linha["validacao_motivos"] = ["falta vínculo"]
            linha["requer_validacao"] = True
        if linha["requer_validacao"]:
            contagens["divergencias"] += 1
            total_divergencia += linha["diferenca_valor"]
        if linha["conferida"]:
            contagens["conferidas"] += 1
        else:
            contagens["pendente_ok"] += 1
            total_pendente_ok += abs(valor_pdf)

    status = request.args.get("status", "todas")
    filtros_validos = {
        "todas", "pendente_classificacao", "pendente_ok", "dre", "fora",
        "sem_vinculo", "requer_validacao", "multiplos",
    }
    if status not in filtros_validos:
        status = "todas"
    linhas_visiveis = [l for l in linhas if (
        status == "todas" or
        (status == "pendente_classificacao" and not l["pagamento"] and not l["classificada"]) or
        (status == "pendente_ok" and not l["pagamento"] and not l["conferida"]) or
        (status == "dre" and l.get("natureza_estado") in {"dre", "misto"}) or
        (status == "fora" and l.get("natureza_estado") in {"fora", "misto"}) or
        (status == "sem_vinculo" and l["estado"] == "sem_vinculo") or
        (status == "requer_validacao" and l["requer_validacao"]) or
        (status == "multiplos" and l["multiplos"])
    )]

    config = {
        "pode_editar": pode("lancamentos_editar"),
        "pode_conferir": pode("lancamentos_conferir"),
        "dimensoes_obrigatorias": [str(x) for x in obrigatorias],
        "projeto_portfolio_map": projeto_portfolio_map,
        "dim_id_projeto": str(ids_dimensoes.get("projeto") or ""),
        "dim_id_portfolio": str(ids_dimensoes.get("portfolio") or ""),
    }
    conta = contas_by_id.get(account_id)
    cur.close()
    conn.close()
    if fatura.get("periodo_inicio") and fatura.get("periodo_fim"):
        url_resumida = (
            "/?periodo=intervalo&data_inicio=" + fatura["periodo_inicio"].isoformat()
            + "&data_fim=" + fatura["periodo_fim"].isoformat()
            + "&origem=" + account_id + "&status=todas"
        )
    else:
        url_resumida = (
            f"/?mes={fatura['ano_referencia']}-{fatura['mes_referencia']:02d}"
            f"&periodo=mes&origem={account_id}&status=todas"
        )
    return render_template(
        "lancamentos_fatura.html", titulo="Lançamentos por fatura",
        topbar=topbar_html("Lançamentos", "inicio"), fatura=fatura,
        fatura_nova=fatura_nova, fatura_antiga=fatura_antiga,
        faturas=faturas, conta=conta, contas_credito=contas_credito,
        account_id=account_id, linhas=linhas_visiveis, categorias=categorias_template,
        dimensoes=dimensoes, valores_por_dim=valores_por_dim, status=status,
        totais={
            "pdf": total_pdf, "dre": total_dre, "fora": total_fora,
            "pendente": total_pendente, "pendente_ok": total_pendente_ok,
            "sem_vinculo": total_sem_vinculo, "divergencia": total_divergencia,
        },
        contagens=contagens, config_json=json_script(config),
        projeto_portfolio_map=projeto_portfolio_map,
        url_resumida=url_resumida,
        pode_editar=pode("lancamentos_editar"), pode_conferir=pode("lancamentos_conferir"),
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
        "t.conferida, t.observacao, t.observacao_sistema, t.conferida_por, t.natureza, "
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
        "observacao_sistema": r["observacao_sistema"] or "-",
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
        "categoria, observacao, natureza, COALESCE(valor_brl,valor_original), status, data_transacao, "
        "(SELECT tipo FROM cartao.conta c WHERE c.account_id=cartao.transacao.account_id) "
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
            "SELECT r.id, d.nome FROM cartao.transacao_rateio r CROSS JOIN cartao.dimensao d "
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
            "SELECT d.nome FROM cartao.dimensao d "
            "LEFT JOIN cartao.transacao_dimensao td ON td.dimensao_id = d.id AND td.transacao_id = %s "
            "WHERE d.obrigatoria = true AND (td.valor_id IS NULL);",
            (transacao_id,),
        )
        faltando = [r[0] for r in cur.fetchall()]
        categoria_final = (
            (data.get("categoria") or None) if "categoria" in data else transacao[3]
        )
        if not categoria_final:
            faltando.append("categoria")
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
    if pendente_banco:
        # O PDF oficial encerra a incerteza do status PENDING do Pluggy. Se a
        # cobranca ja foi conciliada a uma fatura, ela pode receber OK.
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM cartao.fatura_vinculo WHERE transacao_id=%s);",
            (transacao_id,),
        )
        pendente_banco = not bool(cur.fetchone()[0])
    alterando_conferencia = "conferida" in data
    conferida_solicitada = bool(data.get("conferida")) if alterando_conferencia else conferida_atual
    sem_pdf_conciliado = False
    if alterando_conferencia and conferida_solicitada and transacao[9] == "CREDIT":
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM cartao.fatura_vinculo WHERE transacao_id=%s);",
            (transacao_id,),
        )
        sem_pdf_conciliado = not bool(cur.fetchone()[0])
    # Campos obrigatorios (e o status pendente) bloqueiam somente uma NOVA
    # marcacao de OK. Uma edicao de categoria/dimensao/observacao jamais pode
    # desmarcar um OK ja existente.
    bloqueada = (
        bool(faltando or rateio_invalido or pendente_banco or sem_pdf_conciliado)
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
    classificacoes_compartilhadas = {
        "membros": 0, "categorias": 0, "dimensoes": 0, "observacoes": 0,
    }
    if "categoria" in data or dimensoes_validadas or "observacao" in data:
        # A classificacao pertence a compra parcelada inteira. Mesmo que esta
        # requisicao tenha alterado so um campo, envia para a familia todo o
        # conjunto ja definido no membro editado. Assim uma observacao nova,
        # por exemplo, tambem completa Categoria/Responsavel/Projeto/Portfolio
        # das demais parcelas sem exigir quatro edicoes separadas.
        cur.execute(
            "SELECT categoria, observacao FROM cartao.transacao WHERE transacao_id=%s;",
            (transacao_id,),
        )
        classificacao_atual = cur.fetchone()
        categoria_familia = classificacao_atual[0]
        observacao_familia = classificacao_atual[1]
        cur.execute(
            "SELECT dimensao_id, valor_id FROM cartao.transacao_dimensao "
            "WHERE transacao_id=%s AND valor_id IS NOT NULL;",
            (transacao_id,),
        )
        dimensoes_familia = {item[0]: item[1] for item in cur.fetchall()}
        # Uma limpeza explicita tambem precisa alcançar as outras parcelas.
        for item in dimensoes_validadas:
            dimensoes_familia[item[0]] = item[1]
        classificacoes_compartilhadas = propagar_classificacao_familia_parcelas(
            cur,
            transacao_id,
            categoria_enviada=bool(categoria_familia) or "categoria" in data,
            categoria=categoria_familia,
            dimensoes=dimensoes_familia,
            observacao_enviada=bool(observacao_familia) or "observacao" in data,
            observacao=observacao_familia,
        )
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
    if (
        classificacoes_compartilhadas["categorias"]
        or classificacoes_compartilhadas["dimensoes"]
        or classificacoes_compartilhadas["observacoes"]
    ):
        registrar_mudanca_auditoria(
            "Classificação compartilhada entre parcelas vinculadas",
            None,
            classificacoes_compartilhadas,
        )
    return jsonify({
        "ok": True,
        "bloqueada": bloqueada,
        "faltando": faltando,
        "rateio_invalido": rateio_invalido,
        "pendente_banco": pendente_banco,
        "sem_pdf_conciliado": sem_pdf_conciliado,
        # A tela sincroniza estes dois estados depois de QUALQUER edicao. Assim
        # nunca mostra OK/duplicidade diferentes do que esta salvo no banco.
        "conferida": conferida_final,
        "conferida_por": conferida_por,
        "duplicada": duplicada_final,
    })
