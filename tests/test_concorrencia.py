"""Proteções contra gravações parciais e disputas entre requisições."""
from pathlib import Path


RAIZ = Path(__file__).parent.parent


def test_tela_envia_apenas_o_campo_que_foi_alterado():
    js = (RAIZ / "static" / "lancamentos.js").read_text(encoding="utf-8")
    trecho = js.split("function salvar(id, el, opcoes)", 1)[1].split("function toggleFormManual", 1)[0]

    assert "if (el.matches('.cat-select'))" in trecho
    assert "else if (el.matches('.dim-select'))" in trecho
    assert "else if (el.matches('.conf-check'))" in trecho
    assert "dimensoes[sel.dataset.dim]" not in trecho
    assert "anterior.catch(() => {}).then" in trecho


def test_ok_e_duplicidade_exigem_confirmacao_sem_repetir_detalhes():
    js = (RAIZ / "static" / "lancamentos.js").read_text(encoding="utf-8")

    assert "abrirConfirmacaoModal('desconferir')" in js
    assert "abrirConfirmacaoModal('duplicar')" in js
    assert "modalConfirmacaoResumo" not in js
    assert "el.checked = true" in js, "o clique de desmarcar deve ser desfeito ate confirmar"
    assert "if ('conferida' in d)" in js, "toda edicao deve sincronizar o OK retornado pelo banco"
    assert "payload.confirmar_desmarcacao = true" in js
    assert "payload.confirmar_duplicada = true" in js
    assert "if (fecharJanela) fecharModal();" in js


def test_servidor_bloqueia_lancamento_durante_edicao_e_exclusao():
    texto = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")

    assert texto.count("WHERE transacao_id = %s FOR UPDATE") >= 2


def test_auditoria_de_lancamento_guarda_valores_anteriores_e_novos():
    app = (RAIZ / "app.py").read_text(encoding="utf-8")
    lancamentos = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    core = (RAIZ / "core.py").read_text(encoding="utf-8")

    assert 'getattr(g, "audit_alteracoes", {})' in app
    assert 'alteracoes[str(campo)[:100]] = {"antes": antes, "depois": depois}' in core
    for campo in ("Conferida", "Duplicada", "Observação", "Natureza"):
        assert f'registrar_mudanca_auditoria("{campo}"' in lancamentos
    assert '"Categoria",' in lancamentos


def test_auditoria_cobre_cadastros_e_usuarios_sem_expor_senha():
    cadastros = (RAIZ / "views" / "cadastros.py").read_text(encoding="utf-8")
    usuarios = (RAIZ / "views" / "usuarios.py").read_text(encoding="utf-8")

    for campo in (
        "Dimensão", "Regra automática", "Centro de custo", "Titular da conexão",
        "Categoria",
    ):
        assert campo in cadastros
    for campo in ("Usuário", "Permissões", "Acesso ativo", "Senha"):
        assert campo in usuarios
    assert 'registrar_mudanca_auditoria("Senha", "mantida em segredo", "alterada")' in usuarios
    assert "senha_hash" not in usuarios.split('registrar_mudanca_auditoria("Senha"', 1)[1]


def test_excluir_regra_libera_lancamentos_e_fk_impede_orfaos():
    cadastros = (RAIZ / "views" / "cadastros.py").read_text(encoding="utf-8")
    core = (RAIZ / "core.py").read_text(encoding="utf-8")

    trecho = cadastros.split('elif acao == "excluir_regra"', 1)[1].split("elif acao", 1)[0]
    assert "UPDATE cartao.transacao SET regra_aplicada_id = NULL" in trecho
    assert "ON DELETE SET NULL" in core


def test_operacoes_de_usuario_serializam_contagem_de_administradores():
    texto = (RAIZ / "views" / "usuarios.py").read_text(encoding="utf-8")

    assert "LOCK TABLE cartao.usuario IN SHARE ROW EXCLUSIVE MODE" in texto
