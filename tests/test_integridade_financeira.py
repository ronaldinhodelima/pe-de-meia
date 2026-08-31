"""Regressoes de integridade para classificacoes e historico patrimonial."""
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal

from core import data_hora_local
from views.relatorios import _montar_historico_investimentos
from views.lancamentos import _valor_manual, _normalizar_rateios
from views.cadastros import _filtro_valor, _condicao_valor_sql, _categorias_para_regras


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
    assert "ABS(COALESCE(t.valor_brl,t.valor_original))" in trecho
    assert "r.account_id IS NULL OR r.account_id=t.account_id" in trecho


def test_estorno_so_herda_classificacao_quando_o_par_e_unico_e_seguro():
    texto = (Path(__file__).parent.parent / "core.py").read_text(encoding="utf-8")
    trecho = texto.split("def aplicar_estornos_classificacao", 1)[1].split(
        "def registrar_e_calcular_crescimento", 1
    )[0]
    assert "COUNT(DISTINCT o.transacao_id)=1" in trecho
    assert "COALESCE(o.valor_brl,o.valor_original)=-COALESCE(e.valor_brl,e.valor_original)" in trecho
    assert "e.numero_cartao_final=o.numero_cartao_final" in trecho
    assert "e.conferida" not in trecho
    assert "observacao=" not in trecho


def test_regra_por_valor_aceita_formato_brasileiro_e_valor_negativo_e_comparado_em_modulo():
    operador, limite, erro = _filtro_valor({"valor_operador": "lt", "valor_limite": "1.234,56"})
    assert (operador, limite, erro) == ("lt", Decimal("1234.56"), None)
    assert _condicao_valor_sql(operador) == " AND ABS(COALESCE(t.valor_brl,t.valor_original)) < %s"


def test_regra_sem_operador_e_limite_significa_qualquer_valor():
    assert _filtro_valor({}) == (None, None, None)


def test_regra_permite_investimento_mas_nao_transferencia_neutra():
    categorias = _categorias_para_regras()
    assert "Pension" in categorias
    assert "Credit card payment" not in categorias

    texto = (Path(__file__).parent.parent / "views" / "cadastros.py").read_text(encoding="utf-8")
    trecho_regras = texto.split('@bp.route("/regras"', 1)[1].split('@bp.route("/api/regras/preview"', 1)[0]
    assert "todas_categorias = _categorias_para_regras()" in trecho_regras


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


def test_rateio_fecha_exatamente_e_preserva_sinal_do_lancamento():
    partes = _normalizar_rateios(-705.28, [
        {"valor": "505,46", "categoria": "Insurance", "dimensoes": {"1": "1"}},
        {"valor": "199.82", "categoria": "Insurance", "dimensoes": {"1": "2"}},
    ])
    assert [p["valor_brl"] for p in partes] == [Decimal("-505.46"), Decimal("-199.82")]
    assert sum(p["valor_brl"] for p in partes) == Decimal("-705.28")


def test_rateio_recusa_total_diferente_do_banco():
    try:
        _normalizar_rateios(-705.28, [
            {"valor": "500", "categoria": "Insurance"},
            {"valor": "200", "categoria": "Insurance"},
        ])
    except ValueError as exc:
        assert "Diferença" in str(exc)
    else:
        raise AssertionError("rateio que nao fecha foi aceito")


def test_relatorios_usam_partes_no_lugar_do_lancamento_pai():
    raiz = Path(__file__).parent.parent
    core = (raiz / "core.py").read_text(encoding="utf-8")
    relatorios = (raiz / "views" / "relatorios.py").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE VIEW cartao.lancamento_financeiro AS" in core
    assert 'base = f"FROM {FINANCEIRO_TABELA} t' in relatorios
    assert "FROM {cfg['tabela']} t" in relatorios
