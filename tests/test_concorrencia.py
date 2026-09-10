"""Proteções contra gravações parciais e disputas entre requisições."""
from pathlib import Path


RAIZ = Path(__file__).parent.parent


def test_tela_envia_apenas_o_campo_que_foi_alterado():
    """Cada gravacao leva SO o campo que mudou, na fila da propria linha.

    Mandar a linha inteira faria duas edicoes seguidas disputarem: a segunda
    sobrescreveria com o valor antigo o que a primeira acabou de gravar. Ate
    10/09/2026 isso era cobrado da Resumida; a regra continua, na tela que ficou.
    """
    js = (RAIZ / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    montagem = js.split("function payloadEditor(editor, alterado)", 1)[1].split("\n  }", 1)[0]
    assert "payload[alterado.dataset.campo] = alterado.value" in montagem
    assert "payload.dimensoes = {[alterado.dataset.dimensao]" in montagem
    # a fila por lancamento: uma gravacao so comeca quando a anterior terminou
    gravacao = js.split("function salvarEditor(editor, alterado)", 1)[1].split("\n  }\n", 1)[0]
    assert "anterior.catch(() => {}).then" in gravacao


def test_desmarcar_o_ok_exige_confirmacao_sem_repetir_detalhes():
    """Retirar uma assinatura exige confirmacao, um a um (secao 1.2).

    A marcacao como duplicada saiu da interface em 02/09/2026 (secao 4.3), e a
    Resumida - onde isso era cobrado - saiu em 10/09/2026. A regra continua na
    tela que ficou.
    """
    js = (RAIZ / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    assert "confirmar_desmarcacao" in js
    assert "confirm(" in js
    assert "abrirConfirmacaoModal('duplicar')" not in js
    assert "confirmar_duplicada" not in js


def test_servidor_bloqueia_lancamento_durante_edicao_e_exclusao():
    texto = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")

    assert texto.count("WHERE transacao_id = %s FOR UPDATE") >= 2


def test_rateio_conferido_pode_ser_editado_sem_perder_ok_mas_nao_desfeito():
    texto = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    trecho = texto.split("def rateios_transacao", 1)[1].split("def update_transacao", 1)[0]

    assert 'if transacao[1] and request.method == "DELETE":' in trecho
    assert 'SELECT id FROM cartao.dimensao WHERE obrigatoria=true' in trecho

    # O pai PODE receber OK a partir das partes (decisao do usuario,
    # 07/09/2026): num rateado a classificacao mora nelas, e quem conferiu as
    # partes conferiu o lancamento. Mas so em uma direcao e sob tres condicoes.
    assert 'data.get("conferir")' in trecho, "exige acao humana explicita"
    assert 'pode("lancamentos_conferir")' in trecho, "exige permissao"
    assert "conferida=true" in trecho and "AND conferida=false" in trecho, (
        "nunca sobrescreve assinatura que ja existe"
    )
    assert "conferida=false," not in trecho, "a rota NUNCA desmarca (secao 1.2)"
    assert "SET conferida=false" not in trecho


def test_auditoria_de_lancamento_guarda_valores_anteriores_e_novos():
    app = (RAIZ / "app.py").read_text(encoding="utf-8")
    lancamentos = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    core = (RAIZ / "core.py").read_text(encoding="utf-8")

    assert 'getattr(g, "audit_alteracoes", {})' in app
    assert 'alteracoes[str(campo)[:100]] = {"antes": antes, "depois": depois}' in core
    for campo in ("Conferida", "Observação", "Natureza"):
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
