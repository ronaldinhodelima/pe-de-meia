"""Checagens estruturais do projeto, feitas em cima do AST.

Existem porque py_compile e os testes de template nao pegam esta classe de erro:
um nome usado mas nao importado so estoura em tempo de execucao, na hora em que
aquela linha roda - que pode ser meses depois, na producao. Aconteceu nesta
sessao duas vezes (um helper apagado junto com um corte, e um import removido
achando que estava sem uso).
"""
import ast
import builtins
import pathlib

RAIZ = pathlib.Path(__file__).resolve().parent.parent
MODULOS = [RAIZ / "app.py", RAIZ / "core.py", *sorted((RAIZ / "views").glob("*.py"))]

# dunders que o Python injeta no modulo e nao aparecem como atribuicao
INJETADOS = {"__file__", "__name__", "__doc__", "__package__"}


def test_os_dois_servicos_usam_servidor_de_producao():
    principal = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    sync = (RAIZ / "bussola" / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["gunicorn"' in principal
    assert 'CMD ["gunicorn"' in sync
    assert 'CMD ["python","app.py"]' not in sync


def test_containers_rodam_sem_usuario_root_e_dependencias_estao_fixadas():
    for pasta in (RAIZ, RAIZ / "bussola"):
        docker = (pasta / "Dockerfile").read_text(encoding="utf-8")
        requisitos = (pasta / "requirements.txt").read_text(encoding="utf-8").splitlines()
        assert "USER 10001:10001" in docker
        assert "-r /app/requirements.txt" in docker
        assert requisitos
        assert all("==" in linha for linha in requisitos if linha.strip() and not linha.startswith("#"))


def test_lancamento_conferido_usa_destaque_cinza_claro():
    css = (RAIZ / "static" / "app.css").read_text(encoding="utf-8")
    assert "tr.conferida { background: var(--raise); }" in css
    assert "tr.conferida:hover { background: var(--line); }" in css
    assert "tr.conferida { background: var(--good-soft); }" not in css


def nomes_definidos(arvore):
    achados = set(dir(builtins)) | INJETADOS
    for n in ast.walk(arvore):
        if isinstance(n, ast.Import):
            for a in n.names:
                achados.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                achados.add(a.asname or a.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            achados.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            achados.add(n.id)
        elif isinstance(n, ast.arg):
            achados.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            achados.add(n.name)
        elif isinstance(n, ast.Global):
            achados.update(n.names)
    return achados


def test_nenhum_nome_usado_sem_estar_definido_ou_importado():
    problemas = {}
    for caminho in MODULOS:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        definidos = nomes_definidos(arvore)
        faltando = sorted({
            n.id for n in ast.walk(arvore)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in definidos
        })
        if faltando:
            problemas[caminho.name] = faltando
    assert not problemas, f"nome sem import/definicao: {problemas}"


def test_core_nao_importa_das_views_nem_do_app():
    """A dependencia so pode correr app.py -> views/ -> core.py."""
    arvore = ast.parse((RAIZ / "core.py").read_text(encoding="utf-8"))
    for n in ast.walk(arvore):
        modulo = None
        if isinstance(n, ast.ImportFrom):
            modulo = n.module or ""
        elif isinstance(n, ast.Import):
            modulo = n.names[0].name
        if modulo and (modulo.startswith("views") or modulo == "app"):
            raise AssertionError(f"core.py importa {modulo} - volta o import circular")


def test_migracao_cria_dimensao_antes_de_tentar_renomear():
    """Um PostgreSQL vazio nao possui cartao.dimensao antes da primeira migracao."""
    texto = (RAIZ / "core.py").read_text(encoding="utf-8")
    criar = texto.index('CREATE TABLE IF NOT EXISTS cartao.dimensao (')
    renomear = texto.index("UPDATE cartao.dimensao SET nome = 'Projeto'")
    assert criar < renomear


def test_esquema_base_contem_campos_de_revisao_do_lancamento():
    schema = (RAIZ / "bussola" / "app.py").read_text(encoding="utf-8").split("SCHEMA_SQL =", 1)[1]
    for campo in ("conferida", "observacao", "conferida_por", "conferida_em"):
        assert campo in schema.split('"""', 2)[1]


def test_views_nao_importam_umas_das_outras():
    for caminho in (RAIZ / "views").glob("*.py"):
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for n in ast.walk(arvore):
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("views"):
                raise AssertionError(f"{caminho.name} importa de outra view: {n.module}")


def test_nenhuma_tela_monta_html_por_fstring():
    """Todo HTML tem que morar em templates/. Se voltar f-string, e regressao."""
    culpados = []
    for caminho in MODULOS:
        texto = caminho.read_text(encoding="utf-8")
        if "<html>" in texto or "<body>" in texto:
            culpados.append(caminho.name)
    assert not culpados, f"HTML em Python: {culpados}"


def test_todas_as_rotas_continuam_registradas():
    import app

    rotas = {str(r) for r in app.app.url_map.iter_rules() if r.endpoint != "static"}
    esperadas = {
        "/", "/login", "/logout", "/health", "/favicon.ico",
        "/api/sync-status", "/api/sync-agora", "/api/transacao/<transacao_id>",
        "/api/transacao/<transacao_id>/rateios",
        "/api/lancamento-manual", "/api/lancamento-manual/<transacao_id>",
        "/api/categoria-lancamentos", "/api/dimensao-lancamentos",
        "/api/classificacao/consenso-preview",
        "/api/regras/preview", "/api/dimensao/<int:dimensao_id>/valor",
        "/relatorios", "/relatorios/dados", "/relatorios/lancamentos",
        "/relatorios/conciliar-fatura", "/lancamentos/fatura",
        "/api/fatura-linha/<int:linha_id>/criar-lancamento",
        "/api/fatura-linha/marcar-conferida-repeticao", "/relatorios/fatura/<int:fatura_id>/pdf",
        "/api/fatura/<int:fatura_id>/vincular-automatico",
        "/api/faturas/sincronizar-parcelas",
        "/api/faturas/criar-cobrancas-sem-pluggy",
        "/relatorios/duplicidades-fatura",
        "/api/duplicidades/marcar",
        "/api/fatura-linha/<int:linha_id>/vincular",
        "/api/fatura-linha/<int:linha_id>/desvincular",
        "/dre", "/investimentos",
        "/categorias", "/grupos", "/dimensoes", "/regras", "/contas", "/pendencias",
        "/configuracoes/faturas-pdf",
        "/usuarios", "/logs",
    }
    assert rotas == esperadas


def test_visao_por_fatura_reutiliza_ok_do_lancamento_e_mantem_agregados():
    """As duas telas assinam o mesmo lancamento, sem esconder a auditoria."""
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    template = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    js = (RAIZ / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    assert 'linha["conferida"] = bool(principal["conferida"])' in view
    assert 'data-expande="{{ linha.id }}"' in template
    assert "Lançamentos agregados a esta linha" not in template
    assert "contabilizado e editável" in template
    assert "registro técnico · somente leitura" in template
    assert "data-ok-lancamento" in template
    assert "data-toggle-linha" in template
    assert "/api/transacao/" in js
    assert "dataset.okLancamento" in js


def test_visualizacoes_resumida_e_detalhada_sao_escolha_explicita():
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    js = (RAIZ / "static" / "lancamentos.js").read_text(encoding="utf-8")
    resumo = (RAIZ / "templates" / "index.html").read_text(encoding="utf-8")
    detalhe = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    assert '"origens_credito"' in view
    assert "/lancamentos/fatura?account_id=" in js
    assert "abrirVisualizacaoDetalhada" in js
    assert "abrirVisaoDetalhada" in resumo
    assert ">Resumida<" in resumo and ">Detalhada<" in resumo
    assert ">Resumida<" in detalhe and ">Detalhada<" in detalhe
    trecho = view.split('def lancamentos_por_fatura', 1)[1].split('@bp.route("/api/fatura-linha', 1)[0]
    assert "ORDER BY f.ano_referencia DESC, f.mes_referencia DESC" in trecho
    assert "LIMIT 1" in trecho


def test_cards_da_fatura_explicam_valores_e_filtram_divergencias():
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    template = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    for rotulo in (
        "Total oficial", "Conciliação", "Despesas no DRE", "Fora do DRE",
        "Classificação", "OK dos lançamentos", "Divergências", "Com agregados",
    ):
        assert rotulo in template
    assert 'data-filtro="requer_validacao"' in template
    assert 'value="requer_validacao"' in template
    assert 'status == "requer_validacao" and l["requer_validacao"]' in view
    assert 'linha["ambigua"]' in view
    assert 'linha["diferenca_valor"]' in view
    assert 'total_pendente_ok' in view


def test_compra_agregada_nao_vira_falsa_divergencia_de_valor():
    from views.lancamentos import (
        _candidatos_fatura_equivalentes,
        _diferenca_valor_linha_fatura,
    )

    assert _diferenca_valor_linha_fatura("360.00", 6, "2160.00") == 0
    assert _diferenca_valor_linha_fatura("1416.70", 6, "8500.00") == 0
    assert _diferenca_valor_linha_fatura("91.15", 3, "273.41") == 0
    assert _diferenca_valor_linha_fatura("100.00", None, "102.00") == 2
    ecos = [
        {"data_local": "10/07/2026 17:00", "valor": "70.00", "numero_cartao_final": "1234"},
        {"data_local": "10/07/2026 17:00", "valor": "70.00", "numero_cartao_final": "1234"},
    ]
    assert _candidatos_fatura_equivalentes(ecos)
    assert not _candidatos_fatura_equivalentes([
        ecos[0], {**ecos[1], "valor": "71.00"},
    ])


def test_observacao_pessoal_fica_separada_das_mensagens_do_sistema():
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    relatorios = (RAIZ / "views" / "relatorios.py").read_text(encoding="utf-8")
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    resumo = (RAIZ / "templates" / "index.html").read_text(encoding="utf-8")
    detalhada = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    js = (RAIZ / "static" / "lancamentos.js").read_text(encoding="utf-8")

    assert "if versao_atual < 32:" in core
    assert "if versao_atual < 33:" in core
    assert "observacao_sistema=observacao" in core
    assert "observacao=NULL" in core
    assert "observacao_sistema" in relatorios
    assert "t.observacao_sistema" in view
    assert "modalInfoSistema" in resumo
    assert "Informação interna do sistema" in detalhada
    assert "obsInput.value = DUPLICADA_OBS_PADRAO" not in js
    assert "payload.observacao = tr.querySelector('.obs-input').value" not in js
    assert "Reconstrucao da procedencia interna das faturas" in core


def test_detalhada_salva_sozinha_e_reutiliza_regras_da_resumida():
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    template = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    js = (RAIZ / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    trecho = view.split("def lancamentos_por_fatura", 1)[1].split('@bp.route("/api/lancamento-manual"', 1)[0]

    assert "aplicar_regras(cur)" in trecho
    assert '"projeto_portfolio_map": projeto_portfolio_map' in trecho
    assert '"dim_id_projeto"' in trecho and '"dim_id_portfolio"' in trecho
    assert "data-salvar" not in template
    assert "Salvar</button>" not in template
    assert "Salvo automaticamente" in js
    assert "setTimeout(() => salvarEditor(editor, campo), 650)" in js
    assert "config.projeto_portfolio_map" in js
    assert "/regras?transacao=" in template
    assert "+ Cadastrar novo..." in template
    assert "window.location.reload()" not in js


def test_detalhada_exibe_fontes_e_informacoes_tecnicas_com_cabecalho_compacto():
    template = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    js = (RAIZ / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    cabecalho = template.split('<div class="fatura-cabecalho">', 1)[1].split('</div>', 1)[0]
    assert "Fatura {{ meses[fatura.mes_referencia-1] }} de {{ fatura.ano_referencia }}" in cabecalho
    assert "Ciclo {{ fatura.periodo_inicio.strftime" in cabecalho
    assert "vence {{ fatura.vencimento.strftime" in cabecalho
    assert 'class="fatura-ciclo"' in cabecalho
    assert 'data-tip="{{ v.fonte_nome }}"' in template
    assert "Mais informações da transação" not in template
    assert "data-info-target" in template and "transacao-info" in template
    assert 'lancamentos_fatura.js?v=' in template
    trecho_detalhe = template.split('<div class="vinculo-bloco">', 1)[1].split('</td></tr>', 1)[0]
    assert trecho_detalhe.index('class="transacao-info"') < trecho_detalhe.index('{% endfor %}')
    assert trecho_detalhe.index('{% endfor %}') < trecho_detalhe.index('class="editor-financeiro"')
    assert "detalhe-id" in template and "overflow-wrap:anywhere" in template
    assert "fonte-badge:hover::after" not in template
    assert 'data-tip="{{ v.fonte_nome }}"' in template
    assert "atualizarAvisoClassificacao(editor)" in js
    assert "destino.replaceChildren(aviso)" in js
    assert "await (filaSalvar[campo.dataset.okLancamento]" in js
    assert "cursor:default" in template
    assert 'v["fonte"] = "F"' in view
    assert 'v["fonte_nome"]' in view
    assert "atualizarResumoPagina(novo && status && status.value === 'pendente_ok')" in js
    assert "if (ocultarAusentes)" in js
    assert "linha.remove()" in js
    assert 'data-ordenar="data"' in template
    assert 'data-ordenar="descricao"' in template
    assert 'data-ordenar="titular"' in template
    assert 'data-ordenar="parcela"' in template
    assert 'data-ordenar="valor"' in template
    assert 'data-ordenar="classificacao"' in template
    assert 'data-ordenar="ok"' in template
    assert "function ordenarFatura(cabecalho)" in js
    assert "corpo.appendChild(item.detalhe)" in js


def test_padronizacao_aprovada_fica_restrita_aos_ciclos_unicred_revisados():
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    trecho = core.split("if versao_atual < 35:", 1)[1].split("if versao_atual < 36:", 1)[0]
    assert "fi.ano_referencia=2026" in trecho
    assert "fi.mes_referencia IN (7,8)" in trecho
    assert "t.conferida" not in trecho
    assert "APPLE.COM" not in trecho
    assert "MERCADOLIVRE" not in trecho
    assert "Iron Maiden 2026" in trecho
    assert "Serviços Financeiros" in trecho
    assert "Reformas da casa" in trecho
    assert "ACOUGUE CARNE FRESCA" in trecho
    assert "SUPERVIZA" in trecho
    assert "SUPERMERCADO VIDE" in trecho
    complemento = core.split("if versao_atual < 36:", 1)[1].split("if versao_atual < 37:", 1)[0]
    assert "cartao.fatura_vinculo" in complemento
    assert "fi.mes_referencia IN (7,8)" in complemento
    assert "t.conferida" not in complemento
    assert "LIKE 'LISCIA%%'" in complemento
    historico = core.split("if versao_atual < 37:", 1)[1].split("if versao_atual < 38:", 1)[0]
    assert "preencher_classificacao_vazia_parcelas(" in historico
    assert "account_id=conta_unicred" in historico
    assert "aplicar_regras(cur, account_id=conta_unicred)" in historico
    assert "t.conferida" not in historico
    assert 'escopo_sql = " AND t.account_id=%s "' in core
    tarifas = core.split("if versao_atual < 38:", 1)[1].split("if versao_atual < 39:", 1)[0]
    assert "account_id uuid" in tarifas
    assert "estorno_origem_id uuid" in tarifas
    assert "Credit card fees" in tarifas
    assert "Serviços Financeiros" in tarifas
    assert "t.conferida" not in tarifas
    consenso = core.split("if versao_atual < 39:", 1)[1].split("if versao_atual < 40:", 1)[0]
    assert "fi.mes_referencia<=5" in consenso
    assert "d.obrigatoria" in consenso
    assert "account_id=%s" in consenso
    assert "t.conferida" not in consenso
    assert "SET observacao" not in consenso
    assert "AUTO POSTO" not in consenso
    assert "MP*PRODUTOS" in consenso
    assert "FARM GEREMIAS - CENTRO" in consenso


def test_parcelamento_total_com_uma_fatura_ja_vira_registro_tecnico():
    view = (RAIZ / "views" / "relatorios.py").read_text(encoding="utf-8")
    trecho = view.split("def _sincronizar_parcelas_de_agregado", 1)[1].split(
        '@bp.route("/relatorios/duplicidades-fatura")', 1
    )[0]
    assert "fl.parcela_total >= 2" in trecho
    assert "fl.valor * fl.parcela_total" in trecho
    assert "<= 1.00" in trecho
    assert "transferir_trabalho" in trecho
    assert 'origem["observacao"] if origem else None' in trecho
    assert "resumo_parcelas = _sincronizar_parcelas_de_agregado" in view
    assert 'escopo_sql = " AND t.account_id=%s "' in trecho
    assert "account_id=account_id" in view
    assert 'request.form.get("retorno")' in view
    template = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    assert "Revisar parcelamentos" in template
    assert template.index('class="tabela-scroll"') < template.index('class="rodape-fatura"')
    filtros = template.split('<div class="fatura-filtros">', 1)[1].split('</div>\n\n<div class="cards', 1)[0]
    assert 'class="visao-lancamentos"' in filtros
    cabecalho = template.split('<div class="fatura-cabecalho">', 1)[1].split('</div>', 1)[0]
    assert "Ciclo" in cabecalho
    assert "PDF oficial</span>" not in cabecalho
    assert 'id="buscaFatura"' in template
    js = (RAIZ / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    assert "textoFiltravelDoGrupo" in js
    assert "visiveis + ' de ' + linhas.length" in js
    assert "detalhe.style.display = exibir ? '' : 'none'" in js
    assert "carregarPreviaParcelamentos" in js
    assert "Isso pode alterar os totais mensais" in js
    assert "request.method == \"GET\"" in view
    assert "account_id=str(fatura[\"account_id\"])" in view


def test_categoria_tambem_e_obrigatoria_para_novo_ok():
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    trecho = view.split("def update_transacao", 1)[1]
    assert 'faltando.append("categoria")' in trecho
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    assert "if versao_atual < 34:" in core
    assert "Classificacao completa obrigatoria" in core
    assert "'responsável','responsavel','projeto','portfólio','portfolio'" in core


def test_pendente_conciliado_ao_pdf_pode_receber_ok():
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    trecho = view.split("def update_transacao", 1)[1]
    assert "O PDF oficial encerra a incerteza" in trecho
    assert "SELECT EXISTS (SELECT 1 FROM cartao.fatura_vinculo" in trecho
    assert '"SELECT d.nome FROM cartao.dimensao d "' in trecho


def test_cadastro_rapido_mantem_listas_alfabeticas():
    resumo = (RAIZ / "static" / "lancamentos.js").read_text(encoding="utf-8")
    detalhada = (RAIZ / "static" / "lancamentos_fatura.js").read_text(encoding="utf-8")
    assert "lista.sort((a, b)" in resumo and "localeCompare" in resumo
    assert "json.nome.localeCompare" in detalhada


def test_tojson_nunca_dentro_de_atributo_html():
    """|tojson e o escape certo para dentro de <script>, e errado dentro de atributo.

    O filtro do Flask nao escapa aspas duplas, entao {{ x|tojson }} num atributo
    delimitado por aspas duplas FECHA o atributo antes da hora:

        onclick="f(event, {{ id|tojson }})"  ->  onclick="f(event, "abc")"

    O navegador le onclick como 'f(event, ' - erro de sintaxe, o handler nunca
    roda. Foi assim que a tela de Lancamentos parou de abrir os detalhes e de
    salvar as edicoes, sem erro nenhum aparecer. Para passar dado ao JS: use
    data-attribute + delegacao, ou um bloco <script type="application/json">.
    """
    import re

    padrao = re.compile(r'=\s*"[^"\n]*\{\{[^}]*\|\s*tojson')
    culpados = []
    for caminho in sorted((RAIZ / "templates").glob("*.html")):
        for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
            if padrao.search(linha):
                culpados.append(f"{caminho.name}:{numero}")
    assert not culpados, f"|tojson dentro de atributo HTML: {culpados}"


def test_nenhum_handler_inline_recebe_id_interpolado():
    """Handlers inline com dado interpolado sao a origem do bug acima.

    Os eventos da tabela de Lancamentos passaram a ser tratados por delegacao,
    lendo o id do data-id da linha. Isto trava a volta do padrao antigo.
    """
    import re

    html = (RAIZ / "templates" / "index.html").read_text(encoding="utf-8")
    suspeitos = re.findall(r'on\w+="[^"]*\{\{[^}]*\br\.id\b[^}]*\}\}[^"]*"', html)
    assert not suspeitos, f"handler inline com o id da linha: {suspeitos}"


def test_posicao_da_pagina_e_mantida_em_todas_as_telas():
    """Salvar reenvia o form e a view devolve a pagina inteira, entao o navegador
    voltaria ao topo a cada alteracao.

    A ativacao e automatica no tabelas.js (carregado por todas as telas via
    base.html) em vez de uma chamada por template - assim tela nova ja nasce com
    o comportamento certo, sem depender de alguem lembrar.
    """
    tabelas = (RAIZ / "static" / "tabelas.js").read_text(encoding="utf-8")
    assert "function manterPosicaoAoSalvar" in tabelas
    assert "addEventListener('DOMContentLoaded', manterPosicaoAoSalvar)" in tabelas
    assert '<script src="/static/tabelas.js">' in (RAIZ / "templates" / "base.html").read_text(encoding="utf-8")


def test_filtros_criam_historico_e_botao_voltar_restaura_estado():
    for arquivo in ("lancamentos.js", "relatorios.js"):
        js = (RAIZ / "static" / arquivo).read_text(encoding="utf-8")
        assert "history.pushState" in js
        assert "history.replaceState" not in js
        assert "addEventListener('popstate'" in js


def test_todo_reload_por_js_guarda_a_posicao_antes():
    """window.location.reload() nao dispara submit, entao a posicao nao seria
    guardada sozinha - cada reload por codigo precisa chamar guardarPosicaoAtual()
    antes."""
    import re

    problemas = []
    for caminho in sorted((RAIZ / "static").glob("*.js")):
        if caminho.name == "chart.umd.min.js":
            continue
        # comentario que apenas menciona reload nao conta
        texto = "\n".join(
            "" if l.lstrip().startswith("//") else l
            for l in caminho.read_text(encoding="utf-8").splitlines()
        )
        for m in re.finditer(r"location\.reload\(\)", texto):
            antes = texto[max(0, m.start() - 220):m.start()]
            if "guardarPosicaoAtual()" not in antes:
                linha = texto[:m.start()].count("\n") + 1
                problemas.append(f"{caminho.name}:{linha}")
    assert not problemas, f"reload sem guardar a posicao antes: {problemas}"


def test_selo_do_banco_nao_e_escapado_no_filtro_de_origem():
    """O filtro de Origem mostra o selo colorido do banco antes do nome da conta.

    O selo e HTML montado por selo_banco_html(). Quando a varredura de XSS passou
    a escapar o texto da opcao, o selo vinha concatenado nesse texto e o usuario
    passou a ver a marcacao crua ('<SPAN CLASS="SELO"...') dentro do dropdown.
    Por isso o selo viaja num campo separado da tupla de opcoes.
    """
    import app  # noqa: F401
    import core

    with app.app.test_request_context("/"):
        html = core.chip_filter_html(
            "origem", "Origem",
            [("a1", 'Conta <b>X</b>', "titulo", "curto", '<span class="selo">Nu</span>')],
            [],
        )
    assert '<span class="selo">Nu</span>' in html, "o selo tem que renderizar como HTML"
    assert "&lt;span" not in html, "o selo nao pode aparecer escapado"
    assert "&lt;b&gt;" in html, "o nome da conta continua escapado"


def test_filtro_de_tabela_existe_e_e_automatico():
    """O campo de filtro e injetado pelo tabelas.js junto com a barra de colunas,
    entao vale para toda tabela marcada como ajustavel - inclusive as que vierem
    depois, sem precisar mexer no template."""
    js = (RAIZ / "static" / "tabelas.js").read_text(encoding="utf-8")
    assert "function ativarFiltroTabela" in js
    assert "ativarFiltroTabela(table, busca, contador)" in js
    assert "placeholder = 'Filtrar'" in js

    # o texto da linha nao pode sair do textContent puro: as celulas trazem
    # <select> cujas opcoes listam todas as categorias, e aí qualquer busca
    # casaria com todas as linhas
    assert "clone.querySelectorAll('select').forEach" in js


def test_rateio_pode_ser_editado_nas_linhas_e_ok_depende_do_fechamento():
    template = (RAIZ / "templates" / "index.html").read_text(encoding="utf-8")
    js = (RAIZ / "static" / "lancamentos.js").read_text(encoding="utf-8")

    for classe in (
        "rateio-valor-inline", "rateio-cat-select", "rateio-dim-select",
        "rateio-obs-inline", "rateio-salvar-inline",
    ):
        assert classe in template
    assert "function lerRateioInline(id)" in js
    assert "function validarRateioInline(id)" in js
    assert "body: JSON.stringify({partes: lerRateioInline(id)})" in js
    assert "conf.disabled = !window.configLancamentos.pode_conferir || !estado.valido" in js
    assert "data-rateio-total" in template
    assert "{{ r.descricao }} — Parte {{ loop.index }}" in template
    assert template.index('id="modalDup"') < template.index('id="modalRateioBox"')
    assert 'class="rateio-salvar-inline"' in template and '>✓</button>' in template
    assert "rateio-parte-titulo" in js
    assert "el.textContent = fecha ? ''" in js
    assert "linha.classList.toggle('rateio-invalido', !estado.valido)" in js


def test_registro_substituido_fica_agrupado_sem_heuristica_e_acompanha_ordenacao():
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    template = (RAIZ / "templates" / "index.html").read_text(encoding="utf-8")
    js = (RAIZ / "static" / "tabelas.js").read_text(encoding="utf-8")

    assert "alvo = linhas_por_id.get(alvo_id)" in view
    assert 'len(alvos) == 1' in view
    assert 'linha["substituido_por"] or linha["principal_conciliacao"]' in view
    assert 'data-tecnico-parent="{{ r.id }}"' in template
    assert 'class="tecnico-toggle"' in template
    assert "filha.dataset.tecnicoParent === id" in js


def test_classificacao_de_parcela_so_preenche_vazios_e_exige_consenso():
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    trecho = core.split("def preencher_classificacao_vazia_parcelas", 1)[1].split("def migrate", 1)[0]

    assert "somente_conciliacao" in trecho
    assert "HAVING COUNT(DISTINCT t.categoria)=1" in trecho
    assert "destino.categoria IS NULL" in trecho
    assert "HAVING COUNT(DISTINCT td.valor_id)=1" in trecho
    assert "ON CONFLICT (transacao_id,dimensao_id) DO NOTHING" in trecho
    assert "HAVING COUNT(DISTINCT t.observacao)=1" in trecho
    assert "NULLIF(BTRIM(destino.observacao),'') IS NULL" in trecho


def test_edicao_compartilha_classificacao_so_por_familia_explicita_de_parcelas():
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    trecho = core.split("def propagar_classificacao_familia_parcelas", 1)[1].split(
        "def importar_legado_para_parcelas_fatura", 1
    )[0]

    assert "fatura_vinculo" in trecho
    assert "somente_conciliacao" in trecho
    assert "transacao_id_criado" in trecho
    assert "t.descricao" not in trecho and "fl.descricao" not in trecho
    assert "data_transacao" not in trecho
    assert "SET conferida" not in trecho
    assert "observacao_enviada" in trecho and "SET observacao=%s" in trecho
    assert "propagar_classificacao_familia_parcelas(" in view
    assert "dimensoes_familia" in view
    assert "categoria_familia" in view and "observacao_familia" in view
    assert 'categoria_enviada=bool(categoria_familia) or "categoria" in data' in view
    assert 'observacao_enviada=bool(observacao_familia) or "observacao" in data' in view


def test_importacao_legada_unicred_preserva_ajustes_da_nova_tela():
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    trecho = core.split("def importar_legado_para_parcelas_fatura", 1)[1].split(
        "def calcular_totais_dre_fatura", 1
    )[0]

    assert "fi.account_id=%s" in trecho
    assert "somente_conciliacao" in trecho
    assert "transacao_id_criado" in trecho
    assert "fv.transacao_id::text AS agregado_id" in trecho
    assert "fl.transacao_id_criado::text AS parcela_id" in trecho
    assert "a.transacao_id::text=r.agregado_id" in trecho
    assert "destino.transacao_id::text=e.parcela_id" in trecho
    assert "destino.categoria IS NULL OR destino.categoria=''" in trecho
    assert "ON CONFLICT (transacao_id,dimensao_id) DO NOTHING" in trecho
    assert "Parcela gerada pela fatura %%" in trecho
    assert "COALESCE(destino.conferida,false)=false" in trecho
    assert "duplicada=" not in trecho


def test_resumo_conta_transacao_rateada_uma_vez_e_status_tem_filtros_explicitos():
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    template = (RAIZ / "templates" / "index.html").read_text(encoding="utf-8")

    assert "COUNT(DISTINCT t.transacao_id) AS total_reais" in view
    assert "total_recebidos" in view
    for status in (
        "pendente_banco", "fora_resultado", "somente_conciliacao",
        "substituido", "rateio_incompleto",
    ):
        assert f'value="{status}"' in template


def test_dre_da_fatura_usa_valor_do_pdf_e_classificacao_do_vinculo():
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    trecho = core.split("def calcular_totais_dre_fatura", 1)[1].split("def migrate", 1)[0]

    assert "cartao.fatura_linha" in trecho
    assert "cartao.fatura_vinculo" in trecho
    assert "valor*proporcao" in trecho
    assert "v.transacao_id=l.transacao_id_criado" in trecho


def test_migracoes_sao_sequenciais_e_registradas_uma_vez():
    """Cada bloco 'if versao_atual < N' precisa ter o seu 'INSERT ... VALUES (N)'.

    Sem isso a migracao ou nunca e registrada (roda de novo a cada boot) ou pula
    um numero e a proxima nunca roda. Migracao ja aplicada em producao nao pode
    ser reescrita: criaria divergencia de schema entre bancos.
    """
    import re

    texto = (RAIZ / "core.py").read_text(encoding="utf-8")
    versoes_bloco = [int(v) for v in re.findall(r"if versao_atual < (\d+):", texto)]
    versoes_gravadas = [
        int(v) for v in re.findall(r"INSERT INTO cartao\.schema_version \(versao\) VALUES \((\d+)\)", texto)
    ]

    assert versoes_bloco == sorted(versoes_bloco), "blocos fora de ordem"
    assert versoes_bloco == list(range(1, len(versoes_bloco) + 1)), f"numeracao com buraco: {versoes_bloco}"
    assert sorted(versoes_gravadas) == versoes_bloco, (
        f"blocos {versoes_bloco} mas gravam {sorted(versoes_gravadas)}"
    )
    assert len(versoes_gravadas) == len(set(versoes_gravadas)), "versao gravada mais de uma vez"


def test_consenso_dos_ok_fica_restrito_a_unicred_e_preserva_dados_humanos():
    texto = (RAIZ / "core.py").read_text(encoding="utf-8")
    # delimitado no bloco seguinte, e nao em cur.close(): senao o trecho engole
    # as migracoes 43+ e as asserts passam a falar de codigo de outra migracao
    trecho = texto.split("if versao_atual < 42:", 1)[1].split("if versao_atual < 43:", 1)[0]

    assert 'conta_unicred = "b6243125-dca2-42b2-8c20-0825782c6d8d"' in trecho
    assert "CREATE TABLE IF NOT EXISTS cartao.classificacao_backup_v42" in trecho
    assert "t.conferida=false" in trecho
    assert "NULLIF(t.categoria,'') IS NULL" in trecho
    assert "ON CONFLICT (transacao_id,dimensao_id) DO NOTHING" in trecho
    assert "t.observacao" in trecho
    assert "ESTACAO\"" not in trecho
    assert "INSERT INTO cartao.schema_version (versao) VALUES (42)" in trecho


def test_menu_de_colunas_nao_depende_de_funcao_de_outra_tela():
    """O menu "Colunas" aparece em todas as telas, mas cfToggle() so existe em
    lancamentos.js e relatorios.js - usar ela aqui quebraria /categorias,
    /grupos e as demais com ReferenceError."""
    tabelas = (RAIZ / "static" / "tabelas.js").read_text(encoding="utf-8")
    codigo = "\n".join(
        "" if l.lstrip().startswith("//") else l for l in tabelas.splitlines()
    )
    assert "cfToggle(" not in codigo, "tabelas.js nao pode chamar cfToggle"
    assert "function menuColunas" in tabelas
    assert "aplicarOcultas();" in tabelas, "a preferencia salva precisa valer no carregamento"


def test_item_do_filtro_nao_herda_caixa_alta_do_rotulo():
    """.chip-opt e um <label> dentro de .filters / .rel-filtros, que aplicam
    text-transform: uppercase nos rotulos de campo ("MES", "STATUS").

    Essa regra tem especificidade 0,1,1 - maior que ".chip-opt" sozinho (0,1,0) -
    entao o nome da conta saia em CAIXA ALTA na lista de origens. O seletor
    precisa ser "label.chip-opt" para empatar em peso e vencer por vir depois.
    """
    css = (RAIZ / "static" / "app.css").read_text(encoding="utf-8")
    assert "label.chip-opt {" in css, "o seletor precisa do 'label.' para ter peso suficiente"
    bloco = css[css.index("label.chip-opt {"):]
    bloco = bloco[:bloco.index("}")]
    assert "text-transform: none" in bloco


def test_contador_do_chip_nao_vaza_para_o_texto_da_opcao():
    """O numero de lancamentos mora DENTRO do span de texto da opcao, para fluir
    junto da frase em vez de ser empurrado para a borda direita pelo flex.

    Efeito colateral: textContent da opcao passa a incluir o numero. Ele nao pode
    aparecer no chip pequeno de selecionado nem casar na busca do painel - quem
    procura "61" quer o texto, nao a conta que tem 61 lancamentos.
    """
    tabelas = (RAIZ / "static" / "tabelas.js").read_text(encoding="utf-8")
    assert "function textoDaOpcao" in tabelas

    for nome in ("lancamentos.js", "relatorios.js"):
        js = (RAIZ / "static" / nome).read_text(encoding="utf-8")
        assert "lbl.textContent.trim()" not in js, f"{nome}: chip selecionado leria o numero"
        assert "opt.textContent.toLowerCase()" not in js, f"{nome}: a busca casaria com o numero"
        assert "textoDaOpcao(" in js


def test_menu_de_colunas_nao_usa_a_classe_do_filtro():
    """O menu "Colunas" nao pode ser .chipfilter.

    atualizarChipLabels() varre .chipfilter esperando um .chip-btn com
    data-label, e coletarQuery() varre os checkboxes de dentro esperando que
    tenham name. O menu de colunas nao tem nem um nem outro: com a classe errada,
    atualizarChipLabels estourava TypeError na primeira linha de aplicarFiltros()
    e o filtro de origem parava de funcionar - sem erro visivel na tela.
    """
    tabelas = (RAIZ / "static" / "tabelas.js").read_text(encoding="utf-8")
    assert "caixa.className = 'menu-colunas'" in tabelas
    assert "caixa.className = 'chipfilter'" not in tabelas

    for nome in ("lancamentos.js", "relatorios.js"):
        js = (RAIZ / "static" / nome).read_text(encoding="utf-8")
        # so checkbox com name e filtro
        assert "input[type=checkbox]:checked" not in js, f"{nome}: pegaria checkbox sem name"
        assert "if (!btn) return;" in js, f"{nome}: atualizarChipLabels precisa ser defensiva"


def test_botao_de_filtro_nao_tem_sinal_de_mais():
    """O botao abre um filtro, nao adiciona nada - o '+' confundia."""
    assert "chip-plus" not in (RAIZ / "core.py").read_text(encoding="utf-8")
    for nome in ("lancamentos.js", "relatorios.js"):
        assert "chip-plus" not in (RAIZ / "static" / nome).read_text(encoding="utf-8")


def test_barra_da_tabela_e_refeita_quando_a_tabela_e_trocada():
    """O filtro AJAX de Lancamentos troca a tabela por replaceWith.

    A barra (campo de filtro + menu de colunas) guarda closures apontando para a
    tabela em que foi criada. Sem refazer, ela continuaria controlando um
    elemento fora do DOM: as colunas ja escondidas apareciam certas - porque a
    ativacao nova reaplica o estado salvo - mas clicar no menu nao fazia mais
    nada, o que e pior do que falhar de forma visivel.
    """
    js = (RAIZ / "static" / "tabelas.js").read_text(encoding="utf-8")
    assert "barra.__tabela = table;" in js, "a barra precisa saber que tabela serve"
    assert "barraAtual.__tabela !== table" in js, "precisa detectar que a tabela mudou"
    assert "filtroAnterior" in js, "o texto digitado no filtro nao pode se perder na troca"


def test_aviso_de_duplicadas_usa_a_mesma_normalizacao_da_validacao():
    """categoria_com_nome() ignora acento e caixa ao BARRAR um nome repetido.

    O aviso que LISTA as repetidas agrupava pelo nome literal, entao deixava
    passar justamente o caso mais dificil de ver na tela: "Transferencia Interna"
    e "Transferência Interna" sao duas categorias e parecem a mesma. As duas
    pontas precisam usar chave_alfa().
    """
    codigo = (RAIZ / "views" / "cadastros.py").read_text(encoding="utf-8")
    assert "por_nome.setdefault(chave_alfa(c[\"nome\"])" in codigo
    assert "por_nome.setdefault(c[\"nome\"]" not in codigo


def test_status_duplicada_filtra_somente_itens_ja_marcados():
    codigo = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    assert 'status == "duplicada"' in codigo
    assert 'where.append("COALESCE(t.duplicada, false) = true")' in codigo


def test_ocultar_coluna_vence_a_regra_defensiva_do_cabecalho():
    """O CSS tem 'table.ajustavel th[data-col] { display: table-cell !important }'
    - defesa contra classes de celula (.cel-origem usa flex) vazarem para o <th>.

    Esconder coluna com style inline ou classe simples PERDE para esse !important:
    o cabecalho continuava visivel e so a celula do corpo sumia, o que desalinhava
    a tabela inteira. O seletor do ocultar precisa ser mais especifico.
    """
    css = (RAIZ / "static" / "app.css").read_text(encoding="utf-8")
    assert "table.ajustavel th[data-col].coluna-oculta" in css
    assert "table.ajustavel td[data-col].coluna-oculta" in css

    js = (RAIZ / "static" / "tabelas.js").read_text(encoding="utf-8")
    assert "classList.toggle('coluna-oculta'" in js
    assert "th.style.display" not in js, "style inline perde para o !important do CSS"


def test_dica_automatica_so_no_que_esta_cortado():
    """Qualquer campo cujo conteudo nao caiba na largura atual ganha data-tip,
    como a descricao ja tinha vindo do servidor.

    Duas coisas que o codigo precisa garantir:
    - marcar SO o que esta cortado de fato (dica em texto ja visivel e ruido),
      por isso mede a largura do texto em vez de marcar tudo;
    - nao pisar no data-tip que vem do servidor - as dicas automaticas levam
      data-tip-auto para poderem ser retiradas quando a coluna alargar.
    """
    js = (RAIZ / "static" / "tabelas.js").read_text(encoding="utf-8")
    assert "function atualizarDicasDeTruncamento" in js
    assert "data-tip-auto" in js, "precisa distinguir a dica automatica da do servidor"
    assert "function larguraDoTexto" in js, "precisa medir, nao marcar tudo"
    # roda ao ativar, ao esconder coluna e ao redimensionar
    assert js.count("atualizarDicasDeTruncamento(table)") >= 3


def test_cartao_padrao_da_fatura_prefere_conta_com_fatura_importada():
    """Abrir /lancamentos/fatura sem account_id nao pode cair em cartao vazio.

    A primeira conta de credito da lista pode nunca ter tido fatura importada
    (Nubank). Nesse caso a tela abria so com a mensagem de erro e sem seletor
    de cartao — sem saida para trocar para a Unicred.
    """
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    template = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    assert "def _conta_credito_padrao(" in view
    assert "contas_credito[0][0]" not in view
    assert view.count("account_id = _conta_credito_padrao(cur, contas_credito)") == 2
    # o seletor de cartao tem que existir tambem no estado de erro
    assert template.count('id="faturaConta"') == 2


def test_marca_de_agregado_tem_caminho_de_volta():
    """`somente_conciliacao` precisa poder ser retirada, nao so posta.

    A marca so era aplicada. Quando o conjunto de vinculos mudava (refazer
    vinculos, reenvio de PDF, desvincular na mao), a transacao deixava de ser
    agregado e continuava fora do DRE para sempre, sem nenhuma parcela no lugar
    dela — cinco compras a vista sumiram do resultado (R$ 1.167,38).
    """
    view = (RAIZ / "views" / "relatorios.py").read_text(encoding="utf-8")
    nucleo = (RAIZ / "core.py").read_text(encoding="utf-8")
    assert "SET somente_conciliacao = false" in view
    assert "desmarcados_agora" in view
    # a trava: nao desmarcar quem ja teve parcela gerada, senao conta duas vezes
    trecho = view[view.index("SET somente_conciliacao = false"):]
    trecho = trecho[:trecho.index("desmarcados = ")]
    assert "fl.transacao_id_criado IS NOT NULL" in trecho
    assert "NOT EXISTS" in trecho
    # a correcao pontual do dado que ja estava errado, com ponto de reversao
    assert "versao_atual < 44" in nucleo
    assert "cartao.agregado_backup_v44" in nucleo


def test_sincronizacao_de_parcelas_nao_aborta_sem_agregado():
    """Sem agregado ainda pode haver marca obsoleta para retirar.

    O retorno antecipado pulava o UPDATE de desmarcacao; so a previa pode sair
    cedo, porque ela e' estritamente somente leitura.
    """
    view = (RAIZ / "views" / "relatorios.py").read_text(encoding="utf-8")
    assert "if not agregados and preview:" in view


def test_consenso_publicado_em_2025_aprende_so_com_ok_e_nao_toca_conferido():
    """A migracao 45 publica em 2025 o que o usuario ja assinou com OK.

    As travas que nao podem cair: aprende so de conferido, exige unanimidade e
    2+ evidencias, preenche so campo vazio de lancamento NAO conferido, tem
    ponto de reversao e recusa os padroes que a revisao humana reprovou.
    """
    texto = (RAIZ / "core.py").read_text(encoding="utf-8")
    trecho = texto.split("if versao_atual < 45:", 1)[1].split("VALUES (45)", 1)[0]

    assert 'conta_unicred = "b6243125-dca2-42b2-8c20-0825782c6d8d"' in trecho
    assert "CREATE TABLE IF NOT EXISTS cartao.classificacao_backup_v45" in trecho
    # so aprende de quem tem OK
    assert "if not conferida:" in trecho and "continue" in trecho
    # unanimidade e duas evidencias
    assert "if len(contagem) != 1:" in trecho
    assert "if vezes < 2:" in trecho
    # nunca escreve por cima nem toca conferido
    assert "NULLIF(categoria,'') IS NULL" in trecho
    assert "AND conferida=false" in trecho
    assert "ON CONFLICT (transacao_id,dimensao_id) DO NOTHING" in trecho
    # escopo 2025 e padroes reprovados na revisao
    assert "dia.year >= 2026" in trecho
    for reprovado in ("LETICIAKAYSER", "POUSADA FOGO*RESE", "CATIVA", "ESTACAO"):
        assert reprovado in trecho
    # projeto de viagem e evento datado: nunca propagar
    assert 'startswith("VIAGEM ")' in trecho


def test_canonizacao_de_lojista_corta_em_limite_de_palavra():
    """ESTACAO nao pode engolir HIPER CENTER ESTACAO.

    Sem o corte em limite de palavra, uma chave curta vira prefixo de outra
    loja sem relacao nenhuma e as duas passam a compartilhar classificacao.
    """
    texto = (RAIZ / "core.py").read_text(encoding="utf-8")
    trecho = texto.split("def _canonizar_v45(", 1)[1].split("return mapa", 1)[0]
    assert 'chave[len(curta)] == " "' in trecho
    assert "chave.startswith(curta)" in trecho


def test_helpers_de_lojista_ficam_no_modulo_e_nao_dentro_da_migracao():
    """Helper usado por mais de uma migracao nao pode morar dentro de um bloco.

    `_loja_v45` e `_canonizar_v45` nasceram dentro do `if versao_atual < 45`.
    Num banco que ja estava na versao 45 aquele bloco nao roda, e a migracao 46
    quebrava com NameError - erro que so aparece no banco ja migrado, nunca num
    banco novo, que e onde os testes normalmente olham.
    """
    import ast as _ast
    arvore = _ast.parse((RAIZ / "core.py").read_text(encoding="utf-8"))
    topo = {
        n.name for n in arvore.body
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
    }
    assert "_loja_v45" in topo
    assert "_canonizar_v45" in topo


def test_decisoes_do_usuario_valem_contra_o_consenso_mas_nunca_mexem_no_ok():
    """Migracao 46: o usuario decidiu, um a um, os lojistas divergentes.

    Aqui a decisao dele sobrescreve categoria mesmo em lancamento conferido -
    foi ele quem definiu. O que NAO pode acontecer em hipotese nenhuma e o
    codigo tocar em `conferida`, `conferida_por` ou `conferida_em`.
    """
    texto = (RAIZ / "core.py").read_text(encoding="utf-8")
    trecho = texto.split("if versao_atual < 46:", 1)[1].split("VALUES (46)", 1)[0]

    assert "CREATE TABLE IF NOT EXISTS cartao.classificacao_backup_v46" in trecho
    for decidido in ("CATIVA", "LETICIAKAYSER", "LISCIA", "TOTAL SPORTES",
                     "PANIFICADORA E CONFEIT", "APPLE.COM/BILL"):
        assert decidido in trecho
    # observacao do usuario so entra onde esta vazia
    assert "NULLIF(observacao,'') IS NULL" in trecho
    # completar OK preenche so campo vazio
    assert "NULLIF(categoria,'') IS NULL" in trecho
    assert "ON CONFLICT (transacao_id,dimensao_id) DO NOTHING" in trecho
    # o OK e' intocavel
    for proibido in ("SET conferida", "conferida=true", "conferida = true",
                     "conferida_por=", "conferida_em="):
        assert proibido not in trecho, f"a migracao 46 nao pode escrever {proibido}"


def test_regra_do_guilherme_respeita_o_limite_de_120_reais():
    """Abaixo de R$120 e Agua, acima e Gas. R$120,00 exatos seguem sem decisao."""
    texto = (RAIZ / "core.py").read_text(encoding="utf-8")
    trecho = texto.split("if versao_atual < 46:", 1)[1].split("VALUES (46)", 1)[0]
    assert '("Agua", "<")' in trecho
    assert '("Agua / Gas", ">")' in trecho
    assert '"<=" ' not in trecho and '">=" ' not in trecho


def test_migracao_47_trata_valor_nulo_como_dimensao_vazia():
    """O bug que zerou o preenchimento nas migracoes 45 e 46.

    ON CONFLICT DO NOTHING nunca conserta uma linha que ja existe com
    valor_id NULL - e o WHERE do DO UPDATE garante que valor preenchido
    jamais e sobrescrito.
    """
    texto = (RAIZ / "core.py").read_text(encoding="utf-8")
    trecho = texto.split("if versao_atual < 47:", 1)[1].split("VALUES (47)", 1)[0]
    assert "_dimensao_vazia(dims, campo)" in trecho
    assert "DO UPDATE" in trecho
    assert "WHERE cartao.transacao_dimensao.valor_id IS NULL" in trecho
    assert "CREATE TABLE IF NOT EXISTS cartao.classificacao_backup_v47" in trecho
    for proibido in ("SET conferida", "conferida=true", "conferida_por="):
        assert proibido not in trecho


def test_cartao_pendente_explica_por_que_falta_o_titular_na_fatura_em_andamento():
    """"-" mudo escondia que o Pluggy so nao mandou o metadado ainda.

    Titular completo (nome impresso) so existe no PDF - Pluggy nunca manda
    isso por transacao. O final do cartao (creditCardMetadata.cardNumber) as
    vezes falta enquanto a compra esta PENDING (assinaturas, cobranca
    internacional) e costuma aparecer quando o Pluggy confirma o POSTED. Sem
    aviso, a tela parecia ter um dado faltando por erro do sistema.
    """
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    template = (RAIZ / "templates" / "lancamentos_fatura.html").read_text(encoding="utf-8")
    assert '"cartao_aguardando": not tx["numero_cartao_final"]' in view
    assert 'linha["cartao_aguardando"] = False' in view  # fatura fechada: nunca se aplica
    assert template.count("cartao_aguardando") == 2  # celula da tabela + painel expandido
    assert "cartão pendente" in template


def test_migracao_48_nao_encosta_no_ok_e_tem_ponto_de_reversao():
    """O OK e assinatura humana (secao 1.2) e alteracao em lote precisa de backup."""
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    bloco = core.split("if versao_atual < 48:", 1)[1].split("cur.close()", 1)[0]
    assert "classificacao_backup_v48" in bloco, "sem ponto de reversao"
    assert "SET conferida" not in bloco
    assert "conferida=" not in bloco
    assert "SET observacao" not in bloco, "observacao pertence ao usuario"
    # so preenche vazio: nunca sobrescreve valor ja escolhido
    assert "NULLIF(categoria,'') IS NULL" in bloco
    assert "cartao.transacao_dimensao.valor_id IS NULL" in bloco
    assert "_dimensao_vazia" in bloco
    # os dois consensos recusados na revisao da previa
    assert '"Leisure"' in bloco and '"Insurance"' in bloco
