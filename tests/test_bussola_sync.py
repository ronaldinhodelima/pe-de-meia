"""Regressoes do worker que sincroniza os dados do Pluggy."""
import importlib.util
import pathlib
import sys
import types


def carregar_worker(monkeypatch):
    monkeypatch.setenv("SYNC_SECRET", "segredo-exclusivo-dos-testes")
    # requests pertence somente a imagem do worker e nao precisa ser instalado
    # na suite principal para estes testes de concorrencia.
    monkeypatch.setitem(sys.modules, "requests", types.ModuleType("requests"))
    caminho = pathlib.Path(__file__).parent.parent / "bussola" / "app.py"
    spec = importlib.util.spec_from_file_location("bussola_app_teste", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_sync_simultaneo_e_recusado_sem_chamar_servicos_externos(monkeypatch):
    worker = carregar_worker(monkeypatch)
    assert worker.SYNC_LOCK.acquire(blocking=False)
    try:
        resultado = worker.run_sync()
    finally:
        worker.SYNC_LOCK.release()

    assert resultado["status"] == "busy"
    assert "andamento" in resultado["detail"]


def test_trava_e_liberada_quando_sync_falha(monkeypatch):
    worker = carregar_worker(monkeypatch)

    def falhar():
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(worker, "_run_sync_unlocked", falhar)
    try:
        worker.run_sync()
    except RuntimeError:
        pass

    assert worker.SYNC_LOCK.acquire(blocking=False)
    worker.SYNC_LOCK.release()


def test_paginacao_repetida_para_e_nao_repete_transacao(monkeypatch):
    worker = carregar_worker(monkeypatch)
    chamadas = []

    def pagina_repetida(path, api_key, params=None):
        chamadas.append(dict(params or {}))
        return {
            "results": [{"id": "tx-1"}],
            "next": "?accountId=conta-1&after=cursor-repetido",
        }

    monkeypatch.setattr(worker, "pluggy_get", pagina_repetida)
    resultado = worker.fetch_all_transactions("chave", "conta-1")

    assert resultado == [{"id": "tx-1"}]
    assert len(chamadas) == 2


def test_endpoint_explica_sync_ocupado_ou_com_erro(monkeypatch):
    worker = carregar_worker(monkeypatch)
    cliente = worker.app.test_client()
    cabecalho = {"X-Sync-Secret": "segredo-exclusivo-dos-testes"}

    monkeypatch.setattr(worker, "run_sync", lambda: {"status": "busy"})
    assert cliente.post("/sync", headers=cabecalho).status_code == 409

    monkeypatch.setattr(worker, "run_sync", lambda: {"status": "error"})
    assert cliente.post("/sync", headers=cabecalho).status_code == 502

    monkeypatch.setattr(worker, "run_sync", lambda: {"status": "warning"})
    assert cliente.post("/sync", headers=cabecalho).status_code == 200


def test_resultado_parcial_nunca_e_registrado_como_sucesso(monkeypatch):
    worker = carregar_worker(monkeypatch)

    assert worker._classificar_resultado_sync(["item-ok"], []) == ("SUCCESS", "ok")
    assert worker._classificar_resultado_sync(["item-ok"], ["item-2 falhou"]) == ("WARNING", "warning")
    assert worker._classificar_resultado_sync([], ["todos falharam"]) == ("ERROR", "error")


def test_falha_de_uma_conexao_nao_impede_as_demais_e_gera_aviso(monkeypatch):
    worker = carregar_worker(monkeypatch)

    class CursorFalso:
        def __init__(self):
            self.comandos = []

        def execute(self, sql, params=None):
            self.comandos.append((sql, params))

        def close(self):
            pass

    class ConexaoFalsa:
        def __init__(self):
            self.cursor_falso = CursorFalso()
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return self.cursor_falso

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            pass

    banco = ConexaoFalsa()
    monkeypatch.setattr(worker, "PLUGGY_CLIENT_ID", "cliente")
    monkeypatch.setattr(worker, "PLUGGY_CLIENT_SECRET", "segredo")
    monkeypatch.setattr(worker, "pluggy_auth", lambda: "api-key")
    monkeypatch.setattr(worker, "get_conn", lambda: banco)
    monkeypatch.setattr(worker, "listar_itens", lambda cur: ["item-com-falha", "item-ok"])
    monkeypatch.setattr(worker, "upsert_item", lambda *args: None)
    monkeypatch.setattr(worker, "upsert_account", lambda *args: None)
    monkeypatch.setattr(worker, "fetch_all_transactions", lambda *args: [])

    def resposta_pluggy(path, api_key, params=None):
        if path == "/items/item-com-falha":
            raise RuntimeError("conexao expirada")
        if path == "/items/item-ok":
            return {"id": "item-ok", "connector": {"name": "Banco OK"}, "status": "UPDATED"}
        if path == "/accounts":
            return {"results": [{"id": "conta-ok", "type": "BANK"}]}
        if path == "/investments":
            return {"results": []}
        raise AssertionError(path)

    monkeypatch.setattr(worker, "pluggy_get", resposta_pluggy)
    resultado = worker._run_sync_unlocked()

    assert resultado["status"] == "warning"
    assert len(resultado["detail"]["conexoes"]) == 2
    assert banco.rollbacks == 1
    log_params = banco.cursor_falso.comandos[-1][1]
    assert log_params[1] == "WARNING"
    assert "conexao expirada" in log_params[4]
