"""Leitor da fatura em PDF da Unicred (cartao Visa), usado pela tela de
conciliacao de fatura em /relatorios/conciliar-fatura.

Layout especifico dessa cooperativa: cada lancamento fica numa linha (as vezes
2-3, quando tem conversao de moeda estrangeira) com Data | Descricao | Valor
em colunas fixas. Se a Unicred mudar o modelo do PDF, este parser para de
bater e precisa ser revisado - por isso ele levanta erro claro em vez de
devolver numero errado quando nao acha o total ou a referencia.
"""
import re
from datetime import date

import pdfplumber

MESES = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}
DATA_RE = re.compile(r"^(\d{2})/([a-zç]{3})$", re.IGNORECASE)
TITULAR_RE = re.compile(r"^[A-ZÀ-Ú][A-ZÀ-Ú ]+$")


class FaturaInvalida(ValueError):
    pass


def _num_valor(txt):
    return float(txt.replace(".", "").replace(",", "."))


def extrair_fatura(arquivo):
    """arquivo: caminho ou objeto binario (BytesIO) do PDF.
    Devolve {mes_ref, ano_ref, total, cartao_final4, linhas: [...]}."""
    linhas = []
    titular_atual = None
    ref_mes = ref_ano = None
    total_fatura = None
    final4 = None

    with pdfplumber.open(arquivo) as pdf:
        if not pdf.pages:
            raise FaturaInvalida("PDF vazio.")

        texto_pag1 = pdf.pages[0].extract_text() or ""
        m = re.search(r"REF\.?:\s*([a-zç]{3})/(\d{4})", texto_pag1, re.IGNORECASE)
        if not m:
            raise FaturaInvalida(
                "Não encontrei o mês de referência (REF.: mmm/aaaa) na primeira página. "
                "Confirme que é uma fatura da Unicred no formato esperado."
            )
        ref_mes = MESES.get(m.group(1).lower())
        ref_ano = int(m.group(2))
        if not ref_mes:
            raise FaturaInvalida(f"Mês de referência não reconhecido: {m.group(1)}")

        texto_pag2 = pdf.pages[1].extract_text() if len(pdf.pages) > 1 else ""
        m = re.search(r"SALDO TOTAL\s*=?\s*R\$\s*([\d.,]+)", texto_pag2 or "", re.IGNORECASE)
        if not m:
            raise FaturaInvalida("Não encontrei o SALDO TOTAL da fatura na segunda página.")
        total_fatura = _num_valor(m.group(1))

        for page in pdf.pages[2:]:
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            if not words:
                continue

            if not final4:
                for w in words:
                    mm = re.match(r"\d{4}\*\*\.\*+\.(\d{4})", w["text"])
                    if mm:
                        final4 = mm.group(1)
                        break

            y_cabecalho = next((w["top"] for w in words if w["text"] == "DATA" and w["x0"] < 100), None)
            if y_cabecalho:
                tit_toks = [w["text"] for w in words if 120 < w["top"] < y_cabecalho - 2]
                nome = " ".join(tit_toks).strip()
                if TITULAR_RE.match(nome):
                    titular_atual = nome.title()

            limite = (y_cabecalho + 2) if y_cabecalho else 178
            relevantes = sorted((w for w in words if w["top"] > limite), key=lambda w: (w["top"], w["x0"]))

            # agrupa palavras em "entradas": gap vertical < 12pt = mesmo lancamento
            # (cobre lancamentos com conversao de moeda, que ocupam 2-3 linhas)
            grupos, atual, ultimo_top = [], [], None
            for w in relevantes:
                if ultimo_top is not None and w["top"] - ultimo_top > 12:
                    grupos.append(atual)
                    atual = []
                atual.append(w)
                ultimo_top = w["top"]
            if atual:
                grupos.append(atual)

            for grupo in grupos:
                data_tok, valor_toks, desc_toks = None, [], []
                for w in grupo:
                    if w["x0"] < 100 and DATA_RE.match(w["text"]):
                        data_tok = w["text"]
                    elif w["x0"] > 450:
                        valor_toks.append(w["text"])
                    else:
                        desc_toks.append(w["text"])
                if not data_tok or not valor_toks:
                    continue  # rodape, cabecalho de secao ou lixo de layout

                dm = DATA_RE.match(data_tok)
                dia, mes = int(dm.group(1)), MESES.get(dm.group(2).lower())
                if not mes:
                    continue
                ano = ref_ano - 1 if mes > ref_mes else ref_ano
                try:
                    dt = date(ano, mes, dia)
                except ValueError:
                    continue

                valor_str = "".join(valor_toks)
                sinal = -1 if valor_str.strip().startswith("-") else 1
                num_str = re.sub(r"[^\d,.]", "", valor_str)
                try:
                    valor = sinal * _num_valor(num_str)
                except ValueError:
                    continue

                linhas.append({
                    "data": dt,
                    "descricao": " ".join(desc_toks).strip(),
                    "valor": round(valor, 2),
                    "titular": titular_atual,
                })

    if not linhas:
        raise FaturaInvalida("Não encontrei nenhum lançamento nas páginas do PDF.")

    return {
        "mes_referencia": ref_mes,
        "ano_referencia": ref_ano,
        "total": total_fatura,
        "cartao_final4": final4,
        "linhas": linhas,
    }
