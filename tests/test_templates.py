"""Renderiza os templates com o formato REAL dos dados que a view entrega.

Existe por causa de um bug que passou despercebido: a tela /importar desempacotava
3 valores de uma tupla que carregar_origens() devolve com 4 desde o commit fcedcf1,
e ficou dando 500 sem ninguem ver (ela nao estava mais no menu). A tela foi removida
depois, mas a licao fica: testar com dado inventado nao pega esse tipo de erro - o
formato usado aqui tem que espelhar o que a view realmente entrega.
"""
from pathlib import Path
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
            pendente_classificacao=60, classificados_reais=185, pendentes_ok=60,
            pct_classificados=75.5, pct_conferidos=75.5,
        )
        assert "Receitas no DRE" in html
        assert "Despesas no DRE" in html
        assert "245 / 273" in html
        assert "contabilizados / recebidos" in html
        assert "28 fora do resultado" in html
        # Conferidos e classificados sao trabalho pendente, entao ganharam card
        # proprio - com o numero, o quanto falta e o clique que leva ate as
        # linhas. Antes viviam como uma linha de texto que nao levava a lugar
        # nenhum.
        assert "185 / 245" in html
        assert "Faltam 60" in html

    def test_cada_card_que_parece_clicavel_leva_a_um_filtro_real(self, ctx):
        """Card com `data-filtro` vira porta de entrada para as proprias linhas.

        O atributo e tambem o gatilho do CSS (`.card[data-filtro]`), entao nao
        existe card com cara de clicavel e sem filtro por tras - nem o contrario.
        E todo valor que ele aponta precisa ser um status que a rota aceita,
        senao o clique cai no default `todas` em silencio e a tela mente sobre o
        recorte que esta mostrando.
        """
        import re

        html = self.render([])
        filtros = set(re.findall(r'data-filtro="([^"]+)"', html))
        assert filtros, "os cards deixaram de oferecer filtro"
        opcoes = set(re.findall(r'<option value="([^"]+)"', html))
        assert filtros <= opcoes, (
            "card aponta para status que o filtro nao oferece: "
            + str(sorted(filtros - opcoes))
        )
        # "Resultado no DRE" nao filtra de proposito: ele e a conta entre os
        # dois cards ao lado, nao um recorte de lancamentos.
        bloco = html.split('class="cards"', 1)[1].split("</div>\n</div>", 1)[0]
        resultado = bloco.split("Resultado no DRE", 1)[0].rsplit("<div class=", 1)[1]
        assert "data-filtro" not in resultado

    def test_linha_exibe_todas_as_situacoes_sem_depender_so_da_cor(self, ctx):
        situacoes = [
            {"classe": "conferida", "rotulo": "Conferido"},
            {"classe": "fora", "rotulo": "Fora do resultado"},
        ]
        html = self.render([
            self.linha(situacoes=situacoes, situacoes_texto="Conferido · Fora do resultado")
        ])
        # Os pontos no inicio da linha sairam a pedido do usuario (09/09/2026).
        # A situacao continua nao dependendo so da cor: ela esta por extenso no
        # tooltip da data e nos atalhos de filtro do rodape (secao 7.6).
        assert "linha-ponto" not in html
        assert "Filtrar por situação" in html
        assert 'data-tip="Conferido · Fora do resultado"' in html

    def test_oferece_filtro_de_possiveis_duplicidades(self, ctx):
        html = self.render([self.linha()])
        assert 'value="duplicidade"' in html
        assert "Possíveis duplicidades" in html
        assert "Mostrar apenas suspeitas" not in html

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
            'id="modalConferidaPor"', 'id="modalConferida"',
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
        # 5 e nao 4: a ultima linha tem duas versoes, porque lancamento manual
        # nunca sincroniza e mostra "Criado em / Ultima alteracao" no lugar
        assert js.count('class="row row-pareada"') == 5
        assert "<small>Última alteração</small>" in js
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
        for tela in ("index.html", "lancamentos_fatura.html"):
            html = (raiz / "templates" / tela).read_text(encoding="utf-8")
            assert 'class="barra-lote-fechar"' in html, tela
        for js in ("lancamentos.js", "lancamentos_fatura.js"):
            texto = (raiz / "static" / js).read_text(encoding="utf-8")
            assert "window.pdmLote.ligarFechar" in texto, js
            trecho = texto.split("Edicao em lote", 1)[-1]
            assert "Escape" not in trecho, js + ": Escape do lote mora no nucleo"

    def test_salvar_limpa_a_selecao_mas_nao_quando_houve_recusa(self):
        """Com recusa, a selecao e a lista de quem precisa de nova tentativa."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        for js in ("lancamentos.js", "lancamentos_fatura.js"):
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
        html = (raiz / "templates" / "index.html").read_text(encoding="utf-8")
        assert html.count('data-col="sel"') == 4, "cabecalho, linha, rateio e tecnica"


def test_descricao_manual_salva_sozinha_e_so_no_modal_do_manual():
    """Sem botao Salvar: espera curta e ao sair do campo, como a observacao."""
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    js = (raiz / "static" / "lancamentos.js").read_text(encoding="utf-8")
    html = (raiz / "templates" / "index.html").read_text(encoding="utf-8")

    assert "modalSalvarDescricao" not in js and "modalSalvarDescricao" not in html
    assert "d._manual" in js.split("Descrição", 1)[0][-400:] or "d._manual\n" in js
    # o campo so e montado para lancamento manual
    trecho = js.split("'<div class=\"row\"><span>Descrição</span>", 1)[0][-300:]
    assert "d._manual" in trecho
    # salvamento automatico nos dois gatilhos
    assert "setTimeout(() => salvarDescricaoModal(campo), 700)" in js
    assert "focusout" in js.split("salvarDescricaoModal", 1)[1]
    # o texto nunca vai por atributo dentro de innerHTML
    assert "campoDesc.value = d.descricao" in js


def test_modal_sincroniza_o_combobox_ao_espelhar_a_categoria():
    """Atribuir .value nao redesenha o combobox.

    O widget so se atualiza em mudanca de filho (MutationObserver) ou evento
    `change`; `selCat.value = ...` nao dispara nenhum dos dois, entao o texto
    visivel ficava em "(sem categoria)" com o select real ja correto. As
    dimensoes escapavam por usarem replaceChildren.
    """
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    js = (raiz / "static" / "lancamentos.js").read_text(encoding="utf-8")
    trecho = js.split("espelha a categoria da linha", 1)[1].split("const emRateio", 1)[0]
    assert "pdmCombobox.sincronizar(selCat)" in trecho


def test_bloqueio_de_ok_por_falta_de_pdf_tem_mensagem_propria():
    """Secao 7.5: OK de cartao de credito exige vinculo com uma linha do PDF.

    Sem tratar `sem_pdf_conciliado`, as duas telas caiam no texto generico e
    mandavam "preencha os campos obrigatorios" mesmo com `faltando` VAZIO - o
    usuario procurava um campo que nao existia enquanto o motivo era outro.
    """
    for arquivo in ("lancamentos.js", "lancamentos_fatura.js"):
        caminho = Path(__file__).resolve().parents[1] / "static" / arquivo
        js = caminho.read_text(encoding="utf-8")
        assert "sem_pdf_conciliado" in js, f"{arquivo} ignora a trava do PDF"
        assert "Conciliar fatura" in js, (
            f"{arquivo} precisa dizer ONDE resolver, nao so que esta bloqueado"
        )


def test_setas_andam_por_fatura_so_com_um_cartao_que_tenha_fatura():
    """Com varias origens nao existe 'a fatura'; sem PDF/OFX o ciclo seria
    palpite. Nos dois casos as setas continuam andando por mes."""
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    view = (raiz / "views" / "lancamentos.py").read_text(encoding="utf-8")
    js = (raiz / "static" / "lancamentos.js").read_text(encoding="utf-8")

    bloco = view.split("Ciclos de fatura da origem selecionada", 1)[1].split("config_lancamentos", 1)[0]
    assert 'len(origem_sel) == 1' in bloco
    assert '"tipo") == "CREDIT"' in bloco
    assert "periodo_fim IS NOT NULL" in bloco
    # a coluna que decide quem manda no ciclo tem que vir junto (secao 11.3-A)
    assert "ciclo_do_arquivo" in bloco

    # sem ciclos, o comportamento por mes segue intacto
    assert "if (ciclos.length && !document.getElementById('periodoAno').checked)" in js


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
    assert "fatura.value.startsWith('futuro-')" in js


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
    """As partes de um rateio sao montadas pelos QUATRO construtores de linha -
    Resumida, recorte por periodo, fatura oficial e fatura em andamento, que
    passaram a ratear em 10/09/2026. A consulta estava escrita duas vezes,
    palavra por palavra, e uma copia nova divergiria na primeira regra nova."""
    import pathlib
    fonte = (pathlib.Path(__file__).resolve().parent.parent
             / "views" / "lancamentos.py").read_text(encoding="utf-8")
    # `_estado_rateios` fica de fora de proposito: ele le UMA transacao para a
    # auditoria antes/depois da API, com cursor de tupla - outro proposito.
    assert fonte.count('"WHERE r.transacao_id IN %s ORDER BY r.transacao_id') == 1, (
        "a consulta das partes das telas tem que sair de partes_do_rateio()")
    assert fonte.count("def rateio_da_linha(") == 1
    # a definicao mais uma chamada em cada um dos quatro construtores
    assert fonte.count("rateio_da_linha(") == 5
    assert fonte.count("partes_do_rateio(") == 5


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
        # os quatro construtores de linha do sistema
        assert view.count("situacoes_da_linha(") == 5

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
        for tela in ("index.html", "lancamentos_fatura.html"):
            html = (raiz / "templates" / tela).read_text(encoding="utf-8")
            assert "/static/rateio.js" in html, tela
        for js in ("lancamentos.js", "lancamentos_fatura.js"):
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
        js = (raiz / "static" / "lancamentos.js").read_text(encoding="utf-8")
        assert "function montarQuadro(box" in nucleo
        assert "rateio-parte-titulo" in nucleo
        assert "rateio-parte-titulo" not in js, "segunda copia do quadro"
        assert "window.pdmRateio.montarQuadro" in js
        assert "window.pdmRateio.montarQuadro" in (
            raiz / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")

    def test_o_valor_da_pendencia_sai_em_portugues(self):
        """toFixed(2) escreve 699.82, que e ingles - a mesma licao do valor_pt()
        no Python (secao 7.8-A)."""
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent
        tabelas = (raiz / "static" / "tabelas.js").read_text(encoding="utf-8")
        assert "window.pdmMoedaBr = function" in tabelas and "'pt-BR'" in tabelas
        for arquivo in ("rateio.js", "lancamentos.js", "lancamentos_fatura.js"):
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
        for tela, funcao in (("lancamentos.js", "function aplicarFiltros("),
                             ("lancamentos_fatura.js", "window.aplicarFiltrosPeriodo = function")):
            texto = self.js(tela)
            assert "pdmTrocarPorAjax(" in texto, tela
            # o filtro nao monta o proprio DOMParser: a troca de blocos e uma so.
            # (o resumo parcial da Detalhada tem um, e faz outra coisa: atualiza
            # linha a linha sem destruir o que o usuario esta preenchendo)
            trecho = texto.split(funcao, 1)[1].split("\n  };", 1)[0]
            assert "DOMParser" not in trecho, tela + ": segunda copia da troca"

    def test_a_detalhada_nao_recarrega_ao_filtrar(self):
        js = self.js("lancamentos_fatura.js")
        trecho = js.split("window.aplicarFiltrosPeriodo = function", 1)[1].split("};", 1)[0]
        assert "location.assign" not in trecho
        assert "pdmTrocarPorAjax" in trecho

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
