"""Fluxo completo contra PostgreSQL real, usado somente no job de integracao do CI."""
import importlib.util
import os
import pathlib
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
    assert cur.fetchone()[0] == 12
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
    assert resposta.headers["Location"].endswith("/")


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
        json={"categoria": "Travel", "observacao": "nao sobrescrever", "duplicada": True},
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
    rotas = (
        "/", "/relatorios", "/dre", "/investimentos", "/logs", "/pendencias",
        "/categorias", "/grupos", "/dimensoes", "/contas", "/regras", "/usuarios",
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
    resposta = cliente.get("/?mes=2026-08")
    duracao = time.perf_counter() - inicio
    assert resposta.status_code == 200
    assert duracao < 8.0, f"tela levou {duracao:.2f}s para {total_mes} lancamentos"

    html = resposta.get_data(as_text=True)
    tabela = html.split('<table class="compacta', 1)[1].split("</table>", 1)[0]
    assert tabela.count('data-lazy-options="categoria"') == total_mes
    # Uma opcao atual por caixa; as 84 categorias nao podem voltar a ser
    # repetidas em cada linha, pois isso multiplicaria o HTML e o tempo do DOM.
    dimensoes = tabela.count('data-lazy-options="dimensao"') // total_mes
    assert tabela.count("<option") == total_mes * (1 + dimensoes)

    conn = core.get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM cartao.transacao WHERE descricao LIKE %s;", (prefixo + "%",))
    conn.commit()
    cur.close()
    conn.close()
