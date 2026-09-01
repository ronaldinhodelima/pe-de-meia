from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]


def test_componente_pesquisavel_preserva_select_e_fluxo_de_teclado():
    js = (RAIZ / "static" / "combobox.js").read_text(encoding="utf-8")

    assert "select[data-pdm-combobox]" in js
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
    assert ".pdm-combobox-opcao.ativo::after{content:'Enter ↵'" in css
    assert "--field-soft: var(--bg)" in css
    assert "background:transparent!important" in css
    assert "font-size:12.5px" in css
    assert "height:28px" in css
    assert "border-radius:5px!important" in css
    assert "box-shadow:0 1px 3px rgba(20,20,20,.08)!important" in css
    assert ".pdm-combobox-input:hover,.pdm-combobox-input:focus" in css
    assert "transform:scale(1.012)" in css
    assert "table.compacta .pdm-combobox-input { font-size: 11.5px; }" in css
