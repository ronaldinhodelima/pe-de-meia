"""Relatorios, DRE e investimentos."""
import io
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

import psycopg2
import psycopg2.extras
from flask import Blueprint, Response, request, jsonify, render_template, session, redirect

from fatura_unicred import extrair_fatura, FaturaInvalida
from core import (
    CATEGORIAS_EXTRA,
    CATEGORIAS_OCULTAS,
    CATEGORIA_PT_DB,
    DATA_LOCAL_SQL,
    FINANCEIRO_DIM_TABELA,
    FINANCEIRO_TABELA,
    JOIN_NATUREZA,
    MESES_ABREV,
    NATUREZA_SQL,
    VAL_DESPESA,
    _montar_filtro_relatorio,
    aplicar_regras,
    carregar_origens,
    rotulo_valor_dimensao,
    cat_pt_puro,
    calcular_totais_dre_fatura,
    chave_alfa,
    chip_filter_html,
    data_hora_local,
    get_conn,
    intervalo_ano_local,
    levantar_pendencias,
    pode,
    preencher_classificacao_vazia_parcelas,
    registrar_auditoria,
    registrar_mudanca_auditoria,
    requer,
    topbar_html,
    CONTA_UNICRED,
    aplicar_consenso_classificacao,
    _normalizar_desc,
    _tokens_significativos,
    exige_dimensoes,
    _canonizar_v45,
    _consenso_por_categoria,
    _consenso_por_lojista,
    _dimensao_vazia,
    _loja_v45,
)

bp = Blueprint("relatorios", __name__)

CENTAVO = Decimal("0.01")


def _decimal_monetario(valor):
    """Normaliza dinheiro sem carregar o erro binario de ``float``.

    O banco entrega ``Decimal`` e alguns testes/integrações ainda entregam
    ``float``. Converter pelo texto preserva o valor humano e arredondar uma
    unica vez, com meio centavo para cima, deixa a regra explicita.
    """
    if valor is None or valor == "":
        valor = 0
    if not isinstance(valor, Decimal):
        valor = Decimal(str(valor))
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _centavos(valor):
    """Representacao inteira usada em somas, comparacoes e tolerancias."""
    return int(_decimal_monetario(valor) * 100)


def _reais(valor_centavos):
    """Mantem o contrato atual das telas/APIs, que recebem numeros JSON."""
    return float(Decimal(valor_centavos) / 100)


def _montar_historico_investimentos(historico):
    """Transforma posicoes mensais ascendentes em linhas recentes primeiro.

    A variacao pertence ao mes mais novo: agosto mostra agosto menos julho.
    """
    linhas = []
    saldo_anterior = None
    for h in historico:
        saldo = float(h["saldo"] or 0)
        mes = h["mes"]
        linhas.append({
            "rotulo": f"{MESES_ABREV[int(mes[5:7]) - 1]}/{mes[2:4]}",
            "aplicado": float(h["aplicado"] or 0),
            "saldo": saldo,
            "variacao": None if saldo_anterior is None else saldo - saldo_anterior,
        })
        saldo_anterior = saldo
    return list(reversed(linhas))


@bp.route("/dre")
@requer("relatorios")
def dre():
    ano = request.args.get("ano") or str(datetime.now().year)
    hoje = datetime.now()
    try:
        inicio_ano, fim_ano = intervalo_ano_local(ano)
    except ValueError:
        ano = str(hoje.year)
        inicio_ano, fim_ano = intervalo_ano_local(ano)

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    base = f"FROM {FINANCEIRO_TABELA} t {JOIN_NATUREZA} WHERE COALESCE(t.duplicada, false) = false "

    cur.execute(
        f"SELECT t.categoria, SUM({VAL_DESPESA}) AS total {base} "
        f"AND t.data_transacao >= %s AND t.data_transacao < %s AND {NATUREZA_SQL} = 'despesa' "
        "AND t.categoria IS NOT NULL GROUP BY t.categoria;",
        (inicio_ano, fim_ano),
    )
    anual_por_cat = {r["categoria"]: float(r["total"]) for r in cur.fetchall()}

    # ---- DRE propriamente dito: receitas, despesas e resultado de cada mes do ano ----
    cur.execute(
        f"SELECT to_char({DATA_LOCAL_SQL},'YYYY-MM') AS mes, {NATUREZA_SQL} AS natureza, "
        f"SUM({VAL_DESPESA}) AS total {base} AND t.data_transacao >= %s AND t.data_transacao < %s "
        f"GROUP BY 1, 2 ORDER BY 1;",
        (inicio_ano, fim_ano),
    )
    meses_dre = {}
    for r in cur.fetchall():
        m = meses_dre.setdefault(r["mes"], {"receita": 0.0, "despesa": 0.0, "investimento": 0.0, "bem": 0.0})
        v = float(r["total"] or 0)
        if r["natureza"] == "receita":
            m["receita"] += -v          # receita entra: VAL_DESPESA e negativo
        elif r["natureza"] == "despesa":
            m["despesa"] += v
        elif r["natureza"] in ("investimento", "bem"):
            m[r["natureza"]] += v       # positivo = dinheiro aplicado/investido no bem

    cur.execute(
        "SELECT g.id AS grupo_id, g.nome AS grupo_nome, "
        "s.id AS subgrupo_id, s.nome AS subgrupo_nome, "
        "cs.categoria "
        "FROM cartao.grupo_custo g "
        "JOIN cartao.subgrupo_custo s ON s.grupo_id = g.id "
        "LEFT JOIN cartao.categoria_subgrupo cs ON cs.subgrupo_id = s.id "
        "ORDER BY g.nome, s.nome;"
    )
    linhas_map = cur.fetchall()

    cur.execute("SELECT id, nome FROM cartao.dimensao ORDER BY ordem, nome;")
    dims = cur.fetchall()
    por_dimensao = []
    for d in dims:
        cur.execute(
            "SELECT COALESCE(dv.nome, '(nao definido)') AS nome, "
            f"SUM({VAL_DESPESA}) AS total "
            f"FROM {FINANCEIRO_TABELA} t {JOIN_NATUREZA} "
            f"LEFT JOIN {FINANCEIRO_DIM_TABELA} td ON td.linha_id = t.linha_id AND td.dimensao_id = %s "
            "LEFT JOIN cartao.dimensao_valor dv ON dv.id = td.valor_id "
            "WHERE t.data_transacao >= %s AND t.data_transacao < %s "
            "AND COALESCE(t.duplicada, false) = false "
            f"AND {NATUREZA_SQL} = 'despesa' AND t.categoria IS NOT NULL "
            "GROUP BY dv.nome ORDER BY total DESC;",
            (d["id"], inicio_ano, fim_ano),
        )
        por_dimensao.append({"nome": d["nome"], "linhas": cur.fetchall()})

    # o DRE e onde a ma classificacao vira numero errado - avisa aqui
    pendencias = levantar_pendencias(cur) if pode("cadastros") else None

    cur.close()
    conn.close()

    grupos = {}
    categorias_mapeadas = set()
    for r in linhas_map:
        g = grupos.setdefault(r["grupo_id"], {
            "nome": r["grupo_nome"],
            "subgrupos": {},
        })
        s = g["subgrupos"].setdefault(r["subgrupo_id"], {
            "nome": r["subgrupo_nome"],
            "categorias": [],
        })
        if r["categoria"]:
            s["categorias"].append(r["categoria"])
            categorias_mapeadas.add(r["categoria"])

    nao_classificadas = sorted(set(anual_por_cat) - categorias_mapeadas)

    # ---- centro de custo: total do ano por grupo > subgrupo ----
    blocos_grupo = []
    for g in sorted(grupos.values(), key=lambda x: chave_alfa(x["nome"])):
        subs = []
        for s in sorted(g["subgrupos"].values(), key=lambda x: chave_alfa(x["nome"])):
            s_anual = sum(anual_por_cat.get(c, 0.0) for c in s["categorias"])
            subs.append({
                "nome": s["nome"],
                "total": s_anual,
                "categorias": ", ".join(cat_pt_puro(c) for c in s["categorias"]) or "sem categorias vinculadas",
            })
        blocos_grupo.append({
            "nome": g["nome"],
            "total": sum(s["total"] for s in subs),
            "subgrupos": subs,
        })

    blocos_dimensao = [{
        "nome": pd["nome"],
        "linhas": [{"nome": l["nome"], "total": float(l["total"] or 0)} for l in pd["linhas"]],
    } for pd in por_dimensao]

    # ---- DRE: um mes por linha, do mais recente para o mais antigo ----
    rec_ano = sum(m["receita"] for m in meses_dre.values())
    desp_ano = sum(m["despesa"] for m in meses_dre.values())
    inv_ano = sum(m["investimento"] + m["bem"] for m in meses_dre.values())

    linhas_dre = []
    for mes_key in sorted(meses_dre, reverse=True):
        m = meses_dre[mes_key]
        res = m["receita"] - m["despesa"]
        linhas_dre.append({
            "rotulo": f'{MESES_ABREV[int(mes_key[5:7]) - 1]}/{mes_key[2:4]}',
            "receita": m["receita"],
            "despesa": m["despesa"],
            "resultado": res,
            "margem": (res / m["receita"] * 100) if m["receita"] else 0,
            "investido": m["investimento"] + m["bem"],
        })

    return render_template(
        "dre.html",
        titulo="DRE / Centro de Custos",
        topbar=topbar_html("DRE / Centro de Custos", "dre"),
        pendencias=pendencias,
        ano=ano,
        anos=list(range(hoje.year - 3, hoje.year + 1)),
        rec_ano=rec_ano,
        desp_ano=desp_ano,
        resultado_ano=rec_ano - desp_ano,
        inv_ano=inv_ano,
        linhas_dre=linhas_dre,
        blocos_dimensao=blocos_dimensao,
        grupos=blocos_grupo,
        nao_classificadas=[
            {"nome": cat_pt_puro(c), "total": anual_por_cat[c]} for c in nao_classificadas
        ],
    )


@bp.route("/relatorios")
@requer("relatorios")
def relatorios():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    regras_resultado = aplicar_regras(cur)
    conn.commit()
    if regras_resultado["lancamentos"] or regras_resultado["dimensoes"] or regras_resultado["erro"]:
        registrar_auditoria(
            "regra_automatica",
            "classificacao",
            sucesso=not bool(regras_resultado["erro"]),
            detalhes=regras_resultado,
        )

    cur.execute("SELECT id, nome, obrigatoria FROM cartao.dimensao ORDER BY ordem, nome;")
    dimensoes = cur.fetchall()
    cur.execute("SELECT id, dimensao_id, nome, icone FROM cartao.dimensao_valor ORDER BY nome;")
    valores_por_dim = {}
    for v in cur.fetchall():
        valores_por_dim.setdefault(v["dimensao_id"], []).append(v)

    cur.execute("SELECT final4, prefixo FROM cartao.cartao_nome ORDER BY prefixo;")
    cartoes_cadastrados = cur.fetchall()

    cur.execute(f"SELECT DISTINCT categoria FROM {FINANCEIRO_TABELA} WHERE categoria IS NOT NULL;")
    categorias_db = {r["categoria"] for r in cur.fetchall()}
    todas_categorias = sorted((categorias_db | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB)) - CATEGORIAS_OCULTAS, key=lambda c: chave_alfa(cat_pt_puro(c)))

    cur.execute("SELECT DISTINCT numero_cartao_final FROM cartao.transacao WHERE numero_cartao_final IS NOT NULL;")
    finais_usados = sorted({r["numero_cartao_final"] for r in cur.fetchall()})

    contas_by_id, origem_opcoes = carregar_origens(cur)

    cfg = _montar_filtro_relatorio(dimensoes)
    cur.close()
    conn.close()

    cartao_opcoes = [(c["final4"], f'{c["prefixo"]} - final {c["final4"]}') for c in cartoes_cadastrados]
    registrados = {c["final4"] for c in cartoes_cadastrados}
    cartao_opcoes += [(f, f"final {f}") for f in finais_usados if f not in registrados]

    # cada filtro de chip ja vem como HTML pronto de chip_filter_html(); o template
    # so injeta na ordem, marcando |safe
    filtros_chip = [
        chip_filter_html("origem", "Origem", origem_opcoes, cfg["origens_sel"]),
        chip_filter_html("categoria", "Categoria",
                         [(c, cat_pt_puro(c)) for c in todas_categorias], cfg["categorias_sel"]),
        chip_filter_html("cartao", "Cartão", cartao_opcoes, cfg["cartoes_sel"]),
    ]
    filtros_chip += [
        chip_filter_html(f"dim_{d['id']}", d["nome"],
                         [(v["id"], rotulo_valor_dimensao(v)) for v in valores_por_dim.get(d["id"], [])],
                         cfg["dim_sel"].get(d["id"], []))
        for d in dimensoes if valores_por_dim.get(d["id"])
    ]

    agrupar_opcoes = [("categoria", "Categoria"), ("origem", "Origem"),
                      ("cartao", "Cartão"), ("mes", "Período (mês)"), ("ano", "Período (ano)")]
    agrupar_opcoes += [(f"dim_{d['id']}", d["nome"]) for d in dimensoes]

    return render_template(
        "relatorios.html",
        titulo="Relatórios",
        topbar=topbar_html("Relatórios", "relatorios"),
        visao=cfg["visao"],
        visao_opcoes=[
            ("despesa", "Despesas"),
            ("receita", "Receitas"),
            ("investimento", "Investimentos e bens"),
            ("tudo", "Tudo (fluxo de caixa)"),
        ],
        agrupar=cfg["agrupar"],
        agrupar_opcoes=agrupar_opcoes,
        filtros_chip=filtros_chip,
        data_ini=cfg["data_ini"],
        data_fim=cfg["data_fim"],
    )


@bp.route("/relatorios/dados")
@requer("relatorios")
def relatorios_dados():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id, nome, obrigatoria FROM cartao.dimensao ORDER BY ordem, nome;")
    dimensoes = cur.fetchall()
    cfg = _montar_filtro_relatorio(dimensoes)

    # agrupando por periodo o resultado e uma linha do tempo: ordena cronologicamente
    # (do mais antigo para o mais recente). Nos demais agrupamentos, maior valor primeiro.
    # periodo sai em ordem cronologica; o resto, do maior gasto para o menor
    ordem = f"{cfg['group_expr']} ASC" if cfg["agrupar"] in ("mes", "ano") else "total DESC"
    cur.execute(
        f"SELECT {cfg['group_expr']} AS grupo, COUNT(*) AS qtd, SUM({cfg['soma_expr']}) AS total "
        f"FROM {cfg['tabela']} t {cfg['join_natureza']} {cfg['join_extra']} "
        f"WHERE {cfg['where_sql']} GROUP BY {cfg['group_expr']} ORDER BY {ordem};",
        cfg["params"],
    )
    grupos_raw = cur.fetchall()

    cur.execute(
        f"SELECT COUNT(*) AS qtd, SUM({cfg['soma_expr']}) AS total "
        f"FROM {cfg['tabela']} t {cfg['join_natureza']} WHERE {cfg['where_sql']};",
        cfg["params"],
    )
    totalizador = cur.fetchone()
    total_geral = float(totalizador["total"] or 0)
    qtd_geral = totalizador["qtd"] or 0

    cur.execute("SELECT final4, prefixo FROM cartao.cartao_nome;")
    nomes_cartao = {r["final4"]: r["prefixo"] for r in cur.fetchall()}

    contas_by_id, _ = carregar_origens(cur)

    cur.close()
    conn.close()

    def selo_grupo(g):
        """Selo do banco quando agrupado por origem (o grafico usa so o nome puro)."""
        if cfg["agrupar"] != "origem":
            return ""
        c = contas_by_id.get(str(g))
        return c["selo"] if c else ""

    def nome_grupo(g):
        if cfg["agrupar"] == "categoria":
            return cat_pt_puro(g)
        if cfg["agrupar"] == "cartao":
            if not g:
                return "(sem cartao)"
            prefixo = nomes_cartao.get(g)
            return f"{prefixo} - final {g}" if prefixo else f"final {g}"
        if cfg["agrupar"] == "origem":
            c = contas_by_id.get(str(g))
            return c["label"] if c else "(sem origem)"
        if cfg["agrupar"] == "ano":
            return str(g) if g else "(sem periodo)"
        if cfg["agrupar"] == "mes":
            # '2026-01' -> 'jan/26', mais legivel na linha do tempo
            try:
                ano, mes = str(g).split("-")
                return f"{MESES_ABREV[int(mes) - 1]}/{ano[2:]}"
            except (ValueError, IndexError):
                return g or "(sem periodo)"
        return g if g else "(nao definido)"

    grupos = []
    for g in grupos_raw:
        total_g = float(g["total"] or 0)
        pct = (total_g / total_geral * 100) if total_geral else 0
        grupos.append({
            "valor": g["grupo"],
            "nome": nome_grupo(g["grupo"]),
            "selo": selo_grupo(g["grupo"]),
            "qtd": g["qtd"],
            "total": round(total_g, 2),
            "pct": round(pct, 1),
        })

    agrupar_labels = {"categoria": "Categoria", "origem": "Origem", "cartao": "Cartão", "mes": "Período (mês)"}
    for d in dimensoes:
        agrupar_labels[f"dim_{d['id']}"] = d["nome"]

    return jsonify({
        "total_geral": round(total_geral, 2),
        "qtd_geral": qtd_geral,
        "visao": cfg["visao"],
        "agrupar": cfg["agrupar"],
        "agrupar_label": agrupar_labels.get(cfg["agrupar"], cfg["agrupar"]),
        "grupos": grupos,
    })


@bp.route("/relatorios/lancamentos")
@requer("relatorios")
def relatorios_lancamentos():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id, nome, obrigatoria FROM cartao.dimensao ORDER BY ordem, nome;")
    dimensoes = cur.fetchall()
    cfg = _montar_filtro_relatorio(dimensoes)

    where_sql = cfg["where_sql"]
    params = list(cfg["params"])
    if request.args.get("valor_none") == "1":
        where_sql += f" AND {cfg['group_expr']} IS NULL"
    elif request.args.get("valor") is not None:
        where_sql += f" AND {cfg['group_expr']} = %s"
        params.append(request.args.get("valor"))

    cur.execute(
        f"SELECT t.data_transacao, t.descricao, t.categoria, {cfg['soma_expr']} AS valor, "
        f"t.numero_cartao_final, t.account_id FROM {cfg['tabela']} t {cfg['join_natureza']} {cfg['join_extra']} "
        f"WHERE {where_sql} ORDER BY t.data_transacao DESC LIMIT 300;",
        params,
    )
    rows = cur.fetchall()

    cur.execute("SELECT final4, prefixo FROM cartao.cartao_nome;")
    nomes_cartao = {r["final4"]: r["prefixo"] for r in cur.fetchall()}

    contas_by_id, _ = carregar_origens(cur)

    cur.close()
    conn.close()

    def nome_cartao_curto(final4):
        if not final4:
            return "-"
        prefixo = nomes_cartao.get(final4)
        return prefixo if prefixo else f"final {final4}"

    def origem_de(r):
        """(selo_html, texto_curto, texto_completo).

        O selo e HTML montado pelo app; o resto e texto puro e quem escapa e o
        relatorios.js, na hora de montar o innerHTML. Antes isso vinha tudo
        concatenado e so o apelido do cartao era escapado - a descricao e o
        label da conta iam crus para o innerHTML.
        """
        c = contas_by_id.get(str(r["account_id"]))
        if not c:
            return "", "-", "-"
        apelido = nomes_cartao.get(r["numero_cartao_final"])
        # se for cartao de credito e tiver apelido cadastrado, o apelido e mais informativo
        if c["tipo"] == "CREDIT" and r["numero_cartao_final"] and apelido:
            return c["selo"], apelido, f'{c["label"]} - {nome_cartao_curto(r["numero_cartao_final"])}'
        return c["selo"], c["label_curto"], c["label"]

    lancamentos = []
    for r in rows:
        selo, curto, completo = origem_de(r)
        lancamentos.append({
            "data": data_hora_local(r["data_transacao"]).strftime("%d/%m/%Y"),
            "descricao": r["descricao"],
            "origem_selo": selo,
            "origem": curto,
            "origem_completa": completo,
            "cartao": nome_cartao_curto(r["numero_cartao_final"]),
            "categoria": cat_pt_puro(r["categoria"]),
            "valor": float(r["valor"] or 0),
        })
    return jsonify({"lancamentos": lancamentos, "total": len(lancamentos)})


def _melhor_agregado(candidatos, valor_esperado_centavos, parcela_total, desc_norm):
    """Procura a transacao que representa o parcelamento INTEIRO (valor cheio,
    data da compra). O Pluggy nem sempre preenche parcela_total nesse
    lancamento agregado (esse cartao chega a mostrar "Parcela: A vista" num
    parcelamento real) - por isso o casamento e' pelo valor cheio esperado,
    com o numero de parcelas e a descricao servindo apenas de desempate.
    Tolerancia de R$1 porque o valor da parcela impresso na fatura e'
    arredondado por mes: multiplicado pelo numero de parcelas pode nao bater
    centavo a centavo com o valor cheio real (ex: fatura mostra R$198,05 x12 =
    R$2.376,60, o lancamento real e R$2.376,70).

    Nao filtra por _bloqueado de proposito: a mesma transacao agregada atende
    uma linha por mes, em faturas diferentes - e' o unico caso em que reusar
    transacao ja vinculada e' correto."""
    tolerancia_centavos = 100
    tokens_linha = _tokens_significativos(desc_norm)
    candidatos_valor = [
        c for c in candidatos
        if not c["_usado"]
        and abs(c["_valor_centavos"] - valor_esperado_centavos) <= tolerancia_centavos
        # Valor cheio igual nao basta: duas compras diferentes podem ter o
        # mesmo total (caso real: MERCADOLIVRE 5x32,80 e YELLOW BOX 164,00).
        # Exigimos ao menos um token de estabelecimento em comum.
        and bool(tokens_linha & _tokens_significativos(c.get("descricao")))
    ]
    if not candidatos_valor:
        return None
    if len(candidatos_valor) == 1:
        return candidatos_valor[0]
    com_parcela = [c for c in candidatos_valor if c["parcela_total"] == parcela_total]
    opcoes = com_parcela or candidatos_valor
    return min(opcoes, key=lambda c: (
        desc_norm.split()[0] not in (c["descricao"] or "").upper(),
        abs(c["_valor_centavos"] - valor_esperado_centavos),
    ))


def _conciliar_linhas(cur, account_id, linhas, fatura_linha_ids=None, todos_fatura_linha_ids=None,
                       ciclo_inicio_min=None, transacoes_bloqueadas=None, ciclo_fim_real=None):
    """Casa as linhas de uma fatura (do parser ou ja salvas no banco) contra os
    lancamentos que o Pluggy trouxe naquela conta. fatura_linha_ids, quando
    informado, e um dict {id(linha) python: id da cartao.fatura_linha} para o
    template poder oferecer "criar lancamento" apontando pro registro certo -
    exclui linha que ja virou lancamento. todos_fatura_linha_ids e o mesmo dict
    sem essa exclusao, usado para marcar "conferido" em cobranca repetida.
    ciclo_inicio_min, quando informado (dia seguinte ao fim do ciclo da fatura
    anterior desta mesma conta), evita que "sem_fatura" reapresente lancamento
    que ja pertence ao ciclo anterior so porque a janela padrao de 35 dias
    (usada quando nao ha fatura anterior salva) e' mais larga que o ciclo real."""
    fatura_linha_ids = fatura_linha_ids or {}
    todos_fatura_linha_ids = todos_fatura_linha_ids or fatura_linha_ids
    datas = [l["data"] for l in linhas]
    # O fim do ciclo tem que vir do periodo da FATURA, nao de max(datas): numa
    # fatura em que toda linha e' parcela, a data impressa e' a da compra
    # original e o "fim" cairia meses no passado, deixando de fora justamente
    # as cobrancas deste mes. max(datas) fica so como ultimo recurso.
    fim_busca = ciclo_fim_real or max(datas)
    if fim_busca < max(datas):
        fim_busca = max(datas)
    # A busca vai alguns dias alem do fim do ciclo porque o Pluggy as vezes
    # data a compra um ou dois dias depois do que a fatura imprime (caso real:
    # D MORI, fatura imprime 11/02 e o Pluggy grava 12/02). Sem essa folga a
    # transacao nem entrava como candidata e a compra ficava orfa dos dois
    # lados. So a avulsa aproveita: os caminhos de parcela filtram por
    # ciclo_inicio..ciclo_fim, que continuam sendo o ciclo real.
    fim_busca_sql = fim_busca + timedelta(days=3)
    cur.execute(
        f"SELECT t.transacao_id, t.data_transacao, t.descricao, t.parcela_total, "
        f"COALESCE(t.valor_brl, t.valor_original) AS valor, "
        f"({DATA_LOCAL_SQL})::date AS data_local "
        # Cobranca ja marcada duplicada pelo usuario fica fora dos totais de
        # proposito (ver regra de duplicidade) - nao faz sentido a conciliacao
        # cobrar dela um par na fatura, ela ja foi resolvida.
        f"FROM cartao.transacao t WHERE t.account_id = %s AND COALESCE(t.duplicada, false) = false "
        # Lancamento que NASCEU da fatura (parcela gerada, ou criado a mao a
        # partir de uma linha) nao e' candidato: ele ja E' a fatura. Deixar
        # entrar fazia a parcela gerada disputar a linha com o agregado do
        # Pluggy e virar orfa.
        f"AND NOT EXISTS (SELECT 1 FROM cartao.fatura_linha fl "
        f"WHERE fl.transacao_id_criado = t.transacao_id) "
        f"AND ({DATA_LOCAL_SQL})::date BETWEEN %s AND %s;",
        (account_id, min(datas), fim_busca_sql),
    )
    candidatos = [dict(r) for r in cur.fetchall()]
    # Transacao ja vinculada a alguma linha de fatura (ver cartao.fatura_vinculo)
    # nao pode ser reivindicada de novo pelo casamento 1:1 nem por avulsa - foi
    # isso que fazia um mes "roubar" a cobranca do outro. A excecao e' o
    # fallback de parcelamento agregado, onde a MESMA transacao (valor cheio)
    # legitimamente atende uma linha por mes, em faturas diferentes.
    transacoes_bloqueadas = transacoes_bloqueadas or set()
    for c in candidatos:
        c["_usado"] = False
        c["_data_local"] = c["data_local"]
        c["_valor_centavos"] = _centavos(c["valor"])
        c["_bloqueado"] = str(c["transacao_id"]) in transacoes_bloqueadas

    # Ciclo real desta fatura - calculado aqui (nao so mais embaixo, perto de
    # "sem_fatura") porque o fallback de parcela por mes tambem precisa dele:
    # a data impressa numa linha de parcela e' a da COMPRA ORIGINAL, nao da
    # cobranca atual, entao filtrar candidato por proximidade dessa data (como
    # se faz com avulsa) nunca acha nada - o filtro certo e' "aconteceu dentro
    # do ciclo desta fatura", nao "perto da data impressa".
    ciclo_fim = fim_busca
    ciclo_inicio = ciclo_fim - timedelta(days=35)
    if ciclo_inicio_min and ciclo_inicio_min > ciclo_inicio:
        ciclo_inicio = ciclo_inicio_min

    # A fatura lista cada parcela mensal numa linha (ex: "Parc.9/12 R$129,00");
    # o Pluggy grava o parcelamento inteiro como UMA transacao so, no valor
    # cheio, na data da compra original. Sem agrupar por parcela_total, toda
    # compra parcelada bateria errado dos dois lados - nao por falha de
    # sincronizacao, so porque sao representacoes diferentes da mesma compra.
    grupos_parcela = {}
    avulsas = []
    for linha in linhas:
        linha["_valor_centavos"] = _centavos(linha["valor"])
        # A camada visual continua recebendo numero comum; apenas o calculo
        # interno usa centavos inteiros.
        linha["valor"] = _reais(linha["_valor_centavos"])
        if linha["parcela_total"]:
            # O VALOR entra na chave: o mesmo lojista pode ter dois
            # parcelamentos com o mesmo numero de parcelas e valores
            # diferentes (caso real: MECANICA HOCHIOVE com 2x R$135,00 e
            # 2x R$233,50 na mesma fatura). Sem o valor os dois colapsam numa
            # chave so, o valor da parcela vira a media e nenhum dos dois
            # agregados e' encontrado - os dois viravam orfaos.
            chave = (linha["titular"], linha["descricao_base"].upper(),
                     linha["parcela_total"], linha["_valor_centavos"])
            grupos_parcela.setdefault(chave, []).append(linha)
        else:
            avulsas.append(linha)

    batidos, sem_sistema = [], []

    for linha in avulsas:
        melhor, melhor_dist = None, None
        for c in candidatos:
            if (c["_usado"] or c["_bloqueado"]
                    or c["_valor_centavos"] != linha["_valor_centavos"]):
                continue
            dist = abs((c["_data_local"] - linha["data"]).days)
            if dist > 3:
                continue
            if melhor is None or dist < melhor_dist:
                melhor, melhor_dist = c, dist
        if melhor:
            melhor["_usado"] = True
            batidos.append({**linha, "transacao_id": str(melhor["transacao_id"]),
                             "descricao_sistema": melhor["descricao"],
                             "fatura_linha_id": fatura_linha_ids.get(id(linha))})
        else:
            sem_sistema.append({**linha, "fatura_linha_id": fatura_linha_ids.get(id(linha))})

    for (titular, desc_norm, parcela_total, valor_mensal_centavos), linhas_grupo in grupos_parcela.items():
        # O valor faz parte da chave do grupo; logo todas as linhas têm os
        # mesmos centavos e não há média em ponto flutuante a calcular.
        valor_esperado_centavos = valor_mensal_centavos * parcela_total
        valor_esperado = _reais(valor_esperado_centavos)

        # PRIORIDADE 1: parcelamento agregado, quando existe.
        # O Pluggy as vezes grava o parcelamento inteiro como UMA transacao, no
        # valor cheio, na data da compra. Quando essa transacao existe, ela ja
        # representa TODAS as parcelas - entao toda linha do grupo tem que
        # apontar pra ela, em qualquer mes. Se em vez disso a parcela casasse
        # com uma cobranca mensal de mesmo valor (o que acontecia antes), a
        # escolha ficava arbitraria e, pior, escondia a mensal atras de um
        # vinculo: ela e' cobranca A MAIS (o agregado ja cobre tudo) e precisa
        # sobrar como orfa pra aparecer como candidata a duplicidade.
        # Caso real: OTICA CALLIARI 10x R$316, agregado de R$3.160 em
        # 02/11/2025, mais mensais de R$316 em 12/06, 12/07 e 12/08 de 2026.
        # So vale com 2+ parcelas: com parcela_total=1 o "valor cheio" e' igual
        # ao da parcela e qualquer cobranca normal pareceria um agregado.
        agregado = _melhor_agregado(candidatos, valor_esperado_centavos, parcela_total, desc_norm) \
            if parcela_total >= 2 else None
        if agregado:
            agregado["_usado"] = True
            for l in linhas_grupo:
                batidos.append({**l, "transacao_id": str(agregado["transacao_id"]),
                                "descricao_sistema": agregado["descricao"],
                                "valor_esperado_parcelamento": valor_esperado,
                                "fatura_linha_id": fatura_linha_ids.get(id(l))})
            continue

        # PRIORIDADE 2: uma transacao por parcela, no valor da parcela.
        # A fatura lista so a(s) parcela(s) COBRADA(S) neste mes (ex: agosto
        # traz "AQUAMATER Parc.9/12", julho traz "Parc.8/12") e o Pluggy manda
        # uma transacao por mes, no mesmo valor - entao o casamento natural e'
        # 1:1. Filtra por estar dentro do ciclo desta fatura, nao por
        # proximidade da data impressa: essa data e' a da COMPRA ORIGINAL
        # (pode ser de um ano atras), nao a da cobranca deste mes.
        pendentes = []
        for l in linhas_grupo:
            melhor_linha = None
            melhor_chave = None
            tokens_l = _tokens_significativos(l.get("descricao_base") or l["descricao"])
            for c in candidatos:
                if (c["_usado"] or c["_bloqueado"]
                        or c["_valor_centavos"] != l["_valor_centavos"]):
                    continue
                if not (ciclo_inicio <= c["_data_local"] <= ciclo_fim):
                    continue
                # Havendo varios candidatos de MESMO valor no ciclo, o
                # estabelecimento desempata primeiro. Sem isso a escolha era "o
                # mais recente, sem motivo melhor" e duas compras de mesmo valor
                # trocavam de dono: XIMANGO ficou com a transacao da ALLPARK e
                # vice-versa, ambas R$ 25,00. Nao e' exigencia, e preferencia -
                # par legitimo sem palavra em comum existe ("Pagamento Recebido"
                # x "Pag de Fatura Via Deb Aut", secao 6.5 n.12) e continua
                # casando quando nao ha candidato melhor.
                casa_lojista = bool(tokens_l and (tokens_l & _tokens_significativos(c["descricao"])))
                chave = (casa_lojista, c["_data_local"])
                if melhor_chave is None or chave > melhor_chave:
                    melhor_linha, melhor_chave = c, chave
            if melhor_linha:
                melhor_linha["_usado"] = True
                batidos.append({**l, "transacao_id": str(melhor_linha["transacao_id"]),
                                 "descricao_sistema": melhor_linha["descricao"],
                                 "fatura_linha_id": fatura_linha_ids.get(id(l))})
            else:
                pendentes.append(l)

        if not pendentes:
            continue

        # FALLBACK: parcelamento que o Pluggy gravou como UMA transacao so, no
        # valor cheio, na data da compra original (acontece com parte das
        # compras parceladas). O Pluggy nem sempre preenche parcela_total nesse
        # lancamento agregado (esse cartao chega a mostrar "Parcela: A vista"
        # num parcelamento real) - por isso o casamento e' pelo valor cheio
        # esperado, com o numero de parcelas e a descricao servindo apenas de
        # desempate. Tolerancia de R$1 porque o valor da parcela impresso na
        # fatura e' arredondado por mes: multiplicado pelo numero de parcelas
        # pode nao bater centavo a centavo com o valor cheio real (ex: fatura
        # mostra R$198,05 x12=R$2.376,60, o lancamento real e R$2.376,70).
        melhor = _melhor_agregado(
            candidatos, valor_esperado_centavos, parcela_total, desc_norm
        )
        if melhor:
            melhor["_usado"] = True
            for l in pendentes:
                batidos.append({**l, "transacao_id": str(melhor["transacao_id"]),
                                "descricao_sistema": melhor["descricao"],
                                "valor_esperado_parcelamento": valor_esperado,
                                "fatura_linha_id": fatura_linha_ids.get(id(l))})
        else:
            for l in pendentes:
                sem_sistema.append({**l, "valor_esperado_parcelamento": valor_esperado,
                                     "titular": titular, "fatura_linha_id": fatura_linha_ids.get(id(l))})

    # ciclo_inicio/ciclo_fim ja calculados mais acima (o fallback de parcela
    # tambem usa). "sem_fatura" so faz sentido dentro desse ciclo - o intervalo
    # de busca dos candidatos e' largo (cobre ate um ano, por causa de parcela
    # antiga que ainda aparece cobrada), mas listar TODA sobra desse intervalo
    # encheria a tela de lancamentos de faturas passadas que nunca deveriam
    # bater com esta mesmo.
    sem_fatura = [
        {"data": c["_data_local"], "descricao": c["descricao"], "valor": _reais(c["_valor_centavos"]),
         "transacao_id": str(c["transacao_id"])}
        for c in candidatos if not c["_usado"] and c["_data_local"] >= ciclo_inicio
    ]

    # "Pagamento Recebido" e' a Unicred informando, por transparencia, que a
    # fatura ANTERIOR foi paga - nao e' uma cobranca deste ciclo. O proprio
    # SALDO TOTAL da fatura (extraido na pagina 2) nao inclui essa linha, entao
    # a soma daqui tambem nao pode incluir, senao "nao fecha" por R$dezenas de
    # milhares mesmo com tudo batido (caso real: fatura de 08/2026, SALDO TOTAL
    # R$18.821,76 batendo 100% so depois de tirar -R$16.543,97 dessa linha).
    # A linha continua em `linhas` para casar com o lancamento correspondente
    # no Pluggy (a propria fatura de cartao sendo paga) e nao sobrar como
    # "sem_fatura" - so sai da soma usada na comparacao.
    def _nao_e_pagamento_recebido(l):
        return l["descricao"].strip().lower() != "pagamento recebido"

    soma_fatura_centavos = sum(
        l["_valor_centavos"] for l in linhas if _nao_e_pagamento_recebido(l)
    )
    soma_batida_centavos = sum(
        l["_valor_centavos"] for l in batidos if _nao_e_pagamento_recebido(l)
    )
    soma_fatura = _reais(soma_fatura_centavos)

    def _linha_publica(linha):
        return {chave: valor for chave, valor in linha.items() if not chave.startswith("_")}

    return {
        "soma_fatura": soma_fatura,
        "batidos": sorted((_linha_publica(l) for l in batidos), key=lambda l: l["data"]),
        "sem_sistema": sorted(
            (_linha_publica(l) for l in sem_sistema), key=lambda l: l["data"]
        ),
        "sem_fatura": sorted(sem_fatura, key=lambda l: l["data"]),
        "fecha_100": not sem_sistema and not sem_fatura,
        "diferenca": _reais(soma_fatura_centavos - soma_batida_centavos),
        # min(datas) fica errado quando ha parcela antiga na fatura: a data
        # impressa dela e' a da COMPRA ORIGINAL (pode ser quase um ano atras),
        # nao deste ciclo - ja vimos isso mostrar "periodo" de 10 meses. Usar
        # o mesmo ciclo_inicio (~35 dias antes do fim) da checagem de
        # "sem_fatura" da o periodo real desta fatura.
        "periodo_inicio": ciclo_inicio,
        "periodo_fim": ciclo_fim,
        "repetidas_na_fatura": _repetidas_na_fatura(linhas, todos_fatura_linha_ids),
    }


def _repetidas_na_fatura(linhas, fatura_linha_ids=None):
    """Cobrancas que a propria operadora lancou mais de uma vez no mesmo dia,
    com mesmo titular, descricao e valor - caso real ja visto: um acougue
    cobrado 2x em 08/08 e depois estornado pela propria operadora.

    Isto e diferente da duplicidade do Pluggy (mesmo gasto chegando com dois
    ids): aqui a fatura oficial mostra a cobranca repetida, entao o dinheiro
    saiu duas vezes de verdade. Casamos com o estorno (linha negativa de mesmo
    valor e titular) para separar "ja resolvido" de "contestar com a operadora".

    Repeticao no mesmo dia nao prova erro - pedagio, por exemplo, aparece
    legitimamente varias vezes - por isso a tela chama de "revisar", nao de
    duplicidade confirmada.

    fatura_linha_ids mapeia id(linha python) -> id de cartao.fatura_linha,
    para o "conferido" poder ser marcado no banco (grupo inteiro fica com a
    mesma marcacao, guardando quem conferiu e quando).
    """
    fatura_linha_ids = fatura_linha_ids or {}
    grupos = {}
    for l in linhas:
        valor_centavos = _centavos(l["valor"])
        if valor_centavos <= 0 or l["parcela_total"]:
            continue  # estorno/pagamento e parcelamento nao entram
        chave = (l["titular"], (l["descricao_base"] or l["descricao"]).upper(),
                 valor_centavos, l["data"])
        grupos.setdefault(chave, []).append(l)

    estornos = [l for l in linhas if _centavos(l["valor"]) < 0]
    usados = set()
    repetidas = []
    for (titular, desc, valor_centavos, data), grupo in grupos.items():
        if len(grupo) < 2:
            continue
        # procura um estorno de mesmo valor/titular que ainda nao foi atribuido
        estorno = None
        for e in estornos:
            if id(e) in usados or e["titular"] != titular:
                continue
            if abs(abs(_centavos(e["valor"])) - valor_centavos) > 1 or e["data"] < data:
                continue
            estorno = e
            usados.add(id(e))
            break
        linha_ids = [fatura_linha_ids[id(l)] for l in grupo if id(l) in fatura_linha_ids]
        repetidas.append({
            "titular": titular, "descricao": grupo[0]["descricao"],
            "valor": _reais(valor_centavos),
            "data": data, "vezes": len(grupo),
            "total_cobrado": _reais(valor_centavos * len(grupo)),
            "estorno_data": estorno["data"] if estorno else None,
            "estorno_valor": _reais(_centavos(estorno["valor"])) if estorno else None,
            "linha_ids": linha_ids,
            "conferida": bool(linha_ids) and all(l.get("conferida_repeticao") for l in grupo),
            "conferida_por": grupo[0].get("conferida_repeticao_por"),
            "conferida_em": grupo[0].get("conferida_repeticao_em"),
        })
    return sorted(repetidas, key=lambda r: r["data"])


def _linhas_da_fatura(cur, fatura_id):
    """Linhas da fatura no formato que o matcher espera, com o id do banco."""
    cur.execute(
        "SELECT id, data, descricao, descricao_base, parcela_atual, parcela_total, valor, titular, "
        "transacao_id_criado, conferida_repeticao, conferida_repeticao_por, conferida_repeticao_em "
        "FROM cartao.fatura_linha WHERE fatura_id=%s ORDER BY data, id;",
        (fatura_id,),
    )
    linhas_db = [dict(r) for r in cur.fetchall()]
    for l in linhas_db:
        l["valor"] = _reais(_centavos(l["valor"]))
    return linhas_db


def _transacoes_vinculadas(cur, ignorar_fatura_id=None):
    """transacao_id que ja tem vinculo com alguma linha de fatura. Ignorando
    opcionalmente uma fatura (a que esta sendo reconciliada agora, cujos
    proprios vinculos nao devem bloquear o recalculo dela mesma)."""
    if ignorar_fatura_id is None:
        cur.execute("SELECT DISTINCT transacao_id FROM cartao.fatura_vinculo;")
    else:
        cur.execute(
            "SELECT DISTINCT v.transacao_id FROM cartao.fatura_vinculo v "
            "JOIN cartao.fatura_linha fl ON fl.id = v.fatura_linha_id "
            "WHERE fl.fatura_id <> %s;",
            (ignorar_fatura_id,),
        )
    return {str(r["transacao_id"]) for r in cur.fetchall()}


def _ciclo_inicio_encadeado(cur, fatura_row):
    """Inicio real do ciclo = fim da fatura do mes anterior + 1 dia, calculado
    na LEITURA e nao congelado no import.

    Congelar no import tornava o resultado dependente da ORDEM em que os PDFs
    foram enviados: quem mandasse da fatura mais nova para a mais antiga ficava
    com todas no palpite generico de 35 dias, porque a anterior ainda nao
    existia no banco na hora. Aconteceu de verdade com as faturas de 2025.
    Calculando aqui, a ordem de envio deixa de importar."""
    cur.execute(
        "SELECT periodo_fim FROM cartao.fatura_importada "
        "WHERE account_id = %s AND (ano_referencia, mes_referencia) < (%s, %s) "
        "AND periodo_fim IS NOT NULL "
        "ORDER BY ano_referencia DESC, mes_referencia DESC LIMIT 1;",
        (fatura_row["account_id"], fatura_row["ano_referencia"], fatura_row["mes_referencia"]),
    )
    anterior = cur.fetchone()
    if anterior and anterior["periodo_fim"]:
        return anterior["periodo_fim"] + timedelta(days=1)
    return fatura_row["periodo_inicio"]


def _estado_fatura(cur, fatura_row):
    """Retrato da fatura a partir dos VINCULOS gravados - nao por heuristica.
    A fatura e' a autoridade: cada linha dela aparece com os lancamentos que
    o usuario (ou o vinculo automatico) associou. Fica orfao o que o Pluggy
    trouxe dentro do ciclo e nao tem vinculo com NENHUMA linha de NENHUMA
    fatura - antes a checagem era so contra a fatura aberta, e por isso a
    mesma cobranca aparecia como sobra em dois meses seguidos."""
    fatura_id = fatura_row["id"]
    account_id = str(fatura_row["account_id"])
    linhas = _linhas_da_fatura(cur, fatura_id)

    cur.execute(
        f"SELECT v.fatura_linha_id, v.transacao_id, v.origem, v.criado_por, v.criado_em, "
        f"t.descricao, COALESCE(t.valor_brl, t.valor_original) AS valor, "
        f"({DATA_LOCAL_SQL})::date AS data_local "
        f"FROM cartao.fatura_vinculo v "
        f"JOIN cartao.fatura_linha fl ON fl.id = v.fatura_linha_id "
        f"JOIN cartao.transacao t ON t.transacao_id = v.transacao_id "
        f"WHERE fl.fatura_id = %s ORDER BY v.criado_em;",
        (fatura_id,),
    )
    vinculos_por_linha = {}
    for r in cur.fetchall():
        vinculos_por_linha.setdefault(r["fatura_linha_id"], []).append({
            "transacao_id": str(r["transacao_id"]),
            "descricao": r["descricao"],
            "valor": _reais(_centavos(r["valor"])),
            "data": r["data_local"],
            "origem": r["origem"],
            "criado_por": r["criado_por"],
            "criado_em": data_hora_local(r["criado_em"]),
        })

    for l in linhas:
        l["vinculos"] = vinculos_por_linha.get(l["id"], [])
        l["tem_vinculo"] = bool(l["vinculos"]) or bool(l["transacao_id_criado"])

    # orfas: no ciclo desta fatura e sem vinculo com nenhuma fatura
    datas_linhas = [l["data"] for l in linhas]
    periodo_inicio = _ciclo_inicio_encadeado(cur, fatura_row) or (min(datas_linhas) if datas_linhas else None)
    periodo_fim = fatura_row["periodo_fim"] or (max(datas_linhas) if datas_linhas else None)
    if not periodo_inicio or not periodo_fim:
        # fatura sem linha nenhuma (nao deveria acontecer: o import recusa PDF
        # sem lancamento) - sem ciclo nao da pra dizer o que e' orfao
        return {
            "linhas": linhas, "sem_vinculo": linhas, "orfas": [],
            "soma_fatura": 0.0, "soma_vinculada": 0.0, "diferenca": 0.0,
            "despesas_dre": 0.0, "fora_dre": 0.0,
            "fecha_100": False, "periodo_inicio": periodo_inicio, "periodo_fim": periodo_fim,
            "repetidas_na_fatura": [],
        }
    cur.execute(
        f"SELECT t.transacao_id, t.descricao, COALESCE(t.valor_brl, t.valor_original) AS valor, "
        f"({DATA_LOCAL_SQL})::date AS data_local "
        f"FROM cartao.transacao t "
        f"WHERE t.account_id = %s AND COALESCE(t.duplicada, false) = false "
        # Lancamento ja resolvido nao e' orfao: `substituido_por` diz que ele e'
        # o mesmo evento que outro (que esta vinculado a fatura), e
        # `somente_conciliacao` e' registro de compra, nao cobranca. Sem esses
        # dois filtros a tela repetia como "sem vinculo" tudo o que a tela de
        # duplicidades ja tinha resolvido.
        f"AND t.substituido_por IS NULL "
        f"AND COALESCE(t.somente_conciliacao, false) = false "
        f"AND ({DATA_LOCAL_SQL})::date BETWEEN %s AND %s "
        f"AND NOT EXISTS (SELECT 1 FROM cartao.fatura_vinculo v WHERE v.transacao_id = t.transacao_id) "
        f"ORDER BY 4, 2;",
        (account_id, periodo_inicio, periodo_fim),
    )
    orfas = [{
        "transacao_id": str(r["transacao_id"]), "descricao": r["descricao"],
        "valor": _reais(_centavos(r["valor"])), "data": r["data_local"],
    } for r in cur.fetchall()]

    def _nao_e_pagamento_recebido(l):
        return not _normalizar_desc(l["descricao"]).startswith(("PAGAMENTO RECEBIDO", "PAG DE FATURA"))

    consideradas = [l for l in linhas if _nao_e_pagamento_recebido(l)]
    soma_fatura_centavos = sum(_centavos(l["valor"]) for l in consideradas)
    soma_vinculada_centavos = sum(
        _centavos(l["valor"]) for l in consideradas if l["tem_vinculo"]
    )
    soma_fatura = _reais(soma_fatura_centavos)
    soma_vinculada = _reais(soma_vinculada_centavos)
    # A fatura prova o valor cobrado; o DRE responde quanto disso e' despesa.
    # A classificacao vem dos lancamentos vinculados, mas o valor vem de cada
    # linha do PDF (ver core.calcular_totais_dre_fatura).
    totais_dre = calcular_totais_dre_fatura(cur, fatura_id)
    despesas_dre_centavos = _centavos(totais_dre["despesas_dre"])
    # "Pagamento Recebido" e' a fatura ANTERIOR sendo quitada: nao e' cobranca
    # deste ciclo, ja fica fora das duas somas e nunca vira lancamento. Cobrar
    # vinculo dela travaria o "fecha 100%" para sempre nos meses em que o
    # Pluggy nao tem o pagamento (todo o inicio de 2025).
    sem_vinculo = [l for l in linhas if not l["tem_vinculo"] and _nao_e_pagamento_recebido(l)]

    return {
        "linhas": linhas,
        "sem_vinculo": sem_vinculo,
        "orfas": orfas,
        "soma_fatura": soma_fatura,
        "soma_vinculada": soma_vinculada,
        "diferenca": _reais(soma_fatura_centavos - soma_vinculada_centavos),
        "despesas_dre": _reais(despesas_dre_centavos),
        "fora_dre": _reais(soma_fatura_centavos - despesas_dre_centavos),
        "fecha_100": not sem_vinculo and not orfas,
        "periodo_inicio": periodo_inicio,
        "periodo_fim": periodo_fim,
        "repetidas_na_fatura": _repetidas_na_fatura(linhas, {id(l): l["id"] for l in linhas}),
    }


def _vincular_automatico(cur, fatura_row, usuario):
    """Cria vinculo para as linhas que ainda nao tem, usando as heuristicas de
    _conciliar_linhas apenas como SUGESTAO. Nunca toca em vinculo que ja
    existe (inclusive manual) e nunca reivindica transacao ja vinculada a
    outra fatura, salvo no caso do parcelamento agregado. Devolve quantos
    vinculos criou."""
    fatura_id = fatura_row["id"]
    linhas_db = _linhas_da_fatura(cur, fatura_id)
    if not linhas_db:
        return 0

    cur.execute(
        # origem='fatura' e' o vinculo com a parcela que a propria fatura
        # gerou - ele NAO impede a linha de tambem apontar para o agregado do
        # Pluggy, que e' o outro lado da conciliacao. Sem essa ressalva a linha
        # ficava "ja vinculada" e o agregado nunca era reencontrado.
        "SELECT DISTINCT v.fatura_linha_id FROM cartao.fatura_vinculo v "
        "JOIN cartao.fatura_linha fl ON fl.id = v.fatura_linha_id "
        "WHERE fl.fatura_id = %s AND v.origem <> 'fatura';",
        (fatura_id,),
    )
    ja_vinculadas = {r["fatura_linha_id"] for r in cur.fetchall()}
    pendentes = [l for l in linhas_db if l["id"] not in ja_vinculadas]
    if not pendentes:
        return 0

    linhas = [{k: v for k, v in l.items() if k != "id"} for l in pendentes]
    ids_por_linha = {id(l): db["id"] for l, db in zip(linhas, pendentes)}
    bloqueadas = _transacoes_vinculadas(cur, ignorar_fatura_id=fatura_id)

    resultado = _conciliar_linhas(
        cur, str(fatura_row["account_id"]), linhas, ids_por_linha, ids_por_linha,
        _ciclo_inicio_encadeado(cur, fatura_row), transacoes_bloqueadas=bloqueadas,
        ciclo_fim_real=fatura_row["periodo_fim"],
    )

    criados = 0
    for b in resultado["batidos"]:
        linha_id = b.get("fatura_linha_id")
        if not linha_id:
            continue
        cur.execute(
            "INSERT INTO cartao.fatura_vinculo (fatura_linha_id, transacao_id, origem, criado_por) "
            "VALUES (%s, %s, 'automatico', %s) ON CONFLICT (fatura_linha_id, transacao_id) DO NOTHING;",
            (linha_id, b["transacao_id"], usuario),
        )
        criados += cur.rowcount or 0
    return criados




@bp.route("/relatorios/conciliar-fatura", methods=["GET", "POST"])
@requer("relatorios")
def conciliar_fatura():
    """Confere se os lancamentos que o Pluggy trouxe para um cartao de credito
    batem 100% com a fatura em PDF da Unicred. O Pluggy pode classificar
    diferente ou duplicar - mas o valor que sai da conta pagando a fatura tem
    que fechar com o que a operadora cobrou, e isso so a fatura oficial prova."""
    if request.method == "POST" and not pode("conciliacao_editar"):
        return render_template(
            "sem_permissao.html",
            titulo="Sem permissão",
            topbar=topbar_html("Sem permissão"),
            permissao="Editar conciliação",
        ), 403

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    contas_by_id, origem_opcoes = carregar_origens(cur)
    contas_credito = [o for o in origem_opcoes if contas_by_id[o[0]]["tipo"] == "CREDIT"]
    cur.execute(f"SELECT DISTINCT categoria FROM {FINANCEIRO_TABELA} WHERE categoria IS NOT NULL;")
    categorias_db = {r["categoria"] for r in cur.fetchall()}
    categorias = sorted(
        (categorias_db | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB)) - CATEGORIAS_OCULTAS,
        key=lambda c: chave_alfa(cat_pt_puro(c)),
    )
    categorias_template = [{"chave": c, "nome": cat_pt_puro(c)} for c in categorias]

    erro = None
    resultado = None
    fatura_meta = None
    account_id = request.form.get("account_id") if request.method == "POST" else None
    fatura_id = request.args.get("fatura_id", type=int)

    if request.method == "POST":
        arquivo = request.files.get("fatura")
        if not arquivo or not arquivo.filename:
            erro = "Selecione o PDF da fatura."
        elif not account_id or account_id not in contas_by_id:
            erro = "Selecione a qual cartão essa fatura pertence."
        else:
            pdf_bytes = arquivo.read()
            try:
                fatura = extrair_fatura(io.BytesIO(pdf_bytes))
            except FaturaInvalida as exc:
                erro = str(exc)
            except Exception as exc:
                erro = f"Não consegui ler esse PDF: {exc}"

            if not erro:
                # As datas do ciclo vem inteiramente das proprias faturas
                # importadas, nunca de cadastro manual em /contas. periodo_fim
                # ja e' confiavel (e' a data do ultimo lancamento REAL desta
                # fatura, extraida do PDF); periodo_inicio usa o fim da fatura
                # do MES ANTERIOR desta mesma conta, quando ela ja foi
                # importada - so cai no palpite fixo de 35 dias (calculado no
                # parser) quando e' a primeira fatura desta conta no app, sem
                # nada pra comparar. (cartao.conta.fechamento_fatura foi
                # tentado antes e removido: o Pluggy nunca preenche essa
                # coluna pra nenhuma conta real, so vencimento_fatura.)
                periodo_fim = fatura["periodo_fim"]
                cur.execute(
                    "SELECT periodo_fim FROM cartao.fatura_importada "
                    "WHERE account_id=%s AND (ano_referencia, mes_referencia) < (%s, %s) "
                    "AND periodo_fim IS NOT NULL "
                    "ORDER BY ano_referencia DESC, mes_referencia DESC LIMIT 1;",
                    (account_id, fatura["ano_referencia"], fatura["mes_referencia"]),
                )
                fatura_anterior = cur.fetchone()
                periodo_inicio = (
                    fatura_anterior["periodo_fim"] + timedelta(days=1)
                    if fatura_anterior else fatura["periodo_inicio"]
                )

                # Guarda as linhas extraidas E o PDF original (pdf_arquivo) -
                # o app roda em container sem volume persistente confirmado,
                # entao o arquivo fica como bytea no Postgres (500KB-1MB,
                # tranquilo) em vez de disco, pra sobreviver a deploy e poder
                # ser baixado de novo em /configuracoes/faturas-pdf.
                # Reenviar a mesma fatura (conta+mes+ano) substitui tudo.
                # As datas do ciclo (inicio/fim/vencimento) sao so leitura -
                # vem inteiras do PDF/Pluggy, ninguem edita na tela.
                cur.execute(
                    "INSERT INTO cartao.fatura_importada "
                    "(account_id, mes_referencia, ano_referencia, total, cartao_final4, arquivo_nome, importado_por, "
                    "periodo_inicio, periodo_fim, vencimento, pdf_arquivo) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (account_id, mes_referencia, ano_referencia) DO UPDATE SET "
                    "total=EXCLUDED.total, cartao_final4=EXCLUDED.cartao_final4, "
                    "arquivo_nome=EXCLUDED.arquivo_nome, importado_por=EXCLUDED.importado_por, importado_em=now(), "
                    "periodo_inicio=EXCLUDED.periodo_inicio, periodo_fim=EXCLUDED.periodo_fim, "
                    "vencimento=EXCLUDED.vencimento, pdf_arquivo=EXCLUDED.pdf_arquivo "
                    "RETURNING id;",
                    (account_id, fatura["mes_referencia"], fatura["ano_referencia"], fatura["total"],
                     fatura["cartao_final4"], arquivo.filename, session.get("user"),
                     periodo_inicio, periodo_fim, fatura["vencimento"],
                     psycopg2.Binary(pdf_bytes)),
                )
                fatura_id = cur.fetchone()["id"]
                # Reenviar o mesmo PDF (ex: corrigir algo, ou so pra recalcular
                # periodo_inicio depois que a fatura anterior passou a existir)
                # apagava tudo e recriava do zero - perdendo silenciosamente o
                # "conferido" de cobranca repetida e o lancamento manual ja
                # criado a partir de uma linha "so na fatura". Guarda esse
                # estado antes de apagar, casando por chave natural (a fatura
                # nao tem id estavel entre envios), e devolve pra linha nova
                # que bater exatamente igual.
                cur.execute(
                    "SELECT data, descricao, valor, titular, transacao_id_criado, "
                    "conferida_repeticao, conferida_repeticao_por, conferida_repeticao_em, "
                    "conferida_fatura, conferida_fatura_por, conferida_fatura_em "
                    "FROM cartao.fatura_linha WHERE fatura_id=%s;",
                    (fatura_id,),
                )
                estado_anterior = {
                    (r["data"], r["descricao"], _centavos(r["valor"]), r["titular"]): r
                    for r in cur.fetchall()
                }
                cur.execute("DELETE FROM cartao.fatura_linha WHERE fatura_id=%s;", (fatura_id,))
                for l in fatura["linhas"]:
                    anterior = estado_anterior.get(
                        (l["data"], l["descricao"], _centavos(l["valor"]), l["titular"])
                    )
                    cur.execute(
                        "INSERT INTO cartao.fatura_linha "
                        "(fatura_id, data, descricao, descricao_base, parcela_atual, parcela_total, valor, titular, "
                        "transacao_id_criado, conferida_repeticao, conferida_repeticao_por, conferida_repeticao_em, "
                        "conferida_fatura, conferida_fatura_por, conferida_fatura_em) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);",
                        (fatura_id, l["data"], l["descricao"], l["descricao_base"],
                         l["parcela_atual"], l["parcela_total"], l["valor"], l["titular"],
                         anterior["transacao_id_criado"] if anterior else None,
                         anterior["conferida_repeticao"] if anterior else False,
                         anterior["conferida_repeticao_por"] if anterior else None,
                         anterior["conferida_repeticao_em"] if anterior else None,
                         anterior["conferida_fatura"] if anterior else False,
                         anterior["conferida_fatura_por"] if anterior else None,
                         anterior["conferida_fatura_em"] if anterior else None),
                    )
                # Casamento automatico roda aqui (POST), nao na abertura da
                # tela: GET nao pode gravar - e assim o resultado para de mudar
                # sozinho entre uma visita e outra.
                cur.execute(
                    "SELECT id, account_id, ano_referencia, mes_referencia, periodo_inicio, periodo_fim "
                    "FROM cartao.fatura_importada WHERE id = %s;",
                    (fatura_id,),
                )
                fatura_para_vincular = cur.fetchone()
                # reenviar o PDF recria as linhas com ids novos e o CASCADE
                # leva os vinculos junto - inclusive o da parcela ja gerada
                _revincular_lancamentos_da_fatura(cur, session.get("user"))
                _vincular_automatico(cur, fatura_para_vincular, session.get("user"))
                # A propria importacao conclui o regime de caixa. Assim uma
                # compra total do Pluggy nunca espera outra acao manual para
                # virar parcelas mensais oficiais.
                resumo_parcelas = _sincronizar_parcelas_de_agregado(
                    cur, session.get("user"), account_id=account_id
                )
                conn.commit()
                registrar_auditoria(
                    "alteracao", "relatorios.conciliar_fatura_importar", sucesso=True,
                    detalhes={
                        "conta": account_id, "fatura_id": fatura_id,
                        "mes_referencia": fatura["mes_referencia"], "ano_referencia": fatura["ano_referencia"],
                        "linhas": len(fatura["linhas"]),
                        "parcelas": resumo_parcelas,
                    },
                )

    if not erro and fatura_id:
        cur.execute(
            "SELECT f.*, c.tipo FROM cartao.fatura_importada f "
            "JOIN cartao.conta c ON c.account_id = f.account_id WHERE f.id = %s;",
            (fatura_id,),
        )
        fatura_row = cur.fetchone()
        if not fatura_row:
            erro = "Fatura não encontrada."
        else:
            account_id = str(fatura_row["account_id"])
            fatura_meta = {
                "id": fatura_id, "mes_referencia": fatura_row["mes_referencia"],
                "ano_referencia": fatura_row["ano_referencia"],
                "total": _reais(_centavos(fatura_row["total"])),
                "cartao_final4": fatura_row["cartao_final4"], "arquivo_nome": fatura_row["arquivo_nome"],
                "importado_em": data_hora_local(fatura_row["importado_em"]),
                "periodo_inicio": fatura_row["periodo_inicio"], "periodo_fim": fatura_row["periodo_fim"],
                "vencimento": fatura_row["vencimento"],
            }
            # A tela le o estado dos VINCULOS gravados, nao recalcula heuristica
            # a cada abertura (era isso que fazia o resultado mudar sozinho e a
            # mesma cobranca aparecer como sobra em dois meses). O casamento
            # automatico so roda no import ou quando o usuario pede.
            resultado = _estado_fatura(cur, fatura_row)
            resultado["fatura"] = fatura_meta

    # historico de faturas ja importadas, pra reabrir sem reenviar o PDF
    # `fecha_100` por fatura, no proprio SELECT: linha sem vinculo E lancamento
    # do Pluggy sem vinculo, ambos zerados. O inicio do ciclo e' o encadeado
    # (fim da anterior + 1 dia), o mesmo que a tela mostra - nao o congelado.
    cur.execute(
        f"SELECT f.id, f.account_id, f.mes_referencia, f.ano_referencia, f.total, f.importado_em, "
        f"f.periodo_inicio, f.periodo_fim, f.vencimento, "
        f"(SELECT COUNT(*) FROM cartao.fatura_linha fl WHERE fl.fatura_id = f.id "
        f" AND fl.descricao NOT ILIKE 'Pagamento Recebido%%' "
        f" AND fl.descricao NOT ILIKE 'Pag de Fatura%%' "
        f" AND NOT EXISTS (SELECT 1 FROM cartao.fatura_vinculo v WHERE v.fatura_linha_id = fl.id)"
        f") AS linhas_sem_vinculo, "
        f"(SELECT COUNT(*) FROM cartao.transacao t WHERE t.account_id = f.account_id "
        f" AND COALESCE(t.duplicada,false) = false AND t.substituido_por IS NULL "
        f" AND COALESCE(t.somente_conciliacao,false) = false "
        f" AND ({DATA_LOCAL_SQL})::date BETWEEN COALESCE(("
        f"   SELECT ant.periodo_fim + 1 FROM cartao.fatura_importada ant "
        f"   WHERE ant.account_id = f.account_id AND ant.periodo_fim IS NOT NULL "
        f"   AND (ant.ano_referencia, ant.mes_referencia) < (f.ano_referencia, f.mes_referencia) "
        f"   ORDER BY ant.ano_referencia DESC, ant.mes_referencia DESC LIMIT 1), f.periodo_inicio) "
        f" AND f.periodo_fim "
        f" AND NOT EXISTS (SELECT 1 FROM cartao.fatura_vinculo v WHERE v.transacao_id = t.transacao_id)"
        f") AS orfaos "
        f"FROM cartao.fatura_importada f "
        f"ORDER BY f.ano_referencia DESC, f.mes_referencia DESC, f.importado_em DESC;"
    )
    historico = []
    linhas_historico = cur.fetchall()
    # o inicio mostrado e' o encadeado (fim da fatura anterior + 1 dia), nao o
    # que ficou congelado no import - assim a ordem de envio dos PDFs nao muda
    # o que a tela mostra
    for r in linhas_historico:
        conta = contas_by_id.get(str(r["account_id"]))
        historico.append({
            **r, "conta_label": conta["label_curto"] if conta else "(conta removida)",
            "importado_em": data_hora_local(r["importado_em"]),
            "periodo_inicio": _ciclo_inicio_encadeado(cur, r),
            "fecha_100": not r["linhas_sem_vinculo"] and not r["orfaos"],
        })

    # navegacao entre faturas: `historico` ja vem da mais nova para a mais
    # antiga, entao a seguinte na lista e' o mes ANTERIOR
    fatura_mais_nova = fatura_mais_antiga = None
    if fatura_id:
        ids = [h["id"] for h in historico]
        if fatura_id in ids:
            pos = ids.index(fatura_id)
            fatura_mais_nova = historico[pos - 1] if pos > 0 else None
            fatura_mais_antiga = historico[pos + 1] if pos + 1 < len(historico) else None

    cur.close()
    conn.close()
    return render_template(
        "conciliar_fatura.html",
        fatura_mais_nova=fatura_mais_nova,
        fatura_mais_antiga=fatura_mais_antiga,
        titulo="Conciliar fatura",
        topbar=topbar_html("Conciliar fatura", "conciliar-fatura"),
        contas_credito=contas_credito,
        categorias=categorias_template,
        account_id=account_id,
        erro=erro,
        resultado=resultado,
        historico=historico,
        fatura_id=fatura_id,
        pode_editar_conciliacao=pode("conciliacao_editar"),
        pode_criar_lancamento=pode("lancamentos_manual"),
    )


@bp.route("/api/fatura-linha/<int:linha_id>/criar-lancamento", methods=["POST"])
@requer("lancamentos_manual")
def criar_lancamento_de_fatura(linha_id):
    """Cria um lancamento manual a partir de uma linha da fatura que a
    conciliacao nao achou no Pluggy (ex: tarifa que o banco nao sincronizou) -
    a fatura oficial vira a fonte, ja que o Pluggy nao trouxe."""
    data = request.get_json(force=True) or {}
    categoria = (data.get("categoria") or "").strip()
    if not categoria:
        return jsonify({"ok": False, "erro": "Escolha uma categoria."}), 400

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT fl.id, fl.data, fl.descricao, fl.valor, fl.transacao_id_criado, fi.account_id "
            "FROM cartao.fatura_linha fl JOIN cartao.fatura_importada fi ON fi.id = fl.fatura_id "
            "WHERE fl.id = %s FOR UPDATE;",
            (linha_id,),
        )
        linha = cur.fetchone()
        if not linha:
            return jsonify({"ok": False, "erro": "Linha da fatura não encontrada."}), 404
        if linha["transacao_id_criado"]:
            return jsonify({"ok": False, "erro": "Essa linha já tem um lançamento criado."}), 409

        transacao_id = uuid.uuid4()
        # valor da fatura sempre positivo = despesa; conta de credito trata
        # valor_brl positivo como gasto (ver VAL_DESPESA em core.py)
        cur.execute(
            "INSERT INTO cartao.transacao ("
            "transacao_id, account_id, descricao, descricao_bruta, valor_original, moeda_original, "
            "valor_brl, data_transacao, categoria, categoria_manual, status, tipo, "
            "observacao_sistema, criado_em, atualizado_em, sincronizado_em, primeiro_sincronizado_em"
            ") VALUES (%s,%s,%s,%s,%s,'BRL',%s,%s,%s,true,'POSTED','DEBIT',%s, now(), now(), now(), now());",
            (
                str(transacao_id), linha["account_id"], linha["descricao"], linha["descricao"],
                linha["valor"], linha["valor"], f"{linha['data']} 12:00:00-03:00", categoria,
                "Criado a partir da fatura em PDF - não veio do Pluggy.",
            ),
        )
        cur.execute(
            "UPDATE cartao.fatura_linha SET transacao_id_criado = %s WHERE id = %s;",
            (str(transacao_id), linha_id),
        )
        conn.commit()
        registrar_mudanca_auditoria("Lançamento criado a partir da fatura", None, {
            "fatura_linha_id": linha_id, "transacao_id": str(transacao_id),
            "descricao": linha["descricao"], "valor": float(linha["valor"]), "categoria": categoria,
        })
        return jsonify({"ok": True, "transacao_id": str(transacao_id)})
    except Exception as exc:
        conn.rollback()
        return jsonify({"ok": False, "erro": f"Não consegui salvar: {exc}"}), 400
    finally:
        cur.close()
        conn.close()


def _par_substituicao_compativel(origem, destino):
    """Ultima trava antes de excluir um registro do resultado.

    A classificacao de orfaos ja monta pares conservadores, mas esta validacao
    independente impede que um resultado antigo ou defeituoso associe
    estabelecimentos diferentes apenas por proximidade de data ou valor.
    """
    if not origem or not destino:
        return False
    if str(origem["account_id"]) != str(destino["account_id"]):
        return False
    dias = abs((origem["data"] - destino["data"]).days)
    valor_origem = abs(_centavos(origem["valor"]))
    valor_destino = abs(_centavos(destino["valor"]))

    # Pagamento/credito duplicado pode chegar com descricoes inteiramente
    # diferentes; nesse caso exigimos dia e valor exatos.
    if _centavos(origem["valor"]) <= 0 and _centavos(destino["valor"]) <= 0:
        return dias == 0 and abs(valor_origem - valor_destino) <= 2

    tokens_comuns = (
        _tokens_significativos(origem["descricao"])
        & _tokens_significativos(destino["descricao"])
    )
    if dias > 5 or len(tokens_comuns) < 2:
        return False
    if abs(valor_origem - valor_destino) <= 100:
        return True

    # Eco de parcelamento: um lado pode ser a parcela mensal e o outro o
    # valor total da compra. A relacao precisa ser um numero inteiro plausivel.
    menor, maior = sorted((valor_origem, valor_destino))
    if menor == 0:
        return False
    parcelas = round(maior / menor)
    return 2 <= parcelas <= 24 and abs(maior - menor * parcelas) <= 100


def _classificar_orfaos(cur, incluir_duplicadas=False):
    """Separa os lancamentos sem vinculo em tres baldes, usando o modelo de
    dados (nao heuristica de lojista):

    - inequivocos: o lancamento repete uma parcela de um parcelamento cujo
      AGREGADO ja esta marcado `somente_conciliacao` e cujas parcelas ja foram
      geradas pela fatura. Como a fatura e' a autoridade e ela ja cobra aquele
      parcelamento por inteiro, essa cobranca a mais nao existe na fatura.
    - aguardando: cai no ciclo da fatura MAIS RECENTE da conta. Compra perto do
      fechamento entra na fatura seguinte, que ainda nao foi importada - some
      sozinho quando o proximo PDF chegar. Nao e' duplicidade.
    - revisar: o resto, que precisa de olho humano.

    Agrupar parcelamento por (lojista + numero de parcelas) NAO serve: o mesmo
    lojista pode ter varios parcelamentos de 6x com valores diferentes (caso
    real: MECANICA HOCHIOVE tem 6x R$583,33 e 6x R$1.316,66). O valor da
    parcela entra na chave.
    """
    cur.execute(
        f"SELECT fl.descricao_base, fl.parcela_total, fl.titular, fl.valor, "
        f"COUNT(DISTINCT fl.id) AS linhas, "
        f"MIN(({DATA_LOCAL_SQL})::date) AS data_agregado, "
        f"MIN(t.transacao_id::text) AS agregado_id "
        f"FROM cartao.fatura_linha fl "
        f"JOIN cartao.fatura_vinculo v ON v.fatura_linha_id = fl.id "
        f"JOIN cartao.transacao t ON t.transacao_id = v.transacao_id "
        f"WHERE fl.parcela_total >= 2 AND COALESCE(t.somente_conciliacao,false) "
        f"GROUP BY fl.descricao_base, fl.parcela_total, fl.titular, fl.valor;"
    )
    cobertos = []
    for r in cur.fetchall():
        valor_centavos = _centavos(r["valor"])
        cobertos.append({
            "base": _normalizar_desc(r["descricao_base"]),
            "parcela_total": r["parcela_total"],
            "valor": _reais(valor_centavos),
            "valor_centavos": valor_centavos,
            "linhas": r["linhas"],
            "data_agregado": r["data_agregado"],
            "agregado_id": r["agregado_id"],
            "titular": r["titular"],
            "descricao_base": r["descricao_base"],
        })

    # ciclo da fatura mais recente por conta (compra perto do fechamento entra
    # na fatura seguinte, que ainda nao existe no sistema)
    cur.execute(
        "SELECT DISTINCT ON (account_id) account_id, periodo_inicio, periodo_fim, "
        "mes_referencia, ano_referencia FROM cartao.fatura_importada "
        "WHERE periodo_fim IS NOT NULL "
        "ORDER BY account_id, ano_referencia DESC, mes_referencia DESC;"
    )
    ultima_por_conta = {str(r["account_id"]): dict(r) for r in cur.fetchall()}

    # ja vinculadas: servem de "par" quando o Pluggy gravou o mesmo evento
    # duas vezes com descricoes diferentes (pending -> posted)
    cur.execute(
        f"SELECT t.transacao_id, t.account_id, t.descricao, "
        f"COALESCE(t.valor_brl, t.valor_original) AS valor, "
        f"({DATA_LOCAL_SQL})::date AS data_local FROM cartao.transacao t "
        f"WHERE EXISTS (SELECT 1 FROM cartao.fatura_vinculo v "
        f"WHERE v.transacao_id = t.transacao_id);"
    )
    vinculadas = []
    for r in cur.fetchall():
        valor_centavos = _centavos(r["valor"])
        vinculadas.append({
            "transacao_id": str(r["transacao_id"]), "account_id": str(r["account_id"]),
            "descricao": r["descricao"], "valor": _reais(valor_centavos),
            "valor_centavos": valor_centavos,
            "data": r["data_local"], "tokens": _tokens_significativos(r["descricao"]),
        })

    # linhas de fatura que ja tem vinculo, com o valor da parcela e a descricao
    # impressa: cobrem o eco de parcelamento NOVO, em que o agregado ainda
    # atende uma linha so e por isso nao foi reconhecido como agregado.
    cur.execute(
        f"SELECT fl.descricao_base, fl.valor AS valor_parcela, fl.parcela_total, "
        f"v.transacao_id, t.account_id, t.descricao AS desc_vinculada, "
        f"({DATA_LOCAL_SQL})::date AS data_vinculada "
        f"FROM cartao.fatura_linha fl "
        f"JOIN cartao.fatura_vinculo v ON v.fatura_linha_id = fl.id "
        f"JOIN cartao.transacao t ON t.transacao_id = v.transacao_id "
        f"WHERE fl.parcela_total >= 2 AND fl.descricao_base IS NOT NULL;"
    )
    linhas_vinculadas = []
    for r in cur.fetchall():
        valor_centavos = _centavos(r["valor_parcela"])
        linhas_vinculadas.append({
            "tokens": _tokens_significativos(r["descricao_base"]),
            "valor_parcela": _reais(valor_centavos),
            "valor_parcela_centavos": valor_centavos,
            "parcela_total": r["parcela_total"],
            "transacao_id": str(r["transacao_id"]), "account_id": str(r["account_id"]),
            "descricao_base": r["descricao_base"], "data": r["data_vinculada"],
        })

    # estornos/cancelamentos: anulam uma cobranca do mesmo dia e valor
    # So conta como estorno o negativo que AINDA ESTA no resultado. Se ele ja
    # foi excluido (duplicada, substituido_por, somente_conciliacao), o par
    # deixou de se anular: a cobranca positiva ficou sozinha contando, e tem
    # que seguir para as outras regras em vez de ser dada como resolvida.
    cur.execute(
        f"SELECT t.account_id, COALESCE(t.valor_brl, t.valor_original) AS valor, "
        f"({DATA_LOCAL_SQL})::date AS data_local FROM cartao.transacao t "
        f"WHERE COALESCE(t.valor_brl, t.valor_original) < 0 "
        f"AND COALESCE(t.duplicada,false) = false AND t.substituido_por IS NULL "
        f"AND COALESCE(t.somente_conciliacao,false) = false;"
    )
    estornos = {(str(r["account_id"]), r["data_local"], abs(_centavos(r["valor"])))
                for r in cur.fetchall()}

    cur.execute(
        f"SELECT t.transacao_id, t.account_id, t.descricao, "
        f"COALESCE(t.valor_brl, t.valor_original) AS valor, "
        f"({DATA_LOCAL_SQL})::date AS data_local, t.categoria "
        f"FROM cartao.transacao t "
        f"JOIN cartao.fatura_importada fi ON fi.account_id = t.account_id "
        f"AND ({DATA_LOCAL_SQL})::date BETWEEN fi.periodo_inicio AND fi.periodo_fim "
        f"WHERE ({'TRUE' if incluir_duplicadas else 'COALESCE(t.duplicada,false) = false'}) "
        f"AND COALESCE(t.somente_conciliacao,false) = false "
        f"AND t.substituido_por IS NULL "
        f"AND NOT EXISTS (SELECT 1 FROM cartao.fatura_vinculo v "
        f"WHERE v.transacao_id = t.transacao_id) "
        f"GROUP BY t.transacao_id, t.account_id, t.descricao, t.valor_brl, t.valor_original, "
        f"t.data_transacao, t.categoria "
        f"ORDER BY 5, 3;"
    )
    repetidas, ecos, estornadas, aguardando, revisar = [], [], [], [], []
    for r in cur.fetchall():
        valor_centavos = _centavos(r["valor"])
        valor = _reais(valor_centavos)
        item = {
            "transacao_id": str(r["transacao_id"]), "descricao": r["descricao"],
            "valor": valor, "data": r["data_local"], "categoria": r["categoria"],
        }
        desc = _normalizar_desc(r["descricao"])
        tokens = _tokens_significativos(r["descricao"])
        # Valor negativo (pagamento de fatura, credito) nao entra nas regras de
        # parcelamento nem de estorno, mas PODE ser o mesmo evento gravado duas
        # vezes: o Pluggy manda o pagamento como "Pag de Fatura Via Deb Aut" e
        # de novo como "Pagamento recebido", mesmo dia e mesmo valor. Como o
        # par tem o MESMO sinal, um estorno (que tem sinal oposto ao da
        # cobranca) nunca casa aqui.
        if valor <= 0:
            # Aqui os tokens nao ajudam: o Pluggy chama o mesmo pagamento de
            # "Pagamento recebido" e de "Pag de Fatura Via Deb Aut", que nao
            # tem palavra nenhuma em comum. O que identifica o par e' valor
            # identico no MESMO dia, com a outra gravacao ja vinculada a
            # fatura. Exige dia exato (nao 5) justamente por nao ter o reforco
            # da descricao.
            par_neg = next(
                (v for v in vinculadas
                 if v["account_id"] == str(r["account_id"])
                 and abs(v["valor_centavos"] - valor_centavos) <= 2
                 and v["data"] == r["data_local"]),
                None,
            )
            if par_neg:
                item["motivo"] = (
                    f"O Pluggy gravou este mesmo lançamento duas vezes. A outra gravação "
                    f"(\"{par_neg['descricao'][:60]}\") é a que está vinculada à fatura."
                )
                item["substituto_id"] = par_neg["transacao_id"]
                ecos.append(item)
            continue

        # Só a forma MENSAL entra em "inequívoco". Nesse cartão as duas formas
        # são distinguíveis pela descrição e significam coisas diferentes:
        #   "Parcelado Lojista - Visa - X"  = o parcelamento inteiro (agregado)
        #   "Parcela Lojista Visa - X"      = a cobrança de UMA parcela
        # Um agregado sem vínculo costuma ser parcelamento novo, cujas parcelas
        # ainda vão aparecer em faturas futuras - marcar como duplicidade
        # apagaria uma compra real. Ele vai para "revisar".
        e_mensal = desc.startswith("PARCELA LOJISTA")
        casado = next(
            (c for c in cobertos
             if abs(c["valor_centavos"] - valor_centavos) <= 2
             and c["base"] and c["base"] in desc),
            None,
        ) if e_mensal else None
        if casado:
            # Dois fenomenos diferentes, com causas diferentes:
            #  - ECO do ciclo PENDING -> POSTED: o Pluggy registra a compra na
            #    autorizacao e registra DE NOVO ao consolidar como parcelamento,
            #    sem remover a primeira. Cai a 1-5 dias do agregado, e costuma
            #    ter 1 centavo de diferenca (arredondamento da parcela).
            #  - PARCELA REPETIDA: cobranca mensal que chega meses depois do
            #    agregado, que ja cobre o parcelamento inteiro.
            dias = abs((item["data"] - casado["data_agregado"]).days) \
                if casado["data_agregado"] else 999
            item["dias_do_agregado"] = dias
            if dias <= 5:
                item["motivo"] = (
                    f"Registrado {dias} dia(s) do lançamento da compra inteira de "
                    f"{casado['base']} ({casado['parcela_total']}x de R$ {casado['valor']:,.2f}) — "
                    f"é a mesma compra gravada duas vezes pelo Pluggy, na autorização e "
                    f"depois já consolidada como parcelamento."
                )
                # o que substituiu o eco e' a compra ja consolidada
                item["substituto_id"] = casado["agregado_id"]
                ecos.append(item)
            else:
                item["motivo"] = (
                    f"Repete uma parcela de {casado['base']} ({casado['parcela_total']}x de "
                    f"R$ {casado['valor']:,.2f}), cobrada {dias} dias depois da compra. A compra "
                    f"inteira já está lançada e fora do resultado, e a fatura já cobre "
                    f"{casado['linhas']} parcela(s) — essa cobrança a mais não existe na fatura."
                )
                # o que vale e' a parcela que a fatura gerou para o mes em
                # que essa cobranca caiu; sem ela, aponta para a compra inteira
                cur.execute(
                    "SELECT fl.transacao_id_criado FROM cartao.fatura_linha fl "
                    "JOIN cartao.fatura_importada fi ON fi.id = fl.fatura_id "
                    "WHERE fl.descricao_base = %s AND fl.parcela_total = %s "
                    "AND fl.titular = %s AND fl.valor = %s "
                    "AND %s BETWEEN fi.periodo_inicio AND fi.periodo_fim "
                    "AND fl.transacao_id_criado IS NOT NULL LIMIT 1;",
                    (casado["descricao_base"], casado["parcela_total"], casado["titular"],
                     _decimal_monetario(casado["valor"]), item["data"]),
                )
                alvo = cur.fetchone()
                item["substituto_id"] = (
                    str(alvo["transacao_id_criado"]) if alvo and alvo["transacao_id_criado"]
                    else casado["agregado_id"]
                )
                repetidas.append(item)
            continue

        # cobranca que a operadora estornou: os dois lancamentos sao legitimos
        # e se anulam sozinhos no resultado. Marcar um deles deixaria o estorno
        # negativo solto - por isso fica so informativo, sem acao.
        if (str(r["account_id"]), r["data_local"], valor_centavos) in estornos:
            item["motivo"] = (
                "Existe um estorno de mesmo valor no mesmo dia. Os dois lançamentos são "
                "legítimos e se anulam sozinhos — não há nada a marcar."
            )
            estornadas.append(item)
            continue

        # mesmo evento gravado duas vezes pelo Pluggy, com descricoes
        # diferentes (pending -> posted): existe uma transacao JA VINCULADA a
        # fatura, mesma conta, mesmo valor, mesmo dia (ou um de diferenca), e
        # com os mesmos tokens de estabelecimento.
        par = next(
            (v for v in vinculadas
             if v["account_id"] == str(r["account_id"])
             and abs(v["valor_centavos"] - valor_centavos) <= 2
             and abs((v["data"] - r["data_local"]).days) <= 5
             and len(tokens & v["tokens"]) >= 2),
            None,
        ) if tokens else None
        if par:
            item["motivo"] = (
                f"O Pluggy gravou este mesmo evento duas vezes, com descrições diferentes. "
                f"A outra gravação (\"{par['descricao'][:60]}\") é a que está vinculada à fatura."
            )
            item["substituto_id"] = par["transacao_id"]
            ecos.append(item)
            continue

        # eco de parcelamento NOVO: existe uma linha de fatura do mesmo
        # estabelecimento ja vinculada, e o orfao vale ou a parcela dela ou o
        # parcelamento inteiro. O agregado dessa compra ainda atende uma linha
        # so, entao nao entrou em `cobertos` - sem esta regra o eco so seria
        # pego quando a fatura do mes seguinte chegasse.
        alvo = next(
            (l for l in linhas_vinculadas
             if l["account_id"] == str(r["account_id"])
             and l["data"] and abs((l["data"] - r["data_local"]).days) <= 5
             and len(tokens & l["tokens"]) >= 2
             and (abs(l["valor_parcela_centavos"] - valor_centavos) <= 5
                  or abs(l["valor_parcela_centavos"] * l["parcela_total"]
                         - valor_centavos) <= 100)),
            None,
        ) if tokens else None
        if alvo:
            item["motivo"] = (
                f"Mesma compra de {alvo['descricao_base']} ({alvo['parcela_total']}x de "
                f"R$ {alvo['valor_parcela']:,.2f}) gravada duas vezes pelo Pluggy, com "
                f"{abs((alvo['data'] - r['data_local']).days)} dia(s) de diferença. A linha da "
                f"fatura já está vinculada à outra gravação."
            )
            item["substituto_id"] = alvo["transacao_id"]
            ecos.append(item)
            continue

        ultima = ultima_por_conta.get(str(r["account_id"]))
        if ultima and ultima["periodo_inicio"] <= r["data_local"] <= ultima["periodo_fim"]:
            item["motivo"] = (
                f"Está no ciclo da fatura mais recente ({ultima['mes_referencia']:02d}/"
                f"{ultima['ano_referencia']}). Compra perto do fechamento entra na fatura "
                f"seguinte — deve se resolver quando o próximo PDF for importado."
            )
            aguardando.append(item)
        else:
            item["motivo"] = "Sem vínculo com nenhuma linha de fatura e sem padrão claro."
            revisar.append(item)
    return {"repetidas": repetidas, "ecos": ecos, "estornadas": estornadas,
            "aguardando": aguardando, "revisar": revisar}


def _revincular_lancamentos_da_fatura(cur, usuario):
    """Garante o vinculo de toda linha que ja virou lancamento.

    Duas situacoes apagam esse vinculo sem recria-lo, porque a geracao de
    parcelas e' idempotente por `transacao_id_criado`:
      - "refazer vinculos automaticos" (deu 340 falsos orfaos de uma vez);
      - REENVIAR o PDF: as linhas sao apagadas e recriadas com ids novos, e o
        ON DELETE CASCADE leva os vinculos junto. O `transacao_id_criado` e'
        preservado pela chave natural, mas o vinculo nao.
    """
    cur.execute(
        "INSERT INTO cartao.fatura_vinculo (fatura_linha_id, transacao_id, origem, criado_por) "
        "SELECT fl.id, fl.transacao_id_criado, 'fatura', %s FROM cartao.fatura_linha fl "
        "WHERE fl.transacao_id_criado IS NOT NULL "
        "ON CONFLICT (fatura_linha_id, transacao_id) DO NOTHING;",
        (usuario,),
    )
    return cur.rowcount or 0


def _sincronizar_parcelas_de_agregado(cur, usuario, account_id=None, preview=False):
    """Regime de caixa para parcelamento (decisão do usuário, 29/08/2026).

    O Pluggy as vezes grava o parcelamento inteiro como UMA transacao no valor
    cheio, na data da compra. Contar assim joga a despesa toda no mes da compra
    e deixa os demais meses vazios - mas o dinheiro sai parcela a parcela.
    Aqui:
      1. todo agregado (transacao vinculada a 2+ linhas de fatura) vira
         `somente_conciliacao` - continua existindo e conciliando, sai do
         resultado;
      2. cada linha de fatura ligada a ele vira um lancamento proprio, no valor
         e no mes em que a fatura cobrou - a fatura e' a autoridade.

    Idempotente: usa `fatura_linha.transacao_id_criado`, que ja existe pra
    marcar "esta linha ja virou lancamento". Rodar de novo nao duplica.
    Herda categoria e dimensoes do agregado, para nao perder classificacao.
    """
    # A prévia é estritamente somente leitura. Na execução, recompõe antes os
    # vínculos de parcelas já criadas que um reenvio de PDF possa ter removido.
    if not preview:
        _revincular_lancamentos_da_fatura(cur, usuario)
    escopo_sql = " AND t.account_id=%s " if account_id else ""
    escopo_params = (account_id,) if account_id else ()
    cur.execute(
        "SELECT v.transacao_id, COUNT(DISTINCT v.fatura_linha_id) AS linhas, "
        "COALESCE(bool_or(t.somente_conciliacao),false) AS era_tecnico "
        "FROM cartao.fatura_vinculo v "
        "JOIN cartao.transacao t ON t.transacao_id=v.transacao_id "
        "JOIN cartao.fatura_linha fl ON fl.id=v.fatura_linha_id "
        "WHERE NOT EXISTS (SELECT 1 FROM cartao.fatura_linha criada "
        " WHERE criada.transacao_id_criado=v.transacao_id) "
        + escopo_sql +
        "GROUP BY v.transacao_id "
        "HAVING COUNT(DISTINCT v.fatura_linha_id) >= 2 OR bool_or("
        " fl.parcela_total >= 2 AND ABS(ABS(COALESCE(t.valor_brl,t.valor_original)) "
        " - ABS(fl.valor * fl.parcela_total)) <= 1.00);",
        escopo_params,
    )
    agregados_info = {str(r["transacao_id"]): dict(r) for r in cur.fetchall()}
    agregados = list(agregados_info)
    if not agregados and preview:
        return {"agregados": 0, "parcelas_pendentes": 0, "parcelas_criadas": 0,
                "preview": True}
    # Sem nenhum agregado a execucao continua de proposito: ainda pode haver
    # marca obsoleta de agregado para retirar (ver o UPDATE de desmarcacao).

    # Conta antes de alterar. O mesmo levantamento alimenta a confirmação da
    # interface e impede que o usuário confirme uma operação de impacto incerto.
    cur.execute(
        "SELECT COUNT(*) AS parcelas_pendentes FROM cartao.fatura_linha fl "
        "JOIN cartao.fatura_importada fi ON fi.id=fl.fatura_id "
        "JOIN cartao.fatura_vinculo v ON v.fatura_linha_id=fl.id "
        "WHERE v.transacao_id=ANY(%s::uuid[]) AND fl.transacao_id_criado IS NULL "
        "AND fi.periodo_fim IS NOT NULL;",
        (agregados,),
    )
    parcelas_pendentes = int(cur.fetchone()["parcelas_pendentes"] or 0)
    if preview:
        return {
            "agregados": len(agregados), "parcelas_pendentes": parcelas_pendentes,
            "parcelas_criadas": 0, "preview": True,
        }

    cur.execute(
        "UPDATE cartao.transacao SET somente_conciliacao = true, atualizado_em = now() "
        "WHERE transacao_id = ANY(%s::uuid[]) AND NOT somente_conciliacao;",
        (agregados,),
    )
    marcados = cur.rowcount or 0

    # E o caminho de volta. A marca so era posta, nunca retirada: quando o
    # conjunto de vinculos muda (refazer vinculos, reenvio de PDF, desvincular
    # na mao), a transacao deixa de ser agregado e continuava `somente
    # conciliacao` para sempre - fora do DRE, sem nenhuma parcela cobrindo o
    # lugar dela. Foi assim que 5 compras A VISTA sumiram do resultado
    # (R$ 1.167,38, faturas 09/2025, 10/2025, 12/2025 e 01/2026).
    # Trava: so desmarca quando NENHUMA linha vinculada a ela ja virou
    # lancamento proprio. Se alguma virou, as parcelas e' que contam e o
    # agregado tem que continuar fora, senao a despesa conta duas vezes.
    cur.execute(
        "UPDATE cartao.transacao t SET somente_conciliacao = false, atualizado_em = now() "
        "WHERE t.somente_conciliacao "
        "AND NOT (t.transacao_id = ANY(%s::uuid[])) "
        "AND EXISTS (SELECT 1 FROM cartao.fatura_vinculo v "
        " WHERE v.transacao_id = t.transacao_id) "
        "AND NOT EXISTS (SELECT 1 FROM cartao.fatura_vinculo v "
        " JOIN cartao.fatura_linha fl ON fl.id = v.fatura_linha_id "
        " WHERE v.transacao_id = t.transacao_id "
        " AND fl.transacao_id_criado IS NOT NULL) "
        + (" AND t.account_id=%s " if account_id else ""),
        (agregados,) + escopo_params,
    )
    desmarcados = cur.rowcount or 0

    # linhas cobertas por um agregado e que ainda nao viraram lancamento
    cur.execute(
        "SELECT fl.id, fl.data, fl.descricao, fl.valor, fl.titular, "
        "fi.periodo_fim, fi.account_id, fi.mes_referencia, fi.ano_referencia, "
        "v.transacao_id AS agregado_id "
        "FROM cartao.fatura_linha fl "
        "JOIN cartao.fatura_importada fi ON fi.id = fl.fatura_id "
        "JOIN cartao.fatura_vinculo v ON v.fatura_linha_id = fl.id "
        "WHERE v.transacao_id = ANY(%s::uuid[]) AND fl.transacao_id_criado IS NULL "
        "AND fi.periodo_fim IS NOT NULL "
        "ORDER BY fi.ano_referencia, fi.mes_referencia;",
        (agregados,),
    )
    pendentes = [dict(r) for r in cur.fetchall()]

    criadas = 0
    for linha in pendentes:
        cur.execute(
            "SELECT categoria, natureza, observacao, conferida, conferida_por, conferida_em "
            "FROM cartao.transacao WHERE transacao_id = %s;",
            (str(linha["agregado_id"]),),
        )
        origem = cur.fetchone()
        info_agregado = agregados_info.get(str(linha["agregado_id"]), {})
        # Quando o PDF revelou pela primeira vez que um registro do Pluggy e'
        # a compra inteira, esta linha mensal assume o trabalho humano que ja'
        # estava nela. Parcelas futuras continuam independentes.
        transferir_trabalho = bool(
            origem and int(info_agregado.get("linhas") or 0) == 1
            and not info_agregado.get("era_tecnico")
        )
        novo_id = str(uuid.uuid4())
        # data = fim do ciclo da fatura que cobrou esta parcela. Nao da' pra
        # usar a data impressa na linha: numa parcela ela e' a da COMPRA
        # ORIGINAL, fixa em toda reimpressao mensal.
        cur.execute(
            "INSERT INTO cartao.transacao ("
            "transacao_id, account_id, descricao, descricao_bruta, valor_original, moeda_original, "
            "valor_brl, data_transacao, categoria, categoria_manual, natureza, status, tipo, "
            "observacao, conferida, conferida_por, conferida_em, observacao_sistema, "
            "criado_em, atualizado_em, sincronizado_em, primeiro_sincronizado_em"
            ") VALUES (%s,%s,%s,%s,%s,'BRL',%s,%s,%s,true,%s,'POSTED','DEBIT',"
            "%s,%s,%s,%s,%s,now(),now(),now(),now());",
            (
                novo_id, linha["account_id"], linha["descricao"], linha["descricao"],
                linha["valor"], linha["valor"],
                f"{linha['periodo_fim']} 12:00:00-03:00",
                origem["categoria"] if origem else None,
                origem["natureza"] if origem else None,
                origem["observacao"] if origem else None,
                bool(origem["conferida"]) if transferir_trabalho else False,
                origem["conferida_por"] if transferir_trabalho else None,
                origem["conferida_em"] if transferir_trabalho else None,
                f"Parcela gerada pela fatura {linha['mes_referencia']:02d}/{linha['ano_referencia']} "
                f"(a compra inteira está em outro lançamento, fora do resultado).",
            ),
        )
        cur.execute(
            "INSERT INTO cartao.transacao_dimensao (transacao_id, dimensao_id, valor_id) "
            "SELECT %s, dimensao_id, valor_id FROM cartao.transacao_dimensao WHERE transacao_id = %s "
            "ON CONFLICT (transacao_id, dimensao_id) DO NOTHING;",
            (novo_id, str(linha["agregado_id"])),
        )
        cur.execute(
            "UPDATE cartao.fatura_linha SET transacao_id_criado = %s WHERE id = %s;",
            (novo_id, linha["id"]),
        )
        cur.execute(
            "INSERT INTO cartao.fatura_vinculo (fatura_linha_id, transacao_id, origem, criado_por) "
            "VALUES (%s, %s, 'fatura', %s) ON CONFLICT DO NOTHING;",
            (linha["id"], novo_id, usuario),
        )
        criadas += 1

    preenchidas = preencher_classificacao_vazia_parcelas(cur)
    return {
        "agregados": len(agregados), "marcados_agora": marcados,
        "desmarcados_agora": desmarcados,
        "parcelas_pendentes": parcelas_pendentes, "parcelas_criadas": criadas,
        "classificacoes_preenchidas": preenchidas, "preview": False,
    }


@bp.route("/relatorios/duplicidades-fatura")
@requer("relatorios")
def duplicidades_fatura():
    """Revisao das cobrancas que o Pluggy trouxe e a fatura nao reconhece.
    A fatura e' a autoridade: toda linha dela ja tem a cobranca contabilizada
    (por casamento 1:1 ou pela parcela gerada), entao o que sobra sem vinculo
    ou e' duplicidade, ou e' compra que cai na fatura seguinte."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    baldes = _classificar_orfaos(cur)
    cur.close()
    conn.close()
    return render_template(
        "duplicidades_fatura.html",
        titulo="Duplicidades da fatura",
        topbar=topbar_html("Duplicidades da fatura", "duplicidades-fatura"),
        pode_editar_duplicidades=pode("lancamentos_editar"),
        **baldes,
    )


@bp.route("/api/duplicidades/marcar", methods=["POST"])
@requer("lancamentos_editar")
def marcar_duplicidades():
    """Registra que um lancamento e' o MESMO evento real que outro
    (`substituido_por`), com o vinculo 1-para-1 entre os dois - em vez de
    chama-lo de duplicado, que e' outra coisa.

    `alvo` escolhe o balde: 'repetidas' (parcela mensal por cima da parcela que
    a fatura ja cobra) ou 'ecos' (pending -> posted). Uma lista explicita de
    ids vem da revisao manual, e usa o substituto que a classificacao apontou.

    Nao apaga nada: o lancamento sai do resultado e continua consultavel, com
    o vinculo dizendo qual registro o substituiu.
    """
    data = request.get_json(force=True) or {}
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # alvo explicito: o usuario aponta os dois lados na mao. Serve para o
        # caso que nenhuma regra alcanca - inclusive quando o lancamento ja foi
        # marcado como duplicado por engano e precisa virar "mesmo evento".
        substituto_manual = (data.get("substituto_id") or "").strip()
        if substituto_manual and data.get("ids"):
            marcados = 0
            for tid in [str(i) for i in data["ids"]]:
                if tid == substituto_manual:
                    continue
                cur.execute(
                    "UPDATE cartao.transacao SET substituido_por = %s, duplicada = false, "
                    "atualizado_em = now() WHERE transacao_id = %s;",
                    (substituto_manual, tid),
                )
                marcados += cur.rowcount or 0
            conn.commit()
            if marcados:
                registrar_mudanca_auditoria(
                    "Mesmo evento apontado manualmente", None,
                    {"ids": data["ids"], "substituido_por": substituto_manual},
                )
            return jsonify({"ok": True, "marcados": marcados})

        baldes = _classificar_orfaos(cur, incluir_duplicadas=True)
        alvo = data.get("alvo")
        if alvo in ("repetidas", "ecos"):
            itens = baldes[alvo]
        else:
            pedidos = {str(i) for i in (data.get("ids") or [])}
            itens = [i for b in ("repetidas", "ecos", "revisar")
                     for i in baldes[b] if i["transacao_id"] in pedidos]
        itens = [i for i in itens if i.get("substituto_id")]
        if not itens:
            return jsonify({"ok": True, "marcados": 0})

        marcados = 0
        for i in itens:
            if i["substituto_id"] == i["transacao_id"]:
                continue  # nunca apontar para si mesmo
            cur.execute(
                f"SELECT transacao_id, account_id, descricao, "
                f"COALESCE(valor_brl,valor_original) AS valor, "
                f"({DATA_LOCAL_SQL})::date AS data FROM cartao.transacao "
                f"WHERE transacao_id IN (%s,%s);",
                (i["transacao_id"], i["substituto_id"]),
            )
            candidatos = {str(r["transacao_id"]): dict(r) for r in cur.fetchall()}
            if not _par_substituicao_compativel(
                candidatos.get(i["transacao_id"]),
                candidatos.get(i["substituto_id"]),
            ):
                continue
            cur.execute(
                "UPDATE cartao.transacao SET substituido_por = %s, duplicada = false, "
                "atualizado_em = now() WHERE transacao_id = %s AND substituido_por IS NULL;",
                (i["substituto_id"], i["transacao_id"]),
            )
            marcados += cur.rowcount or 0
        conn.commit()
        if marcados:
            registrar_mudanca_auditoria(
                "Lançamento marcado como mesmo evento que outro (substituido_por)",
                None,
                [{"transacao_id": i["transacao_id"], "descricao": i["descricao"],
                  "valor": i["valor"], "substituido_por": i["substituto_id"]} for i in itens],
            )
        return jsonify({"ok": True, "marcados": marcados})
    except Exception as exc:
        conn.rollback()
        return jsonify({"ok": False, "erro": f"Não consegui marcar: {exc}"}), 400
    finally:
        cur.close()
        conn.close()


# Cobrancas que a fatura tem e o Pluggy nao sincroniza. A fatura e' a
# autoridade: se ela cobrou, o dinheiro saiu - e a despesa (ou o credito)
# precisa existir no resultado. Cada padrao aponta a categoria correta.
# So entram tipos CONFERIDOS um a um contra o PDF; nao generalizar sozinho.
COBRANCAS_SO_NA_FATURA = [
    ("Unicred TAG%", "Tolls and in vehicle payment", "pedágio"),
    ("Anuidade - bonifica%", "Credit card fees", "bonificação da anuidade"),
    ("IOF compra internacional%", "Tax on financial operations", "IOF de compra internacional"),
    ("ESTORNO%", None, "estorno"),
]

# Linhas que NUNCA viram lancamento: sao a fatura anterior sendo quitada, nao
# uma cobranca deste ciclo (o proprio SALDO TOTAL da Unicred nao as inclui).
LINHAS_NAO_LANCAVEIS = ("Pagamento Recebido%", "Pagamento recebido%", "Pag de Fatura%")


def _criar_lancamento_da_linha(cur, linha, categoria, usuario):
    """Cria um lancamento usando a fatura como fonte e devolve o id.

    `categoria_manual` so fica ligado quando a categoria veio de um padrao
    conferido; sem categoria, as regras automaticas ainda podem classificar.
    """
    novo_id = str(uuid.uuid4())
    valor = _decimal_monetario(linha["valor"])
    # Numa linha de PARCELA a data impressa e' a da COMPRA ORIGINAL, fixa em
    # toda reimpressao mensal - usa-la jogaria a despesa no mes da compra, que
    # e' o oposto do regime de caixa. Vale o fim do ciclo da fatura que cobrou.
    # Em compra avulsa a data impressa e' a da propria cobranca.
    data_evento = (linha["periodo_fim"] if (linha.get("parcela_total") or 0) >= 2
                   and linha.get("periodo_fim") else linha["data"])
    cur.execute(
        "INSERT INTO cartao.transacao ("
        "transacao_id, account_id, descricao, descricao_bruta, valor_original, "
        "moeda_original, valor_brl, data_transacao, categoria, categoria_manual, "
        "status, tipo, observacao_sistema, criado_em, atualizado_em, sincronizado_em, "
        "primeiro_sincronizado_em) "
        "VALUES (%s,%s,%s,%s,%s,'BRL',%s,%s,%s,%s,'POSTED',%s,%s, now(), now(), now(), now());",
        (novo_id, linha["account_id"], linha["descricao"], linha["descricao"], valor, valor,
         f"{data_evento} 12:00:00-03:00", categoria, bool(categoria),
            "CREDIT" if valor < Decimal("0") else "DEBIT",
         f"Criado a partir da fatura {linha['mes_referencia']:02d}/{linha['ano_referencia']} — "
         f"a operadora cobrou e o Pluggy não sincronizou."),
    )
    cur.execute(
        "UPDATE cartao.fatura_linha SET transacao_id_criado = %s WHERE id = %s;",
        (novo_id, linha["id"]),
    )
    cur.execute(
        "INSERT INTO cartao.fatura_vinculo (fatura_linha_id, transacao_id, origem, criado_por) "
        "VALUES (%s, %s, 'fatura', %s) ON CONFLICT DO NOTHING;",
        (linha["id"], novo_id, usuario),
    )
    return novo_id


@bp.route("/api/faturas/criar-cobrancas-sem-pluggy", methods=["POST"])
@requer("lancamentos_manual")
def criar_cobrancas_sem_pluggy():
    """Cria, a partir da fatura, as cobrancas que o Pluggy nao sincronizou.

    A fatura e' a autoridade: se ela cobrou, o dinheiro saiu. Depois de todo o
    casamento (agregado, parcela, eco, tokens), uma linha SEM VINCULO significa
    que nao existe contraparte no Pluggy - e a despesa (ou o credito)
    simplesmente nao existe no resultado.

    Isso so e' seguro porque o outro lado esta zerado: quando nao sobra
    lancamento do Pluggy sem vinculo, criar pela fatura nao pode duplicar.
    Por isso a rota RECUSA se ainda houver orfao pendente.

    `preview: true` devolve o levantamento sem gravar nada.
    """
    data = request.get_json(silent=True) or {}
    preview = bool(data.get("preview"))
    # `ano` limita o alcance: 2025 e' historico e 2026 e' o que precisa fechar,
    # entao raramente se quer criar tudo de uma vez.
    ano = data.get("ano")
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if not preview:
            orfaos = _classificar_orfaos(cur)
            pendentes = len(orfaos["repetidas"]) + len(orfaos["ecos"]) + len(orfaos["revisar"])
            if pendentes:
                return jsonify({
                    "ok": False,
                    "erro": f"Ainda há {pendentes} lançamento(s) do Pluggy sem vínculo esperando "
                            f"decisão. Resolva em Duplicidades da fatura antes de criar pela "
                            f"fatura, senão o mesmo gasto pode entrar duas vezes.",
                }), 409

        # Corrige a data de parcela ja criada com a data impressa (a da compra
        # original). Idempotente: so mexe em quem esta fora do mes cobrado.
        cur.execute(
            "UPDATE cartao.transacao t SET data_transacao = "
            "(fi.periodo_fim + time '12:00') AT TIME ZONE 'America/Sao_Paulo', "
            "atualizado_em = now() "
            "FROM cartao.fatura_linha fl JOIN cartao.fatura_importada fi ON fi.id = fl.fatura_id "
            "WHERE fl.transacao_id_criado = t.transacao_id AND fl.parcela_total >= 2 "
            "AND fi.periodo_fim IS NOT NULL "
            f"AND ({DATA_LOCAL_SQL})::date <> fi.periodo_fim;"
        )
        datas_corrigidas = cur.rowcount or 0

        nao_lancaveis = " AND ".join(["fl.descricao NOT ILIKE %s"] * len(LINHAS_NAO_LANCAVEIS))
        cur.execute(
            "SELECT fl.id, fl.data, fl.descricao, fl.valor, fl.parcela_total, "
            "fi.account_id, fi.mes_referencia, fi.ano_referencia, fi.periodo_fim "
            "FROM cartao.fatura_linha fl "
            "JOIN cartao.fatura_importada fi ON fi.id = fl.fatura_id "
            "WHERE fl.transacao_id_criado IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM cartao.fatura_vinculo v WHERE v.fatura_linha_id = fl.id) "
            f"AND {nao_lancaveis} "
            + ("AND fi.ano_referencia = %s " if ano else "")
            + "ORDER BY fi.ano_referencia, fi.mes_referencia, fl.data;",
            LINHAS_NAO_LANCAVEIS + ((int(ano),) if ano else ()),
        )
        linhas = [dict(r) for r in cur.fetchall()]

        por_mes, criadas = {}, 0
        for l in linhas:
            categoria = next(
                (cat for padrao, cat, _ in COBRANCAS_SO_NA_FATURA
                 if cat and _normalizar_desc(l["descricao"]).startswith(
                     _normalizar_desc(padrao.replace("%", "")))),
                None,
            )
            chave = f"{l['mes_referencia']:02d}/{l['ano_referencia']}"
            resumo = por_mes.setdefault(chave, {"linhas": 0, "total_centavos": 0})
            resumo["linhas"] += 1
            resumo["total_centavos"] += _centavos(l["valor"])
            if not preview:
                _criar_lancamento_da_linha(cur, l, categoria, session.get("user"))
                criadas += 1

        for resumo in por_mes.values():
            resumo["total"] = _reais(resumo.pop("total_centavos"))

        # commita tambem quando so houve correcao de data: antes o UPDATE ficava
        # sem commit quando nenhuma linha nova era criada, e a correcao se perdia
        # no fechamento da conexao - em silencio, com a rota devolvendo sucesso.
        if not preview and (criadas or datas_corrigidas):
            if criadas:
                aplicar_regras(cur)
            conn.commit()
            registrar_auditoria(
                "alteracao", "relatorios.criar_cobrancas_sem_pluggy", sucesso=True,
                detalhes={"criadas": criadas, "datas_corrigidas": datas_corrigidas,
                          "por_mes": por_mes},
            )
        return jsonify({
            "ok": True, "preview": preview,
            "linhas": len(linhas), "criadas": criadas, "datas_corrigidas": datas_corrigidas,
            "total": _reais(sum(_centavos(l["valor"]) for l in linhas)),
            "por_mes": por_mes,
        })
    except Exception as exc:
        conn.rollback()
        return jsonify({"ok": False, "erro": f"Não consegui criar: {exc}"}), 400
    finally:
        cur.close()
        conn.close()


@bp.route("/api/faturas/sincronizar-parcelas", methods=["GET", "POST"])
@requer("conciliacao_editar")
def sincronizar_parcelas():
    """Aplica o regime de caixa nos parcelamentos agregados (ver
    _sincronizar_parcelas_de_agregado). Muda numero do DRE de proposito:
    tira a compra cheia do mes da compra e coloca cada parcela no mes em que
    a fatura cobrou."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        fonte = request.args if request.method == "GET" else request.form
        try:
            fatura_id = int(fonte.get("fatura_id") or 0)
        except (TypeError, ValueError):
            fatura_id = 0
        if not fatura_id:
            return jsonify({"ok": False, "erro": "Informe a fatura que será revisada."}), 400
        cur.execute("SELECT account_id FROM cartao.fatura_importada WHERE id=%s;", (fatura_id,))
        fatura = cur.fetchone()
        if not fatura:
            return jsonify({"ok": False, "erro": "Fatura não encontrada."}), 404
        resumo = _sincronizar_parcelas_de_agregado(
            cur, session.get("user"), account_id=str(fatura["account_id"]),
            preview=request.method == "GET",
        )
        if request.method == "GET":
            conn.rollback()
            return jsonify({"ok": True, **resumo})
        conn.commit()
        registrar_auditoria(
            "alteracao", "relatorios.sincronizar_parcelas", sucesso=True, detalhes=resumo,
        )
        retorno = (request.form.get("retorno") or "").strip()
        if retorno.startswith("/lancamentos/fatura"):
            return redirect(retorno)
        return jsonify({"ok": True, **resumo})
    except Exception as exc:
        conn.rollback()
        return jsonify({"ok": False, "erro": f"Não consegui sincronizar: {exc}"}), 400
    finally:
        cur.close()
        conn.close()


@bp.route("/api/fatura/<int:fatura_id>/vincular-automatico", methods=["POST"])
@requer("conciliacao_editar")
def vincular_automatico_fatura(fatura_id):
    """Roda o casamento automatico nas linhas que ainda nao tem vinculo.
    Nao mexe em vinculo existente - nem no manual, nem no automatico ja
    gravado antes."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT id, account_id, ano_referencia, mes_referencia, periodo_inicio, periodo_fim "
            "FROM cartao.fatura_importada WHERE id = %s;",
            (fatura_id,),
        )
        fatura_row = cur.fetchone()
        if not fatura_row:
            return jsonify({"ok": False, "erro": "Fatura não encontrada."}), 404
        # refazer=1 apaga os vinculos AUTOMATICOS desta fatura antes de
        # recalcular (usado quando a regra de casamento muda). Vinculo manual
        # e' decisao humana e nunca e' apagado aqui.
        removidos = 0
        if (request.get_json(silent=True) or {}).get("refazer"):
            cur.execute(
                "DELETE FROM cartao.fatura_vinculo v USING cartao.fatura_linha fl "
                "WHERE fl.id = v.fatura_linha_id AND fl.fatura_id = %s AND v.origem = 'automatico';",
                (fatura_row["id"],),
            )
            removidos = cur.rowcount or 0
        criados = _vincular_automatico(cur, fatura_row, session.get("user"))
        conn.commit()
        if criados or removidos:
            registrar_mudanca_auditoria(
                f"Vínculos automáticos recalculados (fatura_id={fatura_id})",
                {"removidos": removidos}, {"criados": criados},
            )
        return jsonify({"ok": True, "criados": criados, "removidos": removidos})
    except Exception as exc:
        conn.rollback()
        return jsonify({"ok": False, "erro": f"Não consegui vincular: {exc}"}), 400
    finally:
        cur.close()
        conn.close()


@bp.route("/api/fatura-linha/<int:linha_id>/vincular", methods=["POST"])
@requer("conciliacao_editar")
def vincular_linha_fatura(linha_id):
    """Vincula manualmente um lancamento do Pluggy a uma linha da fatura.
    Vinculo manual e' decisao humana: o automatico nunca sobrescreve."""
    data = request.get_json(force=True) or {}
    transacao_id = (data.get("transacao_id") or "").strip()
    if not transacao_id:
        return jsonify({"ok": False, "erro": "Informe o lançamento."}), 400

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT fl.id, fl.descricao, fl.valor, fi.account_id "
            "FROM cartao.fatura_linha fl JOIN cartao.fatura_importada fi ON fi.id = fl.fatura_id "
            "WHERE fl.id = %s;",
            (linha_id,),
        )
        linha = cur.fetchone()
        if not linha:
            return jsonify({"ok": False, "erro": "Linha da fatura não encontrada."}), 404
        cur.execute(
            "SELECT descricao, account_id FROM cartao.transacao WHERE transacao_id = %s;",
            (transacao_id,),
        )
        transacao = cur.fetchone()
        if not transacao:
            return jsonify({"ok": False, "erro": "Lançamento não encontrado."}), 404
        if str(transacao["account_id"]) != str(linha["account_id"]):
            return jsonify({"ok": False, "erro": "Esse lançamento é de outra conta."}), 400

        cur.execute(
            "INSERT INTO cartao.fatura_vinculo (fatura_linha_id, transacao_id, origem, criado_por) "
            "VALUES (%s, %s, 'manual', %s) "
            "ON CONFLICT (fatura_linha_id, transacao_id) DO UPDATE SET origem='manual', "
            "criado_por=EXCLUDED.criado_por, criado_em=now();",
            (linha_id, transacao_id, session.get("user")),
        )
        conn.commit()
        registrar_mudanca_auditoria(
            f"Vínculo manual com a fatura (linha {linha_id})", None,
            {"transacao_id": transacao_id, "lancamento": transacao["descricao"],
             "linha_fatura": linha["descricao"], "valor_linha": float(linha["valor"])},
        )
        return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback()
        return jsonify({"ok": False, "erro": f"Não consegui vincular: {exc}"}), 400
    finally:
        cur.close()
        conn.close()


@bp.route("/api/fatura-linha/<int:linha_id>/desvincular", methods=["POST"])
@requer("conciliacao_editar")
def desvincular_linha_fatura(linha_id):
    """Desfaz um vinculo (automatico ou manual) entre linha da fatura e
    lancamento. Nao apaga nem altera o lancamento em si."""
    data = request.get_json(force=True) or {}
    transacao_id = (data.get("transacao_id") or "").strip()
    if not transacao_id:
        return jsonify({"ok": False, "erro": "Informe o lançamento."}), 400

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "DELETE FROM cartao.fatura_vinculo WHERE fatura_linha_id = %s AND transacao_id = %s "
            "RETURNING origem;",
            (linha_id, transacao_id),
        )
        removido = cur.fetchone()
        conn.commit()
        if removido:
            registrar_mudanca_auditoria(
                f"Vínculo com a fatura removido (linha {linha_id})",
                {"transacao_id": transacao_id, "origem": removido["origem"]}, None,
            )
        return jsonify({"ok": True, "removido": bool(removido)})
    except Exception as exc:
        conn.rollback()
        return jsonify({"ok": False, "erro": f"Não consegui desvincular: {exc}"}), 400
    finally:
        cur.close()
        conn.close()


@bp.route("/api/fatura-linha/marcar-conferida-repeticao", methods=["POST"])
@requer("conciliacao_editar")
def marcar_repeticao_conferida():
    """Marca (ou desmarca) como revisado um grupo de cobranca repetida na
    fatura (ver _repetidas_na_fatura). E' so o registro de quem ja olhou
    aquele grupo e quando - nao altera nenhuma transacao nem valor."""
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    conferida = bool(data.get("conferida"))
    if not ids or not all(isinstance(i, int) for i in ids):
        return jsonify({"ok": False, "erro": "Lista de linhas inválida."}), 400

    conn = get_conn()
    cur = conn.cursor()
    try:
        if conferida:
            cur.execute(
                "UPDATE cartao.fatura_linha SET conferida_repeticao = true, "
                "conferida_repeticao_por = %s, conferida_repeticao_em = now() "
                "WHERE id = ANY(%s);",
                (session.get("user"), ids),
            )
        else:
            cur.execute(
                "UPDATE cartao.fatura_linha SET conferida_repeticao = false, "
                "conferida_repeticao_por = NULL, conferida_repeticao_em = NULL "
                "WHERE id = ANY(%s);",
                (ids,),
            )
        conn.commit()
        registrar_mudanca_auditoria("Cobrança repetida na fatura conferida", not conferida, {
            "fatura_linha_ids": ids, "conferida": conferida,
        })
        return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback()
        return jsonify({"ok": False, "erro": f"Não consegui salvar: {exc}"}), 400
    finally:
        cur.close()
        conn.close()


@bp.route("/relatorios/fatura/<int:fatura_id>/pdf")
@requer("relatorios")
def baixar_fatura_pdf(fatura_id):
    """Devolve o PDF original guardado da fatura (ver /configuracoes/faturas-pdf
    em cadastros.py). Nao guardamos em disco - o app roda em container sem
    volume persistente confirmado no Coolify, entao o arquivo fica como bytea
    no proprio Postgres."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT arquivo_nome, pdf_arquivo FROM cartao.fatura_importada WHERE id=%s;",
        (fatura_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row["pdf_arquivo"]:
        return "PDF não encontrado (pode ter sido apagado).", 404
    # nome vem do arquivo enviado pelo usuario - tira aspas/controle antes de
    # colocar no header, pra nao dar pra escapar do filename="..."
    nome = re.sub(r'[\r\n"]', "", row["arquivo_nome"] or "") or f"fatura-{fatura_id}.pdf"
    return Response(
        bytes(row["pdf_arquivo"]),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nome}"'},
    )


@bp.route("/investimentos")
@requer("relatorios")
def investimentos_view():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute(
            "SELECT investimento_id, nome, tipo, subtipo, instituicao, saldo, valor_bruto, "
            "valor_aplicado, impostos, taxa, tipo_taxa, data_posicao, data_vencimento, "
            "data_aplicacao, status "
            "FROM cartao.investimento ORDER BY saldo DESC NULLS LAST;"
        )
        posicoes = cur.fetchall()
    except Exception:
        conn.rollback()
        posicoes = None

    historico = []
    if posicoes is not None:
        # Usa a ultima posicao de CADA investimento em cada mes. Usar uma unica
        # data maxima do mes omitiria produtos que nao tivessem retrato naquele
        # mesmo dia e reduziria o patrimonio exibido.
        cur.execute(
            "SELECT mes, MAX(data) AS ultima, SUM(saldo) AS saldo, SUM(valor_aplicado) AS aplicado "
            "FROM ("
            "  SELECT DISTINCT ON (investimento_id, to_char(data, 'YYYY-MM')) "
            "    investimento_id, to_char(data, 'YYYY-MM') AS mes, data, saldo, valor_aplicado "
            "  FROM cartao.investimento_saldo "
            "  ORDER BY investimento_id, to_char(data, 'YYYY-MM'), data DESC"
            ") ultimas GROUP BY mes ORDER BY mes;"
        )
        historico = cur.fetchall()

    cur.close()
    conn.close()

    contexto = {
        "titulo": "Investimentos",
        "topbar": topbar_html("Investimentos", "investimentos"),
    }
    if posicoes is None:
        return render_template("investimentos.html", sincronizado=False, **contexto)

    def _dt(v):
        return v.strftime("%d/%m/%Y") if v else "-"

    brutos = [p for p in posicoes if float(p["saldo"] or 0) > 0]
    ativos = []
    for p in brutos:
        aplicado = float(p["valor_aplicado"] or 0)
        bruto = float(p["valor_bruto"] or 0)
        rend = bruto - aplicado
        taxa = ""
        if p["taxa"] and float(p["taxa"]) > 0:
            taxa = f'{float(p["taxa"]):g}% {p["tipo_taxa"] or ""}'.strip()
        detalhe = p["subtipo"] or p["tipo"] or ""
        if taxa:
            detalhe = f"{detalhe} · {taxa}" if detalhe else taxa
        ativos.append({
            "nome": (p["nome"] or "-")[:46],
            "detalhe": detalhe,
            "aplicado": aplicado,
            "bruto": bruto,
            "rend": rend,
            "pct": (rend / aplicado * 100) if aplicado else 0,
            "impostos": float(p["impostos"] or 0),
            "saldo": float(p["saldo"] or 0),
            "vencimento": _dt(p["data_vencimento"]),
        })

    aplicado_total = sum(a["aplicado"] for a in ativos)
    bruto_total = sum(a["bruto"] for a in ativos)
    rendimento_bruto = bruto_total - aplicado_total

    hist = _montar_historico_investimentos(historico)

    return render_template(
        "investimentos.html",
        sincronizado=True,
        ativos=ativos,
        encerrados=len(posicoes) - len(ativos),
        saldo_total=sum(a["saldo"] for a in ativos),
        aplicado_total=aplicado_total,
        rendimento_bruto=rendimento_bruto,
        rend_pct=(rendimento_bruto / aplicado_total * 100) if aplicado_total else 0,
        ir_total=sum(a["impostos"] for a in ativos),
        historico=hist,
        **contexto,
    )


@bp.route("/api/categoria-lancamentos")
@requer("cadastros")
def api_categoria_lancamentos():
    """Lista os lancamentos de uma categoria - usado pelo botao 'protegida' em /categorias,
    pra mostrar o que esta impedindo a remocao sem precisar ir pra tela de Lancamentos."""
    categoria = request.args.get("categoria") or ""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT t.transacao_id, t.data_transacao, t.descricao, "
        "COALESCE(t.valor_brl, t.valor_original) AS valor "
        f"FROM {FINANCEIRO_TABELA} t WHERE t.categoria = %s ORDER BY t.data_transacao DESC LIMIT 300;",
        (categoria,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([
        {
            # o id vai junto para o modal poder abrir os detalhes do lancamento
            "transacao_id": str(r["transacao_id"]),
            "data": data_hora_local(r["data_transacao"]).strftime("%d/%m/%Y") if r["data_transacao"] else "-",
            "descricao": r["descricao"] or "-",
            "valor": float(r["valor"]) if r["valor"] is not None else 0,
        }
        for r in rows
    ])


@bp.route("/api/dimensao-lancamentos")
@requer("cadastros")
def api_dimensao_lancamentos():
    """Lista os lancamentos vinculados a um valor (ou a todos os valores de uma
    dimensao) - usado pelo botao 'protegida' em /dimensoes, mesma logica do
    /api/categoria-lancamentos."""
    valor_id = request.args.get("valor_id")
    dimensao_id = request.args.get("dimensao_id")
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if valor_id:
        cur.execute(
            "SELECT lf.transacao_id, lf.data_transacao, lf.descricao, "
            "COALESCE(lf.valor_brl, lf.valor_original) AS valor "
            "FROM cartao.lancamento_financeiro lf "
            "JOIN cartao.lancamento_financeiro_dimensao ld ON ld.linha_id = lf.linha_id "
            "WHERE ld.valor_id = %s ORDER BY lf.data_transacao DESC LIMIT 300;",
            (valor_id,),
        )
    else:
        cur.execute(
            "SELECT lf.transacao_id, lf.data_transacao, lf.descricao, "
            "COALESCE(lf.valor_brl, lf.valor_original) AS valor "
            "FROM cartao.lancamento_financeiro lf "
            "JOIN cartao.lancamento_financeiro_dimensao ld ON ld.linha_id = lf.linha_id "
            "WHERE ld.dimensao_id = %s ORDER BY lf.data_transacao DESC LIMIT 300;",
            (dimensao_id,),
        )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([
        {
            "transacao_id": str(r["transacao_id"]),
            "data": data_hora_local(r["data_transacao"]).strftime("%d/%m/%Y") if r["data_transacao"] else "-",
            "descricao": r["descricao"] or "-",
            "valor": float(r["valor"]) if r["valor"] is not None else 0,
        }
        for r in rows
    ])


@bp.route("/api/classificacao/consenso-preview")
@requer("cadastros")
def api_consenso_preview():
    """Levanta, SEM GRAVAR NADA, o que o consenso dos OK ainda completaria.

    Existe porque nao ha ambiente de staging: e a unica forma de olhar o dado
    real antes de decidir uma alteracao em lote. Roda os dois eixos - por
    lojista (o de sempre) e por categoria (novo, para quem so tem categoria) -
    e devolve quantos lancamentos VAZIOS cada um preencheria, por campo.

    Nunca escreve, nunca toca em `conferida`, nunca abre transacao de escrita.
    """
    conta = request.args.get("account_id") or "b6243125-dca2-42b2-8c20-0825782c6d8d"
    anos = [int(a) for a in (request.args.get("anos") or "2025,2026").split(",") if a.strip()]
    minimo_cat = max(2, int(request.args.get("minimo_categoria") or 3))
    recusados = {"POUSADA FOGO*RESE", "ESTACAO"}
    # Mesmos recusados da migracao 48: lazer local nao e viagem e seguro nem
    # sempre e de veiculo. A previa precisa mostrar o que de fato seria gravado.
    recusados_cat = {"Leisure", "Insurance"}
    if request.args.get("sem_recusa") == "1":
        recusados_cat = set()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,nome FROM cartao.dimensao WHERE lower(nome) IN "
        "('responsável','responsavel','projeto','portfólio','portfolio');"
    )
    dims = {chave_alfa(n): i for i, n in cur.fetchall()}
    cur.execute("SELECT id,nome FROM cartao.dimensao WHERE id = ANY(%s);", (list(dims.values()),))
    nomes_dim = dict(cur.fetchall())
    cur.execute("SELECT id,nome FROM cartao.dimensao_valor;")
    nomes_valor = dict(cur.fetchall())
    # Natureza junto: centro de custo so faz sentido em categoria de DESPESA
    # (secao 4.1). Sem isso, pagamento de fatura entra na conta de pendencia
    # como se fosse gasto a classificar, e o numero mente.
    cur.execute("SELECT categoria,natureza FROM cartao.categoria_natureza;")
    naturezas = dict(cur.fetchall())

    cur.execute(
        "SELECT t.transacao_id::text, t.descricao, t.categoria, t.conferida, "
        "COALESCE((SELECT jsonb_object_agg(td.dimensao_id,td.valor_id) "
        " FROM cartao.transacao_dimensao td WHERE td.transacao_id=t.transacao_id::text),"
        " '{}'::jsonb) "
        "FROM cartao.transacao t WHERE t.account_id=%s "
        "AND COALESCE(t.duplicada,false)=false "
        "AND COALESCE(t.somente_conciliacao,false)=false "
        "AND t.substituido_por IS NULL "
        f"AND EXTRACT(YEAR FROM {DATA_LOCAL_SQL}) = ANY(%s);",
        (conta, anos),
    )
    linhas = cur.fetchall()
    cur.close()
    conn.close()

    dim_projeto = dims.get("projeto")
    mapa, por_loja = _consenso_por_lojista(
        linhas, dim_projeto=dim_projeto, nomes_valor=nomes_valor, recusados=recusados)
    por_cat = _consenso_por_categoria(
        [(l[0], l[2], l[3], l[4]) for l in linhas],
        dim_projeto=dim_projeto, nomes_valor=nomes_valor,
        recusados=recusados_cat, minimo=minimo_cat)

    def rotulo(campo):
        return "categoria" if campo == "cat" else nomes_dim.get(campo, str(campo))

    # Lojista cujos proprios OK discordam sobre a categoria. O consenso descarta
    # esse caso em silencio (nao ha unanimidade), entao ele nunca aparecia -
    # e um deles esta errado, ou o lojista e mesmo ambiguo. Decisao do usuario.
    votos_cat = {}
    for _tid, descricao, categoria, conf, _ds in linhas:
        if not conf or not categoria:
            continue
        alvo = votos_cat.setdefault(mapa[_loja_v45(descricao)], {})
        alvo[categoria] = alvo.get(categoria, 0) + 1
    divergentes = {
        loja: dict(sorted(cats.items(), key=lambda x: -x[1]))
        for loja, cats in sorted(votos_cat.items()) if len(cats) > 1
    }

    ganho = {"lojista": {}, "categoria": {}}
    alvos = {"lojista": set(), "categoria": set()}
    incompletos = 0
    # O que sobra, agrupado por lojista e pelo conjunto exato de campos que
    # falta: e essa lista, e nao o total, que diz onde vale decidir uma regra.
    sobra = {}
    for tid, descricao, categoria, _conf, ds in linhas:
        # Mesma regra das telas: natureza neutra nao exige dimensao (secao 4.1).
        # Sem isto este levantamento contava pagamento de fatura como pendencia
        # e discordava de /lancamentos - o erro da secao 6.5 n.10.
        exige = exige_dimensoes(naturezas.get(categoria)) if categoria else True
        faltando = [c for c in dims.values() if exige and _dimensao_vazia(ds, c)]
        if not categoria or faltando:
            incompletos += 1
            rotulos = ([] if categoria else ["categoria"]) + sorted(
                rotulo(c) for c in faltando)
            nat = naturezas.get(categoria) or ("(sem categoria)" if not categoria
                                               else "(sem natureza)")
            chave_sobra = (f"{mapa[_loja_v45(descricao)]} · {categoria or '-'} [{nat}]"
                           f" · falta {', '.join(rotulos)}")
            sobra[chave_sobra] = sobra.get(chave_sobra, 0) + 1
        escolha_loja = por_loja.get(mapa[_loja_v45(descricao)], {})
        escolha_cat = por_cat.get(categoria, {}) if categoria else {}
        for eixo, escolha in (("lojista", escolha_loja), ("categoria", escolha_cat)):
            for campo, valor in escolha.items():
                vazio = (not categoria) if campo == "cat" else _dimensao_vazia(ds, campo)
                if not vazio:
                    continue
                if eixo == "categoria" and campo == "cat":
                    continue
                chave = f"{rotulo(campo)} = {nomes_valor.get(valor, valor)}"
                ganho[eixo][chave] = ganho[eixo].get(chave, 0) + 1
                alvos[eixo].add(tid)

    detalhe_cat = {
        cat: {rotulo(c): nomes_valor.get(v, v) for c, v in escolha.items()}
        for cat, escolha in sorted(por_cat.items())
    }
    return jsonify({
        "conta": conta, "anos": anos, "minimo_categoria": minimo_cat,
        "sobra_por_lojista": dict(sorted(sobra.items(), key=lambda x: -x[1])[:60]),
        "categoria_divergente_entre_oks": divergentes,
        "lancamentos_avaliados": len(linhas),
        "classificacao_incompleta": incompletos,
        "lojistas_com_consenso": len(por_loja),
        "categorias_com_consenso": len(por_cat),
        "consenso_por_categoria": detalhe_cat,
        "preencheria_por_lojista": dict(sorted(ganho["lojista"].items(), key=lambda x: -x[1])),
        "preencheria_por_categoria": dict(sorted(ganho["categoria"].items(), key=lambda x: -x[1])),
        "lancamentos_alcancados_lojista": len(alvos["lojista"]),
        "lancamentos_alcancados_categoria": len(alvos["categoria"]),
        "lancamentos_alcancados_total": len(alvos["lojista"] | alvos["categoria"]),
    })


@bp.route("/api/classificacao/reaplicar-consenso", methods=["POST"])
@requer("cadastros")
def api_reaplicar_consenso():
    """Reaplica o consenso dos OK aos campos VAZIOS. Repetivel de proposito.

    O consenso cresce a cada OK que o usuario assina, entao isto e uma acao de
    tela, nao uma migracao unica. `preview: true` e estritamente somente
    leitura - nao abre escrita e faz rollback ao final, por seguranca.

    Nunca marca nem desmarca `conferida` (secao 1.2) e nunca escreve em
    `observacao` (secao 7.3).
    """
    dados = request.get_json(silent=True) or {}
    preview = bool(dados.get("preview"))
    conta = dados.get("account_id") or CONTA_UNICRED
    conn = get_conn()
    cur = conn.cursor()
    try:
        res = aplicar_consenso_classificacao(cur, account_id=conta, preview=preview)
        if preview:
            conn.rollback()
        else:
            conn.commit()
            registrar_auditoria(
                "alteracao", "Reaplica o consenso de classificacao",
                detalhes={"account_id": conta, "categorias": res["categorias"],
                          "dimensoes": res["dimensoes"], "tocados_com_ok": res["com_ok"],
                          "tocados_sem_ok": res["sem_ok"]},
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
    return jsonify({"ok": True, "preview": preview, **res})


@bp.route("/api/fatura/vinculos-suspeitos")
@requer("cadastros")
def api_vinculos_suspeitos():
    """Vinculos em que a linha da fatura e a transacao NAO tem um unico termo
    do estabelecimento em comum. Somente leitura: levanta, nao desfaz nada.

    E a armadilha 17 da secao 6.5 aplicada para tras: coincidencia de valor
    nunca e prova de familia. O caso que a revelou de novo: TOTAL SPORTES
    10x R$ 44,99 (= R$ 449,90) grudado no agregado ORAL UNIC ODONTOL de
    R$ 450,00 - R$ 0,10 de diferenca, dentro da tolerancia de R$ 1,00.

    Nao desfaz porque desfazer vinculo mexe no que entra no DRE (o agregado
    volta ou sai do resultado) e isso e decisao do usuario.
    """
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT fv.id AS vinculo_id, fv.origem, fi.mes_referencia, "
        "fl.descricao AS linha_descricao, "
        "COALESCE(fl.descricao_base, fl.descricao) AS linha_base, "
        "fl.valor AS linha_valor, fl.parcela_total, "
        "t.transacao_id::text AS transacao_id, t.descricao AS transacao_descricao, "
        "COALESCE(t.valor_brl,t.valor_original) AS transacao_valor, "
        "COALESCE(t.somente_conciliacao,false) AS agregado, t.conferida "
        "FROM cartao.fatura_vinculo fv "
        "JOIN cartao.fatura_linha fl ON fl.id=fv.fatura_linha_id "
        "JOIN cartao.fatura_importada fi ON fi.id=fl.fatura_id "
        "JOIN cartao.transacao t ON t.transacao_id=fv.transacao_id "
        "ORDER BY fi.mes_referencia;"
    )
    linhas = cur.fetchall()
    cur.close()
    conn.close()

    suspeitos = []
    for r in linhas:
        tokens_linha = _tokens_significativos(r["linha_base"])
        tokens_trans = _tokens_significativos(r["transacao_descricao"])
        # Sem token de nenhum lado nao da para afirmar nada: fica de fora para
        # nao gerar falso positivo em descricao curta ou toda generica.
        if not tokens_linha or not tokens_trans:
            continue
        if tokens_linha & tokens_trans:
            continue
        suspeitos.append({
            "vinculo_id": r["vinculo_id"], "origem": r["origem"],
            "fatura": str(r["mes_referencia"]),
            "linha": r["linha_descricao"],
            "linha_valor": float(r["linha_valor"] or 0),
            "parcela_total": r["parcela_total"],
            "transacao": r["transacao_descricao"],
            "transacao_valor": float(r["transacao_valor"] or 0),
            "transacao_id": r["transacao_id"],
            "agregado_fora_do_dre": bool(r["agregado"]),
            "conferida": bool(r["conferida"]),
        })
    return jsonify({
        "vinculos_avaliados": len(linhas),
        "suspeitos": len(suspeitos),
        "com_agregado_fora_do_dre": sum(1 for s in suspeitos if s["agregado_fora_do_dre"]),
        "detalhe": suspeitos[:200],
    })
