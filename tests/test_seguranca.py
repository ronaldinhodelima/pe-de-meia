"""Regressoes dos controles que protegem autenticacao e dados financeiros."""
import pathlib
from datetime import timedelta

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


def test_sessao_expira_em_24_horas_e_cookie_e_protegido():
    assert app.app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(hours=24)
    assert app.app.config["SESSION_REFRESH_EACH_REQUEST"] is True
    assert app.app.config["SESSION_COOKIE_SECURE"] is True
    assert app.app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    texto = (pathlib.Path(__file__).parent.parent / "core.py").read_text(encoding="utf-8")
    trecho = texto.split("def validar_sessao_atual():", 1)[1].split("def requer", 1)[0]
    assert "session.permanent = True" in trecho


def test_respostas_recebem_cabecalhos_de_seguranca():
    resposta = app.app.test_client().get("/health")
    assert resposta.headers["Strict-Transport-Security"].startswith("max-age=")
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert resposta.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in resposta.headers["Content-Security-Policy"]


def test_paginas_financeiras_nao_podem_ser_guardadas_em_cache():
    resposta = app.app.test_client().get("/login")
    assert resposta.headers["Cache-Control"] == "no-store, private"


def test_editar_nao_da_permissao_de_conferir(monkeypatch):
    class Cursor:
        def execute(self, *args, **kwargs):
            pass

        def fetchone(self):
            return (False,)

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(core, "validar_sessao_atual", lambda: True)
    monkeypatch.setattr(lancamentos, "get_conn", lambda: Conn())
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


def test_editor_sem_permissao_de_conferir_ainda_edita_categoria(monkeypatch):
    class Cursor:
        def execute(self, *args, **kwargs):
            pass

        def fetchone(self):
            return (False,)

        def fetchall(self):
            return []

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(core, "validar_sessao_atual", lambda: True)
    monkeypatch.setattr(lancamentos, "get_conn", lambda: Conn())
    cliente = app.app.test_client()
    with cliente.session_transaction() as sessao:
        sessao["user"] = "operador"
        sessao["permissoes"] = ["lancamentos_editar"]

    resposta = cliente.post(
        "/api/transacao/existente",
        json={"categoria": "Groceries", "conferida": False},
        headers={"Origin": "http://localhost"},
    )
    assert resposta.status_code == 200
    assert resposta.get_json()["ok"] is True


def test_valor_de_outra_dimensao_e_rejeitado(monkeypatch):
    class Cursor:
        respostas = iter([(False,), None])

        def execute(self, *args, **kwargs):
            pass

        def fetchone(self):
            return next(self.respostas)

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(core, "validar_sessao_atual", lambda: True)
    monkeypatch.setattr(lancamentos, "get_conn", lambda: Conn())
    cliente = app.app.test_client()
    with cliente.session_transaction() as sessao:
        sessao["user"] = "operador"
        sessao["permissoes"] = ["lancamentos_editar", "lancamentos_conferir"]

    resposta = cliente.post(
        "/api/transacao/existente", json={"dimensoes": {"1": "999"}},
        headers={"Origin": "http://localhost"},
    )
    assert resposta.status_code == 400
    assert "não pertence" in resposta.get_json()["erro"]


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
