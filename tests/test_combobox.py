from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]


def test_componente_pesquisavel_preserva_select_e_fluxo_de_teclado():
    js = (RAIZ / "static" / "combobox.js").read_text(encoding="utf-8")

    assert "select.hasAttribute('data-pdm-combobox')" in js
    assert "normalize('NFD')" in js
    assert ".includes(termo)" in js
    for tecla in ("ArrowDown", "ArrowUp", "Enter", "Tab", "Escape"):
        assert f"evento.key === '{tecla}'" in js
    assert "select.dispatchEvent(new Event('change', {bubbles: true}))" in js


def test_piloto_cobre_quatro_campos_nas_duas_visualizacoes():
    detalhada = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    resumida = (RAIZ / "templates" / "index.html").read_text(encoding="utf-8")
    base = (RAIZ / "templates" / "base.html").read_text(encoding="utf-8")

    assert '/static/combobox.js?v=' in base
    assert 'data-pdm-combobox data-campo="categoria"' in detalhada
    assert 'data-pdm-combobox data-dimensao=' in detalhada
    assert 'data-pdm-combobox aria-label="Categoria" class="cat-select' in resumida
    assert 'data-pdm-combobox aria-label="{{ d.nome }}" class="dim-select' in resumida


def test_layout_sombra_flutuante_e_compacto():
    css = (RAIZ / "static" / "app.css").read_text(encoding="utf-8")

    assert ".pdm-combobox-input:focus" in css
    assert "content:'Enter ↵'" not in css
    assert "--field-soft: var(--bg)" in css
    assert "background:transparent!important" in css
    # o combobox segue a escala de texto, nao um tamanho proprio
    assert "font-size:var(--fonte-sm)" in css
    assert "height:26px" in css
    # combobox e campo do modal compartilham o raio: eram 5px e 6px lado a lado
    assert "border-radius:var(--radius-xs)!important" in css
    assert "padding:1px 18px 1px 3px!important" in css
    assert "padding:7px 4px" in css
    # a sombra do campo virou token: sem isso os campos do modal nao tinham
    # como igualar o combobox sem copiar o numero na mao
    assert "box-shadow:var(--campo-sombra)!important" in css
    assert ".pdm-combobox-input:hover,.pdm-combobox-input:focus" in css
    assert "background:var(--raise)!important" in css
    # o contorno de hover tambem e token: em cor fixa ele nao acompanha o
    # fundo do modo escuro (secao 7.8-A)
    assert "border-color:var(--campo-linha-hover)!important" in css
    assert "transform:scale(1.012)" in css
    assert "table.compacta .pdm-combobox-input { font-size: var(--fonte-xs); }" in css


def test_componente_se_expande_para_listas_pesquisaveis_do_projeto():
    js = (RAIZ / "static" / "combobox.js").read_text(encoding="utf-8")

    assert "select.hasAttribute('data-lazy-options')" in js
    assert "select.matches('[data-pdm-native], [multiple]')" in js
    assert "escopo.querySelectorAll('select')" in js
    assert "new MutationObserver(function (mudancas)" in js
    assert "select.options.length >= 7" not in js


def test_filtros_de_navegacao_da_fatura_permanecem_nativos():
    tela = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")

    assert '<select id="faturaConta" data-pdm-native>' in tela
    assert '<select id="faturaSelecionada" data-pdm-native>' in tela
    assert '<select id="faturaStatus" data-pdm-native>' in tela


def test_filtro_status_resumido_nao_repete_rotulo_visual():
    tela = (RAIZ / "templates" / "index.html").read_text(encoding="utf-8")

    assert '<label>Status</label>' not in tela
    assert '<select id="statusInput" data-pdm-native aria-label="Status"' in tela


def test_pendencias_nao_registra_evento_em_formulario_ausente():
    tela = (RAIZ / "templates" / "pendencias.html").read_text(encoding="utf-8")

    assert "const formLote = document.getElementById('formLote');" in tela
    assert "if (formLote)" in tela


def test_fatura_em_andamento_bloqueia_ok_e_preserva_edicao():
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    tela = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    js = (RAIZ / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")

    assert "def _render_fatura_em_andamento" in view
    assert 'sem_pdf_conciliado = not bool(cur.fetchone()[0])' in view
    assert "faltando or rateio_invalido or pendente_banco or sem_pdf_conciliado" in view
    assert "OK será liberado somente depois da importação e conciliação da fatura" in tela
    assert "not pode_conferir or fatura.em_andamento" in tela
    assert "fatura.value === 'andamento'" in js
    assert "andamento=1&amp;account_id={{ account_id }}" in tela
    assert "'andamento=1&amp;account_id=' ~ account_id" not in tela


def test_pendente_banco_nao_colore_fundo_da_linha():
    css = (RAIZ / "static" / "app.css").read_text(encoding="utf-8")

    assert "tr.pendente-banco,tr.pendente-banco:hover { background: transparent; }" in css
    assert "tr.pendente-banco { background: #fdf6e8; }" not in css


def test_tab_nao_apaga_valor_que_o_usuario_nao_escolheu():
    """Regra obrigatoria: tabular por um campo nao pode edita-lo.

    O caso real (07/09/2026): escolher o Projeto dispara a gravacao e, na
    RESPOSTA dela, a regra preenche o Portfolio padrao. O usuario tabula para o
    Portfolio antes disso: a lista abre com o valor ainda vazio e destaca
    "(nao definido)". Quando a resposta chega, o campo passa a mostrar "Imoveis"
    - mas o destaque da lista aberta continuava em "(nao definido)". O Tab
    seguinte confirma a DESTACADA (secao 7.7) e apaga o que a regra acabou de
    preencher. A tela mostrava um valor e gravava outro.

    Duas travas, e as duas sao necessarias:

    * `sincronizar` re-alinha o destaque quando o valor do <select> muda POR
      FORA com a lista aberta - senao a lista mente sobre o que sera confirmado;
    * o Tab so confirma se o usuario tiver mesmo navegado (seta) ou filtrado.
      Sem isso, passar pelo campo grava - e "tabular" viraria "editar".
    """
    js = (RAIZ / "static" / "combobox.js").read_text(encoding="utf-8")

    assert "function realinharDestaque()" in js, (
        "o re-alinhamento do destaque sumiu: a lista volta a mentir sobre o "
        "que o Tab vai confirmar"
    )
    corpo_sync = js.split("function sincronizar() {", 1)[1].split("\n    }", 1)[0]
    assert "realinharDestaque()" in corpo_sync, (
        "sincronizar parou de re-alinhar o destaque - o valor pode mudar por "
        "fora enquanto a lista esta aberta"
    )

    tab = js.split("evento.key === 'Tab'", 1)[1].split("else if", 1)[0]
    assert "navegouNaLista" in tab, (
        "Tab voltou a confirmar sem o usuario ter escolhido nada: tabular por "
        "um campo passaria a grava-lo"
    )

    # A bandeira precisa nascer limpa a cada abertura e subir quando o usuario
    # realmente mexe - se ficar presa em true, a primeira trava se desliga.
    foco = js.split("input.addEventListener('focus'", 1)[1].split("});", 1)[0]
    assert "navegouNaLista = false" in foco, "a bandeira nao e limpa ao abrir"
    assert js.count("navegouNaLista = true") >= 2, (
        "seta e filtro precisam marcar que houve navegacao"
    )
