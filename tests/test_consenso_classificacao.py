"""Consenso de classificacao aprendido dos lancamentos com OK.

Estes testes rodam a logica de verdade, com dados sinteticos - nao sao
checagens de texto. Existem porque o bug que zerou o preenchimento na
migracao 46 passou por toda a suite estrutural sem ser notado: o codigo
"parecia certo" e so o dado real revelava o problema.
"""
from core import _canonizar_v45, _consenso_por_lojista, _dimensao_vazia, _loja_v45


def linha(tid, loja, categoria, conferida, dims):
    return (tid, f"A vista sem juros - Visa - {loja}", categoria, conferida, dims)


def test_dimensao_com_valor_nulo_conta_como_vazia():
    """O bug que zerou a migracao 46.

    `transacao_dimensao.valor_id` e nulavel (ON DELETE SET NULL), entao a
    chave existe no jsonb com valor None. Testar so a presenca da chave dava
    a dimensao como preenchida enquanto a tela mostrava "(nao definido)".
    """
    assert _dimensao_vazia({}, 1) is True
    assert _dimensao_vazia({"1": None}, 1) is True
    assert _dimensao_vazia(None, 1) is True
    assert _dimensao_vazia({"1": 4}, 1) is False


def test_consenso_exige_unanimidade_e_duas_evidencias():
    linhas = [
        linha("a", "LOJA X", "Groceries", True, {"1": 4}),
        linha("b", "LOJA X", "Groceries", True, {"1": 4}),
        linha("c", "LOJA Y", "Groceries", True, {"1": 4}),   # so uma evidencia
        linha("d", "LOJA Z", "Groceries", True, {"1": 4}),
        linha("e", "LOJA Z", "Eating out", True, {"1": 2}),  # divergem
    ]
    _mapa, consenso = _consenso_por_lojista(linhas)
    assert consenso["LOJA X"] == {"cat": "Groceries", 1: 4}
    assert "LOJA Y" not in consenso, "uma evidencia so nao e padrao"
    assert "LOJA Z" not in consenso, "OKs divergentes nao geram consenso"


def test_consenso_decide_campo_a_campo():
    """O posto abastece o Jeep e o Tracker: categoria e unanime, projeto nao.

    Exigir o conjunto inteiro jogava a categoria fora junto com o projeto.
    """
    linhas = [
        linha("a", "POSTO", "Gas stations", True, {"1": 1, "2": 14}),
        linha("b", "POSTO", "Gas stations", True, {"1": 2, "2": 18}),
    ]
    _mapa, consenso = _consenso_por_lojista(linhas)
    assert consenso["POSTO"] == {"cat": "Gas stations"}


def test_consenso_ignora_quem_nao_tem_ok():
    """OK e a assinatura humana - so ele ensina."""
    linhas = [
        linha("a", "LOJA X", "Groceries", False, {"1": 4}),
        linha("b", "LOJA X", "Groceries", False, {"1": 4}),
    ]
    _mapa, consenso = _consenso_por_lojista(linhas)
    assert consenso == {}


def test_consenso_nunca_propaga_projeto_de_viagem():
    """Viagem e evento datado: o mesmo hotel volta em outra viagem."""
    linhas = [
        linha("a", "HOTEL", "Accomodation", True, {"2": 6}),
        linha("b", "HOTEL", "Accomodation", True, {"2": 6}),
    ]
    _mapa, consenso = _consenso_por_lojista(
        linhas, dim_projeto=2, nomes_valor={6: "Viagem Chile"})
    assert consenso["HOTEL"] == {"cat": "Accomodation"}
    assert 2 not in consenso["HOTEL"]


def test_lojista_recusado_na_revisao_nao_gera_consenso():
    linhas = [
        linha("a", "POUSADA FOGO*RESE", "Gas stations", True, {}),
        linha("b", "POUSADA FOGO*RESE", "Gas stations", True, {}),
    ]
    _mapa, consenso = _consenso_por_lojista(
        linhas, recusados={"POUSADA FOGO*RESE"})
    assert consenso == {}


def test_mesma_loja_com_e_sem_sufixo_de_cidade_vira_um_grupo():
    linhas = [
        linha("a", "DELTA VIDEIRA VIDEIRA BR", "Gas stations", True, {}),
        linha("b", "DELTA VIDEIRA", "Gas stations", True, {}),
    ]
    mapa, consenso = _consenso_por_lojista(linhas)
    assert mapa["DELTA VIDEIRA VIDEIRA BR"] == "DELTA VIDEIRA"
    assert consenso["DELTA VIDEIRA"] == {"cat": "Gas stations"}


def test_canonizacao_nao_deixa_loja_curta_engolir_outra():
    """ESTACAO nao pode capturar HIPER CENTER ESTACAO - sao lojas diferentes."""
    mapa = _canonizar_v45({"ESTACAO", "HIPER CENTER ESTACAO", "ESTACAO CENTRO"})
    assert mapa["HIPER CENTER ESTACAO"] == "HIPER CENTER ESTACAO"
    assert mapa["ESTACAO CENTRO"] == "ESTACAO"


def test_nome_do_lojista_ignora_prefixo_e_numero_de_parcela():
    assert _loja_v45("Parcela Lojista Visa - LISCIA") == "LISCIA"
    assert _loja_v45("A vista sem juros - Visa - LOJA X") == "LOJA X"
    assert _loja_v45("LISCIA Parc.2/2") == "LISCIA"
