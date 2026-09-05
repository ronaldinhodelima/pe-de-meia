"""Leitura do extrato de conta corrente da Unicred.

O texto abaixo reproduz o layout REAL do PDF de agosto/2026, com os valores
trocados: o que importa e a forma - descricao quebrada em tres pedacos, com a
data e os valores no meio, e a secao de lancamentos futuros no fim. O arquivo
original nao entra no repositorio porque e documento bancario da familia.
"""
import io
from datetime import date
from decimal import Decimal

import pytest

from extrato_unicred import (
    FaturaInvalida,
    _ler_compromissos,
    _ler_movimentos,
    eh_extrato,
)

TEXTO = """04/09/2026 20:22:10
Extrato
RONALDO DE LIMA - ***.638.679-**
Período de 01/08/2026 a 31/08/2026
Coop: 544 - AG: 1505 - Conta: 4451007
Saldo em 31/07/2026: R$ 100,00
Data Lançamentos Valor (R$) Saldo (R$)
TRANSFERENCIA TEF PIX ( Doc.: 2362112 / CLINICA
03/08/2026 - R$ 40,00 R$ 60,00
DE ANESTESIOLOGIA MACCARINI VIEIRA LTDA )
03/08/2026 DEPOSITO EM ESPÉCIE ( Doc.: 482 ) R$ 25,00 R$ 85,00
DEB MENSALID QUANTA PREVID-CENTRALIZADO (
25/08/2026 - R$ 5,00 R$ 80,00
Doc.: 114159 )
25/08/2026 DEBITO MONGERAL ( Doc.: 0056386796 ) - R$ 30,00 R$ 50,00
Saldo no final do período R$ 50,00
Data Lançamentos Valor (R$)
Lançamentos futuros - R$ 13,00
10/09/2026 ARRECADAÇÃO DE CONVÊNIOS (Doc: Convênio / Agua) - R$ 3,00
DEBITO PORTO SEGURO CONSORCIO (Doc: I0240/492 /
15/09/2026 - R$ 10,00
I0240/492)
"""


def linhas_ate_futuros():
    linhas = TEXTO.splitlines()
    corte = next(i for i, l in enumerate(linhas) if l.startswith("Lançamentos futuros"))
    return linhas[:corte], linhas[corte:]


def test_reconhece_o_extrato_pelo_conteudo_e_nao_pela_extensao():
    assert eh_extrato(TEXTO)
    assert not eh_extrato("Fatura do cartão Visa\nSALDO TOTAL R$ 100,00")


def test_deteccao_abre_o_pdf_em_vez_de_procurar_nos_bytes_crus():
    """Num PDF o texto vem comprimido: procurar a palavra nos bytes nunca acha.

    Foi assim que o extrato caiu no leitor de FATURA e o usuario recebeu
    "nao encontrei o mes de referencia" - erro do parser errado, que nao diz
    nada sobre o problema real.
    """
    import inspect

    import extrato_unicred

    fonte = inspect.getsource(extrato_unicred.eh_extrato)
    assert 'conteudo[:5] == b"%PDF-"' in fonte
    assert "pdfplumber.open" in fonte
    # bytes que nao sao PDF continuam sendo lidos como texto
    assert eh_extrato(TEXTO.encode("latin-1"))
    assert not eh_extrato(b"%PDF-1.5 conteudo binario que nao abre")


def test_descricao_quebrada_em_tres_pedacos_e_remontada():
    """O lojista fica cortado no meio se as tres partes nao forem juntadas, e
    nenhuma regra de classificacao consegue casar com meia descricao."""
    movimentos, _ = linhas_ate_futuros()
    lidos = _ler_movimentos(movimentos)
    assert lidos[0]["descricao"] == (
        "TRANSFERENCIA TEF PIX ( Doc.: 2362112 / CLINICA "
        "DE ANESTESIOLOGIA MACCARINI VIEIRA LTDA )"
    )
    assert lidos[0]["valor"] == Decimal("-40.00")
    assert lidos[0]["data"] == date(2026, 8, 3)


def test_fecho_de_uma_descricao_nao_vira_inicio_da_seguinte():
    """`Doc.: 114159 )` fecha a descricao anterior; usa-lo de novo colava duas
    descricoes numa so."""
    movimentos, _ = linhas_ate_futuros()
    lidos = _ler_movimentos(movimentos)
    mongeral = [m for m in lidos if "MONGERAL" in m["descricao"]][0]
    assert mongeral["descricao"] == "DEBITO MONGERAL ( Doc.: 0056386796 )"
    quanta = [m for m in lidos if "QUANTA" in m["descricao"]][0]
    assert quanta["descricao"].endswith("( Doc.: 114159 )")


def test_sinal_segue_o_extrato_e_credito_fica_positivo():
    movimentos, _ = linhas_ate_futuros()
    lidos = _ler_movimentos(movimentos)
    deposito = [m for m in lidos if "DEPOSITO" in m["descricao"]][0]
    assert deposito["valor"] == Decimal("25.00")
    assert sum(m["valor"] for m in lidos) == Decimal("-50.00")


def test_compromissos_futuros_ficam_fora_dos_lancamentos():
    """Sao debitos AGENDADOS: contar dinheiro que ainda nao saiu inflaria o
    resultado (secao 1.1)."""
    movimentos, futuros = linhas_ate_futuros()
    lidos = _ler_movimentos(movimentos)
    assert not any(m["data"].month == 9 for m in lidos)

    compromissos = _ler_compromissos(futuros)
    assert len(compromissos) == 2
    assert sum(c["valor"] for c in compromissos) == Decimal("-13.00")
    # descricao longa tambem quebra ANTES da linha da data, aqui
    consorcio = [c for c in compromissos if c["valor"] == Decimal("-10.00")][0]
    assert consorcio["descricao"].startswith("DEBITO PORTO SEGURO CONSORCIO")


def test_arquivo_que_nao_e_pdf_da_erro_claro():
    from extrato_unicred import extrair_extrato

    with pytest.raises(FaturaInvalida):
        extrair_extrato(io.BytesIO(b"isto nao e um pdf"))
