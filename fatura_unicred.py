"""Leitor da fatura em PDF da Unicred (cartao Visa), usado pela tela de
conciliacao de fatura em /relatorios/conciliar-fatura.

Layout especifico dessa cooperativa: cada lancamento fica numa linha (as vezes
2-3, quando tem conversao de moeda estrangeira) com Data | Descricao | Valor
em colunas fixas. Se a Unicred mudar o modelo do PDF, este parser para de
bater e precisa ser revisado - por isso ele levanta erro claro em vez de
devolver numero errado quando nao acha o total ou a referencia.
"""
import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pdfplumber

MESES = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}
DATA_RE = re.compile(r"^(\d{2})/([a-zç]{3})$", re.IGNORECASE)
TITULAR_RE = re.compile(r"^[A-ZÀ-Ú][A-ZÀ-Ú ]+$")
PARC_RE = re.compile(r"Parc\.\d+/\d+", re.IGNORECASE)
MAX_PAGINAS_FATURA = 50
CENTAVO = Decimal("0.01")


class FaturaInvalida(ValueError):
    pass


# Datas de fechamento REAIS, conferidas pelo usuario direto no app do Unicred
# (tela "Melhor dia para compra" = data de fechamento). O PDF da fatura nao
# imprime essa data, e o intervalo vencimento-fechamento varia mes a mes
# (9 a 14 dias nos meses abaixo) - por isso nao da pra usar um deslocamento
# fixo. Preencher aqui conforme o usuario for confirmando novos meses no app;
# fora daqui, cai no fallback por heuristica (ultima compra impressa).
FECHAMENTOS_CONHECIDOS = {
    (2026, 9): date(2026, 9, 11),
    (2026, 10): date(2026, 10, 13),
    (2026, 11): date(2026, 11, 12),
    (2026, 12): date(2026, 12, 11),
    (2027, 1): date(2027, 1, 8),
    (2027, 2): date(2027, 2, 9),
    (2027, 3): date(2027, 3, 9),
    (2027, 4): date(2027, 4, 9),
}


def _num_valor(txt):
    return Decimal(txt.replace(".", "").replace(",", ".")).quantize(
        CENTAVO, rounding=ROUND_HALF_UP
    )


def extrair_fatura(arquivo):
    """arquivo: caminho ou objeto binario (BytesIO) do PDF.
    Devolve {mes_ref, ano_ref, total, cartao_final4, linhas: [...]}."""
    linhas = []
    titular_atual = None
    ref_mes = ref_ano = None
    total_fatura = None
    final4 = None

    # `accept="application/pdf"` no navegador e apenas uma dica e pode ser
    # contornado. Recusar antes do parser deixa a mensagem clara e evita tentar
    # interpretar imagem, documento ou arquivo arbitrario como fatura.
    if hasattr(arquivo, "read"):
        posicao = arquivo.tell()
        assinatura = arquivo.read(5)
        arquivo.seek(posicao)
        if assinatura != b"%PDF-":
            raise FaturaInvalida("O arquivo enviado não é um PDF válido.")

    with pdfplumber.open(arquivo) as pdf:
        if not pdf.pages:
            raise FaturaInvalida("PDF vazio.")
        if len(pdf.pages) > MAX_PAGINAS_FATURA:
            raise FaturaInvalida(
                f"PDF com páginas demais ({len(pdf.pages)}). O limite é {MAX_PAGINAS_FATURA}."
            )

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

        # A Unicred nao imprime a data de FECHAMENTO no PDF (so o vencimento) -
        # por isso fechamento fica de fora daqui; a tela deixa o usuario
        # preencher esse campo manualmente.
        vencimento = None
        m = re.search(r"VENCIMENTO:?\s*(\d{2})\s*([A-ZÇ]{3})\s*(\d{4})", texto_pag2 or "", re.IGNORECASE)
        if m:
            venc_mes = MESES.get(m.group(2).lower())
            if venc_mes:
                try:
                    vencimento = date(int(m.group(3)), venc_mes, int(m.group(1)))
                except ValueError:
                    vencimento = None

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

                descricao = " ".join(desc_toks).strip()
                # "Parc.9/12" na fatura vira UMA linha por mes; o Pluggy grava o
                # parcelamento inteiro como UMA transacao so, no valor cheio, na
                # data da compra original (ex: 12 parcelas de R$129 -> uma linha
                # de R$1.548). Sem separar isso, toda compra parcelada bate errado
                # nos dois lados da conciliacao.
                pm = re.search(r"Parc\.(\d+)/(\d+)", descricao, re.IGNORECASE)
                parcela_atual = int(pm.group(1)) if pm else None
                parcela_total = int(pm.group(2)) if pm else None
                descricao_base = PARC_RE.sub("", descricao).strip()

                linhas.append({
                    "data": dt,
                    "descricao": descricao,
                    "descricao_base": descricao_base,
                    "parcela_atual": parcela_atual,
                    "parcela_total": parcela_total,
                    "valor": valor.quantize(CENTAVO, rounding=ROUND_HALF_UP),
                    "titular": titular_atual,
                })

    if not linhas:
        raise FaturaInvalida("Não encontrei nenhum lançamento nas páginas do PDF.")

    # Fechamento real (conferido no app do Unicred) tem prioridade. Sem isso,
    # cai no fallback: data mais recente entre as linhas (parcela antiga tem a
    # data da COMPRA ORIGINAL, sempre no passado, entao nunca puxa o maximo pra
    # frente) - e nunca pode fechar no dia do vencimento ou depois dele.
    periodo_fim = FECHAMENTOS_CONHECIDOS.get((ref_ano, ref_mes))
    if periodo_fim is None:
        periodo_fim = max(l["data"] for l in linhas)
        if vencimento and periodo_fim >= vencimento:
            periodo_fim = vencimento - timedelta(days=1)
    # Inicio aproximado: 35 dias antes - so usado quando nao ha fatura anterior
    # no sistema (views/relatorios.py troca por periodo_fim+1 da fatura
    # anterior sempre que ela existir).
    periodo_inicio = periodo_fim - timedelta(days=35)

    return {
        "mes_referencia": ref_mes,
        "ano_referencia": ref_ano,
        "total": total_fatura,
        "cartao_final4": final4,
        "vencimento": vencimento,
        "periodo_inicio": periodo_inicio,
        "periodo_fim": periodo_fim,
        "linhas": linhas,
    }
