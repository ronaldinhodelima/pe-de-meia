"""Fluxo completo contra PostgreSQL real, usado somente no job de integracao do CI."""
import importlib.util
import os
import pathlib
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import psycopg2.extras
import pytest


RAIZ = pathlib.Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
    reason="executado somente no job com PostgreSQL temporario",
)


def _carregar_worker():
    caminho = RAIZ / "bussola" / "app.py"
    spec = importlib.util.spec_from_file_location("bussola_app_integracao", caminho)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def sistema_real():
    """Cria o esquema do zero na mesma ordem dos dois servicos de producao."""
    worker = _carregar_worker()
    worker.run_migration()
    assert worker.STATE["migration"] == "ok", worker.STATE

    import core
    import app as webapp

    # core.migrate() ja roda no import. A segunda chamada prova que reiniciar o
    # aplicativo sobre um banco pronto nao repete nem quebra migracoes.
    core.migrate()
    worker.run_migration()
    assert worker.STATE["migration"] == "ok", worker.STATE

    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(versao) FROM cartao.schema_version;")
    versao = cur.fetchone()[0]
    # O numero era escrito a mao aqui (`== 12`) e ficou parado enquanto as
    # migracoes passavam de 12 para 65: o teste so falharia no CI, entao a
    # fixture inteira estava quebrada sem ninguem ver. Agora ele cobra a REGRA
    # - o banco chegou na ultima migracao que o core conhece - em vez de um
    # numero que envelhece a cada migracao nova.
    ultima_conhecida = max(
        int(n) for n in re.findall(r"versao_atual < (\d+)", (RAIZ / "core.py").read_text(encoding="utf-8"))
    )
    assert versao == ultima_conhecida, (
        f"banco na versao {versao}, core conhece ate {ultima_conhecida}"
    )
    cur.execute(
        "INSERT INTO cartao.usuario (usuario, nome, senha_hash, perfil, permissoes) "
        "VALUES ('integracao', 'Integração', %s, 'admin', %s) "
        "ON CONFLICT (usuario) DO UPDATE SET senha_hash=EXCLUDED.senha_hash, "
        "perfil=EXCLUDED.perfil, permissoes=EXCLUDED.permissoes, ativo=true;",
        (core.hash_senha("senha-teste"), core.permissoes_do_perfil("admin")),
    )
    conn.commit()
    cur.close()
    conn.close()

    webapp.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    return worker, core, webapp


def _login(cliente):
    resposta = cliente.post("/login", data={"usuario": "integracao", "senha": "senha-teste"})
    assert resposta.status_code == 302
    # o destino do login e a tela de Lancamentos desde 09/09/2026, e mora num
    # ponto unico (core.URL_LANCAMENTOS). Escrito a mao aqui, ficou em "/" e
    # derrubou os seis testes desta suite - que so roda no CI e por isso passou
    # despercebido, como o `== 12` da versao do schema logo acima.
    import core

    assert resposta.headers["Location"].endswith(core.URL_LANCAMENTOS)


def test_fluxo_manual_completo_no_postgres_real(sistema_real):
    _worker, core, webapp = sistema_real
    cliente = webapp.app.test_client()
    _login(cliente)
    descricao = f"INTEGRACAO MANUAL {uuid.uuid4()}"

    resposta = cliente.post(
        "/api/lancamento-manual",
        json={
            "data": "2026-08-23", "descricao": descricao,
            "direcao": "saida", "valor": "123,45", "categoria": "Groceries",
        },
    )
    assert resposta.status_code == 200
    assert resposta.get_json()["ok"] is True

    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT transacao_id, valor_original, categoria, categoria_manual "
        "FROM cartao.transacao WHERE descricao = %s;",
        (descricao,),
    )
    transacao_id, valor, categoria, categoria_manual = cur.fetchone()
    assert float(valor) == -123.45
    assert categoria == "Groceries"
    assert categoria_manual is True
    cur.execute(
        "SELECT d.id, dv.id FROM cartao.dimensao d "
        "JOIN cartao.dimensao_valor dv ON dv.dimensao_id=d.id "
        "WHERE d.obrigatoria=true ORDER BY d.ordem, dv.id LIMIT 1;"
    )
    dimensao_id, valor_id = cur.fetchone()
    cur.close()
    conn.close()

    resposta = cliente.post(
        f"/api/transacao/{transacao_id}",
        json={
            "categoria": "Travel", "observacao": "ajuste preservado",
            "dimensoes": {str(dimensao_id): valor_id}, "conferida": True,
        },
    )
    assert resposta.status_code == 200
    assert resposta.get_json()["bloqueada"] is False

    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT categoria, categoria_manual, observacao, conferida, conferida_por "
        "FROM cartao.transacao WHERE transacao_id=%s;",
        (transacao_id,),
    )
    assert cur.fetchone() == ("Travel", True, "ajuste preservado", True, "integracao")

    resposta = cliente.post(
        f"/api/transacao/{transacao_id}",
        json={"conferida": False},
    )
    assert resposta.status_code == 409
    assert "Confirme" in resposta.get_json()["erro"]

    # Simula um OK antigo anterior a uma dimensao obrigatoria. Mesmo faltando o
    # vinculo, editar outro campo nao pode apagar a assinatura humana.
    cur.execute(
        "DELETE FROM cartao.transacao_dimensao WHERE transacao_id=%s AND dimensao_id=%s;",
        (transacao_id, dimensao_id),
    )
    conn.commit()
    cur.close()
    conn.close()

    resposta = cliente.post(
        f"/api/transacao/{transacao_id}",
        json={"observacao": "OK antigo preservado"},
    )
    assert resposta.status_code == 200
    assert resposta.get_json()["conferida"] is True

    # Editar classificacao depois do OK nao pode desmarcar a conferencia. A
    # resposta tambem devolve o estado real para a tela se sincronizar com o DB.
    resposta = cliente.post(
        f"/api/transacao/{transacao_id}",
        json={
            "categoria": "Fuel", "observacao": "ajuste posterior ao OK",
            "dimensoes": {str(dimensao_id): valor_id},
        },
    )
    assert resposta.status_code == 200
    assert resposta.get_json()["conferida"] is True
    assert resposta.get_json()["conferida_por"] == "integracao"
    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT categoria, observacao, conferida, conferida_por "
        "FROM cartao.transacao WHERE transacao_id=%s;",
        (transacao_id,),
    )
    assert cur.fetchone() == ("Fuel", "ajuste posterior ao OK", True, "integracao")
    cur.close()
    conn.close()

    resposta = cliente.post(
        f"/api/transacao/{transacao_id}",
        json={"conferida": False, "confirmar_desmarcacao": True},
    )
    assert resposta.status_code == 200
    assert resposta.get_json()["conferida"] is False

    resposta = cliente.post(
        f"/api/transacao/{transacao_id}",
        json={"duplicada": True},
    )
    assert resposta.status_code == 409
    resposta = cliente.post(
        f"/api/transacao/{transacao_id}",
        json={"duplicada": True, "confirmar_duplicada": True},
    )
    assert resposta.status_code == 200
    assert resposta.get_json()["duplicada"] is True

    resposta = cliente.delete(f"/api/lancamento-manual/{transacao_id}")
    assert resposta.status_code == 200
    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM cartao.transacao WHERE transacao_id=%s;", (transacao_id,))
    assert cur.fetchone()[0] == 0
    cur.close()
    conn.close()


def test_nova_sincronizacao_preserva_ajustes_humanos(sistema_real):
    worker, core, webapp = sistema_real
    cliente = webapp.app.test_client()
    _login(cliente)
    item_id = str(uuid.uuid4())
    conta_id = str(uuid.uuid4())
    transacao_id = str(uuid.uuid4())

    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO cartao.pluggy_item (item_id, connector_name, status) VALUES (%s,'Banco Teste','OK');",
        (item_id,),
    )
    cur.execute(
        "INSERT INTO cartao.conta (account_id, item_id, nome, tipo) VALUES (%s,%s,'Conta Teste','BANK');",
        (conta_id, item_id),
    )
    primeira = {
        "id": transacao_id, "accountId": conta_id, "description": "COMPRA INTEGRACAO",
        "descriptionRaw": "COMPRA INTEGRACAO", "amount": -50, "currencyCode": "BRL",
        "amountInAccountCurrency": -50, "date": "2026-08-20T12:00:00Z",
        "category": "Groceries", "categoryId": "1", "status": "POSTED", "type": "DEBIT",
        "createdAt": "2026-08-20T12:00:00Z", "updatedAt": "2026-08-20T12:00:00Z",
    }
    assert worker.upsert_transaction(cur, primeira) is True
    conn.commit()
    cur.close()
    conn.close()

    resposta = cliente.post(
        f"/api/transacao/{transacao_id}",
        json={
            "categoria": "Travel", "observacao": "nao sobrescrever",
            "duplicada": True, "confirmar_duplicada": True,
        },
    )
    assert resposta.status_code == 200

    conn = core.get_conn()
    cur = conn.cursor()
    atualizada = {
        **primeira,
        "amountInAccountCurrency": -55,
        "date": "2026-08-21T15:00:00Z",
        "category": "Fuel",
        "status": "PENDING",
        "updatedAt": "2026-08-21T15:00:00Z",
    }
    assert worker.upsert_transaction(cur, atualizada) is False
    conn.commit()
    cur.execute(
        "SELECT categoria, categoria_manual, observacao, duplicada, status, valor_brl, data_transacao "
        "FROM cartao.transacao WHERE transacao_id=%s;",
        (transacao_id,),
    )
    categoria, manual, observacao, duplicada, status, valor_brl, data_transacao = cur.fetchone()
    assert (categoria, manual, observacao, duplicada, status, float(valor_brl)) == (
        "Travel", True, "nao sobrescrever", True, "PENDING", -55.0,
    )
    assert data_transacao.isoformat().startswith("2026-08-21T15:00:00")
    cur.close()
    conn.close()


def test_edicoes_simultaneas_nao_apagam_campos_uma_da_outra(sistema_real):
    _worker, core, webapp = sistema_real
    transacao_id = str(uuid.uuid4())
    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO cartao.transacao ("
        "transacao_id, account_id, descricao, valor_original, valor_brl, data_transacao, "
        "categoria, status, tipo, criado_em, atualizado_em, sincronizado_em"
        ") VALUES (%s,%s,'CONCORRENCIA REAL',-10,-10,'2026-08-22T12:00:00Z',"
        "'Groceries','POSTED','DEBIT',now(),now(),now());",
        (transacao_id, core.CONTA_MANUAL_ID),
    )
    conn.commit()
    cur.close()
    conn.close()

    barreira = threading.Barrier(2)

    def editar(payload):
        cliente = webapp.app.test_client()
        _login(cliente)
        barreira.wait(timeout=5)
        resposta = cliente.post(f"/api/transacao/{transacao_id}", json=payload)
        return resposta.status_code, resposta.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futuro_categoria = executor.submit(editar, {"categoria": "Travel"})
        futuro_observacao = executor.submit(editar, {"observacao": "gravada em paralelo"})
        resultados = [futuro_categoria.result(timeout=10), futuro_observacao.result(timeout=10)]

    assert all(status == 200 and corpo["ok"] for status, corpo in resultados)
    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT categoria, categoria_manual, observacao FROM cartao.transacao WHERE transacao_id=%s;",
        (transacao_id,),
    )
    assert cur.fetchone() == ("Travel", True, "gravada em paralelo")
    cur.close()
    conn.close()


def test_todas_as_telas_principais_abrem_no_postgres_real(sistema_real):
    _worker, _core, webapp = sistema_real
    cliente = webapp.app.test_client()
    _login(cliente)
    # "/" so redireciona desde que a Resumida saiu (10/09/2026); a tela de
    # lancamentos e /lancamentos/fatura - que nem estava nesta lista.
    rotas = (
        "/lancamentos/fatura", "/relatorios", "/dre", "/investimentos", "/logs",
        "/pendencias", "/categorias", "/grupos", "/dimensoes", "/contas",
        "/regras", "/usuarios",
    )

    for rota in rotas:
        resposta = cliente.get(rota)
        assert resposta.status_code == 200, rota
        html = resposta.get_data(as_text=True)
        assert "Internal Server Error" not in html, rota
        assert "Pé de Meia" in html, rota


def test_renomear_dimensao_trata_nome_repetido_sem_erro_500(sistema_real):
    _worker, core, webapp = sistema_real
    sufixo = uuid.uuid4().hex
    nome_a = f"DIM A {sufixo}"
    nome_b = f"DIM B {sufixo}"
    nome_novo = f"DIM C {sufixo}"

    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO cartao.dimensao (nome, obrigatoria, ordem) VALUES (%s,false,99) RETURNING id;",
        (nome_a,),
    )
    dimensao_a = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cartao.dimensao (nome, obrigatoria, ordem) VALUES (%s,false,99) RETURNING id;",
        (nome_b,),
    )
    dimensao_b = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    cliente = webapp.app.test_client()
    _login(cliente)
    resposta = cliente.post(
        "/dimensoes",
        data={"acao": "editar_dimensao", "dimensao_id": dimensao_a, "nome": nome_b},
    )
    assert resposta.status_code == 200
    assert "Já existe uma dimensão" in resposta.get_data(as_text=True)

    resposta = cliente.post(
        "/dimensoes",
        data={"acao": "editar_dimensao", "dimensao_id": dimensao_a, "nome": nome_novo},
    )
    assert resposta.status_code == 200

    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT nome FROM cartao.dimensao WHERE id=%s;", (dimensao_a,))
    assert cur.fetchone()[0] == nome_novo
    cur.execute("DELETE FROM cartao.dimensao WHERE id IN (%s,%s);", (dimensao_a, dimensao_b))
    conn.commit()
    cur.close()
    conn.close()


def test_tela_suporta_dez_vezes_o_volume_atual(sistema_real):
    _worker, core, webapp = sistema_real
    quantidade = 1200
    prefixo = f"CARGA {uuid.uuid4()}"
    valores = []
    for i in range(quantidade):
        dia = i % 28 + 1
        valores.append((
            str(uuid.uuid4()), core.CONTA_MANUAL_ID, f"{prefixo} {i}", -10, -10,
            f"2026-08-{dia:02d} 12:00:00-03:00", "Groceries", "POSTED", "DEBIT",
        ))

    conn = core.get_conn()
    cur = conn.cursor()
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO cartao.transacao ("
        "transacao_id, account_id, descricao, valor_original, valor_brl, data_transacao, "
        "categoria, status, tipo, criado_em, atualizado_em, sincronizado_em"
        ") VALUES %s;",
        valores,
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now(),now())",
        page_size=500,
    )
    conn.commit()
    cur.execute(
        "SELECT COUNT(*) FROM cartao.transacao "
        "WHERE data_transacao >= '2026-08-01T00:00:00-03:00' "
        "AND data_transacao < '2026-09-01T00:00:00-03:00';"
    )
    total_mes = cur.fetchone()[0]
    cur.close()
    conn.close()

    cliente = webapp.app.test_client()
    _login(cliente)
    inicio = time.perf_counter()
    resposta = cliente.get("/lancamentos/fatura?recorte=periodo&mes=2026-08&periodo=mes")
    duracao = time.perf_counter() - inicio
    assert resposta.status_code == 200
    # O que o usuario sente e o tempo de abrir. A Resumida carregava as opcoes
    # sob demanda (uma por caixa); a Detalhada, que ficou, traz a lista inteira
    # em cada linha - e e esse custo que este teste passou a medir (10/09/2026).
    assert duracao < 8.0, f"tela levou {duracao:.2f}s para {total_mes} lancamentos"

    html = resposta.get_data(as_text=True)
    tabela = html.split('<table class="compacta', 1)[1].split("</table>", 1)[0]
    # cada lancamento do mes vira uma linha: nenhum some da tela com o volume
    assert tabela.count('data-linha="') == total_mes

    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM cartao.transacao WHERE descricao LIKE %s;", (prefixo + "%",))
    conn.commit()
    cur.close()
    conn.close()


def test_regra_mais_especifica_manda_o_gasto_para_outro_centro(sistema_real):
    """A mesma categoria em dois centros, separada pela dimensao (migracao 65).

    Era o caso que motivou a mudanca: "Seguros" e usada tanto no seguro do carro
    quanto no seguro de vida da familia, e ate aqui o centro de custo era da
    CATEGORIA inteira - o seguro de vida contava dentro de Transporte.

    Isto so se prova com banco de verdade: quem resolve a regra e SQL, e cursor
    dublado nao executa SQL nenhum (secao 10.4 n.7).
    """
    _worker, core, _webapp = sistema_real
    import uuid as _uuid

    import psycopg2.extras

    conn = core.get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    marca = _uuid.uuid4().hex[:8]

    item = str(_uuid.uuid4())
    conta = str(_uuid.uuid4())
    cur.execute(
        "INSERT INTO cartao.pluggy_item (item_id, connector_name, status) "
        "VALUES (%s,'Teste','OK');", (item,))
    cur.execute(
        "INSERT INTO cartao.conta (account_id, item_id, nome, tipo) "
        "VALUES (%s,%s,'Cartao Regra','CREDIT');", (conta, item))
    cur.execute(
        "INSERT INTO cartao.categoria_natureza (categoria, natureza) VALUES ('Insurance','despesa') "
        "ON CONFLICT (categoria) DO UPDATE SET natureza='despesa';")

    cur.execute("SELECT id FROM cartao.dimensao ORDER BY ordem, id LIMIT 1;")
    dimensao = cur.fetchone()["id"]

    def valor(nome):
        cur.execute(
            "INSERT INTO cartao.dimensao_valor (dimensao_id, nome) VALUES (%s,%s) "
            "ON CONFLICT (dimensao_id, nome) DO UPDATE SET nome=EXCLUDED.nome RETURNING id;",
            (dimensao, f"{nome} {marca}"))
        return cur.fetchone()["id"]

    do_carro, da_vida = valor("Veiculos"), valor("Protecao")

    cur.execute("INSERT INTO cartao.grupo_custo (nome) VALUES (%s) RETURNING id;", (f"Transporte {marca}",))
    grupo_a = cur.fetchone()["id"]
    cur.execute("INSERT INTO cartao.grupo_custo (nome) VALUES (%s) RETURNING id;", (f"Protecao {marca}",))
    grupo_b = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO cartao.subgrupo_custo (grupo_id, nome) VALUES (%s,'Manutencao') RETURNING id;",
        (grupo_a,))
    sub_carro = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO cartao.subgrupo_custo (grupo_id, nome) VALUES (%s,'Seguro de Vida') RETURNING id;",
        (grupo_b,))
    sub_vida = cur.fetchone()["id"]

    # padrao da categoria: seguro de vida. Especifica: quando a dimensao diz carro.
    core.definir_regra_padrao(cur, "Insurance", sub_vida)
    cur.execute(
        "INSERT INTO cartao.centro_regra (categoria, subgrupo_id) VALUES ('Insurance',%s) RETURNING id;",
        (sub_carro,))
    especifica = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO cartao.centro_regra_dimensao (regra_id, dimensao_id, valor_id) VALUES (%s,%s,%s);",
        (especifica, dimensao, do_carro))

    def lancar(valor_brl, valor_id):
        tid = str(_uuid.uuid4())
        cur.execute(
            "INSERT INTO cartao.transacao (transacao_id, account_id, data_transacao, descricao, "
            "valor_brl, categoria, status) VALUES (%s,%s,'2026-05-10 12:00+00',%s,%s,'Insurance','POSTED');",
            (tid, conta, f"Seguro {marca}", valor_brl))
        if valor_id:
            cur.execute(
                "INSERT INTO cartao.transacao_dimensao (transacao_id, dimensao_id, valor_id) "
                "VALUES (%s,%s,%s);", (tid, dimensao, valor_id))

    lancar(300, do_carro)
    lancar(700, da_vida)
    lancar(50, None)          # sem dimensao: cai no padrao
    conn.commit()

    inicio, fim = core.intervalo_ano_local("2026")
    totais, solto, _ = core.totais_por_subgrupo(cur, inicio, fim)
    assert totais.get(sub_carro) == 300.0, "o seguro do carro tem que ir para Transporte"
    assert totais.get(sub_vida) == 750.0, "o de vida e o sem dimensao ficam no padrao"
    assert solto == 0.0, "nada pode ficar fora do centro de custo aqui"

    # tirando a regra especifica, tudo volta para o padrao: quem separou os dois
    # foi a condicao, nao o acaso do desempate
    cur.execute("DELETE FROM cartao.centro_regra WHERE id = %s;", (especifica,))
    conn.commit()
    totais_sem, _, _ = core.totais_por_subgrupo(cur, inicio, fim)
    assert totais_sem.get(sub_vida) == 1050.0
    assert totais_sem.get(sub_carro) is None

    cur.execute("DELETE FROM cartao.transacao WHERE descricao = %s;", (f"Seguro {marca}",))
    cur.execute("DELETE FROM cartao.grupo_custo WHERE id IN (%s,%s);", (grupo_a, grupo_b))
    cur.execute("DELETE FROM cartao.conta WHERE account_id = %s;", (conta,))
    cur.execute("DELETE FROM cartao.pluggy_item WHERE item_id = %s;", (item,))
    conn.commit()
    cur.close()
    conn.close()


def test_desfazer_devolve_o_estado_anterior_e_recusa_o_que_nao_pode(sistema_real):
    """Desfazer e GRAVACAO: precisa ser exercitado, nao lido (migracao 66).

    Cobre o que da errado caro: a volta tem que restaurar o estado anterior
    inteiro (inclusive as condicoes de uma regra apagada, senao ela volta mais
    generica e muda o DRE em silencio), a fila e por usuario, e a lista branca
    recusa o que a secao 1.2 reserva ao clique humano - o OK.
    """
    _worker, core, _webapp = sistema_real
    import uuid as _uuid

    import psycopg2.extras
    import pytest as _pytest

    conn = core.get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    marca = _uuid.uuid4().hex[:6]

    cur.execute("INSERT INTO cartao.grupo_custo (nome) VALUES (%s) RETURNING id;", (f"A {marca}",))
    grupo = cur.fetchone()["id"]
    cur.execute("INSERT INTO cartao.subgrupo_custo (grupo_id, nome) VALUES (%s,'um') RETURNING id;", (grupo,))
    sub_a = cur.fetchone()["id"]
    cur.execute("INSERT INTO cartao.subgrupo_custo (grupo_id, nome) VALUES (%s,'dois') RETURNING id;", (grupo,))
    sub_b = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO cartao.centro_regra (categoria, subgrupo_id) VALUES ('Insurance',%s) RETURNING id;",
        (sub_a,))
    regra = cur.fetchone()["id"]
    conn.commit()

    # mover e desfazer
    core.registrar_desfazivel(cur, "voltar", [{
        "op": "update", "tabela": "cartao.centro_regra",
        "onde": {"id": regra}, "valores": {"subgrupo_id": sub_a},
    }], usuario=f"u{marca}")
    cur.execute("UPDATE cartao.centro_regra SET subgrupo_id=%s WHERE id=%s;", (sub_b, regra))
    conn.commit()
    core.desfazer_ultima_acao(cur, f"u{marca}")
    conn.commit()
    cur.execute("SELECT subgrupo_id FROM cartao.centro_regra WHERE id=%s;", (regra,))
    assert cur.fetchone()["subgrupo_id"] == sub_a, "desfazer tem que devolver a regra"

    # cada um desfaz o proprio passo
    core.registrar_desfazivel(cur, "da outra pessoa", [{
        "op": "update", "tabela": "cartao.centro_regra",
        "onde": {"id": regra}, "valores": {"subgrupo_id": sub_a},
    }], usuario=f"outro{marca}")
    conn.commit()
    assert core.desfazer_ultima_acao(cur, f"u{marca}")[0] is None

    # Passo que mexe no OK nao e recusado - ele existe para recriar um
    # lancamento manual excluido por engano, com a assinatura que ele tinha.
    # A trava da secao 1.2 mudou de lugar: a tela marca a acao como
    # `exige_confirmacao` e pede o segundo "sim" antes de retirar um OK.
    core.registrar_desfazivel(cur, "mexe no OK", [{
        "op": "update", "tabela": "cartao.transacao",
        "onde": {"transacao_id": "x"}, "valores": {"conferida": False},
    }], usuario=f"u{marca}")
    conn.commit()
    topo = core.acoes_desfazeis(cur, f"u{marca}")[0]
    assert topo["exige_confirmacao"] is True, "retirar OK tem que pedir confirmacao"

    # Vale para QUALQUER coluna de conferencia, nao so o OK do lancamento:
    # `conferida_repeticao` tambem registra que uma pessoa olhou aquele grupo,
    # e apaga-la sem perguntar perderia revisao humana do mesmo jeito.
    core.registrar_desfazivel(cur, "mexe na revisao da repeticao", [{
        "op": "update", "tabela": "cartao.fatura_linha",
        "onde": {"id": 1}, "valores": {"conferida_repeticao": False},
    }], usuario=f"u{marca}")
    conn.commit()
    assert core.acoes_desfazeis(cur, f"u{marca}")[0]["exige_confirmacao"] is True

    # e uma acao que nao toca conferencia nenhuma NAO pode pedir confirmacao -
    # pedir sempre ensina o usuario a clicar "sim" sem ler
    core.registrar_desfazivel(cur, "so o nome", [{
        "op": "update", "tabela": "cartao.grupo_custo",
        "onde": {"id": grupo}, "valores": {"nome": "x"},
    }], usuario=f"u{marca}")
    conn.commit()
    assert core.acoes_desfazeis(cur, f"u{marca}")[0]["exige_confirmacao"] is False

    # tabela e coluna fora da lista branca continuam recusadas
    for passo in (
        {"op": "update", "tabela": "cartao.usuario",
         "onde": {"usuario": "x"}, "valores": {"perfil": "admin"}},
        {"op": "update", "tabela": "cartao.transacao",
         "onde": {"transacao_id": "x"}, "valores": {"observacao_sistema": "x"}},
    ):
        with _pytest.raises(ValueError):
            core.registrar_desfazivel(cur, "nao", [passo], usuario=f"u{marca}")
    conn.rollback()

    cur.execute("DELETE FROM cartao.acao_desfazivel WHERE usuario LIKE %s;", (f"%{marca}",))
    cur.execute("DELETE FROM cartao.grupo_custo WHERE id=%s;", (grupo,))
    conn.commit()
    cur.close()
    conn.close()


def test_toda_coluna_da_lista_do_desfazer_existe_mesmo_no_banco(sistema_real):
    """A whitelist do desfazer nao pode citar coluna que nao existe.

    Ela e escrita a mao, e nome inventado nao quebra nada ate alguem apertar
    Desfazer: so entao o UPDATE levanta UndefinedColumn, com a acao ja gravada
    e sem volta. Foi assim que `regra_classificacao.trecho`, `cartao_nome.apelido`
    e `compra_futura` sem `situacao` entraram e sobreviveram a suite inteira -
    a varredura estrutural le codigo, e so o banco sabe quais colunas existem.
    """
    import core

    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT table_schema || '.' || table_name, column_name "
        "FROM information_schema.columns WHERE table_schema = 'cartao';"
    )
    reais = {}
    for tabela, coluna in cur.fetchall():
        reais.setdefault(tabela, set()).add(coluna)
    cur.close()
    conn.close()

    faltando = []
    for tabela, colunas in core.DESFAZER_PERMITIDO.items():
        if tabela not in reais:
            faltando.append(f"{tabela} (tabela inexistente)")
            continue
        for coluna in sorted(colunas - reais[tabela]):
            faltando.append(f"{tabela}.{coluna}")
    assert not faltando, "colunas na lista do desfazer que nao existem: " + ", ".join(faltando)


def test_desfazer_em_lote_alcanca_exatamente_os_lancamentos_movidos(sistema_real):
    """Mover categoria em lote e voltar: so os movidos voltam.

    A volta e por LISTA de transacao_id. Duas coisas se provam aqui e nenhuma
    aparece sem Postgres: `transacao_id = ANY(...)` com uuid contra texto
    derruba a consulta inteira (secao 10.4 n.6), e reverter por "categoria =
    destino" levaria junto quem ja estava no destino antes.
    """
    _worker, core, _webapp = sistema_real
    marca = uuid.uuid4().hex[:8]
    conn = core.get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "INSERT INTO cartao.pluggy_item (item_id, connector_name) VALUES (%s,%s) "
        "ON CONFLICT DO NOTHING;",
        (str(uuid.uuid4()), f"lote{marca}"),
    )
    cur.execute("SELECT item_id FROM cartao.pluggy_item LIMIT 1;")
    item = cur.fetchone()["item_id"]
    conta = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO cartao.conta (account_id, item_id, nome, tipo) VALUES (%s,%s,%s,'MANUAL');",
        (conta, item, f"lote{marca}"),
    )

    origem, destino = f"Ori{marca}", f"Des{marca}"
    ids = []
    for n in range(3):
        tid = str(uuid.uuid4())
        ids.append(tid)
        # o terceiro ja NASCE no destino: ele nao pode ser tocado pelo desfazer
        cur.execute(
            "INSERT INTO cartao.transacao (transacao_id, account_id, descricao, "
            "valor_brl, data_transacao, categoria) VALUES (%s,%s,%s,%s,now(),%s);",
            (tid, conta, f"t{n}", -10, destino if n == 2 else origem),
        )

    cur.execute("SELECT transacao_id FROM cartao.transacao WHERE categoria=%s;", (origem,))
    movidos = [str(r["transacao_id"]) for r in cur.fetchall()]
    assert len(movidos) == 2

    cur.execute(
        "UPDATE cartao.transacao SET categoria=%s WHERE categoria=%s;", (destino, origem)
    )
    core.registrar_desfazivel(cur, "voltar", [{
        "op": "update", "tabela": "cartao.transacao",
        "onde": {"transacao_id": movidos}, "valores": {"categoria": origem},
    }], usuario=f"u{marca}")
    conn.commit()

    core.desfazer_ultima_acao(cur, f"u{marca}")
    conn.commit()

    cur.execute(
        "SELECT transacao_id, categoria FROM cartao.transacao WHERE account_id::text=%s;",
        (conta,),
    )
    por_id = {str(r["transacao_id"]): r["categoria"] for r in cur.fetchall()}
    assert por_id[ids[0]] == origem and por_id[ids[1]] == origem, "os movidos tem que voltar"
    assert por_id[ids[2]] == destino, "quem ja estava no destino nao pode ser arrastado junto"

    cur.execute("DELETE FROM cartao.acao_desfazivel WHERE usuario=%s;", (f"u{marca}",))
    cur.execute("DELETE FROM cartao.transacao WHERE account_id::text=%s;", (conta,))
    cur.execute("DELETE FROM cartao.conta WHERE account_id::text=%s;", (conta,))
    conn.commit()
    cur.close()
    conn.close()
