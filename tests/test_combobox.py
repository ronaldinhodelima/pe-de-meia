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
    assert "font-size:12.5px" in css
    assert "height:26px" in css
    assert "border-radius:5px!important" in css
    assert "padding:1px 18px 1px 3px!important" in css
    assert "padding:7px 4px" in css
    assert "box-shadow:0 1px 3px rgba(20,20,20,.08)!important" in css
    assert ".pdm-combobox-input:hover,.pdm-combobox-input:focus" in css
    assert "background:#f2f2f0!important" in css
    assert "border-color:rgba(92,95,102,.24)!important" in css
    assert "transform:scale(1.012)" in css
    assert "table.compacta .pdm-combobox-input { font-size: 11.5px; }" in css


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
