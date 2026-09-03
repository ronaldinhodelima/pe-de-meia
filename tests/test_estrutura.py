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
import re

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
        "/api/classificacao/reaplicar-consenso",
        "/api/fatura/vinculos-suspeitos",
        "/api/diagnostico/eco-3h",
        "/api/diagnostico/classificacao-ok",
        "/api/diagnostico/suspeitas-duplicidade",
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
    assert template.index('id="modalConferida"') < template.index('id="modalRateioBox"')
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


def _bloco_migracao(core, versao):
    """Texto de UMA migracao.

    Delimitar por "cur.close()" so funcionava enquanto a migracao fosse a
    ultima do arquivo: assim que outra era acrescentada abaixo, o bloco passava
    a engolir as seguintes e as asercoes viravam ruido. O fim certo e o inicio
    da proxima migracao.
    """
    corpo = core.split("if versao_atual < %d:" % versao, 1)[1]
    seguinte = re.search(r"\n\s*if versao_atual < \d+:", corpo)
    return corpo[:seguinte.start()] if seguinte else corpo.split("cur.close()", 1)[0]


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


def test_reaplicar_consenso_e_repetivel_com_previa_e_sem_tocar_no_ok():
    view = (RAIZ / "views" / "relatorios.py").read_text(encoding="utf-8")
    rota = view.split("def api_reaplicar_consenso", 1)[1]
    assert 'methods=["POST"]' in view.split("def api_reaplicar_consenso", 1)[0][-200:], \
        "gravar em lote nunca por GET"
    assert "conn.rollback()" in rota, "a previa nao pode deixar rastro"
    assert "registrar_auditoria" in rota
    assert "conferida=" not in rota and "SET conferida" not in rota

    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    helper = core.split("def aplicar_consenso_classificacao", 1)[1].split(
        "def preencher_classificacao_vazia_parcelas", 1)[0]
    assert "SET conferida" not in helper and "conferida=" not in helper
    assert "SET observacao" not in helper
    assert "NULLIF(categoria,'') IS NULL" in helper
    assert "cartao.transacao_dimensao.valor_id IS NULL" in helper


def test_migracao_50_desfaz_so_a_lista_explicita_e_respeita_a_trava_da_6_6():
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    bloco = _bloco_migracao(core, 50)
    assert "vinculo_backup_v50" in bloco and "agregado_backup_v50" in bloco
    assert "SET conferida" not in bloco and "conferida=" not in bloco
    # os falsos positivos legitimos nao podem estar na lista de alvos
    alvos = bloco.split("alvos_v50 = (", 1)[1].split(")", 1)[0]
    for legitimo in ("Pagamento", "Anuidade", "ANJOS", "ZANELATTO"):
        assert legitimo.upper() not in alvos.upper(), legitimo
    # trava da 6.6: nunca desmarcar quem ja gerou parcela
    assert "transacao_id_criado" in bloco
    assert "if vinculos >= 2 or com_parcela:" in bloco


def test_migracao_51_nao_encosta_no_ok_e_so_marca_o_par_que_ainda_bate():
    """Secao 4.3: `substituido_por` e' o estado do MESMO evento, nao `duplicada`.

    E secao 1.2: os dois pares estao com OK assinado, e a migracao nao pode
    mexer nele - so tirar do resultado o registro repetido.
    """
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    bloco = _bloco_migracao(core, 51)

    assert "eco_backup_v51" in bloco, "alteracao de dado sem ponto de reversao"
    assert "SET conferida" not in bloco and "conferida=" not in bloco
    # e' o mesmo evento, nao cobranca em dobro: `duplicada` seria o estado errado
    assert "SET substituido_por" in bloco
    assert "SET duplicada" not in bloco
    # so grava sobre quem ainda esta contando no resultado
    assert "substituido_por IS NULL" in bloco
    assert "COALESCE(duplicada,false)=false" in bloco
    assert "COALESCE(somente_conciliacao,false)=false" in bloco
    # e so se o par ainda for o que a varredura mediu
    assert "interval '3 hours'" in bloco
    assert "a.account_id=b.account_id" in bloco


def test_migracao_52_respeita_o_ok_a_origem_e_o_que_o_usuario_nao_decidiu():
    """Secao 1.2, 1.4, 8.1 e 11.4 aplicadas as decisoes de 02/09/2026."""
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    bloco = _bloco_migracao(core, 52)

    assert "classificacao_backup_v52" in bloco, "alteracao em lote sem reversao"
    assert "SET conferida" not in bloco and "conferida=" not in bloco
    # observacao e' do usuario (secao 7.3): esta migracao nao escreve nela
    assert "SET observacao" not in bloco

    # VISA NACIONAL sao estornos e NAO pode estar entre os alvos
    decisoes = bloco.split("decisoes_v52 = (", 1)[1].split("\n            )", 1)[0]
    assert "VISA NACIONAL" not in decisoes.upper()

    # regra nova sempre presa a origem (secao 8.1 / 11.4)
    regras = bloco.split("novas_regras_v52 = (", 1)[1].split("\n            )", 1)[0]
    assert "EVENTIM" not in regras, "projeto datado nao vira regra (secao 8.2)"
    assert "account_id" in bloco.split("INSERT INTO cartao.regra_classificacao", 1)[1][:400]

    # o corte do GuilhermeDaSilva passou a ser 100
    assert "valor_limite=100" in bloco
    assert "120" in bloco, "a migracao precisa achar as regras antigas de 120"


def test_migracao_53_troca_duplicada_por_mesmo_evento_sem_mover_o_dre():
    """Secao 4.3: `duplicada` e' o que SOBRA. Havendo par identificavel, o
    estado certo e' `substituido_por` - e ele tem caminho de volta."""
    core = (RAIZ / "core.py").read_text(encoding="utf-8")
    bloco = _bloco_migracao(core, 53)

    assert "duplicada_backup_v53" in bloco, "troca de estado sem reversao"
    assert "SET conferida" not in bloco and "conferida=" not in bloco
    assert "SET duplicada=false, substituido_por=" in bloco
    # so age sobre quem ainda esta marcado, e nunca por cima de uma substituicao
    assert "COALESCE(duplicada,false)=true" in bloco
    assert "substituido_por IS NULL" in bloco
    # valida o par antes de gravar (secao 4.3)
    assert "a.account_id=b.account_id" in bloco


def test_marcar_como_duplicada_nao_existe_mais_em_lugar_nenhum():
    """Decisao de 02/09/2026: a marcacao saiu da interface e da API.

    Pela secao 4.3, `duplicada` e o estado que SOBRA - mesma cobranca duas
    vezes, sem estorno e sem par identificavel. Na pratica todo caso real tinha
    par e era `substituido_por`, que aponta qual registro conta e tem caminho de
    volta; `duplicada` so escondia o lancamento sem dizer por quem.

    A COLUNA continua existindo e continua excluindo do resultado - ela faz
    parte de `elegivel`, e tirar isso faria consultas que hoje excluem
    duplicados passarem a inclui-los em silencio.
    """
    lanc = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    index = (RAIZ / "templates" / "index.html").read_text(encoding="utf-8")
    js = (RAIZ / "static" / "lancamentos.js").read_text(encoding="utf-8")

    # nada grava a coluna
    assert 'sets.append("duplicada' not in lanc
    assert '"duplicada" in data' not in lanc
    assert "confirmar_duplicada" not in lanc and "confirmar_duplicada" not in js
    # o controle nao existe mais na tela
    assert "modalDup" not in index and "modalDup" not in js
    assert "Marcar como duplicada" not in index
    assert "dup-check" not in index and "dup-check" not in js
    # nem o filtro de status
    assert 'status == "duplicada"' not in lanc
    assert 'value="duplicada"' not in index

    # mas a coluna segue tirando do resultado
    assert "COALESCE(t.duplicada, false) = false" in lanc
    assert 'item["duplicada"]' in lanc, "duplicada ainda define `elegivel`"


def test_natureza_neutra_nao_exige_dimensao_em_nenhuma_tela():
    """Secao 4.1: centro de custo so faz sentido em lancamento do resultado.

    Pagamento de fatura e transferencia entre contas proprias nunca teriam essas
    dimensoes, entao cobrar delas so inflava a lista de pendencia - e a trava do
    servidor impedia o OK para sempre.
    """
    import core

    assert core.exige_dimensoes("despesa") is True
    assert core.exige_dimensoes("receita") is True
    assert core.exige_dimensoes(None) is True, "sem natureza o padrao e despesa"
    for neutra in core.NATUREZAS_NEUTRAS:
        assert core.exige_dimensoes(neutra) is False, neutra

    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    # a mesma condicao nas tres telas e na trava do servidor (secao 6.5 n.10)
    assert view.count("exige_dimensoes(") >= 2, "detalhada e fatura em andamento"
    assert "EXIGE_DIMENSOES_SQL" in view, "e a trava do servidor, em SQL"


def test_checkbox_tem_uma_unica_definicao_visual_em_todo_o_sistema():
    """Regra obrigatoria: o desenho da caixa de marcacao mora so no app.css.

    Uma segunda definicao vence ou perde por especificidade dependendo da tela,
    sem erro nenhum: `table.compacta input[type=checkbox]` deixava a caixa 14px
    dentro da tabela e 16px fora, e o CSS "parecia certo" nas duas leituras.
    """
    import re

    def sem_comentarios(texto):
        return re.sub(r"/\*.*?\*/", " ", texto, flags=re.S)

    css = (RAIZ / "static" / "app.css").read_text(encoding="utf-8")
    partes = css.split("CAIXAS DE MARCACAO - REGRA UNICA E OBRIGATORIA", 1)
    assert len(partes) == 2, "o bloco unico sumiu do app.css"
    unico, resto = partes[1].split("input[type=checkbox]:disabled", 1)
    for propriedade in ("appearance", "width", "height", "border-radius", "box-shadow"):
        assert propriedade in unico, propriedade

    # todo lugar que poderia desenhar um checkbox, menos o proprio bloco unico
    fontes = {"static/app.css": sem_comentarios(partes[0] + resto)}
    for pasta, padrao in (("templates", "*.html"), ("static", "*.css")):
        for caminho in sorted((RAIZ / pasta).glob(padrao)):
            chave = f"{pasta}/{caminho.name}"
            if chave in fontes:
                continue  # o app.css ja entrou acima, sem o bloco unico
            fontes[chave] = sem_comentarios(caminho.read_text(encoding="utf-8"))
    fontes["core.py"] = sem_comentarios((RAIZ / "core.py").read_text(encoding="utf-8"))

    propriedades = r"(appearance|width|height|border|accent-color|background)\s*:"
    culpados = []
    for nome, texto in fontes.items():
        for regra in re.findall(r"[^{}]*\{[^}]*\}", texto):
            seletor, corpo = regra.split("{", 1)
            # `accent-color` so existe para checkbox e radio: onde ele aparece,
            # alguem esta desenhando a caixa. E o sinal confiavel, e pega o
            # seletor que nao diz "checkbox" - foi assim que passaram
            # `.perm-item input` e `label.chip-opt input`.
            desenha = "accent-color" in corpo
            if "checkbox" in seletor and re.search(propriedades, corpo):
                desenha = True
            if desenha:
                culpados.append(nome + ": " + " ".join(regra.split())[:90])

    # A varredura acima so enxerga regra CSS (com chaves). O atributo style= de
    # um <input type=checkbox> nao tem chaves e passava batido: era assim que
    # `style="width:15px;height:15px;accent-color:var(--accent)"` desenhava a
    # caixa de "Marcar como duplicada" por fora do bloco unico, na mesma sessao
    # em que a regra foi declarada obrigatoria.
    for pasta, padrao in (("templates", "*.html"), ("static", "*.js")):
        for caminho in sorted((RAIZ / pasta).glob(padrao)):
            texto = caminho.read_text(encoding="utf-8")
            for tag in re.findall(r"<input[^>]*>", texto):
                if "checkbox" not in tag:
                    continue
                estilo = re.search(r'style\s*=\s*"([^"]*)"', tag)
                if estilo and re.search(propriedades, estilo.group(1)):
                    culpados.append(f"{pasta}/{caminho.name}: {estilo.group(1)[:70]}")

    assert not culpados, (
        "checkbox so pode ser desenhado no bloco unico do app.css: " + str(culpados)
    )


def test_valores_visuais_fora_do_sistema_nao_aumentam():
    """Catraca do sistema de design: o numero so pode cair.

    Nao trava tudo de uma vez - sao centenas de valores escritos na mao ao longo
    do projeto, e converter todos num golpe so mudaria pixel em toda tela sem
    ninguem conferir. Trava o crescimento: tela nova nasce usando token, e cada
    limpeza abaixa o teto. Para ver o que falta:
        python3 ferramentas/inventario_estilo.py --lista
    """
    import subprocess
    import sys

    TETO = 55

    saida = subprocess.run(
        [sys.executable, str(RAIZ / "ferramentas" / "inventario_estilo.py")],
        capture_output=True, text=True, check=True,
    ).stdout
    total = int(saida.split("tokens:", 1)[1].split()[0])
    assert total <= TETO, (
        f"{total} valores visuais fora do sistema de tokens (teto {TETO}). "
        "Use as variaveis do :root em vez de cor/tamanho/raio escritos na mao."
    )
    assert total >= TETO - 25, (
        f"o teto ficou folgado demais ({total} de {TETO}): abaixe TETO para {total} "
        "para que a proxima regressao seja pega."
    )


def test_cabecalho_de_tabela_tem_um_peso_so():
    """`table.ajustavel th[data-col]` e mais especifico que `th` e desfazia, em
    silencio, a decisao de tirar o negrito dos cabecalhos - a mesma classe de
    erro do checkbox 14px vs 16px."""
    css = (RAIZ / "static" / "app.css").read_text(encoding="utf-8")
    geral = css.split("\n  th {", 1)[1].split("}", 1)[0]
    ajustavel = css.split("table.ajustavel th[data-col] {", 1)[1].split("}", 1)[0]
    assert "font-weight: var(--peso-medio)" in geral
    assert "var(--peso-forte)" not in ajustavel, "cabecalho de tabela nao volta a negrito"


def test_lancamento_manual_aceita_a_classificacao_inteira_e_trava_o_ok():
    """O manual passa pelas mesmas regras da tela, nao por um caminho paralelo."""
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    bloco = view.split("def lancamento_manual", 1)[1].split("@bp.route", 1)[0]
    assert "transacao_dimensao" in bloco, "grava as dimensoes"
    assert "observacao" in bloco
    # o OK exige permissao E classificacao completa, como em qualquer lancamento
    assert 'pode("lancamentos_conferir")' in bloco
    assert "obrigatoria = true" in bloco
    assert "falta_categoria" in bloco
    # recusa explicita: nunca cria descartando o OK em silencio
    assert '"ok": False' in bloco and "Para marcar como conferido" in bloco
    assert "registrar_auditoria" in bloco


def test_descricao_so_muda_em_lancamento_manual():
    """A descricao de lancamento do Pluggy pertence ao banco (secao 4.6).

    A trava mora no WHERE, e nao so na tela: chamada direta a API tambem nao
    pode reescrever a descricao de um lancamento sincronizado.
    """
    view = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    bloco = view.split("def update_transacao", 1)[1]
    assert 'escopo = " AND account_id = %s" if "descricao" in data else ""' in bloco
    assert "extra = [CONTA_MANUAL_ID]" in bloco
    assert "{escopo}" in bloco


def test_valor_e_impresso_no_formato_brasileiro():
    """A interface e em portugues (secao 1.5) e o `,.2f` do Python e ingles.

    O modal de detalhes mostrava "- R$ 200.00" e "-200.00 BRL": o helper certo
    existia, mas morava dentro de views/cadastros.py e nenhuma outra tela o
    enxergava. Agora e do core, e nenhuma view volta a formatar na mao.
    """
    from core import valor_pt

    assert valor_pt(200) == "200,00"
    assert valor_pt(-200) == "-200,00"
    assert valor_pt(1234.56) == "1.234,56"
    assert valor_pt(1234567.8) == "1.234.567,80"

    for caminho in sorted((RAIZ / "views").glob("*.py")):
        texto = caminho.read_text(encoding="utf-8")
        assert ",.2f" not in texto, f"{caminho.name} formata valor na mao"


def test_modal_tem_uma_tipografia_de_rotulo_e_um_desenho_de_campo():
    """O modal de detalhes tinha dois tamanhos de rotulo e quatro campos.

    O rotulo da linha pareada usava `<small>`, que encolhe para 0,8em = 10,4px,
    enquanto o da linha simples ficava em 13px - mesmo papel, mesma coluna, dois
    tamanhos, e um deles fora da escala. Os campos somavam tres raios (5, 6 e
    9px), duas alturas e duas bordas. Nada disso aparece lendo o CSS: cada regra
    parecia certa sozinha.
    """
    css = (RAIZ / "static" / "app.css").read_text(encoding="utf-8")

    # o <small> do rotulo nao pode voltar a decidir o tamanho sozinho
    assert ".modal .modal-campo small," in css
    assert ".modal-conferencia small { font-size: var(--fonte-sm)" in css

    # um so bloco desenha campo dentro do modal, e ele nao repete raio na mao
    bloco = css.split('.modal select, .modal input[type="text"]', 1)
    assert len(bloco) == 2, "o bloco unico de campo do modal sumiu"
    corpo = bloco[1].split("}", 1)[0]
    for esperado in ("height: 26px", "var(--radius-xs)", "var(--campo-sombra)", "var(--fonte-sm)"):
        assert esperado in corpo, esperado

    # o combobox le os mesmos tokens: e ele o padrao que os campos seguem
    assert "border-radius:var(--radius-xs)!important" in css
    assert "box-shadow:var(--campo-sombra)!important" in css

    # a descricao nao pode voltar a ter desenho proprio: por ser mais
    # especifica, ela venceria o bloco unico sem nenhum erro aparente
    proprio = css.split("\n.modal-desc-input {", 1)[1].split("}", 1)[0]
    for herdado in ("border", "padding", "font-size", "background", "border-radius"):
        assert herdado not in proprio, f".modal-desc-input redefine {herdado}"


def test_nenhuma_rota_usa_cursor_depois_de_fechar_a_conexao():
    """Consulta depois de `cur.close()` derruba a rota inteira com 500.

    Aconteceu em 02/09/2026: a leitura de `atualizado_em` para o campo "Última
    alteração" do modal ficou DEPOIS de `cur.close()`/`conn.close()` e passou a
    quebrar TODA edicao de lancamento - descricao, OK, categoria, dimensao.
    `py_compile` passa, a suite passa, e o erro so aparece quando alguem edita
    de verdade (secao 10.3 n.3).

    A varredura olha so o CORPO DIRETO da funcao. Um `close` dentro de um `if`
    que retorna e' saida antecipada legitima - foi o que gerou 20 falsos
    positivos na primeira versao deste teste, e por isso a checagem por numero
    de linha nao serve.
    """
    import ast

    def usa_cursor(no):
        for filho in ast.walk(no):
            if (
                isinstance(filho, ast.Call)
                and isinstance(filho.func, ast.Attribute)
                and getattr(filho.func.value, "id", "") == "cur"
                and (filho.func.attr == "execute" or filho.func.attr.startswith("fetch"))
            ):
                return filho
        return None

    def reabre(no):
        return any(
            isinstance(f, ast.Call) and isinstance(f.func, ast.Name)
            and f.func.id == "get_conn"
            for f in ast.walk(no)
        )

    problemas = []
    for arquivo in sorted((RAIZ / "views").glob("*.py")) + [RAIZ / "core.py"]:
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            fechou = False
            for stmt in no.body:
                if fechou and reabre(stmt):
                    fechou = False
                if fechou:
                    uso = usa_cursor(stmt)
                    if uso is not None:
                        problemas.append(
                            f"{arquivo.name}:{uso.lineno} usa cur.{uso.func.attr} "
                            f"depois de fechar a conexao (em {no.name})"
                        )
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    alvo = stmt.value.func
                    if (
                        isinstance(alvo, ast.Attribute)
                        and alvo.attr == "close"
                        and getattr(alvo.value, "id", "") in ("cur", "conn")
                    ):
                        fechou = True
    assert not problemas, "\n".join(problemas)


def test_editar_lancamento_grava_a_hora_da_alteracao():
    """"Ultima alteracao" no modal precisa refletir a edicao que acabou de ocorrer.

    Migracoes e rotinas em lote sempre gravaram `atualizado_em`, mas a rota que
    o usuario de fato usa para editar nao gravava: o campo mostrava a hora de
    CRIACAO para sempre. Ele dizia a verdade sobre a coluna e mentia sobre o
    dado - pior que nao existir, porque parecia conferido.
    """
    codigo = (RAIZ / "views" / "lancamentos.py").read_text(encoding="utf-8")
    trecho = codigo.split("def update_transacao", 1)[1].split("\ndef ", 1)[0]
    assert 'sets + ["atualizado_em = now()"]' in trecho, (
        "o UPDATE da edicao precisa carimbar atualizado_em"
    )
    # e a resposta le a hora DEPOIS do UPDATE, senao devolveria a anterior
    assert trecho.index("UPDATE cartao.transacao SET") < trecho.index(
        "SELECT atualizado_em FROM cartao.transacao"
    )


def test_fragmentos_de_sql_nao_se_colam_sem_separador():
    """SQL montado por concatenacao implicita nao pode grudar dois tokens.

    Em 03/09/2026 uma edicao encostou `AS importado"` em `"COALESCE(...)` e o
    SQL virou `AS importadoCOALESCE(...)`. `py_compile` passa, a suite inteira
    passa - Python junta os literais adjacentes ANTES do AST, entao nao ha como
    ver a emenda na arvore - e a rota so quebra com 500 ao ser chamada. Por isso
    esta checagem le o texto do arquivo, e nao o AST.

    Vale so para fragmento que parece SQL: texto e URL sao colados de proposito
    em outros lugares.
    """
    import re

    sql = re.compile(
        r"\b(SELECT|FROM|WHERE|JOIN|COALESCE|UPDATE|INSERT|VALUES|GROUP BY"
        r"|ORDER BY|SET |AND |OR |ON |AS )",
        re.I,
    )
    problemas = []
    for arquivo in sorted((RAIZ / "views").glob("*.py")) + [RAIZ / "core.py"]:
        linhas = arquivo.read_text(encoding="utf-8").splitlines()
        for i in range(len(linhas) - 1):
            atual, seguinte = linhas[i].rstrip(), linhas[i + 1].strip()
            fim = re.search(r'"([^"]*)"\s*$', atual)
            ini = re.match(r'f?"([^"]*)"', seguinte)
            if not fim or not ini or not fim.group(1) or not ini.group(1):
                continue
            a, b = fim.group(1), ini.group(1)
            if a.endswith((" ", "(", ",")) or b.startswith((" ", ")", ",", ";")):
                continue
            if not (sql.search(a) or sql.search(b)):
                continue
            problemas.append(f"{arquivo.name}:{i + 1}  ...{a[-30:]!r} colado a {b[:30]!r}")
    assert not problemas, "\n".join(problemas)
