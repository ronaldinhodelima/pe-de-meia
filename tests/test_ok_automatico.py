"""O OK dado pela fatura, com cursor dublado.

Existe porque o defeito que derrubou a importacao nao era de SQL nem de regra:
era acesso por POSICAO num cursor que devolve dicionario. A suite estrutural
nao pega isso - so executar a funcao pega.
"""
import psycopg2.extras
import pytest

from core import marcar_ok_automatico_da_fatura


class CursorDublado:
    """Devolve as respostas na ordem em que a funcao consulta.

    `tipo` decide o formato das linhas: a importacao usa RealDictCursor e as
    migracoes usam cursor comum. A mesma funcao roda nos dois.
    """

    def __init__(self, referencia, candidatos, tipo="dict"):
        self._referencia = referencia
        self._candidatos = candidatos
        self._tipo = tipo
        self._proxima = None
        self.rowcount = 0
        self.executados = []

    def _linha(self, valores, colunas):
        if self._tipo == "dict":
            return psycopg2.extras.RealDictRow(zip(colunas, valores))
        return tuple(valores)

    def execute(self, sql, params=None):
        self.executados.append(" ".join(sql.split()))
        if "FROM cartao.fatura_importada" in sql:
            self._proxima = ("uma", self._linha(
                self._referencia, ["ano_referencia", "mes_referencia"]))
        elif sql.strip().upper().startswith("UPDATE"):
            self.rowcount = len(self._candidatos)
            self._proxima = ("nenhuma", None)
        else:
            self._proxima = ("varias", [
                self._linha([t], ["transacao_id"]) for t in self._candidatos
            ])

    def fetchone(self):
        return self._proxima[1]

    def fetchall(self):
        return self._proxima[1]


@pytest.mark.parametrize("tipo", ["dict", "tupla"])
def test_funciona_com_os_dois_tipos_de_cursor(tipo):
    """A importacao usa RealDictCursor: acessar `referencia[1]` ali levanta
    KeyError e derruba a importacao inteira, com 500 na tela."""
    cur = CursorDublado((2026, 8), ["aaa", "bbb"], tipo=tipo)
    resultado = marcar_ok_automatico_da_fatura(cur, 42)
    assert resultado["rotulo"] == "fatura 08/2026"
    assert resultado["marcados"] == 2


def test_previa_nao_grava_nada():
    cur = CursorDublado((2026, 8), ["aaa"], tipo="dict")
    resultado = marcar_ok_automatico_da_fatura(cur, 42, preview=True)
    assert resultado["marcados"] == 1
    assert not any(s.upper().startswith("UPDATE") for s in cur.executados)


def test_fatura_inexistente_nao_assina_nada():
    cur = CursorDublado(None, [], tipo="dict")
    cur.execute = lambda *a, **k: setattr(cur, "_proxima", ("uma", None))
    assert marcar_ok_automatico_da_fatura(cur, 999)["marcados"] == 0


def test_uuid_vai_como_texto_para_a_comparacao():
    """Postgres nao tem operador uuid = text: sem ::text dos dois lados a
    consulta estoura."""
    cur = CursorDublado((2026, 8), ["aaa"], tipo="dict")
    marcar_ok_automatico_da_fatura(cur, 42)
    update = [s for s in cur.executados if s.upper().startswith("UPDATE")][0]
    assert "transacao_id::text = ANY(%s)" in update
    selecao = [s for s in cur.executados if "elegiveis" in s][0]
    assert "e.transacao_id::text AS transacao_id" in selecao
