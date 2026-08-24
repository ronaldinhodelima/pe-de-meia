"""Regressoes de integridade para classificacoes e historico patrimonial."""
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal

from core import data_hora_local
from views.relatorios import _montar_historico_investimentos
from views.lancamentos import _valor_manual


def test_sync_nao_sobrescreve_nenhum_ajuste_manual_do_usuario():
    texto = (Path(__file__).parent.parent / "bussola" / "app.py").read_text(encoding="utf-8")
    trecho_update = texto.split("ON CONFLICT (transacao_id) DO UPDATE SET", 1)[1].split("RETURNING", 1)[0]
    campos_manuais = (
        "categoria",
        "categoria_id",
        "observacao",
        "conferida",
        "conferida_por",
        "conferida_em",
        "duplicada",
        "natureza",
        "regra_aplicada_id",
    )
    for campo in campos_manuais:
        assert f"{campo} =" not in trecho_update

    # Projeto, Portfolio e Responsavel sao dimensoes. O worker do Pluggy nao
    # escreve na tabela que guarda essas escolhas manuais.
    assert "transacao_dimensao" not in texto


def test_sync_aceita_correcao_de_horario_do_pluggy_no_mesmo_id():
    texto = (Path(__file__).parent.parent / "bussola" / "app.py").read_text(encoding="utf-8")
    trecho_update = texto.split("ON CONFLICT (transacao_id) DO UPDATE SET", 1)[1].split("RETURNING", 1)[0]
    assert "data_transacao = EXCLUDED.data_transacao" in trecho_update


def test_regra_automatica_nao_sobrescreve_categoria_manual():
    texto = (Path(__file__).parent.parent / "core.py").read_text(encoding="utf-8")
    trecho = texto.split("def aplicar_regras", 1)[1].split("DUPLICADA_OBS_PADRAO", 1)[0]

    assert "COALESCE(t.categoria_manual, false) = false" in trecho


def test_edicao_humana_marca_categoria_como_manual():
    raiz = Path(__file__).parent.parent
    lancamentos = (raiz / "views" / "lancamentos.py").read_text(encoding="utf-8")
    cadastros = (raiz / "views" / "cadastros.py").read_text(encoding="utf-8")

    assert '"categoria_manual = true"' in lancamentos
    assert '"regra_aplicada_id = NULL"' in lancamentos
    assert "categoria_manual = true" in cadastros


def test_horario_03_utc_da_conta_corrente_aparece_como_meia_noite_local():
    valor = datetime(2026, 8, 19, 3, 0, tzinfo=timezone.utc)
    local = data_hora_local(valor)
    assert local.strftime("%Y-%m-%d %H:%M %z") == "2026-08-19 00:00 -0300"


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


def test_valor_manual_respeita_sinal_de_conta_bancaria():
    assert _valor_manual("100,25", "entrada") == Decimal("100.25")
    assert _valor_manual("100,25", "saida") == Decimal("-100.25")


def test_valor_manual_arredonda_centavos_sem_float():
    assert _valor_manual("10.005", "entrada") == Decimal("10.01")
