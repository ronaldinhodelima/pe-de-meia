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

    assert 'src="/static/combobox.js"' in base
    assert 'data-pdm-combobox data-campo="categoria"' in detalhada
    assert 'data-pdm-combobox data-dimensao=' in detalhada
    assert 'data-pdm-combobox aria-label="Categoria" class="cat-select' in resumida
    assert 'data-pdm-combobox aria-label="{{ d.nome }}" class="dim-select' in resumida
