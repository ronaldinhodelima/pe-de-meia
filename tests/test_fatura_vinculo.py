"""Casamento fatura x Pluggy e o bloqueio por vinculo ja existente.

Estes testes reproduzem os casos REAIS que quebraram a conciliacao em
producao (agosto/2026), com dados sinteticos no mesmo formato:

- AQUAMATER: a mesma compra parcelada gera UMA cobranca por mes, de mesmo
  valor. Sem bloqueio, a fatura de agosto "roubava" a transacao de julho e
  ambas apareciam como sobra.
- Parcelamento agregado: o Pluggy as vezes grava o parcelamento inteiro como
  UMA transacao (valor cheio, data da compra). Essa transacao atende uma
  linha por mes, em faturas diferentes - o bloqueio NAO pode impedir isso.

O cursor e' dublado porque _conciliar_linhas faz uma unica consulta; assim
o teste roda sem PostgreSQL.
"""
from datetime import date

from decimal import Decimal

from views.relatorios import (
    _centavos,
    _conciliar_linhas,
    _decimal_monetario,
    _melhor_agregado,
    _reais,
)


def test_dinheiro_da_conciliacao_usa_centavos_exatos():
    assert _centavos(0.1 + 0.2) == 30
    assert _reais(sum(_centavos(Decimal("0.01")) for _ in range(1000))) == 10.0
    assert _decimal_monetario(Decimal("1.005")) == Decimal("1.01")


def test_tolerancia_do_agregado_termina_exatamente_em_um_real():
    base = {"_usado": False, "parcela_total": 2, "descricao": "LOJA"}
    dentro = {**base, "_valor_centavos": 10100}
    fora = {**base, "_valor_centavos": 10101}

    assert _melhor_agregado([dentro], 10000, 2, "LOJA") is dentro
    assert _melhor_agregado([fora], 10000, 2, "LOJA") is None


def test_agregado_exige_estabelecimento_compativel_e_nao_so_valor():
    mercado = {
        "_usado": False, "parcela_total": None, "_valor_centavos": 16400,
        "descricao": "Parcelado Lojista - Visa - MERCADOLIVRE*COM CURITIBA BR",
    }
    pizzaria = {
        "_usado": False, "parcela_total": None, "_valor_centavos": 16400,
        "descricao": "A vista sem juros - Visa - YELLOW BOX PIZZARIA VIDEIRA BR",
    }
    assert _melhor_agregado(
        [pizzaria, mercado], 16400, 5, "MERCADOLIVRE*COMPRAS"
    ) is mercado
    assert _melhor_agregado([pizzaria], 16400, 5, "MERCADOLIVRE*COMPRAS") is None


class CursorFake:
    """Devolve sempre a mesma lista de candidatos, no formato da consulta real."""

    def __init__(self, candidatos):
        self._candidatos = candidatos

    def execute(self, *args, **kwargs):
        return None

    def fetchall(self):
        return [dict(c) for c in self._candidatos]


def _transacao(transacao_id, dia, descricao, valor, parcela_total=None):
    return {
        "transacao_id": transacao_id,
        "data_transacao": dia,
        "descricao": descricao,
        "parcela_total": parcela_total,
        "valor": valor,
        "data_local": dia,
    }


def _linha(dia, descricao, valor, parcela_atual=None, parcela_total=None, titular="Ronaldo De Lima"):
    return {
        "data": dia,
        "descricao": descricao,
        "descricao_base": descricao.split(" Parc.")[0],
        "parcela_atual": parcela_atual,
        "parcela_total": parcela_total,
        "valor": valor,
        "titular": titular,
    }


def test_avulsa_casa_pelo_valor_e_data_proxima():
    candidatos = [_transacao("t1", date(2026, 8, 5), "A vista sem juros - Visa - MERCADO", 120.50)]
    linhas = [_linha(date(2026, 8, 5), "MERCADO", 120.50)]
    r = _conciliar_linhas(CursorFake(candidatos), "conta-1", linhas)
    assert len(r["batidos"]) == 1
    assert r["batidos"][0]["transacao_id"] == "t1"
    assert r["sem_sistema"] == []


def test_parcela_casa_uma_por_uma_dentro_do_ciclo():
    """A fatura traz so a parcela do mes; o Pluggy manda uma transacao de mesmo
    valor. A data impressa e' a da COMPRA ORIGINAL (bem no passado), entao o
    casamento nao pode depender de proximidade de data."""
    candidatos = [_transacao("ago", date(2026, 8, 12), "Parcela Lojista Visa - AQUAMATER", 129.00)]
    linhas = [_linha(date(2025, 11, 12), "AQUAMATER Parc.9/12", 129.00, 9, 12)]
    r = _conciliar_linhas(CursorFake(candidatos), "conta-1", linhas, ciclo_inicio_min=date(2026, 7, 10), ciclo_fim_real=date(2026, 8, 12))
    assert [b["transacao_id"] for b in r["batidos"]] == ["ago"]


def test_transacao_ja_vinculada_a_outra_fatura_nao_e_roubada():
    """Caso AQUAMATER real: julho e agosto tem cobranca identica em valor. Se a
    de julho ja tem vinculo, agosto nao pode reivindica-la - tem que ficar com
    a sua propria e deixar a de julho em paz."""
    candidatos = [
        _transacao("jul", date(2026, 7, 12), "Parcela Lojista Visa - AQUAMATER", 129.00),
        _transacao("ago", date(2026, 8, 12), "Parcela Lojista Visa - AQUAMATER", 129.00),
    ]
    linhas = [_linha(date(2025, 11, 12), "AQUAMATER Parc.9/12", 129.00, 9, 12)]
    r = _conciliar_linhas(
        CursorFake(candidatos), "conta-1", linhas,
        ciclo_inicio_min=date(2026, 7, 10), transacoes_bloqueadas={"jul"},
        ciclo_fim_real=date(2026, 8, 12),
    )
    assert [b["transacao_id"] for b in r["batidos"]] == ["ago"], "agosto tem que pegar a de agosto"
    # a de julho nao pode ter sido consumida por esta fatura
    assert "jul" not in [b["transacao_id"] for b in r["batidos"]]


def test_sem_bloqueio_o_mes_errado_seria_escolhido():
    """Prova que o bloqueio e' o que resolve: sem ele, a heuristica escolhe a
    transacao mais recente e as duas cobrancas disputam a mesma linha."""
    candidatos = [
        _transacao("jul", date(2026, 7, 12), "Parcela Lojista Visa - AQUAMATER", 129.00),
        _transacao("ago", date(2026, 8, 12), "Parcela Lojista Visa - AQUAMATER", 129.00),
    ]
    linhas = [_linha(date(2025, 11, 12), "AQUAMATER Parc.9/12", 129.00, 9, 12)]
    r = _conciliar_linhas(CursorFake(candidatos), "conta-1", linhas, ciclo_inicio_min=date(2026, 7, 10), ciclo_fim_real=date(2026, 8, 12))
    # so uma linha, entao so um batido - a outra transacao sobra
    assert len(r["batidos"]) == 1
    assert len(r["sem_fatura"]) == 1


def test_parcelamento_agregado_pode_ser_reusado_mesmo_bloqueado():
    """O Pluggy gravou o parcelamento inteiro como UMA transacao de valor cheio.
    Ela ja esta vinculada a linha de outro mes - e isso e' legitimo, porque a
    mesma transacao representa todas as parcelas. O fallback tem que aceitar."""
    candidatos = [_transacao("cheio", date(2026, 5, 13), "Parcelado Lojista - Visa - SESI", 247.38, 6)]
    linhas = [_linha(date(2026, 5, 13), "SESI FARMACIA Parc.2/6", 41.23, 2, 6)]
    r = _conciliar_linhas(
        CursorFake(candidatos), "conta-1", linhas,
        ciclo_inicio_min=date(2026, 6, 12), transacoes_bloqueadas={"cheio"},
        ciclo_fim_real=date(2026, 7, 9),
    )
    assert [b["transacao_id"] for b in r["batidos"]] == ["cheio"]
    assert r["batidos"][0]["valor_esperado_parcelamento"] == 247.38


def test_pagamento_recebido_fica_fora_das_duas_somas():
    """A linha 'Pagamento Recebido' e' a fatura anterior sendo quitada - o
    proprio SALDO TOTAL da Unicred nao a inclui. Se entrar em um lado so, a
    conciliacao acusa diferenca de dezenas de milhares sem erro nenhum."""
    candidatos = [
        _transacao("pag", date(2026, 8, 1), "Pag de Fatura Via Deb Aut", -16543.97),
        _transacao("t1", date(2026, 8, 5), "MERCADO", 120.50),
    ]
    linhas = [
        _linha(date(2026, 8, 1), "Pagamento Recebido", -16543.97),
        _linha(date(2026, 8, 5), "MERCADO", 120.50),
    ]
    r = _conciliar_linhas(CursorFake(candidatos), "conta-1", linhas)
    assert r["soma_fatura"] == 120.50
    assert r["diferenca"] == 0.0


def test_linha_sem_par_vira_sem_sistema():
    candidatos = []
    linhas = [_linha(date(2026, 8, 5), "TARIFA QUE O PLUGGY NAO MANDA", 39.58)]
    r = _conciliar_linhas(CursorFake(candidatos), "conta-1", linhas)
    assert len(r["sem_sistema"]) == 1
    assert r["batidos"] == []


class CursorEncadeamento:
    """Duble para _ciclo_inicio_encadeado: devolve a fatura anterior pedida."""

    def __init__(self, periodo_fim_anterior):
        self._anterior = periodo_fim_anterior
        self._ultimo = None

    def execute(self, sql, params=None):
        self._ultimo = params

    def fetchone(self):
        if self._anterior is None:
            return None
        return {"periodo_fim": self._anterior}


def test_inicio_do_ciclo_encadeia_com_a_fatura_anterior():
    """O inicio tem que ser o fim da fatura anterior + 1 dia, calculado na
    leitura. Congelar isso no import fazia o resultado depender da ORDEM de
    envio dos PDFs - quem enviasse da mais nova para a mais antiga ficava com
    todas no palpite de 35 dias (aconteceu com as faturas de 2025)."""
    from views.relatorios import _ciclo_inicio_encadeado

    fatura = {
        "account_id": "conta-1", "ano_referencia": 2025, "mes_referencia": 8,
        "periodo_inicio": date(2025, 7, 7),   # palpite de 35 dias, errado
        "periodo_fim": date(2025, 8, 11),
    }
    cur = CursorEncadeamento(date(2025, 7, 10))  # fim da fatura de 07/2025
    assert _ciclo_inicio_encadeado(cur, fatura) == date(2025, 7, 11)


def test_primeira_fatura_da_conta_cai_no_valor_guardado():
    """Sem fatura anterior no banco nao ha o que encadear - mantem o que veio
    do parser, sem inventar data."""
    from views.relatorios import _ciclo_inicio_encadeado

    fatura = {
        "account_id": "conta-1", "ano_referencia": 2025, "mes_referencia": 7,
        "periodo_inicio": date(2025, 6, 5),
        "periodo_fim": date(2025, 7, 10),
    }
    assert _ciclo_inicio_encadeado(CursorEncadeamento(None), fatura) == date(2025, 6, 5)


def test_agregado_tem_prioridade_sobre_a_mensal():
    """Quando o parcelamento TEM agregado no Pluggy (valor cheio), todas as
    parcelas da fatura tem que apontar pra ele - mesmo existindo uma cobranca
    mensal de valor exato dentro do ciclo. Essa mensal e' cobranca a mais (o
    agregado ja cobre as 10 parcelas) e precisa sobrar como orfa, pra aparecer
    como candidata a duplicidade. Caso real: OTICA CALLIARI 10x R$316, agregado
    de R$3.160 em 02/11/2025 + mensais em 12/06, 12/07 e 12/08 de 2026."""
    candidatos = [
        _transacao("agreg", date(2025, 11, 2), "Parcelado Lojista - Visa - OTICA CALLIARI", 3160.00),
        _transacao("mensal", date(2026, 8, 12), "Parcela Lojista Visa - OTICA CALLIARI", 316.00),
    ]
    linhas = [_linha(date(2025, 11, 2), "OTICA CALLIARI Parc.10/10", 316.00, 10, 10)]
    r = _conciliar_linhas(
        CursorFake(candidatos), "conta-1", linhas,
        ciclo_inicio_min=date(2026, 7, 10), ciclo_fim_real=date(2026, 8, 12),
    )
    assert [b["transacao_id"] for b in r["batidos"]] == ["agreg"], "a parcela tem que ir pro agregado"
    assert [s["transacao_id"] for s in r["sem_fatura"]] == ["mensal"], "a mensal tem que sobrar como orfa"


def test_sem_agregado_continua_casando_uma_a_uma():
    """Parcelamento que o Pluggy so mandou mensal a mensal (sem agregado)
    continua no casamento 1:1 - a prioridade do agregado nao pode quebrar isso."""
    candidatos = [_transacao("m1", date(2026, 8, 12), "Parcela Lojista Visa - LOJA", 316.00)]
    linhas = [_linha(date(2025, 11, 2), "LOJA Parc.10/10", 316.00, 10, 10)]
    r = _conciliar_linhas(
        CursorFake(candidatos), "conta-1", linhas,
        ciclo_inicio_min=date(2026, 7, 10), ciclo_fim_real=date(2026, 8, 12),
    )
    assert [b["transacao_id"] for b in r["batidos"]] == ["m1"]


def test_parcela_unica_nao_e_confundida_com_agregado():
    """Com parcela_total=1 o valor cheio e' igual ao da parcela - nao pode
    tratar uma cobranca normal como se fosse parcelamento agregado."""
    candidatos = [_transacao("t1", date(2026, 8, 12), "Compra - Visa - LOJA", 316.00)]
    linhas = [_linha(date(2026, 8, 12), "LOJA Parc.1/1", 316.00, 1, 1)]
    r = _conciliar_linhas(
        CursorFake(candidatos), "conta-1", linhas,
        ciclo_inicio_min=date(2026, 7, 10), ciclo_fim_real=date(2026, 8, 12),
    )
    assert [b["transacao_id"] for b in r["batidos"]] == ["t1"]
    assert "valor_esperado_parcelamento" not in r["batidos"][0]


def test_dois_parcelamentos_do_mesmo_lojista_com_mesmo_numero_de_parcelas():
    """Caso real: na fatura 09/2025 havia MECANICA HOCHIOVE Parc.2/2 de R$135,00
    E Parc.2/2 de R$233,50 - dois parcelamentos diferentes, mesmo lojista, mesmo
    numero de parcelas. Agrupando so por (titular, lojista, parcelas) os dois
    colapsam numa chave: o valor da parcela vira a media (R$184,25), o valor
    cheio esperado vira R$368,50 e NENHUM dos dois agregados (R$270 e R$467)
    e' encontrado. Os dois viravam orfaos. O valor da parcela tem que entrar
    na chave do grupo."""
    candidatos = [
        _transacao("ag270", date(2025, 8, 13), "Parcelado Lojista - Visa - MECANICA HOCHIOVE", 270.00),
        _transacao("ag467", date(2025, 8, 13), "Parcelado Lojista - Visa - MECANICA HOCHIOVE", 467.00),
    ]
    linhas = [
        _linha(date(2025, 8, 11), "MECANICA HOCHIOVE Parc.2/2", 135.00, 2, 2),
        _linha(date(2025, 8, 11), "MECANICA HOCHIOVE Parc.2/2", 233.50, 2, 2),
    ]
    r = _conciliar_linhas(
        CursorFake(candidatos), "conta-1", linhas,
        ciclo_inicio_min=date(2025, 8, 12), ciclo_fim_real=date(2025, 9, 11),
    )
    achados = {b["valor"]: b["transacao_id"] for b in r["batidos"]}
    assert achados == {135.00: "ag270", 233.50: "ag467"}, f"casou errado: {achados}"
    assert r["sem_fatura"] == [], "nenhum agregado pode sobrar como orfao"


def test_avulsa_um_dia_depois_do_fim_do_ciclo_ainda_casa():
    """Caso real D MORI: a fatura 02/2026 imprime a compra em 11/02 (dentro do
    ciclo) e o Pluggy datou 12/02 - um dia depois do fim do ciclo. A janela de
    busca terminava exatamente no fim do ciclo, entao a transacao nem entrava
    como candidata e a compra ficava orfa dos dois lados."""
    candidatos = [_transacao("t1", date(2026, 2, 12), "A vista sem juros - Visa - D MORI", 73.10)]
    linhas = [_linha(date(2026, 2, 11), "D MORI COZINHA AUTORAL", 73.10)]
    r = _conciliar_linhas(
        CursorFake(candidatos), "conta-1", linhas,
        ciclo_inicio_min=date(2026, 1, 26), ciclo_fim_real=date(2026, 2, 11),
    )
    assert [b["transacao_id"] for b in r["batidos"]] == ["t1"]


def test_tokens_significativos_ignora_prefixo_generico():
    """Duas gravacoes do MESMO evento pelo Pluggy diferem no prefixo generico
    ('Compra Exterior R$ -' vs 'Compra Exterior -') e no sufixo. O que
    identifica o par sao os tokens do estabelecimento. Caso real ANTHROPIC:
    duas transacoes de R$110,63 em 04/07/2026 19:13, uma vinculada a fatura e
    outra nao."""
    from views.relatorios import _tokens_significativos

    a = _tokens_significativos("Compra Exterior R$ - Visa - ANTHROPIC* CLAUDE SUB")
    b = _tokens_significativos("Compra Exterior - Visa - ANTHROPIC* CLAUDE SUB ANTHROPIC.COMUS")
    assert "ANTHROPIC" in a and "CLAUDE" in a
    assert len(a & b) >= 2, f"tokens em comum: {a & b}"
    # generico nao pode sustentar um par sozinho
    assert "COMPRA" not in a and "EXTERIOR" not in a and "VISA" not in a


def test_tokens_de_lojistas_diferentes_nao_se_confundem():
    from views.relatorios import _tokens_significativos

    a = _tokens_significativos("A vista sem juros - Visa - SUPERVIZA VIDEIRA BR")
    b = _tokens_significativos("A vista sem juros - Visa - HIPERCENTER VIDEIRA BR")
    assert len(a & b) < 2, f"nao podiam casar: {a & b}"


def test_eco_de_parcelamento_novo_casa_pela_linha_da_fatura():
    """Parcelamento novo: o agregado ainda atende UMA linha so, entao nao e'
    reconhecido como agregado (isso exige 2+ linhas) e o eco escapa das duas
    regras. Casos reais de 08/2026:

      16/07 Parcela Lojista Visa - PARC=106ANJOS DE QUINTA  R$360,00
        linha da fatura: PARC=106ANJOS DE QUINTA Parc.1/6   R$360,00
        ja vinculada ao agregado de 18/07                   R$2.160,00

    Comparar com a descricao do AGREGADO nao funciona ("ANJOS" != "106ANJOS");
    o que casa e' a descricao_base da LINHA da fatura."""
    from views.relatorios import _tokens_significativos

    orfao = _tokens_significativos("Parcela Lojista Visa - PARC=106ANJOS DE QUINTA")
    linha = _tokens_significativos("PARC=106ANJOS DE QUINTA")
    agregado = _tokens_significativos("Parcelado Lojista - Visa - ANJOS DE QUINTAL VIDEIRA BR")
    assert len(orfao & linha) >= 2, f"linha deveria casar: {orfao & linha}"
    assert len(orfao & agregado) < 2, "o agregado tem outro nome - por isso a linha e' a referencia"

    orfao_d = _tokens_significativos("Parcela Lojista Visa - DIMED SA-DISTRIBUIDOR")
    linha_d = _tokens_significativos("DIMED SA-DISTRIBUIDOR")
    assert len(orfao_d & linha_d) >= 2


def test_mesmo_evento_com_ate_cinco_dias_de_diferenca():
    """O Pluggy manda o valor cheio duas vezes, com ate 3 dias de diferenca:
      08/08 AIRBNB * HM2DSX2SAO PAULO BR  R$271,47  (orfao)
      11/08 AIRBNB * HM2DSX2 SAO PAULO BR R$271,47  (vinculada)
    A janela de 1 dia nao alcancava. Os tokens ainda casam apesar do
    'HM2DSX2SAO' colado."""
    from views.relatorios import _tokens_significativos

    a = _tokens_significativos("Parcelado Lojista - Visa -  AIRBNB * HM2DSX2SAO PAULO    BR")
    b = _tokens_significativos("Parcelado Lojista - Visa - AIRBNB * HM2DSX2 SAO PAULO    BR")
    assert len(a & b) >= 2, f"tokens em comum: {a & b}"

    c = _tokens_significativos("Parcelado Lojista - Visa - LISCIA           VIDEIRA      BR")
    assert len(c & _tokens_significativos("Parcelado Lojista - Visa - LISCIA VIDEIRA BR")) >= 2
