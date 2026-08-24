"""Fluxo completo contra PostgreSQL real, usado somente no job de integracao do CI."""
import importlib.util
import os
import pathlib
import sys
import uuid

import psycopg2
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
    cur.close()
    conn.close()

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
