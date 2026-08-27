"""Relatorios, DRE e investimentos."""
import uuid
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
from flask import Blueprint, request, jsonify, render_template, session

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
    chave_alfa,
    chip_filter_html,
    data_hora_local,
    get_conn,
    intervalo_ano_local,
    levantar_pendencias,
    pode,
    registrar_auditoria,
    registrar_mudanca_auditoria,
    requer,
    topbar_html,
)

bp = Blueprint("relatorios", __name__)


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


def _conciliar_linhas(cur, account_id, linhas, fatura_linha_ids=None):
    """Casa as linhas de uma fatura (do parser ou ja salvas no banco) contra os
    lancamentos que o Pluggy trouxe naquela conta. fatura_linha_ids, quando
    informado, e um dict {id(linha) python: id da cartao.fatura_linha} para o
    template poder oferecer "criar lancamento" apontando pro registro certo."""
    fatura_linha_ids = fatura_linha_ids or {}
    datas = [l["data"] for l in linhas]
    cur.execute(
        f"SELECT t.transacao_id, t.data_transacao, t.descricao, t.parcela_total, "
        f"COALESCE(t.valor_brl, t.valor_original) AS valor, "
        f"({DATA_LOCAL_SQL})::date AS data_local "
        f"FROM cartao.transacao t WHERE t.account_id = %s "
        f"AND ({DATA_LOCAL_SQL})::date BETWEEN %s AND %s;",
        (account_id, min(datas), max(datas)),
    )
    candidatos = [dict(r) for r in cur.fetchall()]
    for c in candidatos:
        c["_usado"] = False
        c["_data_local"] = c["data_local"]

    # A fatura lista cada parcela mensal numa linha (ex: "Parc.9/12 R$129,00");
    # o Pluggy grava o parcelamento inteiro como UMA transacao so, no valor
    # cheio, na data da compra original. Sem agrupar por parcela_total, toda
    # compra parcelada bateria errado dos dois lados - nao por falha de
    # sincronizacao, so porque sao representacoes diferentes da mesma compra.
    grupos_parcela = {}
    avulsas = []
    for linha in linhas:
        if linha["parcela_total"]:
            chave = (linha["titular"], linha["descricao_base"].upper(), linha["parcela_total"])
            grupos_parcela.setdefault(chave, []).append(linha)
        else:
            avulsas.append(linha)

    batidos, sem_sistema = [], []

    for linha in avulsas:
        melhor, melhor_dist = None, None
        for c in candidatos:
            if c["_usado"] or round(float(c["valor"]), 2) != linha["valor"]:
                continue
            dist = abs((c["_data_local"] - linha["data"]).days)
            if dist > 3:
                continue
            if melhor is None or dist < melhor_dist:
                melhor, melhor_dist = c, dist
        if melhor:
            melhor["_usado"] = True
            batidos.append({**linha, "transacao_id": str(melhor["transacao_id"]),
                             "descricao_sistema": melhor["descricao"]})
        else:
            sem_sistema.append({**linha, "fatura_linha_id": fatura_linha_ids.get(id(linha))})

    for (titular, desc_norm, parcela_total), linhas_grupo in grupos_parcela.items():
        valor_mensal = sum(l["valor"] for l in linhas_grupo) / len(linhas_grupo)
        valor_esperado = round(valor_mensal * parcela_total, 2)
        # O Pluggy nem sempre preenche parcela_total no lancamento agregado
        # (esse cartao chega a mostrar "Parcela: A vista" num parcelamento
        # real) - por isso o casamento aqui e so pelo valor cheio esperado,
        # com o numero de parcelas e a descricao servindo apenas de desempate
        # quando ha mais de um candidato proximo do valor. Tolerancia de R$1
        # porque o valor da parcela impresso na fatura e arredondado por mes -
        # multiplicado pelo numero de parcelas pode nao bater centavo a
        # centavo com o valor cheio real (ex: fatura mostra parcela de
        # R$198,05 x12=R$2.376,60, o lancamento real e R$2.376,70).
        tolerancia = 1.00
        candidatos_valor = [
            c for c in candidatos
            if not c["_usado"] and abs(round(float(c["valor"]), 2) - valor_esperado) <= tolerancia
        ]
        melhor = None
        if len(candidatos_valor) == 1:
            melhor = candidatos_valor[0]
        elif len(candidatos_valor) > 1:
            com_parcela = [c for c in candidatos_valor if c["parcela_total"] == parcela_total]
            opcoes = com_parcela or candidatos_valor
            melhor = min(
                opcoes,
                key=lambda c: (
                    desc_norm.split()[0] not in (c["descricao"] or "").upper(),
                    abs(round(float(c["valor"]), 2) - valor_esperado),
                ),
            )
        if melhor:
            melhor["_usado"] = True
            for l in linhas_grupo:
                batidos.append({**l, "transacao_id": str(melhor["transacao_id"]),
                                "descricao_sistema": melhor["descricao"],
                                "valor_esperado_parcelamento": valor_esperado})
        else:
            for l in linhas_grupo:
                sem_sistema.append({**l, "valor_esperado_parcelamento": valor_esperado,
                                     "titular": titular, "fatura_linha_id": fatura_linha_ids.get(id(l))})

    # "sem_fatura" so faz sentido dentro do ciclo desta fatura - o intervalo de
    # busca acima e largo (cobre ate um ano, por causa de parcelas antigas que
    # ainda aparecem cobradas), mas listar TODA sobra desse intervalo encheria
    # a tela de lancamentos de faturas passadas que nunca deveriam bater com
    # esta mesmo.
    ciclo_inicio = max(datas) - timedelta(days=35)
    sem_fatura = [
        {"data": c["_data_local"], "descricao": c["descricao"], "valor": round(float(c["valor"]), 2),
         "transacao_id": str(c["transacao_id"])}
        for c in candidatos if not c["_usado"] and c["_data_local"] >= ciclo_inicio
    ]

    soma_fatura = round(sum(l["valor"] for l in linhas), 2)
    soma_batida = round(sum(l["valor"] for l in batidos), 2)
    return {
        "soma_fatura": soma_fatura,
        "batidos": sorted(batidos, key=lambda l: l["data"]),
        "sem_sistema": sorted(sem_sistema, key=lambda l: l["data"]),
        "sem_fatura": sorted(sem_fatura, key=lambda l: l["data"]),
        "fecha_100": not sem_sistema and not sem_fatura,
        "diferenca": round(soma_fatura - soma_batida, 2),
        "periodo_inicio": min(datas),
        "periodo_fim": max(datas),
        "repetidas_na_fatura": _repetidas_na_fatura(linhas),
    }


def _repetidas_na_fatura(linhas):
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
    """
    grupos = {}
    for l in linhas:
        if l["valor"] <= 0 or l["parcela_total"]:
            continue  # estorno/pagamento e parcelamento nao entram
        chave = (l["titular"], (l["descricao_base"] or l["descricao"]).upper(), l["valor"], l["data"])
        grupos.setdefault(chave, []).append(l)

    estornos = [l for l in linhas if l["valor"] < 0]
    usados = set()
    repetidas = []
    for (titular, desc, valor, data), grupo in grupos.items():
        if len(grupo) < 2:
            continue
        # procura um estorno de mesmo valor/titular que ainda nao foi atribuido
        estorno = None
        for e in estornos:
            if id(e) in usados or e["titular"] != titular:
                continue
            if abs(abs(e["valor"]) - valor) > 0.01 or e["data"] < data:
                continue
            estorno = e
            usados.add(id(e))
            break
        repetidas.append({
            "titular": titular, "descricao": grupo[0]["descricao"], "valor": valor,
            "data": data, "vezes": len(grupo),
            "total_cobrado": round(valor * len(grupo), 2),
            "estorno_data": estorno["data"] if estorno else None,
            "estorno_valor": estorno["valor"] if estorno else None,
        })
    return sorted(repetidas, key=lambda r: r["data"])


@bp.route("/relatorios/conciliar-fatura", methods=["GET", "POST"])
@requer("relatorios")
def conciliar_fatura():
    """Confere se os lancamentos que o Pluggy trouxe para um cartao de credito
    batem 100% com a fatura em PDF da Unicred. O Pluggy pode classificar
    diferente ou duplicar - mas o valor que sai da conta pagando a fatura tem
    que fechar com o que a operadora cobrou, e isso so a fatura oficial prova."""
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
            try:
                fatura = extrair_fatura(arquivo.stream)
            except FaturaInvalida as exc:
                erro = str(exc)
            except Exception as exc:
                erro = f"Não consegui ler esse PDF: {exc}"

            if not erro:
                # Guarda so os lancamentos extraidos (nao o PDF) - da historico
                # e permite reabrir a conciliacao depois sem reenviar o arquivo.
                # Reenviar a mesma fatura (conta+mes+ano) substitui as linhas.
                cur.execute(
                    "INSERT INTO cartao.fatura_importada "
                    "(account_id, mes_referencia, ano_referencia, total, cartao_final4, arquivo_nome, importado_por) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (account_id, mes_referencia, ano_referencia) DO UPDATE SET "
                    "total=EXCLUDED.total, cartao_final4=EXCLUDED.cartao_final4, "
                    "arquivo_nome=EXCLUDED.arquivo_nome, importado_por=EXCLUDED.importado_por, importado_em=now() "
                    "RETURNING id;",
                    (account_id, fatura["mes_referencia"], fatura["ano_referencia"], fatura["total"],
                     fatura["cartao_final4"], arquivo.filename, session.get("user")),
                )
                fatura_id = cur.fetchone()["id"]
                cur.execute("DELETE FROM cartao.fatura_linha WHERE fatura_id=%s;", (fatura_id,))
                for l in fatura["linhas"]:
                    cur.execute(
                        "INSERT INTO cartao.fatura_linha "
                        "(fatura_id, data, descricao, descricao_base, parcela_atual, parcela_total, valor, titular) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s);",
                        (fatura_id, l["data"], l["descricao"], l["descricao_base"],
                         l["parcela_atual"], l["parcela_total"], l["valor"], l["titular"]),
                    )
                conn.commit()
                registrar_auditoria(
                    "alteracao", "relatorios.conciliar_fatura_importar", sucesso=True,
                    detalhes={
                        "conta": account_id, "fatura_id": fatura_id,
                        "mes_referencia": fatura["mes_referencia"], "ano_referencia": fatura["ano_referencia"],
                        "linhas": len(fatura["linhas"]),
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
            cur.execute(
                "SELECT id, data, descricao, descricao_base, parcela_atual, parcela_total, valor, titular, "
                "transacao_id_criado FROM cartao.fatura_linha WHERE fatura_id=%s ORDER BY data;",
                (fatura_id,),
            )
            linhas_db = [dict(r) for r in cur.fetchall()]
            linhas = [{k: v for k, v in l.items() if k not in ("id", "transacao_id_criado")} for l in linhas_db]
            ids_por_linha = {id(l): db["id"] for l, db in zip(linhas, linhas_db) if not db["transacao_id_criado"]}
            for l, db in zip(linhas, linhas_db):
                l["valor"] = float(l["valor"])
                l["_ja_criado"] = bool(db["transacao_id_criado"])

            fatura_meta = {
                "id": fatura_id, "mes_referencia": fatura_row["mes_referencia"],
                "ano_referencia": fatura_row["ano_referencia"], "total": float(fatura_row["total"]),
                "cartao_final4": fatura_row["cartao_final4"], "arquivo_nome": fatura_row["arquivo_nome"],
                "importado_em": fatura_row["importado_em"],
            }
            resultado = _conciliar_linhas(cur, account_id, linhas, ids_por_linha)
            resultado["fatura"] = fatura_meta
            # linhas ja resolvidas (usuario ja criou o lancamento) saem da lista
            # de pendencias, mas continuam contando no total como "cobertas"
            ja_criadas = [l for l, db in zip(linhas, linhas_db) if db["transacao_id_criado"]]
            if ja_criadas:
                resultado["sem_sistema"] = [
                    l for l in resultado["sem_sistema"] if not l.get("_ja_criado")
                ]
                resultado["diferenca"] = round(
                    resultado["diferenca"] - sum(l["valor"] for l in ja_criadas), 2
                )
                if not resultado["sem_sistema"] and not resultado["sem_fatura"]:
                    resultado["fecha_100"] = True

    # historico de faturas ja importadas, pra reabrir sem reenviar o PDF
    cur.execute(
        "SELECT f.id, f.account_id, f.mes_referencia, f.ano_referencia, f.total, f.importado_em "
        "FROM cartao.fatura_importada f ORDER BY f.ano_referencia DESC, f.mes_referencia DESC, f.importado_em DESC;"
    )
    historico = []
    for r in cur.fetchall():
        conta = contas_by_id.get(str(r["account_id"]))
        historico.append({**r, "conta_label": conta["label_curto"] if conta else "(conta removida)"})

    cur.close()
    conn.close()
    return render_template(
        "conciliar_fatura.html",
        titulo="Conciliar fatura",
        topbar=topbar_html("Conciliar fatura", "conciliar-fatura"),
        contas_credito=contas_credito,
        categorias=categorias_template,
        account_id=account_id,
        erro=erro,
        resultado=resultado,
        historico=historico,
        fatura_id=fatura_id,
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
            "observacao, criado_em, atualizado_em, sincronizado_em, primeiro_sincronizado_em"
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
