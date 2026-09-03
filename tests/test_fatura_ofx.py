"""Extrator de fatura em OFX, testado contra o arquivo real do Nubank.

O OFX de exemplo esta embutido aqui de proposito: o teste tem que rodar sem
depender de um arquivo na maquina de quem executa, e o formato e o contrato
que estamos travando.
"""
import io
import pathlib
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from fatura_ofx import (  # noqa: E402
    extrair_fatura, eh_ofx, identificar_origem, _partir_parcela,
)
from fatura_unicred import FaturaInvalida  # noqa: E402

OFX_NUBANK = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE></STATUS>
<FI><ORG>NU PAGAMENTOS S.A.</ORG><FID>260</FID></FI>
</SONRS></SIGNONMSGSRSV1>
<CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS>
<CURDEF>BRL</CURDEF>
<CCACCTFROM><ACCTID>5b0c5acf-772d-433b-b660-670579357a01</ACCTID></CCACCTFROM>
<BANKTRANLIST>
<DTSTART>20250916000000[-3:BRT]</DTSTART>
<DTEND>20251016000000[-3:BRT]</DTEND>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20250922000000[-3:BRT]</DTPOSTED>
<TRNAMT>-10.00</TRNAMT><FITID>aaa-1</FITID><MEMO>Jetshr</MEMO></STMTTRN>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20250922000000[-3:BRT]</DTPOSTED>
<TRNAMT>-8.41</TRNAMT><FITID>aaa-2</FITID><MEMO>Jetshr</MEMO></STMTTRN>
<STMTTRN><TRNTYPE>CREDIT</TRNTYPE><DTPOSTED>20250916000000[-3:BRT]</DTPOSTED>
<TRNAMT>1.00</TRNAMT><FITID>aaa-3</FITID><MEMO>Pagamento recebido</MEMO></STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>-18.41</BALAMT><DTASOF>20251016000000[-3:BRT]</DTASOF></LEDGERBAL>
</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1>
</OFX>
"""


def _fatura():
    return extrair_fatura(io.BytesIO(OFX_NUBANK.encode("cp1252")))


def test_reconhece_ofx_e_recusa_pdf():
    assert eh_ofx(OFX_NUBANK.encode("cp1252"))
    assert not eh_ofx(b"%PDF-1.4 alguma coisa")
    with pytest.raises(FaturaInvalida):
        extrair_fatura(io.BytesIO(b"%PDF-1.4 nao sou ofx"))


def test_identifica_banco_e_conta_do_arquivo():
    """Sem isso a origem teria que ser escolhida a cada importacao - e ha DUAS
    contas Nubank, entao adivinhar por banco mandaria a fatura de um titular
    para o outro."""
    origem = identificar_origem(OFX_NUBANK.encode("cp1252"))
    assert origem["banco"] == "NU PAGAMENTOS S.A."
    assert origem["banco_id"] == "260"
    assert origem["conta_externa"] == "5b0c5acf-772d-433b-b660-670579357a01"


def test_ciclo_vem_do_arquivo_e_nao_de_heuristica():
    """Secao 6.2: na Unicred a data de fechamento nao e impressa e precisou ser
    conferida no app do banco. No OFX ela e explicita."""
    f = _fatura()
    assert (f["periodo_inicio"].isoformat(), f["periodo_fim"].isoformat()) == (
        "2025-09-16", "2025-10-16"
    )
    # a fatura e nomeada pelo mes em que o ciclo FECHA, tirado do arquivo
    assert (f["mes_referencia"], f["ano_referencia"]) == (10, 2025)


def test_compra_fica_positiva_e_credito_negativo():
    """O OFX traz a compra NEGATIVA; a fatura do app usa o contrario, como o PDF
    da Unicred, e todas as somas dependem disso."""
    linhas = {l["descricao"]: l for l in _fatura()["linhas"]}
    assert linhas["Jetshr"]["valor"] > 0
    assert linhas["Pagamento recebido"]["valor"] < 0


def test_soma_sem_pagamento_bate_com_o_total_informado():
    """Secao 6.3: 'Pagamento Recebido' e a fatura ANTERIOR sendo quitada e fica
    fora das duas somas. Incluir de um lado so gera diferenca inventada."""
    f = _fatura()
    soma = sum(
        l["valor"] for l in f["linhas"]
        if not l["descricao"].upper().startswith("PAGAMENTO RECEBIDO")
    )
    assert soma == f["total"]


def test_cada_linha_guarda_o_id_do_banco():
    ids = [l["id_externo"] for l in _fatura()["linhas"]]
    assert ids == ["aaa-1", "aaa-2", "aaa-3"]
    assert len(set(ids)) == len(ids)


def test_parcela_so_e_reconhecida_quando_o_formato_e_inequivoco():
    """Nao havia parcelamento no arquivo real disponivel. Na duvida a descricao
    fica inteira e a parcela nula - o parcelamento aparece como compra a vista e
    o usuario corrige, em vez de o extrator inventar numero de parcelas."""
    assert _partir_parcela("LOJA X - Parcela 3/10") == ("LOJA X", 3, 10)
    assert _partir_parcela("LOJA X 3/10") == ("LOJA X", 3, 10)
    # 1/1 nao e parcelamento; data solta nao pode virar parcela
    assert _partir_parcela("LOJA X 1/1") == ("LOJA X 1/1", None, None)
    assert _partir_parcela("Jetshr") == ("Jetshr", None, None)


def test_arquivo_sem_lancamentos_e_recusado_com_mensagem_util():
    sem_lista = OFX_NUBANK.replace("BANKTRANLIST", "OUTRACOISA")
    with pytest.raises(FaturaInvalida) as exc:
        extrair_fatura(io.BytesIO(sem_lista.encode("cp1252")))
    assert "extrato" in str(exc.value).lower()
