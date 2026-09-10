"""Renderiza os templates com o formato REAL dos dados que a view entrega.

Existe por causa de um bug que passou despercebido: a tela /importar desempacotava
3 valores de uma tupla que carregar_origens() devolve com 4 desde o commit fcedcf1,
e ficou dando 500 sem ninguem ver (ela nao estava mais no menu). A tela foi removida
depois, mas a licao fica: testar com dado inventado nao pega esse tipo de erro - o
formato usado aqui tem que espelhar o que a view realmente entrega.
"""
from pathlib import Path

from views.lancamentos import opcoes_de_status
from datetime import date

import re
import pytest

import app  # noqa: F401  (cria o Flask app e registra os blueprints)
import core
from flask import render_template


@pytest.fixture
def ctx():
    with app.app.test_request_context("/"):
        yield


class TestInvestimentos:
    def test_estado_nao_sincronizado(self, ctx):
        html = render_template(
            "investimentos.html", titulo="Investimentos", topbar="", sincronizado=False
        )
        assert "Ainda não sincronizado" in html

    def test_nome_da_aplicacao_e_escapado(self, ctx):
        # o nome vem do Pluggy - conteudo de terceiro
        ativos = [{
            "nome": "<img src=x onerror=alert(1)>", "detalhe": "CDB", "aplicado": 100.0,
            "bruto": 110.0, "rend": 10.0, "pct": 10.0, "impostos": 1.5,
            "saldo": 108.5, "vencimento": "-",
        }]
        html = render_template(
            "investimentos.html", titulo="Investimentos", topbar="", sincronizado=True,
            ativos=ativos, encerrados=0, saldo_total=108.5, aplicado_total=100.0,
            rendimento_bruto=10.0, rend_pct=10.0, ir_total=1.5, historico=[],
        )
        assert "<img src=x" not in html
        assert "&lt;img" in html


class TestDRE:
    BASE = dict(
        titulo="DRE", topbar="", ano="2026", anos=[2026], rec_ano=0,
        desp_ano=0, resultado_ano=0, inv_ano=0, linhas_dre=[],
        blocos_dimensao=[], grupos=[], nao_classificadas=[],
    )

    def test_aviso_de_pendencias_e_montado_sem_html_dinamico(self, ctx):
        pendencias = {
            "sem_categoria": 2,
            "sem_natureza": ["A"],
            "despesa_sem_centro": ["B", "C"],
            "total": 5,
        }
        html = render_template("dre.html", **{**self.BASE, "pendencias": pendencias})
        assert "2</strong> lançamentos sem categoria" in html
        assert "1</strong> categoria sem natureza" in html
        assert "2</strong> categorias de despesa sem centro" in html
        assert "|safe" not in html

    def test_sem_pendencias_nao_mostra_alerta(self, ctx):
        html = render_template("dre.html", **{**self.BASE, "pendencias": None})
        assert "Revisar agora" not in html

    def test_filtro_de_ano_envia_formulario_sem_montar_url_no_javascript(self, ctx):
        html = render_template("dre.html", **{**self.BASE, "pendencias": None})
        assert '<form action="/dre" method="get">' in html
        assert 'name="ano"' in html
        assert "window.location" not in html


class TestConciliacaoFatura:
    def test_cabecalho_usa_nome_do_mes_sem_linha_do_ciclo(self, ctx):
        template = (
            Path(__file__).parent.parent / "templates" / "conciliar_fatura.html"
        ).read_text(encoding="utf-8")

        assert "Fatura {{ meses_nome[f.mes_referencia - 1] }} de {{ f.ano_referencia }}" in template
        assert "ciclo {{ resultado.periodo_inicio" not in template

        resultado = {
            "fatura": {"id": 8, "mes_referencia": 8, "ano_referencia": 2026, "total": 100},
            "fecha_100": True,
            "periodo_inicio": date(2026, 7, 10),
            "periodo_fim": date(2026, 8, 12),
            "soma_fatura": 100,
            "soma_vinculada": 100,
            "diferenca": 0,
            "despesas_dre": 80,
            "fora_dre": 20,
            "linhas": [],
            "sem_vinculo": [],
            "orfas": [],
            "repetidas_na_fatura": [],
        }
        html = render_template(
            "conciliar_fatura.html", titulo="Conciliar fatura", topbar="",
            resultado=resultado, historico=[], erro=None, contas_credito=[], categorias=[],
            account_id="conta", fatura_id=8,
            fatura_mais_antiga={"id": 7}, fatura_mais_nova={"id": 9},
            pode_editar_conciliacao=False, pode_criar_lancamento=False,
        )
        assert "Fatura Agosto de 2026" in html
        assert "ciclo 10/07/2026" not in html
        assert "Despesas no DRE" in html
        assert "Fora do DRE" in html
        assert "R$ 80,00" in html and "R$ 20,00" in html

    def test_setas_de_mes_guardam_a_posicao_da_pagina(self):
        template = (
            Path(__file__).parent.parent / "templates" / "conciliar_fatura.html"
        ).read_text(encoding="utf-8")

        assert template.count("data-nav-mes") == 3  # duas setas + seletor do listener
        assert "sessionStorage.setItem('conciliar_scroll'" in template
        assert "window.scrollTo(0, parseInt(y, 10) || 0)" in template


class TestLogs:
    def test_detalhes_e_campos_sao_escapados(self, ctx):
        evento = {
            "quando": "23/08/2026 20:00:00",
            "usuario_rotulo": "<script>usuario</script>",
            "acao_rotulo": "Alteração",
            "recurso": "usuarios.view",
            "recurso_id": None,
            "rota": "/usuarios",
            "metodo": "POST",
            "sucesso": True,
            "status_http": 200,
            "ip_origem": "127.0.0.1",
            "user_agent": "teste",
            "detalhes_json": '<img src=x onerror="alert(1)">',
        }
        html = render_template(
            "logs.html", titulo="Logs", topbar="", eventos=[evento],
            acoes=[], usuarios=[], filtros={"acao": "", "usuario": "", "resultado": "", "busca": "", "data_ini": "", "data_fim": ""},
            total=1, pagina=1, total_paginas=1, url_anterior=None, url_proxima=None,
        )
        assert "<script>usuario</script>" not in html
        assert "<img src=x" not in html
        assert "&lt;img" in html


class TestRegras:
    BASE = dict(
        titulo="Regras", topbar="", erro=None, categorias=[{"chave": "Fuel", "nome": "Combustível"}],
        dimensoes=[{"id": 1, "nome": "Responsável", "obrigatoria": True}],
        valores_por_dim={1: [{"id": 10, "dimensao_id": 1, "nome": "Ronaldo"}]},
        total_aplicadas=0, editar_id=None, regras=[],
    )

    def test_padrao_da_regra_e_escapado(self, ctx):
        regra = {
            "id": 1, "padrao": "<script>alert(1)</script>", "categoria": "Fuel",
            "categoria_nome": "Combustível", "dims_txt": "-", "dims_selecionadas": {},
        }
        html = render_template("regras.html", **{**self.BASE, "regras": [regra]})
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_modo_edicao_marca_so_a_regra_escolhida(self, ctx):
        regras = [
            {"id": 1, "padrao": "A", "categoria": "Fuel", "categoria_nome": "Combustível",
             "dims_txt": "-", "dims_selecionadas": {1: 10}},
            {"id": 2, "padrao": "B", "categoria": "Fuel", "categoria_nome": "Combustível",
             "dims_txt": "-", "dims_selecionadas": {}},
        ]
        html = render_template("regras.html", **{**self.BASE, "regras": regras, "editar_id": 1})
        assert html.count('value="editar_regra"') == 1
        assert '<option value="10" selected>' in html

    def test_sem_regras_mostra_aviso(self, ctx):
        html = render_template("regras.html", **self.BASE)
        assert "Nenhuma regra cadastrada ainda." in html



class TestRelatorios:
    """Ultima tela a sair da f-string. Todo o conteudo chega por AJAX, entao o
    template so monta os filtros e os containers vazios."""

    BASE = dict(
        titulo="Relatórios", topbar="", visao="despesa",
        visao_opcoes=[("despesa", "Despesas"), ("receita", "Receitas")],
        agrupar="categoria",
        agrupar_opcoes=[("categoria", "Categoria"), ("mes", "Período (mês)")],
        filtros_chip=[], data_ini="", data_fim="",
    )

    def test_marca_a_visao_e_o_agrupamento_atuais(self, ctx):
        html = render_template(
            "relatorios.html", **{**self.BASE, "visao": "receita", "agrupar": "mes"}
        )
        assert '<option value="receita" selected>' in html
        assert '<option value="mes" selected>' in html
        assert '<option value="despesa" >' in html or '<option value="despesa">' in html

    def test_containers_que_o_ajax_preenche_existem(self, ctx):
        html = render_template("relatorios.html", **self.BASE)
        for alvo in ("totalGeral", "qtdGeral", "gruposCont", "chartGrupos", "chipsSel"):
            assert f'id="{alvo}"' in html

    def test_carrega_chartjs_e_o_script_da_tela(self, ctx):
        html = render_template("relatorios.html", **self.BASE)
        assert "chart.umd.min.js" in html
        assert "/static/relatorios.js" in html

    def test_filtros_chip_entram_como_html(self, ctx):
        # chip_filter_html() ja devolve HTML pronto e escapado
        html = render_template(
            "relatorios.html",
            **{**self.BASE, "filtros_chip": ['<div class="chipfilter">Origem</div>']},
        )
        assert '<div class="chipfilter">Origem</div>' in html

    def test_aviso_contabil_do_dre_continua_na_tela(self, ctx):
        # a regra de ouro: investimento/bem/transferencia nao sao despesa
        html = render_template("relatorios.html", **self.BASE)
        assert "não são despesa" in html
        assert 'href="/categorias"' in html


class TestEdicaoEmLote:
    """A barra de lote nao pode virar um caminho paralelo de gravacao."""

    def nucleo(self):
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        return (raiz / "static" / "lote.js").read_text(encoding="utf-8")

    def test_nao_existe_endpoint_de_lote(self):
        """Cada selecionado passa pelo MESMO POST de uma linha.

        Um segundo caminho de escrita divergiria das validacoes - foi assim que
        nasceram os 57 falsos pendentes da secao 6.5 n.10.
        """
        nucleo = self.nucleo()
        assert "/api/transacao/' + encodeURIComponent(alvo.id)" in nucleo
        assert nucleo.count("fetch(") == 1, "um unico ponto de gravacao"

    def test_o_nucleo_e_compartilhado_pelas_duas_telas(self):
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        for tela in ("lancamentos_fatura.html",):
            html = (raiz / "templates" / tela).read_text(encoding="utf-8")
            assert "/static/lote.js" in html, tela
        for js in ("lancamentos_fatura.js",):
            texto = (raiz / "static" / js).read_text(encoding="utf-8")
            assert "window.pdmLote.aplicar" in texto, js

    def test_lote_nunca_desmarca_ok(self):
        """Retirar assinatura exige confirmacao um a um (secao 1.2)."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        for js in ("lote.js", "lancamentos_fatura.js"):
            texto = (raiz / "static" / js).read_text(encoding="utf-8")
            trecho = texto.split("Edicao em lote", 1)[-1] if js != "lote.js" else texto
            assert "conferida = false" not in trecho, js
            assert "confirmar_desmarcacao" not in trecho, js

    def test_sair_da_barra_e_uma_regra_so_para_as_duas_telas(self):
        """Esc e o "x" fecham em qualquer uma das telas, pelo mesmo codigo.

        Se cada tela escrevesse o proprio Escape, a segunda divergiria da
        primeira - e o motivo de o nucleo existir.
        """
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        nucleo = self.nucleo()
        assert "ligarFechar" in nucleo
        assert "e.key !== 'Escape'" in nucleo
        # caixa de marcacao nao bloqueia o Esc: e onde o foco esta quando a
        # barra acabou de abrir, e Esc nao significa nada dentro dela
        assert "input:not([type=checkbox])" in nucleo
        for tela in ("lancamentos_fatura.html",):
            html = (raiz / "templates" / tela).read_text(encoding="utf-8")
            assert 'class="barra-lote-fechar"' in html, tela
        for js in ("lancamentos_fatura.js",):
            texto = (raiz / "static" / js).read_text(encoding="utf-8")
            assert "window.pdmLote.ligarFechar" in texto, js
            trecho = texto.split("Edicao em lote", 1)[-1]
            assert "Escape" not in trecho, js + ": Escape do lote mora no nucleo"

    def test_salvar_limpa_a_selecao_mas_nao_quando_houve_recusa(self):
        """Com recusa, a selecao e a lista de quem precisa de nova tentativa."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        for js in ("lancamentos_fatura.js",):
            texto = (raiz / "static" / js).read_text(encoding="utf-8")
            trecho = texto.split("Edicao em lote", 1)[-1]
            assert "if (!r.falhas.length) {" in trecho, js
            assert "desmarcarTudo();" in trecho, js

    def test_lote_nao_sobrescreve_observacao_sem_intencao(self):
        """A observacao pertence ao usuario (secao 7.3)."""
        nucleo = self.nucleo()
        assert "opcoes.substituirObservacao || !(alvo.observacaoAtual" in nucleo

    def test_coluna_de_selecao_participa_do_layout_da_tabela(self):
        """tabelas.js indexa por data-col: sem ele a coluna some ao reordenar."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        html = (raiz / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
        assert html.count('data-col="sel"') == 3, "cabecalho, linha e parte de rateio"


def test_descricao_do_manual_se_edita_na_linha(ctx):
    """A descricao do lancamento MANUAL e do usuario e se edita na propria linha.

    Isso morava so no modal da Resumida - o unico recurso que ela ainda tinha e
    a Detalhada nao - e veio antes de ela sair (10/09/2026). A de um lancamento
    do banco pertence ao banco (secao 4.6): nao vira campo, e o servidor recusa
    no proprio UPDATE mesmo que alguem chame a API direto.
    """
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    for editavel, pode, esperado in ((True, True, True), (False, True, False), (True, False, False)):
        ctxt = TestDetalhadaPorPeriodo().contexto(pode_editar=pode)
        ctxt["linhas"][0]["descricao_editavel"] = editavel
        html = render_template("lancamentos_fatura.html", **ctxt)
        assert ('data-campo="descricao"' in html) is esperado, (editavel, pode)

    view = (raiz / "views" / "lancamentos.py").read_text(encoding="utf-8")
    assert '"descricao_editavel": str(row["account_id"]) == CONTA_MANUAL_ID' in view
    assert "escopo = \" AND account_id = %s\" if \"descricao\" in data" in view
    # grava pelo MESMO caminho da observacao: espera curta e ao sair do campo
    js = (raiz / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    assert "input[data-campo=\"descricao\"]" in js.split("const SELETOR_TEXTO", 1)[1].split(";", 1)[0]



def test_bloqueio_de_ok_por_falta_de_pdf_tem_mensagem_propria():
    """Secao 7.5: OK de cartao de credito exige vinculo com uma linha do PDF.

    Sem tratar `sem_pdf_conciliado`, as duas telas caiam no texto generico e
    mandavam "preencha os campos obrigatorios" mesmo com `faltando` VAZIO - o
    usuario procurava um campo que nao existia enquanto o motivo era outro.
    """
    for arquivo in ("lancamentos_fatura.js",):
        caminho = Path(__file__).resolve().parents[1] / "static" / arquivo
        js = caminho.read_text(encoding="utf-8")
        assert "sem_pdf_conciliado" in js, f"{arquivo} ignora a trava do PDF"
        assert "Conciliar fatura" in js, (
            f"{arquivo} precisa dizer ONDE resolver, nao so que esta bloqueado"
        )


def test_o_filtro_fatura_so_existe_para_um_cartao_com_fatura(monkeypatch):
    """O liga/desliga que o usuario pediu (10/09/2026).

    Com varias origens nao existe "a fatura"; em conta corrente e dinheiro,
    fatura nao existe; num cartao sem fatura importada o ciclo seria palpite. Nos
    tres casos o filtro some. Era a mesma regra que decidia se as setas da
    Resumida andavam por ciclo - ela saiu, a regra ficou.
    """
    from views import lancamentos as L
    contas = {"cc": {"tipo": "CHECKING"}, "cartao": {"tipo": "CREDIT"},
              "sem_fatura": {"tipo": "CREDIT"}}
    pedidos = []

    def lista_falsa(cur, account_id):
        pedidos.append(account_id)
        return [] if account_id == "sem_fatura" else [{"id": 3, "url": "/x"}]

    monkeypatch.setattr(L, "lista_de_faturas", lista_falsa)
    assert L.faturas_para_o_filtro(None, [], contas) == []
    assert L.faturas_para_o_filtro(None, ["cartao", "cc"], contas) == []
    assert L.faturas_para_o_filtro(None, ["cc"], contas) == []
    assert L.faturas_para_o_filtro(None, ["sem_fatura"], contas) == []
    itens = L.faturas_para_o_filtro(None, ["cartao"], contas, id_em_foco=3)
    assert itens and itens[0]["selecionada"] is True
    # so consulta o banco quando pode haver fatura: uma origem, e ela e cartao
    assert pedidos == ["sem_fatura", "cartao"]


def test_busca_da_detalhada_nao_casa_com_opcao_nao_escolhida():
    """Com a classificacao na linha, `textContent` traria TODAS as opcoes de
    cada select - e toda linha casaria com quase qualquer termo. E o titular,
    que virou avatar, so existe no tooltip: sem le-lo, buscar por 'Andrea'
    deixaria de funcionar."""
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    js = (raiz / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    fn = js.split("function textoPesquisavel(no)", 1)[1].split("\n  }", 1)[0]
    assert "select.remove()" in fn, "as opcoes nao escolhidas saem do texto"
    assert "select.options[select.selectedIndex]" in fn
    assert "data-tip" in fn, "o titular do avatar continua pesquisavel"


def test_seletor_de_fatura_oferece_meses_futuros_com_dados():
    """Parcela futura chega adiantada do Pluggy e nao aparecia em lugar nenhum:
    o ciclo em andamento termina hoje."""
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    view = (raiz / "views" / "lancamentos.py").read_text(encoding="utf-8")
    template = (raiz / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    js = (raiz / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")

    fn = view.split("def _meses_futuros_com_dados", 1)[1].split("\ndef ", 1)[0]
    assert "'YYYY-MM') > %s" in fn, "do mes seguinte em diante, nao de amanha"
    assert "COALESCE(t.duplicada,false)=false" in fn
    assert "somente_conciliacao" in fn
    # e um ciclo PREVISTO, nao uma fatura: a janela e o mes civil
    assert "mes civil" in fn or "MES CIVIL" in fn
    assert "· Previsto" in template
    assert "fatura.previsto" in template
    # O ciclo previsto aparece no filtro Fatura como qualquer outro, com a URL
    # ja montada no servidor: `_url_da_fatura` e o ponto unico dela, e monta-la
    # tambem no JS faria a navegacao discordar de si mesma.
    assert "item.previsto" in template
    assert "window.irParaFatura = function" in js
    assert "startsWith('futuro-')" not in js


def test_setas_da_detalhada_seguem_a_mesma_ordem_do_seletor():
    """As setas paravam na fatura mais nova: o ciclo em andamento e os meses
    previstos so eram alcancaveis pelo seletor. A URL de cada entrada e montada
    NO SERVIDOR - em dois lugares, a navegacao discordaria de si mesma."""
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    view = (raiz / "views" / "lancamentos.py").read_text(encoding="utf-8")
    template = (raiz / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")

    url = view.split("def _url_da_fatura", 1)[1].split("\ndef ", 1)[0]
    assert 'startswith("futuro-")' in url and "andamento=1&mes=" in url
    assert 'item.get("em_andamento")' in url
    assert "fatura_id=" in url

    # o template so consome a url pronta, nao remonta nada
    assert '{{ fatura_antiga.url }}' in template
    assert '{{ fatura_nova.url }}' in template
    assert 'fatura_nova.em_andamento' not in template

    # e as duas telas calculam vizinhas pela posicao na lista do seletor
    assert view.count("_vizinhas_no_seletor(") == 3


class TestDetalhadaPorPeriodo:
    """A Detalhada recortando por PERIODO, e nao por fatura.

    Existe porque a tela nasceu presa a uma fatura de cartao: conta corrente,
    dinheiro e lancamento manual nao pertencem a fatura nenhuma e por isso
    nunca tiveram lugar nela. Renderiza com o formato REAL que
    `_render_periodo` entrega - erro de template nao aparece em py_compile nem
    na suite estrutural, e derruba a tela inteira (secao 10.3 n.3).
    """

    def contexto(self, **extra):
        from datetime import datetime
        from decimal import Decimal
        principal = {
            "transacao_id": "11111111-1111-1111-1111-111111111111",
            "descricao": "Agua Visan", "categoria": "Water",
            "dims": {1: 7}, "observacao": "boleto", "rateado": False,
            "exige_dimensoes": True, "conferida": False, "conferida_por": None,
            "conferida_local": None, "sincronizado_local": None,
            "primeiro_sincronizado_local": None,
            "data_local": datetime(2026, 8, 10, 9, 0),
            "valor": Decimal("212.35"), "valor_original": None, "moeda_original": None,
            "status": "POSTED", "tipo": "DEBIT", "parcela_atual": None, "parcela_total": None,
            "observacao_sistema": "", "principal": True, "tecnico": False,
            "fonte": "P", "fonte_nome": "Pluggy", "numero_cartao_final": None,
        }
        base = {
            "titulo": "Lançamentos", "topbar": "", "modo_periodo": True,
            "fatura": {"id": "periodo", "periodo": True, "em_andamento": False, "previsto": False},
            "fatura_nova": None, "fatura_antiga": None, "faturas": [],
            "conta": None, "avatar_banco": None, "contas_credito": [],
            "account_id": "", "status": "todas",
            "categorias": [{"chave": "Water", "nome": "Água"}],
            "dimensoes": [{"id": 1, "nome": "Responsável", "obrigatoria": True}],
            "valores_por_dim": {1: [{"id": 7, "nome": "Família", "icone": None}]},
            "mes": "2026-08", "periodo": "mes", "data_inicio": "", "data_fim": "",
            "origem_filtro_html": '<div class="chipfilter"></div>',
            "por_categoria": [{"nome": "Água", "total": 212.35}],
            "filtros_situacao": [{"rotulo": "Conferido", "url": "?status=conferida"}],
            "status_opcoes": opcoes_de_status(),
            "faturas_da_origem": [], "url_do_periodo": "",
            "receita_mes": 0, "gasto_real": 212.35, "resultado_mes": -212.35,
            "total_reais": 1, "total_recebidos": 1, "total_fora": 0, "conf_reais": 0,
            "pendente_classificacao": 0, "classificados_reais": 1, "pendentes_ok": 1,
            "pct_classificados": 100, "pct_conferidos": 0,
            "totais": {}, "contagens": {}, "config_json": "{}",
            "projeto_portfolio_map": {}, "url_resumida": "/",
            "pode_editar": True, "pode_conferir": True, "pode_regras": True,
            "pode_manual": True,
            "linhas": [{
                "id": "t-11111111-1111-1111-1111-111111111111",
                "data": datetime(2026, 8, 10, 9, 0), "descricao": "Agua Visan",
                "origem_selo": '<span class="selo">UN</span>',
                "origem_texto": "Unicred C/C", "origem_completa": "Unicred conta corrente",
                "autor": None, "titular": None, "titular_fonte": None,
                "procedencia": "Unicred conta corrente",
                "rateios": [], "rateio_valido": True, "valor_rateio": 0.0,
                "cartao_aguardando": False, "cartao_nome": None, "cartao_final": None,
                "parcela_atual": None, "parcela_total": None,
                "valor": Decimal("212.35"), "valor_fmt": "- R$ 212,35",
                "cor_valor": "color:var(--bad)", "pagamento": False,
                "vinculos": [principal], "principal": principal, "multiplos": False,
                "requer_validacao": False, "validacao_motivos": [], "faltando": [],
                "classificada": True, "conferida": False, "fora_do_resultado": "",
                "suspeita_duplicidade": False, "pendente_banco": False,
                "pendente_bloqueia_ok": False, "natureza_estado": "dre",
                "natureza_rotulo": "Despesa", "estado": "periodo",
            }],
        }
        base.update(extra)
        return base

    def test_a_tela_abre_com_conta_corrente(self, ctx):
        html = render_template("lancamentos_fatura.html", **self.contexto())
        assert "Agua Visan" in html
        # a origem precisa aparecer: num recorte com varias contas, sem ela a
        # tela nao diz de onde veio cada lancamento
        assert "Unicred C/C" in html
        assert "- R$ 212,35" in html
        # o recorte por periodo nao fala em fatura nem em conciliacao
        assert "Falta vincular" not in html
        assert "Ver conciliação da fatura" not in html

    def test_o_gasto_por_categoria_acompanha_o_recorte(self, ctx):
        """Ele e do PERIODO: numa fatura o total ja e a soma das compras
        daquele cartao, e a quebra por categoria vive na conciliacao."""
        html = render_template("lancamentos_fatura.html", **self.contexto())
        assert "Gasto por categoria (mês)" in html
        assert "Água" in html.split("cat-breakdown", 1)[1]

    def test_os_cards_sao_os_do_dre_e_nao_os_da_conciliacao(self, ctx):
        """Card de conciliacao num recorte por periodo nao significa nada: com
        varias origens misturadas nao existe "a fatura" a conciliar."""
        html = render_template("lancamentos_fatura.html", **self.contexto())
        assert "Resultado no DRE" in html and "Receitas no DRE" in html
        assert "Divergências" not in html and "Com agregados" not in html

    def test_lancamento_manual_mostra_quem_digitou(self, ctx):
        ctxt = self.contexto()
        ctxt["linhas"][0]["autor"] = "ronaldo"
        html = render_template("lancamentos_fatura.html", **ctxt)
        assert 'class="avatar-autor"' in html and ">RO<" in html

    def test_fora_do_resultado_nao_vira_pendencia(self, ctx):
        """Registro fora do resultado nunca tem classificacao completa e nao e
        trabalho pendente (secao 10.4 n.13)."""
        ctxt = self.contexto()
        ctxt["linhas"][0]["fora_do_resultado"] = "Mesmo evento que outro lançamento."
        ctxt["linhas"][0]["faltando"] = []
        html = render_template("lancamentos_fatura.html", **ctxt)
        assert "Faltam:" not in html

    def test_a_tela_por_fatura_continua_de_pe(self, ctx):
        """A mesma template serve os dois recortes: o de fatura nao pode ter
        sido quebrado pelo novo."""
        from datetime import date
        ctxt = self.contexto(
            modo_periodo=False,
            fatura={"id": 3, "mes_referencia": 8, "ano_referencia": 2026,
                    "periodo_inicio": date(2026, 7, 13), "periodo_fim": date(2026, 8, 12),
                    "vencimento": date(2026, 8, 20), "em_andamento": False, "previsto": False},
            faturas=[{"id": 3, "mes_referencia": 8, "ano_referencia": 2026,
                      "em_andamento": False, "previsto": False}],
            contas_credito=[("abc", "Unicred", "Unicred Conjunta", "Unicred", "")],
            account_id="abc",
            totais={"pdf": 100, "dre": 100, "fora": 0, "pendente": 0,
                    "pendente_ok": 0, "sem_vinculo": 0, "divergencia": 0},
            contagens={"linhas": 1, "vinculadas": 1, "classificadas": 1, "conferidas": 1,
                       "multiplos": 0, "pendente_classificacao": 0, "pendente_ok": 0,
                       "divergencias": 0},
        )
        ctxt["linhas"][0]["data"] = date(2026, 8, 10)
        html = render_template("lancamentos_fatura.html", **ctxt)
        assert "Fatura Agosto de 2026" in html
        assert "Ver conciliação da fatura" in html
        # A TABELA e a mesma nos dois recortes (decisao do usuario, 10/09/2026):
        # o que muda entre fatura e periodo sao os filtros, nao as colunas.
        assert 'data-col="origem"' in html
        # o formulario manual continua FORA da fatura, de proposito: um
        # lancamento manual nao pertence a fatura nenhuma
        assert 'id="formManual"' not in html


    def contexto_fatura(self, em_andamento=False):
        from datetime import date
        ctxt = self.contexto(
            modo_periodo=False, pode_conferir=not em_andamento,
            status_opcoes=opcoes_de_status(com_fatura=True, em_andamento=em_andamento),
            fatura={"id": "andamento" if em_andamento else 3,
                    "mes_referencia": 8, "ano_referencia": 2026,
                    "periodo_inicio": date(2026, 7, 13), "periodo_fim": date(2026, 8, 12),
                    "vencimento": None if em_andamento else date(2026, 8, 20),
                    "em_andamento": em_andamento, "previsto": False},
            contas_credito=[("abc", "Unicred", "Unicred Conjunta", "Unicred", "")],
            account_id="abc",
            totais={"pdf": 100, "dre": 100, "fora": 0, "pendente": 0,
                    "pendente_ok": 0, "sem_vinculo": 0, "divergencia": 0},
            contagens={"linhas": 1, "vinculadas": 1, "classificadas": 1, "conferidas": 1,
                       "multiplos": 0, "pendente_classificacao": 0, "pendente_ok": 0,
                       "divergencias": 0},
        )
        ctxt["linhas"][0]["data"] = date(2026, 8, 10)
        return ctxt

    def test_descricao_e_escapada(self, ctx):
        """A descricao vem do banco (Pluggy ou lancamento manual digitado)."""
        ctxt = self.contexto()
        ctxt["linhas"][0]["descricao"] = "<img src=x onerror=alert(1)>"
        html = render_template("lancamentos_fatura.html", **ctxt)
        assert "<img src=x" not in html
        assert "&lt;img" in html

    def test_apelido_do_cartao_e_escapado_mas_o_selo_nao(self, ctx):
        """O apelido e digitado pelo usuario em /contas; o selo e HTML do app."""
        ctxt = self.contexto()
        ctxt["linhas"][0]["origem_texto"] = '"><script>alert(1)</script>'
        html = render_template("lancamentos_fatura.html", **ctxt)
        assert "<script>alert(1)</script>" not in html
        assert '<span class="selo">UN</span>' in html

    def test_cada_card_que_parece_clicavel_leva_a_um_filtro_real(self, ctx):
        """Todo `data-filtro` de card tem que existir no seletor de Status.

        O clique no card escolhe o valor NO SELETOR: um valor que ele nao tem vira
        vazio no navegador, e a rota cai em "Todas" - a tela diz que filtrou e
        mostra tudo. Aconteceu na fatura em 10/09/2026, com o card "OK dos
        lancamentos" apontando para `pendente_ok`. Por isso vale nos TRES
        estados da tela.
        """
        import re
        for rotulo, ctxt in (("periodo", self.contexto()),
                             ("fatura", self.contexto_fatura()),
                             ("em andamento", self.contexto_fatura(em_andamento=True))):
            html = render_template("lancamentos_fatura.html", **ctxt)
            filtros = set(re.findall(r'data-filtro="([^"]+)"', html))
            assert filtros, rotulo + ": os cards deixaram de oferecer filtro"
            seletor = html.split('id="periodoStatus"', 1)[1].split("</select>", 1)[0]
            opcoes = set(re.findall(r'<option value="([^"]+)"', seletor))
            assert filtros <= opcoes, (
                rotulo + ": card aponta para status que o seletor nao oferece: "
                + str(sorted(filtros - opcoes)))

    def test_a_tabela_e_a_mesma_nos_dois_recortes(self, ctx):
        """Decisao do usuario (10/09/2026): trocar de fatura para periodo nao
        pode mudar COMO os lancamentos sao vistos - o que esses botoes mudam sao
        os filtros. Colunas diferentes obrigam a reaprender a tela a cada troca,
        e foi por isso que os `data-col` ja tinham sido unificados (secao 7.1).
        """
        from datetime import date
        por_fatura = self.contexto(
            modo_periodo=False,
            fatura={"id": 3, "mes_referencia": 8, "ano_referencia": 2026,
                    "periodo_inicio": date(2026, 7, 13), "periodo_fim": date(2026, 8, 12),
                    "vencimento": date(2026, 8, 20), "em_andamento": False, "previsto": False},
            faturas=[{"id": 3, "mes_referencia": 8, "ano_referencia": 2026,
                      "em_andamento": False, "previsto": False}],
            contas_credito=[("abc", "Unicred", "Unicred Conjunta", "Unicred", "")],
            account_id="abc",
            totais={"pdf": 100, "dre": 100, "fora": 0, "pendente": 0,
                    "pendente_ok": 0, "sem_vinculo": 0, "divergencia": 0},
            contagens={"linhas": 1, "vinculadas": 1, "classificadas": 1, "conferidas": 1,
                       "multiplos": 0, "pendente_classificacao": 0, "pendente_ok": 0,
                       "divergencias": 0},
        )
        por_fatura["linhas"][0]["data"] = date(2026, 8, 10)
        htmls = {
            "periodo": render_template("lancamentos_fatura.html", **self.contexto()),
            "fatura": render_template("lancamentos_fatura.html", **por_fatura),
        }
        for recorte, html in htmls.items():
            cabecalho = html.split("<thead>")[1].split("</thead>")[0]
            colunas = re.findall(r'data-col="([^"]+)"', cabecalho)
            assert colunas == ["sel", "data", "desc", "origem", "categoria",
                               "dim_1", "valor", "obs", "regra", "check"], recorte
            # Gasto por categoria e "Filtrar por situacao" existem nos dois: um
            # deles so no periodo era um recurso que sumia ao trocar de botao.
            assert "Gasto por categoria" in html, recorte
            assert 'class="legenda-lancamentos"' in html, recorte

    def test_a_coluna_origem_nao_nasce_oculta_em_recorte_nenhum(self, ctx):
        """Os dois recortes compartilham o mesmo `data-tabela="fatura"`, entao o
        estado de colunas escondidas e UM SO no localStorage. Marcar
        `data-oculta-padrao` na Origem de um dos recortes esconderia a coluna no
        outro tambem, e quem decidiria seria o recorte que o usuario abrisse
        primeiro - esconder passa a ser escolha dele, pelo cabecalho."""
        html = render_template("lancamentos_fatura.html", **self.contexto())
        cabecalho = html.split("<thead>")[1].split("</thead>")[0]
        origem = re.search(r'<th[^>]*data-col="origem"[^>]*>', cabecalho).group(0)
        assert "data-oculta-padrao" not in origem


    def test_o_corpo_da_tabela_nao_sabe_em_que_recorte_esta(self, ctx):
        """Um layout de lancamento so (decisao do usuario, 10/09/2026).

        O `<tbody>` le SEMPRE os mesmos campos - `procedencia`, `valor_fmt`,
        `cor_valor`, `rateios`, `pendente_bloqueia_ok` - e quem decide o conteudo
        e o construtor da linha. Assim o que muda de um lancamento para o outro e
        o TIPO dele (tem rateio, tem titular, e de conta corrente), nunca a tela
        em que ele esta sendo visto. Enquanto o template perguntava
        `modo_periodo`, cada `if` era uma diferenca a mais para manter viva nos
        dois lados.

        O `<thead>` continua podendo perguntar: ali o tooltip diz DE ONDE o
        numero vem, e isso realmente muda - na fatura o valor e o cobrado pela
        operadora, no periodo e o do lancamento.
        """
        from pathlib import Path
        template = (Path(__file__).resolve().parent.parent
                    / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
        corpo = template.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        assert "modo_periodo" not in corpo


def test_o_rateio_tem_um_construtor_so():
    """As partes de um rateio sao montadas pelos TRES construtores de linha -
    recorte por periodo, fatura oficial e fatura em andamento (a Resumida, o
    quarto, saiu em 10/09/2026). A consulta estava escrita duas vezes, palavra
    por palavra, e uma copia nova divergiria na primeira regra nova."""
    import pathlib
    fonte = (pathlib.Path(__file__).resolve().parent.parent
             / "views" / "lancamentos.py").read_text(encoding="utf-8")
    # `_estado_rateios` fica de fora de proposito: ele le UMA transacao para a
    # auditoria antes/depois da API, com cursor de tupla - outro proposito.
    assert fonte.count('"WHERE r.transacao_id IN %s ORDER BY r.transacao_id') == 1, (
        "a consulta das partes das telas tem que sair de partes_do_rateio()")
    assert fonte.count("def rateio_da_linha(") == 1
    # a definicao mais uma chamada em cada um dos tres construtores
    assert fonte.count("rateio_da_linha(") == 4
    assert fonte.count("partes_do_rateio(") == 4


def test_a_origem_da_linha_tem_uma_implementacao_so():
    """Tres construtores de linha mostram a coluna Origem - a Resumida, o
    recorte por periodo e o recorte por fatura. Tres copias da mesma regra
    divergiriam no primeiro banco novo, que e literalmente como nasceram os 57
    falsos pendentes da secao 6.5 nº 10."""
    import pathlib
    fonte = (pathlib.Path(__file__).resolve().parent.parent
             / "views" / "lancamentos.py").read_text(encoding="utf-8")
    assert fonte.count("def origem_da_linha(") == 1
    # e ela mora no modulo, nao aninhada dentro de uma rota
    assert "\ndef origem_da_linha(" in fonte
    assert fonte.count("origem_da_linha(") >= 4


def test_as_duas_telas_leem_valor_pela_mesma_conta():
    """Ordenar por Valor tem uma conta so, e ela mora no tabelas.js.

    Lida de novo na Detalhada, a conversao apagava a virgula decimal
    (`replace(/,/g,'')`) e ordenava R$ 212,35 como 21.235 - o mesmo texto,
    duas leituras, resultados diferentes.
    """
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    tabelas = (raiz / "static" / "tabelas.js").read_text(encoding="utf-8")
    detalhada = (raiz / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    assert "window.pdmNumeroDeTexto = function" in tabelas
    assert "window.pdmNumeroDeTexto(" in detalhada
    assert "replace(/,/g, '')" not in detalhada


class TestSemanticaDeLinha:
    """Cor nunca e a unica explicacao de estado (secao 7.6): cada situacao vira
    um ponto no inicio da linha, uma frase no tooltip e um item na legenda."""

    def linha_com(self, base, **flags):
        from views.lancamentos import situacoes_da_linha, texto_das_situacoes
        situacoes = situacoes_da_linha(**flags)
        base["linhas"][0]["situacoes"] = situacoes
        base["linhas"][0]["situacoes_texto"] = texto_das_situacoes(situacoes)
        return base

    def test_o_que_falta_conferir_e_que_fica_cinza(self):
        """Decisao do usuario (07/09/2026). A Detalhada fazia o CONTRARIO -
        `.fatura-ok` pintava o conferido - e no fim do mes a tela inteira ficava
        cinza justamente ao contrario do que se procura."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        css = (raiz / "static" / "app.css").read_text(encoding="utf-8")
        template = (raiz / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
        assert ".fatura-tabela tbody tr[data-linha]:not(.conferida)" in css
        assert "tr.conferida { background: transparent; }" in css
        # a regra nao pode voltar a depender do id de UMA das telas
        assert "tr.fatura-ok" not in css and "tr.fatura-ok" not in template
        assert "fatura-pendente" not in template

    def test_as_duas_telas_calculam_situacao_pela_mesma_lista(self):
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        view = (raiz / "views" / "lancamentos.py").read_text(encoding="utf-8")
        assert view.count("def situacoes_da_linha") == 1
        # os tres construtores de linha do sistema (a Resumida saiu em 10/09/2026)
        assert view.count("situacoes_da_linha(") == 4

    def test_a_situacao_da_linha_aparece_por_extenso_e_no_selo(self, ctx):
        """Os pontos no inicio da linha sairam a pedido do usuario
        (09/09/2026). A explicacao de estado nao pode ficar so na cor
        (secao 7.6): ela continua no tooltip da data, no selo de fora do
        resultado e nos atalhos de filtro do rodape."""
        from tests.test_templates import TestDetalhadaPorPeriodo
        base = TestDetalhadaPorPeriodo().contexto()
        base["linhas"][0]["fora_do_resultado"] = "Mesmo evento que outro lançamento."
        base = self.linha_com(base, substituido=True, suspeita=True)
        html = render_template("lancamentos_fatura.html", **base)
        assert "linha-ponto" not in html
        assert 'class="selo-fora"' in html
        assert "Possível duplicidade — revisar" in html
        assert 'class="legenda-lancamentos"' in html
        # o tooltip fica na propria celula de data (a do corpo, nao o cabecalho)
        celula = html.split('class="cel-data-fatura"', 1)[1].split(">", 1)[0]
        assert "Fora do resultado" in celula and "Possível duplicidade" in celula

    def test_linha_sem_situacao_nao_fica_com_tooltip_vazio(self, ctx):
        from tests.test_templates import TestDetalhadaPorPeriodo
        base = self.linha_com(TestDetalhadaPorPeriodo().contexto())
        html = render_template("lancamentos_fatura.html", **base)
        assert "Lançamento contabilizado" in html

    def test_o_resumo_parcial_atualiza_a_situacao_junto_com_o_ok(self):
        """Sem isso o tooltip continuaria dizendo "pendente" numa linha que
        acabou de ser conferida: a linha diria uma coisa e a cor, outra."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        js = (raiz / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
        assert "linha.className = nova.className" in js
        assert "data.dataset.tip = dataNova.dataset.tip" in js
        assert "linha-indicadores" not in js


class TestRateioNasDuasTelas:
    """As partes de um rateio sao editadas nas linhas abaixo do pai, pelo mesmo
    codigo nas duas telas (secao 4.4 e 7.1)."""

    def contexto_rateado(self):
        from tests.test_templates import TestDetalhadaPorPeriodo
        base = TestDetalhadaPorPeriodo().contexto()
        linha = base["linhas"][0]
        linha["principal"]["rateado"] = True
        linha["rateios"] = [
            {"id": 1, "valor": 150.00, "cor_valor": "", "categoria": "Water",
             "categoria_nome": "Água", "observacao": "parte do Ronaldo",
             "dims": {1: 7}, "dims_rotulos": {1: "Família"}},
            {"id": 2, "valor": 62.35, "cor_valor": "", "categoria": "Water",
             "categoria_nome": "Água", "observacao": "", "dims": {1: 7},
             "dims_rotulos": {1: "Família"}},
        ]
        linha["rateio_valido"] = True
        linha["valor_rateio"] = 212.35
        linha["situacoes"] = []
        linha["situacoes_texto"] = "Lançamento contabilizado"
        return base

    def test_as_partes_aparecem_como_linhas_abaixo_do_pai(self, ctx):
        html = render_template("lancamentos_fatura.html", **self.contexto_rateado())
        assert html.count('class="rateio-row') == 2
        assert "rateio-valor-inline" in html and "rateio-cat-select" in html
        assert "rateio-obs-inline" in html and "rateio-salvar-inline" in html
        # o nucleo le o total do pai para conferir a soma em centavos
        assert 'data-rateio-total="212.35"' in html
        assert "— Parte 1" in html and "— Parte 2" in html

    def test_o_botao_das_partes_fica_depois_da_descricao(self, ctx):
        """Antes dela, empurrava o texto e as linhas com e sem botao comecavam
        em colunas diferentes (secao 7.6)."""
        html = render_template("lancamentos_fatura.html", **self.contexto_rateado())
        desc = html.index("desc-loja")
        botao = html.index('class="rateio-toggle"')
        assert desc < botao

    def test_as_partes_nascem_recolhidas(self, ctx):
        html = render_template("lancamentos_fatura.html", **self.contexto_rateado())
        trecho = html.split('class="rateio-row', 1)[1].split(">", 1)[0]
        assert "hidden" in html.split('class="rateio-row', 1)[1].split("<td", 1)[0]

    def test_o_nucleo_do_rateio_e_o_mesmo_nas_duas_telas(self):
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        nucleo = (raiz / "static" / "rateio.js").read_text(encoding="utf-8")
        assert nucleo.count("fetch(") == 1, "um unico ponto de gravacao"
        for tela in ("lancamentos_fatura.html",):
            html = (raiz / "templates" / tela).read_text(encoding="utf-8")
            assert "/static/rateio.js" in html, tela
        for js in ("lancamentos_fatura.js",):
            texto = (raiz / "static" / js).read_text(encoding="utf-8")
            assert "window.pdmRateio.configurar" in texto, js
            assert "/rateios'" not in texto, js + ": segundo caminho de gravacao"


class TestAcoesDoLancamento:
    """Rateio e exclusao nao sao campos: sao acoes sobre o lancamento, e moram
    num rodape do painel (decisao do usuario, 09/09/2026). A linha ja tem dez
    colunas, e o painel e onde se abre um lancamento especifico."""

    def contexto(self, **extra):
        from tests.test_templates import TestDetalhadaPorPeriodo
        base = TestDetalhadaPorPeriodo().contexto()
        base["linhas"][0]["situacoes"] = []
        base["linhas"][0]["situacoes_texto"] = "Lançamento contabilizado"
        base["linhas"][0]["rateios"] = []
        base["linhas"][0]["rateio_valido"] = True
        base["linhas"][0]["valor_rateio"] = 212.35
        base["linhas"][0]["principal"]["pode_excluir"] = False
        for chave, valor in extra.items():
            base["linhas"][0]["principal"][chave] = valor
        return base

    def test_as_acoes_ficam_na_faixa_do_vinculo_principal(self, ctx):
        """Modelo A (decisao do usuario, 09/09/2026): procedência e ações na
        MESMA linha. O quadro de rateio nasce fechado - ele traz um select de
        categoria e um por dimensão, e deixa-lo aberto custava 111px em todo
        lançamento, para uma ação rara."""
        html = render_template("lancamentos_fatura.html", **self.contexto())
        assert 'class="vinculo-faixa principal"' in html
        assert "data-abrir-rateio=" in html
        assert 'data-rateio-quadro="11111111-1111-1111-1111-111111111111"' in html
        assert 'data-rateio-total="212.35"' in html
        quadro = html.split("data-rateio-quadro", 1)[1].split(">", 1)[0]
        assert "hidden" in quadro, "o quadro nasce fechado"
        # a cor nao e a unica explicacao de estado (secao 7.6)
        assert "contabilizado e editável" in html
        # o painel continua sendo auditoria: os campos nao voltaram para dentro
        painel = html.split('class="vinculos-detalhe"', 1)[1]
        assert 'data-campo="categoria"' not in painel
        assert 'data-campo="observacao"' not in painel
        # e nao ha mais tres caixas aninhadas
        assert "acoes-lancamento" not in html and "vinculo-grid" not in html

    def test_so_manual_e_importado_oferecem_excluir(self, ctx):
        """Lancamento do Pluggy nunca se apaga: a origem fica para auditoria
        (secao 9.3)."""
        assert "data-excluir-lancamento" not in render_template(
            "lancamentos_fatura.html", **self.contexto())
        assert "data-excluir-lancamento" in render_template(
            "lancamentos_fatura.html", **self.contexto(pode_excluir=True))

    def test_a_fatura_nao_ganha_o_rodape_de_acoes(self, ctx):
        """Rateio e exclusao pertencem ao recorte por periodo; na fatura a linha
        e do documento, e o lancamento pode nem existir."""
        base = self.contexto(pode_excluir=True)
        base["modo_periodo"] = False
        from datetime import date
        base["fatura"] = {"id": 3, "mes_referencia": 8, "ano_referencia": 2026,
                          "periodo_inicio": date(2026, 7, 13), "periodo_fim": date(2026, 8, 12),
                          "vencimento": None, "em_andamento": False, "previsto": False}
        base["faturas"] = [dict(base["fatura"])]
        base["contas_credito"] = [("abc", "U", "Unicred", "U", "")]
        base["totais"] = {"pdf": 0, "dre": 0, "fora": 0, "pendente": 0,
                          "pendente_ok": 0, "sem_vinculo": 0, "divergencia": 0}
        base["contagens"] = {"linhas": 1, "vinculadas": 1, "classificadas": 1,
                             "conferidas": 0, "multiplos": 0, "pendente_classificacao": 0,
                             "pendente_ok": 1, "divergencias": 0}
        base["linhas"][0]["data"] = date(2026, 8, 10)
        html = render_template("lancamentos_fatura.html", **base)
        assert 'class="acoes-lancamento"' not in html

    def test_o_quadro_do_rateio_tem_uma_implementacao_so(self):
        """Criar, mudar a quantidade de partes e desfazer sao a mesma acao nas
        duas telas. Enquanto isso morava so no modal, a Detalhada nao tinha
        como criar rateio nenhum."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        nucleo = (raiz / "static" / "rateio.js").read_text(encoding="utf-8")
        js = (raiz / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
        assert "function montarQuadro(box" in nucleo
        assert "rateio-parte-titulo" in nucleo
        assert "rateio-parte-titulo" not in js, "segunda copia do quadro"
        assert "window.pdmRateio.montarQuadro" in (
            raiz / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")

    def test_o_valor_da_pendencia_sai_em_portugues(self):
        """toFixed(2) escreve 699.82, que e ingles - a mesma licao do valor_pt()
        no Python (secao 7.8-A)."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        tabelas = (raiz / "static" / "tabelas.js").read_text(encoding="utf-8")
        assert "window.pdmMoedaBr = function" in tabelas and "'pt-BR'" in tabelas
        for arquivo in ("rateio.js", "lancamentos_fatura.js"):
            texto = (raiz / "static" / arquivo).read_text(encoding="utf-8")
            assert "'Rateado R$ '" not in texto, arquivo


class TestFormularioManualCompartilhado:
    """O formulario e o mesmo nas duas telas (partial + manual.js). Renderizado
    aqui com o contexto REAL de cada uma: variavel que falta nao levanta erro no
    Jinja - o campo so nasce vazio, em silencio."""

    def test_o_recorte_por_periodo_desenha_o_formulario_inteiro(self, ctx):
        from tests.test_templates import TestDetalhadaPorPeriodo
        base = TestDetalhadaPorPeriodo().contexto()
        base["linhas"][0]["situacoes"] = []
        base["linhas"][0]["situacoes_texto"] = ""
        base["linhas"][0]["rateios"] = []
        base["linhas"][0]["rateio_valido"] = True
        base["linhas"][0]["valor_rateio"] = 0
        base["linhas"][0]["principal"]["pode_excluir"] = False
        base["hoje_iso"] = "2026-09-09"
        html = render_template("lancamentos_fatura.html", **base)
        assert 'id="formManual"' in html
        # a data nasce preenchida: sem hoje_iso o campo vem vazio e ninguem avisa
        assert 'id="manualData" required value="2026-09-09"' in html
        assert 'id="manualCategoria"' in html and 'class="manual-dim"' in html
        assert "Responsável" in html.split('id="formManual"', 1)[1]
        assert 'id="manualConferida"' in html

    def test_a_view_do_periodo_entrega_a_data_de_hoje(self):
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        view = (raiz / "views" / "lancamentos.py").read_text(encoding="utf-8")
        trecho = view.split("def _render_periodo", 1)[1].split("\ndef ", 1)[0]
        assert "hoje_iso=" in trecho


class TestFiltrarSemRecarregar:
    """Filtrar troca a lista NO LUGAR: recarregar joga quem esta no meio da
    conferencia de volta ao topo. A URL acompanha, entao o Voltar do navegador
    retorna ao filtro anterior."""

    def js(self, nome):
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        return (raiz / "static" / nome).read_text(encoding="utf-8")

    def test_a_troca_por_ajax_tem_uma_implementacao_so(self):
        tabelas = self.js("tabelas.js")
        assert "window.pdmTrocarPorAjax = function" in tabelas
        assert "history.pushState" in tabelas
        for tela, funcao in (("lancamentos_fatura.js", "window.aplicarFiltrosPeriodo = function"),):
            texto = self.js(tela)
            assert "pdmTrocarPorAjax(" in texto, tela
            # o filtro nao monta o proprio DOMParser: a troca de blocos e uma so.
            # (o resumo parcial da Detalhada tem um, e faz outra coisa: atualiza
            # linha a linha sem destruir o que o usuario esta preenchendo)
            trecho = texto.split(funcao, 1)[1].split("\n  };", 1)[0]
            assert "DOMParser" not in trecho, tela + ": segunda copia da troca"

    def test_a_detalhada_nao_recarrega_ao_filtrar(self):
        """Filtrar troca a lista NO LUGAR nos dois recortes, e a troca de blocos
        e uma so. Ate 10/09/2026 o Status da fatura recarregava a pagina inteira
        enquanto o seletor ao lado trocava no lugar - dois comportamentos para a
        mesma acao."""
        js = self.js("lancamentos_fatura.js")
        status = js.split("window.aplicarFiltroStatus = function", 1)[1].split("\n  };", 1)[0]
        assert "location.assign" not in status
        assert "trocarBlocos(" in status
        # Periodo e origem trocam no lugar DENTRO do recorte por periodo. Sair de
        # uma fatura troca o recorte - cabecalho, cards e formulario sao outros -
        # e a troca por AJAX e pareada pelo indice: o segundo grupo de cards da
        # fatura ficaria na tela sob a lista do periodo. So ai recarrega.
        periodo = js.split("window.aplicarFiltrosPeriodo = function", 1)[1].split("\n  };", 1)[0]
        assert "trocarBlocos(url)" in periodo
        antes_do_assign = periodo.split("location.assign", 1)[0]
        assert "if (!mesInput)" in antes_do_assign, "recarregar so ao sair da fatura"
        assert js.count("pdmTrocarPorAjax(") == 1, "a troca de blocos e uma so"
        # a barra INTEIRA e trocada: o filtro Fatura aparece e some conforme a
        # origem, e as opcoes de Status crescem e encolhem junto
        blocos = js.split("function trocarBlocos(", 1)[1].split("}\n", 1)[0]
        assert "'.fatura-filtros'" in blocos

    def test_a_tabela_trocada_volta_a_ter_alcas_e_ordenacao(self):
        """replaceWith descarta o elemento antigo com TUDO que estava anexado
        nele: os listeners e as alcas de redimensionar somem."""
        js = self.js("lancamentos_fatura.js")
        assert "window.ativarTabelaAjustavel(tabela, tabela.dataset.tabela)" in js
        assert "ligarOrdenacao();" in js
        # a chave sai do data-tabela: escrita a mao divergiria do template, e as
        # larguras salvas se perderiam em silencio
        assert "'fatura')" not in js.split("aoTrocar", 1)[1][:400]


def test_todos_os_mais_e_menos_ficam_depois_da_descricao():
    """Secao 7.6: antes dela, empurram o texto e as linhas com e sem botao
    comecam em colunas diferentes.

    O `+` que abre a procedencia era a excecao - ficava na primeira celula, com
    caixa propria, competindo com o checkbox e forcando a coluna a 56px.
    """
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    html = (raiz / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    celula_sel = html.split('class="cel-sel-fatura" data-col="sel"', 1)[1].split("</td>", 1)[0]
    assert "data-expande" not in celula_sel, "o + voltou para antes da descrição"
    desc = html.split('class="desc-loja"', 1)[1].split("</td>", 1)[0]
    assert "data-expande" in desc
    assert desc.index("</div>") < desc.index("data-expande"), (
        "o + tem que ficar FORA do .desc-texto: ali dentro ele vira uma linha "
        "propria e engorda toda a tabela")
    # sem caixa: o glifo so ganha fundo no hover, como os outros
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    regra = css.split(".expande{", 1)[1].split("}", 1)[0]
    assert "border:0" in regra and "background:transparent" in regra


def test_painel_do_manual_mostra_criado_em_e_ultima_alteracao(ctx):
    """Lancamento manual nunca sincroniza: "ultima sincronizacao" nao diria nada.

    No manual o painel troca os carimbos por "Criado em / Ultima alteracao" -
    o que responde "minha edicao entrou?". A regra veio do modal da Resumida
    (saiu em 10/09/2026) e voltou a pedido do usuario no mesmo dia. Lancamento
    do banco continua com primeira e ultima sincronizacao.
    """
    import pathlib
    from datetime import datetime
    raiz = pathlib.Path(__file__).resolve().parent.parent
    for manual in (True, False):
        ctxt = TestDetalhadaPorPeriodo().contexto()
        principal = ctxt["linhas"][0]["principal"]
        principal["manual"] = manual
        principal["primeiro_sincronizado_local"] = None
        principal["sincronizado_local"] = datetime(2026, 8, 10, 9, 0)
        principal["atualizado_local"] = datetime(2026, 9, 10, 18, 42)
        html = render_template("lancamentos_fatura.html", **ctxt)
        painel = html.split('class="detalhes-grid"', 1)[1].split("</div></div>", 1)[0]
        assert ("Criado em" in painel) is manual
        assert ("Última alteração" in painel) is manual
        assert ("Primeira sincronização" in painel) is not manual
        if manual:
            # sem primeira sincronizacao, "Criado em" cai na ultima - a regra do modal
            assert "10/08/2026 09:00" in painel
            assert "10/09/2026 18:42" in painel
            assert 'data-atualizado="' in painel

    view = (raiz / "views" / "lancamentos.py").read_text(encoding="utf-8")
    periodo = view.split("def _render_periodo", 1)[1].split("\ndef ", 1)[0]
    assert "t.atualizado_em" in periodo, "so o periodo alcanca lancamento manual"
    assert 'row["manual"] = conta_row.get("tipo") == "MANUAL"' in periodo
    # a hora se atualiza na hora, e vem do servidor
    js = (raiz / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    gravacao = js.split("function salvarEditor(editor, alterado)", 1)[1].split("\n  }\n", 1)[0]
    assert "[data-atualizado=\"" in gravacao and "json.atualizado_em" in gravacao
    assert "new Date(" not in gravacao, "a hora nao pode vir do relogio do navegador"
