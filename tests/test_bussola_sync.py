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
