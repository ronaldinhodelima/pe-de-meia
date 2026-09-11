"""Pendente e confirmado do mesmo evento (decisao do usuario, 11/09/2026).

O banco confirma o debito com OUTRO id e o pendente fica para tras; os dois
contavam no DRE. O criterio que liga os dois e estreito de proposito."""
import pathlib
import re

RAIZ = pathlib.Path(__file__).parent.parent


def _core():
    return (RAIZ / "core.py").read_text(encoding="utf-8")


def _sql():
    # lido do texto: importar o core roda as migracoes e exige o banco
    return _core().split("PARES_PENDENTE_CONFIRMADO_SQL = (", 1)[1].split("\n)\n", 1)[0]


def test_criterio_do_par_e_estreito():
    sql = _sql()
    assert "c.account_id = p.account_id" in sql
    assert "COALESCE(c.valor_brl, c.valor_original) = COALESCE(p.valor_brl, p.valor_original)" in sql
    assert "regexp_replace" in sql and "upper(" in sql
    assert "<= 3*86400" in sql
    assert "'PENDING'" in sql and "'POSTED'" in sql
    # um candidato de cada lado: com dois, quem decide e o usuario
    assert "HAVING count(*) = 1" in sql
    assert "u2.confirmado = u.confirmado) = 1" in sql


def test_pendente_com_ok_rateio_ou_fatura_fica_para_o_usuario():
    sql = _sql()
    assert "NOT COALESCE(p.conferida,false)" in sql
    assert "cartao.transacao_rateio" in sql
    assert "cartao.fatura_vinculo" in sql


def test_nunca_apaga_nem_toca_no_ok():
    fonte = _core()
    corpo = fonte.split("def vincular_pendentes_confirmados", 1)[1].split("\ndef ", 1)[0]
    assert "DELETE" not in corpo
    assert "conferida =" not in corpo.replace("NOT COALESCE(c.conferida,false)", "")
    assert "substituido_por = %s::uuid" in corpo
    # classificacao so entra onde o confirmado esta vazio e sem OK
    assert "WHERE cartao.transacao_dimensao.valor_id IS NULL" in corpo
    assert "COALESCE(c.observacao,'') = ''" in corpo


def test_migracao_63_faz_backup_antes():
    fonte = _core()
    bloco = fonte.split("if versao_atual < 63:", 1)[1].split("if versao_atual <", 1)[0]
    assert bloco.index("pendente_backup_v63") < bloco.index("feito = vincular_pendentes_confirmados(cur)")
    assert "VALUES (63)" in bloco


def test_hora_embaixo_da_data_sem_hora_inventada():
    tpl = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    celula = re.search(r'<td class="cel-data-fatura" data-col="data" data-tip[^\n]+', tpl).group(0)
    assert "data-hora" in celula
    assert "quando.hour or quando.minute" in celula  # 00:00 e "sem hora"
    assert "!= 'F'" in celula  # criado pela fatura tem hora padrao


def test_suspeita_de_duplicidade_ignora_par_ja_resolvido():
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    consulta = view.split("SELECT array_agg(t.transacao_id::text) AS ids", 1)[1].split("HAVING", 1)[0]
    assert "t.substituido_por IS NULL" in consulta
    assert "somente_conciliacao" in consulta


def test_ordenacao_por_data_le_a_hora_sem_colar_no_ano():
    js = (RAIZ / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    trecho = js.split("if (chave === 'data') {", 1)[1].split("if (chave === 'valor')", 1)[0]
    assert ".split('/')" not in trecho
    assert "(\\d{2}):(\\d{2})" in trecho
