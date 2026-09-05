"""Leitor do extrato de CONTA CORRENTE da Unicred (PDF).

Serve para conferir os lancamentos que o Pluggy trouxe da conta corrente contra
o documento oficial do banco - o mesmo papel que a fatura cumpre no cartao
(secao 5). Devolve o mesmo dicionario dos extratores de fatura, porque toda a
maquina de conciliacao e agnostica de formato: so o extrator conhece o layout.

Tres diferencas em relacao a fatura, e todas importam:

1. **O periodo vem impresso** ("Periodo de 01/08/2026 a 31/08/2026"), entao nao
   ha a deducao de ciclo que o PDF do cartao exige (secao 6.2) - `ciclo_do_arquivo`
   e True, como no OFX.
2. **A prova de leitura e outra.** No cartao e "soma das linhas = total
   impresso"; aqui e `saldo inicial + soma = saldo final`. E isso que garante
   que nenhuma linha se perdeu na quebra de pagina antes de comparar com o
   Pluggy.
3. **Ha lancamentos FUTUROS** numa secao propria: debitos ja agendados pelo
   banco que ainda nao aconteceram. Eles NAO sao lancamentos do periodo e nao
   entram em `linhas` - viram `compromissos`, para a tela mostrar a parte,
   porque contar dinheiro que ainda nao saiu inflaria o resultado (secao 1.1).

A descricao quebra em ate tres linhas de texto, com a data e os valores no
meio. Se a Unicred mudar o modelo, o fechamento por saldo acusa - e por isso
este parser levanta erro em vez de devolver numero errado.
"""
import io
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pdfplumber

from fatura_unicred import FaturaInvalida

CENTAVO = Decimal("0.01")
MAX_PAGINAS_EXTRATO = 40

PERIODO_RE = re.compile(r"Per[ií]odo de (\d{2}/\d{2}/\d{4}) a (\d{2}/\d{2}/\d{4})")
CONTA_RE = re.compile(r"Coop:\s*(\d+)\s*-\s*AG:\s*(\d+)\s*-\s*Conta:\s*([\d-]+)")
SALDO_ANTERIOR_RE = re.compile(r"Saldo em (\d{2}/\d{2}/\d{4}):\s*R\$\s*(-?[\d.,]+)")
SALDO_FINAL_RE = re.compile(r"Saldo no final do per[ií]odo\s*R\$\s*(-?[\d.,]+)")
# data | (descricao) | valor com sinal | saldo corrente
MOVIMENTO_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+(.*?)(-?)\s*R\$\s*([\d.,]+)\s+(-?)\s*R\$\s*([\d.,]+)\s*$"
)
# na secao de futuros nao ha saldo corrente
FUTURO_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.*?)(-?)\s*R\$\s*([\d.,]+)\s*$")


def _num(txt):
    return Decimal(txt.replace(".", "").replace(",", ".")).quantize(
        CENTAVO, rounding=ROUND_HALF_UP
    )


def _data(txt):
    dia, mes, ano = txt.split("/")
    return date(int(ano), int(mes), int(dia))


def eh_extrato(conteudo):
    """Reconhece pelo CONTEUDO, nunca pela extensao: o despachante precisa
    distinguir extrato de fatura, e os dois sao PDF da mesma cooperativa.

    Aceita texto ja extraido OU os bytes do arquivo. Num PDF o texto vem
    comprimido (FlateDecode), entao procurar a palavra nos bytes crus nunca
    acha - foi assim que o extrato caiu no leitor de fatura e o usuario viu
    "nao encontrei o mes de referencia". Quando chegam bytes de PDF, o texto e
    extraido antes de decidir.
    """
    if isinstance(conteudo, bytes):
        if conteudo[:5] == b"%PDF-":
            try:
                with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
                    conteudo = "\n".join(
                        (pagina.extract_text() or "") for pagina in pdf.pages[:2]
                    )
            except Exception:
                return False
        else:
            conteudo = conteudo.decode("latin-1", errors="replace")
    return "Extrato" in conteudo and "Saldo no final do per" in conteudo


def _texto_do_pdf(arquivo):
    with pdfplumber.open(arquivo) as pdf:
        if len(pdf.pages) > MAX_PAGINAS_EXTRATO:
            raise FaturaInvalida(
                f"Extrato com {len(pdf.pages)} páginas — acima do limite aceito."
            )
        return "\n".join((pagina.extract_text() or "") for pagina in pdf.pages)


def extrair_extrato(arquivo):
    """arquivo: caminho ou objeto binario. Devolve o dicionario dos extratores
    de fatura, mais `saldo_inicial`, `saldo_final`, `conta_externa` e
    `compromissos` (os lancamentos futuros)."""
    try:
        texto = _texto_do_pdf(arquivo)
    except FaturaInvalida:
        raise
    except Exception:
        raise FaturaInvalida("O arquivo enviado não é um PDF válido.")

    if not eh_extrato(texto):
        raise FaturaInvalida(
            "Este PDF não parece um extrato de conta corrente da Unicred."
        )

    periodo = PERIODO_RE.search(texto)
    if not periodo:
        raise FaturaInvalida("Não encontrei o período deste extrato.")
    periodo_inicio, periodo_fim = _data(periodo.group(1)), _data(periodo.group(2))

    anterior = SALDO_ANTERIOR_RE.search(texto)
    final = SALDO_FINAL_RE.search(texto)
    if not anterior or not final:
        raise FaturaInvalida(
            "Não encontrei o saldo inicial ou o saldo final — sem eles não dá "
            "para provar que a leitura do extrato está completa."
        )
    saldo_inicial, saldo_final = _num(anterior.group(2)), _num(final.group(1))

    linhas_texto = texto.splitlines()
    corte = next(
        (i for i, l in enumerate(linhas_texto) if l.startswith("Lançamentos futuros")),
        len(linhas_texto),
    )

    movimentos = _ler_movimentos(linhas_texto[:corte])
    if not movimentos:
        raise FaturaInvalida("Não encontrei nenhum lançamento neste extrato.")

    soma = sum((m["valor"] for m in movimentos), Decimal("0"))
    if saldo_inicial + soma != saldo_final:
        # Erro claro em vez de numero errado: o saldo e a unica prova de que a
        # extracao pegou todas as linhas, inclusive as quebradas entre paginas.
        raise FaturaInvalida(
            f"A leitura não fecha: saldo inicial {saldo_inicial} + lançamentos "
            f"{soma} = {saldo_inicial + soma}, mas o extrato diz {saldo_final}. "
            "Alguma linha não foi lida — o arquivo não foi importado."
        )

    conta = CONTA_RE.search(texto)
    return {
        "mes_referencia": periodo_fim.month,
        "ano_referencia": periodo_fim.year,
        "total": soma,
        "cartao_final4": None,
        "vencimento": None,
        "periodo_inicio": periodo_inicio,
        "periodo_fim": periodo_fim,
        # O extrato IMPRIME o periodo; nao ha o que deduzir (secao 6.2).
        "ciclo_do_arquivo": True,
        "extrato": True,
        "saldo_inicial": saldo_inicial,
        "saldo_final": saldo_final,
        "conta_externa": conta.group(3) if conta else None,
        "agencia": conta.group(2) if conta else None,
        "linhas": [
            {
                "data": m["data"],
                "descricao": m["descricao"],
                "descricao_base": m["descricao"],
                "valor": m["valor"],
                "parcela_atual": None,
                "parcela_total": None,
                "titular": None,
                "cartao_final4": None,
            }
            for m in movimentos
        ],
        "compromissos": _ler_compromissos(linhas_texto[corte:]),
    }


def _ler_movimentos(linhas):
    """A descricao vem quebrada: parte ANTES da linha da data, parte DEPOIS.

    O PDF imprime, por exemplo:
        TRANSFERENCIA TEF PIX ( Doc.: 2362112 / CLINICA
        03/08/2026 - R$ 1.700,00 R$ 3.449,47
        DE ANESTESIOLOGIA MACCARINI VIEIRA LTDA )
    Juntar as tres partes e o que produz "TRANSFERENCIA TEF PIX ( Doc.: ...
    CLINICA DE ANESTESIOLOGIA ... )" - sem isso o lojista fica cortado no meio
    e nenhuma regra de classificacao consegue casar.
    """
    movimentos = []
    # Linha ja usada como FIM da descricao de um movimento nao pode ser usada de
    # novo como INICIO do proximo: sem isso, "Doc.: 114159 )" - o fecho de uma
    # descricao - virava o comeco da descricao seguinte.
    consumidas = set()
    for i, linha in enumerate(linhas):
        achado = MOVIMENTO_RE.match(linha.strip())
        if not achado:
            continue
        data_txt, meio, sinal, valor_txt, _sinal_saldo, _saldo = achado.groups()
        partes = []
        anterior = linhas[i - 1].strip() if i else ""
        if (anterior and i - 1 not in consumidas and not MOVIMENTO_RE.match(anterior)
                and _parece_descricao(anterior)):
            partes.append(anterior)
        if meio.strip():
            partes.append(meio.strip())
        seguinte = linhas[i + 1].strip() if i + 1 < len(linhas) else ""
        if seguinte and not MOVIMENTO_RE.match(seguinte) and _parece_continuacao(seguinte):
            partes.append(seguinte)
            consumidas.add(i + 1)
        descricao = " ".join(" ".join(partes).split())
        valor = _num(valor_txt)
        movimentos.append({
            "data": _data(data_txt),
            "descricao": descricao or "(sem descrição)",
            "valor": -valor if sinal == "-" else valor,
        })
    return movimentos


def _parece_descricao(texto):
    """Linha acima que é o começo da descrição, e não cabeçalho ou saldo."""
    if texto.startswith(("Data ", "Saldo", "Período", "Periodo", "Coop", "Limite")):
        return False
    return not texto.startswith("R$") and "Total" not in texto


def _parece_continuacao(texto):
    """Fim da descrição costuma fechar o parêntese aberto antes da data."""
    return texto.endswith(")") or texto.endswith("-")


def _ler_compromissos(linhas):
    """Débitos JÁ AGENDADOS pelo banco, que ainda não aconteceram.

    Ficam fora de `linhas` de propósito: não são movimento do período, e contar
    dinheiro que não saiu inflaria o resultado (secao 1.1). Servem para a tela
    mostrar o que está por vir.
    """
    compromissos = []
    for i, linha in enumerate(linhas):
        achado = FUTURO_RE.match(linha.strip())
        if not achado:
            continue
        data_txt, descricao, sinal, valor_txt = achado.groups()
        descricao = " ".join(descricao.split())
        if not descricao and i:
            # descricao longa quebra ANTES da linha da data, igual aos
            # movimentos - foi assim que o debito do consorcio ficou sem nome
            anterior = linhas[i - 1].strip()
            if anterior and not FUTURO_RE.match(anterior):
                descricao = " ".join(anterior.split())
        valor = _num(valor_txt)
        compromissos.append({
            "data": _data(data_txt),
            "descricao": descricao or "(sem descrição)",
            "valor": -valor if sinal == "-" else valor,
        })
    return compromissos
