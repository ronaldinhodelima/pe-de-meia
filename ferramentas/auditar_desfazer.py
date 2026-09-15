"""Quais rotas GRAVAM no banco e nao registram desfazer.

Le por AST: para cada funcao de rota, procura INSERT/UPDATE/DELETE em
literais de SQL e a chamada a registrar_desfazivel no corpo (inclusive dentro
de helpers que a propria rota chama, no mesmo modulo).
"""
import ast
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parent.parent
ESCRITA = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+cartao\.|DELETE\s+FROM)", re.I)


def textos(no):
    return [n.value for n in ast.walk(no)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def chamadas(no):
    nomes = set()
    for n in ast.walk(no):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                nomes.add(f.id)
            elif isinstance(f, ast.Attribute):
                nomes.add(f.attr)
    return nomes


for arq in sorted((RAIZ / "views").glob("*.py")):
    a = ast.parse(arq.read_text(encoding="utf-8"))
    defs = {n.name: n for n in a.body if isinstance(n, ast.FunctionDef)}
    linhas = []
    for nome, no in defs.items():
        rota = any("bp.route" in ast.unparse(d) for d in no.decorator_list)
        if not rota:
            continue
        # SQL de escrita na propria rota ou nos helpers do modulo que ela chama
        alcance = [no] + [defs[c] for c in chamadas(no) if c in defs]
        escreve = any(ESCRITA.search(s) for alvo in alcance for s in textos(alvo))
        if not escreve:
            continue
        tem = any("registrar_desfazivel" in chamadas(alvo) for alvo in alcance)
        if not tem:
            linhas.append(f"  {nome}  (linha {no.lineno})")
    if linhas:
        print(f"\n{arq.name} — gravam sem desfazer:")
        print("\n".join(linhas))
