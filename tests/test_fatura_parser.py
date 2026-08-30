"""Travas de entrada do leitor de fatura.

Os casos completos de casamento ficam em test_fatura_vinculo.py. Aqui ficam
as validacoes que precisam acontecer antes de qualquer dado do PDF chegar ao
banco ou ao matcher.
"""
import io
from decimal import Decimal

import pytest

import app
import fatura_unicred
from fatura_unicred import FaturaInvalida, MAX_PAGINAS_FATURA, extrair_fatura


class PaginaFake:
    def __init__(self, texto="", palavras=None):
        self._texto = texto
        self._palavras = palavras or []

    def extract_text(self):
        return self._texto

    def extract_words(self, **_kwargs):
        return self._palavras


class PDFFake:
    def __init__(self, paginas):
        self.pages = paginas

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def palavra(texto, x0, top):
    return {"text": texto, "x0": x0, "top": top}


def test_upload_global_tem_limite_de_10_mb():
    assert app.app.config["MAX_CONTENT_LENGTH"] == 10 * 1024 * 1024


def test_recusa_arquivo_sem_assinatura_pdf():
    with pytest.raises(FaturaInvalida, match="não é um PDF válido"):
        extrair_fatura(io.BytesIO(b"isto nao e um pdf"))


def test_recusa_pdf_com_paginas_demais(monkeypatch):
    class PDFExcessivo:
        pages = [object()] * (MAX_PAGINAS_FATURA + 1)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(fatura_unicred.pdfplumber, "open", lambda _arquivo: PDFExcessivo())

    with pytest.raises(FaturaInvalida, match="páginas demais"):
        extrair_fatura(io.BytesIO(b"%PDF-conteudo-de-teste"))


def test_extrai_layout_unicred_sintetico(monkeypatch):
    palavras = [
        palavra("1234**.****.5678", 150, 20),
        palavra("RONALDO", 150, 130),
        palavra("DE", 220, 130),
        palavra("LIMA", 250, 130),
        palavra("DATA", 20, 150),
        palavra("21/ago", 20, 180),
        palavra("LOJA", 150, 180),
        palavra("Parc.2/3", 220, 180),
        palavra("123,45", 500, 180),
        palavra("22/ago", 20, 200),
        palavra("ESTORNO", 150, 200),
        palavra("-10,00", 500, 200),
    ]

    pdf = PDFFake([
        PaginaFake("REF.: ago/2026"),
        PaginaFake("SALDO TOTAL = R$ 113,45\nVENCIMENTO: 24 AGO 2026"),
        PaginaFake(palavras=palavras),
    ])
    monkeypatch.setattr(fatura_unicred.pdfplumber, "open", lambda _arquivo: pdf)

    resultado = extrair_fatura(io.BytesIO(b"%PDF-conteudo-sintetico"))
    assert resultado["mes_referencia"] == 8
    assert resultado["ano_referencia"] == 2026
    assert resultado["total"] == Decimal("113.45")
    assert resultado["cartao_final4"] == "5678"
    assert resultado["vencimento"].isoformat() == "2026-08-24"
    assert resultado["periodo_fim"].isoformat() == "2026-08-22"
    assert resultado["linhas"] == [
        {
            "data": fatura_unicred.date(2026, 8, 21),
            "descricao": "LOJA Parc.2/3",
            "descricao_base": "LOJA",
            "parcela_atual": 2,
            "parcela_total": 3,
            "valor": Decimal("123.45"),
            "titular": "Ronaldo De Lima",
        },
        {
            "data": fatura_unicred.date(2026, 8, 22),
            "descricao": "ESTORNO",
            "descricao_base": "ESTORNO",
            "parcela_atual": None,
            "parcela_total": None,
            "valor": Decimal("-10.00"),
            "titular": "Ronaldo De Lima",
        },
    ]


def test_compra_internacional_em_tres_linhas_usa_so_o_valor_em_reais(monkeypatch):
    """A conversão vem como detalhes abaixo da compra, mas forma uma só linha.

    O valor que entra no sistema é o da coluna direita, já convertido em BRL;
    moeda, valor estrangeiro e cotação permanecem apenas na descrição.
    """
    palavras = [
        palavra("RONALDO", 150, 130),
        palavra("DE", 225, 130),
        palavra("LIMA", 255, 130),
        palavra("DATA", 20, 150),
        palavra("18/ago", 20, 180),
        palavra("ANTHROPIC*", 150, 180),
        palavra("CLAUDE", 235, 180),
        palavra("SUB", 295, 180),
        palavra("123,45", 500, 180),
        palavra("MOEDA:", 150, 186),
        palavra("USD", 215, 186),
        palavra("20.00", 260, 186),
        palavra("COTACAO:", 150, 192),
        palavra("6,1725", 230, 192),
    ]
    pdf = PDFFake([
        PaginaFake("REF.: ago/2026"),
        PaginaFake("SALDO TOTAL = R$ 123,45\nVENCIMENTO: 24 AGO 2026"),
        PaginaFake(palavras=palavras),
    ])
    monkeypatch.setattr(fatura_unicred.pdfplumber, "open", lambda _arquivo: pdf)

    resultado = extrair_fatura(io.BytesIO(b"%PDF-internacional"))

    assert len(resultado["linhas"]) == 1
    linha = resultado["linhas"][0]
    assert linha["valor"] == Decimal("123.45")
    assert sum(l["valor"] for l in resultado["linhas"]) == resultado["total"]
    assert linha["descricao"] == (
        "ANTHROPIC* CLAUDE SUB MOEDA: USD 20.00 COTACAO: 6,1725"
    )


def test_extrai_lancamentos_de_varias_paginas_e_mantem_titular(monkeypatch):
    """Cada página de lançamentos precisa ser lida, sem repetir ou perder linhas."""
    pagina_ronaldo = [
        palavra("1234**.****.5678", 150, 20),
        palavra("RONALDO", 150, 130),
        palavra("DE", 225, 130),
        palavra("LIMA", 255, 130),
        palavra("DATA", 20, 150),
        palavra("30/dez", 20, 180),
        palavra("LOJA", 150, 180),
        palavra("A", 205, 180),
        palavra("10,01", 500, 180),
    ]
    # A página seguinte não repete o nome do titular: o leitor deve manter o
    # último titular identificado, como ocorre em continuações do mesmo cartão.
    pagina_continuacao = [
        palavra("DATA", 20, 150),
        palavra("02/jan", 20, 180),
        palavra("LOJA", 150, 180),
        palavra("B", 205, 180),
        palavra("20,02", 500, 180),
        palavra("03/jan", 20, 200),
        palavra("LOJA", 150, 200),
        palavra("C", 205, 200),
        palavra("30,03", 500, 200),
    ]
    pdf = PDFFake([
        PaginaFake("REF.: jan/2026"),
        PaginaFake("SALDO TOTAL = R$ 60,06\nVENCIMENTO: 22 JAN 2026"),
        PaginaFake(palavras=pagina_ronaldo),
        PaginaFake(palavras=pagina_continuacao),
    ])
    monkeypatch.setattr(fatura_unicred.pdfplumber, "open", lambda _arquivo: pdf)

    resultado = extrair_fatura(io.BytesIO(b"%PDF-multiplas-paginas"))

    assert resultado["cartao_final4"] == "5678"
    assert sum(l["valor"] for l in resultado["linhas"]) == resultado["total"]
    assert [(l["data"].isoformat(), l["descricao"], l["valor"], l["titular"])
            for l in resultado["linhas"]] == [
        ("2025-12-30", "LOJA A", Decimal("10.01"), "Ronaldo De Lima"),
        ("2026-01-02", "LOJA B", Decimal("20.02"), "Ronaldo De Lima"),
        ("2026-01-03", "LOJA C", Decimal("30.03"), "Ronaldo De Lima"),
    ]


def test_resposta_413_explica_o_limite():
    cliente = app.app.test_client()
    resposta = cliente.post(
        "/login",
        data=b"x" * (10 * 1024 * 1024 + 1),
        content_type="application/x-www-form-urlencoded",
    )
    assert resposta.status_code == 413
    assert "10 MB" in resposta.get_data(as_text=True)
