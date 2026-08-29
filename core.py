"""Nucleo compartilhado: constantes, acesso ao banco, permissoes e helpers de HTML.

Nao importa nada de views/ nem de app.py. A dependencia corre sempre na mesma
direcao - app.py -> views/ -> core.py - o que impede import circular.
"""
import os
import functools
import hashlib
import html
import json
import logging
import secrets
import unicodedata
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
from flask import request, redirect, session, jsonify, render_template, has_request_context, g


logger = logging.getLogger(__name__)


# URL do servico bussola-financeira-app que faz a sincronizacao com o Pluggy.
# Pode ser sobrescrita via env var caso o dominio mude.
BUSSOLA_SYNC_URL = os.environ.get(
    "BUSSOLA_SYNC_URL", "https://hdgffcvh3ljqe61dczztaycz.coolify.brdrive.net/sync"
)


# usuarios iniciais (env). Servem apenas para criar os primeiros acessos:
# depois do primeiro boot os usuarios passam a viver na tabela cartao.usuario,
# com senha guardada em hash. Trocar a senha pela tela nao depende mais da env.
USERS = {
    login: senha
    for login, senha in (
        (os.environ.get("APP_USER_1", "ronaldo"), os.environ.get("APP_PASS_1")),
        (os.environ.get("APP_USER_2", "andrea"), os.environ.get("APP_PASS_2")),
    )
    if senha  # sem senha na env, essa conta de emergencia fica desativada
}


PERMISSOES = {
    "lancamentos_ver": ("Ver lançamentos", "Abrir a tela de lançamentos e consultar o que foi gasto."),
    "lancamentos_editar": ("Editar lançamentos", "Mudar categoria, responsável, projeto, observação e marcar duplicadas."),
    "lancamentos_conferir": ("Conferir lançamentos", "Marcar um lançamento como conferido."),
    "lancamentos_manual": ("Lançar dinheiro manual", "Criar e excluir lançamentos em espécie."),
    "relatorios": ("Ver relatórios", "Relatórios, DRE e investimentos."),
    "cadastros": ("Gerenciar cadastros", "Grupos de custo, dimensões, regras automáticas, cartões e naturezas."),
    "sincronizar": ("Sincronizar com o banco", "Usar o botão Atualizar agora."),
    "usuarios": ("Gerenciar usuários", "Criar usuários, trocar senhas e definir permissões."),
}


PERFIS = {
    "admin": ("Administrador", list(PERMISSOES.keys())),
    "operador": ("Operador", [
        "lancamentos_ver", "lancamentos_editar", "lancamentos_conferir",
        "lancamentos_manual", "relatorios", "sincronizar",
    ]),
    "leitura": ("Somente leitura", ["lancamentos_ver", "relatorios"]),
}


def hash_senha(senha, salt=None):
    """Guarda a senha como hash PBKDF2 - a senha em si nunca fica salva."""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 200_000)
    return f"pbkdf2$200000${salt}${dk.hex()}"


def senha_confere(senha, guardado):
    try:
        _, iteracoes, salt, esperado = (guardado or "").split("$")
        dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), int(iteracoes))
        return secrets.compare_digest(dk.hex(), esperado)
    except (ValueError, AttributeError):
        return False


def permissoes_do_perfil(perfil, extras=None):
    base = list(PERFIS.get(perfil, PERFIS["leitura"])[1])
    for p in (extras or []):
        if p in PERMISSOES and p not in base:
            base.append(p)
    return base


def pode(permissao):
    """Permissao do usuario logado na sessao."""
    return permissao in (session.get("permissoes") or [])


def validar_sessao_atual():
    """Confirma que o usuario segue ativo e recarrega suas permissoes."""
    usuario = session.get("user")
    if not usuario:
        return False
    # Atualiza tambem cookies criados antes da politica de expiracao. Como
    # SESSION_REFRESH_EACH_REQUEST esta ativo, cada uso valido renova as 24h.
    session.permanent = True
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT nome, perfil, permissoes, ativo FROM cartao.usuario WHERE usuario = %s;",
            (usuario,),
        )
        conta = cur.fetchone()
        cur.close()
        conn.close()
        if not conta or not conta["ativo"]:
            session.clear()
            return False
        session["nome"] = conta["nome"] or usuario
        session["perfil"] = conta["perfil"]
        session["permissoes"] = list(conta["permissoes"] or [])
        return True
    except Exception as e:
        print("Aviso: falha ao validar sessao:", e)
        session.clear()
        return False


def requer(permissao):
    """Bloqueia a rota para quem nao tem a permissao."""
    def decorador(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not validar_sessao_atual():
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "erro": "Sessão inválida ou expirada."}), 401
                return redirect("/login")
            if not pode(permissao):
                titulo, _ = PERMISSOES.get(permissao, (permissao, ""))
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "erro": f"Sem permissão para: {titulo}"}), 403
                return render_template(
                    "sem_permissao.html",
                    titulo="Sem permissão",
                    topbar=topbar_html("Sem permissão"),
                    permissao=titulo,
                ), 403
            return view(*args, **kwargs)
        return wrapped
    return decorador


CATEGORIA_PT = {
    "Accomodation": "Hospedagem",
    "Airport and airlines": "Aeroporto e Companhias Aéreas",
    "Bookstore": "Livraria",
    "Cinema, theater and concerts": "Cinema, Teatro e Shows",
    "Clothing": "Vestuário",
    "Credit card fees": "Tarifas do Cartão",
    "Credit card payment": "Pagamento de Fatura",
    "Dentist": "Dentista",
    "Digital services": "Serviços Digitais",
    "Donations": "Doações",
    "Eating out": "Restaurantes",
    "Electronics": "Eletrônicos",
    "Gas stations": "Postos de Combustível",
    "Groceries": "Mercado",
    "Healthcare": "Saúde",
    "Hospital clinics and labs": "Hospitais e Laboratórios",
    "Houseware": "Utilidades Domésticas",
    "Insurance": "Seguros",
    "Interests charged": "Juros Cobrados",
    "Kids and toys": "Infantil e Brinquedos",
    "Leisure": "Lazer",
    "Office supplies": "Material de Escritório",
    "Online shopping": "Compras Online",
    "Parking": "Estacionamento",
    "Pharmacy": "Farmácia",
    "School": "Educação",
    "Services": "Serviços",
    "Shopping": "Compras",
    "Taxi and ride-hailing": "Táxi e Transporte por App",
    "Telecommunications": "Telecomunicações",
    "Tickets": "Ingressos",
    "Vehicle maintenance": "Manutenção Veicular",
    "Transfer - Internal": "Transferência Interna",
    "Tax on financial operations": "IOF",
    "Tolls and in vehicle payment": "Pedágio",
    "Agua": "Água",
    "Agua / Gas": "Gás",
    "Natacao": "Natação",
    "Academia": "Academia",
    "Viagem": "Viagem",
}


CATEGORIAS_NAO_GASTO = ("Credit card payment", "Transfer - Internal")


CATEGORIAS_EXTRA = (
    "BRDrive", "Agua", "Agua / Gas", "Natacao", "Academia", "Viagem",
    "Imóveis / Terrenos", "Veículos / Bens",
)


NATUREZAS = {
    "receita": "Receita",
    "despesa": "Despesa",
    "investimento": "Investimento",
    "bem": "Aquisição de bem",
    "transferencia": "Transferência",
    "fluxo": "Depende da direção",
}


NATUREZA_PADRAO = "despesa"


NATUREZAS_NEUTRAS = ("investimento", "bem", "transferencia")


SEED_NATUREZAS = {
    # so movem dinheiro de lugar - nunca sao despesa
    "Credit card payment": "transferencia",
    "Transfer - Internal": "transferencia",
    "Same person transfer": "transferencia",
    "Same person transfer - CASH": "transferencia",
    "Same person transfer - PIX": "transferencia",
    "Same person transfer - TED": "transferencia",
    "Same person transfer - DOC": "transferencia",
    "Same person transfer - Bank Slip": "transferencia",
    # na base do Ronaldo isto veio do Pluggy como financiamento, mas e o
    # pagamento da fatura do cartao de marco/2026 (bate com o valor da fatura)
    "Loans and financing": "transferencia",

    # despesas confirmadas pelo usuario na revisao da base de producao
    "Automotive": "despesa",
    "Travel": "despesa",
    "Wellness and fitness": "despesa",
    "Rent": "despesa",

    # poupanca de longo prazo - sai do resultado, entra no bloco de investimentos
    "Investments": "investimento",
    "Automatic investment": "investimento",
    "Pension": "investimento",
    "Fixed income": "investimento",
    "Variable income": "investimento",
    "Savings": "investimento",

    # aquisicao de bem - nao e despesa, e troca de ativo
    "Imóveis / Terrenos": "bem",
    "Veículos / Bens": "bem",

    # a direcao define: o que entra e receita, o que sai e despesa
    "Transfer - PIX": "fluxo",
    "Transfer - TED": "fluxo",
    "Transfer - DOC": "fluxo",
    "Transfer - Bank Slip": "fluxo",
    "Transfer - Cash": "fluxo",
    "Transfers": "fluxo",
    "Third party transfers": "fluxo",

    # entradas
    "Income": "receita",
    "Salary": "receita",
    "Government aid": "receita",
    "Interest income": "receita",
    "Dividends": "receita",

    # custo financeiro real: dinheiro que saiu de fato
    "Interests charged": "despesa",
    "Credit card fees": "despesa",
    "Tax on financial operations": "despesa",
}


CATEGORIAS_NEUTRAS_PADRAO = {
    c for c, n in SEED_NATUREZAS.items() if n in NATUREZAS_NEUTRAS
}


# Vinculos aprovados pelo usuario na revisao da base de producao. O subgrupo e
# identificado pelo nome porque os ids variam entre instalacoes.
CENTROS_CONFIRMADOS = {
    "Water": "Casa",
    "Housing": "Casa",
    "Electricity": "Casa",
    "Urban land and building tax": "Casa",
    "Rent": "Casa",
    "Internet": "Casa",
    "Sports goods": "Atividades Físicas",
    "Wellness and fitness": "Atividades Físicas",
    "Wellness": "Atividades Físicas",
    "Sports practice": "Atividades Físicas",
    "Optometry": "Saúde",
    "Education": "Educação",
    "Bicycle": "Manutenção",
    "Automotive": "Manutenção",
    "Vehicle ownership taxes and fees": "Manutenção",
    "Transportation": "Manutenção",
    "Public transportation": "Uber Taxi",
    "Travel": "Viagem",
    "Food and drinks": "Restaurantes",
    "Taxes": "Taxas Financeiras",
    "Entrepreneurial activities": "BRDrive",
}


CONTA_MANUAL_ID = "00000000-0000-0000-0000-000000000002"


APP_NOME = "Pé de Meia"


MESES_ABREV = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


JOIN_NATUREZA = (
    " JOIN cartao.conta c ON c.account_id = t.account_id "
    " LEFT JOIN cartao.categoria_natureza n ON n.categoria = t.categoria "
)


# Fonte financeira usada por DRE e relatorios. Quando um lancamento possui
# rateio, a view devolve as partes no lugar da linha original; sem rateio, ela
# devolve o proprio lancamento. Assim o total bancario nunca e contado duas
# vezes e cada parte pode ter categoria e dimensoes independentes.
FINANCEIRO_TABELA = "cartao.lancamento_financeiro"
FINANCEIRO_DIM_TABELA = "cartao.lancamento_financeiro_dimensao"


VAL_DESPESA = (
    "(CASE WHEN c.tipo = 'CREDIT' THEN COALESCE(t.valor_brl, t.valor_original) "
    "ELSE -COALESCE(t.valor_brl, t.valor_original) END)"
)


_NAT_BASE = "COALESCE(t.natureza, n.natureza, '" + NATUREZA_PADRAO + "')"


NATUREZA_SQL = (
    "(CASE WHEN " + _NAT_BASE + " = 'fluxo' "
    "THEN (CASE WHEN " + VAL_DESPESA + " > 0 THEN 'despesa' ELSE 'receita' END) "
    "ELSE " + _NAT_BASE + " END)"
)


# O banco e o container trabalham em UTC, mas a competencia financeira e a
# data civil de Sao Paulo. Sem esta conversao, uma compra perto da meia-noite
# pode cair no dia/mes seguinte no DRE.
DATA_LOCAL_SQL = "(t.data_transacao AT TIME ZONE 'America/Sao_Paulo')"
FUSO_LOCAL = ZoneInfo("America/Sao_Paulo")


def intervalo_mes_local(valor):
    """Limites UTC de um mes civil de Sao Paulo, prontos para usar o indice de data."""
    inicio = datetime.strptime(valor, "%Y-%m").replace(tzinfo=FUSO_LOCAL)
    if inicio.month == 12:
        fim = inicio.replace(year=inicio.year + 1, month=1)
    else:
        fim = inicio.replace(month=inicio.month + 1)
    return inicio, fim


def intervalo_ano_local(valor):
    """Limites UTC de um ano civil de Sao Paulo, inclusive em anos com horario de verao."""
    inicio = datetime.strptime(str(valor), "%Y").replace(tzinfo=FUSO_LOCAL)
    return inicio, inicio.replace(year=inicio.year + 1)


def data_hora_local(valor):
    """Converte um instante do banco para o horario civil de Sao Paulo.

    O PostgreSQL devolve TIMESTAMPTZ com fuso. Evitamos subtrair tres horas na
    mao porque isso perde a informacao de fuso e falha para datas historicas em
    que a regra de horario local era diferente. Valores ingenuos sao tratados
    como UTC, que e o fuso usado pelos containers e pelo banco.
    """
    if valor is None:
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(FUSO_LOCAL)


BANCOS_CONHECIDOS = (
    ("Nubank", ("nubank", "nu pagamentos", "nu financeira", "nu invest")),
    ("Unicred", ("unicred",)),
    ("Itaú", ("itau", "itaú")),
    ("Bradesco", ("bradesco",)),
    ("Santander", ("santander",)),
    ("Caixa", ("caixa economica", "caixa econômica")),
    ("Banco do Brasil", ("banco do brasil",)),
    ("Inter", ("banco inter", "inter s.a", "intermedium")),
    ("C6 Bank", ("c6 bank", "banco c6")),
    ("PicPay", ("picpay",)),
    ("Mercado Pago", ("mercado pago", "mercadopago")),
    ("BTG", ("btg pactual", "btg")),
    ("XP", ("xp investimentos", "banco xp")),
    ("Sicoob", ("sicoob",)),
    ("Sicredi", ("sicredi",)),
    ("Neon", ("banco neon", "neon pagamentos")),
    ("Will Bank", ("will bank", "willbank")),
    ("Original", ("banco original",)),
    ("Safra", ("safra",)),
    ("Pan", ("banco pan",)),
)


def detectar_banco(nome_conta, connector_name):
    texto = f"{nome_conta or ''} {connector_name or ''}".lower()
    for banco, apelidos in BANCOS_CONHECIDOS:
        if any(a in texto for a in apelidos):
            return banco
    return connector_name or nome_conta or "Banco"


BANCOS_ESTILO = {
    "Nubank": ("#820ad1", "Nu"),
    "Unicred": ("#00995d", "UN"),
    "Itaú": ("#ec7000", "It"),
    "Bradesco": ("#cc092f", "Br"),
    "Santander": ("#ec0000", "Sa"),
    "Caixa": ("#0070af", "CX"),
    "Banco do Brasil": ("#f9dd16", "BB", "#1c1c1c"),
    "Inter": ("#ff7a00", "In"),
    "C6 Bank": ("#242424", "C6"),
    "PicPay": ("#11c76f", "PP"),
    "Mercado Pago": ("#00b1ea", "MP"),
    "BTG": ("#0d1b2a", "BT"),
    "XP": ("#0f0f0f", "XP"),
    "Sicoob": ("#00a94f", "Sc"),
    "Sicredi": ("#3fa110", "Si"),
    "Neon": ("#00c8f0", "Ne"),
    "Will Bank": ("#ffe600", "Wl", "#1c1c1c"),
    "Original": ("#00a868", "Or"),
    "Safra": ("#00294b", "Sf"),
    "Pan": ("#00a0df", "Pa"),
}


def selo_banco_html(banco, tipo=None):
    """Selo colorido do banco. Para a conta manual usa um selo neutro."""
    if tipo == "MANUAL":
        return '<span class="selo" style="background:#5c6672">R$</span>'
    estilo = BANCOS_ESTILO.get(banco)
    if estilo:
        cor, sigla = estilo[0], estilo[1]
        cor_texto = estilo[2] if len(estilo) > 2 else "#ffffff"
    else:
        cor, sigla, cor_texto = "#7b828c", (banco or "?")[:2].upper(), "#ffffff"
    return f'<span class="selo" style="background:{cor};color:{cor_texto}">{sigla}</span>'


def origem_label(tipo, connector_name, nome_conta, titular=None):
    """Rotulo amigavel (completo) de origem a partir do tipo da conta + nome do banco detectado."""
    banco = detectar_banco(nome_conta, connector_name)
    if tipo == "CREDIT":
        base = f"Cartão de Crédito {banco}"
    elif tipo == "BANK":
        base = f"Conta Corrente {banco}"
    elif tipo == "MANUAL":
        base = "Dinheiro (manual)"
    else:
        base = nome_conta or "Outra origem"
    return f"{base} · {titular}" if titular else base


def origem_label_curto(tipo, connector_name, nome_conta, titular=None):
    """Rotulo curto de origem, usado na UI SEMPRE ao lado do selo do banco.

    Como o selo ja identifica o banco (o "Nu" roxo, o "UN" verde), repetir o nome
    ao lado e redundante - "Nu Conta Corrente Nubank (Andrea)". Por isso o nome do
    banco sai daqui quando ele tem selo proprio. Bancos sem selo conhecido caem no
    generico de duas letras, que nao identifica sozinho, entao esses mantem o nome.

    O rotulo completo (origem_label) continua trazendo o banco: ele aparece no
    tooltip e no rotulo do grafico, onde nao ha selo ao lado.
    """
    banco = detectar_banco(nome_conta, connector_name)
    tem_selo = banco in BANCOS_ESTILO
    if tipo == "CREDIT":
        base = "Cartão" if tem_selo else f"Cartão {banco}"
    elif tipo == "BANK":
        base = "Conta Corrente" if tem_selo else f"Conta Corrente {banco}"
    elif tipo == "MANUAL":
        base = "Dinheiro"
    else:
        base = nome_conta or "Outra"
    # sem parentese e sem separador: o rotulo aparece em lista estreita (filtro de
    # origem) e le melhor como uma frase so - "Conta Corrente Andrea"
    return f"{base} {titular}" if titular else base


def carregar_origens(cur):
    """Le todas as contas (Pluggy + manual) e devolve estruturas prontas de origem.

    O nome do banco costuma so aparecer no nome de UMA das contas da conexao (ex: a conta
    corrente traz a razao social do banco, o cartao traz so 'Cartao de credito'). Por isso
    detectamos o banco olhando todas as contas da conexao (item_id) e aplicamos para todas.
    """
    cur.execute(
        "SELECT c.account_id, c.item_id, c.tipo, c.nome, c.numero_final, p.connector_name, it.titular "
        "FROM cartao.conta c JOIN cartao.pluggy_item p ON p.item_id = c.item_id "
        "LEFT JOIN cartao.item_titular it ON it.item_id = c.item_id "
        "ORDER BY c.tipo, p.connector_name;"
    )
    contas = cur.fetchall()

    banco_por_item = {}
    for c in contas:
        banco = detectar_banco(c["nome"], c["connector_name"])
        if banco != (c["connector_name"] or c["nome"] or "Banco"):
            banco_por_item.setdefault(c["item_id"], banco)

    contas_by_id = {}
    opcoes = []
    for c in contas:
        banco = banco_por_item.get(c["item_id"], c["connector_name"])
        titular = c["titular"]
        completo = origem_label(c["tipo"], banco, c["nome"], titular)
        curto = origem_label_curto(c["tipo"], banco, c["nome"], titular)
        selo = selo_banco_html(detectar_banco(c["nome"], banco), c["tipo"])
        aid = str(c["account_id"])
        contas_by_id[aid] = {
            **c, "banco": banco, "label": completo, "label_curto": curto, "selo": selo, "titular": titular,
        }
        # (valor, texto puro, titulo do tooltip, texto curto, selo em HTML)
        # o selo vai separado porque e HTML do proprio app: junto com o texto ele
        # seria escapado e o usuario veria a marcacao crua no filtro
        opcoes.append((aid, curto, completo, curto, selo))
    return contas_by_id, opcoes


IMPORT_NAMESPACE = uuid.UUID("6f1c2a52-0000-4000-8000-000000000042")


def chip_filter_html(nome, label, opcoes, selecionados, onchange="aplicarFiltros()", contagens=None):
    """Filtro em chip com dropdown, busca e multi-selecao.

    opcoes: (valor, texto) e, opcionalmente, mais (titulo, texto_curto, selo_html).

    O texto e sempre escapado - vem do banco. O selo, quando existe, e HTML
    montado por selo_banco_html() e entra cru; por isso vem num campo separado,
    e nao concatenado no texto.

    contagens: {valor: n} opcional. Mostra quantos lancamentos cada opcao tem no
    periodo em que a tela esta - assim da para ver de relance de onde vem o
    movimento do mes sem precisar filtrar um a um.
    """
    contagens = contagens or {}
    n_sel = len(selecionados)
    partes = []
    for opt in opcoes:
        val, texto = opt[0], opt[1]
        titulo = opt[2] if len(opt) > 2 else texto
        curto = opt[3] if len(opt) > 3 else None
        selo = opt[4] if len(opt) > 4 else ""
        marcado = "checked" if str(val) in selecionados else ""
        attr_curto = f' data-curto="{esc(curto)}"' if curto else ""
        n = contagens.get(str(val))
        qtd = f'<span class="chip-qtd">{n}</span>' if n is not None else ""
        # selo, texto e numero num span so: dentro dele o fluxo e inline, entao o
        # numero acompanha a ultima palavra mesmo quando o nome quebra em duas
        # linhas. Soltos no flex do <label>, o texto esticava e empurrava o numero
        # para a borda direita, parecendo uma coluna.
        partes.append(
            f'<label class="chip-opt" data-tip="{esc(titulo)}"{attr_curto}>'
            f'<input type="checkbox" name="{nome}" value="{esc(val)}" {marcado} '
            f'onchange="{onchange}">'
            f'<span class="chip-txt">{selo}{esc(texto)}{qtd}</span></label>'
        )
    opts_html = "".join(partes)
    label_esc = esc(label)
    return f"""
    <div class="chipfilter">
      <button type="button" class="chip-btn {"ativo" if n_sel else ""}" data-label="{label_esc}" onclick="cfToggle(this)">
        {label_esc}{f' ({n_sel})' if n_sel else ''}
        {f'<span class="chip-clear" onclick="cfClear(event, this)">&times;</span>' if n_sel else ''}
      </button>
      <div class="chip-panel">
        <div class="chip-search-wrap"><input type="text" class="chip-search" placeholder="Procure {label_esc.lower()}..." oninput="cfFiltrar(this)" onkeydown="cfKeydown(event, this)"></div>
        <div class="chip-list">{opts_html}</div>
      </div>
    </div>
    """




def esc(valor):
    """Escapa texto que veio de input do usuario antes de embutir no HTML (evita XSS).
    Uso: em qualquer f-string de HTML que interpola nome de categoria, dimensao, grupo,
    observacao etc - qualquer campo de texto livre editavel pela tela."""
    if valor is None:
        return ""
    return html.escape(str(valor), quote=True)


def json_script(obj):
    """json.dumps seguro para embutir dentro de <script>...</script>. json.dumps sozinho
    NAO escapa "</" - uma descricao de lancamento contendo literalmente "</script>" fecharia
    a tag e executaria HTML/JS arbitrario para qualquer um que abrisse a tela."""
    return json.dumps(obj).replace("</", "<\\/")


def cat_pt_puro(categoria):
    """Nome da categoria em texto puro, SEM escapar.

    Use nos templates Jinja: lá o escaping é automático, então escapar aqui faria
    escapar duas vezes e a tela mostraria "&amp;lt;" em vez do caractere.
    Nas telas que ainda montam HTML por f-string, use cat_pt() (que já escapa)."""
    if not categoria:
        return "-"
    if categoria in CATEGORIA_PT_DB:
        return CATEGORIA_PT_DB[categoria]
    return CATEGORIA_PT.get(categoria, categoria)


def cat_pt(categoria):
    """Nome da categoria já escapado, para interpolar direto em f-string de HTML."""
    return esc(cat_pt_puro(categoria))


def rotulo_valor_dimensao(valor):
    """Nome do valor de dimensao, com o icone na frente quando houver.

    Usado na tabela de Lancamentos, no filtro e no relatorio - assim "Jeep" e
    "Tracker" se distinguem de relance sem precisar ler.
    """
    icone = (valor.get("icone") or "").strip()
    nome = valor.get("nome") or ""
    return f"{icone} {nome}".strip() if icone else nome


def categoria_com_nome(nome, exceto=None):
    """Devolve a chave da categoria que ja usa esse nome, ou None.

    Renomear categoria so troca o apelido - a chave do Pluggy continua distinta.
    Sem esta checagem duas categorias diferentes acabam com o mesmo nome na tela,
    e aí o relatorio mostra linhas repetidas, cada uma com seu vinculo de centro
    de custo e sua propria natureza. Foi assim que "Estacionamento" acabou em tres
    categorias.

    A comparacao ignora acento e caixa: "Mercado" e "mercado" sao o mesmo nome
    para quem le a tela.
    """
    alvo = chave_alfa(nome or "")
    if not alvo:
        return None
    conhecidas = set(CATEGORIA_PT) | set(CATEGORIAS_EXTRA) | set(CATEGORIA_PT_DB)
    for chave in conhecidas:
        if chave == exceto or chave in CATEGORIAS_OCULTAS:
            continue
        if chave_alfa(cat_pt_puro(chave)) == alvo:
            return chave
    return None


def chave_alfa(texto):
    """Chave de ordenacao alfabetica que ignora acentos, maiusculas/minusculas
    e espacos nas bordas - para que 'Água' venha antes de 'Banco', por exemplo."""
    texto = (texto or "").strip().casefold()
    sem_acento = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


CATEGORIA_PT_DB = {}


CATEGORIAS_OCULTAS = set()


def recarregar_categorias_db():
    """Atualiza os apelidos e as categorias ocultas a partir do banco.

    Altera os dois dicionarios NO LUGAR, em vez de reatribui-los. Isso importa
    porque outros modulos fazem `from core import CATEGORIA_PT_DB`: se aqui
    reatribuisse, esses modulos continuariam enxergando o dicionario antigo e
    os nomes de categoria congelariam na versao do boot.
    """
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT categoria, nome_pt FROM cartao.categoria;")
        novos = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT categoria FROM cartao.categoria_oculta;")
        ocultas = {r[0] for r in cur.fetchall()}
        cur.close()
        conn.close()
    except Exception:
        return
    # Atualiza primeiro e so depois remove o que saiu, em vez de clear()+update().
    # Com varias threads atendendo ao mesmo tempo, o clear() abriria uma janela em
    # que outra requisicao leria o dicionario vazio e mostraria a chave crua do
    # Pluggy no lugar do nome.
    CATEGORIA_PT_DB.update(novos)
    for chave in [c for c in CATEGORIA_PT_DB if c not in novos]:
        CATEGORIA_PT_DB.pop(chave, None)
    CATEGORIAS_OCULTAS.update(ocultas)
    CATEGORIAS_OCULTAS.difference_update([c for c in CATEGORIAS_OCULTAS if c not in ocultas])


def get_ultima_sincronizacao():
    """Busca o status da ultima execucao de sync registrada pelo bussola-financeira-app."""
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT executado_em, status, transacoes_novas, transacoes_atualizadas, mensagem_erro "
            "FROM cartao.sync_log ORDER BY executado_em DESC LIMIT 1;"
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return {"executado_em": None, "status": None}
        executado_local = data_hora_local(row["executado_em"])
        return {
            "executado_em": executado_local.strftime("%d/%m/%Y %H:%M") if executado_local else None,
            "status": row["status"],
            "transacoes_novas": row["transacoes_novas"],
            "transacoes_atualizadas": row["transacoes_atualizadas"],
            "mensagem_erro": row["mensagem_erro"],
        }
    except Exception:
        logger.exception("Falha ao consultar o status da sincronizacao")
        return {"executado_em": None, "status": "erro"}


def disparar_sincronizacao():
    """Chama o endpoint /sync do bussola-financeira-app para forcar uma atualizacao imediata."""
    try:
        headers = {"X-Sync-Secret": os.environ["SYNC_SECRET"]} if os.environ.get("SYNC_SECRET") else {}
        req = urllib.request.Request(BUSSOLA_SYNC_URL, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
        return True, None
    except urllib.error.URLError:
        logger.exception("Falha de rede ao disparar a sincronizacao")
        return False, None
    except Exception:
        logger.exception("Falha inesperada ao disparar a sincronizacao")
        return False, None


def get_conn():
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "postgres"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
    )


def fechar_recursos_banco(conn=None, cur=None, *, rollback=False):
    """Libera cursor e conexao mesmo quando o Postgres falha no meio da operacao."""
    if rollback and conn is not None:
        try:
            conn.rollback()
        except Exception:
            pass
    if cur is not None:
        try:
            cur.close()
        except Exception:
            pass
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


_CAMPOS_SIGILOSOS_AUDITORIA = (
    "senha", "password", "secret", "token", "authorization", "cookie", "api_key", "apikey",
)


def sanitizar_dados_auditoria(valor, chave=""):
    """Converte dados de requisicao em JSON seguro, sem credenciais ou volumes enormes."""
    chave_lower = str(chave or "").lower()
    if any(marcador in chave_lower for marcador in _CAMPOS_SIGILOSOS_AUDITORIA):
        return "[PROTEGIDO]"
    if valor is None or isinstance(valor, (bool, int, float)):
        return valor
    if isinstance(valor, str):
        return valor[:500] + ("…" if len(valor) > 500 else "")
    if isinstance(valor, dict):
        return {
            str(k)[:100]: sanitizar_dados_auditoria(v, k)
            for k, v in list(valor.items())[:50]
        }
    if isinstance(valor, (list, tuple, set)):
        return [sanitizar_dados_auditoria(v, chave) for v in list(valor)[:50]]
    return sanitizar_dados_auditoria(str(valor), chave)


def registrar_mudanca_auditoria(campo, antes, depois):
    """Acrescenta um antes/depois ao evento HTTP atual, somente quando mudou."""
    if not has_request_context() or antes == depois:
        return False
    alteracoes = getattr(g, "audit_alteracoes", None)
    if alteracoes is None:
        alteracoes = {}
        g.audit_alteracoes = alteracoes
    alteracoes[str(campo)[:100]] = {"antes": antes, "depois": depois}
    return True


def marcar_falha_auditoria():
    """Marca uma resposta HTTP 200 com erro de formulario como falha de auditoria."""
    if has_request_context():
        g.audit_sucesso = False
        g.audit_alteracoes = {}


def registrar_auditoria(
    acao,
    recurso,
    *,
    usuario=None,
    recurso_id=None,
    metodo=None,
    rota=None,
    status_http=None,
    sucesso=True,
    ip_origem=None,
    user_agent=None,
    detalhes=None,
):
    """Acrescenta um evento ao historico sem jamais quebrar a operacao original."""
    conn = cur = None
    gravado = False
    try:
        if has_request_context():
            usuario = usuario or session.get("user")
            metodo = metodo or request.method
            rota = rota or request.path
            ip_origem = ip_origem or request.remote_addr
            user_agent = user_agent or request.headers.get("User-Agent")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cartao.audit_log ("
            "usuario, acao, recurso, recurso_id, metodo, rota, status_http, sucesso, "
            "ip_origem, user_agent, detalhes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);",
            (
                usuario,
                str(acao or "evento")[:60],
                str(recurso or "sistema")[:160],
                str(recurso_id)[:160] if recurso_id is not None else None,
                str(metodo)[:10] if metodo else None,
                str(rota)[:300] if rota else None,
                status_http,
                bool(sucesso),
                str(ip_origem)[:80] if ip_origem else None,
                str(user_agent)[:500] if user_agent else None,
                psycopg2.extras.Json(sanitizar_dados_auditoria(detalhes or {})),
            ),
        )
        conn.commit()
        gravado = True
        return True
    except Exception as e:
        print("Aviso: falha ao registrar auditoria:", e)
        return False
    finally:
        fechar_recursos_banco(conn, cur, rollback=not gravado)


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not validar_sessao_atual():
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "erro": "Sessão inválida ou expirada."}), 401
            return redirect("/login")
        return view(*args, **kwargs)
    return wrapped


def migrate():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS cartao.schema_version (versao integer PRIMARY KEY);")
        conn.commit()
        cur.execute("SELECT COALESCE(MAX(versao), 0) FROM cartao.schema_version;")
        versao_atual = cur.fetchone()[0]

        # tudo abaixo ja rodou em producao (schema atual = versao 1). So roda de novo
        # se for um banco novo (versao 0) - evita bater no Postgres com ~30 comandos
        # DDL redundantes a cada boot. Migracoes futuras: adicionar um novo bloco
        # "if versao_atual < N" abaixo deste, terminando em "INSERT ... VALUES (N)".
        if versao_atual < 1:
            cur.execute("ALTER TABLE cartao.transacao ADD COLUMN IF NOT EXISTS duplicada boolean DEFAULT false;")
            # marca lancamentos que entraram por importacao de arquivo (nao vieram do Pluggy),
            # para permitir exclui-los sem risco de "ressuscitarem" numa sincronizacao
            cur.execute("ALTER TABLE cartao.transacao ADD COLUMN IF NOT EXISTS importado boolean DEFAULT false;")
            # natureza definida no proprio lancamento, quando ele foge do padrao da categoria
            # (ex: um PIX de R$ 98 mil que foi a compra de um terreno, e nao consumo)
            cur.execute("ALTER TABLE cartao.transacao ADD COLUMN IF NOT EXISTS natureza text;")
            # usuarios e permissoes. A senha fica em hash - nunca em texto puro.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.usuario ("
                "usuario text PRIMARY KEY, "
                "nome text, "
                "senha_hash text NOT NULL, "
                "perfil text NOT NULL DEFAULT 'leitura', "
                "permissoes text[] NOT NULL DEFAULT '{}', "
                "ativo boolean NOT NULL DEFAULT true, "
                "criado_em timestamptz DEFAULT now(), "
                "ultimo_acesso timestamptz);"
            )
            conn.commit()

            # primeiro boot: cria os acessos que hoje vivem nas variaveis de ambiente,
            # ja como administradores, para ninguem ficar de fora do sistema
            cur.execute("SELECT COUNT(*) FROM cartao.usuario;")
            if cur.fetchone()[0] == 0:
                for login, senha in USERS.items():
                    if not login:
                        continue
                    cur.execute(
                        "INSERT INTO cartao.usuario (usuario, nome, senha_hash, perfil, permissoes) "
                        "VALUES (%s,%s,%s,'admin',%s) ON CONFLICT (usuario) DO NOTHING;",
                        (login, login.capitalize(), hash_senha(senha), permissoes_do_perfil("admin")),
                    )
                conn.commit()
            cur.execute("ALTER TABLE cartao.transacao ADD COLUMN IF NOT EXISTS regra_aplicada_id integer;")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.regra_classificacao ("
                "id serial PRIMARY KEY, padrao text NOT NULL, categoria text NOT NULL, ordem integer DEFAULT 0);"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.regra_dimensao_valor ("
                "regra_id integer NOT NULL REFERENCES cartao.regra_classificacao(id) ON DELETE CASCADE, "
                "dimensao_id integer NOT NULL, valor_id integer NOT NULL, "
                "PRIMARY KEY (regra_id, dimensao_id));"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.cartao_nome ("
                "final4 varchar(4) PRIMARY KEY, prefixo varchar(100) NOT NULL);"
            )
            cur.execute(
                "INSERT INTO cartao.cartao_nome (final4, prefixo) VALUES "
                "('9938', 'Andrea - digital'), "
                "('3200', 'Andrea - físico'), "
                "('6493', 'Ronaldo - físico'), "
                "('7638', 'Ronaldo - digital') "
                "ON CONFLICT (final4) DO NOTHING;"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.grupo_custo ("
                "id serial PRIMARY KEY, nome text UNIQUE NOT NULL, "
                "teto_mensal numeric, teto_anual numeric);"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.subgrupo_custo ("
                "id serial PRIMARY KEY, "
                "grupo_id integer NOT NULL REFERENCES cartao.grupo_custo(id) ON DELETE CASCADE, "
                "nome text NOT NULL, teto_mensal numeric, teto_anual numeric, "
                "UNIQUE(grupo_id, nome));"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.categoria_subgrupo ("
                "categoria text PRIMARY KEY, "
                "subgrupo_id integer REFERENCES cartao.subgrupo_custo(id) ON DELETE SET NULL);"
            )
            # natureza de cada categoria (base do DRE). ON CONFLICT DO NOTHING para
            # nunca sobrescrever uma classificacao que o usuario tenha ajustado.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.categoria_natureza ("
                "categoria text PRIMARY KEY, natureza text NOT NULL DEFAULT 'despesa');"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.categoria ("
                "categoria text PRIMARY KEY, nome_pt text NOT NULL, criado_em timestamptz DEFAULT now());"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.categoria_oculta (categoria text PRIMARY KEY);"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.item_titular ("
                "item_id uuid PRIMARY KEY REFERENCES cartao.pluggy_item(item_id) ON DELETE CASCADE, "
                "titular text NOT NULL);"
            )
            for categoria, natureza in SEED_NATUREZAS.items():
                cur.execute(
                    "INSERT INTO cartao.categoria_natureza (categoria, natureza) VALUES (%s,%s) "
                    "ON CONFLICT (categoria) DO NOTHING;",
                    (categoria, natureza),
                )
            conn.commit()

            # seed inicial de grupos/subgrupos (so roda se a tabela grupo_custo estiver vazia)
            cur.execute("SELECT COUNT(*) FROM cartao.grupo_custo;")
            if cur.fetchone()[0] == 0:
                for grupo_nome, g_teto_mensal, g_teto_anual, subgrupos in SEED_GRUPOS:
                    cur.execute(
                        "INSERT INTO cartao.grupo_custo (nome, teto_mensal, teto_anual) VALUES (%s,%s,%s) RETURNING id;",
                        (grupo_nome, g_teto_mensal, g_teto_anual),
                    )
                    grupo_id = cur.fetchone()[0]
                    for sub_nome, s_teto_mensal, s_teto_anual, categorias in subgrupos:
                        cur.execute(
                            "INSERT INTO cartao.subgrupo_custo (grupo_id, nome, teto_mensal, teto_anual) "
                            "VALUES (%s,%s,%s,%s) RETURNING id;",
                            (grupo_id, sub_nome, s_teto_mensal, s_teto_anual),
                        )
                        subgrupo_id = cur.fetchone()[0]
                        for categoria in categorias:
                            cur.execute(
                                "INSERT INTO cartao.categoria_subgrupo (categoria, subgrupo_id) VALUES (%s,%s) "
                                "ON CONFLICT (categoria) DO UPDATE SET subgrupo_id = EXCLUDED.subgrupo_id;",
                                (categoria, subgrupo_id),
                            )
                conn.commit()

            # juros e tarifas passaram a contar como despesa real: garante o grupo
            # "Despesas Financeiras" tambem nas bases que ja tinham sido semeadas
            cur.execute("SELECT id FROM cartao.grupo_custo WHERE nome = 'Despesas Financeiras';")
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO cartao.grupo_custo (nome) VALUES ('Despesas Financeiras') RETURNING id;"
                )
                grupo_fin_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO cartao.subgrupo_custo (grupo_id, nome) VALUES (%s, 'Juros & Tarifas') RETURNING id;",
                    (grupo_fin_id,),
                )
                sub_fin_id = cur.fetchone()[0]
                for categoria in ("Interests charged", "Credit card fees", "Tax on financial operations"):
                    cur.execute(
                        "INSERT INTO cartao.categoria_subgrupo (categoria, subgrupo_id) VALUES (%s,%s) "
                        "ON CONFLICT (categoria) DO UPDATE SET subgrupo_id = EXCLUDED.subgrupo_id;",
                        (categoria, sub_fin_id),
                    )
                conn.commit()

            # dimensoes adicionais (ex: Responsavel, Projeto/Evento) - independentes do Centro de Custo
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.dimensao ("
                "id serial PRIMARY KEY, nome text UNIQUE NOT NULL, "
                "obrigatoria boolean DEFAULT true, ordem integer DEFAULT 0);"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.dimensao_valor ("
                "id serial PRIMARY KEY, "
                "dimensao_id integer NOT NULL REFERENCES cartao.dimensao(id) ON DELETE CASCADE, "
                "nome text NOT NULL, UNIQUE(dimensao_id, nome));"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.transacao_dimensao ("
                "transacao_id text NOT NULL, "
                "dimensao_id integer NOT NULL REFERENCES cartao.dimensao(id) ON DELETE CASCADE, "
                "valor_id integer REFERENCES cartao.dimensao_valor(id) ON DELETE SET NULL, "
                "PRIMARY KEY (transacao_id, dimensao_id));"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.transacao_rateio ("
                "id bigserial PRIMARY KEY, "
                "transacao_id uuid NOT NULL REFERENCES cartao.transacao(transacao_id) ON DELETE CASCADE, "
                "ordem integer NOT NULL DEFAULT 0, valor_brl numeric(14,2) NOT NULL, "
                "categoria text NOT NULL, observacao text, "
                "criado_em timestamptz NOT NULL DEFAULT now(), atualizado_em timestamptz NOT NULL DEFAULT now(), "
                "UNIQUE(transacao_id, ordem), CHECK (valor_brl <> 0));"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.transacao_rateio_dimensao ("
                "rateio_id bigint NOT NULL REFERENCES cartao.transacao_rateio(id) ON DELETE CASCADE, "
                "dimensao_id integer NOT NULL REFERENCES cartao.dimensao(id) ON DELETE CASCADE, "
                "valor_id integer REFERENCES cartao.dimensao_valor(id) ON DELETE SET NULL, "
                "PRIMARY KEY (rateio_id, dimensao_id));"
            )
            cur.execute(
                "CREATE OR REPLACE VIEW cartao.lancamento_financeiro AS "
                "SELECT t.transacao_id::text AS linha_id, NULL::bigint AS rateio_id, "
                "t.transacao_id, t.account_id, t.data_transacao, t.descricao, t.categoria, "
                "t.valor_brl, t.valor_original, t.moeda_original, t.status, t.tipo, "
                "t.numero_cartao_final, t.conferida, COALESCE(t.duplicada,false) AS duplicada, "
                "t.natureza, t.observacao "
                "FROM cartao.transacao t WHERE NOT EXISTS ("
                "SELECT 1 FROM cartao.transacao_rateio r WHERE r.transacao_id=t.transacao_id) "
                "UNION ALL "
                "SELECT t.transacao_id::text || ':' || r.id::text AS linha_id, r.id AS rateio_id, "
                "t.transacao_id, t.account_id, t.data_transacao, t.descricao, r.categoria, "
                "r.valor_brl, r.valor_brl AS valor_original, 'BRL'::text AS moeda_original, "
                "t.status, t.tipo, t.numero_cartao_final, t.conferida, "
                "COALESCE(t.duplicada,false) AS duplicada, NULL::text AS natureza, r.observacao "
                "FROM cartao.transacao t JOIN cartao.transacao_rateio r ON r.transacao_id=t.transacao_id;"
            )
            cur.execute(
                "CREATE OR REPLACE VIEW cartao.lancamento_financeiro_dimensao AS "
                "SELECT td.transacao_id AS linha_id, td.dimensao_id, td.valor_id "
                "FROM cartao.transacao_dimensao td "
                "WHERE NOT EXISTS (SELECT 1 FROM cartao.transacao_rateio r "
                "WHERE r.transacao_id::text=td.transacao_id) "
                "UNION ALL "
                "SELECT r.transacao_id::text || ':' || r.id::text AS linha_id, rd.dimensao_id, rd.valor_id "
                "FROM cartao.transacao_rateio r JOIN cartao.transacao_rateio_dimensao rd ON rd.rateio_id=r.id;"
            )
            # Bancos antigos podem ter o nome anterior. Em banco novo, esta
            # tabela acabou de ser criada; por isso a renomeacao precisa ficar
            # depois do CREATE, e nao no inicio da migracao.
            cur.execute("UPDATE cartao.dimensao SET nome = 'Projeto' WHERE nome = 'Projeto / Evento';")
            conn.commit()

            # conta sintetica para lancamentos manuais (dinheiro em especie), fora do Pluggy
            cur.execute(
                "INSERT INTO cartao.pluggy_item (item_id, connector_name, status) VALUES "
                "('00000000-0000-0000-0000-000000000001', 'Manual', 'OK') "
                "ON CONFLICT (item_id) DO NOTHING;"
            )
            cur.execute(
                "INSERT INTO cartao.conta (account_id, item_id, nome, tipo, numero_final) VALUES "
                "('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', "
                "'Dinheiro', 'MANUAL', NULL) "
                "ON CONFLICT (account_id) DO NOTHING;"
            )
            conn.commit()

            cur.execute("SELECT COUNT(*) FROM cartao.dimensao;")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO cartao.dimensao (nome, obrigatoria, ordem) VALUES ('Responsável', true, 1) RETURNING id;")
                resp_id = cur.fetchone()[0]
                for nome in ("Ronaldo", "Andrea", "Amanda", "Compartilhado"):
                    cur.execute("INSERT INTO cartao.dimensao_valor (dimensao_id, nome) VALUES (%s,%s);", (resp_id, nome))

                cur.execute("INSERT INTO cartao.dimensao (nome, obrigatoria, ordem) VALUES ('Projeto', false, 2) RETURNING id;")
                proj_id = cur.fetchone()[0]
                for nome in ("Geral", "Viagem Chile 2027"):
                    cur.execute("INSERT INTO cartao.dimensao_valor (dimensao_id, nome) VALUES (%s,%s);", (proj_id, nome))
                conn.commit()
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (1);")
            conn.commit()

        if versao_atual < 2:
            # teto de gasto passa a ser por valor de dimensao (ex: "Ronaldo: R$3000/mes"),
            # nao mais por centro de custo - ver conversa que motivou essa mudanca.
            cur.execute("ALTER TABLE cartao.dimensao_valor ADD COLUMN IF NOT EXISTS teto_mensal numeric;")
            cur.execute("ALTER TABLE cartao.dimensao_valor ADD COLUMN IF NOT EXISTS teto_anual numeric;")
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (2);")
            conn.commit()

        if versao_atual < 3:
            # Fechamento/vencimento da fatura passam a ser por cartao - antes o
            # fechamento era uma constante unica no codigo, o que so funcionava com
            # um cartao (Unicred, Nubank Ronaldo e Nubank Andrea fecham em dias
            # diferentes). As datas vem do Pluggy: vencimento_fatura ja existia,
            # fechamento_fatura foi adicionado aqui.
            cur.execute("ALTER TABLE cartao.conta ADD COLUMN IF NOT EXISTS fechamento_fatura DATE;")
            # dia_fechamento/dia_vencimento foram uma tentativa de sobrescrita manual,
            # DESCONTINUADA logo depois: a tela ficou confusa e o dado do banco e mais
            # confiavel. Os ALTER continuam aqui so porque a migracao ja rodou em
            # producao - reescrever migracao aplicada criaria divergencia de schema.
            # As colunas ficam sem uso; nada le nem grava nelas.
            cur.execute("ALTER TABLE cartao.conta ADD COLUMN IF NOT EXISTS dia_fechamento integer;")
            cur.execute("ALTER TABLE cartao.conta ADD COLUMN IF NOT EXISTS dia_vencimento integer;")
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (3);")
            conn.commit()

        if versao_atual < 4:
            # Nome de categoria unico tambem no banco, nao so na validacao da tela.
            #
            # ALCANCE: a tabela cartao.categoria guarda apenas os APELIDOS - so tem
            # linha para categoria que foi renomeada. Entao este indice impede duas
            # renomeacoes para o mesmo nome, mas nao impede renomear para o nome
            # padrao de uma categoria que nunca foi renomeada. A checagem completa
            # e a de categoria_com_nome(), na aplicacao; isto aqui e a rede de baixo.
            #
            # lower() para "Mercado" e "mercado" colidirem. Acento nao entra: exigiria
            # a extensao unaccent, que pode nao estar instalada no Postgres do Coolify.
            #
            # Passo com try proprio: se sobrar nome repetido na base, o indice falha e
            # so este passo e desfeito - o app sobe, avisa no log e tenta de novo no
            # boot seguinte, depois que a base for limpa.
            try:
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS categoria_nome_pt_unico "
                    "ON cartao.categoria (lower(nome_pt));"
                )
                cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (4);")
                conn.commit()
            except Exception as e:
                conn.rollback()
                print("Aviso: indice unico de nome de categoria nao aplicado "
                      "(ha nomes repetidos na base?):", e)

        if versao_atual < 5:
            # Icone (emoji) opcional por valor de dimensao. Emoji e texto puro,
            # entao funciona dentro de <option> - diferente do selo do banco, que
            # e HTML e precisa de campo separado. Por isso aqui basta prefixar.
            cur.execute("ALTER TABLE cartao.dimensao_valor ADD COLUMN IF NOT EXISTS icone varchar(8);")
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (5);")
            conn.commit()

        if versao_atual < 6:
            # Leva para bancos antigos as naturezas que foram acrescentadas ao
            # codigo depois da primeira migracao. Nunca sobrescreve uma decisao
            # feita pelo usuario.
            for categoria, natureza in SEED_NATUREZAS.items():
                cur.execute(
                    "INSERT INTO cartao.categoria_natureza (categoria, natureza) VALUES (%s,%s) "
                    "ON CONFLICT (categoria) DO NOTHING;",
                    (categoria, natureza),
                )

            # Lancamentos manuais antigos gravavam entrada e saida positivas.
            # Contas bancarias usam credito positivo e debito negativo; corrige
            # apenas a conta sintetica Manual e somente linhas com sinal errado.
            cur.execute(
                "UPDATE cartao.transacao SET valor_original = -ABS(valor_original), "
                "valor_brl = -ABS(valor_brl), atualizado_em = now() "
                "WHERE account_id = %s AND tipo = 'DEBIT' "
                "AND (valor_original > 0 OR valor_brl > 0);",
                (CONTA_MANUAL_ID,),
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (6);")
            conn.commit()

        if versao_atual < 7:
            # Inclui novas classificacoes-padrao sem alterar categorias que o
            # usuario eventualmente ja tenha decidido de outra forma.
            for categoria, natureza in SEED_NATUREZAS.items():
                cur.execute(
                    "INSERT INTO cartao.categoria_natureza (categoria, natureza) VALUES (%s,%s) "
                    "ON CONFLICT (categoria) DO NOTHING;",
                    (categoria, natureza),
                )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (7);")
            conn.commit()

        if versao_atual < 8:
            # Completa as naturezas confirmadas na revisao, preservando qualquer
            # escolha que ja tenha sido feita manualmente no cadastro.
            for categoria, natureza in SEED_NATUREZAS.items():
                cur.execute(
                    "INSERT INTO cartao.categoria_natureza (categoria, natureza) VALUES (%s,%s) "
                    "ON CONFLICT (categoria) DO NOTHING;",
                    (categoria, natureza),
                )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (8);")
            conn.commit()

        if versao_atual < 9:
            # Preenche apenas categorias ainda sem centro. Um vinculo manual ja
            # existente tem prioridade e nunca e movido por esta migracao.
            for categoria, subgrupo_nome in CENTROS_CONFIRMADOS.items():
                cur.execute(
                    "SELECT id FROM cartao.subgrupo_custo WHERE nome = %s ORDER BY id LIMIT 1;",
                    (subgrupo_nome,),
                )
                subgrupo = cur.fetchone()
                if not subgrupo:
                    print("Aviso: subgrupo nao encontrado para", categoria, subgrupo_nome)
                    continue
                cur.execute(
                    "INSERT INTO cartao.categoria_subgrupo (categoria, subgrupo_id) VALUES (%s,%s) "
                    "ON CONFLICT (categoria) DO UPDATE SET subgrupo_id = EXCLUDED.subgrupo_id "
                    "WHERE cartao.categoria_subgrupo.subgrupo_id IS NULL;",
                    (categoria, subgrupo[0]),
                )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (9);")
            conn.commit()

        if versao_atual < 10:
            # Historico separado dos dados financeiros. Nao ha FK para usuario:
            # excluir uma conta nunca pode apagar o rastro de auditoria dela.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.audit_log ("
                "id bigserial PRIMARY KEY, "
                "ocorrido_em timestamptz NOT NULL DEFAULT now(), "
                "usuario text, acao text NOT NULL, recurso text NOT NULL, recurso_id text, "
                "metodo varchar(10), rota text, status_http integer, sucesso boolean NOT NULL DEFAULT true, "
                "ip_origem text, user_agent text, detalhes jsonb NOT NULL DEFAULT '{}'::jsonb);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_log_ocorrido "
                "ON cartao.audit_log (ocorrido_em DESC);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_log_usuario "
                "ON cartao.audit_log (usuario, ocorrido_em DESC);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_log_acao "
                "ON cartao.audit_log (acao, ocorrido_em DESC);"
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (10);")
            conn.commit()

        if versao_atual < 11:
            # A escolha humana de categoria tem prioridade sobre regras criadas
            # depois. Lançamentos já conferidos também ficam explicitamente
            # protegidos; os demais continuam elegíveis às regras existentes.
            cur.execute(
                "ALTER TABLE cartao.transacao ADD COLUMN IF NOT EXISTS "
                "categoria_manual boolean NOT NULL DEFAULT false;"
            )
            cur.execute(
                "UPDATE cartao.transacao SET categoria_manual = true "
                "WHERE conferida = true;"
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (11);")
            conn.commit()

        if versao_atual < 12:
            # Uma regra excluída não pode deixar lançamentos presos a um ID que
            # já não existe. A FK também protege exclusões feitas fora da tela.
            cur.execute(
                "UPDATE cartao.transacao t SET regra_aplicada_id = NULL "
                "WHERE regra_aplicada_id IS NOT NULL AND NOT EXISTS ("
                "SELECT 1 FROM cartao.regra_classificacao r WHERE r.id = t.regra_aplicada_id);"
            )
            cur.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = 'transacao_regra_aplicada_fk';"
            )
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE cartao.transacao ADD CONSTRAINT transacao_regra_aplicada_fk "
                    "FOREIGN KEY (regra_aplicada_id) REFERENCES cartao.regra_classificacao(id) "
                    "ON DELETE SET NULL;"
                )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (12);")
            conn.commit()

        if versao_atual < 13:
            # Regras podem distinguir a mesma descrição pelo valor absoluto do
            # lançamento (conta corrente costuma guardar débitos negativos).
            cur.execute(
                "ALTER TABLE cartao.regra_classificacao "
                "ADD COLUMN IF NOT EXISTS valor_operador varchar(4);"
            )
            cur.execute(
                "ALTER TABLE cartao.regra_classificacao "
                "ADD COLUMN IF NOT EXISTS valor_limite numeric(14,2);"
            )

            # Decisão de classificação: GuilhermeDaSilva abaixo de R$ 120 é
            # Água; acima de R$ 120 é Gás. R$ 120 exatos ficam sem regra até uma
            # decisão explícita. Nada aqui altera o OK/conferida.
            cur.execute(
                "INSERT INTO cartao.categoria (categoria, nome_pt) VALUES "
                "('Agua', 'Água'), ('Agua / Gas', 'Gás') "
                "ON CONFLICT (categoria) DO UPDATE SET nome_pt = EXCLUDED.nome_pt;"
            )
            cur.execute(
                "INSERT INTO cartao.categoria_natureza (categoria, natureza) VALUES "
                "('Agua', 'despesa'), ('Agua / Gas', 'despesa') "
                "ON CONFLICT (categoria) DO NOTHING;"
            )
            cur.execute(
                "INSERT INTO cartao.categoria_subgrupo (categoria, subgrupo_id) "
                "SELECT c.categoria, s.id FROM (VALUES ('Agua'), ('Agua / Gas')) c(categoria) "
                "JOIN cartao.subgrupo_custo s ON lower(s.nome) = lower('Casa') "
                "JOIN cartao.grupo_custo g ON g.id = s.grupo_id "
                "WHERE lower(g.nome) = lower('Moradia & Utilidades') "
                "ON CONFLICT (categoria) DO UPDATE SET subgrupo_id = EXCLUDED.subgrupo_id;"
            )
            for operador, limite, categoria in (("lt", 120, "Agua"), ("gt", 120, "Agua / Gas")):
                cur.execute(
                    "INSERT INTO cartao.regra_classificacao "
                    "(padrao, categoria, valor_operador, valor_limite, ordem) "
                    "SELECT 'GuilhermeDaSilva', %s, %s, %s, 100 "
                    "WHERE NOT EXISTS (SELECT 1 FROM cartao.regra_classificacao "
                    "WHERE lower(padrao)=lower('GuilhermeDaSilva') "
                    "AND valor_operador=%s AND valor_limite=%s) RETURNING id;",
                    (categoria, operador, limite, operador, limite),
                )
                nova_regra = cur.fetchone()
                if nova_regra:
                    for dim_nome, valor_nome in (
                        ("Responsável", "Família"), ("Projeto", "Casa"), ("Portfólio", "Moradia")
                    ):
                        cur.execute(
                            "INSERT INTO cartao.regra_dimensao_valor (regra_id, dimensao_id, valor_id) "
                            "SELECT %s, d.id, dv.id FROM cartao.dimensao d "
                            "JOIN cartao.dimensao_valor dv ON dv.dimensao_id=d.id "
                            "WHERE lower(d.nome)=lower(%s) AND lower(dv.nome)=lower(%s) "
                            "ON CONFLICT (regra_id, dimensao_id) DO UPDATE SET valor_id=EXCLUDED.valor_id;",
                            (nova_regra[0], dim_nome, valor_nome),
                        )
            for dim_nome, valor_nome in (
                ("Responsável", "Família"), ("Projeto", "Casa"), ("Portfólio", "Moradia")
            ):
                cur.execute(
                    "INSERT INTO cartao.dimensao_valor (dimensao_id,nome) "
                    "SELECT id,%s FROM cartao.dimensao WHERE lower(nome)=lower(%s) "
                    "ON CONFLICT (dimensao_id,nome) DO NOTHING;",
                    (valor_nome, dim_nome),
                )
            cur.execute(
                "UPDATE cartao.transacao t SET "
                "categoria=CASE WHEN ABS(COALESCE(t.valor_brl,t.valor_original)) < 120 "
                "THEN 'Agua' ELSE 'Agua / Gas' END, categoria_manual=false, "
                "regra_aplicada_id=(SELECT r.id FROM cartao.regra_classificacao r "
                " WHERE lower(r.padrao)=lower('GuilhermeDaSilva') "
                " AND ((r.valor_operador='lt' AND ABS(COALESCE(t.valor_brl,t.valor_original)) < r.valor_limite) "
                "   OR (r.valor_operador='gt' AND ABS(COALESCE(t.valor_brl,t.valor_original)) > r.valor_limite)) "
                " ORDER BY r.ordem,r.id LIMIT 1) "
                "WHERE t.descricao ILIKE '%GuilhermeDaSilva%' "
                "AND ABS(COALESCE(t.valor_brl,t.valor_original)) <> 120;"
            )
            guilherme_ajustados = max(cur.rowcount, 0)
            for dim_nome, valor_nome in (
                ("Responsável", "Família"), ("Projeto", "Casa"), ("Portfólio", "Moradia")
            ):
                cur.execute(
                    "INSERT INTO cartao.transacao_dimensao (transacao_id,dimensao_id,valor_id) "
                    "SELECT t.transacao_id::text,d.id,dv.id FROM cartao.transacao t "
                    "JOIN cartao.dimensao d ON lower(d.nome)=lower(%s) "
                    "JOIN cartao.dimensao_valor dv ON dv.dimensao_id=d.id AND lower(dv.nome)=lower(%s) "
                    "WHERE t.descricao ILIKE '%%GuilhermeDaSilva%%' "
                    "AND ABS(COALESCE(t.valor_brl,t.valor_original)) <> 120 "
                    "ON CONFLICT (transacao_id,dimensao_id) DO UPDATE SET valor_id=EXCLUDED.valor_id;",
                    (dim_nome, valor_nome),
                )
            cur.execute(
                "INSERT INTO cartao.audit_log (usuario,acao,recurso,detalhes) "
                "VALUES ('sistema','migracao','Classificação GuilhermeDaSilva',"
                "jsonb_build_object('lancamentos_ajustados',%s,'ok_preservado',true));",
                (guilherme_ajustados,),
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (13);")
            conn.commit()

        if versao_atual < 14:
            # Desativa (sem apagar) a regra antiga, sem filtro de valor. Sem
            # isso ela (ID menor) ganharia a prioridade e todo GuilhermeDaSilva
            # futuro iria para a mesma categoria. O registro fica recuperável.
            cur.execute(
                "ALTER TABLE cartao.regra_classificacao "
                "ADD COLUMN IF NOT EXISTS ativa boolean NOT NULL DEFAULT true;"
            )
            cur.execute(
                "SELECT id FROM cartao.regra_classificacao "
                "WHERE lower(padrao)=lower('GuilhermeDaSilva') "
                "AND valor_operador IS NULL AND valor_limite IS NULL;"
            )
            regras_antigas = [r[0] for r in cur.fetchall()]
            if regras_antigas:
                cur.execute(
                    "UPDATE cartao.regra_classificacao SET ativa=false WHERE id=ANY(%s);",
                    (regras_antigas,),
                )

            # Os nomes podem estar com ou sem acento dependendo de quando a
            # dimensão foi criada. Comparamos em Python pela mesma chave usada
            # na interface e garantimos as três dimensões nas duas regras.
            cur.execute(
                "SELECT d.id AS dimensao_id,d.nome AS dimensao_nome,dv.id AS valor_id,dv.nome AS valor_nome "
                "FROM cartao.dimensao d JOIN cartao.dimensao_valor dv ON dv.dimensao_id=d.id;"
            )
            valores_dimensao = cur.fetchall()
            mapa_desejado = {"responsavel": "familia", "projeto": "casa", "portfolio": "moradia"}
            selecionados = []
            for dimensao_id, dimensao_nome, valor_id, valor_nome in valores_dimensao:
                alvo = mapa_desejado.get(chave_alfa(dimensao_nome))
                if alvo and chave_alfa(valor_nome) == alvo:
                    selecionados.append((dimensao_id, valor_id))
            cur.execute(
                "SELECT id FROM cartao.regra_classificacao "
                "WHERE lower(padrao)=lower('GuilhermeDaSilva') "
                "AND valor_operador IN ('lt','gt') AND valor_limite=120;"
            )
            regras_novas = [r[0] for r in cur.fetchall()]
            for regra_id in regras_novas:
                for dimensao_id, valor_id in selecionados:
                    cur.execute(
                        "INSERT INTO cartao.regra_dimensao_valor (regra_id,dimensao_id,valor_id) "
                        "VALUES (%s,%s,%s) ON CONFLICT (regra_id,dimensao_id) "
                        "DO UPDATE SET valor_id=EXCLUDED.valor_id;",
                        (regra_id, dimensao_id, valor_id),
                    )
            cur.execute(
                "INSERT INTO cartao.audit_log (usuario,acao,recurso,detalhes) "
                "VALUES ('sistema','migracao','Regras GuilhermeDaSilva',"
                "jsonb_build_object('regras_antigas_desativadas',%s,'regras_por_valor',%s));",
                (len(regras_antigas), len(regras_novas)),
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (14);")
            conn.commit()

        if versao_atual < 15:
            # A estrutura de rateio precisa ser criada também nos bancos que já
            # passaram pela instalação inicial. Mantemos o mesmo DDL no bloco 1
            # para instalações novas e repetimos aqui de forma idempotente para
            # atualizar a produção existente.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.transacao_rateio ("
                "id bigserial PRIMARY KEY, "
                "transacao_id uuid NOT NULL REFERENCES cartao.transacao(transacao_id) ON DELETE CASCADE, "
                "ordem integer NOT NULL DEFAULT 0, valor_brl numeric(14,2) NOT NULL, "
                "categoria text NOT NULL, observacao text, "
                "criado_em timestamptz NOT NULL DEFAULT now(), atualizado_em timestamptz NOT NULL DEFAULT now(), "
                "UNIQUE(transacao_id, ordem), CHECK (valor_brl <> 0));"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.transacao_rateio_dimensao ("
                "rateio_id bigint NOT NULL REFERENCES cartao.transacao_rateio(id) ON DELETE CASCADE, "
                "dimensao_id integer NOT NULL REFERENCES cartao.dimensao(id) ON DELETE CASCADE, "
                "valor_id integer REFERENCES cartao.dimensao_valor(id) ON DELETE SET NULL, "
                "PRIMARY KEY (rateio_id, dimensao_id));"
            )
            cur.execute(
                "CREATE OR REPLACE VIEW cartao.lancamento_financeiro AS "
                "SELECT t.transacao_id::text AS linha_id, NULL::bigint AS rateio_id, "
                "t.transacao_id, t.account_id, t.data_transacao, t.descricao, t.categoria, "
                "t.valor_brl, t.valor_original, t.moeda_original, t.status, t.tipo, "
                "t.numero_cartao_final, t.conferida, COALESCE(t.duplicada,false) AS duplicada, "
                "t.natureza, t.observacao "
                "FROM cartao.transacao t WHERE NOT EXISTS ("
                "SELECT 1 FROM cartao.transacao_rateio r WHERE r.transacao_id=t.transacao_id) "
                "UNION ALL "
                "SELECT t.transacao_id::text || ':' || r.id::text AS linha_id, r.id AS rateio_id, "
                "t.transacao_id, t.account_id, t.data_transacao, t.descricao, r.categoria, "
                "r.valor_brl, r.valor_brl AS valor_original, 'BRL'::text AS moeda_original, "
                "t.status, t.tipo, t.numero_cartao_final, t.conferida, "
                "COALESCE(t.duplicada,false) AS duplicada, NULL::text AS natureza, r.observacao "
                "FROM cartao.transacao t JOIN cartao.transacao_rateio r ON r.transacao_id=t.transacao_id;"
            )
            cur.execute(
                "CREATE OR REPLACE VIEW cartao.lancamento_financeiro_dimensao AS "
                "SELECT td.transacao_id AS linha_id, td.dimensao_id, td.valor_id "
                "FROM cartao.transacao_dimensao td "
                "WHERE NOT EXISTS (SELECT 1 FROM cartao.transacao_rateio r "
                "WHERE r.transacao_id::text=td.transacao_id) "
                "UNION ALL "
                "SELECT r.transacao_id::text || ':' || r.id::text AS linha_id, rd.dimensao_id, rd.valor_id "
                "FROM cartao.transacao_rateio r JOIN cartao.transacao_rateio_dimensao rd ON rd.rateio_id=r.id;"
            )
            cur.execute(
                "INSERT INTO cartao.audit_log (usuario,acao,recurso,detalhes) "
                "VALUES ('sistema','migracao','Estrutura de rateio',"
                "jsonb_build_object('versao',15));"
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (15);")
            conn.commit()

        if versao_atual < 16:
            # sincronizado_em e sobrescrito a cada sync (mesmo em linha que ja existia),
            # entao sozinho ele nao diz se o registro e novo ou so foi confirmado de novo
            # pelo Pluggy. primeiro_sincronizado_em e gravado uma unica vez, no INSERT, e
            # nunca mais atualizado - e o jeito de saber quando aquele transacao_id
            # realmente apareceu no banco pela primeira vez. Motivado por um caso real:
            # o Pluggy reenviou compras ja classificadas e conferidas com um id novo e
            # outra descricao (ex: "Compra a Vista - X" vs "A vista sem juros - Visa - X"),
            # criando linha pendente nova que parecia (e nao era) o ajuste antigo apagado.
            cur.execute(
                "ALTER TABLE cartao.transacao ADD COLUMN IF NOT EXISTS primeiro_sincronizado_em timestamptz;"
            )
            # backfill: para quem ja existe, a melhor aproximacao que temos e o
            # sincronizado_em atual (ou criado_em, se vier do Pluggy e for mais antigo).
            cur.execute(
                "UPDATE cartao.transacao SET primeiro_sincronizado_em = "
                "LEAST(COALESCE(sincronizado_em, now()), COALESCE(criado_em, sincronizado_em, now())) "
                "WHERE primeiro_sincronizado_em IS NULL;"
            )
            cur.execute(
                "INSERT INTO cartao.audit_log (usuario,acao,recurso,detalhes) "
                "VALUES ('sistema','migracao','Coluna primeiro_sincronizado_em',"
                "jsonb_build_object('versao',16));"
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (16);")
            conn.commit()

        if versao_atual < 17:
            # Snapshot diario do total de lancamentos, para o card "Lancamentos"
            # da tela principal mostrar quanto cresceu desde ontem/semana/mes -
            # sem isso nao ha como saber quantos existiam num dia passado, so o
            # total de agora. Uma linha por dia, sobrescrita a cada carregamento
            # da tela (ver core.registrar_metrica_diaria).
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.metrica_diaria ("
                "data date PRIMARY KEY, "
                "total_transacoes integer NOT NULL, "
                "total_conferidas integer NOT NULL, "
                "atualizado_em timestamptz NOT NULL DEFAULT now());"
            )
            cur.execute(
                "INSERT INTO cartao.audit_log (usuario,acao,recurso,detalhes) "
                "VALUES ('sistema','migracao','Tabela metrica_diaria',"
                "jsonb_build_object('versao',17));"
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (17);")
            conn.commit()

        if versao_atual < 18:
            # Guarda os lancamentos extraidos de uma fatura em PDF importada (nao
            # o PDF em si), pra ter historico e poder reabrir a conciliacao sem
            # reenviar o arquivo. Uma fatura por conta+mes+ano (reenviar substitui
            # as linhas antigas, ON DELETE CASCADE cuida disso).
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.fatura_importada ("
                "id bigserial PRIMARY KEY, "
                "account_id uuid NOT NULL REFERENCES cartao.conta(account_id), "
                "mes_referencia integer NOT NULL, "
                "ano_referencia integer NOT NULL, "
                "total numeric(14,2) NOT NULL, "
                "cartao_final4 text, "
                "arquivo_nome text, "
                "importado_por text, "
                "importado_em timestamptz NOT NULL DEFAULT now(), "
                "UNIQUE(account_id, mes_referencia, ano_referencia));"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS cartao.fatura_linha ("
                "id bigserial PRIMARY KEY, "
                "fatura_id bigint NOT NULL REFERENCES cartao.fatura_importada(id) ON DELETE CASCADE, "
                "data date NOT NULL, "
                "descricao text NOT NULL, "
                "descricao_base text, "
                "parcela_atual integer, "
                "parcela_total integer, "
                "valor numeric(14,2) NOT NULL, "
                "titular text, "
                # preenchido quando o usuario cria um lancamento manual a partir
                # desta linha (ver /relatorios/conciliar-fatura) - evita oferecer
                # "criar lancamento" de novo pra quem ja foi resolvido
                "transacao_id_criado uuid REFERENCES cartao.transacao(transacao_id));"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_fatura_linha_fatura ON cartao.fatura_linha(fatura_id);")
            cur.execute(
                "INSERT INTO cartao.audit_log (usuario,acao,recurso,detalhes) "
                "VALUES ('sistema','migracao','Historico de faturas importadas',"
                "jsonb_build_object('versao',18));"
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (18);")
            conn.commit()

        if versao_atual < 19:
            # "Conferido" da tela de cobrancas repetidas na fatura (ver
            # views/relatorios.py:_repetidas_na_fatura). E' so um registro de quem
            # ja revisou aquele grupo de cobranca repetida e quando - nao mexe em
            # transacao nem em dinheiro, e' historico de revisao humana, igual ao
            # OK de lancamento (so quem confere marca).
            cur.execute(
                "ALTER TABLE cartao.fatura_linha "
                "ADD COLUMN IF NOT EXISTS conferida_repeticao boolean NOT NULL DEFAULT false;"
            )
            cur.execute(
                "ALTER TABLE cartao.fatura_linha "
                "ADD COLUMN IF NOT EXISTS conferida_repeticao_por text;"
            )
            cur.execute(
                "ALTER TABLE cartao.fatura_linha "
                "ADD COLUMN IF NOT EXISTS conferida_repeticao_em timestamptz;"
            )
            cur.execute(
                "INSERT INTO cartao.audit_log (usuario,acao,recurso,detalhes) "
                "VALUES ('sistema','migracao','Conferido de cobranca repetida na fatura',"
                "jsonb_build_object('versao',19));"
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (19);")
            conn.commit()

        if versao_atual < 20:
            # Vinculo Projeto -> Portfolio padrao: cada valor de Projeto pode
            # apontar para o Portfolio que ele "sempre" usa (ex: Projeto "Jeep"
            # -> Portfolio "Veiculos"), pra nao ter que escolher os dois toda
            # vez. So faz sentido em valor da dimensao Projeto apontando para
            # um valor da dimensao Portfolio - por isso e' auto-referencia na
            # mesma tabela (dimensao_valor serve qualquer dimensao) em vez de
            # uma coluna por dimensao.
            cur.execute(
                "ALTER TABLE cartao.dimensao_valor "
                "ADD COLUMN IF NOT EXISTS portfolio_valor_id integer "
                "REFERENCES cartao.dimensao_valor(id) ON DELETE SET NULL;"
            )
            cur.execute(
                "INSERT INTO cartao.audit_log (usuario,acao,recurso,detalhes) "
                "VALUES ('sistema','migracao','Vinculo Projeto -> Portfolio padrao',"
                "jsonb_build_object('versao',20));"
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (20);")
            conn.commit()

        if versao_atual < 21:
            # Datas do ciclo da fatura, mostradas em "Faturas ja importadas"
            # (/relatorios/conciliar-fatura). Inicio/fim e vencimento vem do
            # proprio PDF (fatura_unicred.py); fechamento a Unicred nao
            # imprime em lugar nenhum do documento, entao fica so editavel
            # manualmente na tela - por isso as 4 colunas sao nullable.
            cur.execute("ALTER TABLE cartao.fatura_importada ADD COLUMN IF NOT EXISTS periodo_inicio date;")
            cur.execute("ALTER TABLE cartao.fatura_importada ADD COLUMN IF NOT EXISTS periodo_fim date;")
            cur.execute("ALTER TABLE cartao.fatura_importada ADD COLUMN IF NOT EXISTS fechamento date;")
            cur.execute("ALTER TABLE cartao.fatura_importada ADD COLUMN IF NOT EXISTS vencimento date;")
            cur.execute(
                "INSERT INTO cartao.audit_log (usuario,acao,recurso,detalhes) "
                "VALUES ('sistema','migracao','Datas do ciclo da fatura importada',"
                "jsonb_build_object('versao',21));"
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (21);")
            conn.commit()

        if versao_atual < 22:
            # "Fechamento" saiu (era sempre igual a periodo_fim na pratica -
            # a Unicred nunca imprime essa data separada, entao a coluna so
            # duplicava periodo_fim; migracao ja aplicada, entao aqui e' DROP,
            # nao reescrita). As datas do ciclo (inicio/fim/vencimento) agora
            # sao so leitura - vem inteiras do PDF, ninguem edita na tela.
            #
            # pdf_arquivo guarda o PDF original (nao so as linhas extraidas)
            # para poder reprocessar sem pedir upload de novo - o app roda em
            # container no Coolify sem volume persistente confirmado, e o
            # arquivo (500KB-1MB) cabe tranquilo como bytea no Postgres, que
            # ja sobrevive a qualquer deploy.
            cur.execute("ALTER TABLE cartao.fatura_importada DROP COLUMN IF EXISTS fechamento;")
            cur.execute("ALTER TABLE cartao.fatura_importada ADD COLUMN IF NOT EXISTS pdf_arquivo bytea;")
            cur.execute(
                "INSERT INTO cartao.audit_log (usuario,acao,recurso,detalhes) "
                "VALUES ('sistema','migracao','Remove fechamento e guarda PDF original da fatura',"
                "jsonb_build_object('versao',22));"
            )
            cur.execute("INSERT INTO cartao.schema_version (versao) VALUES (22);")
            conn.commit()

        cur.close()
        conn.close()
    except Exception as e:
        print("Aviso: falha ao rodar migracao:", e)


def aplicar_regras(cur):
    """Aplica regras de classificacao automatica a lancamentos pendentes ainda nao tocados por nenhuma regra.
    Nunca sobrescreve categoria escolhida pelo usuario nem transacao confirmada, e nunca
    classifica sozinha uma linha que pode ser a mesma compra de outra ja existente (mesma
    conta, data e valor) - essas ficam pendentes para revisao manual."""
    # Uma regra antiga pode apontar para uma dimensao/valor removido. Isolamos a
    # aplicacao em um savepoint para que esse dado ruim nao aborte todas as
    # consultas da pagina que chamou esta funcao.
    resultado = {"lancamentos": 0, "dimensoes": 0, "erro": None, "duplicatas_ignoradas": []}
    cur.execute("SAVEPOINT aplicar_regras")
    try:
        # Diagnostico: lancamentos que bateriam com uma regra mas foram pulados
        # por parecerem duplicata de outra linha ja existente (mesma conta, data
        # e valor). Nao decide nada sozinho - so fica visivel em Logs para o
        # usuario avaliar se e mesmo duplicata ou coincidencia de valor.
        cur.execute(
            "SELECT DISTINCT t.transacao_id::text, t.descricao, t.data_transacao "
            "FROM cartao.transacao t "
            "JOIN cartao.regra_classificacao r ON COALESCE(r.ativa,true)=true "
            " AND t.descricao ILIKE '%%' || r.padrao || '%%' "
            " AND (r.valor_operador IS NULL OR r.valor_limite IS NULL OR "
            "   CASE r.valor_operador "
            "     WHEN 'lt' THEN ABS(COALESCE(t.valor_brl,t.valor_original)) < r.valor_limite "
            "     WHEN 'lte' THEN ABS(COALESCE(t.valor_brl,t.valor_original)) <= r.valor_limite "
            "     WHEN 'gt' THEN ABS(COALESCE(t.valor_brl,t.valor_original)) > r.valor_limite "
            "     WHEN 'gte' THEN ABS(COALESCE(t.valor_brl,t.valor_original)) >= r.valor_limite "
            "     WHEN 'eq' THEN ABS(COALESCE(t.valor_brl,t.valor_original)) = r.valor_limite "
            "     ELSE false END) "
            "WHERE t.regra_aplicada_id IS NULL AND t.conferida = false "
            "  AND COALESCE(t.categoria_manual, false) = false "
            "  AND EXISTS ("
            "    SELECT 1 FROM cartao.transacao t2 "
            "    WHERE t2.transacao_id <> t.transacao_id "
            "      AND t2.account_id = t.account_id "
            "      AND t2.data_transacao = t.data_transacao "
            "      AND COALESCE(t2.valor_brl, t2.valor_original) = COALESCE(t.valor_brl, t.valor_original)"
            "  ) LIMIT 50;"
        )
        resultado["duplicatas_ignoradas"] = [
            {"transacao_id": row[0], "descricao": row[1], "data": row[2].isoformat() if row[2] else None}
            for row in cur.fetchall()
        ]
        cur.execute(
            "WITH match AS ("
            "  SELECT DISTINCT ON (t.transacao_id) t.transacao_id, r.id AS regra_id, r.categoria "
            "  FROM cartao.transacao t "
            "  JOIN cartao.regra_classificacao r ON COALESCE(r.ativa,true)=true "
            "   AND t.descricao ILIKE '%%' || r.padrao || '%%' "
            "   AND (r.valor_operador IS NULL OR r.valor_limite IS NULL OR "
            "     CASE r.valor_operador "
            "       WHEN 'lt' THEN ABS(COALESCE(t.valor_brl,t.valor_original)) < r.valor_limite "
            "       WHEN 'lte' THEN ABS(COALESCE(t.valor_brl,t.valor_original)) <= r.valor_limite "
            "       WHEN 'gt' THEN ABS(COALESCE(t.valor_brl,t.valor_original)) > r.valor_limite "
            "       WHEN 'gte' THEN ABS(COALESCE(t.valor_brl,t.valor_original)) >= r.valor_limite "
            "       WHEN 'eq' THEN ABS(COALESCE(t.valor_brl,t.valor_original)) = r.valor_limite "
            "       ELSE false END) "
            "  WHERE t.regra_aplicada_id IS NULL AND t.conferida = false "
            "    AND COALESCE(t.categoria_manual, false) = false "
            # O Pluggy as vezes reenvia a mesma compra com um id novo e uma
            # descricao diferente (ex: 'Compra a Vista - X' e 'A vista sem
            # juros - Visa - X ... BR' no mesmo minuto e valor). Se ja existe
            # outra linha da mesma conta com data e valor identicos, essa
            # entrada pode ser a mesma compra que o usuario ja classificou
            # sob outro id - a regra nao decide sozinha, fica para revisao
            # manual (o alerta de duplicidade tambem deveria pegar o caso).
            "    AND NOT EXISTS ("
            "      SELECT 1 FROM cartao.transacao t2 "
            "      WHERE t2.transacao_id <> t.transacao_id "
            "        AND t2.account_id = t.account_id "
            "        AND t2.data_transacao = t.data_transacao "
            "        AND COALESCE(t2.valor_brl, t2.valor_original) = COALESCE(t.valor_brl, t.valor_original)"
            "    ) "
            "  ORDER BY t.transacao_id, r.ordem, r.id"
            ") "
            "UPDATE cartao.transacao t SET categoria = m.categoria, regra_aplicada_id = m.regra_id "
            "FROM match m WHERE t.transacao_id = m.transacao_id::uuid;"
        )
        resultado["lancamentos"] = max(cur.rowcount, 0)
        cur.execute(
            "INSERT INTO cartao.transacao_dimensao (transacao_id, dimensao_id, valor_id) "
            "SELECT t.transacao_id::text, rdv.dimensao_id, rdv.valor_id "
            "FROM cartao.transacao t "
            "JOIN cartao.regra_dimensao_valor rdv ON rdv.regra_id = t.regra_aplicada_id "
            "WHERE t.regra_aplicada_id IS NOT NULL "
            "ON CONFLICT (transacao_id, dimensao_id) DO NOTHING;"
        )
        resultado["dimensoes"] = max(cur.rowcount, 0)

        # Regra automatica Projeto -> Portfolio: quando uma regra_classificacao
        # define o Projeto de um lancamento (linha acima) mas nao define o
        # Portfolio, e aquele Projeto tem um Portfolio padrao cadastrado em
        # /dimensoes, usa esse padrao. ON CONFLICT DO NOTHING garante que isto
        # nunca sobrescreve um Portfolio que a propria regra ja tenha definido.
        cur.execute(
            "INSERT INTO cartao.transacao_dimensao (transacao_id, dimensao_id, valor_id) "
            "SELECT td.transacao_id, dport.id, dvp.portfolio_valor_id "
            "FROM cartao.transacao_dimensao td "
            "JOIN cartao.transacao t ON t.transacao_id::text = td.transacao_id "
            "JOIN cartao.dimensao dproj ON lower(dproj.nome)=lower('Projeto') AND dproj.id = td.dimensao_id "
            "JOIN cartao.dimensao_valor dvp ON dvp.id = td.valor_id AND dvp.portfolio_valor_id IS NOT NULL "
            "JOIN cartao.dimensao dport ON lower(dport.nome)=lower('Portfólio') "
            "WHERE t.regra_aplicada_id IS NOT NULL "
            "ON CONFLICT (transacao_id, dimensao_id) DO NOTHING;"
        )
    except Exception as e:
        cur.execute("ROLLBACK TO SAVEPOINT aplicar_regras")
        resultado["lancamentos"] = 0
        resultado["dimensoes"] = 0
        resultado["duplicatas_ignoradas"] = []
        resultado["erro"] = str(e)[:500]
        print("Aviso: falha ao aplicar regras:", e)
    finally:
        cur.execute("RELEASE SAVEPOINT aplicar_regras")
    return resultado


def registrar_e_calcular_crescimento(cur):
    """Grava o snapshot de hoje (total de lancamentos e conferidos) e devolve o
    crescimento desde ontem, 7 dias e 30 dias, para o card "Lancamentos" da
    tela principal. Tudo dentro de um savepoint proprio, com lock_timeout
    curto: se por qualquer motivo nao conseguir escrever ou ler a tempo
    (ex: disputa de lock com outra requisicao concorrente), desiste e devolve
    None nos deltas - a tela principal nunca pode cair por causa disto.

    cur e sempre RealDictCursor aqui (import de core so cursor com esse
    factory por convencao); fetchone()[0] quebraria porque RealDictRow so
    aceita chave por nome, nao indice numerico.
    """
    resultado = {"total": 0, "deltas": {"ontem": None, "semana": None, "mes": None}}
    cur.execute("SAVEPOINT crescimento_lancamentos")
    try:
        # nao vale travar a tela inteira esperando lock de outra aba/thread
        # gravando o mesmo snapshot do dia - 2s e tempo de sobra pra um UPSERT
        # de uma linha, e se nao rolar essa requisicao so fica sem o dado novo
        cur.execute("SET LOCAL lock_timeout = '2s';")
        cur.execute(
            "INSERT INTO cartao.metrica_diaria (data, total_transacoes, total_conferidas) "
            "SELECT current_date, COUNT(*), COUNT(*) FILTER (WHERE conferida) FROM cartao.transacao "
            "ON CONFLICT (data) DO UPDATE SET "
            "total_transacoes = EXCLUDED.total_transacoes, "
            "total_conferidas = EXCLUDED.total_conferidas, "
            "atualizado_em = now();"
        )

        cur.execute("SELECT COUNT(*) AS n FROM cartao.transacao;")
        total_hoje = cur.fetchone()["n"]

        def total_ate(dias_atras):
            cur.execute(
                "SELECT total_transacoes FROM cartao.metrica_diaria "
                "WHERE data <= current_date - %s::int ORDER BY data DESC LIMIT 1;",
                (dias_atras,),
            )
            r = cur.fetchone()
            return r["total_transacoes"] if r else None

        deltas = {}
        for rotulo, dias in (("ontem", 1), ("semana", 7), ("mes", 30)):
            anterior = total_ate(dias)
            deltas[rotulo] = (total_hoje - anterior) if anterior is not None else None
        resultado = {"total": total_hoje, "deltas": deltas}
    except Exception as e:
        cur.execute("ROLLBACK TO SAVEPOINT crescimento_lancamentos")
        print("Aviso: falha ao calcular crescimento de lancamentos:", e)
    finally:
        cur.execute("RELEASE SAVEPOINT crescimento_lancamentos")
    return resultado


DUPLICADA_OBS_PADRAO = "Duplicada - mesma compra ja lancada em outra linha (registro repetido pelo Pluggy)"


SEED_GRUPOS = [
    ("Moradia & Utilidades", None, None, [
        ("Casa", None, None, ["Houseware", "Agua", "Agua / Gas", "Telecommunications"]),
    ]),
    ("Alimentação", None, None, [
        ("Mercado", None, None, ["Groceries"]),
        ("Restaurantes", None, None, ["Eating out"]),
    ]),
    ("Transporte", None, None, [
        ("Veículo & Deslocamento", None, None, [
            "Gas stations", "Vehicle maintenance", "Parking",
            "Tolls and in vehicle payment", "Taxi and ride-hailing",
        ]),
    ]),
    ("Saúde & Bem-estar", None, None, [
        ("Saúde", None, None, ["Healthcare", "Hospital clinics and labs", "Dentist", "Pharmacy", "Insurance"]),
        ("Atividades Físicas", None, None, ["Natacao", "Academia"]),
    ]),
    ("Lazer & Viagem", None, None, [
        ("Lazer", None, None, ["Leisure", "Cinema, theater and concerts"]),
        ("Viagem", None, 50000, ["Airport and airlines", "Accomodation", "Tickets", "Viagem"]),
    ]),
    ("Educação & Filhos", None, None, [
        ("Educação", None, None, ["School"]),
        ("Infantil", None, None, ["Kids and toys"]),
    ]),
    ("Compras & Pessoal", None, None, [
        ("Vestuário", None, None, ["Clothing"]),
        ("Compras Gerais", None, None, ["Shopping", "Online shopping", "Electronics", "Bookstore", "Office supplies"]),
    ]),
    ("Serviços & Diversos", None, None, [
        ("Serviços", None, None, ["Services", "Digital services"]),
        ("Doações", None, None, ["Donations"]),
        ("Taxas Financeiras", None, None, ["Tax on financial operations"]),
    ]),
    ("Negócios", None, None, [
        ("BRDrive", None, None, ["BRDrive"]),
    ]),
    ("Despesas Financeiras", None, None, [
        ("Juros & Tarifas", None, None, ["Interests charged", "Credit card fees", "Tax on financial operations"]),
    ]),
]


migrate()


recarregar_categorias_db()






def topbar_html(titulo, ativo=None):
    def cls(nome):
        return "ativo" if ativo == nome else ""
    return f"""
      <div class="topbar">
        <a href="/" class="marca-box" style="text-decoration:none" title="Ir para o início">
          <img class="marca-icon" src="/static/logo-topbar.png" alt="Pé de Meia">
          <div>
            <span class="marca">{APP_NOME}</span><br>
            <span class="marca-pagina">{titulo} · {session.get('user')}</span>
          </div>
        </a>
        <div class="nav-menu">
          {f'<a href="/" class="{cls("inicio")}">Lançamentos</a>' if pode("lancamentos_ver") else ""}
          {f'''<div class="dropdown">
            <button type="button" class="dropbtn" onclick="menuToggle(event, this)">Relatórios ▾</button>
            <div class="dropdown-content">
              {f'<a href="/relatorios" class="{cls("relatorios")}">Relatórios</a>' if pode("relatorios") else ""}
              {f'<a href="/dre" class="{cls("dre")}">DRE / Centro de Custos</a>' if pode("relatorios") else ""}
              {f'<a href="/investimentos" class="{cls("investimentos")}">Investimentos</a>' if pode("relatorios") else ""}
              {f'<a href="/relatorios/conciliar-fatura" class="{cls("conciliar-fatura")}">Conciliar fatura (PDF)</a>' if pode("relatorios") else ""}
              {f'<a href="/logs" class="{cls("logs")}">Logs</a>' if pode("usuarios") else ""}
            </div>
          </div>''' if (pode("relatorios") or pode("usuarios")) else ""}
          {f'''<div class="dropdown">
            <button type="button" class="dropbtn" onclick="menuToggle(event, this)">Configurações ▾</button>
            <div class="dropdown-content">
              {f'<a href="/pendencias" class="{cls("pendencias")}">Pendências de classificação</a>' if pode("cadastros") else ""}
              {f'<a href="/categorias" class="{cls("categorias")}">Gerenciar categorias</a>' if pode("cadastros") else ""}
              {f'<a href="/grupos" class="{cls("grupos")}">Centro de Custos</a>' if pode("cadastros") else ""}
              {f'<a href="/dimensoes" class="{cls("dimensoes")}">Gerenciar dimensões</a>' if pode("cadastros") else ""}
              {f'<a href="/regras" class="{cls("regras")}">Regras automáticas</a>' if pode("cadastros") else ""}
              {f'<a href="/contas" class="{cls("contas")}">Configurações de Contas / Cartão</a>' if pode("cadastros") else ""}
              {f'<a href="/configuracoes/faturas-pdf" class="{cls("faturas-pdf")}">Faturas em PDF</a>' if pode("cadastros") else ""}
              {f'<a href="/usuarios" class="{cls("usuarios")}">Usuários e permissões</a>' if pode("usuarios") else ""}
            </div>
          </div>''' if (pode("cadastros") or pode("usuarios")) else ""}
          {'''<div class="sync-widget">
            <span class="sync-dot" id="syncDot"></span>
            <span id="syncTexto">Verificando...</span>
            <button class="sync-btn" id="syncBtn" onclick="dispararSync()">Atualizar agora</button>
          </div>''' if pode("sincronizar") else ""}
          <a href="/logout">Sair</a>
        </div>
      </div>
      <script src="/static/topbar.js"></script>
    """


def _fmt_moeda(v):
    return f"R$ {v:,.2f}"


def _barra_html(realizado, teto):
    # PostgreSQL devolve colunas NUMERIC como Decimal, enquanto os totais da
    # tela sao convertidos para float. Normalizar os dois evita TypeError ao
    # redesenhar /dimensoes depois de qualquer alteracao.
    teto_num = float(teto or 0)
    realizado_num = float(realizado or 0)
    if teto_num <= 0:
        return ""
    pct = min(realizado_num / teto_num * 100, 999)
    cor = "#2e8b3d" if pct < 70 else ("#d68a00" if pct < 100 else "#c0392b")
    largura = min(pct, 100)
    return (
        f'<div style="background:#eee;border-radius:4px;height:8px;margin-top:4px;overflow:hidden">'
        f'<div style="background:{cor};width:{largura:.0f}%;height:100%"></div></div>'
        f'<div style="font-size:11px;color:{cor};margin-top:2px">{pct:.0f}% do teto</div>'
    )


def _montar_filtro_relatorio(dimensoes):
    """Le os filtros da querystring (request.args) e monta where/params/group_expr reutilizaveis
    tanto pela pagina quanto pelos endpoints de dados (AJAX)."""
    categorias_sel = request.args.getlist("categoria")
    cartoes_sel = request.args.getlist("cartao")
    origens_sel = request.args.getlist("origem")
    # a data vai como parametro para o Postgres, que rejeita texto invalido com
    # erro - e o endpoint AJAX virava 500, deixando a tela em "Carregando..."
    # para sempre. Data que nao for AAAA-MM-DD e tratada como filtro nao
    # preenchido, que e o comportamento util aqui.
    def _data_valida(valor):
        valor = (valor or "").strip()
        if not valor:
            return ""
        try:
            datetime.strptime(valor, "%Y-%m-%d")
        except ValueError:
            return ""
        return valor

    data_ini = _data_valida(request.args.get("data_ini"))
    data_fim = _data_valida(request.args.get("data_fim"))
    agrupar = request.args.get("agrupar") or "categoria"
    dim_sel = {}
    for d in dimensoes:
        vals = request.args.getlist(f"dim_{d['id']}")
        if vals:
            dim_sel[d["id"]] = vals

    # visao do relatorio: o que estamos medindo. Por padrao, despesas (consumo real).
    # Investimentos, aquisicao de bens e transferencias NAO sao despesa - ver NATUREZAS.
    visao = request.args.get("visao") or "despesa"
    if visao not in ("despesa", "receita", "investimento", "tudo"):
        visao = "despesa"

    where = ["COALESCE(t.duplicada, false) = false"]
    params = []
    if visao == "despesa":
        where.append(NATUREZA_SQL + " = 'despesa'")
    elif visao == "receita":
        where.append(NATUREZA_SQL + " = 'receita'")
    elif visao == "investimento":
        where.append(NATUREZA_SQL + " IN ('investimento', 'bem')")
    else:  # tudo: mostra o fluxo de caixa completo, menos o que so troca de bolso
        where.append(NATUREZA_SQL + " <> 'transferencia'")

    if categorias_sel:
        where.append("t.categoria IN %s")
        params.append(tuple(categorias_sel))
    if cartoes_sel:
        where.append("t.numero_cartao_final IN %s")
        params.append(tuple(cartoes_sel))
    if origens_sel:
        where.append("t.account_id IN %s")
        params.append(tuple(origens_sel))
    if data_ini:
        # O banco guarda instantes em UTC, mas a competencia financeira e o
        # dia civil de Sao Paulo. Converter o limite em Python deixa a coluna
        # intacta na comparacao e permite usar idx_transacao_data.
        where.append("t.data_transacao >= %s")
        params.append(datetime.strptime(data_ini, "%Y-%m-%d").replace(tzinfo=FUSO_LOCAL))
    if data_fim:
        # Limite superior exclusivo inclui tambem fracoes do ultimo segundo.
        where.append("t.data_transacao < %s")
        fim_local = datetime.strptime(data_fim, "%Y-%m-%d").replace(tzinfo=FUSO_LOCAL)
        params.append(fim_local + timedelta(days=1))
    for dim_id, vals in dim_sel.items():
        where.append(
            f"EXISTS (SELECT 1 FROM {FINANCEIRO_DIM_TABELA} td WHERE td.linha_id = t.linha_id "
            "AND td.dimensao_id = %s AND td.valor_id IN %s)"
        )
        params.append(dim_id)
        params.append(tuple(int(v) for v in vals))
    where_sql = " AND ".join(where)

    join_extra = ""
    if agrupar == "categoria":
        group_expr = "t.categoria"
    elif agrupar == "cartao":
        group_expr = "t.numero_cartao_final"
    elif agrupar == "origem":
        group_expr = "t.account_id::text"
    elif agrupar == "mes":
        group_expr = f"to_char({DATA_LOCAL_SQL}, 'YYYY-MM')"
    elif agrupar == "ano":
        # comparacao ano a ano: "quanto de troca de oleo a Tracker custou em cada ano"
        group_expr = f"to_char({DATA_LOCAL_SQL}, 'YYYY')"
    elif agrupar.startswith("dim_") and agrupar.split("_", 1)[1].isdigit():
        # o int() ja impede injecao, mas sozinho ele estoura ValueError (500) em
        # "agrupar=dim_abc", que qualquer um alcanca editando a URL. Com o isdigit
        # na condicao, entrada invalida cai no else e vira o agrupamento padrao.
        dim_id_grp = int(agrupar.split("_", 1)[1])
        join_extra = (
            f"LEFT JOIN {FINANCEIRO_DIM_TABELA} tdg ON tdg.linha_id = t.linha_id "
            f"AND tdg.dimensao_id = {dim_id_grp} LEFT JOIN cartao.dimensao_valor dvg ON dvg.id = tdg.valor_id"
        )
        group_expr = "COALESCE(dvg.nome, '(nao definido)')"
    else:
        agrupar = "categoria"
        group_expr = "t.categoria"

    # valor somado conforme a visao: na visao de receita invertemos o sinal para
    # que entrada apareca positiva (VAL_DESPESA e positivo quando o dinheiro sai)
    soma_expr = f"-{VAL_DESPESA}" if visao == "receita" else VAL_DESPESA

    return {
        "categorias_sel": categorias_sel,
        "cartoes_sel": cartoes_sel,
        "origens_sel": origens_sel,
        "data_ini": data_ini,
        "data_fim": data_fim,
        "agrupar": agrupar,
        "visao": visao,
        "dim_sel": dim_sel,
        "where_sql": where_sql,
        "params": params,
        "join_extra": join_extra,
        "join_natureza": JOIN_NATUREZA,
        "tabela": FINANCEIRO_TABELA,
        "group_expr": group_expr,
        "soma_expr": soma_expr,
    }


def levantar_pendencias(cur):
    """Levanta o que esta mal classificado e pode distorcer o DRE.

    Quatro coisas, em ordem de gravidade contabil:

    1. lancamento SEM categoria - entra como despesa padrao, mas nao aparece em
       categoria nem centro de custo. Um credito/estorno sem categoria pode
       reduzir a despesa silenciosamente no lugar errado.
    2. categoria SEM natureza definida - o app assume 'despesa' por padrao, entao
       uma categoria nova que o Pluggy inventou (ex: um investimento) entra como
       despesa silenciosamente e infla o resultado. E o caso mais grave porque
       ninguem decidiu nada: aconteceu sozinho.
    3. categoria de DESPESA sem centro de custo - nao afeta o resultado (a despesa
       e contada de qualquer forma), mas some dos totais por grupo do DRE. So vale
       para despesa: vincular receita ou transferencia a centro de custo nao faz
       sentido contabil (centro de custo e analise de gasto).
    4. lancamentos com natureza manual - excecao marcada no proprio lancamento, que
       sobrepoe a natureza da categoria. Funciona, mas fica invisivel: o certo e
       mover o lancamento para uma categoria que ja tenha a natureza correta.
    """
    cur.execute(
        "SELECT t.transacao_id, t.data_transacao, t.descricao, "
        "COALESCE(t.valor_brl, t.valor_original) AS valor "
        "FROM cartao.transacao t WHERE t.categoria IS NULL "
        "AND COALESCE(t.duplicada, false) = false ORDER BY t.data_transacao DESC;"
    )
    sem_categoria_db = cur.fetchall()

    cur.execute(
        f"SELECT DISTINCT t.categoria FROM {FINANCEIRO_TABELA} t "
        "LEFT JOIN cartao.categoria_natureza n ON n.categoria = t.categoria "
        "WHERE t.categoria IS NOT NULL AND n.categoria IS NULL;"
    )
    sem_natureza = sorted(
        (r["categoria"] for r in cur.fetchall() if r["categoria"] not in CATEGORIAS_OCULTAS),
        key=lambda c: chave_alfa(cat_pt(c)),
    )

    cur.execute(
        f"SELECT DISTINCT t.categoria FROM {FINANCEIRO_TABELA} t "
        "JOIN cartao.categoria_natureza n ON n.categoria = t.categoria "
        "LEFT JOIN cartao.categoria_subgrupo cs ON cs.categoria = t.categoria "
        "WHERE n.natureza = 'despesa' AND cs.subgrupo_id IS NULL;"
    )
    despesa_sem_centro = sorted(
        (r["categoria"] for r in cur.fetchall() if r["categoria"] not in CATEGORIAS_OCULTAS),
        key=lambda c: chave_alfa(cat_pt(c)),
    )

    # a natureza por lancamento saiu do modal de detalhes: quem ainda tiver uma
    # marcada precisa poder revisar por aqui, senao vira excecao invisivel
    cur.execute(
        "SELECT t.transacao_id, t.data_transacao, t.descricao, t.categoria, t.natureza, "
        "COALESCE(t.valor_brl, t.valor_original) AS valor "
        "FROM cartao.transacao t WHERE t.natureza IS NOT NULL "
        "ORDER BY t.data_transacao DESC;"
    )
    manuais = cur.fetchall()

    return {
        "sem_categoria": len(sem_categoria_db),
        "lancamentos_sem_categoria": [{
            "id": str(m["transacao_id"]),
            "data": data_hora_local(m["data_transacao"]).strftime("%d/%m/%Y") if m["data_transacao"] else "-",
            "descricao": m["descricao"] or "-",
            "valor": float(m["valor"] or 0),
        } for m in sem_categoria_db],
        "sem_natureza": sem_natureza,
        "despesa_sem_centro": despesa_sem_centro,
        "natureza_manual": len(manuais),
        "manuais": [{
            "id": str(m["transacao_id"]),
            "data": data_hora_local(m["data_transacao"]).strftime("%d/%m/%Y") if m["data_transacao"] else "-",
            "descricao": m["descricao"] or "-",
            "categoria": m["categoria"],
            "categoria_nome": cat_pt_puro(m["categoria"]),
            "natureza": m["natureza"],
            "natureza_rotulo": NATUREZAS.get(m["natureza"], m["natureza"]),
            "valor": float(m["valor"] or 0),
        } for m in manuais],
        "total": len(sem_categoria_db) + len(sem_natureza) + len(despesa_sem_centro),
    }


def aviso_pendencias_html(pend):
    """Faixa de alerta mostrada no topo das telas de uso diario. So aparece quando
    ha algo que realmente pode distorcer numero - nunca polui a tela a toa."""
    if not pend["total"]:
        return ""
    partes = []
    if pend.get("sem_categoria"):
        n = pend["sem_categoria"]
        rotulo = "lançamentos" if n > 1 else "lançamento"
        partes.append(f'<strong>{n}</strong> {rotulo} sem categoria')
    if pend["sem_natureza"]:
        n = len(pend["sem_natureza"])
        partes.append(f'<strong>{n}</strong> categoria{"s" if n > 1 else ""} sem natureza definida'
                      f' (entra{"m" if n > 1 else ""} como despesa por padrão)')
    if pend["despesa_sem_centro"]:
        n = len(pend["despesa_sem_centro"])
        partes.append(f'<strong>{n}</strong> categoria{"s" if n > 1 else ""} de despesa sem centro de custo')
    return (
        '<div style="background:var(--bad-soft);border:1px solid var(--bad);border-radius:10px;'
        'padding:10px 14px;margin-bottom:14px;font-size:13px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
        '<span>⚠</span><span>' + " · ".join(partes) + '</span>'
        '<a href="/pendencias" style="margin-left:auto;color:var(--bad);font-weight:600">Revisar agora →</a>'
        '</div>'
    )
