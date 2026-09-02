#!/usr/bin/env python3
"""Levanta todo valor visual escrito na mao, fora do sistema de tokens.

Existe porque tres bugs desta sessao eram invisiveis lendo o codigo: as cores
fixas do DRE que ignoravam os tokens, a caixa de 14px vs 16px decidida por
especificidade, e os campos brancos no modo escuro. Contar o que esta fora do
sistema transforma "o layout esta desalinhado" em um numero que cai.

Uso:
    python3 ferramentas/inventario_estilo.py          # resumo
    python3 ferramentas/inventario_estilo.py --lista  # com arquivo e linha
"""
import collections
import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# O que se espera que venha de token, e o padrao que denuncia o valor cru.
PROPRIEDADES = {
    "cor": re.compile(r"(?<![\w-])(?:color|background|background-color|border-color|fill|stroke)\s*:\s*([^;{}]+)"),
    "raio": re.compile(r"(?<![\w-])border-radius\s*:\s*([^;{}]+)"),
    "sombra": re.compile(r"(?<![\w-])box-shadow\s*:\s*([^;{}]+)"),
    "tamanho_fonte": re.compile(r"(?<![\w-])font-size\s*:\s*([^;{}]+)"),
    "peso_fonte": re.compile(r"(?<![\w-])font-weight\s*:\s*([^;{}]+)"),
}
COR_CRUA = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\([^)]*\)|\bhsla?\([^)]*\)")
# Valores que nao sao "cor de marca": nao adianta exigir token deles.
COR_NEUTRA = re.compile(r"^(transparent|inherit|currentColor|none|unset|initial)$", re.I)


def sem_definicao_de_tokens(texto):
    """O :root e onde os tokens NASCEM - valor cru ali e a definicao, nao a
    violacao. Sem isto a ferramenta acusaria o proprio sistema de design."""
    return re.sub(r":root[^{]*\{[^}]*\}", lambda m: "\n" * m.group(0).count("\n"), texto)


def sem_comentarios(texto):
    """Preserva as quebras de linha: sem isso o arquivo encolhe e todo numero
    de linha reportado sai deslocado - a ferramenta apontaria para o lugar
    errado, que e pior do que nao apontar."""
    def vazio(m):
        return "\n" * m.group(0).count("\n")

    texto = re.sub(r"/\*.*?\*/", vazio, texto, flags=re.S)
    return re.sub(r"<!--.*?-->", vazio, texto, flags=re.S)


def fontes():
    for caminho in sorted((RAIZ / "static").glob("*.css")):
        yield caminho
    for caminho in sorted((RAIZ / "templates").glob("*.html")):
        yield caminho
    yield RAIZ / "core.py"


def analisar():
    achados = collections.defaultdict(list)
    for caminho in fontes():
        texto = sem_definicao_de_tokens(sem_comentarios(caminho.read_text(encoding="utf-8")))
        rel = caminho.relative_to(RAIZ)
        for linha_num, linha in enumerate(texto.splitlines(), 1):
            for tipo, padrao in PROPRIEDADES.items():
                for valor in padrao.findall(linha):
                    valor = valor.strip()
                    if "var(--" in valor or COR_NEUTRA.match(valor):
                        continue
                    if tipo == "cor" and not COR_CRUA.search(valor):
                        continue
                    achados[tipo].append((str(rel), linha_num, valor[:60]))
    return achados


def main():
    achados = analisar()
    detalhar = "--lista" in sys.argv
    total = sum(len(v) for v in achados.values())
    print(f"Valores visuais fora do sistema de tokens: {total}\n")
    for tipo in sorted(achados, key=lambda t: -len(achados[t])):
        itens = achados[tipo]
        print(f"  {tipo:<16} {len(itens):>4}")
        por_arquivo = collections.Counter(a for a, _l, _v in itens)
        for arquivo, quantos in por_arquivo.most_common(6 if not detalhar else 99):
            print(f"      {arquivo:<34} {quantos:>4}")
        if detalhar:
            for arquivo, linha, valor in itens:
                print(f"        {arquivo}:{linha}  {valor}")
        print()
    return total


if __name__ == "__main__":
    main()
