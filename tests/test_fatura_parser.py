"""Travas de entrada do leitor de fatura.

Os casos completos de casamento ficam em test_fatura_vinculo.py. Aqui ficam
as validacoes que precisam acontecer antes de qualquer dado do PDF chegar ao
banco ou ao matcher.
"""
import io

import pytest

import app
import fatura_unicred
from fatura_unicred import FaturaInvalida, MAX_PAGINAS_FATURA, extrair_fatura


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
    class Pagina:
        def __init__(self, texto="", palavras=None):
            self._texto = texto
            self._palavras = palavras or []

        def extract_text(self):
            return self._texto

        def extract_words(self, **_kwargs):
            return self._palavras

    def palavra(texto, x0, top):
        return {"text": texto, "x0": x0, "top": top}

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

    class PDF:
        pages = [
            Pagina("REF.: ago/2026"),
            Pagina("SALDO TOTAL = R$ 113,45\nVENCIMENTO: 24 AGO 2026"),
            Pagina(palavras=palavras),
        ]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(fatura_unicred.pdfplumber, "open", lambda _arquivo: PDF())

    resultado = extrair_fatura(io.BytesIO(b"%PDF-conteudo-sintetico"))
    assert resultado["mes_referencia"] == 8
    assert resultado["ano_referencia"] == 2026
    assert resultado["total"] == 113.45
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
            "valor": 123.45,
            "titular": "Ronaldo De Lima",
        },
        {
            "data": fatura_unicred.date(2026, 8, 22),
            "descricao": "ESTORNO",
            "descricao_base": "ESTORNO",
            "parcela_atual": None,
            "parcela_total": None,
            "valor": -10.0,
            "titular": "Ronaldo De Lima",
        },
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
