"""Tela de Lancamentos e a API que ela usa."""
import uuid
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode

import psycopg2
import psycopg2.extras
from flask import Blueprint, request, session, jsonify, render_template, redirect

from core import (
    URL_LANCAMENTOS,
    URL_RESUMIDA,
    valor_pt,
    EXIGE_DIMENSOES_SQL,
    exige_dimensoes,
    CATEGORIAS_EXTRA,
    CATEGORIAS_OCULTAS,
    CATEGORIA_PT,
    CATEGORIA_PT_DB,
    CONTA_MANUAL_ID,
    DATA_LOCAL_SQL,
    FINANCEIRO_TABELA,
    JOIN_NATUREZA,
    NATUREZAS,
    NATUREZA_SQL,
    VAL_DESPESA,
    aplicar_regras,
    vincular_pendentes_confirmados,
    carregar_origens,
    chip_origem_html,
    rotulo_valor_dimensao,
    cat_pt_puro,
    cor_banco,
    calcular_totais_dre_fatura,
    chave_alfa,
    chip_filter_html,
    data_hora_local,
    FUSO_LOCAL,
    fechar_recursos_banco,
    get_conn,
    intervalo_ano_local,
    intervalo_mes_local,
    json_script,
    pode,
    propagar_classificacao_familia_parcelas,
    registrar_auditoria,
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
def raiz():
    """A raiz leva a tela de Lancamentos do sistema, que e a Detalhada.

    Enquanto a Resumida morava aqui, quem abria o endereco do sistema - favorito,
    historico, digitar o dominio - caia nela, e nao adiantava o menu, a marca e o
    login apontarem para a Detalhada. A query e preservada porque mes, periodo,
    origem e status tem os MESMOS nomes nos dois lados: um favorito antigo
    continua abrindo o mesmo recorte, so que na tela certa.

    Sem `@requer` de proposito: aqui nao se le dado nenhum, so se redireciona -
    quem cobra a permissao e o destino.
    """
    query = request.query_string.decode()
    return redirect(URL_LANCAMENTOS + ("?" + query if query else ""))


@bp.route(URL_RESUMIDA)
def resumida_removida():
    """A Resumida saiu em 10/09/2026 (decisao do usuario): a Detalhada passou a
    ter tudo o que ela tinha, e duas telas para o mesmo dado divergiam na
    primeira regra nova.

    O endereco continua respondendo, redirecionando com a query: favorito e
    historico antigos abrem o mesmo recorte na tela que existe. Os nomes dos
    parametros sao os mesmos dos dois lados.
    """
    query = request.query_string.decode()
    return redirect(URL_LANCAMENTOS + ("?" + query if query else ""))


# Ponto unico: o CARD "Classificacao" e o FILTRO "Pendentes de classificacao"
# tem que contar exatamente a mesma coisa. Definidos separados, divergem na
# primeira regra nova - e um card que promete N linhas e entrega outra coisa e
# pior que card nenhum. A condicao respeita a natureza (secao 4.1): lancamento
# neutro so precisa de categoria.
def _pct(parte, total):
    """Percentual para a barra de progresso do card. Sem total, a barra fica
    cheia: "0 de 0 pendentes" e um estado completo, nao um estado vazio."""
    if not total:
        return 100.0
    return round(parte / total * 100, 1)


# Num lancamento RATEADO a classificacao mora nas partes, nao no pai: o pai nao
# tem categoria propria nem linha em `transacao_dimensao`. Contar por ele daria
# TODO rateado como pendente, inclusive os completos - por isso o CASE.
_PENDENTE_SIMPLES = (
    "(t.categoria IS NULL OR t.categoria = '' OR EXISTS ("
    "  SELECT 1 FROM cartao.dimensao d"
    "  LEFT JOIN cartao.transacao_dimensao td"
    # `transacao_dimensao.transacao_id` e TEXT, `transacao.transacao_id` e UUID:
    # o Postgres nao tem operador `uuid = text` e a consulta inteira quebra
    # (secao 10.4 n.6). Todo o resto do codigo ja castava; so este destoava.
    "    ON td.dimensao_id = d.id AND td.transacao_id = t.transacao_id::text"
    "  WHERE d.obrigatoria = true AND td.valor_id IS NULL AND " + EXIGE_DIMENSOES_SQL +
    "))"
)

_PENDENTE_RATEADO = (
    "(EXISTS ("
    "  SELECT 1 FROM cartao.transacao_rateio rx"
    "  WHERE rx.transacao_id = t.transacao_id"
    "    AND (rx.categoria IS NULL OR rx.categoria = '')"
    ") OR EXISTS ("
    "  SELECT 1 FROM cartao.transacao_rateio rx"
    "  CROSS JOIN cartao.dimensao dx"
    "  LEFT JOIN cartao.transacao_rateio_dimensao rdx"
    "    ON rdx.rateio_id = rx.id AND rdx.dimensao_id = dx.id"
    "  WHERE rx.transacao_id = t.transacao_id"
    "    AND dx.obrigatoria = true AND rdx.valor_id IS NULL AND " + EXIGE_DIMENSOES_SQL +
    "))"
)

# Registro que ja esta fora do resultado nunca vai ter classificacao completa e
# nao e trabalho pendente (secao 10.4 n.11).
PENDENTE_CLASSIFICACAO_SQL = (
    "(t.substituido_por IS NULL AND NOT COALESCE(t.somente_conciliacao, false) "
    "AND COALESCE(t.duplicada, false) = false AND CASE WHEN EXISTS ("
    "  SELECT 1 FROM cartao.transacao_rateio rx WHERE rx.transacao_id = t.transacao_id"
    ") THEN " + _PENDENTE_RATEADO + " ELSE " + _PENDENTE_SIMPLES + " END)"
)


# Os status que a lista de lancamentos aceita, e o WHERE de cada um. Ponto
# unico de proposito: as duas telas filtram a MESMA coisa, e um filtro
# reescrito na segunda tela divergiria na primeira regra nova - foi assim que
# nasceram os 57 falsos pendentes da secao 6.5 n.10.
STATUS_LANCAMENTO = (
    "todas", "pendente", "conferida", "pendente_banco", "duplicidade",
    "fora_resultado", "somente_conciliacao", "substituido",
    "rateio_incompleto", "pendente_classificacao", "receita", "despesa",
)


def where_status_lancamento(status, ids_suspeitos=()):
    """(clausulas, params) do filtro de Status, sobre `cartao.transacao t`."""
    where, params = [], []
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
    elif status == "pendente_banco":
        where.append("upper(COALESCE(t.status,'')) = 'PENDING'")
    elif status == "pendente_classificacao":
        # Falta categoria, ou falta uma dimensao obrigatoria QUE ESTE
        # lancamento exige. Registro que ja esta fora do resultado nunca vai
        # ter classificacao completa e nao e trabalho pendente (secao 10.4 n.11).
        where.append(PENDENTE_CLASSIFICACAO_SQL)
    elif status in ("receita", "despesa"):
        # Os cards de Receitas/Despesas somam sobre a view financeira, que exclui
        # substituido/somente_conciliacao/duplicada. O filtro le cartao.transacao
        # direto, entao precisa repetir a exclusao - senao o card promete um
        # total e a lista entrega outro, com registro que nao conta no resultado.
        where.append(
            "t.substituido_por IS NULL AND NOT COALESCE(t.somente_conciliacao, false) "
            "AND COALESCE(t.duplicada, false) = false "
            f"AND {NATUREZA_SQL} = %s"
        )
        params.append(status)
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
    return where, params


def resumo_do_periodo(cur, inicio_mes, fim_mes, origem_sel, periodo):
    """Numeros do periodo (recebidos, reais, DRE, classificacao) e o gasto por
    categoria. Ponto unico das duas telas: card e filtro tem que contar a mesma
    coisa, e um segundo calculo divergiria na primeira regra nova.

    Independe do filtro de Status: "recebidos" conta tudo o que chegou ao
    banco; "reais" conta cada transacao financeira uma vez, mesmo quando o
    rateio cria varias linhas no DRE.
    """
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

    # Contado a parte, sobre cartao.transacao e com a MESMA condicao do filtro
    # "Pendentes de classificacao": o card e o filtro tem que mostrar o mesmo
    # conjunto, senao o numero promete N linhas e a tela entrega outra coisa.
    cur.execute(
        f"SELECT COUNT(*) AS pendente_classificacao FROM cartao.transacao t {JOIN_NATUREZA} "
        "WHERE " + " AND ".join(where_resumo) + " AND " + PENDENTE_CLASSIFICACAO_SQL + ";",
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

    where_cat = ["t.data_transacao >= %s", "t.data_transacao < %s"]
    params_cat = [inicio_mes, fim_mes]
    if origem_sel:
        where_cat.append("t.account_id IN %s")
        params_cat.append(tuple(origem_sel))
    return resumo, gasto_por_categoria(cur, where_cat, params_cat)


def janela_do_periodo():
    """Le mes/periodo/intervalo da URL e devolve a janela local ja validada.

    Ponto unico de quem recorta por periodo. Nasceu servindo as duas telas de
    lancamentos (a Resumida saiu em 10/09/2026): recortes escritos duas vezes
    divergem na primeira regra nova.
    """
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
    return mes, periodo, data_inicio_str, data_fim_str, inicio_mes, fim_mes


def config_da_tela(obrigatorias, projeto_portfolio_map, ids_dimensoes, categorias,
                   dimensoes, valores_por_dim, pode_conferir, em_andamento=False):
    """O `config` que o JS da tela le. Ponto unico dos QUATRO construtores.

    O quadro de rateio monta os campos POR JS a partir daqui: sem `categorias` e
    `dimensoes` ele nasce com "(sem categoria)" como unica opcao e sem dimensao
    nenhuma, e o rateio novo fica impossivel de preencher - com a tela
    respondendo 200 e o `py_compile` passando (secao 7.1, etapa 7). Escrito uma
    vez por tela, bastava uma delas esquecer uma chave para a interface desligar
    em silencio naquela tela so.
    """
    return {
        "pode_editar": pode("lancamentos_editar"),
        "pode_conferir": pode_conferir,
        "em_andamento": em_andamento,
        "dimensoes_obrigatorias": [str(x) for x in obrigatorias],
        "projeto_portfolio_map": projeto_portfolio_map,
        "dim_id_projeto": str(ids_dimensoes.get("projeto") or ""),
        "dim_id_portfolio": str(ids_dimensoes.get("portfolio") or ""),
        "categorias": [{"chave": c, "nome": cat_pt_puro(c)} for c in categorias],
        "dimensoes": {
            str(d["id"]): [
                {"id": v["id"], "rotulo": rotulo_valor_dimensao(v)}
                for v in valores_por_dim.get(d["id"], [])
            ]
            for d in dimensoes
        },
        "dimensoes_nomes": {str(d["id"]): d["nome"] for d in dimensoes},
    }


def partes_do_rateio(cur, ids):
    """As partes de cada rateio, cruas do banco, agrupadas por transacao_id.

    Ponto unico dos TRES construtores de linha - recorte por periodo, fatura
    oficial e fatura em andamento (a Resumida, o quarto, saiu em 10/09/2026). A
    consulta estava escrita duas vezes, palavra por palavra, e uma copia nova
    teria divergido na primeira regra nova. E o mesmo motivo do `lote.js` e do `rateio.js`
    (secoes 7.2-A e 7.1).
    """
    if not ids:
        return {}
    cur.execute(
        "SELECT r.id, r.transacao_id, r.ordem, r.valor_brl, r.categoria, r.observacao, "
        "rd.dimensao_id, rd.valor_id FROM cartao.transacao_rateio r "
        "LEFT JOIN cartao.transacao_rateio_dimensao rd ON rd.rateio_id=r.id "
        "WHERE r.transacao_id IN %s ORDER BY r.transacao_id, r.ordem, r.id;",
        (tuple(ids),),
    )
    por_id = {}
    for rr in cur.fetchall():
        item = por_id.setdefault(rr["id"], {
            "id": rr["id"], "transacao_id": str(rr["transacao_id"]),
            "ordem": rr["ordem"], "valor_brl": rr["valor_brl"],
            "categoria": rr["categoria"], "observacao": rr["observacao"] or "", "dims": {},
        })
        # LEFT JOIN: parte sem dimensao nenhuma vem com dimensao_id nulo, e nao
        # pode virar uma chave None no dicionario.
        if rr["dimensao_id"] is not None:
            item["dims"][rr["dimensao_id"]] = rr["valor_id"]
    por_tx = {}
    for item in por_id.values():
        por_tx.setdefault(item["transacao_id"], []).append(item)
    return por_tx


def rateio_da_linha(partes, valor_pai, dimensoes, nomes_por_dim, obrigatorias,
                    com_sinal=False):
    """(as partes prontas para a linha, o rateio fecha?).

    Ponto unico dos quatro construtores, pelo mesmo motivo de `partes_do_rateio`.
    Um rateio so e valido com duas partes ou mais, soma exata, categoria em
    todas e as dimensoes obrigatorias preenchidas (secao 4.4) - Salvar e OK
    ficam bloqueados enquanto nao fechar.

    `com_sinal` e a diferenca legitima entre as telas: conta corrente mostra
    entrada e saida, cartao de credito nao tem sinal.
    """
    ui = []
    for parte in partes:
        dims_parte = {d["id"]: parte["dims"].get(d["id"]) for d in dimensoes}
        valor_parte = Decimal(str(parte["valor_brl"] or 0))
        if com_sinal:
            sinal = "-" if valor_parte < 0 else "+"
            cor = "color:var(--bad)" if valor_parte < 0 else "color:var(--good)"
            valor_fmt = f"{sinal} R$ {valor_pt(abs(valor_parte))}"
        else:
            cor, valor_fmt = "", f"R$ {valor_pt(abs(valor_parte))}"
        ui.append({
            "id": parte["id"],
            "valor": float(abs(valor_parte)),
            "valor_fmt": valor_fmt,
            "cor_valor": cor,
            "categoria": parte["categoria"],
            "categoria_nome": (
                cat_pt_puro(parte["categoria"]) if parte["categoria"] else "(não definido)"
            ),
            "observacao": parte["observacao"],
            "dims": dims_parte,
            "dims_rotulos": {
                d["id"]: nomes_por_dim[d["id"]].get(dims_parte[d["id"]], "(não definido)")
                for d in dimensoes
            },
        })
    if not ui:
        return [], False
    soma = sum(
        (Decimal(str(parte["valor_brl"] or 0)) for parte in partes), Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    valido = (
        len(ui) >= 2
        and soma == Decimal(str(valor_pai or 0)).quantize(Decimal("0.01"))
        and all(parte["categoria"] for parte in partes)
        and all(
            all(parte["dims"].get(dim_id) is not None for dim_id in obrigatorias)
            for parte in partes
        )
    )
    return ui, valido


def procedencia_do_registro(conta, importado=False, criado_pela_fatura=False):
    """(letra, nome) de onde o registro veio - o selo F/P/M/I do painel.

    Ponto unico dos tres construtores de linha. Eram duas letras so (F/P), e
    tudo o que nao nascia da fatura caia em "Pluggy": o lancamento manual e o
    que veio do importador de arquivo antigo diziam uma origem falsa - no
    painel que existe justamente para mostrar a origem.

    - M: conta MANUAL (dinheiro, digitado por alguem);
    - F: criado pela fatura (`fatura_linha.transacao_id_criado`, secao 11.3);
    - I: `transacao.importado` - o importador de OFX/CSV que existiu de 18 a
      21/08/2026 (commits 5361fad e 78c0d32; removido no 4cc22e3). Nenhum
      codigo atual grava essa marca;
    - P: o resto, que e o que o Pluggy sincronizou.
    """
    if (conta or {}).get("tipo") == "MANUAL":
        return "M", "Lançamento manual"
    if criado_pela_fatura:
        return "F", "Fatura importada"
    if importado:
        return "I", "Importado de arquivo"
    return "P", "Pluggy"


def origem_da_linha(conta, final4=None, nomes_cartao=None):
    """(selo em HTML, texto curto, texto completo) da origem de um lancamento.

    Ponto unico de quem mostra a coluna Origem - o recorte por periodo e o
    recorte por fatura (a Resumida tambem usava, ate sair em 10/09/2026). A
    coluna existe nos dois recortes desde 10/09/2026, porque a tabela e a
    mesma; copias
    da mesma regra divergiriam no primeiro banco novo, que foi exatamente como
    nasceram os 57 falsos pendentes da secao 6.5 nº 10.

    O selo e HTML montado pelo app e vai com `|safe`; o texto vem do apelido do
    cartao, digitado pelo usuario, e o template escapa.
    """
    conta = conta or {}
    nomes_cartao = nomes_cartao or {}
    if not conta:
        return "", "-", "-"
    apelido = nomes_cartao.get(final4) if final4 else None
    if conta.get("tipo") == "CREDIT" and final4:
        curto = apelido or conta.get("label_curto") or "-"
        completa = f'{conta.get("label", "-")} · ' + (apelido or f"final {final4}")
    else:
        curto = conta.get("label_curto") or "-"
        completa = conta.get("label", "-")
    return conta.get("selo", ""), curto, completa


def gasto_por_categoria(cur, where, params, limite=8):
    """Os maiores gastos por categoria, do maior para o menor.

    Ponto unico dos recortes por periodo e por fatura: os dois mostram o mesmo
    quadro, e o que muda entre eles e so o WHERE - janela + origem num, conjunto
    de lancamentos no outro. Roda sobre a view financeira, entao um lancamento
    rateado aparece pela categoria de cada PARTE, e nao como "(sem categoria)"
    do pai, que nao tem categoria propria (secao 4.4).
    """
    cur.execute(
        f"SELECT t.categoria, SUM({VAL_DESPESA}) AS total "
        f"FROM {FINANCEIRO_TABELA} t {JOIN_NATUREZA} WHERE "
        + " AND ".join(list(where) + [
            f"{NATUREZA_SQL} = 'despesa'", "t.categoria IS NOT NULL",
            "COALESCE(t.duplicada, false) = false",
        ])
        + " GROUP BY t.categoria ORDER BY total DESC LIMIT %s;",
        list(params) + [limite],
    )
    # Devolve ja no formato que o template imprime. A traducao do nome ficava
    # em cada chamador, e um chamador novo que a esquecesse renderizaria o
    # quadro com os nomes em branco, sem erro nenhum - a mesma familia da
    # coluna ausente num `.get()` (secao 11.3-A).
    return [
        {"nome": cat_pt_puro(r["categoria"]), "total": float(r["total"] or 0)}
        for r in cur.fetchall()
    ]


def situacoes_da_linha(conferida=False, duplicada=False, suspeita=False,
                       pendente_banco=False, substituido=False,
                       somente_conciliacao=False, rateio_incompleto=False,
                       requer_validacao=None, sem_vinculo=False):
    """As situacoes de um lancamento, na ordem em que a linha as mostra.

    Ponto unico das duas telas. Cor nunca e a unica explicacao de estado
    (secao 7.6): cada situacao vira um ponto no inicio da linha e uma frase no
    tooltip, e a legenda no rodape filtra por ela. Duas listas divergiriam - e
    a Detalhada ficaria com um estado a menos, em silencio.
    """
    situacoes = []
    if conferida:
        situacoes.append({"classe": "conferida", "rotulo": "Conferido"})
    if duplicada:
        situacoes.append({"classe": "duplicada", "rotulo": "Duplicado confirmado — não contabilizado"})
    if suspeita:
        situacoes.append({"classe": "suspeita", "rotulo": "Possível duplicidade — revisar"})
    if pendente_banco:
        situacoes.append({"classe": "pendente-banco", "rotulo": "Pendente no banco"})
    if substituido:
        situacoes.append({"classe": "fora", "rotulo": "Fora do resultado — substituído por outro lançamento"})
    elif somente_conciliacao:
        situacoes.append({"classe": "fora", "rotulo": "Fora do resultado — somente conciliação"})
    if rateio_incompleto:
        situacoes.append({"classe": "rateio", "rotulo": "Rateio incompleto"})
    # Proprias da conciliacao: so existem quando a linha vem de um documento.
    if sem_vinculo:
        situacoes.append({"classe": "suspeita", "rotulo": "Cobrança sem lançamento vinculado"})
    elif requer_validacao:
        situacoes.append({"classe": "suspeita", "rotulo": "Validar: " + ", ".join(requer_validacao)})
    return situacoes


def tem_situacao(linha, classe):
    """A linha esta nesta situacao? Le a lista que `situacoes_da_linha` monta.

    E o mesmo ponto de verdade que pinta a linha e escreve o tooltip, entao
    filtrar por situacao devolve exatamente o que a tela mostra. Uma segunda
    condicao aqui divergiria da primeira regra nova - a licao da secao 6.5 nº 10.
    """
    return any(s["classe"] == classe for s in linha.get("situacoes", []))


def filtros_por_situacao(disponiveis):
    """Os atalhos de "Filtrar por situacao" do rodape da tela.

    Cada recorte declara os status que ele de fato entende, e o template so
    imprime - as URLs escritas a mao no template prendiam o bloco a um recorte
    so. A URL preserva tudo o que ja esta na barra (mes, origem, fatura) e troca
    apenas o status, senao filtrar por situacao jogaria o usuario para outro
    periodo sem avisar.
    """
    base = [(k, v) for k, valores in request.args.lists() for v in valores if k != "status"]
    return [
        {"rotulo": rotulo, "url": "?" + urlencode(base + [("status", chave)])}
        for chave, rotulo in disponiveis
    ]


def texto_das_situacoes(situacoes):
    """O tooltip dos pontos: todas as situacoes, ou a frase de quem nao tem
    nenhuma. Sem ele, linha sem situacao ficaria com um tooltip vazio."""
    return " · ".join(x["rotulo"] for x in situacoes) or "Lançamento contabilizado"


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


def _url_da_fatura(item, account_id):
    """Endereco de uma entrada do seletor - oficial, em andamento ou prevista.

    Fica no servidor porque as setas < > e o seletor precisam da MESMA regra:
    montada em dois lugares, a navegacao passaria a discordar de si mesma.
    """
    if str(item["id"]).startswith("futuro-"):
        return (f"/lancamentos/fatura?andamento=1&mes={str(item['id'])[7:]}"
                f"&account_id={account_id}")
    if item.get("em_andamento"):
        return f"/lancamentos/fatura?andamento=1&account_id={account_id}"
    return f"/lancamentos/fatura?fatura_id={item['id']}"


ROTULO_STATUS = {
    "todas": "Todas",
    "pendente": "Pendentes de conferência",
    "conferida": "Conferidas",
    "pendente_classificacao": "Pendentes de classificação",
    "receita": "Só receitas",
    "despesa": "Só despesas",
    "pendente_banco": "Pendentes no banco",
    "duplicidade": "Possíveis duplicidades",
    "fora_resultado": "Fora do resultado",
    "somente_conciliacao": "Somente conciliação",
    "substituido": "Substituídos por outro",
    "rateio_incompleto": "Rateio incompleto",
    # So existem com uma fatura em foco: falam do DOCUMENTO, nao do lancamento.
    "dre": "Despesas no DRE",
    "fora": "Fora do DRE",
    "sem_vinculo": "Sem lançamento vinculado",
    "requer_validacao": "Requer validação",
    "multiplos": "Com registros agregados",
}

# Com uma fatura em foco a tela lista linhas do DOCUMENTO, e alguns filtros do
# periodo nao se aplicam: "possiveis duplicidades", "substituidos" e "somente
# conciliacao" falam de lancamentos que a fatura nem mostra como linha propria.
STATUS_COM_FATURA = (
    "todas", "pendente", "conferida", "pendente_classificacao",
    "pendente_banco", "rateio_incompleto", "dre", "fora",
    "sem_vinculo", "requer_validacao", "multiplos",
)
# Sem fatura importada nao ha o que conciliar: estes tres sumiriam sozinhos.
STATUS_SO_COM_DOCUMENTO = ("sem_vinculo", "requer_validacao", "multiplos")


def opcoes_de_status(com_fatura=False, em_andamento=False):
    """A lista de Status, que cresce e encolhe sozinha conforme o recorte.

    Era escrita a mao em dois `<select>` diferentes no template, e por isso o
    mesmo filtro tinha nomes diferentes nas duas metades da tela. Aqui cada
    recorte declara o que de fato entende - listar um filtro que nao traria
    linha nenhuma e prometer o que a tela nao cumpre.
    """
    if not com_fatura:
        chaves = list(STATUS_LANCAMENTO)
    else:
        chaves = [
            c for c in STATUS_COM_FATURA
            if not (em_andamento and c in STATUS_SO_COM_DOCUMENTO)
        ]
    return [(c, ROTULO_STATUS[c]) for c in chaves]


def lista_de_faturas(cur, account_id):
    """Ciclos previstos, ciclo em andamento e faturas oficiais de um cartao, na
    ordem do seletor e ja com a URL de cada um.

    Ponto unico do filtro Fatura, do seletor e das setas. Estava escrito duas
    vezes, e as duas listas ja divergiam em quais colunas traziam.
    """
    cur.execute(
        "SELECT id, mes_referencia, ano_referencia, periodo_inicio, periodo_fim "
        "FROM cartao.fatura_importada WHERE account_id=%s "
        "ORDER BY ano_referencia DESC, mes_referencia DESC, id DESC;",
        (account_id,),
    )
    oficiais = [dict(f) for f in cur.fetchall()]
    itens = []
    if oficiais and oficiais[0].get("periodo_fim"):
        prox_mes, prox_ano = oficiais[0]["mes_referencia"] + 1, oficiais[0]["ano_referencia"]
        if prox_mes == 13:
            prox_mes, prox_ano = 1, prox_ano + 1
        # Os previstos comecam no mes SEGUINTE: o resto do mes corrente pertence
        # ao ciclo em andamento, e listar o mesmo mes duas vezes confundia.
        itens = _meses_futuros_com_dados(cur, account_id, datetime.now(FUSO_LOCAL).date())
        itens = itens + [{
            "id": "andamento", "mes_referencia": prox_mes,
            "ano_referencia": prox_ano, "em_andamento": True,
        }]
    itens = itens + oficiais
    for item in itens:
        item["url"] = _url_da_fatura(item, account_id)
    return itens


def faturas_para_o_filtro(cur, origens, contas_by_id, id_em_foco=None):
    """O liga/desliga que o usuario pediu (10/09/2026).

    O filtro Fatura so aparece quando UMA origem esta selecionada e ela e um
    cartao de credito com fatura importada. Com varias origens nao existe "a
    fatura"; numa conta corrente ou no dinheiro, fatura nao existe. Assim o
    proprio filtro se gerencia com o que ha, em vez de um alternador fixo que
    prometia um recorte que nem sempre se aplica.
    """
    if len(origens) != 1:
        return []
    conta = contas_by_id.get(str(origens[0])) or {}
    if conta.get("tipo") != "CREDIT":
        return []
    itens = lista_de_faturas(cur, str(origens[0]))
    for item in itens:
        item["selecionada"] = str(item["id"]) == str(id_em_foco)
    return itens


def _vizinhas_no_seletor(lista, id_atual):
    """A seguinte e a anterior segundo a MESMA ordem do seletor.

    A lista vem do mais futuro para o mais antigo, entao "seguinte" e o item
    acima e "anterior" e o de baixo. Calcular por posicao, e nao por data,
    mantem as setas coerentes com o que o seletor mostra.
    """
    ids = [str(item["id"]) for item in lista]
    if str(id_atual) not in ids:
        return None, None
    pos = ids.index(str(id_atual))
    seguinte = lista[pos - 1] if pos > 0 else None
    anterior = lista[pos + 1] if pos + 1 < len(lista) else None
    return seguinte, anterior


def _meses_futuros_com_dados(cur, account_id, hoje):
    """Meses AINDA POR VIR que ja tem lancamento do Pluggy.

    Parcela futura chega adiantada e, sem isto, nao aparecia em lugar nenhum: o
    ciclo em andamento termina hoje. Sao ciclos PREVISTOS, nao faturas - o
    intervalo fechamento-vencimento varia (secao 6.2) e projeta-lo seria
    palpite, entao a janela e o mes civil.
    """
    # A partir do mes SEGUINTE, nao de amanha: o resto do mes corrente pertence
    # ao ciclo em andamento, e listar "Setembro · Previsto" ao lado de
    # "Setembro · Em andamento" poe o mesmo mes duas vezes no seletor.
    cur.execute(
        f"SELECT DISTINCT to_char({DATA_LOCAL_SQL}, 'YYYY-MM') AS mes "
        "FROM cartao.transacao t WHERE t.account_id=%s "
        f"AND to_char({DATA_LOCAL_SQL}, 'YYYY-MM') > %s "
        "AND COALESCE(t.duplicada,false)=false AND t.substituido_por IS NULL "
        "AND COALESCE(t.somente_conciliacao,false)=false ORDER BY 1 DESC;",
        (account_id, hoje.strftime("%Y-%m")),
    )
    return [
        {"id": "futuro-" + r["mes"], "mes_referencia": int(r["mes"][5:]),
         "ano_referencia": int(r["mes"][:4]), "em_andamento": True, "previsto": True}
        for r in cur.fetchall()
    ]


def _render_fatura_em_andamento(cur, account_id, contas_credito, contas_by_id,
                                origem_opcoes, mes_futuro=None):
    """Mostra o ciclo atual do Pluggy sem fingir que ja existe um PDF oficial.

    Com `mes_futuro` ('AAAA-MM'), mostra um mes que ainda nem comecou a ser
    cobrado: parcela futura que o Pluggy ja entregou. Ali a janela e o MES
    CIVIL, e nao um ciclo - projetar fechamento seria palpite (secao 6.2).
    """
    cur.execute(
        "SELECT * FROM cartao.fatura_importada WHERE account_id=%s "
        "ORDER BY ano_referencia DESC, mes_referencia DESC, id DESC LIMIT 1;",
        (account_id,),
    )
    ultima = cur.fetchone()
    if not ultima or not ultima["periodo_fim"]:
        return None
    hoje = datetime.now(FUSO_LOCAL).date()
    if mes_futuro:
        ano, mes = int(mes_futuro[:4]), int(mes_futuro[5:])
        inicio = date(ano, mes, 1)
        fim = date(ano + (mes == 12), (mes % 12) + 1, 1) - timedelta(days=1)
    else:
        inicio = ultima["periodo_fim"] + timedelta(days=1)
        # Ate o fim do mes corrente, e nao ate hoje: o que o Pluggy ja entregou
        # com data nos proximos dias tambem pertence a este ciclo, e sem isso
        # ficava invisivel - nem aqui, nem nos meses previstos, que comecam no
        # mes seguinte.
        fim = date(hoje.year + (hoje.month == 12), (hoje.month % 12) + 1, 1) - timedelta(days=1)
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
        "false AS somente_conciliacao, COALESCE(t.importado,false) AS importado, "
        + NATUREZA_SQL + " AS natureza_efetiva "
        "FROM cartao.transacao t " + JOIN_NATUREZA + " WHERE t.account_id=%s "
        "AND (" + DATA_LOCAL_SQL + ")::date >= %s AND (" + DATA_LOCAL_SQL + ")::date <= %s "
        "AND COALESCE(t.duplicada,false)=false AND t.substituido_por IS NULL "
        "AND COALESCE(t.somente_conciliacao,false)=false ORDER BY t.data_transacao, t.transacao_id;",
        (account_id, inicio, fim),
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
    nomes_por_dim = {
        d["id"]: {v["id"]: rotulo_valor_dimensao(v) for v in valores_por_dim.get(d["id"], [])}
        for d in dimensoes
    }
    dims_por_tx = {}
    partes_por_tx = {}
    if ids:
        cur.execute(
            "SELECT transacao_id, dimensao_id, valor_id FROM cartao.transacao_dimensao "
            "WHERE transacao_id IN %s;", (tuple(ids),),
        )
        for row in cur.fetchall():
            dims_por_tx.setdefault(str(row["transacao_id"]), {})[row["dimensao_id"]] = row["valor_id"]
        partes_por_tx = partes_do_rateio(cur, ids)

    cur.execute(f"SELECT DISTINCT categoria FROM {FINANCEIRO_TABELA} WHERE categoria IS NOT NULL;")
    categorias_db = {r["categoria"] for r in cur.fetchall()}
    categorias = sorted(
        (categorias_db | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB)) - CATEGORIAS_OCULTAS,
        key=lambda c: chave_alfa(cat_pt_puro(c)),
    )
    cur.execute("SELECT final4, prefixo FROM cartao.cartao_nome;")
    nomes_cartao = {r["final4"]: r["prefixo"] for r in cur.fetchall()}

    conta_da_fatura = contas_by_id.get(account_id) or {}
    titular_conexao = conta_da_fatura.get("titular")

    linhas, total, total_dre, total_fora, classificadas = [], Decimal("0"), Decimal("0"), Decimal("0"), 0
    for indice, tx in enumerate(transacoes, 1):
        tid = str(tx["transacao_id"])
        tx["transacao_id"] = tid
        tx["data_local"] = data_hora_local(tx.pop("data_transacao"))
        tx["conferida_local"] = data_hora_local(tx.pop("conferida_em"))
        tx["sincronizado_local"] = data_hora_local(tx.pop("sincronizado_em"))
        tx["primeiro_sincronizado_local"] = data_hora_local(tx.pop("primeiro_sincronizado_em"))
        tx["elegivel"] = True
        tx["principal"], tx["tecnico"] = True, False
        tx["fonte"], tx["fonte_nome"] = procedencia_do_registro(conta_da_fatura, tx.get("importado"))
        tx["dims"] = dims_por_tx.get(tid, {})
        partes = partes_por_tx.get(tid, [])
        tx["rateado"] = bool(partes)
        tx["pode_excluir"] = bool(tx["importado"])
        rateios_ui, valido_do_rateio = rateio_da_linha(
            partes, tx["valor"], dimensoes, nomes_por_dim, obrigatorias)
        tx["exige_dimensoes"] = exige_dimensoes(tx["natureza_efetiva"])
        origem_selo, origem_texto, origem_full = origem_da_linha(
            conta_da_fatura, tx["numero_cartao_final"], nomes_cartao)
        if partes:
            # Num rateado a classificacao mora nas partes (secao 4.4): o pai nao
            # tem categoria propria, e cobra-la aqui daria todo rateado como
            # pendente, inclusive os completos.
            faltando = [] if valido_do_rateio else ["Rateio"]
        else:
            faltando = ([] if tx["categoria"] else ["Categoria"]) + [
                nomes_dimensoes[d] for d in obrigatorias
                if tx["exige_dimensoes"] and not tx["dims"].get(d)
            ]
        completo = not faltando
        classificadas += int(completo)
        valor = Decimal(str(tx["valor"] or 0))
        total += valor
        if tx["natureza_efetiva"] == "despesa":
            total_dre += valor
        else:
            total_fora += valor
        # Titular (nome completo do portador) so existe impresso no PDF - o
        # Pluggy nunca manda isso por transacao, so o final do cartao via
        # creditCardMetadata. Enquanto a compra esta PENDING, alguns lojistas
        # (assinaturas, cobranca internacional) chegam sem esse metadado
        # ainda; ele costuma aparecer quando o Pluggy confirma o POSTED.
        linhas.append({
            "id": "andamento-" + str(indice), "data": tx["data_local"].date(),
            "descricao": tx["descricao"],
            # O nome impresso do portador so existe no PDF. Ate a fatura chegar,
            # o avatar mostra o que o Pluggy tem: o apelido do cartao (por final4)
            # ou, na falta dele, o titular da conexao. Quando a fatura e
            # importada, o titular dela substitui isto - e a fonte que manda
            # sobre o que foi cobrado (secao 5).
            "titular": nomes_cartao.get(tx["numero_cartao_final"]) or titular_conexao,
            "titular_fonte": "pluggy",
            # A coluna Origem existe nos DOIS recortes (a tabela e a mesma).
            # Aqui todas as linhas sao do mesmo cartao, mas nao da mesma via:
            # um cartao de credito pode ter varios cartoes fisicos/virtuais, e
            # e o final4 que os separa - por isso a origem e por linha, nao da
            # tela. Lancamento de fatura nunca tem autor: ninguem o digitou.
            "origem_selo": origem_selo, "origem_texto": origem_texto,
            "origem_completa": origem_full, "autor": None,
            # A linha tem UM contrato so nos dois recortes: o template le sempre
            # os mesmos campos, e quem decide o conteudo e o construtor. Assim a
            # tabela e a mesma e o que muda e o TIPO do lancamento, nao a tela.
            "procedencia": (
                (nomes_cartao.get(tx["numero_cartao_final"]) or titular_conexao or origem_full)
                + (" · cartão pendente (o Pluggy ainda não confirmou)"
                   if not tx["numero_cartao_final"] else "")
            ),
            "valor_fmt": f"R$ {valor_pt(valor)}", "cor_valor": "",
            "pendente_bloqueia_ok": _pendente_bloqueia(tx["status"], tx["data_local"]),
            "cartao_aguardando": not tx["numero_cartao_final"],
            "parcela_atual": tx["parcela_atual"], "parcela_total": tx["parcela_total"],
            "valor": valor, "pagamento": False, "vinculos": [tx], "principal": tx,
            "rateios": rateios_ui, "rateio_valido": (not rateios_ui) or valido_do_rateio,
            "valor_rateio": float(abs(valor.quantize(Decimal("0.01")))),
            "multiplos": False, "requer_validacao": False, "validacao_motivos": [],
            "faltando": faltando, "classificada": completo, "conferida": bool(tx["conferida"]),
            "natureza_estado": "dre" if tx["natureza_efetiva"] == "despesa" else "fora",
            "natureza_rotulo": NATUREZAS.get(tx["natureza_efetiva"], tx["natureza_efetiva"]),
            "cartao_final": tx["numero_cartao_final"],
            "cartao_nome": nomes_cartao.get(tx["numero_cartao_final"]),
            "situacoes": situacoes_da_linha(
                conferida=bool(tx["conferida"]),
                pendente_banco=(tx["status"] or "").upper() == "PENDING",
                rateio_incompleto=bool(rateios_ui) and not valido_do_rateio,
            ),
            "classes": " ".join(c for c in [
                "conferida" if tx["conferida"] else "",
                "pendente-banco" if (tx["status"] or "").upper() == "PENDING" else "",
            ] if c),
            "estado": "andamento",
        })

    for linha in linhas:
        linha["situacoes_texto"] = texto_das_situacoes(linha["situacoes"])

    # "Gasto por categoria" existe nos DOIS recortes: soma os lancamentos deste
    # ciclo, os mesmos que a tela lista.
    ids_principais = sorted({str(l["principal"]["transacao_id"]) for l in linhas})
    por_categoria = gasto_por_categoria(
        cur, ["t.transacao_id::text = ANY(%s)"], [ids_principais]
    ) if ids_principais else []

    status = request.args.get("status", "todas")
    # O conjunto aceito sai da MESMA lista que o seletor oferece: o seletor
    # listava "Pendentes de conferencia" e "Rateio incompleto" e a rota nao os
    # entendia, caindo em "Todas" sem avisar.
    if status not in {c for c, _ in opcoes_de_status(com_fatura=True, em_andamento=True)}:
        status = "todas"
    linhas_visiveis = [l for l in linhas if (
        status == "todas" or
        (status == "pendente_classificacao" and not l["classificada"]) or
        (status == "pendente" and not l["conferida"]) or
        (status == "dre" and l["natureza_estado"] == "dre") or
        (status == "fora" and l["natureza_estado"] == "fora") or
        (status == "conferida" and tem_situacao(l, "conferida")) or
        (status == "pendente_banco" and tem_situacao(l, "pendente-banco")) or
        (status == "rateio_incompleto" and tem_situacao(l, "rateio"))
    )]
    # Sem rateio e sem vinculo aqui: o ciclo em andamento nao tem documento,
    # entao essas situacoes nao existem nele - lista-las prometeria um filtro
    # que nunca traria linha nenhuma.
    filtros_situacao = filtros_por_situacao([
        ("conferida", "Conferido"),
        ("pendente_banco", "Pendente no banco"),
        ("fora", "Fora do DRE"),
    ])
    fatura = {
        "id": ("futuro-" + mes_futuro) if mes_futuro else "andamento",
        "mes_referencia": mes, "ano_referencia": ano,
        "periodo_inicio": inicio, "periodo_fim": fim, "vencimento": None,
        "em_andamento": True, "previsto": bool(mes_futuro),
    }
    # O seletor mostra o mesmo conjunto nas duas telas: previstos, em andamento
    # e oficiais. Sem isto, entrar num mes futuro escondia os demais. A entrada
    # que corresponde ao ciclo aberto e trocada pela versao rica dele, que ja
    # sabe se e previsto.
    lista_faturas = [
        fatura if str(f["id"]) == str(fatura["id"]) else f
        for f in lista_de_faturas(cur, account_id)
    ]
    for item in lista_faturas:
        item["selecionada"] = str(item["id"]) == str(fatura["id"])
        item.setdefault("url", _url_da_fatura(item, account_id))
    seguinte, anterior = _vizinhas_no_seletor(lista_faturas, fatura["id"])

    config = config_da_tela(
        obrigatorias, projeto_portfolio_map, ids_dimensoes, categorias,
        dimensoes, valores_por_dim, pode_conferir=False, em_andamento=True)
    # Sair da fatura leva ao PERIODO DELA, nao ao mes corrente: o ciclo e o
    # recorte que o usuario esta olhando, e joga-lo para outro mes ao trocar de
    # filtro seria perder o lugar.
    url_do_periodo = (
        "/lancamentos/fatura?recorte=periodo&periodo=intervalo"
        f"&data_inicio={inicio.isoformat()}&data_fim={fim.isoformat()}"
        f"&origem={account_id}&status=todas"
    )
    return render_template(
        "lancamentos_fatura.html", titulo="Fatura em andamento",
        topbar=topbar_html("Lançamentos", "inicio"), fatura=fatura,
        fatura_nova=seguinte, fatura_antiga=anterior,
        faturas=lista_faturas, conta=contas_by_id.get(account_id),
        avatar_banco=cor_banco((contas_by_id.get(account_id) or {}).get("banco")),
        contas_credito=contas_credito, account_id=account_id, linhas=linhas_visiveis,
        categorias=[{"chave": c, "nome": cat_pt_puro(c)} for c in categorias],
        dimensoes=dimensoes, valores_por_dim=valores_por_dim, status=status,
        totais={"pdf": total, "dre": total_dre, "fora": total_fora, "pendente": sum(abs(l["valor"]) for l in linhas if not l["classificada"]), "pendente_ok": Decimal("0"), "sem_vinculo": Decimal("0"), "divergencia": Decimal("0")},
        contagens={"linhas": len(linhas), "vinculadas": len(linhas), "classificadas": classificadas, "conferidas": 0, "multiplos": 0, "pendente_classificacao": len(linhas)-classificadas, "pendente_ok": 0, "divergencias": 0},
        config_json=json_script(config), projeto_portfolio_map=projeto_portfolio_map,
        # A barra de filtros e a MESMA nos dois recortes e se autogerencia: a
        # Origem em chip, o filtro Fatura so quando a origem selecionada e um
        # cartao com fatura, e o Status crescendo junto. O alternador fixo
        # "Por periodo | Por fatura" deixou de existir (decisao do usuario).
        origem_filtro_html=chip_origem_html(
            contas_by_id, origem_opcoes, [account_id],
            onchange="aplicarFiltrosPeriodo()"),
        faturas_da_origem=faturas_para_o_filtro(
            cur, [account_id], contas_by_id, fatura["id"]),
        status_opcoes=opcoes_de_status(
            com_fatura=True, em_andamento=bool(fatura.get("em_andamento"))),
        url_do_periodo=url_do_periodo,
        mes=f"{ano}-{mes:02d}", periodo="intervalo",
        data_inicio=inicio.isoformat(), data_fim=fim.isoformat(),
        por_categoria=por_categoria, filtros_situacao=filtros_situacao,
        pode_editar=pode("lancamentos_editar"), pode_conferir=False,
        pode_regras=pode("cadastros"), pode_manual=pode("lancamentos_manual"),
    )


def _render_periodo(cur, contas_by_id, origem_opcoes, contas_credito):
    """A Detalhada recortando por PERIODO em vez de por fatura.

    A tela nasceu presa a uma fatura de cartao, e por isso conta corrente,
    dinheiro e lancamento manual nunca tiveram lugar nela: nao pertencem a
    fatura nenhuma. Aqui as mesmas linhas sao montadas a partir de
    `cartao.transacao`, com os mesmos campos e o mesmo salvamento - o que muda
    e o recorte, nao o dado.

    Os registros que ficam FORA do resultado (substituido por outro, somente
    conciliacao) nao viram linha propria: eles se recolhem como vinculo tecnico
    sob o lancamento que conta, do mesmo jeito que a fatura agrega os seus.
    """
    mes, periodo, data_inicio_str, data_fim_str, inicio_mes, fim_mes = janela_do_periodo()
    status = request.args.get("status", "todas")
    if status not in STATUS_LANCAMENTO:
        status = "todas"
    origem_sel = request.args.getlist("origem")

    # Contagem por origem para o chip: sem o filtro de origem de proposito -
    # com ele, marcar uma origem zeraria a contagem das outras.
    cur.execute(
        "SELECT account_id, COUNT(*) AS n FROM cartao.transacao t "
        "WHERE t.data_transacao >= %s AND t.data_transacao < %s GROUP BY account_id;",
        (inicio_mes, fim_mes),
    )
    qtd_por_origem = {str(r["account_id"]): r["n"] for r in cur.fetchall()}

    cur.execute(
        "SELECT array_agg(t.transacao_id::text) AS ids FROM cartao.transacao t "
        "WHERE t.data_transacao >= %s AND t.data_transacao < %s "
        "AND COALESCE(t.duplicada, false) = false "
        # par ja resolvido nao e suspeita: o substituido e o registro de
        # conciliacao estao fora do resultado, e contar com eles marcava o
        # lancamento que sobrou como "possivel duplicidade" (secao 11.3)
        "AND t.substituido_por IS NULL AND NOT COALESCE(t.somente_conciliacao, false) "
        f"GROUP BY t.account_id, ({DATA_LOCAL_SQL})::date, "
        "COALESCE(t.valor_brl, t.valor_original), t.descricao "
        "HAVING COUNT(*) > 1;",
        (inicio_mes, fim_mes),
    )
    ids_suspeitos = set()
    for r in cur.fetchall():
        ids_suspeitos.update(r["ids"] or [])

    where = ["t.data_transacao >= %s", "t.data_transacao < %s"]
    params = [inicio_mes, fim_mes]
    if origem_sel:
        where.append("t.account_id IN %s")
        params.append(tuple(origem_sel))
    clausulas_status, params_status = where_status_lancamento(status, ids_suspeitos)
    where.extend(clausulas_status)
    params.extend(params_status)
    cur.execute(
        "SELECT t.transacao_id, t.account_id, t.data_transacao, t.descricao, t.categoria, "
        "COALESCE(t.valor_brl, t.valor_original) AS valor, t.valor_original, t.moeda_original, "
        "t.status, t.tipo, t.numero_cartao_final, t.parcela_atual, t.parcela_total, "
        "t.conferida, t.conferida_por, t.conferida_em, t.observacao, t.observacao_sistema, "
        "COALESCE(t.duplicada, false) AS duplicada, t.substituido_por, "
        "COALESCE(t.somente_conciliacao, false) AS somente_conciliacao, "
        "COALESCE(t.importado, false) AS importado, t.sincronizado_em, "
        "t.primeiro_sincronizado_em, t.atualizado_em, t.criado_por, "
        f"{NATUREZA_SQL} AS natureza_efetiva "
        f"FROM cartao.transacao t {JOIN_NATUREZA} WHERE " + " AND ".join(where) +
        " ORDER BY t.data_transacao DESC, t.transacao_id;",
        params,
    )
    rows = [dict(r) for r in cur.fetchall()]
    ids = [r["transacao_id"] for r in rows]

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

    nomes_por_dim = {
        d["id"]: {v["id"]: rotulo_valor_dimensao(v) for v in valores_por_dim.get(d["id"], [])}
        for d in dimensoes
    }

    dims_por_tx = {}
    rateio_por_tx = {}
    criados_pela_fatura = set()
    principal_por_tecnico = {}
    if ids:
        cur.execute(
            "SELECT transacao_id, dimensao_id, valor_id FROM cartao.transacao_dimensao "
            "WHERE transacao_id IN %s;", (tuple(ids),),
        )
        for row in cur.fetchall():
            dims_por_tx.setdefault(str(row["transacao_id"]), {})[row["dimensao_id"]] = row["valor_id"]
        rateio_por_tx = partes_do_rateio(cur, ids)
        # F/P: "criado pela fatura" e ser o `transacao_id_criado` de uma linha,
        # NAO o `transacao.importado`, que e outra coisa (secao 11.3).
        cur.execute(
            "SELECT DISTINCT transacao_id_criado FROM cartao.fatura_linha "
            "WHERE transacao_id_criado IN %s;", (tuple(ids),),
        )
        criados_pela_fatura = {str(r["transacao_id_criado"]) for r in cur.fetchall()}
        # Registro de conciliacao recolhido sob a parcela que o substitui -
        # so quando ha UM destino visivel e inequivoco.
        ids_conc = [r["transacao_id"] for r in rows if r["somente_conciliacao"]]
        if ids_conc:
            cur.execute(
                "SELECT DISTINCT fv.transacao_id, fl.transacao_id_criado "
                "FROM cartao.fatura_vinculo fv "
                "JOIN cartao.fatura_linha fl ON fl.id=fv.fatura_linha_id "
                "WHERE fv.transacao_id IN %s AND fl.transacao_id_criado IN %s "
                "AND fl.transacao_id_criado<>fv.transacao_id;",
                (tuple(ids_conc), tuple(ids)),
            )
            destinos = {}
            for v in cur.fetchall():
                destinos.setdefault(str(v["transacao_id"]), set()).add(str(v["transacao_id_criado"]))
            principal_por_tecnico = {
                tec: next(iter(alvos)) for tec, alvos in destinos.items() if len(alvos) == 1
            }

    cur.execute(f"SELECT DISTINCT categoria FROM {FINANCEIRO_TABELA} WHERE categoria IS NOT NULL;")
    categorias_db = {r["categoria"] for r in cur.fetchall()}
    categorias = sorted(
        (categorias_db | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB)) - CATEGORIAS_OCULTAS,
        key=lambda c: chave_alfa(cat_pt_puro(c)),
    )
    cur.execute("SELECT final4, prefixo FROM cartao.cartao_nome;")
    nomes_cartao = {r["final4"]: r["prefixo"] for r in cur.fetchall()}

    linhas, por_id = [], {}
    for row in rows:
        tid = str(row["transacao_id"])
        row["transacao_id"] = tid
        row["data_local"] = data_hora_local(row["data_transacao"])
        row["conferida_local"] = data_hora_local(row.pop("conferida_em"))
        row["sincronizado_local"] = data_hora_local(row.pop("sincronizado_em"))
        row["primeiro_sincronizado_local"] = data_hora_local(row.pop("primeiro_sincronizado_em"))
        row["atualizado_local"] = data_hora_local(row.pop("atualizado_em"))
        row["fonte"], row["fonte_nome"] = procedencia_do_registro(
            contas_by_id.get(str(row["account_id"])), row["importado"],
            tid in criados_pela_fatura)
        row["dims"] = dims_por_tx.get(tid, {})
        rateio = rateio_por_tx.get(tid) or []
        row["rateado"] = bool(rateio)
        row["exige_dimensoes"] = exige_dimensoes(row["natureza_efetiva"])
        row["principal"], row["tecnico"] = True, False
        conta_row = contas_by_id.get(str(row["account_id"])) or {}
        row["pode_excluir"] = bool(conta_row.get("tipo") == "MANUAL" or row["importado"])
        # Lancamento manual nunca sincroniza: no painel, "ultima sincronizacao"
        # nao quer dizer nada nele. O que responde "minha edicao entrou?" e o
        # `atualizado_em` - por isso o painel troca os carimbos quando e manual,
        # como o modal da Resumida fazia.
        row["manual"] = conta_row.get("tipo") == "MANUAL"
        selo, origem_texto, origem_full = origem_da_linha(
            conta_row, row["numero_cartao_final"], nomes_cartao)
        valor = Decimal(str(row["valor"] or 0))
        conta = contas_by_id.get(str(row["account_id"])) or {}
        # Fora do resultado: existe, e consultavel, e nao entra no DRE. Sem
        # marcar, dois lancamentos de mesmo valor aparecem lado a lado sem
        # pista de que so um conta (secao 7.4).
        fora = (
            "Mesmo evento que outro lançamento — só o outro conta no resultado."
            if row["substituido_por"] else
            ("Registro de conciliação (compra parcelada inteira) — as parcelas é que contam."
             if row["somente_conciliacao"] else "")
        )
        rateios_ui, rateio_valido = rateio_da_linha(
            rateio, valor, dimensoes, nomes_por_dim, obrigatorias,
            com_sinal=bool(conta.get("tipo") and conta["tipo"] != "CREDIT"))
        if rateio:
            faltando = [] if rateio_valido else ["Rateio"]
        else:
            faltando = ([] if row["categoria"] else ["Categoria"]) + [
                nomes_dimensoes[d] for d in obrigatorias
                if row["exige_dimensoes"] and not row["dims"].get(d)
            ]
        # Registro fora do resultado nunca vai ter classificacao completa e
        # nao e trabalho pendente (secao 10.4 n.13).
        if fora:
            faltando = []
        natureza = row["natureza_efetiva"]
        linha = {
            "id": "t-" + tid,
            "data": row["data_local"],
            "descricao": row["descricao"] or "",
            "origem_selo": selo, "origem_texto": origem_texto, "origem_completa": origem_full,
            # No recorte por periodo nao existe titular: o nome do portador so
            # vem impresso no documento, e quem identifica a procedencia aqui e
            # a origem. "Titular nao informado" sugeriria um dado faltando que
            # nem se aplica.
            "procedencia": origem_full,
            # So o MANUAL tem descricao editavel: a de um lancamento do banco
            # pertence ao banco (secao 4.6), e o servidor recusa no proprio
            # UPDATE mesmo que alguem chame a API direto.
            "descricao_editavel": str(row["account_id"]) == CONTA_MANUAL_ID,
            # So o MANUAL tem autor: o que veio do banco nao foi digitado por
            # ninguem, e inventar uma inicial diria que alguem lancou o que o
            # Pluggy mandou (secao 7.1).
            "autor": row["criado_por"] if str(row["account_id"]) == CONTA_MANUAL_ID else None,
            "titular": None, "titular_fonte": None,
            "cartao_aguardando": False,
            "cartao_nome": nomes_cartao.get(row["numero_cartao_final"]),
            "cartao_final": row["numero_cartao_final"],
            "parcela_atual": row["parcela_atual"], "parcela_total": row["parcela_total"],
            "valor": valor,
            "valor_fmt": (
                ("- " if row["tipo"] == "DEBIT" else "+ ") + "R$ " + valor_pt(abs(valor))
                if conta.get("tipo") and conta["tipo"] != "CREDIT"
                else "R$ " + valor_pt(valor)
            ),
            "cor_valor": (
                ("color:var(--bad)" if row["tipo"] == "DEBIT" else "color:var(--good)")
                if conta.get("tipo") and conta["tipo"] != "CREDIT" else ""
            ),
            "pagamento": False,
            "vinculos": [row], "principal": row, "multiplos": False,
            "requer_validacao": False, "validacao_motivos": [],
            "faltando": faltando, "classificada": not faltando,
            "conferida": bool(row["conferida"]),
            "fora_do_resultado": fora,
            "suspeita_duplicidade": tid in ids_suspeitos,
            "pendente_banco": (row["status"] or "").upper() == "PENDING",
            "pendente_bloqueia_ok": _pendente_bloqueia(row["status"], row["data_local"]),
            "rateios": rateios_ui,
            "rateio_valido": (not rateios_ui) or rateio_valido,
            "valor_rateio": float(abs(valor)),
            "situacoes": situacoes_da_linha(
                conferida=bool(row["conferida"]), duplicada=bool(row["duplicada"]),
                suspeita=tid in ids_suspeitos,
                pendente_banco=(row["status"] or "").upper() == "PENDING",
                substituido=bool(row["substituido_por"]),
                somente_conciliacao=bool(row["somente_conciliacao"]),
                rateio_incompleto=bool(rateio) and bool(faltando),
            ),
            "classes": " ".join(c for c in [
                "conferida" if row["conferida"] else "",
                "duplicada" if row["duplicada"] else "",
                "fora-resultado" if fora else "",
                "pendente-banco" if (row["status"] or "").upper() == "PENDING" else "",
                "suspeita-dup" if tid in ids_suspeitos else "",
            ] if c),
            "natureza_estado": "dre" if natureza in ("despesa", "receita") else "fora",
            "natureza_rotulo": NATUREZAS.get(natureza, natureza),
            "estado": "periodo",
            "_substituido_por": str(row["substituido_por"]) if row["substituido_por"] else None,
            "_tecnico_de": principal_por_tecnico.get(tid),
        }
        linhas.append(linha)
        por_id[linha["id"]] = linha

    # Recolhe o que esta fora do resultado sob o lancamento que conta. O
    # vinculo vem do BANCO (`substituido_por` / vinculo de fatura): nunca
    # agrupamos so porque data, descricao ou valor parecem iguais.
    principais = []
    for linha in linhas:
        alvo_id = linha["_substituido_por"] or linha["_tecnico_de"]
        alvo = por_id.get("t-" + alvo_id) if alvo_id else None
        if alvo is not None and alvo is not linha:
            tecnico = linha["principal"]
            tecnico["principal"], tecnico["tecnico"] = False, True
            alvo["vinculos"].append(tecnico)
            alvo["multiplos"] = True
        else:
            principais.append(linha)
    linhas = principais

    for linha in linhas:
        linha["situacoes_texto"] = texto_das_situacoes(linha["situacoes"])

    resumo, por_categoria = resumo_do_periodo(cur, inicio_mes, fim_mes, origem_sel, periodo)
    receita = resumo["receita_mes"] or 0
    gasto = resumo["gasto_real"] or 0
    total_reais = resumo["total_reais"] or 0
    config = config_da_tela(
        obrigatorias, projeto_portfolio_map, ids_dimensoes, categorias,
        dimensoes, valores_por_dim, pode_conferir=pode("lancamentos_conferir"))
    return render_template(
        "lancamentos_fatura.html", titulo="Lançamentos",
        topbar=topbar_html("Lançamentos", "inicio"),
        modo_periodo=True,
        # O filtro Fatura so aparece quando UMA origem esta selecionada e ela e
        # cartao com fatura importada - o liga/desliga que se autogerencia.
        faturas_da_origem=faturas_para_o_filtro(cur, origem_sel, contas_by_id),
        status_opcoes=opcoes_de_status(),
        url_do_periodo="",
        filtros_situacao=filtros_por_situacao([
            ("conferida", "Conferido"),
            ("pendente_banco", "Pendente no banco"),
            ("duplicidade", "Possível duplicidade"),
            ("fora_resultado", "Fora do resultado"),
            ("rateio_incompleto", "Rateio incompleto"),
        ]),
        fatura={"id": "periodo", "periodo": True, "em_andamento": False, "previsto": False},
        fatura_nova=None, fatura_antiga=None, faturas=[],
        conta=None, avatar_banco=None, contas_credito=contas_credito,
        account_id="", linhas=linhas,
        categorias=[{"chave": c, "nome": cat_pt_puro(c)} for c in categorias],
        dimensoes=dimensoes, valores_por_dim=valores_por_dim, status=status,
        mes=mes, periodo=periodo, data_inicio=data_inicio_str, data_fim=data_fim_str,
        # o formulario de lancamento manual nasce com a data de hoje
        hoje_iso=datetime.now().strftime("%Y-%m-%d"),
        origem_filtro_html=chip_origem_html(
            contas_by_id, origem_opcoes, origem_sel,
            onchange="aplicarFiltrosPeriodo()", contagens=qtd_por_origem,
        ),
        por_categoria=por_categoria,
        receita_mes=receita, gasto_real=gasto, resultado_mes=receita - gasto,
        total_reais=total_reais,
        total_recebidos=resumo["total_recebidos"] or 0,
        total_fora=max((resumo["total_recebidos"] or 0) - total_reais, 0),
        conf_reais=resumo["conferidos_reais"] or 0,
        pendente_classificacao=resumo["pendente_classificacao"] or 0,
        # Numero derivado se calcula AQUI: aritmetica em Jinja sobre variavel
        # ausente levanta UndefinedError e derruba a tela inteira.
        classificados_reais=max(total_reais - (resumo["pendente_classificacao"] or 0), 0),
        pendentes_ok=max(total_reais - (resumo["conferidos_reais"] or 0), 0),
        pct_classificados=_pct(total_reais - (resumo["pendente_classificacao"] or 0), total_reais),
        pct_conferidos=_pct(resumo["conferidos_reais"] or 0, total_reais),
        totais={}, contagens={},
        config_json=json_script(config), projeto_portfolio_map=projeto_portfolio_map,
        pode_editar=pode("lancamentos_editar"), pode_conferir=pode("lancamentos_conferir"),
        pode_regras=pode("cadastros"), pode_manual=pode("lancamentos_manual"),
    )


def _conta_credito_padrao(cur, contas_credito):
    """Cartao aberto quando a URL nao diz qual.

    A primeira conta da lista pode nao ter nenhuma fatura importada — e ai a
    tela abria vazia, sem seletor, sem saida. Prefere o cartao que ja tem
    fatura; se nenhum tiver, mantem o primeiro.
    """
    ids = [c[0] for c in contas_credito]
    cur.execute(
        "SELECT account_id::text AS account_id FROM cartao.fatura_importada "
        "WHERE account_id::text = ANY(%s) "
        "ORDER BY ano_referencia DESC, mes_referencia DESC, id DESC LIMIT 1;",
        (ids,),
    )
    linha = cur.fetchone()
    return linha["account_id"] if linha else ids[0]


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
    # Regras sao do lancamento, nao da tela: abrir a tela aplica as regras
    # automaticas pendentes, como a Resumida fazia antes de sair.
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
    # Pendente que o banco ja confirmou com outro id vira registro tecnico do
    # confirmado (secao 4.3): sem isto o mesmo debito conta duas vezes no DRE.
    # Roda aqui pelo mesmo motivo das regras: o worker so grava, e e a proxima
    # abertura que organiza o que ele trouxe.
    pendentes_resultado = vincular_pendentes_confirmados(cur)
    conn.commit()
    if pendentes_resultado["erro"]:
        registrar_auditoria(
            "pendente_confirmado", "classificacao", sucesso=False,
            detalhes=pendentes_resultado,
        )
    contas_by_id, origem_opcoes = carregar_origens(cur)
    contas_credito = [o for o in origem_opcoes if contas_by_id[o[0]]["tipo"] == "CREDIT"]
    # O recorte por PERIODO e o padrao (decisao do usuario, 09/09/2026): e o
    # unico que alcanca conta corrente, dinheiro e lancamento manual - nenhum
    # deles pertence a fatura nenhuma. Abrindo por fatura, o seletor lista so
    # cartoes de credito, e quem nao conhecesse o alternador concluiria que a
    # tela nao chega nessas origens.
    #
    # Cai no recorte por fatura quando alguem PEDE uma: `recorte=fatura`, um
    # `fatura_id`, o ciclo em andamento ou um cartao especifico. Assim todo
    # link que ja existia continua chegando onde chegava.
    recorte = request.args.get("recorte")
    pediu_fatura = bool(
        recorte == "fatura"
        or request.args.get("fatura_id")
        or request.args.get("andamento")
        or request.args.get("account_id")
    )
    if recorte == "periodo" or not pediu_fatura:
        resposta = _render_periodo(cur, contas_by_id, origem_opcoes, contas_credito)
        cur.close()
        conn.close()
        return resposta
    account_id = request.args.get("account_id") or ""
    fatura_id = request.args.get("fatura_id", type=int)
    em_andamento = request.args.get("andamento") == "1"
    # mes=AAAA-MM abre um ciclo PREVISTO (parcela que o Pluggy ja entregou para
    # um mes que ainda nem comecou a ser cobrado). Formato validado aqui: vai
    # direto para a janela da consulta.
    mes_futuro = request.args.get("mes") or ""
    if not re.fullmatch(r"\d{4}-\d{2}", mes_futuro):
        mes_futuro = None

    if not account_id and contas_credito:
        account_id = _conta_credito_padrao(cur, contas_credito)
    if em_andamento:
        resposta = _render_fatura_em_andamento(
            cur, account_id, contas_credito, contas_by_id, origem_opcoes,
            mes_futuro=mes_futuro)
        if resposta is not None:
            cur.close()
            conn.close()
            return resposta
    if not fatura_id and "fatura_id" not in request.args:
        resposta = _render_fatura_em_andamento(
            cur, account_id, contas_credito, contas_by_id, origem_opcoes)
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
            account_id = _conta_credito_padrao(cur, contas_credito)
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

    # As setas seguem a MESMA ordem do seletor, entao andam pelos meses
    # previstos e pelo ciclo em andamento tambem - antes paravam na fatura mais
    # nova e o resto so era alcancavel pelo seletor.
    faturas = lista_de_faturas(cur, account_id)
    for item in faturas:
        item["selecionada"] = str(item["id"]) == str(fatura_id)
    fatura_nova, fatura_antiga = _vizinhas_no_seletor(faturas, fatura_id)

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
        f"COALESCE(t.importado,false) AS importado, "
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
    nomes_por_dim = {
        d["id"]: {v["id"]: rotulo_valor_dimensao(v) for v in valores_por_dim.get(d["id"], [])}
        for d in dimensoes
    }

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
        # As partes saem do mesmo nucleo das outras duas telas: a fatura passou
        # a mostrar e a editar rateio (decisao do usuario, 10/09/2026), e uma
        # terceira consulta propria divergiria na primeira regra nova.
        partes_por_tx = partes_do_rateio(cur, sorted(set(todos_ids)))
        for tid, partes in partes_por_tx.items():
            rateados.add(tid)
            resumo_rateios[tid] = {
                "soma": sum(
                    (abs(Decimal(str(parte["valor_brl"] or 0))) for parte in partes),
                    Decimal("0.00"),
                ).quantize(Decimal("0.01")),
            }

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
    conta_da_fatura = contas_by_id.get(account_id) or {}
    for linha in linhas:
        linha["pagamento"] = _eh_pagamento_fatura(linha["descricao"])
        # Toda linha nasce com o contrato completo: o template percorre
        # `linha.rateios`, e iterar um valor ausente levanta UndefinedError e
        # derruba a TELA INTEIRA, nao so o pedaco (secao 7.2-B).
        linha["rateios"], linha["rateio_valido"] = [], True
        linha["valor_rateio"] = 0.0
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
            v["fonte"], v["fonte_nome"] = procedencia_do_registro(
                conta_da_fatura, v.get("importado"), v["transacao_id"] == criado)
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
        # Na fatura fechada o titular vem do PDF, entao "aguardando" nunca
        # se aplica aqui - so na fatura em andamento, onde a unica fonte e
        # o metadado do Pluggy.
        linha["cartao_aguardando"] = False
        linha["cartao_nome"] = (
            nomes_cartao.get(final_cartao) or (f"final {final_cartao}" if final_cartao else None)
        )
        # A coluna Origem existe nos DOIS recortes (a tabela e a mesma). Numa
        # fatura ela nasce oculta - todas as linhas sao do mesmo cartao - mas o
        # final4 separa os cartoes fisicos/virtuais da mesma conta, entao a
        # origem e por linha. Linha de fatura nunca tem autor: ninguem digitou.
        (linha["origem_selo"], linha["origem_texto"],
         linha["origem_completa"]) = origem_da_linha(
            conta_da_fatura, final_cartao, nomes_cartao)
        linha["autor"] = None
        # Mesmo contrato de linha do recorte por periodo: o template le sempre os
        # mesmos campos. Aqui o titular vem impresso no documento, que e a fonte
        # sobre quem realizou a compra (secao 5).
        linha["procedencia"] = (linha["titular"] or "Titular não informado") + (
            " · " + linha["cartao_nome"] if linha["cartao_nome"] else "")
        linha["valor_fmt"] = f"R$ {valor_pt(Decimal(str(linha['valor'] or 0)))}"
        linha["cor_valor"] = ""
        principal_da_linha = linha.get("principal") or {}
        linha["pendente_bloqueia_ok"] = _pendente_bloqueia(
            principal_da_linha.get("status"), principal_da_linha.get("data_local"))
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
            # Lancamento manual ou nascido de arquivo pode ser excluido; o que
            # veio do Pluggy nunca (secao 9.3) - a mesma regra do periodo.
            principal["pode_excluir"] = bool(principal.get("importado"))
            # Ratear vale nos DOIS recortes (decisao do usuario, 10/09/2026):
            # compra de cartao se rateia como qualquer outra, e ate aqui a
            # fatura era a unica tela que nao deixava.
            linha["rateios"], valido_do_rateio = rateio_da_linha(
                partes_por_tx.get(tid, []), principal["valor"], dimensoes,
                nomes_por_dim, obrigatorias)
            rateio_valido[tid] = valido_do_rateio
            linha["rateio_valido"] = (not linha["rateios"]) or valido_do_rateio
            linha["valor_rateio"] = float(
                abs(Decimal(str(principal["valor"] or 0)).quantize(Decimal("0.01"))))
            # Natureza neutra (pagamento de fatura, transferencia, bem,
            # investimento) nao participa do resultado: cobrar dimensao dela so
            # cria pendencia que nunca sera resolvida (secao 4.1).
            exige = exige_dimensoes(principal["natureza_efetiva"])
            obrig_linha = obrigatorias if exige else set()
            # publica a decisao para o template e para o JS: quem PINTA a
            # pendencia tem que ler a mesma regra de quem a CALCULA.
            principal["exige_dimensoes"] = exige
            completa = (
                rateio_valido.get(tid, False) if principal["rateado"] else
                bool(principal["categoria"]) and obrig_linha.issubset({k for k, v in principal["dims"].items() if v})
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
                    nomes_dimensoes[dim_id] for dim_id in obrig_linha
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
        principal_linha = linha.get("principal") or {}
        linha["situacoes"] = situacoes_da_linha(
            conferida=bool(linha["conferida"]),
            pendente_banco=(principal_linha.get("status") or "").upper() == "PENDING",
            rateio_incompleto=principal_linha.get("rateado") and "Rateio" in linha.get("faltando", []),
            requer_validacao=linha["validacao_motivos"] if linha["requer_validacao"] else None,
            sem_vinculo=linha["estado"] == "sem_vinculo",
        )
        linha["classes"] = " ".join(c for c in [
            "conferida" if linha["conferida"] else "",
            "pendente-banco" if (principal_linha.get("status") or "").upper() == "PENDING" else "",
        ] if c)

    for linha in linhas:
        linha["situacoes_texto"] = texto_das_situacoes(linha.get("situacoes", []))

    # "Gasto por categoria" existe nos DOIS recortes (decisao do usuario,
    # 10/09/2026). Aqui ele soma os lancamentos que a FATURA cobrou - nao a
    # janela de datas -, entao bate com o "Despesas no DRE" da propria tela;
    # somar por data traria compras de outro ciclo e o quadro contradiria o
    # card ao lado.
    ids_principais = sorted({
        str(l["principal"]["transacao_id"]) for l in linhas if l.get("principal")
    })
    por_categoria = gasto_por_categoria(
        cur, ["t.transacao_id::text = ANY(%s)"], [ids_principais]
    ) if ids_principais else []

    status = request.args.get("status", "todas")
    # `pendente_ok` era o nome antigo deste filtro aqui; o periodo sempre o
    # chamou de `pendente`. Link antigo continua funcionando, e o seletor passa a
    # mostrar a opcao certa em vez de "Todas" com a lista filtrada.
    if status == "pendente_ok":
        status = "pendente"
    # O conjunto aceito sai da MESMA lista que o seletor oferece. Escritos a
    # parte, os dois divergiram: o seletor passou a oferecer `pendente` e a rota
    # so entendia `pendente_ok` - o card "OK dos lancamentos" caia em "Todas"
    # sem avisar.
    if status not in STATUS_COM_FATURA:
        status = "todas"
    linhas_visiveis = [l for l in linhas if (
        status == "todas" or
        (status == "pendente_classificacao" and not l["pagamento"] and not l["classificada"]) or
        (status == "pendente" and not l["pagamento"] and not l["conferida"]) or
        (status == "dre" and l.get("natureza_estado") in {"dre", "misto"}) or
        (status == "fora" and l.get("natureza_estado") in {"fora", "misto"}) or
        (status == "sem_vinculo" and l["estado"] == "sem_vinculo") or
        (status == "requer_validacao" and l["requer_validacao"]) or
        (status == "multiplos" and l["multiplos"]) or
        (status == "conferida" and tem_situacao(l, "conferida")) or
        (status == "pendente_banco" and tem_situacao(l, "pendente-banco")) or
        (status == "rateio_incompleto" and tem_situacao(l, "rateio"))
    )]
    filtros_situacao = filtros_por_situacao([
        ("conferida", "Conferido"),
        ("pendente_banco", "Pendente no banco"),
        ("sem_vinculo", "Cobrança sem lançamento vinculado"),
        ("requer_validacao", "Requer validação"),
        ("rateio_incompleto", "Rateio incompleto"),
        ("fora", "Fora do DRE"),
    ])

    config = config_da_tela(
        obrigatorias, projeto_portfolio_map, ids_dimensoes, categorias,
        dimensoes, valores_por_dim, pode_conferir=pode("lancamentos_conferir"))
    conta = contas_by_id.get(account_id)
    # Sair da fatura leva ao PERIODO DELA - o ciclo e o recorte que o usuario
    # esta olhando. Sem ciclo no arquivo, cai no mes de referencia.
    if fatura.get("periodo_inicio") and fatura.get("periodo_fim"):
        janela = (
            "periodo=intervalo&data_inicio=" + fatura["periodo_inicio"].isoformat()
            + "&data_fim=" + fatura["periodo_fim"].isoformat()
        )
        mes_da_fatura = fatura["periodo_fim"].strftime("%Y-%m")
        periodo_da_fatura, inicio_da_fatura, fim_da_fatura = (
            "intervalo", fatura["periodo_inicio"].isoformat(), fatura["periodo_fim"].isoformat())
    else:
        mes_da_fatura = f"{fatura['ano_referencia']}-{fatura['mes_referencia']:02d}"
        janela = "periodo=mes&mes=" + mes_da_fatura
        periodo_da_fatura, inicio_da_fatura, fim_da_fatura = "mes", "", ""
    url_do_periodo = (
        "/lancamentos/fatura?recorte=periodo&" + janela
        + "&origem=" + account_id + "&status=todas"
    )
    faturas_da_origem = faturas_para_o_filtro(cur, [account_id], contas_by_id, fatura_id)
    filtro_origem = chip_origem_html(
        contas_by_id, origem_opcoes, [account_id],
        onchange="aplicarFiltrosPeriodo()")
    cur.close()
    conn.close()
    return render_template(
        "lancamentos_fatura.html", titulo="Lançamentos por fatura",
        topbar=topbar_html("Lançamentos", "inicio"), fatura=fatura,
        fatura_nova=fatura_nova, fatura_antiga=fatura_antiga,
        faturas=faturas, conta=conta, contas_credito=contas_credito,
        avatar_banco=cor_banco((conta or {}).get("banco")),
        account_id=account_id, linhas=linhas_visiveis, categorias=categorias_template,
        dimensoes=dimensoes, valores_por_dim=valores_por_dim, status=status,
        totais={
            "pdf": total_pdf, "dre": total_dre, "fora": total_fora,
            "pendente": total_pendente, "pendente_ok": total_pendente_ok,
            "sem_vinculo": total_sem_vinculo, "divergencia": total_divergencia,
        },
        contagens=contagens, config_json=json_script(config),
        projeto_portfolio_map=projeto_portfolio_map,
        por_categoria=por_categoria, filtros_situacao=filtros_situacao,
        # A barra de filtros e a MESMA nos dois recortes e se autogerencia.
        origem_filtro_html=filtro_origem, faturas_da_origem=faturas_da_origem,
        status_opcoes=opcoes_de_status(com_fatura=True),
        url_do_periodo=url_do_periodo,
        mes=mes_da_fatura, periodo=periodo_da_fatura,
        data_inicio=inicio_da_fatura, data_fim=fim_da_fatura,
        pode_editar=pode("lancamentos_editar"), pode_conferir=pode("lancamentos_conferir"),
        pode_regras=pode("cadastros"), pode_manual=pode("lancamentos_manual"),
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

        # O OK e assinatura humana: so vai marcado quando QUEM ESTA CRIANDO
        # marcou a caixa, e ainda assim passa pela mesma trava da tela - sem
        # classificacao completa, nao ha OK (secoes 1.2 e 7.2). O lancamento e
        # criado do mesmo jeito; so a assinatura fica de fora, e a resposta diz
        # o que faltou para a tela apontar o campo.
        conn = get_conn()
        cur = conn.cursor()

        dimensoes = data.get("dimensoes") or {}
        observacao = (data.get("observacao") or "").strip() or None
        quer_conferir = bool(data.get("conferida")) and pode("lancamentos_conferir")

        # Mesma regra da secao 4.1: natureza neutra nao exige dimensao. Sem
        # isto, um lancamento manual de transferencia entre contas proprias ou
        # de compra de um bem nunca poderia receber OK - a trava cobraria um
        # Responsavel/Projeto/Portfolio que nao existe e nao faz sentido.
        cur.execute(
            "SELECT natureza FROM cartao.categoria_natureza WHERE categoria = %s;",
            (categoria,),
        )
        linha_natureza = cur.fetchone() if categoria else None
        exige_dims = exige_dimensoes(linha_natureza[0] if linha_natureza else None)
        cur.execute("SELECT id FROM cartao.dimensao WHERE obrigatoria = true;")
        obrigatorias = [str(r[0]) for r in cur.fetchall()] if exige_dims else []
        faltando_ids = [d for d in obrigatorias if not str(dimensoes.get(d) or "").strip()]
        falta_categoria = not categoria
        if quer_conferir and (faltando_ids or falta_categoria):
            # Recusar e melhor que criar e descartar o OK em silencio: a pessoa
            # marcou a caixa porque quer assinar. Ela completa os campos, ou
            # desmarca e salva sem assinatura - as duas saidas sao explicitas.
            cur.close()
            conn.close()
            conn = cur = None
            return jsonify({
                "ok": False,
                "erro": "Para marcar como conferido, preencha Categoria e as dimensões obrigatórias.",
                "faltando_ids": faltando_ids, "falta_categoria": falta_categoria,
            }), 400
        conferida = quer_conferir

        transacao_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO cartao.transacao ("
            "transacao_id, account_id, descricao, descricao_bruta, valor_original, moeda_original, "
            "valor_brl, data_transacao, categoria, categoria_manual, observacao, "
            "conferida, conferida_por, conferida_em, status, tipo, "
            "criado_em, atualizado_em, sincronizado_em, criado_por"
            ") VALUES (%s,%s,%s,%s,%s,'BRL',%s,%s,%s,%s,%s,%s,%s,%s,'POSTED',%s, now(), now(), now(), %s);",
            (
                transacao_id, CONTA_MANUAL_ID, descricao, descricao,
                valor, valor, data_transacao, categoria, bool(categoria), observacao,
                conferida, session.get("user") if conferida else None,
                datetime.now() if conferida else None, tipo, session.get("user"),
            ),
        )
        for dim_id, valor_id in dimensoes.items():
            if not str(valor_id or "").strip():
                continue
            cur.execute(
                "INSERT INTO cartao.transacao_dimensao (transacao_id, dimensao_id, valor_id) "
                "VALUES (%s,%s,%s) ON CONFLICT (transacao_id, dimensao_id) "
                "DO UPDATE SET valor_id = EXCLUDED.valor_id;",
                (transacao_id, int(dim_id), int(valor_id)),
            )
        conn.commit()
        transacao_encerrada = True
        registrar_auditoria(
            "alteracao", "Lançamento manual criado",
            detalhes={"transacao_id": transacao_id, "descricao": descricao,
                      "valor": float(valor), "conferida": conferida},
        )
        return jsonify({
            "ok": True, "transacao_id": transacao_id, "conferida": conferida,
            "faltando_ids": faltando_ids, "falta_categoria": falta_categoria,
        })
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
                "erro": "Só é possível excluir lançamentos manuais ou importados de arquivo. Este veio da sincronização com o banco e voltaria na próxima atualização — se ele repete um lançamento que já existe, aponte a substituição nos detalhes.",
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
        origem = f'{conta["label"]} · {apelido}'
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
        "valor": f'R$ {valor_pt(float(r["valor"] or 0))}',
        "valor_original": (
            f'{valor_pt(float(r["valor_original"]))} {r["moeda_original"] or ""}'.strip()
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
        # OK do PAI a partir das partes (decisao do usuario, 07/09/2026).
        # Num rateado a classificacao mora nas partes: exigir um clique a mais
        # no pai nao acrescenta conferencia nenhuma - quem conferiu as partes
        # conferiu o lancamento. Tres condicoes, como o OK da fatura (secao
        # 1.2): acao humana explicita (o pedido traz `conferir`), rateio
        # completo e valido, e permissao de conferir. Nunca DESMARCA e nunca
        # sobrescreve assinatura que ja existe.
        conferiu_pai = False
        if data.get("conferir") and not transacao[1] and pode("lancamentos_conferir"):
            cur.execute("SELECT id FROM cartao.dimensao WHERE obrigatoria=true;")
            obrigatorias = [row[0] for row in cur.fetchall()]
            completo = all(
                parte["categoria"]
                and all(parte["dimensoes"].get(dim_id) is not None for dim_id in obrigatorias)
                for parte in partes
            )
            if completo and len(partes) >= 2:
                cur.execute(
                    "UPDATE cartao.transacao SET conferida=true, conferida_por=%s, "
                    "conferida_em=now() WHERE transacao_id=%s AND conferida=false;",
                    (session.get("user"), transacao_id),
                )
                conferiu_pai = cur.rowcount > 0
        conn.commit()
        depois = _estado_rateios(cur, transacao_id)
        registrar_mudanca_auditoria("Rateio", antes or None, depois)
        return jsonify({"ok": True, "rateios": depois, "conferida": conferiu_pai or bool(transacao[1])})
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
        # A trava so cobra dimensao de lancamento que participa do resultado.
        # Sem isto, um pagamento de fatura (natureza neutra) nunca poderia
        # receber OK: as dimensoes dele nao existem e nao fazem sentido (4.1).
        cur.execute(
            "SELECT d.nome FROM cartao.dimensao d "
            "LEFT JOIN cartao.transacao_dimensao td ON td.dimensao_id = d.id AND td.transacao_id = %s "
            "WHERE d.obrigatoria = true AND (td.valor_id IS NULL) AND EXISTS ("
            "  SELECT 1 FROM cartao.transacao t "
            "  LEFT JOIN cartao.categoria_natureza n ON n.categoria = t.categoria "
            f"  WHERE t.transacao_id = %s AND {EXIGE_DIMENSOES_SQL});",
            (transacao_id, transacao_id),
        )
        faltando = [r[0] for r in cur.fetchall()]
        categoria_final = (
            (data.get("categoria") or None) if "categoria" in data else transacao[3]
        )
        if not categoria_final:
            faltando.append("categoria")
    # A natureza vem da CATEGORIA, entao trocar a categoria pode trocar a
    # obrigatoriedade das dimensoes. O cliente nao tem como deduzir isso
    # sozinho: quem responde e o servidor, depois da gravacao.
    cur.execute(
        "SELECT " + EXIGE_DIMENSOES_SQL + " FROM cartao.transacao t "
        "LEFT JOIN cartao.categoria_natureza n ON n.categoria = t.categoria "
        "WHERE t.transacao_id = %s;",
        (transacao_id,),
    )
    linha_exige = cur.fetchone()
    exige_dims = bool(linha_exige[0]) if linha_exige else True
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
    # `duplicada` NAO e' mais gravavel. A marcacao saiu da interface em
    # 02/09/2026: pela secao 4.3 ela e' o estado que SOBRA - mesma cobranca duas
    # vezes, sem estorno e sem par identificavel -, e na pratica todo caso real
    # tinha par e era `substituido_por`, que diz qual registro conta e tem
    # caminho de volta. O campo enviado por uma tela antiga e ignorado de
    # proposito, em vez de dar erro.
    #
    # A COLUNA continua existindo e continua excluindo do resultado: ela faz
    # parte da definicao de `elegivel`, e tirar isso agora faria consultas que
    # hoje excluem duplicados passarem a inclui-los em silencio.
    if "observacao" in data:
        sets.append("observacao = %s")
        valores.append(data.get("observacao"))
    if "descricao" in data:
        # SO em lancamento manual. A descricao de lancamento do Pluggy pertence
        # ao banco (secao 4.6): editar ali falsificaria o registro bancario e
        # ainda seria desfeito na proxima sincronizacao. O filtro vai no proprio
        # UPDATE para que nem uma chamada direta a API alcance outra origem.
        nova_descricao = (data.get("descricao") or "").strip()
        if nova_descricao:
            sets.append("descricao = %s")
            valores.append(nova_descricao)
    if "categoria" in data:
        # Uma escolha humana, mesmo antes de marcar OK, não pode ser desfeita
        # por uma regra automática criada posteriormente.
        sets.extend(["categoria = %s", "categoria_manual = true", "regra_aplicada_id = NULL"])
        valores.append(data.get("categoria") or None)
    if "natureza" in data:
        sets.append("natureza = %s")
        valores.append(natureza)

    if sets:
        # os trechos do SET sao literais fixos daqui; so os valores vao por parametro.
        # A trava da descricao vai no WHERE, e nao so na tela: assim uma chamada
        # direta a API tambem nao consegue reescrever a descricao de um
        # lancamento do Pluggy.
        escopo = " AND account_id = %s" if "descricao" in data else ""
        extra = [CONTA_MANUAL_ID] if "descricao" in data else []
        # Sem isto, "Ultima alteracao" no modal mostrava a hora de CRIACAO para
        # sempre: migracoes e rotinas em lote gravam `atualizado_em`, mas a rota
        # que o usuario usa para editar nunca gravava. O campo dizia a verdade
        # sobre a coluna e mentia sobre o dado.
        cur.execute(
            "UPDATE cartao.transacao SET "
            + ", ".join(sets + ["atualizado_em = now()"])
            + f" WHERE transacao_id = %s{escopo};",
            valores + [transacao_id] + extra,
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
    # Depois do commit e ANTES de fechar. Esta consulta rodava com o cursor e a
    # conexao ja fechados e derrubava a rota inteira com 500 - qualquer edicao,
    # nao so a descricao. Ler aqui devolve a hora DESTA edicao, nao a anterior.
    cur.execute(
        "SELECT atualizado_em FROM cartao.transacao WHERE transacao_id=%s;", (transacao_id,)
    )
    _atualizado = cur.fetchone()
    atualizado_em_fmt = (
        data_hora_local(_atualizado[0]).strftime("%d/%m/%Y %H:%M")
        if _atualizado and _atualizado[0] else "-"
    )
    cur.close()
    conn.close()
    conferida_por = (
        session.get("user") if "conferida" in data and conferida_final
        else (None if "conferida" in data else transacao[1])
    )
    # O estado nunca muda mais por aqui: a resposta devolve o que esta no banco,
    # para a tela nao inferir nada por conta propria.
    duplicada_final = bool(transacao[2])
    if "conferida" in data:
        registrar_mudanca_auditoria("Conferida", bool(transacao[0]), conferida_final)
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
        # Quem pinta a pendencia na tela le esta flag, nunca a lista global de
        # dimensoes obrigatorias: natureza neutra nao exige dimensao (secao 4.1).
        "exige_dimensoes": exige_dims,
        # A hora vem do servidor, nunca do relogio do navegador: o modal mostra
        # "Ultima alteracao" e um relogio adiantado ali seria uma mentira sutil.
        "atualizado_em": atualizado_em_fmt,
        "rateio_invalido": rateio_invalido,
        "pendente_banco": pendente_banco,
        "sem_pdf_conciliado": sem_pdf_conciliado,
        # A tela sincroniza estes dois estados depois de QUALQUER edicao. Assim
        # nunca mostra OK/duplicidade diferentes do que esta salvo no banco.
        "conferida": conferida_final,
        "conferida_por": conferida_por,
        "duplicada": duplicada_final,
    })
