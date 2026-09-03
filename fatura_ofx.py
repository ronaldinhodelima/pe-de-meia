"""Extrator de fatura em OFX (formato do Nubank, e de qualquer banco que siga
o padrao OFX 1.x para cartao de credito).

Devolve o MESMO formato do `fatura_unicred.extrair_fatura`, porque toda a
maquina de conciliacao - `fatura_importada`, `fatura_linha`, `fatura_vinculo`,
o N:N, o regime de caixa do parcelamento, o "fecha 100%" - e agnostica de
formato. So o extrator e especifico.

Por que OFX e melhor que o PDF aqui (secao 6.2 do CLAUDE.md):

- O ciclo vem EXPLICITO em `DTSTART`/`DTEND`. Na Unicred a data de fechamento
  nao e impressa em lugar nenhum do PDF e precisou ser conferida no app do
  banco, uma a uma. Aqui nao ha heuristica nem palpite de 35 dias.
- Cada lancamento tem `FITID`, um identificador estavel do proprio banco.
- Nao ha extracao por posicao de texto, entao nao existe a classe de erro que
  obrigou a validar as 14 faturas da Unicred centavo a centavo.

O que o OFX NAO traz, e por isso fica nulo: numero do cartao, titular e data de
vencimento. O vencimento nao e usado para calcular ciclo - esse vem do arquivo.
"""
import re
from datetime import date
from decimal import Decimal

from fatura_unicred import FaturaInvalida


def _tag(texto, nome):
    """Valor de uma tag OFX.

    OFX 1.x e SGML, nao XML: a tag de fechamento e OPCIONAL, e varios bancos a
    omitem em elementos folha. Por isso a leitura e por expressao regular ate o
    proximo `<` - um parser XML recusaria metade dos arquivos reais.
    """
    achado = re.search(r"<" + nome + r">([^<\r\n]*)", texto, re.I)
    return achado.group(1).strip() if achado else None


def _data_ofx(valor):
    """`20250922000000[-3:BRT]` -> date(2025, 9, 22).

    So os 8 primeiros digitos interessam. A hora vem sempre 000000 nas faturas
    do Nubank (o arquivo traz a data de lancamento, nao o instante da compra),
    entao guardar hora daria uma precisao que o dado nao tem.
    """
    if not valor:
        return None
    digitos = re.sub(r"\D", "", valor)[:8]
    if len(digitos) != 8:
        return None
    try:
        return date(int(digitos[:4]), int(digitos[4:6]), int(digitos[6:8]))
    except ValueError:
        return None


# "Parcela 3/10", "Parc. 3/10", "3/10" no fim da descricao.
_PARCELA = re.compile(
    r"[\s-]*(?:parcela|parc\.?)?\s*(\d{1,2})\s*/\s*(\d{1,2})\s*$", re.I
)


def _partir_parcela(memo):
    """Separa "LOJA X - Parcela 3/10" em ("LOJA X", 3, 10).

    NAO foi validado contra uma fatura do Nubank COM parcelamento - o unico
    arquivo real disponivel nao tinha nenhuma. Se o formato for outro, a
    descricao inteira segue como `descricao_base` e a parcela fica nula, que e
    o comportamento seguro: o parcelamento aparece como compra a vista e o
    usuario corrige, em vez de o extrator inventar um numero de parcelas.
    """
    texto = " ".join((memo or "").split())
    achado = _PARCELA.search(texto)
    if not achado:
        return texto, None, None
    atual, total = int(achado.group(1)), int(achado.group(2))
    if total < 2 or atual < 1 or atual > total:
        return texto, None, None
    return texto[: achado.start()].strip(" -"), atual, total


def identificar_origem(conteudo):
    """Quem emitiu o arquivo e de qual conta, sem depender do nome do arquivo.

    `ORG`/`FID` identificam o banco; `ACCTID` identifica a conta NO BANCO - que
    nao e o `account_id` do Pluggy, entao o vinculo entre os dois e aprendido na
    primeira importacao e reusado depois.
    """
    if isinstance(conteudo, bytes):
        conteudo = conteudo.decode("cp1252", errors="replace")
    return {
        "banco": _tag(conteudo, "ORG"),
        "banco_id": _tag(conteudo, "FID"),
        "conta_externa": _tag(conteudo, "ACCTID"),
    }


def eh_ofx(conteudo):
    if isinstance(conteudo, bytes):
        conteudo = conteudo[:2048].decode("cp1252", errors="replace")
    inicio = conteudo[:2048].upper()
    return "OFXHEADER" in inicio or "<OFX>" in inicio


def extrair_fatura(arquivo):
    """arquivo: caminho ou objeto binario. Devolve o mesmo dicionario do
    extrator da Unicred."""
    if hasattr(arquivo, "read"):
        bruto = arquivo.read()
    else:
        with open(arquivo, "rb") as fh:
            bruto = fh.read()
    if isinstance(bruto, bytes):
        # CHARSET:1252 no cabecalho. `replace` para nao explodir num acento
        # solto - o valor e a data importam mais que um caractere da descricao.
        texto = bruto.decode("cp1252", errors="replace")
    else:
        texto = bruto

    if not eh_ofx(texto):
        raise FaturaInvalida("O arquivo enviado não é um OFX válido.")

    bloco = re.search(r"<BANKTRANLIST>(.*?)</BANKTRANLIST>", texto, re.S | re.I)
    if not bloco:
        raise FaturaInvalida(
            "Não encontrei a lista de lançamentos neste OFX. "
            "Confira se o arquivo é o da fatura, e não o do extrato."
        )
    corpo = bloco.group(1)

    periodo_inicio = _data_ofx(_tag(corpo, "DTSTART"))
    periodo_fim = _data_ofx(_tag(corpo, "DTEND"))
    if not periodo_fim:
        raise FaturaInvalida("Este OFX não informa o período da fatura (DTEND).")

    linhas = []
    for trecho in re.findall(r"<STMTTRN>(.*?)</STMTTRN>", corpo, re.S | re.I):
        data_linha = _data_ofx(_tag(trecho, "DTPOSTED"))
        valor_bruto = _tag(trecho, "TRNAMT")
        if data_linha is None or valor_bruto is None:
            continue
        try:
            valor = Decimal(valor_bruto.replace(",", "."))
        except Exception:
            continue
        # No OFX a compra vem NEGATIVA (saiu dinheiro) e o credito positivo. A
        # fatura do app usa o contrario - compra positiva, credito negativo,
        # como o PDF da Unicred - e todas as somas e comparacoes ja dependem
        # disso. A inversao mora aqui, num lugar so.
        memo = " ".join((_tag(trecho, "MEMO") or "").split())
        base, parcela_atual, parcela_total = _partir_parcela(memo)
        # Mesma convencao do extrator da Unicred: `descricao` guarda o texto
        # INTEIRO como a operadora imprimiu, e `descricao_base` e a versao sem a
        # parcela. Sao usadas para coisas diferentes - a base e quem o casamento
        # automatico compara (secao 6.5 n.11), e jogar o resto fora perderia o
        # que a fatura de fato disse.
        linhas.append({
            "data": data_linha,
            "descricao": memo or "(sem descrição)",
            "descricao_base": base or memo or "(sem descrição)",
            "parcela_atual": parcela_atual,
            "parcela_total": parcela_total,
            "valor": -valor,
            "titular": None,
            "id_externo": _tag(trecho, "FITID"),
        })

    if not linhas:
        raise FaturaInvalida("Não encontrei nenhum lançamento neste OFX.")

    saldo = _tag(texto, "BALAMT")
    total = None
    if saldo:
        try:
            total = abs(Decimal(saldo.replace(",", ".")))
        except Exception:
            total = None

    return {
        # A fatura e nomeada pelo mes em que o ciclo FECHA, tirado do proprio
        # arquivo. Usar o vencimento exigiria o nome do arquivo, que o usuario
        # pode renomear - data financeira nao sai de nome de arquivo.
        "mes_referencia": periodo_fim.month,
        "ano_referencia": periodo_fim.year,
        "total": total,
        "cartao_final4": None,
        "vencimento": None,
        "periodo_inicio": periodo_inicio,
        "periodo_fim": periodo_fim,
        "linhas": linhas,
    }
