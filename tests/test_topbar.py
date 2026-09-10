"""Regressoes do menu principal por permissao."""

from flask import session

from app import app
from core import topbar_html


def _menu_com(permissoes):
    with app.test_request_context("/"):
        session["user"] = "teste"
        session["perfil"] = "personalizado"
        session["permissoes"] = permissoes
        return topbar_html("Teste")


def test_logs_fica_em_relatorios_e_nao_em_configuracoes():
    html = _menu_com(["relatorios", "usuarios"])
    relatorios = html.split("Relatórios ▾", 1)[1].split("Configurações ▾", 1)[0]
    configuracoes = html.split("Configurações ▾", 1)[1]

    assert 'href="/logs"' in relatorios
    assert 'href="/logs"' not in configuracoes


def test_administrador_sem_permissao_de_relatorios_ainda_enxerga_logs():
    html = _menu_com(["usuarios"])

    assert "Relatórios ▾" in html
    assert 'href="/logs"' in html
    assert 'href="/relatorios"' not in html


def test_lancamentos_do_menu_e_a_detalhada():
    """Decisao do usuario (09/09/2026): a Detalhada e a tela principal.

    A URL mora num ponto unico (`core.URL_LANCAMENTOS`) porque ela aparece em
    tres lugares - o item do menu, a marca do topbar e o destino do login.
    Escrita nos tres, divergiria no dia em que um fosse esquecido; e quando a
    Resumida sair, muda so ali.
    """
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    core_py = (raiz / "core.py").read_text(encoding="utf-8")
    auth = (raiz / "views" / "auth.py").read_text(encoding="utf-8")
    assert 'URL_LANCAMENTOS = "/lancamentos/fatura"' in core_py
    # nenhum dos tres escreve a URL na mao
    topbar = core_py.split("def topbar_html", 1)[1].split("\ndef ", 1)[0]
    assert "{URL_LANCAMENTOS}" in topbar
    assert '<a href="/" class="marca-box"' not in topbar
    assert '<a href="/" class="{cls("inicio")}"' not in topbar
    assert 'redirect("/")' not in auth, "o login cai na tela principal"
    assert auth.count("redirect(URL_LANCAMENTOS)") == 2
