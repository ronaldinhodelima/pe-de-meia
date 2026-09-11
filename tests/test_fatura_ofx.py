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
<TRNAMT>-8.41</TRNAMT><FITID>aaa-2</FITID><MEMO>Santtaluz - Parcela 3/6</MEMO></STMTTRN>
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


def test_parcela_do_formato_real_do_nubank():
    """Formato confirmado na fatura 09/2026: "LOJA - Parcela N/M". A descricao
    guarda o texto inteiro, como a operadora imprimiu; `descricao_base` e quem
    o casamento automatico compara (secao 6.5 n.11)."""
    parcelada = [l for l in _fatura()["linhas"] if l["parcela_total"]]
    assert len(parcelada) == 1
    linha = parcelada[0]
    assert linha["descricao"] == "Santtaluz - Parcela 3/6"
    assert linha["descricao_base"] == "Santtaluz"
    assert (linha["parcela_atual"], linha["parcela_total"]) == (3, 6)


def test_compra_fica_positiva_e_credito_negativo():
    """O OFX traz a compra NEGATIVA; a fatura do app usa o contrario, como o PDF
    da Unicred, e todas as somas dependem disso."""
    linhas = {l["descricao_base"]: l for l in _fatura()["linhas"]}
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
    assert "lista de lançamentos" in str(exc.value).lower()


# Layout REAL do extrato de conta corrente Nubank (BANKMSGSRSV1, CHECKING,
# ENCODING:UTF-8), com valores e nomes trocados - o arquivo da familia nao
# entra no repositorio.
OFX_NUBANK_CONTA = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:UTF-8
CHARSET:NONE
<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE></STATUS>
<FI><ORG>NU PAGAMENTOS S.A.</ORG><FID>260</FID></FI>
</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>BRL</CURDEF>
<BANKACCTFROM><BANKID>0260</BANKID><BRANCHID>1</BRANCHID>
<ACCTID>1234567-8</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20250801000000[-3:BRT]</DTSTART>
<DTEND>20250831000000[-3:BRT]</DTEND>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20250807000000[-3:BRT]</DTPOSTED>
<TRNAMT>-50.00</TRNAMT><FITID>b-1</FITID>
<MEMO>Transferência enviada pelo Pix - Fulana - NU PAGAMENTOS Agência: 1 Conta: 22247-0</MEMO></STMTTRN>
<STMTTRN><TRNTYPE>CREDIT</TRNTYPE><DTPOSTED>20250810000000[-3:BRT]</DTPOSTED>
<TRNAMT>300.00</TRNAMT><FITID>b-2</FITID><MEMO>Transferência recebida</MEMO></STMTTRN>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20250812000000[-3:BRT]</DTPOSTED>
<TRNAMT>-1.00</TRNAMT><FITID>b-3</FITID><MEMO>Compra no débito - Loja</MEMO></STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>999.99</BALAMT><DTASOF>20250831000000[-3:BRT]</DTASOF></LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""


def _extrato():
    return extrair_fatura(io.BytesIO(OFX_NUBANK_CONTA.encode("utf-8")))


def test_ofx_de_conta_corrente_vira_extrato_e_nao_fatura():
    e = _extrato()
    assert e["extrato"] is True and e["ciclo_do_arquivo"] is True
    assert e["compromissos"] == []
    assert e["conta_externa"] == "1234567-8"


def test_extrato_ofx_mantem_o_sinal_do_banco_e_nao_le_parcela():
    """Entrada positiva, como no extrato Unicred - e o que Entradas/Saidas
    leem. 'Conta: 22247-0' nao pode virar parcela."""
    valores = [l["valor"] for l in _extrato()["linhas"]]
    assert [str(v) for v in valores] == ["-50.00", "300.00", "-1.00"]
    assert all(l["parcela_atual"] is None for l in _extrato()["linhas"])


def test_extrato_ofx_total_e_o_movimento_e_nao_o_saldo_final():
    """O saldo final (999,99) nao e o total do periodo; usa-lo inventaria
    diferenca na tela."""
    e = _extrato()
    assert str(e["total"]) == "249.00"
    assert str(e["saldo_final"]) == "999.99" and e["saldo_inicial"] is None


def test_ofx_em_utf8_nao_quebra_os_acentos():
    assert _extrato()["linhas"][0]["descricao"].startswith("Transferência enviada")


def test_ofx_de_banco_nao_homologado_e_recusado():
    from fatura_unicred import ArquivoNaoHomologado
    outro = OFX_NUBANK_CONTA.replace("NU PAGAMENTOS S.A.", "BANCO QUALQUER")
    with pytest.raises(ArquivoNaoHomologado) as exc:
        extrair_fatura(io.BytesIO(outro.encode("utf-8")))
    assert "não homologado" in str(exc.value)
    assert "extrato da conta corrente Nubank (OFX)" in str(exc.value)


def test_ofx_de_poupanca_e_recusado():
    from fatura_unicred import ArquivoNaoHomologado
    poupanca = OFX_NUBANK_CONTA.replace("CHECKING", "SAVINGS")
    with pytest.raises(ArquivoNaoHomologado):
        extrair_fatura(io.BytesIO(poupanca.encode("utf-8")))


def test_importacao_confere_documento_contra_o_tipo_da_conta():
    """Extrato so em conta corrente, fatura so em cartao, e arquivo que nao e
    PDF nem OFX recusado como nao homologado."""
    fonte = (RAIZ / "views" / "relatorios.py").read_text(encoding="utf-8")
    assert 'tipo_conta != "BANK"' in fonte and 'tipo_conta != "CREDIT"' in fonte
    assert 'ArquivoNaoHomologado("o arquivo não é PDF nem OFX.")' in fonte


def test_importacao_aceita_varios_arquivos_pelo_mesmo_post_em_serie():
    """Varios arquivos passam, um a um, pelo MESMO POST do envio unico - sem
    rota de lote (secao 7.2-A) e em serie, porque o vinculo automatico depende
    dos vinculos das faturas anteriores (secao 6.1)."""
    tpl = (RAIZ / "templates" / "conciliar_fatura.html").read_text(encoding="utf-8")
    assert 'name="fatura" multiple' in tpl
    assert "data-importada=" in tpl
    assert "await fetch(form.action || window.location.pathname" in tpl
    assert "Promise.all" not in tpl
