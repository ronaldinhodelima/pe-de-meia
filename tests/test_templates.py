"""Renderiza os templates com o formato REAL dos dados que a view entrega.

Existe por causa de um bug que passou despercebido: a tela /importar desempacotava
3 valores de uma tupla que carregar_origens() devolve com 4 desde o commit fcedcf1,
e ficou dando 500 sem ninguem ver (ela nao estava mais no menu). A tela foi removida
depois, mas a licao fica: testar com dado inventado nao pega esse tipo de erro - o
formato usado aqui tem que espelhar o que a view realmente entrega.
"""
from pathlib import Path
from datetime import date

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
        assert "R$ 80.00" in html and "R$ 20.00" in html

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


class TestIndex:
    """A tela de Lançamentos monta linha por linha a partir de dado do banco e do
    Pluggy, e ainda entrega dois blocos JSON para o lancamentos.js ler."""

    DIMS = [{"id": 1, "nome": "Responsável", "obrigatoria": True}]
    VALS = {1: [{"id": 10, "dimensao_id": 1, "nome": "Ronaldo"}]}

    def linha(self, **kw):
        base = dict(
            id="tx1", classes="", data_dia="13/08/26", data_hora="00:00",
            data_full="13/08/2026 00:00", data_sort=1.0, descricao="COMPRA",
            origem_selo='<span class="selo">UN</span>', origem_texto="Unicred",
            origem_completa="Unicred · CC", categoria="Fuel", categoria_nome="Combustível",
            dims={1: 10}, dims_rotulos={1: "Ronaldo"},
            valor_fmt="- R$ 10.00", valor_sort=-10.0, cor_valor="", observacao="",
            conferida=False, duplicada=False,
        )
        base.update(kw)
        return base

    def render(self, ctx_linhas, **kw):
        base = dict(
            titulo="Lançamentos", topbar="", mes="2026-08", status="todas",
            hoje_iso="2026-08-21", origem_filtro_html="", pode_editar=True,
            pode_conferir=True, pode_manual=True,
            categorias=[{"chave": "Fuel", "nome": "Combustível"}],
            dimensoes=self.DIMS, valores_por_dim=self.VALS, naturezas=core.NATUREZAS,
            linhas=ctx_linhas, por_categoria=[], receita_mes=0.0, gasto_real=0.0,
            resultado_mes=0.0, conf=0, total=0,
            detalhes_json="{}", config_json="{}",
        )
        base.update(kw)
        return render_template("index.html", **base)

    def test_descricao_e_escapada(self, ctx):
        # descricao vem do banco (Pluggy ou lancamento manual digitado)
        html = self.render([self.linha(descricao="<img src=x onerror=alert(1)>")])
        assert "<img src=x" not in html
        assert "&lt;img" in html

    def test_apelido_do_cartao_e_escapado_mas_o_selo_nao(self, ctx):
        # o apelido e digitado pelo usuario em /contas; o selo e HTML do proprio app
        html = self.render([self.linha(origem_texto='"><script>alert(1)</script>')])
        assert "<script>alert(1)</script>" not in html
        assert '<span class="selo">UN</span>' in html

    def test_dimensao_obrigatoria_sem_valor_fica_destacada(self, ctx):
        html = self.render([self.linha(dims={1: None})])
        assert "classificacao-faltando" in html

    def test_dimensao_obrigatoria_preenchida_nao_destaca(self, ctx):
        html = self.render([self.linha(dims={1: 10})])
        assert "#c23c34;background:#fbeceb" not in html

    def test_cards_separam_recebidos_contabilizados_e_conferidos(self, ctx):
        html = self.render(
            [], total_reais=245, total_recebidos=273, conf_reais=185, total_fora=28,
        )
        assert "Receitas no DRE" in html
        assert "Despesas no DRE" in html
        assert "245 / 273" in html
        assert "contabilizados / recebidos" in html
        assert "185 conferidos · 28 fora do resultado" in html

    def test_linha_exibe_todas_as_situacoes_sem_depender_so_da_cor(self, ctx):
        situacoes = [
            {"classe": "conferida", "rotulo": "Conferido"},
            {"classe": "fora", "rotulo": "Fora do resultado"},
        ]
        html = self.render([
            self.linha(situacoes=situacoes, situacoes_texto="Conferido · Fora do resultado")
        ])
        assert "Legenda das linhas" in html
        assert 'class="linha-ponto conferida"' in html
        assert 'class="linha-ponto fora"' in html
        assert 'data-tip="Conferido · Fora do resultado"' in html

    def test_oferece_filtro_de_possiveis_duplicidades(self, ctx):
        html = self.render([self.linha()])
        assert 'value="duplicidade"' in html
        assert "Possíveis duplicidades" in html
        assert "Mostrar apenas suspeitas" not in html

    def test_oferece_filtro_de_lancamentos_ja_marcados_como_duplicados(self, ctx):
        html = self.render([self.linha()], status="duplicada")
        assert 'value="duplicada" selected' in html
        assert ">Duplicados confirmados</option>" in html

    def test_sem_permissao_de_editar_trava_os_campos(self, ctx):
        html = self.render([self.linha()], pode_editar=False, pode_conferir=False)
        assert html.count("disabled") >= 3

    def test_sem_lancamentos_mostra_aviso_com_colspan_certo(self, ctx):
        html = self.render([])
        # 8 colunas fixas + 1 dimensao + a coluna de selecao para quem edita
        assert 'colspan="10"' in html
        assert "Nenhum lançamento neste filtro." in html
        assert 'colspan="9"' in self.render([], pode_editar=False)

    def test_natureza_fluxo_nao_aparece_no_modal(self, ctx):
        # 'fluxo' e o padrao (direcao decide), nao faz sentido escolher na mao
        html = self.render([self.linha()])
        assert 'value="fluxo"' not in html

    def test_modal_ordena_campos_e_oferece_confirmacoes_sensiveis(self, ctx):
        html = self.render([self.linha(conferida=True)])
        ids = [
            'id="modalCategoria"', 'id="modalDimensoes"', 'id="modalObservacao"',
            'id="modalConferidaPor"', 'id="modalConferida"', 'id="modalDup"',
        ]
        posicoes = [html.index(item) for item in ids]
        assert posicoes == sorted(posicoes)
        assert '<option value="nao">Não</option>' in html
        assert 'id="modalConfirmacao"' in html
        assert 'modalConfirmacaoResumo' not in html
        assert "cancelarConfirmacaoModal(true)" in html
        assert "confirmarAcaoModal()" in html

    def test_modal_edita_dimensoes_observacao_e_compacta_campos(self, ctx):
        html = self.render([self.linha(conferida=True)])
        js = (Path(__file__).parent.parent / "static" / "lancamentos.js").read_text(encoding="utf-8")

        assert 'class="dim-select modal-dim-select"' in html
        assert 'onchange="salvarDimensaoModal(this)"' in html
        assert 'onchange="salvarObservacaoModal()"' in html
        assert 'id="modalConferidaPor" hidden' in html
        assert js.count('class="row row-pareada"') == 4
        assert js.index('<small>Data</small>') < js.index('<small>Valor (R$)</small>')
        assert js.index('<small>Valor original</small>') < js.index('<small>Parcela</small>')
        assert js.index('<small>Visto 1ª vez em</small>') < js.index('<small>Última sincronização</small>')
        assert html.index('id="modalConferidaPor"') < html.index('id="modalConferida"')
        assert "function salvarDimensaoModal" in js
        assert "function salvarObservacaoModal" in js
        assert "conferidaPor.hidden = !d._conferida" in js

    def test_opcoes_da_tabela_sao_carregadas_sob_demanda(self, ctx):
        categorias = [
            {"chave": "Fuel", "nome": "Combustível"},
            {"chave": "Groceries", "nome": "Mercado"},
            {"chave": "Travel", "nome": "Viagem"},
        ]
        html = self.render(
            [self.linha(), self.linha(id="tx2")],
            categorias=categorias,
        )
        # Cada linha traz somente a selecao atual. A lista completa existe uma
        # unica vez no modal/configuracao, e o JS a coloca na linha ao clicar.
        tabela = html.split('<table class="compacta', 1)[1].split("</table>", 1)[0]
        assert tabela.count('data-lazy-options="categoria"') == 2
        assert tabela.count('value="Fuel"') == 2
        assert 'value="Groceries"' not in tabela
        assert 'value="Travel"' not in tabela

    def test_sem_categoria_aparece_sem_escolher_opcao_errada(self, ctx):
        html = self.render([
            self.linha(categoria=None, categoria_nome="(sem categoria)")
        ])
        tabela = html.split('<table class="compacta', 1)[1].split("</table>", 1)[0]
        assert '<option value="" selected>(sem categoria)</option>' in tabela


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
        for tela in ("index.html", "lancamentos_fatura.html"):
            html = (raiz / "templates" / tela).read_text(encoding="utf-8")
            assert "/static/lote.js" in html, tela
        for js in ("lancamentos.js", "lancamentos_fatura.js"):
            texto = (raiz / "static" / js).read_text(encoding="utf-8")
            assert "window.pdmLote.aplicar" in texto, js

    def test_lote_nunca_desmarca_ok(self):
        """Retirar assinatura exige confirmacao um a um (secao 1.2)."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        for js in ("lote.js", "lancamentos.js", "lancamentos_fatura.js"):
            texto = (raiz / "static" / js).read_text(encoding="utf-8")
            trecho = texto.split("Edicao em lote", 1)[-1] if js != "lote.js" else texto
            assert "conferida = false" not in trecho, js
            assert "confirmar_desmarcacao" not in trecho, js

    def test_lote_nao_sobrescreve_observacao_sem_intencao(self):
        """A observacao pertence ao usuario (secao 7.3)."""
        nucleo = self.nucleo()
        assert "opcoes.substituirObservacao || !(alvo.observacaoAtual" in nucleo

    def test_coluna_de_selecao_participa_do_layout_da_tabela(self):
        """tabelas.js indexa por data-col: sem ele a coluna some ao reordenar."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        html = (raiz / "templates" / "index.html").read_text(encoding="utf-8")
        assert html.count('data-col="sel"') == 4, "cabecalho, linha, rateio e tecnica"
