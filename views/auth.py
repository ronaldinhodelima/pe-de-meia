"""Login e logout."""
import threading
import time

import psycopg2
import psycopg2.extras
from flask import Blueprint, request, redirect, session, render_template

from core import (
    USERS,
    fechar_recursos_banco,
    get_conn,
    permissoes_do_perfil,
    senha_confere,
)

bp = Blueprint("auth", __name__)

_JANELA_LOGIN = 15 * 60
_MAX_FALHAS_LOGIN = 5
_MAX_FALHAS_IP = 20
_falhas_login = {}
_falhas_lock = threading.Lock()


def _chave_tentativa(usuario):
    # ProxyFix, configurado no app, ja extrai o IP do ultimo proxy confiavel.
    # Ler o primeiro X-Forwarded-For diretamente permitia forjar um IP novo a
    # cada tentativa quando o proxy preservava o cabecalho recebido do cliente.
    ip = request.remote_addr or "desconhecido"
    return ip, usuario.lower()


def _tentativas_recentes(chave):
    agora = time.monotonic()
    with _falhas_lock:
        recentes = [t for t in _falhas_login.get(chave, []) if agora - t < _JANELA_LOGIN]
        _falhas_login[chave] = recentes
        return recentes


def _registrar_falha(chave):
    with _falhas_lock:
        _falhas_login.setdefault(chave, []).append(time.monotonic())


def _limpar_falhas(chave):
    with _falhas_lock:
        _falhas_login.pop(chave, None)


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = (request.form.get("usuario", "") or "").strip()
        p = request.form.get("senha", "")
        chave_tentativa = _chave_tentativa(u)
        chave_ip = (chave_tentativa[0], "*")
        if (len(_tentativas_recentes(chave_tentativa)) >= _MAX_FALHAS_LOGIN or
                len(_tentativas_recentes(chave_ip)) >= _MAX_FALHAS_IP):
            return render_template(
                "login.html", titulo="Entrar",
                erro="Muitas tentativas. Aguarde 15 minutos e tente novamente.",
            ), 429
        conta = None
        conn = cur = None
        transacao_encerrada = False
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT usuario, nome, senha_hash, perfil, permissoes, ativo "
                "FROM cartao.usuario WHERE lower(usuario) = lower(%s);",
                (u,),
            )
            conta = cur.fetchone()
            if conta and conta["ativo"] and senha_confere(p, conta["senha_hash"]):
                cur.execute("UPDATE cartao.usuario SET ultimo_acesso = now() WHERE usuario = %s;",
                            (conta["usuario"],))
                conn.commit()
                transacao_encerrada = True
                session.clear()
                session.permanent = True
                session["user"] = conta["usuario"]
                session["nome"] = conta["nome"] or conta["usuario"]
                session["perfil"] = conta["perfil"]
                session["permissoes"] = list(conta["permissoes"] or [])
                _limpar_falhas(chave_tentativa)
                return redirect("/")
        except Exception as e:
            print("Aviso: falha ao autenticar pelo banco:", e)
        finally:
            fechar_recursos_banco(conn, cur, rollback=not transacao_encerrada)

        # rede de seguranca: se a tabela ainda nao existe (primeiro boot), aceita a env
        if conta is None and u in USERS and USERS[u] == p:
            session.clear()
            session.permanent = True
            session["user"] = u
            session["nome"] = u
            session["perfil"] = "admin"
            session["permissoes"] = permissoes_do_perfil("admin")
            _limpar_falhas(chave_tentativa)
            return redirect("/")

        _registrar_falha(chave_tentativa)
        _registrar_falha(chave_ip)
        # A mesma mensagem para usuario inexistente, senha errada ou conta inativa
        # evita confirmar a um atacante quais logins existem.
        error = "Usuário ou senha inválidos."
    return render_template("login.html", titulo="Entrar", erro=error)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect("/login")
