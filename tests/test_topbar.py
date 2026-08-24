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
