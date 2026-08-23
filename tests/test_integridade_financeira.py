"""Regressoes de integridade para classificacoes e historico patrimonial."""
from pathlib import Path

from views.relatorios import _montar_historico_investimentos


def test_sync_nao_sobrescreve_categoria_revisada_pelo_usuario():
    texto = (Path(__file__).parent.parent / "bussola" / "app.py").read_text(encoding="utf-8")
    trecho_update = texto.split("ON CONFLICT (transacao_id) DO UPDATE SET", 1)[1].split("RETURNING", 1)[0]
    assert "categoria = EXCLUDED.categoria" not in trecho_update


def test_variacao_do_investimento_fica_no_mes_mais_novo():
    historico = [
        {"mes": "2026-06", "saldo": 100, "aplicado": 90},
        {"mes": "2026-07", "saldo": 115, "aplicado": 100},
        {"mes": "2026-08", "saldo": 112, "aplicado": 100},
    ]
    linhas = _montar_historico_investimentos(historico)
    assert [l["rotulo"] for l in linhas] == ["ago/26", "jul/26", "jun/26"]
    assert linhas[0]["variacao"] == -3
    assert linhas[1]["variacao"] == 15
    assert linhas[2]["variacao"] is None
