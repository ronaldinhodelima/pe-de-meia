"""Regressoes dos controles que protegem autenticacao e dados financeiros."""
import pathlib

import app
import core
from views import auth, lancamentos


def test_post_sem_origem_e_bloqueado_fora_de_testes():
    anterior = app.app.config["TESTING"]
    app.app.config["TESTING"] = False
    try:
        resposta = app.app.test_client().post("/login", data={"usuario": "x", "senha": "y"})
        assert resposta.status_code == 403
    finally:
        app.app.config["TESTING"] = anterior


def test_editar_nao_da_permissao_de_conferir(monkeypatch):
    monkeypatch.setattr(core, "validar_sessao_atual", lambda: True)
    cliente = app.app.test_client()
    with cliente.session_transaction() as sessao:
        sessao["user"] = "operador"
        sessao["permissoes"] = ["lancamentos_editar"]

    resposta = cliente.post(
        "/api/transacao/qualquer", json={"conferida": True},
        headers={"Origin": "http://localhost"},
    )
    assert resposta.status_code == 403
    assert "Sem permissão" in resposta.get_json()["erro"]


def test_login_bloqueia_depois_de_cinco_falhas(monkeypatch):
    auth._falhas_login.clear()
    monkeypatch.setattr(auth, "get_conn", lambda: (_ for _ in ()).throw(RuntimeError("sem banco")))
    monkeypatch.setattr(auth, "USERS", {})
    cliente = app.app.test_client()
    dados = {"usuario": "alvo", "senha": "errada"}
    for _ in range(5):
        assert cliente.post("/login", data=dados, headers={"Origin": "http://localhost"}).status_code == 200
    resposta = cliente.post("/login", data=dados, headers={"Origin": "http://localhost"})
    assert resposta.status_code == 429
    auth._falhas_login.clear()


def test_worker_nao_tem_autorizacao_fail_open():
    texto = (pathlib.Path(__file__).parent.parent / "bussola" / "app.py").read_text(encoding="utf-8")
    assert "if not SYNC_SECRET:" in texto
    assert "return not SYNC_SECRET or" not in texto
