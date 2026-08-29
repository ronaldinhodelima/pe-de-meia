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

from views.relatorios import _conciliar_linhas


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
