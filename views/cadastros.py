"""Categorias, centro de custos, dimensoes, regras, contas e pendencias."""
from datetime import datetime
from decimal import Decimal, InvalidOperation

import psycopg2
import psycopg2.extras
from flask import Blueprint, request, render_template, jsonify

from core import (
    valor_pt,
    CATEGORIAS_EXTRA,
    CATEGORIAS_NEUTRAS_PADRAO,
    CATEGORIAS_OCULTAS,
    CATEGORIA_PT,
    CATEGORIA_PT_DB,
    FINANCEIRO_TABELA,
    JOIN_NATUREZA,
    NATUREZAS,
    NATUREZAS_NEUTRAS,
    NATUREZA_PADRAO,
    NATUREZA_SQL,
    SEED_NATUREZAS,
    VAL_DESPESA,
    _campo,
    aplicar_regras,
    arvore_centro_custo,
    carregar_origens,
    cat_pt,
    cat_pt_puro,
    categoria_com_nome,
    definir_regra_padrao,
    chave_alfa,
    data_hora_local,
    detectar_banco,
    esc,
    get_conn,
    icone_tipo_html,
    json_script,
    levantar_pendencias,
    ROTULO_TIPO_CONTA,
    marcar_falha_auditoria,
    recarregar_categorias_db,
    registrar_mudanca_auditoria,
    pode,
    requer,
    selo_banco_html,
    topbar_html,
)

bp = Blueprint("cadastros", __name__)


OPERADORES_VALOR = {
    "lt": ("menor que", "<"),
    "lte": ("menor ou igual a", "<="),
    "gt": ("maior que", ">"),
    "gte": ("maior ou igual a", ">="),
    "eq": ("igual a", "="),
}


def _filtro_valor(form):
    operador = (form.get("valor_operador") or "").strip()
    texto = (form.get("valor_limite") or "").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    if not operador and not texto:
        return None, None, None
    if operador not in OPERADORES_VALOR or not texto:
        return None, None, "Escolha a comparação e informe o valor da regra."
    try:
        limite = Decimal(texto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None, None, "Informe um valor válido."
    if limite < 0:
        return None, None, "O valor da regra deve ser positivo."
    return operador, limite, None


def _condicao_valor_sql(operador):
    simbolo = OPERADORES_VALOR.get(operador, (None, None))[1]
    return f" AND ABS(COALESCE(t.valor_brl,t.valor_original)) {simbolo} %s" if simbolo else ""


def _categorias_para_regras():
    """Lista categorias seguras para classificacao automatica.

    Transferencias e aquisicoes de bens continuam fora para nao classificar
    movimentacoes neutras por engano. Investimentos recorrentes, como
    Previdencia, sao permitidos.
    """
    investimentos = {
        categoria for categoria, natureza in SEED_NATUREZAS.items()
        if natureza == "investimento"
    }
    neutras_bloqueadas = {
        categoria
        for categoria in CATEGORIAS_NEUTRAS_PADRAO
        if SEED_NATUREZAS.get(categoria) != "investimento"
    }
    return sorted(
        (set(CATEGORIA_PT) | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB) | investimentos)
        - neutras_bloqueadas
        - CATEGORIAS_OCULTAS,
        key=lambda c: chave_alfa(cat_pt_puro(c)),
    )


@bp.route("/dimensoes", methods=["GET", "POST"])
@requer("cadastros")
def dimensoes_view():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    erro = None

    def contar_uso_valor(valor_id):
        cur.execute(
            "SELECT COUNT(*) AS n FROM cartao.lancamento_financeiro_dimensao WHERE valor_id=%s;",
            (valor_id,),
        )
        return cur.fetchone()["n"]

    def contar_uso_dimensao(dimensao_id):
        cur.execute(
            "SELECT COUNT(*) AS n FROM cartao.lancamento_financeiro_dimensao WHERE dimensao_id=%s;",
            (dimensao_id,),
        )
        return cur.fetchone()["n"]

    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "criar_dimensao":
            nome = (request.form.get("nome") or "").strip()
            if not nome:
                erro = "Informe o nome da dimensao."
            else:
                try:
                    cur.execute(
                        "INSERT INTO cartao.dimensao (nome, obrigatoria, ordem) VALUES (%s,%s,%s) RETURNING id;",
                        (nome, request.form.get("obrigatoria") == "on", 99),
                    )
                    nova_id = cur.fetchone()["id"]
                    conn.commit()
                    registrar_mudanca_auditoria("Dimensão", None, {
                        "id": nova_id, "nome": nome,
                        "obrigatoria": request.form.get("obrigatoria") == "on",
                    })
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    erro = f"Já existe uma dimensão chamada '{nome}'."
        elif acao == "editar_dimensao":
            nome = (request.form.get("nome") or "").strip()
            if not nome:
                erro = "Informe o nome da dimensao."
            else:
                try:
                    cur.execute(
                        "SELECT id, nome, obrigatoria FROM cartao.dimensao WHERE id=%s;",
                        (request.form.get("dimensao_id"),),
                    )
                    anterior = cur.fetchone()
                    obrigatoria = request.form.get("obrigatoria") == "on"
                    cur.execute(
                        "UPDATE cartao.dimensao SET nome=%s, obrigatoria=%s WHERE id=%s;",
                        (nome, obrigatoria, request.form.get("dimensao_id")),
                    )
                    conn.commit()
                    if anterior and cur.rowcount:
                        registrar_mudanca_auditoria("Nome da dimensão", anterior["nome"], nome)
                        registrar_mudanca_auditoria("Dimensão obrigatória", bool(anterior["obrigatoria"]), obrigatoria)
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    erro = f"Já existe uma dimensão chamada '{nome}'."
        elif acao == "excluir_dimensao":
            dimensao_id = request.form.get("dimensao_id")
            qtd = contar_uso_dimensao(dimensao_id)
            if qtd > 0:
                erro = f"Não é possível remover: existem {qtd} lançamento(s) vinculados a valores desta dimensão. Reclassifique-os primeiro."
            else:
                cur.execute(
                    "SELECT id, nome, obrigatoria FROM cartao.dimensao WHERE id=%s;",
                    (dimensao_id,),
                )
                anterior = cur.fetchone()
                cur.execute("DELETE FROM cartao.dimensao WHERE id=%s;", (dimensao_id,))
                conn.commit()
                if anterior and cur.rowcount:
                    registrar_mudanca_auditoria("Dimensão", {
                        "id": anterior["id"], "nome": anterior["nome"],
                        "obrigatoria": bool(anterior["obrigatoria"]),
                    }, None)
        elif acao == "criar_valor":
            nome = (request.form.get("nome") or "").strip()
            if nome:
                try:
                    cur.execute(
                        "INSERT INTO cartao.dimensao_valor (dimensao_id, nome) VALUES (%s,%s) RETURNING id;",
                        (request.form.get("dimensao_id"), nome),
                    )
                    novo_id = cur.fetchone()["id"]
                    conn.commit()
                    registrar_mudanca_auditoria("Valor de dimensão", None, {
                        "id": novo_id,
                        "dimensao_id": request.form.get("dimensao_id"),
                        "nome": nome,
                    })
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    erro = f"Já existe o valor '{nome}' nessa dimensão."
        elif acao == "editar_valor":
            def to_num(v):
                v = (v or "").strip().replace(",", ".")
                return float(v) if v else None
            valor_id = request.form.get("valor_id")
            cur.execute(
                "SELECT id, dimensao_id, nome, teto_mensal, teto_anual, icone, portfolio_valor_id "
                "FROM cartao.dimensao_valor WHERE id=%s;",
                (valor_id,),
            )
            anterior = cur.fetchone()
            nome_novo = (request.form.get("nome") or "").strip()
            teto_mensal_novo = to_num(request.form.get("teto_mensal"))
            teto_anual_novo = to_num(request.form.get("teto_anual"))
            icone_novo = ((request.form.get("icone") or "").strip() or None)
            # so a dimensao Projeto manda esse campo no formulario (ver
            # dimensoes.html); para qualquer outra dimensao o form nao tem o
            # select, entao "portfolio_valor_id" vem ausente e cai em None,
            # que e' o comportamento certo (nenhum vinculo).
            portfolio_valor_id_novo = request.form.get("portfolio_valor_id") or None
            cur.execute(
                "UPDATE cartao.dimensao_valor SET nome=%s, teto_mensal=%s, teto_anual=%s, "
                "icone=%s, portfolio_valor_id=%s WHERE id=%s;",
                (
                    nome_novo,
                    teto_mensal_novo,
                    teto_anual_novo,
                    # varchar(8): cabe um emoji (que pode ter varios code points)
                    icone_novo,
                    portfolio_valor_id_novo,
                    valor_id,
                ),
            )
            conn.commit()
            if anterior and cur.rowcount:
                registrar_mudanca_auditoria("Nome do valor", anterior["nome"], nome_novo)
                registrar_mudanca_auditoria("Teto mensal", float(anterior["teto_mensal"]) if anterior["teto_mensal"] is not None else None, teto_mensal_novo)
                registrar_mudanca_auditoria("Teto anual", float(anterior["teto_anual"]) if anterior["teto_anual"] is not None else None, teto_anual_novo)
                registrar_mudanca_auditoria("Ícone", anterior["icone"], icone_novo)
                anterior_portfolio = str(anterior["portfolio_valor_id"]) if anterior["portfolio_valor_id"] else None
                registrar_mudanca_auditoria("Portfólio padrão do projeto", anterior_portfolio, portfolio_valor_id_novo)
        elif acao == "excluir_valor":
            valor_id = request.form.get("valor_id")
            qtd = contar_uso_valor(valor_id)
            if qtd > 0:
                erro = f"Não é possível remover: existem {qtd} lançamento(s) vinculados a este valor. Reclassifique-os primeiro."
            else:
                cur.execute(
                    "SELECT id, dimensao_id, nome, teto_mensal, teto_anual, icone "
                    "FROM cartao.dimensao_valor WHERE id=%s;",
                    (valor_id,),
                )
                anterior = cur.fetchone()
                cur.execute("DELETE FROM cartao.dimensao_valor WHERE id=%s;", (valor_id,))
                conn.commit()
                if anterior and cur.rowcount:
                    registrar_mudanca_auditoria("Valor de dimensão", {
                        "id": anterior["id"], "dimensao_id": anterior["dimensao_id"],
                        "nome": anterior["nome"],
                        "teto_mensal": float(anterior["teto_mensal"]) if anterior["teto_mensal"] is not None else None,
                        "teto_anual": float(anterior["teto_anual"]) if anterior["teto_anual"] is not None else None,
                        "icone": anterior["icone"],
                    }, None)
        if erro:
            marcar_falha_auditoria()

    cur.execute("SELECT id, nome, obrigatoria, ordem FROM cartao.dimensao ORDER BY ordem, nome;")
    dims = cur.fetchall()
    cur.execute(
        "SELECT id, dimensao_id, nome, teto_mensal, teto_anual, portfolio_valor_id "
        "FROM cartao.dimensao_valor ORDER BY nome;"
    )
    valores_db = cur.fetchall()
    # opcoes pro select "Portfolio padrao" que so aparece nos valores da
    # dimensao Projeto (ver dimensoes.html) - lista todos os valores da
    # dimensao Portfolio, se ela existir.
    cur.execute(
        "SELECT dv.id, dv.nome FROM cartao.dimensao_valor dv "
        "JOIN cartao.dimensao d ON d.id = dv.dimensao_id AND lower(d.nome) = lower('Portfólio') "
        "ORDER BY dv.nome;"
    )
    portfolio_opcoes = cur.fetchall()

    # gasto do mes e do ano corrente por valor de dimensao, pra comparar com o teto
    mes_atual = datetime.now().strftime("%Y-%m")
    ano_atual = datetime.now().strftime("%Y")
    cur.execute(
        "SELECT td.valor_id, "
        f"SUM(CASE WHEN to_char(t.data_transacao,'YYYY-MM') = %s THEN {VAL_DESPESA} ELSE 0 END) AS gasto_mes, "
        f"SUM(CASE WHEN to_char(t.data_transacao,'YYYY') = %s THEN {VAL_DESPESA} ELSE 0 END) AS gasto_ano "
        f"FROM cartao.transacao_dimensao td "
        f"JOIN cartao.transacao t ON t.transacao_id::text = td.transacao_id {JOIN_NATUREZA} "
        f"WHERE {NATUREZA_SQL} = 'despesa' AND COALESCE(t.duplicada, false) = false "
        "GROUP BY td.valor_id;",
        (mes_atual, ano_atual),
    )
    gasto_por_valor = {r["valor_id"]: r for r in cur.fetchall()}

    # contagem de uso por valor, pra travar a exclusao de valor com lancamento vinculado
    cur.execute("SELECT valor_id, COUNT(*) AS n FROM cartao.lancamento_financeiro_dimensao WHERE valor_id IS NOT NULL GROUP BY valor_id;")
    usados_valor = {r["valor_id"]: r["n"] for r in cur.fetchall()}
    cur.close()
    conn.close()

    valores_por_dim = {}
    for v in valores_db:
        valores_por_dim.setdefault(v["dimensao_id"], []).append(v)

    # soma de uso por dimensao (todos os valores dela), pra travar excluir_dimensao
    usados_dimensao = {}
    for v in valores_db:
        n = usados_valor.get(v["id"])
        if n:
            usados_dimensao[v["dimensao_id"]] = usados_dimensao.get(v["dimensao_id"], 0) + n

    # gasto ja somado por valor de dimensao, pro template so exibir
    gastos = {
        vid: {"mes": float(g["gasto_mes"] or 0), "ano": float(g["gasto_ano"] or 0)}
        for vid, g in gasto_por_valor.items()
    }

    return render_template(
        "dimensoes.html",
        titulo="Gerenciar Dimensões",
        topbar=topbar_html("Gerenciar Dimensões", "dimensoes"),
        erro=erro,
        dimensoes=dims,
        valores_por_dim=valores_por_dim,
        usados_valor=usados_valor,
        usados_dimensao=usados_dimensao,
        gastos=gastos,
        portfolio_opcoes=portfolio_opcoes,
    )


@bp.route("/regras", methods=["GET", "POST"])
@requer("cadastros")
def regras_view():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    erro = None

    def estado_regra(regra_id):
        cur.execute(
            "SELECT id, padrao, categoria, valor_operador, valor_limite, account_id "
            "FROM cartao.regra_classificacao WHERE id=%s;",
            (regra_id,),
        )
        regra = cur.fetchone()
        if not regra:
            return None
        cur.execute(
            "SELECT dimensao_id, valor_id FROM cartao.regra_dimensao_valor "
            "WHERE regra_id=%s ORDER BY dimensao_id;",
            (regra_id,),
        )
        return {
            "id": regra["id"],
            "padrao": regra["padrao"],
            "valor_operador": regra["valor_operador"],
            "valor_limite": float(regra["valor_limite"]) if regra["valor_limite"] is not None else None,
            "account_id": str(regra["account_id"]) if regra["account_id"] else None,
            "categoria": {
                "chave": regra["categoria"],
                "nome": cat_pt_puro(regra["categoria"]),
            } if regra["categoria"] else None,
            "dimensoes": {
                str(r["dimensao_id"]): r["valor_id"] for r in cur.fetchall()
            },
        }

    if request.method == "POST":
        acao = request.form.get("acao")
        # Origem da regra. Vazio = vale para todas, que e exatamente como as
        # regras se comportavam antes deste campo - regra antiga nao muda.
        account_id = (request.form.get("account_id") or "").strip() or None
        if acao == "criar_regra":
            padrao = (request.form.get("padrao") or "").strip()
            categoria = request.form.get("categoria") or ""
            valor_operador, valor_limite, erro_valor = _filtro_valor(request.form)
            cur.execute(
                "SELECT id FROM cartao.regra_classificacao WHERE lower(padrao) = lower(%s) "
                "AND valor_operador IS NOT DISTINCT FROM %s "
                "AND valor_limite IS NOT DISTINCT FROM %s;",
                (padrao, valor_operador, valor_limite),
            )
            repetida = cur.fetchone() if padrao else None
            if not padrao:
                erro = "Informe o texto/padrao a procurar na descricao."
            elif erro_valor:
                erro = erro_valor
            elif repetida:
                erro = "Já existe uma regra com o mesmo texto e a mesma condição de valor."
            else:
                cur.execute(
                    "INSERT INTO cartao.regra_classificacao "
                    "(padrao, categoria, valor_operador, valor_limite, account_id) "
                    "VALUES (%s,%s,%s,%s,%s) RETURNING id;",
                    (padrao, categoria, valor_operador, valor_limite, account_id),
                )
                regra_id = cur.fetchone()["id"]
                for chave, valor in request.form.items():
                    if chave.startswith("dim_") and valor:
                        dim_id = chave.replace("dim_", "")
                        cur.execute(
                            "INSERT INTO cartao.regra_dimensao_valor (regra_id, dimensao_id, valor_id) VALUES (%s,%s,%s);",
                            (regra_id, dim_id, valor),
                        )
                conn.commit()
                registrar_mudanca_auditoria("Regra automática", None, estado_regra(regra_id))
        elif acao == "excluir_regra":
            regra_id = request.form.get("regra_id")
            anterior = estado_regra(regra_id)
            cur.execute(
                "UPDATE cartao.transacao SET regra_aplicada_id = NULL "
                "WHERE regra_aplicada_id = %s;",
                (regra_id,),
            )
            cur.execute("DELETE FROM cartao.regra_classificacao WHERE id=%s;", (regra_id,))
            conn.commit()
            if anterior and cur.rowcount:
                registrar_mudanca_auditoria("Regra automática", anterior, None)
        elif acao == "reaplicar_regra":
            # libera as transacoes pendentes que essa regra ja tinha marcado, para reclassificar no proximo acesso
            cur.execute(
                "UPDATE cartao.transacao SET regra_aplicada_id = NULL "
                "WHERE regra_aplicada_id = %s AND conferida = false;",
                (request.form.get("regra_id"),),
            )
            liberados = cur.rowcount
            conn.commit()
            if liberados:
                registrar_mudanca_auditoria(
                    "Lançamentos liberados para reaplicar regra", 0, liberados,
                )
        elif acao == "editar_regra":
            regra_id = request.form.get("regra_id")
            anterior = estado_regra(regra_id)
            padrao = (request.form.get("padrao") or "").strip()
            categoria = request.form.get("categoria")
            valor_operador, valor_limite, erro_valor = _filtro_valor(request.form)
            if not padrao:
                erro = "Informe o texto a ser procurado na descricao."
            elif erro_valor:
                erro = erro_valor
            else:
                cur.execute(
                    "SELECT id FROM cartao.regra_classificacao WHERE id <> %s "
                    "AND lower(padrao)=lower(%s) "
                    "AND valor_operador IS NOT DISTINCT FROM %s "
                    "AND valor_limite IS NOT DISTINCT FROM %s;",
                    (regra_id, padrao, valor_operador, valor_limite),
                )
                if cur.fetchone():
                    erro = "Já existe uma regra com o mesmo texto e a mesma condição de valor."
                    conn.rollback()
                else:
                    cur.execute(
                        "UPDATE cartao.regra_classificacao SET padrao=%s, categoria=%s, "
                        "valor_operador=%s, valor_limite=%s, account_id=%s WHERE id=%s;",
                        (padrao, categoria, valor_operador, valor_limite, account_id, regra_id),
                    )
            if not erro:
                cur.execute("DELETE FROM cartao.regra_dimensao_valor WHERE regra_id = %s;", (regra_id,))
                for chave, valor in request.form.items():
                    if chave.startswith("dim_") and valor:
                        dim_id = chave.split("_", 1)[1]
                        cur.execute(
                            "INSERT INTO cartao.regra_dimensao_valor (regra_id, dimensao_id, valor_id) VALUES (%s, %s, %s);",
                            (regra_id, dim_id, valor),
                        )
                # libera pendentes ja tocados por essa regra para reclassificar com os novos valores
                cur.execute(
                    "UPDATE cartao.transacao SET regra_aplicada_id = NULL "
                    "WHERE regra_aplicada_id = %s AND conferida = false;",
                    (regra_id,),
                )
                conn.commit()
                registrar_mudanca_auditoria(
                    "Regra automática", anterior, estado_regra(regra_id),
                )
        if erro:
            marcar_falha_auditoria()

    aplicar_regras(cur)
    conn.commit()

    cur.execute(
        "SELECT id, padrao, categoria, ordem, valor_operador, valor_limite, account_id "
        "FROM cartao.regra_classificacao WHERE COALESCE(ativa,true)=true ORDER BY ordem, id;"
    )
    regras_db = cur.fetchall()
    cur.execute("SELECT regra_id, dimensao_id, valor_id FROM cartao.regra_dimensao_valor;")
    dim_por_regra = {}
    for r in cur.fetchall():
        dim_por_regra.setdefault(r["regra_id"], {})[r["dimensao_id"]] = r["valor_id"]

    cur.execute("SELECT id, nome, obrigatoria FROM cartao.dimensao ORDER BY ordem, nome;")
    dimensoes = cur.fetchall()
    cur.execute("SELECT id, dimensao_id, nome, icone FROM cartao.dimensao_valor ORDER BY nome;")
    valores_por_dim = {}
    for v in cur.fetchall():
        valores_por_dim.setdefault(v["dimensao_id"], []).append(v)

    cur.execute("SELECT COUNT(*) AS n FROM cartao.transacao WHERE regra_aplicada_id IS NOT NULL;")
    total_aplicadas = cur.fetchone()["n"]

    # Mesmo helper que a tela de Lancamentos usa para montar o filtro de origem:
    # duas listas de conta divergiriam no primeiro banco novo.
    contas_by_id, _origem_opcoes = carregar_origens(cur)
    origens = sorted(
        ({"account_id": aid, "nome": dados["label"]} for aid, dados in contas_by_id.items()),
        key=lambda o: o["nome"],
    )

    prefill = {
        "padrao": "", "valor_operador": "", "valor_limite": "",
        "dimensoes": {}, "categoria": "", "account_id": "",
    }
    transacao_prefill = (request.args.get("transacao") or "").strip()
    if transacao_prefill:
        cur.execute(
            "SELECT descricao, ABS(COALESCE(valor_brl,valor_original)) AS valor, categoria, "
            "account_id "
            "FROM cartao.transacao WHERE transacao_id::text=%s;",
            (transacao_prefill,),
        )
        tx = cur.fetchone()
        if tx:
            prefill.update({
                "padrao": tx["descricao"] or "",
                "valor_limite": f'{tx["valor"]:.2f}' if tx["valor"] is not None else "",
                "categoria": tx["categoria"] or "",
                # a regra nasce restrita a origem do lancamento que a inspirou:
                # e o padrao seguro, e um clique desfaz
                "account_id": str(tx["account_id"]) if tx["account_id"] else "",
            })
            cur.execute(
                "SELECT dimensao_id, valor_id FROM cartao.transacao_dimensao WHERE transacao_id=%s;",
                (transacao_prefill,),
            )
            prefill["dimensoes"] = {r["dimensao_id"]: r["valor_id"] for r in cur.fetchall()}

    todas_categorias = _categorias_para_regras()

    cur.close()
    conn.close()

    categorias = [{"chave": c, "nome": cat_pt_puro(c)} for c in todas_categorias]

    editar_id = request.args.get("editar")
    try:
        editar_id = int(editar_id) if editar_id else None
    except ValueError:
        editar_id = None

    regras = []
    for r in regras_db:
        selecionadas = dim_por_regra.get(r["id"], {})
        dims_txt = []
        for d in dimensoes:
            vid = selecionadas.get(d["id"])
            if vid:
                nome_valor = next(
                    (v["nome"] for v in valores_por_dim.get(d["id"], []) if v["id"] == vid), "?"
                )
                dims_txt.append(f'{d["nome"]}: {nome_valor}')
        regras.append({
            "id": r["id"],
            "padrao": r["padrao"],
            "categoria": r["categoria"],
            "categoria_nome": cat_pt_puro(r["categoria"]),
            "valor_operador": r["valor_operador"] or "",
            "valor_limite": float(r["valor_limite"]) if r["valor_limite"] is not None else None,
            "valor_condicao": (
                f'{OPERADORES_VALOR[r["valor_operador"]][0]} R$ {valor_pt(r["valor_limite"])}'
                if r["valor_operador"] in OPERADORES_VALOR and r["valor_limite"] is not None else "qualquer valor"
            ),
            "dims_txt": ", ".join(dims_txt) or "-",
            "dims_selecionadas": selecionadas,
            "account_id": str(r["account_id"]) if r["account_id"] else "",
            "origem_nome": (
                contas_by_id.get(str(r["account_id"]), {}).get("label", "origem removida")
                if r["account_id"] else "todas as origens"
            ),
        })

    return render_template(
        "regras.html",
        titulo="Regras Automáticas",
        topbar=topbar_html("Regras Automáticas", "regras"),
        erro=erro,
        regras=regras,
        categorias=categorias,
        dimensoes=dimensoes,
        valores_por_dim=valores_por_dim,
        total_aplicadas=total_aplicadas,
        editar_id=editar_id,
        prefill=prefill,
        operadores_valor=OPERADORES_VALOR,
        origens=origens,
    )


@bp.route("/api/regras/preview")
@requer("cadastros")
def regra_preview():
    padrao = (request.args.get("padrao") or "").strip()
    operador, limite, erro = _filtro_valor(request.args)
    if not padrao:
        return jsonify({"ok": True, "total": 0, "lancamentos": []})
    if erro:
        return jsonify({"ok": False, "erro": erro}), 400
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    condicao = _condicao_valor_sql(operador)
    # A previa precisa aplicar a MESMA restricao de origem que `aplicar_regras`
    # aplica (`r.account_id IS NULL OR r.account_id = t.account_id`). Sem isso a
    # previa promete N lancamentos e a regra alcanca outro conjunto - e o
    # numero que o usuario usa para decidir seria falso.
    account_id = (request.args.get("account_id") or "").strip() or None
    condicao_origem = " AND t.account_id = %s" if account_id else ""
    params = [f"%{padrao}%"] + ([limite] if operador else []) + ([account_id] if account_id else [])
    cur.execute(
        "SELECT COUNT(*) AS total FROM cartao.transacao t "
        "WHERE t.descricao ILIKE %s AND t.regra_aplicada_id IS NULL "
        "AND t.conferida=false AND COALESCE(t.categoria_manual,false)=false"
        + condicao + condicao_origem,
        params,
    )
    total = cur.fetchone()["total"]
    cur.execute(
        "SELECT to_char(t.data_transacao AT TIME ZONE 'America/Sao_Paulo','DD/MM/YYYY') AS data, "
        "t.descricao, ABS(COALESCE(t.valor_brl,t.valor_original)) AS valor "
        "FROM cartao.transacao t WHERE t.descricao ILIKE %s "
        "AND t.regra_aplicada_id IS NULL AND t.conferida=false "
        "AND COALESCE(t.categoria_manual,false)=false" + condicao + condicao_origem +
        " ORDER BY t.data_transacao DESC LIMIT 10;",
        params,
    )
    lancamentos = [
        {"data": r["data"], "descricao": r["descricao"], "valor": float(r["valor"] or 0)}
        for r in cur.fetchall()
    ]
    cur.close()
    conn.close()
    return jsonify({"ok": True, "total": total, "lancamentos": lancamentos})


def _estado_centro_custo(cur):
    """Tudo que a tela de Centro de Custos precisa, numa estrutura so.

    E a MESMA estrutura que toda escrita devolve: a tela nunca remonta no
    cliente o que o servidor decidiu. Isso importa porque a regra vencedora
    depende das outras regras da mesma categoria - prever o resultado no JS
    daria a resposta errada na primeira regra com condicao.
    """
    arvore = arvore_centro_custo(cur)

    cur.execute("SELECT id, nome FROM cartao.dimensao ORDER BY ordem, nome;")
    dims = cur.fetchall()
    cur.execute("SELECT id, dimensao_id, nome FROM cartao.dimensao_valor;")
    valores_por_dim = {}
    for v in cur.fetchall():
        valores_por_dim.setdefault(v["dimensao_id"], []).append({
            "id": v["id"], "nome": v["nome"],
        })
    for lista in valores_por_dim.values():
        lista.sort(key=lambda x: chave_alfa(x["nome"]))
    dimensoes = [{
        "id": d["id"],
        "nome": d["nome"],
        "valores": valores_por_dim.get(d["id"], []),
    } for d in dims]

    vinculadas = {r["categoria"] for g in arvore for s in g["subgrupos"] for r in s["regras"]}
    todas = sorted(
        (set(CATEGORIA_PT) | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB))
        - CATEGORIAS_NEUTRAS_PADRAO - CATEGORIAS_OCULTAS,
        key=lambda c: chave_alfa(cat_pt_puro(c)),
    )
    return {
        "grupos": arvore,
        "dimensoes": dimensoes,
        "categorias": [{
            "chave": c,
            "nome": cat_pt_puro(c),
            "vinculada": c in vinculadas,
        } for c in todas],
    }


def _condicoes_do_pedido(cur, bruto):
    """Le e valida as condicoes de dimensao vindas da tela.

    Uma condicao por dimensao, no maximo: duas exigencias na mesma dimensao
    nunca casariam ao mesmo tempo (um lancamento tem um valor por dimensao), e
    a regra ficaria morta sem dizer por que.
    """
    condicoes = []
    vistas = set()
    for c in (bruto or []):
        try:
            dim_id = int(c.get("dimensao_id"))
            valor_id = int(c.get("valor_id"))
        except (TypeError, ValueError):
            raise ValueError("Condição inválida: escolha a dimensão e o valor.")
        if dim_id in vistas:
            raise ValueError("Só cabe uma condição por dimensão.")
        cur.execute(
            "SELECT 1 FROM cartao.dimensao_valor WHERE id = %s AND dimensao_id = %s;",
            (valor_id, dim_id),
        )
        if not cur.fetchone():
            raise ValueError("Esse valor não pertence à dimensão escolhida.")
        vistas.add(dim_id)
        condicoes.append((dim_id, valor_id))
    return condicoes


def _regra_ja_existe(cur, categoria, condicoes, exceto_id=None):
    """Ja existe regra com essa categoria e EXATAMENTE essas condicoes?

    Duas regras iguais sao duas respostas para a mesma pergunta: o desempate
    por id escolheria uma em silencio, e o DRE mudaria sem ninguem decidir.
    """
    cur.execute(
        "SELECT r.id FROM cartao.centro_regra r WHERE r.categoria = %s "
        "AND (SELECT count(*) FROM cartao.centro_regra_dimensao rd WHERE rd.regra_id = r.id) = %s;",
        (categoria, len(condicoes)),
    )
    candidatas = [r["id"] for r in cur.fetchall() if r["id"] != exceto_id]
    if not candidatas:
        return None
    alvo = {(d, v) for d, v in condicoes}
    for rid in candidatas:
        cur.execute(
            "SELECT dimensao_id, valor_id FROM cartao.centro_regra_dimensao WHERE regra_id = %s;",
            (rid,),
        )
        if {(r["dimensao_id"], r["valor_id"]) for r in cur.fetchall()} == alvo:
            return rid
    return None


def _gravar_condicoes(cur, regra_id, condicoes):
    cur.execute("DELETE FROM cartao.centro_regra_dimensao WHERE regra_id = %s;", (regra_id,))
    for dim_id, valor_id in condicoes:
        cur.execute(
            "INSERT INTO cartao.centro_regra_dimensao (regra_id, dimensao_id, valor_id) "
            "VALUES (%s,%s,%s);",
            (regra_id, dim_id, valor_id),
        )


def _rotulo_regra(cur, regra_id):
    """Texto da regra para a auditoria - o mesmo formato da tela."""
    cur.execute(
        "SELECT r.categoria, d.nome AS dimensao, dv.nome AS valor "
        "FROM cartao.centro_regra r "
        "LEFT JOIN cartao.centro_regra_dimensao rd ON rd.regra_id = r.id "
        "LEFT JOIN cartao.dimensao d ON d.id = rd.dimensao_id "
        "LEFT JOIN cartao.dimensao_valor dv ON dv.id = rd.valor_id "
        "WHERE r.id = %s ORDER BY d.nome;",
        (regra_id,),
    )
    linhas = cur.fetchall()
    if not linhas:
        return ""
    detalhe = " · ".join(f'{l["dimensao"]}: {l["valor"]}' for l in linhas if l["dimensao"])
    nome = cat_pt_puro(linhas[0]["categoria"])
    return f"{nome} · {detalhe}" if detalhe else nome


def _destino_regra(cur, regra_id):
    cur.execute(
        "SELECT g.nome AS grupo, s.nome AS subgrupo FROM cartao.centro_regra r "
        "JOIN cartao.subgrupo_custo s ON s.id = r.subgrupo_id "
        "JOIN cartao.grupo_custo g ON g.id = s.grupo_id WHERE r.id = %s;",
        (regra_id,),
    )
    linha = cur.fetchone()
    return f'{linha["grupo"]} › {linha["subgrupo"]}' if linha else None


def _aplicar_acao_centro_custo(cur, acao, dados):
    """Executa uma acao da tela. Erro de uso vira ValueError com texto em portugues."""
    def _txt(campo, obrigatorio=True):
        valor = (dados.get(campo) or "").strip()
        if obrigatorio and not valor:
            raise ValueError("Informe o nome.")
        return valor

    def _id(campo):
        try:
            return int(dados.get(campo))
        except (TypeError, ValueError):
            raise ValueError("Item não encontrado.")

    if acao == "criar_grupo":
        nome = _txt("nome")
        cur.execute("INSERT INTO cartao.grupo_custo (nome) VALUES (%s) RETURNING id;", (nome,))
        registrar_mudanca_auditoria("Centro de custo", None, {
            "id": _campo(cur.fetchone(), "id", 0), "nome": nome,
        })
        return f'Centro de custo "{nome}" criado.'

    if acao == "renomear_grupo":
        grupo_id, nome = _id("grupo_id"), _txt("nome")
        cur.execute("SELECT nome FROM cartao.grupo_custo WHERE id = %s;", (grupo_id,))
        anterior = cur.fetchone()
        cur.execute("UPDATE cartao.grupo_custo SET nome = %s WHERE id = %s;", (nome, grupo_id))
        if anterior and anterior["nome"] != nome:
            registrar_mudanca_auditoria("Nome do centro de custo", anterior["nome"], nome)
        return None

    if acao == "excluir_grupo":
        grupo_id = _id("grupo_id")
        cur.execute("SELECT id, nome FROM cartao.grupo_custo WHERE id = %s;", (grupo_id,))
        anterior = cur.fetchone()
        cur.execute("DELETE FROM cartao.grupo_custo WHERE id = %s;", (grupo_id,))
        if anterior:
            registrar_mudanca_auditoria("Centro de custo", dict(anterior), None)
        return f'Centro de custo "{anterior["nome"]}" excluído.' if anterior else None

    if acao == "criar_subgrupo":
        grupo_id, nome = _id("grupo_id"), _txt("nome")
        cur.execute(
            "INSERT INTO cartao.subgrupo_custo (grupo_id, nome) VALUES (%s,%s) RETURNING id;",
            (grupo_id, nome),
        )
        registrar_mudanca_auditoria("Subgrupo de custo", None, {
            "id": _campo(cur.fetchone(), "id", 0), "grupo_id": grupo_id, "nome": nome,
        })
        return f'Subgrupo "{nome}" criado.'

    if acao == "renomear_subgrupo":
        subgrupo_id, nome = _id("subgrupo_id"), _txt("nome")
        cur.execute("SELECT nome FROM cartao.subgrupo_custo WHERE id = %s;", (subgrupo_id,))
        anterior = cur.fetchone()
        cur.execute("UPDATE cartao.subgrupo_custo SET nome = %s WHERE id = %s;", (nome, subgrupo_id))
        if anterior and anterior["nome"] != nome:
            registrar_mudanca_auditoria("Nome do subgrupo", anterior["nome"], nome)
        return None

    if acao == "excluir_subgrupo":
        subgrupo_id = _id("subgrupo_id")
        cur.execute(
            "SELECT id, grupo_id, nome FROM cartao.subgrupo_custo WHERE id = %s;",
            (subgrupo_id,),
        )
        anterior = cur.fetchone()
        cur.execute("DELETE FROM cartao.subgrupo_custo WHERE id = %s;", (subgrupo_id,))
        if anterior:
            registrar_mudanca_auditoria("Subgrupo de custo", dict(anterior), None)
        return f'Subgrupo "{anterior["nome"]}" excluído.' if anterior else None

    if acao == "criar_regra":
        categoria = _txt("categoria")
        subgrupo_id = _id("subgrupo_id")
        condicoes = _condicoes_do_pedido(cur, dados.get("condicoes"))
        if _regra_ja_existe(cur, categoria, condicoes):
            raise ValueError(
                f'Já existe essa mesma regra para "{cat_pt_puro(categoria)}" — '
                "duas iguais dariam duas respostas para a mesma pergunta."
            )
        cur.execute(
            "INSERT INTO cartao.centro_regra (categoria, subgrupo_id) VALUES (%s,%s) RETURNING id;",
            (categoria, subgrupo_id),
        )
        regra_id = _campo(cur.fetchone(), "id", 0)
        _gravar_condicoes(cur, regra_id, condicoes)
        registrar_mudanca_auditoria(
            f"Centro de custo de {cat_pt_puro(categoria)}", None,
            {"regra": _rotulo_regra(cur, regra_id), "destino": _destino_regra(cur, regra_id)},
        )
        return f'"{_rotulo_regra(cur, regra_id)}" vinculada.'

    if acao == "mover_regra":
        regra_id, subgrupo_id = _id("regra_id"), _id("subgrupo_id")
        antes = _destino_regra(cur, regra_id)
        rotulo = _rotulo_regra(cur, regra_id)
        cur.execute(
            "UPDATE cartao.centro_regra SET subgrupo_id = %s WHERE id = %s;",
            (subgrupo_id, regra_id),
        )
        depois = _destino_regra(cur, regra_id)
        if antes != depois:
            registrar_mudanca_auditoria(f"Centro de custo de {rotulo}", antes, depois)
        return None

    if acao == "atualizar_condicoes":
        regra_id = _id("regra_id")
        cur.execute("SELECT categoria FROM cartao.centro_regra WHERE id = %s;", (regra_id,))
        linha = cur.fetchone()
        if not linha:
            raise ValueError("Regra não encontrada.")
        condicoes = _condicoes_do_pedido(cur, dados.get("condicoes"))
        if _regra_ja_existe(cur, linha["categoria"], condicoes, exceto_id=regra_id):
            raise ValueError("Já existe outra regra igual a essa.")
        antes = _rotulo_regra(cur, regra_id)
        _gravar_condicoes(cur, regra_id, condicoes)
        depois = _rotulo_regra(cur, regra_id)
        if antes != depois:
            registrar_mudanca_auditoria("Condições da regra de centro de custo", antes, depois)
        return None

    if acao == "excluir_regra":
        regra_id = _id("regra_id")
        rotulo = _rotulo_regra(cur, regra_id)
        destino = _destino_regra(cur, regra_id)
        cur.execute("DELETE FROM cartao.centro_regra WHERE id = %s;", (regra_id,))
        if rotulo:
            registrar_mudanca_auditoria(f"Centro de custo de {rotulo}", destino, None)
        return None

    raise ValueError("Ação desconhecida.")


@bp.route("/grupos")
@requer("cadastros")
def grupos_view():
    """Centro de Custos: centro > subgrupo > regras de vinculo.

    A tela e toda AJAX (static/centro_custos.js): cada mudanca salva sozinha e
    a pagina nunca recarrega. Esta rota so entrega o estado inicial; quem grava
    e `/api/centro-custo`, que devolve a arvore recalculada.
    """
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    estado = _estado_centro_custo(cur)
    cur.close()
    conn.close()
    return render_template(
        "grupos.html",
        titulo="Centro de Custos",
        topbar=topbar_html("Centro de Custos", "grupos"),
        # json_script e nao |tojson: dentro de <script> o Jinja nao escapa "</",
        # e um nome de categoria com "</script>" fecharia a tag (secao 9.2)
        estado_json=json_script(estado),
    )


@bp.route("/api/centro-custo", methods=["POST"])
@requer("cadastros")
def api_centro_custo():
    """Toda escrita da tela de Centro de Custos passa por aqui.

    Uma rota so, com a acao no corpo, e a resposta e sempre o estado inteiro
    recalculado pelo servidor (secao 7.2-A: um segundo caminho de gravacao
    comeca igual e diverge na primeira regra nova).
    """
    dados = request.get_json(silent=True) or {}
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        aviso = _aplicar_acao_centro_custo(cur, dados.get("acao"), dados)
        conn.commit()
    except ValueError as e:
        conn.rollback()
        cur.close()
        conn.close()
        marcar_falha_auditoria()
        return jsonify({"ok": False, "erro": str(e)}), 400
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        cur.close()
        conn.close()
        marcar_falha_auditoria()
        return jsonify({"ok": False, "erro": "Já existe um item com esse nome aqui."}), 400
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        marcar_falha_auditoria()
        return jsonify({"ok": False, "erro": str(e)}), 500
    estado = _estado_centro_custo(cur)
    cur.close()
    conn.close()
    return jsonify({"ok": True, "aviso": aviso, "estado": estado})


@bp.route("/contas", methods=["GET", "POST"])
@requer("cadastros")
def contas_view():
    """Configuracoes de Contas / Cartao - centraliza tudo que descreve a origem do
    dinheiro: de quem e a conexao bancaria, e o apelido de cada cartao (fisico,
    virtual, adicional). As datas de fatura vem do Pluggy e sao so leitura."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    aviso = erro = None

    if request.method == "POST":
        acao = request.form.get("acao") or "titular"
        try:
            if acao == "salvar_cartao":
                final4 = (request.form.get("final4") or "").strip()
                prefixo = (request.form.get("prefixo") or "").strip()
                if not (final4.isdigit() and len(final4) == 4):
                    erro = "Os 4 últimos dígitos devem ser exatamente 4 números."
                else:
                    cur.execute(
                        "SELECT prefixo FROM cartao.cartao_nome WHERE final4 = %s;",
                        (final4,),
                    )
                    cartao_anterior = cur.fetchone()
                if not erro and not prefixo:
                    # nome em branco = remover o apelido, volta a aparecer como "final NNNN"
                    cur.execute("DELETE FROM cartao.cartao_nome WHERE final4 = %s;", (final4,))
                    conn.commit()
                    registrar_mudanca_auditoria(
                        f"Apelido do cartão final {final4}",
                        cartao_anterior["prefixo"] if cartao_anterior else None,
                        None,
                    )
                    aviso = f"Nome do cartão final {final4} removido."
                elif not erro:
                    cur.execute(
                        "SELECT final4 FROM cartao.cartao_nome WHERE lower(prefixo) = lower(%s) "
                        "AND final4 <> %s;",
                        (prefixo, final4),
                    )
                    ja_usado = cur.fetchone()
                    if ja_usado:
                        # dois cartoes com o mesmo apelido ficam indistinguiveis na
                        # coluna Origem e no filtro
                        erro = (
                            f'O nome "{prefixo}" já é usado pelo cartão final '
                            f'{ja_usado["final4"]}.'
                        )
                    else:
                        cur.execute(
                            "INSERT INTO cartao.cartao_nome (final4, prefixo) VALUES (%s,%s) "
                            "ON CONFLICT (final4) DO UPDATE SET prefixo = EXCLUDED.prefixo;",
                            (final4, prefixo),
                        )
                        conn.commit()
                        registrar_mudanca_auditoria(
                            f"Apelido do cartão final {final4}",
                            cartao_anterior["prefixo"] if cartao_anterior else None,
                            prefixo,
                        )
                        aviso = f'Cartão final {final4} salvo como "{prefixo}".'
            elif acao == "nome_curto":
                # o nome que aparece ao lado do icone e do selo; vazio volta a
                # usar o titular da conexao
                account_id = request.form.get("account_id") or ""
                nome_curto = (request.form.get("nome_curto") or "").strip()[:40]
                cur.execute(
                    "SELECT nome_curto FROM cartao.conta WHERE account_id::text = %s;",
                    (account_id,),
                )
                antes = cur.fetchone()
                if not antes:
                    erro = "Conta não encontrada."
                else:
                    cur.execute(
                        "UPDATE cartao.conta SET nome_curto = %s WHERE account_id::text = %s;",
                        (nome_curto or None, account_id),
                    )
                    conn.commit()
                    registrar_mudanca_auditoria(
                        "Nome curto da origem", antes["nome_curto"], nome_curto or None
                    )
                    aviso = (f'Nome curto salvo: "{nome_curto}".' if nome_curto
                             else "Nome curto removido; a origem volta a usar o titular.")
            else:
                item_id = request.form.get("item_id")
                titular = (request.form.get("titular") or "").strip()
                cur.execute(
                    "SELECT titular FROM cartao.item_titular WHERE item_id = %s;",
                    (item_id,),
                )
                titular_anterior = cur.fetchone()
                if not titular:
                    cur.execute("DELETE FROM cartao.item_titular WHERE item_id = %s;", (item_id,))
                    aviso = "Titular removido dessa conexão."
                else:
                    cur.execute(
                        "INSERT INTO cartao.item_titular (item_id, titular) VALUES (%s,%s) "
                        "ON CONFLICT (item_id) DO UPDATE SET titular = EXCLUDED.titular;",
                        (item_id, titular),
                    )
                    aviso = f'Titular salvo: "{titular}".'
                conn.commit()
                registrar_mudanca_auditoria(
                    "Titular da conexão",
                    titular_anterior["titular"] if titular_anterior else None,
                    titular or None,
                )
        except Exception as e:
            conn.rollback()
            erro = str(e)
        if erro:
            marcar_falha_auditoria()

    cur.execute(
        "SELECT c.item_id, c.account_id, c.tipo, c.nome, c.numero_final, "
        "c.fechamento_fatura, c.vencimento_fatura, p.connector_name, it.titular "
        "FROM cartao.conta c JOIN cartao.pluggy_item p ON p.item_id = c.item_id "
        "LEFT JOIN cartao.item_titular it ON it.item_id = c.item_id "
        "ORDER BY p.connector_name, c.tipo;"
    )
    linhas = cur.fetchall()

    # quais cartoes (finais) pertencem a cada conta - vem dos proprios lancamentos,
    # que e o unico lugar onde o cartao adicional aparece. A conta traz so o final
    # do cartao principal; os adicionais so existem nas transacoes.
    cur.execute(
        "SELECT DISTINCT account_id::text AS account_id, numero_cartao_final "
        "FROM cartao.transacao WHERE numero_cartao_final IS NOT NULL;"
    )
    finais_por_conta = {}
    for r in cur.fetchall():
        finais_por_conta.setdefault(r["account_id"], set()).add(r["numero_cartao_final"])

    cur.execute("SELECT final4, prefixo FROM cartao.cartao_nome ORDER BY prefixo;")
    cartoes_nome = cur.fetchall()
    # o mesmo nome, icone e selo que as outras telas mostram - a previa ao lado
    # de cada campo sai daqui, e nao de uma segunda regra
    contas_by_id, _ = carregar_origens(cur)
    cur.close()
    conn.close()

    nomes_cartao = {c["final4"]: c["prefixo"] for c in cartoes_nome}

    conexoes = {}
    for r in linhas:
        item_id = str(r["item_id"])
        banco = detectar_banco(r["nome"], r["connector_name"])
        info = conexoes.setdefault(
            item_id, {"banco": banco, "titular": r["titular"], "contas": [], "credito": []}
        )
        tipo_pt = {"CREDIT": "Cartão de crédito", "BANK": "Conta corrente",
                   "MANUAL": "Dinheiro (manual)"}.get(r["tipo"], r["tipo"])
        info["contas"].append(tipo_pt)
        origem = contas_by_id.get(str(r["account_id"])) or {}
        info.setdefault("origens", []).append({
            "account_id": str(r["account_id"]),
            "rotulo": ROTULO_TIPO_CONTA.get(r["tipo"], r["tipo"]),
            "icone": icone_tipo_html(r["tipo"]),
            "nome_curto": origem.get("nome_curto"),
            # o que a tela usa com o campo em branco (titular, senao o banco)
            "padrao": origem.get("nome_padrao") or ROTULO_TIPO_CONTA.get(r["tipo"], ""),
            "efetivo": origem.get("label_curto", ""),
            "completo": origem.get("label", ""),
            "marca": origem.get("selo", ""),
        })
        if r["tipo"] == "CREDIT":
            info["credito"].append(r)

    # finais ja usados por alguma conta - o que sobra e cartao cadastrado a mao
    usados = set()
    for r in linhas:
        if r["tipo"] == "CREDIT":
            usados |= finais_por_conta.get(str(r["account_id"]), set())
            if r["numero_final"]:
                usados.add(r["numero_final"])
    avulsos = [c for c in cartoes_nome if c["final4"] not in usados]

    # prepara os dados prontos pro template: dia da fatura ja extraido e a lista de
    # cartoes de cada conta ja resolvida (principal + adicionais vistos nos lancamentos)
    for info in conexoes.values():
        info["selo"] = selo_banco_html(info["banco"])
        info["contas_txt"] = ", ".join(dict.fromkeys(info["contas"]))
        for c in info["credito"]:
            finais = set(finais_por_conta.get(str(c["account_id"]), set()))
            if c["numero_final"]:
                finais.add(c["numero_final"])
            c["finais"] = sorted(finais)
            c["dia_fechamento"] = c["fechamento_fatura"].day if c["fechamento_fatura"] else None
            c["dia_vencimento"] = c["vencimento_fatura"].day if c["vencimento_fatura"] else None

    return render_template(
        "contas.html",
        titulo="Configurações de Contas / Cartão",
        topbar=topbar_html("Configurações de Contas / Cartão", "contas"),
        aviso=aviso,
        erro=erro,
        conexoes=conexoes,
        nomes_cartao=nomes_cartao,
        avulsos=avulsos,
        sugestoes=["Ronaldo", "Andrea", "Ronaldo e Andrea", "Compartilhado"],
    )


@bp.route("/pendencias", methods=["GET", "POST"])
@requer("cadastros")
def pendencias_view():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    aviso = erro = None

    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "definir_natureza":
            categoria = request.form.get("categoria")
            natureza = request.form.get("natureza")
            if categoria and natureza in NATUREZAS:
                cur.execute(
                    "INSERT INTO cartao.categoria_natureza (categoria, natureza) VALUES (%s,%s) "
                    "ON CONFLICT (categoria) DO UPDATE SET natureza = EXCLUDED.natureza;",
                    (categoria, natureza),
                )
                conn.commit()
                aviso = f'Natureza de "{cat_pt_puro(categoria)}" definida como {NATUREZAS[natureza]}.'
        elif acao == "vincular_centro":
            categoria = request.form.get("categoria")
            subgrupo_id = request.form.get("subgrupo_id") or None
            if categoria and subgrupo_id:
                # vincula o PADRAO da categoria; condicao por dimensao se monta
                # na tela de Centro de Custos, que e onde ela e visivel
                definir_regra_padrao(cur, categoria, subgrupo_id)
                conn.commit()
                aviso = f'"{cat_pt_puro(categoria)}" vinculada ao centro de custo.'
        elif acao == "definir_natureza_lote":
            # a maioria das categorias sem natureza e despesa mesmo; o risco esta nas
            # poucas que nao sao. Decidir em bloco poupa atencao para essas.
            natureza = request.form.get("natureza")
            marcadas = [c for c in request.form.getlist("categoria") if c]
            if natureza not in NATUREZAS:
                erro = "Escolha uma natureza válida."
            elif not marcadas:
                erro = "Marque ao menos uma categoria."
            else:
                for categoria in marcadas:
                    cur.execute(
                        "INSERT INTO cartao.categoria_natureza (categoria, natureza) VALUES (%s,%s) "
                        "ON CONFLICT (categoria) DO UPDATE SET natureza = EXCLUDED.natureza;",
                        (categoria, natureza),
                    )
                conn.commit()
                quantas = len(marcadas)
                aviso = (
                    f"{quantas} categoria{'s' if quantas > 1 else ''} "
                    f"definida{'s' if quantas > 1 else ''} como {NATUREZAS[natureza]}."
                )
        elif acao == "limpar_natureza":
            # volta o lancamento a seguir a natureza da categoria dele
            transacao_id = request.form.get("transacao_id")
            if transacao_id:
                cur.execute(
                    "UPDATE cartao.transacao SET natureza = NULL WHERE transacao_id = %s;",
                    (transacao_id,),
                )
                conn.commit()
                aviso = "Lançamento voltou a seguir a natureza da categoria."
        elif acao == "definir_categoria_lancamento":
            transacao_id = request.form.get("transacao_id")
            categoria = request.form.get("categoria")
            cur.execute(
                "SELECT 1 FROM (SELECT categoria FROM cartao.categoria_natureza "
                "UNION SELECT categoria FROM cartao.categoria "
                "UNION SELECT DISTINCT categoria FROM cartao.transacao WHERE categoria IS NOT NULL) x "
                "WHERE x.categoria = %s;",
                (categoria,),
            )
            if not transacao_id or not categoria or not cur.fetchone():
                erro = "Escolha uma categoria válida."
            else:
                cur.execute(
                    "UPDATE cartao.transacao SET categoria = %s, categoria_manual = true, "
                    "regra_aplicada_id = NULL WHERE transacao_id = %s AND categoria IS NULL;",
                    (categoria, transacao_id),
                )
                if cur.rowcount:
                    conn.commit()
                    aviso = "Categoria definida. O lançamento continua pendente de conferência."
                else:
                    conn.rollback()
                    erro = "O lançamento já foi alterado em outra tela. Recarregue e confira."
        elif acao == "ocultar":
            categoria = request.form.get("categoria")
            if categoria:
                cur.execute(
                    "INSERT INTO cartao.categoria_oculta (categoria) VALUES (%s) ON CONFLICT DO NOTHING;",
                    (categoria,),
                )
                conn.commit()
                recarregar_categorias_db()
                aviso = f'"{cat_pt_puro(categoria)}" ocultada — não aparece mais nas listas.'

    pend = levantar_pendencias(cur)

    cur.execute("SELECT id, nome FROM cartao.grupo_custo;")
    grupos_db = sorted(cur.fetchall(), key=lambda g: chave_alfa(g["nome"]))
    cur.execute("SELECT id, grupo_id, nome FROM cartao.subgrupo_custo;")
    subgrupos_db = sorted(cur.fetchall(), key=lambda s: chave_alfa(s["nome"]))
    cur.execute(
        "SELECT categoria FROM cartao.categoria_natureza "
        "UNION SELECT categoria FROM cartao.categoria "
        "UNION SELECT DISTINCT categoria FROM cartao.transacao WHERE categoria IS NOT NULL;"
    )
    categorias_pendencia = sorted(
        (r["categoria"] for r in cur.fetchall() if r["categoria"] not in CATEGORIAS_OCULTAS),
        key=lambda c: chave_alfa(cat_pt_puro(c)),
    )
    cur.close()
    conn.close()

    subgrupos_por_grupo = {}
    for s in subgrupos_db:
        subgrupos_por_grupo.setdefault(s["grupo_id"], []).append(s)

    # Primeira tela migrada para Jinja (fase 3 da refatoracao). Repare que aqui nao
    # ha nenhuma chamada a esc(): o proprio Jinja escapa todo {{ ... }}, entao nome
    # de categoria com HTML dentro sai como texto sem ninguem precisar lembrar.
    return render_template(
        "pendencias.html",
        titulo="Pendências de classificação",
        topbar=topbar_html("Pendências de classificação", "pendencias"),
        aviso=aviso,
        erro=erro,
        pend=pend,
        grupos=grupos_db,
        subgrupos_por_grupo=subgrupos_por_grupo,
        categorias_pendencia=[{"chave": c, "nome": cat_pt_puro(c)} for c in categorias_pendencia],
        naturezas=NATUREZAS,
        natureza_padrao=NATUREZA_PADRAO,
        nome_categoria=cat_pt_puro,
    )


@bp.route("/categorias", methods=["GET", "POST"])
@requer("cadastros")
def categorias_view():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    aviso = erro = None

    def contar_uso(categoria):
        cur.execute(f"SELECT COUNT(*) AS n FROM {FINANCEIRO_TABELA} WHERE categoria = %s;", (categoria,))
        return cur.fetchone()["n"]

    if request.method == "POST":
        acao = request.form.get("acao")
        try:
            if acao == "natureza":
                categoria = request.form.get("categoria")
                natureza = request.form.get("natureza")
                if categoria and natureza in NATUREZAS:
                    cur.execute(
                        "SELECT natureza FROM cartao.categoria_natureza WHERE categoria = %s;",
                        (categoria,),
                    )
                    anterior = cur.fetchone()
                    cur.execute(
                        "INSERT INTO cartao.categoria_natureza (categoria, natureza) VALUES (%s,%s) "
                        "ON CONFLICT (categoria) DO UPDATE SET natureza = EXCLUDED.natureza;",
                        (categoria, natureza),
                    )
                    conn.commit()
                    registrar_mudanca_auditoria(
                        f"Natureza de {cat_pt_puro(categoria)}",
                        anterior["natureza"] if anterior else None,
                        natureza,
                    )

            elif acao == "criar":
                nome = (request.form.get("nome") or "").strip()
                if not nome:
                    erro = "Informe o nome da categoria."
                elif categoria_com_nome(nome):
                    erro = f'Já existe uma categoria chamada "{nome}".'
                else:
                    cur.execute(
                        "INSERT INTO cartao.categoria (categoria, nome_pt) VALUES (%s,%s) "
                        "ON CONFLICT (categoria) DO UPDATE SET nome_pt = EXCLUDED.nome_pt;",
                        (nome, nome),
                    )
                    cur.execute("DELETE FROM cartao.categoria_oculta WHERE categoria = %s;", (nome,))
                    conn.commit()
                    registrar_mudanca_auditoria("Categoria", None, {
                        "chave": nome, "nome": nome,
                    })
                    aviso = f'Categoria "{nome}" criada.'

            elif acao == "renomear":
                categoria = request.form.get("categoria") or ""
                novo_nome = (request.form.get("novo_nome") or "").strip()
                conflito = categoria_com_nome(novo_nome, exceto=categoria)
                if not novo_nome:
                    erro = "Informe o novo nome."
                elif conflito:
                    # sem isto, duas categorias diferentes ficam com o mesmo nome na
                    # tela e o relatorio passa a mostrar linhas repetidas
                    erro = (
                        f'Já existe uma categoria chamada "{novo_nome}" '
                        f'(a categoria "{conflito}"). Para juntar as duas, use '
                        '"Mover lançamentos" e depois remova a que ficar vazia.'
                    )
                else:
                    nome_anterior = cat_pt_puro(categoria)
                    cur.execute(
                        "INSERT INTO cartao.categoria (categoria, nome_pt) VALUES (%s,%s) "
                        "ON CONFLICT (categoria) DO UPDATE SET nome_pt = EXCLUDED.nome_pt;",
                        (categoria, novo_nome),
                    )
                    conn.commit()
                    registrar_mudanca_auditoria(
                        "Nome da categoria", nome_anterior, novo_nome,
                    )
                    aviso = f'Categoria renomeada para "{novo_nome}".'

            elif acao == "mover":
                origem = request.form.get("origem") or ""
                destino = request.form.get("destino") or ""
                if not origem or not destino:
                    erro = "Escolha a categoria de origem e a de destino."
                elif origem == destino:
                    erro = "Escolha categorias diferentes para mover."
                else:
                    cur.execute(
                        "UPDATE cartao.transacao SET categoria = %s, categoria_manual = true, "
                        "regra_aplicada_id = NULL WHERE categoria = %s;",
                        (destino, origem),
                    )
                    qtd = cur.rowcount
                    cur.execute(
                        "UPDATE cartao.transacao_rateio SET categoria=%s, atualizado_em=now() "
                        "WHERE categoria=%s;",
                        (destino, origem),
                    )
                    qtd += cur.rowcount
                    conn.commit()
                    if qtd:
                        registrar_mudanca_auditoria(
                            "Lançamentos por categoria",
                            {"categoria": cat_pt_puro(origem), "quantidade": qtd},
                            {"categoria": cat_pt_puro(destino), "quantidade": qtd},
                        )
                    aviso = f'{qtd} lançamento(s) movido(s) de "{cat_pt_puro(origem)}" para "{cat_pt_puro(destino)}".'

            elif acao == "excluir":
                categoria = request.form.get("categoria") or ""
                qtd = contar_uso(categoria)
                if qtd > 0:
                    erro = f'Não é possível remover: existem {qtd} lançamento(s) nessa categoria. Mova-os primeiro.'
                else:
                    nome_anterior = cat_pt_puro(categoria)
                    cur.execute("DELETE FROM cartao.categoria WHERE categoria = %s;", (categoria,))
                    cur.execute("DELETE FROM cartao.categoria_natureza WHERE categoria = %s;", (categoria,))
                    cur.execute("DELETE FROM cartao.centro_regra WHERE categoria = %s;", (categoria,))
                    cur.execute("INSERT INTO cartao.categoria_oculta (categoria) VALUES (%s) ON CONFLICT DO NOTHING;", (categoria,))
                    conn.commit()
                    registrar_mudanca_auditoria("Categoria", {
                        "chave": categoria, "nome": nome_anterior,
                    }, None)
                    aviso = f'Categoria "{cat_pt_puro(categoria)}" removida.'
        except psycopg2.errors.UniqueViolation:
            # rede de baixo: a validacao da tela ja barra nome repetido, mas se
            # algo passar, o indice do banco impede - e o usuario merece uma
            # mensagem em portugues, nao o erro cru do Postgres
            conn.rollback()
            erro = "Já existe uma categoria com esse nome."
        except Exception as e:
            conn.rollback()
            erro = str(e)
        if erro:
            marcar_falha_auditoria()
        recarregar_categorias_db()

    cur.execute(
        f"SELECT t.categoria, COUNT(*) AS qtd, SUM({VAL_DESPESA}) AS total "
        f"FROM {FINANCEIRO_TABELA} t JOIN cartao.conta c ON c.account_id = t.account_id "
        "WHERE t.categoria IS NOT NULL "
        "GROUP BY t.categoria;"
    )
    usadas = {r["categoria"]: r for r in cur.fetchall()}

    cur.execute("SELECT categoria, natureza FROM cartao.categoria_natureza;")
    naturezas_atuais = {r["categoria"]: r["natureza"] for r in cur.fetchall()}
    cur.close()
    conn.close()

    todas = sorted(
        (set(usadas) | set(CATEGORIA_PT) | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB)) - CATEGORIAS_OCULTAS,
        key=lambda c: chave_alfa(cat_pt_puro(c)),
    )

    # uma lista de dicts prontos, pro template so exibir. 'chave' e o identificador
    # que vai nos forms; 'nome' e o rotulo traduzido que o usuario le.
    categorias = []
    for c in todas:
        info = usadas.get(c)
        categorias.append({
            "chave": c,
            "nome": cat_pt_puro(c),
            "qtd": info["qtd"] if info else 0,
            "total": float(info["total"] or 0) if info else 0.0,
            "natureza": naturezas_atuais.get(c, NATUREZA_PADRAO),
        })

    # Nomes iguais apontando para categorias diferentes: a renomeacao e so um
    # apelido, a chave do Pluggy continua distinta. Isso vira linha duplicada no
    # relatorio, vinculo separado no centro de custo e - o mais grave - naturezas
    # que podem divergir sem ninguem ver, porque na tela sao "a mesma" categoria.
    # agrupa por chave_alfa, a MESMA normalizacao usada por categoria_com_nome():
    # ignora acento e caixa. Comparar o nome literal deixava passar justamente o
    # caso mais dificil de ver na tela - "Transferencia Interna" e "Transferência
    # Interna" sao duas categorias diferentes e parecem a mesma.
    por_nome = {}
    for c in categorias:
        por_nome.setdefault(chave_alfa(c["nome"]), []).append(c)
    duplicadas = [
        {"nome": itens[0]["nome"], "itens": itens,
         "naturezas_divergem": len({i["natureza"] for i in itens}) > 1}
        for _, itens in sorted(por_nome.items()) if len(itens) > 1
    ]

    return render_template(
        "categorias.html",
        duplicadas=duplicadas,
        titulo="Gerenciar categorias",
        topbar=topbar_html("Gerenciar categorias", "categorias"),
        aviso=aviso,
        erro=erro,
        categorias=categorias,
        naturezas=NATUREZAS,
        naturezas_neutras=NATUREZAS_NEUTRAS,
        # o modal de detalhes deixa trocar a categoria do lancamento; quem so tem
        # 'cadastros' e nao 'lancamentos_editar' ve o seletor travado (a API recusaria)
        pode_editar=pode("lancamentos_editar"),
    )


@bp.route("/configuracoes/faturas-pdf", methods=["GET", "POST"])
@requer("cadastros")
def faturas_pdf_view():
    """Gerencia os PDFs originais das faturas conciliadas (ver
    /relatorios/conciliar-fatura). Guardados como bytea no proprio Postgres -
    o app roda em container sem volume persistente confirmado no Coolify, e o
    banco ja sobrevive a qualquer deploy sem depender de configuracao extra."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    aviso = None

    if request.method == "POST" and request.form.get("acao") == "apagar_pdf":
        fatura_id = request.form.get("fatura_id", type=int)
        cur.execute(
            "SELECT arquivo_nome FROM cartao.fatura_importada WHERE id=%s;",
            (fatura_id,),
        )
        anterior = cur.fetchone()
        cur.execute(
            "UPDATE cartao.fatura_importada SET pdf_arquivo=NULL WHERE id=%s;",
            (fatura_id,),
        )
        conn.commit()
        if anterior and cur.rowcount:
            registrar_mudanca_auditoria("Arquivo da fatura", anterior["arquivo_nome"], None)
            aviso = f'Arquivo "{anterior["arquivo_nome"]}" apagado. O histórico de conciliação continua disponível.'

    contas_by_id, _ = carregar_origens(cur)
    cur.execute(
        "SELECT id, account_id, mes_referencia, ano_referencia, arquivo_nome, importado_em, "
        "octet_length(pdf_arquivo) AS pdf_tamanho "
        "FROM cartao.fatura_importada ORDER BY ano_referencia DESC, mes_referencia DESC;"
    )
    faturas = []
    for r in cur.fetchall():
        conta = contas_by_id.get(str(r["account_id"]))
        faturas.append({
            **r, "conta_label": conta["label"] if conta else "(conta removida)",
            "importado_em": data_hora_local(r["importado_em"]),
        })
    cur.close()
    conn.close()

    return render_template(
        "faturas_pdf.html",
        titulo="Arquivos de fatura",
        topbar=topbar_html("Arquivos de fatura", "faturas-pdf"),
        aviso=aviso,
        faturas=faturas,
    )
