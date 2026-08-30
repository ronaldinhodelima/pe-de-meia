# Pé de Meia — contexto do projeto

**Última atualização:** 29/08/2026. Estado funcional de referência: branch `main`, documentação
consolidada após `7c1ce99` e schema esperado na migração **26**. O histórico registra o conjunto
anterior como publicado e validado em produção; ainda assim, confirmar `git log`, o deploy ativo
no Coolify e `cartao.schema_version` antes de qualquer nova intervenção.

Sistema financeiro pessoal/familiar da família Ronaldo. Sincroniza lançamentos de cartão de
crédito e conta corrente via Open Finance (Pluggy) do Unicred e Nubank (duas contas Nubank:
Ronaldo e Andrea). Substitui o antigo nome "Conferência de Cartão".

Este arquivo existe para que qualquer sessão do Claude (Code, Cowork, etc.) retome o projeto
sem precisar redescobrir decisões já tomadas. Leia isto inteiro antes de mexer em qualquer coisa.

## Regra de ouro (instrução permanente do usuário)

> "Sempre que formos falar em financeiro, preciso que traga as considerações do DRE, do conceito
> de DRE financeiro, não podemos ter dados mascarados ou informações que inflem os lançamentos.
> Os números precisam ser reais."

Na prática:
- Resultado = Receitas − Despesas. Só isso é "resultado".
- Investimento, compra de bem (terreno, veículo, imóvel), pagamento de fatura de cartão e
  transferência entre contas próprias **não são despesa** — só trocam a forma do patrimônio.
- Juros e tarifas **são despesa de verdade** (o dinheiro sai e não volta).
- Terreno não deprecia. Só entraria depreciação de bens que perdem valor com o tempo (não
  implementamos depreciação ainda — hoje bens só ficam fora do resultado, não geram despesa).
- Toda vez que mexer em relatório/DRE/natureza de categoria, explicar o raciocínio contábil,
  nunca só aplicar sem justificar.

## Preferências de estilo do usuário (Ronaldo)

- Respostas diretas, sem enrolação, com tópicos quando fizer sentido.
- **Nunca inventar números ou informação** — se não souber, perguntar.
- Interface do sistema em português.
- **Ao classificar lançamento, sempre avaliar a melhor forma de distribuir e
  organizar** — categoria, natureza, dimensão e projeto — em vez de só encaixar
  no que já existe. Instrução dada em 22/08/2026, durante a conciliação.
- **Nunca marcar o "conferido" (check OK) de um lançamento.** Esse campo é a
  assinatura de quem conferiu — só o Ronaldo ou outro usuário marca. O Claude pode
  ajustar categoria, dimensão, natureza e observação, mas o check é a confirmação
  humana de que o lançamento foi conciliado. Vale também para lançamento manual
  criado pelo Claude: nasce desmarcado.

## Stack e arquitetura

- **App principal**, separado em quatro camadas:
  - `app.py` — cria o Flask app, configura sessão/auditoria, registra os filtros Jinja e os
    blueprints.
  - `core.py` — constantes, acesso ao banco, permissões, helpers de HTML. **Não importa
    nada de `views/` nem de `app.py`.**
  - `views/` — blueprints `auth`, `sistema`, `lancamentos`, `relatorios`, `cadastros`,
    `usuarios` e `logs`. Cada módulo importa do `core` só os nomes que usa, listados
    explicitamente. Não fixar quantidade de rotas aqui: `tests/test_estrutura.py` mantém a
    lista autoritativa e acusa qualquer rota perdida ou inesperada.
  - `templates/` (Jinja2) e `static/` (CSS, JS, logos). **Nenhuma tela monta HTML por
    f-string no Python** — se aparecer uma, é regressão.
  Sem framework front-end — JS puro. A view faz SQL e regra de negócio, o template só exibe.
  **O escaping do Jinja é automático**: nada de `esc()` dentro de valor que vai para template
  (gera `&quot;` visível na tela). Quando o valor já é HTML confiável montado no Python
  (selo do banco, topbar, aviso de pendências), marque `|safe` explicitamente — assim fica
  claro na revisão que foi decisão, não esquecimento.
  **Nunca interpole dado dentro de `onclick`/`onchange`/`onsubmit`.** `|tojson` é o escape
  certo dentro de `<script>`, e errado dentro de atributo: o filtro do Flask não escapa aspas
  duplas, então `onclick="f({{ id|tojson }})"` vira `onclick="f("abc")"` — o atributo fecha
  cedo, o handler não compila e **falha em silêncio**. Foi assim que a tela de Lançamentos
  parou de abrir detalhes e de salvar, e que o "Excluir" de `/usuarios` passou a apagar sem
  pedir confirmação. Para levar dado ao JS: `data-attribute` + delegação de evento, ou um
  bloco `<script type="application/json">`. `tests/test_estrutura.py` trava os dois casos.
- **Banco**: PostgreSQL, schema `cartao.*` (rodando dentro do Coolify, host interno de rede
  Docker — não acessível de fora).
- **Worker de sincronização**: `bussola/app.py` (serviço separado no Coolify) — busca dados do
  Pluggy e grava no mesmo Postgres.
- **Deploy**: Coolify (PaaS auto-hospedado da BRDrive), build via Dockerfile a partir de um
  repositório git no GitHub.

## Repositório e deploy

- **GitHub**: `ronaldinhodelima/pe-de-meia` (branch `main`).
  - `app.py`, `core.py`, `views/`, `templates/`, `static/` → app principal (Flask).
  - `tests/` → suíte pytest.
  - `bussola/app.py` → worker de sincronização Pluggy.
  - `Dockerfile` → build do app principal. **Ao criar pasta nova, adicione o `COPY`** —
    o container sobe sem ela e quebra só na hora de servir a tela.
- **Coolify**: `https://coolify.brdrive.net`, projeto **Ronaldinho**.
  - App principal: nome `conferencia-cartao-app`, uuid `nvbnzjhig1og7s0gn5nrbxjo`.
    Domínio: **https://pedemeia.brdrive.net** (+ domínio padrão do Coolify como backup:
    `https://nvbnzjhig1og7s0gn5nrbxjo.coolify.brdrive.net`).
  - Worker de sync: nome `bussola-financeira-app-v2`, uuid `hdgffcvh3ljqe61dczztaycz`.
    Domínio interno: `https://hdgffcvh3ljqe61dczztaycz.coolify.brdrive.net`.
    **Atenção**: esse domínio já mudou uma vez sem avisar (Coolify reatribuiu o subdomínio
    em algum redeploy) e quebrou o botão "Atualizar agora" porque a URL estava hardcoded em
    `BUSSOLA_SYNC_URL` no app principal. Se o sync voltar a dar erro 404/502, o primeiro
    passo é conferir se o domínio do worker mudou de novo.
- **Deploy automático:** push na `main` dispara o webhook GitHub → Coolify. Acompanhar o build,
  a troca do container e os logs; não considerar o push como conclusão. A API do Coolify é
  apenas alternativa caso o webhook não dispare. Fluxo de contingência:
  ```bash
  git clone https://github.com/ronaldinhodelima/pe-de-meia.git
  cp app.py <repo>/app.py   # depois de editar
  cd <repo> && git add app.py && git commit -m "..." && git push
  curl -X POST -H "Authorization: Bearer <COOLIFY_TOKEN>" \
    "https://coolify.brdrive.net/api/v1/deploy?uuid=nvbnzjhig1og7s0gn5nrbxjo"
  ```
  O token do Coolify e as credenciais de banco ficam nas variáveis de ambiente do próprio
  Coolify (aba Environment de cada app) — nunca no código nem no repositório git.

## Banco de dados — tabelas principais (schema `cartao`)

- `pluggy_item` — cada conexão bancária (1 linha por item Pluggy).
- `conta` — contas dentro de cada item (conta corrente, cartão de crédito, "manual"/dinheiro).
- `transacao` — lançamentos (chave: `transacao_id` do Pluggy, evita duplicidade).
- `sync_log` — auditoria das rodadas de sincronização.
- `metrica_diaria` — fotografia diária da quantidade total e conferida de lançamentos, usada
  pelo card de crescimento da tela principal.
- `categoria_natureza` — natureza contábil de cada categoria (despesa/receita/investimento/
  bem/transferência/fluxo) — base do DRE.
- `categoria` — overrides de nome (renomeações feitas pelo usuário em `/categorias`).
- `categoria_oculta` — categorias removidas pelo usuário (ficam escondidas nos dropdowns).
- `grupo_custo` / `subgrupo_custo` / `categoria_subgrupo` — centro de custo (grupos de gasto).
- `usuario` — login/senha (PBKDF2-HMAC-SHA256, 200k iterações) + perfil + permissões.
- `item_titular` — de quem é cada conexão bancária (Ronaldo / Andrea / Ronaldo e Andrea).
- `investimento` / `investimento_saldo` — posições de investimento e histórico diário de saldo.
- `cartao_nome` — apelido de cada cartão pelos 4 últimos dígitos. Um cartão de crédito (uma
  linha em `conta`) pode ter vários cartões físicos/virtuais/adicionais; a `conta` guarda só o
  final do principal, e os adicionais só aparecem em `transacao.numero_cartao_final` — é dali
  que a tela `/contas` descobre quais cartões existem.
- `regra_classificacao` / `regra_dimensao_valor` — regras automáticas de categorização.
- `dimensao` / `dimensao_valor` / `transacao_dimensao` — dimensões livres (ex: Responsável:
  Ronaldo/Andrea/Amanda/Compartilhado, Projeto, etc.) além da categoria.
  `dimensao_valor` também guarda `teto_mensal` / `teto_anual` (teto de gasto por valor) e o
  vínculo opcional de um Projeto com seu Portfólio padrão.
- `transacao_rateio` / `transacao_rateio_dimensao` — partes internas de um lançamento bancário
  que pertence a mais de uma classificação. O lançamento original do Pluggy é preservado;
  as partes precisam fechar exatamente o seu valor e o substituem somente nos totais e
  relatórios, por meio das views `lancamento_financeiro*`.
- `fatura_importada` / `fatura_linha` — histórico das faturas Unicred importadas, linhas
  extraídas e PDF original armazenado no PostgreSQL.
- `fatura_vinculo` — relação persistente N:N entre linha da fatura e lançamento. Guarda origem
  automática, manual ou criada da própria fatura e impede que a conciliação mude sozinha.
- `schema_version` — controle de migração (ver `migrate()`). Cada bloco `if versao_atual < N`
  roda uma vez só; **não reescrever migração já aplicada** — criaria divergência de schema.

## Modelo de natureza (6 naturezas, base do DRE)

`despesa`, `receita`, `investimento`, `bem`, `transferencia` e `fluxo` — este último é o
padrão: a direção do lançamento decide se é receita ou despesa (usado pra PIX/TED/dinheiro).
As três neutras (`investimento`, `bem`, `transferencia`) ficam fora do resultado.

## Sistema de permissões

Perfis: `admin` (tudo), `operador` (lançamentos + relatórios + conciliação + sincronizar, sem
cadastros/usuários), `leitura` (só ver lançamentos e relatórios). As 9 permissões granulares:
`lancamentos_ver`, `lancamentos_editar`, `lancamentos_conferir`, `lancamentos_manual`,
`relatorios`, `conciliacao_editar`, `cadastros`, `sincronizar`, `usuarios`. Decorator `@requer(permissao)`
protege cada rota; `pode(permissao)` controla o que aparece na interface.

`relatorios` é somente consulta. Importar/reimportar PDF, sincronizar parcelas, refazer vínculos,
vincular/desvincular linhas e registrar revisão de cobrança repetida exigem
`conciliacao_editar`. A migração 26 concede a nova permissão aos Administradores e Operadores já
existentes, preservando seu acesso; o perfil Somente leitura continua vendo as telas sem controles
de gravação.

Usuários atuais: `ronaldo` (admin), `andrea` (admin, herdado do sistema antigo), `amanda`
(operador, criada nesta sessão).

## Identidade visual

- Nome: **Pé de Meia**. Logo oficial fornecida pelo usuário (meia de tricô com dinheiro),
  em **fundo claro sólido** no topbar e na tela de login.
  Os PNGs foram embutidos como base64 direto no `app.py` (`LOGO_TOPBAR_B64`, `LOGO_HERO_B64`)
  — não dependem de arquivo externo.
- **Favicon**: meia sólida em marrom (`#9f7251`) com **fundo transparente**, diferente da logo
  do topbar. Cuidado: o favicon que vale está **hardcoded dentro de `BASE_CSS_HEAD`**
  (`<link rel="icon" ...>`); a variável `LOGO_FAVICON_B64` existe mas **não é usada por
  ninguém** — mexer nela não muda nada na tela.
- Bancos identificados por "selo" colorido em CSS puro (cor da marca + sigla de 2 letras),
  não por logo de imagem — Pluggy não fornece logo utilizável.
- Tooltips customizados (120ms, mais rápido que o `title` nativo do navegador).

## Funcionalidades já construídas (sessão 1 — Cowork)

1. Sync completo do Pluggy (corrigido bug de paginação — campo `next`, não `cursor.after`).
2. Relatórios em ordem cronológica (gráfico) com lista "Totais agrupados" mais recente primeiro.
3. Modelo de natureza contábil (5 naturezas) aplicado em Relatórios e DRE.
4. DRE com Receitas/Despesas/Resultado/Margem por mês + grupos de custo.
5. Tela `/naturezas` para reclassificar a natureza de qualquer categoria.
6. Grupo "Despesas Financeiras" (juros e tarifas) no centro de custo.
7. Conexão Nubank (duas contas — Ronaldo e Andrea) além da Unicred, sync multi-conexão com
   auto-descoberta de conexões já sincronizadas (mas **não** de conexões novas nunca vistas —
   essas precisam ser adicionadas manualmente na env var `PLUGGY_ITEM_ID` do worker).
8. Selos coloridos de banco + tooltips rápidos.
9. Renomeação do app (Conferência de Cartão → Meu Dinheiro → **Pé de Meia**), com DNS próprio
   `pedemeia.brdrive.net`.
10. Remoção do serviço Metabase (não era mais usado).
11. Sistema completo de usuários e permissões (`/usuarios`).
12. Logo oficial aplicada (favicon, topbar, tela de login).
13. Grupos/categorias sempre em ordem alfabética (ignorando acento/maiúscula — função
    `chave_alfa()`); `<details>` de `/grupos` lembram se estavam abertos ao salvar
    (via `localStorage`).
14. Item "Importar extrato/fatura" saiu primeiro do menu e depois a funcionalidade inteira foi
    removida na refatoração, pois estava quebrada e sem uso. Não assumir que a rota ainda existe.
15. `/categorias`: criar, renomear, mover lançamentos entre categorias, excluir (só permite
    excluir categoria vazia — com lançamentos, fica "protegida").
16. `/contas`: identifica o titular de cada conexão bancária (Unicred = Ronaldo e Andrea,
    Nubank 1 = Ronaldo, Nubank 2 = Andrea) — aparece em Lançamentos, Relatórios e em qualquer
    lugar que mostre a origem do dinheiro.
17. Correção do bug do botão "Atualizar agora" (URL do worker de sync desatualizada).

## Funcionalidades e correções (sessão 2 — Claude Code, 20–21/08/2026)

Segurança:
1. Cookie de sessão com `Secure`, `HttpOnly` e `SameSite=Lax`.
2. Senhas genéricas hardcoded (`changeme1/2`) foram removidas do código. Por decisão posterior
   do usuário, os acessos administrativos antigos/de emergência continuam configurados **no
   ambiente do Coolify**, nunca no repositório. Não alterar esses acessos sem pedido explícito.
3. **XSS corrigido em todo o app** (`esc()` e `json_script()`): nome de categoria, dimensão,
   grupo, cartão, titular, descrição/observação de lançamento e mensagens de aviso. O caso mais
   grave era `json.dumps()` dentro de `<script>` — não escapa `</`, então uma descrição contendo
   `</script>` executava JS para qualquer um que abrisse Lançamentos. Validado com payload real.
4. `/sync` do worker exige `X-Sync-Secret` (env `SYNC_SECRET`, mesma nos dois serviços).
5. `/` do worker parou de expor a lista de tabelas do banco e o resumo do sync sem a chave.

Processo:
6. Migração versionada (`cartao.schema_version`) — antes rodava ~30 DDL a cada boot.
7. A suíte começou com 46 testes nesta etapa e foi ampliada depois; ver contagem atual em
   "Testes automatizados".
8. `.gitignore` e identidade do git configurada.

Produto:
9. Login: mostrar senha + `autocomplete` para o navegador salvar credencial.
10. Favicon novo (meia sólida, fundo transparente). **Atenção**: o favicon real fica hardcoded
    dentro de `BASE_CSS_HEAD`, não na variável `LOGO_FAVICON_B64` (que não é usada).
11. `/categorias`: clicar em "X lanç. — protegida" lista os lançamentos que bloqueiam a remoção.
12. `/grupos` virou **Centro de Custos**: tabela única, hierarquia visual grupo → subgrupo,
    vínculo de categoria por chips removíveis.
13. Teto de gasto saiu do centro de custo e virou **teto por valor de dimensão** (ex: "Ronaldo:
    R$3.000/mês"), com barra de progresso do gasto real do mês/ano em `/dimensoes`.
14. **`/pendencias`** — painel de pendências de classificação (ver seção própria).
15. **Colunas ajustáveis em todas as 7 tabelas**: redimensionar (estilo planilha, a vizinha
    compensa), reordenar arrastando o cabeçalho, ordenar clicando no título, botão "Redefinir
    colunas". Preferências no `localStorage` por tabela. Ver seção própria.
16. Fatura por cartão: `fechamento_fatura`/`vencimento_fatura` vêm do Pluggy por conta — antes
    o fechamento era uma constante única (`FATURA_DIA_FECHAMENTO`), que só servia para um cartão.
17. `/cartoes` foi **fundido** em `/contas`, agora **"Configurações de Contas / Cartão"**:
    titular da conexão + nome de cada cartão (físico, virtual, adicional) + datas da fatura
    (somente leitura).
18. Navegação de mês (`‹ ›`) em Lançamentos e logo do topbar como link para o início.

## Refatoração e limpeza (sessão 3 — 21/08/2026)

`app.py` saiu de **5.612 para ~3.260 linhas**. Todo o HTML foi para `templates/` (Jinja) e o
JS/CSS/logos para `static/`. As 11 telas foram migradas uma a uma, cada uma validada em
produção antes da seguinte.

**Cinco XSS latentes** apareceram na migração, todos em conteúdo de terceiro que era
interpolado cru — o escaping automático do Jinja resolveu quatro; o do preview de importação
era JS e foi corrigido com `escHtml`:
descrição de arquivo importado, nome de aplicação (Pluggy), padrão de regra, nome de dimensão
e valor de dimensão, e o apelido do cartão em `origem_curta()`.

**Removido por não estar mais em uso:** a tela `/importar` inteira (3 rotas, 7 auxiliares, a
permissão `importar`) — que aliás estava dando 500 desde `fcedcf1`, sem ninguém notar, porque
tinha saído do menu. E os redirects legados `/cartoes` e `/naturezas`.

### Regra de dependência entre os módulos

`app.py` → `views/` → `core.py`, sempre nessa direção. Nada em `core.py` pode importar de
`views/`, senão volta o import circular.

**Armadilha do estado compartilhado:** `CATEGORIA_PT_DB` e `CATEGORIAS_OCULTAS` são
recarregados em runtime (quando o usuário renomeia ou oculta categoria). Como os blueprints
fazem `from core import CATEGORIA_PT_DB`, `recarregar_categorias_db()` **altera os dicionários
no lugar** (`clear()` + `update()`). Se algum dia alguém trocar isso por reatribuição, os
nomes de categoria congelam na versão do boot — sem erro nenhum, só nome errado na tela.
`tests/test_core_estado.py` trava esse comportamento checando a identidade do objeto.

### Como cortar código sem quebrar nada

Duas vezes nesta sessão um corte por busca de texto apagou código vizinho por engano — uma
delas levou a rota `/dre` inteira, outra levou `_montar_filtro_relatorio` e derrubou
`/relatorios` em produção. O que funciona:

1. Delimitar a função pelo **AST** (`node.end_lineno`), nunca por "até o próximo `@app.route`"
   — auxiliares sem decorator moram entre as rotas e são engolidos.
2. Depois do corte, **comparar as definições de topo antes/depois**. Contar rotas não basta,
   justamente porque o que se perde costuma ser função sem decorator.
3. **302 não é prova de que a tela funciona** — é o redirect de login. Validação real só
   logado, olhando o conteúdo. **Depois do deploy, abrir todas as telas** (um `fetch` em cada
   e conferir status 200 + ausência de traceback no corpo): variável usada mas atribuída só
   dentro de um `if` passa por `py_compile`, passa pelos testes (que não executam view, porque
   exigiriam banco) e só aparece quando alguém abre aquela tela. Já derrubou `/grupos`.
4. **`replace` em código só com `assert` de que casou.** Um `replace` silencioso que não casa
   deixa o código velho no lugar e a edição parece ter funcionado.
4. Em tela com número (DRE, relatórios), **anotar os valores em produção antes do deploy** e
   comparar depois. Migração de tela não pode mexer em número.

## Estado registrado em produção — 29/08/2026

Produção está em `https://pedemeia.brdrive.net`, branch `main`, deploy automático pelo webhook.
O código espera schema na migração **26**. Entre `cfa183e` e `7c1ce99` foram publicados 75
commits, alterando 23 arquivos: proteção de dimensões, visão anual dos lançamentos, rastreio da
primeira sincronização, vínculo Projeto → Portfólio, importação e conciliação das faturas Unicred,
regime de caixa dos parcelamentos e os estados `somente_conciliacao` / `substituido_por`.
Não há implementação versionada conhecida aguardando commit; as pendências atuais são revisão
de permissões, robustez do import de PDF e conciliação/classificação das outras origens.

### Lançamentos e experiência de uso

- Filtro **Status** tem: Todas, Pendentes, Conferidas, Possíveis duplicidades e Duplicados.
  O aviso grande de suspeitas foi removido; a filtragem ficou centralizada no Status.
- Possível duplicidade é apenas alerta. O Pluggy pode realmente trazer duas cobranças iguais;
  nunca criar regra que descarte isso automaticamente. Duplicado confirmado pelo usuário fica
  fora dos totais, mas pode ser consultado pelo status **Duplicados**.
- Linha com OK fica cinza-claro. Tirar OK e marcar duplicidade exigem confirmação; Cancelar
  fecha também os detalhes. A confirmação não repete data/descrição/valor, pois já estão acima.
- Categoria, dimensões e observação podem ser alteradas tanto na tabela quanto nos detalhes.
  Alterar qualquer campo não pode mudar o OK já salvo.
- Detalhes compactam Data/Valor, Valor original/Parcela e Status/Tipo em pares. Campos à direita
  ficam alinhados à direita; "Conferida" mostra `por <usuário>` somente quando estiver em Sim.
- Botão `+` em cada lançamento abre a criação de regra automática já preenchida com a descrição.
  O botão da linha é apenas `+`, sem a palavra "Regra" dentro dele.
- Menu Colunas permanece aberto enquanto marca/desmarca itens e fecha ao clicar fora. Projeto e
  Portfólio permitem cadastro rápido sem sair da tela. Trocar mês por digitação, calendário ou
  setas recarrega a lista. Filtros/meses criam histórico real para o botão Voltar do navegador.
- O período de Lançamentos aceita consultar o ano inteiro. O card antes chamado **Conferidas**
  agora se chama **Lançamentos** e usa `metrica_diaria` para mostrar crescimento do total.
- Os detalhes mostram quando o lançamento apareceu pela primeira vez e a última sincronização.
  Se o Pluggy mudar valor ou data de uma linha já conferida, o worker preserva o OK e grava um
  alerta de auditoria; ele não desfaz silenciosamente a assinatura humana.
- Dimensões e seus valores não podem ser excluídos enquanto houver lançamentos vinculados. A
  tela informa a quantidade e permite abrir a lista dos lançamentos que precisam ser
  reclassificados primeiro.
- Favicon correto é a meia com fundo transparente; o arquivo HTML local cru mostra Jinja sem
  processar e **não serve como teste do site**. Sempre validar pela URL Flask/produção.

### Rateio de lançamento

- Implementado nas migrações/tabelas `transacao_rateio*` e nas views financeiras
  `lancamento_financeiro*`. O registro bancário pai nunca é alterado ou duplicado; no DRE,
  Relatórios e totais as partes substituem o pai.
- Na tabela, o botão `+`/`−` expande linhas chamadas `<descrição original> — Parte 1`, Parte 2…
  Valor, categoria, Responsável, Projeto, Portfólio e observação são editáveis nas próprias
  linhas. O quadrado `✓` no fim de qualquer parte salva **o conjunto inteiro**, não só a linha.
- Soma usa centavos exatos e precisa fechar o valor do pai. Quando fecha, não aparece texto de
  confirmação e a linha fica normal. Quando diverge, as partes ficam vermelho-claro, os campos
  de valor ganham borda vermelha, aparece `Rateado R$ X de R$ Y` e Salvar/OK ficam bloqueados.
- Nos detalhes, Rateio fica no final, abaixo de "Marcar como duplicada", com a mesma formatação
  das linhas superiores e sem o texto explicativo antigo. É possível editar rateio com o pai
  em OK sem apagar a assinatura, desde que o novo conjunto continue completo e válido; desfazer
  o rateio inteiro exige retirar o OK.
- Primeiro caso real validado: **DEB MONGERAL, R$ 705,28** — R$ 505,46 para Ronaldo e
  R$ 199,82 para Andrea; categoria Seguros, projeto Seguro de Vida, portfólio Proteção e Futuro.
  O total mensal ficou idêntico antes/depois e DRE/Relatórios foram validados.

### Sincronização, segurança e auditoria

- Sync do Pluggy importa todos os IDs recebidos, mas não pode sobrescrever categoria manual,
  Responsável, Projeto, Portfólio, observação, OK, duplicidade ou rateio. O banco impede duas
  linhas com a mesma chave do Pluggy; IDs diferentes são mantidos para não esconder cobrança.
- Sessão dura 24 horas e renova com uso; cookies `Secure`, `HttpOnly`, `SameSite=Lax`. Senha
  mínima continua em 6 caracteres por decisão do usuário. Não mudar acessos. Credenciais
  administrativas de emergência permanecem somente nas variáveis do Coolify.
- Login limita tentativas por usuário e IP numa janela de 15 minutos e usa mensagem genérica.
  Senhas do banco ficam em PBKDF2. Requisições mutáveis exigem Origin/Referer do próprio site;
  respostas incluem HSTS, CSP, proteção contra iframe/MIME sniffing e `Cache-Control: no-store`.
- Logs fica dentro de Relatórios e registra acesso, alteração com antes/depois, falha e sync.
  Senhas/tokens são sanitizados. Rateios também geram auditoria.
- Upload de fatura aceita no máximo 10 MB, exige assinatura real `%PDF-` e recusa documentos com
  mais de 50 páginas. Resposta 413 não tenta reler o formulário na auditoria, pois isso causava
  um erro 500 durante o próprio tratamento do arquivo excessivo.
- E-mail operacional mudou para `ronaldo@brdrive.net`; teste foi recebido. Backup no mesmo
  servidor foi aceito pelo usuário. Não executar teste de restore agora; isso ficou adiado.

### Classificações e cadastros já aprovados

- Portfólio **Eventos e Negócios** foi renomeado para **Eventos**.
- Mapeamentos aprovados: Azul → Viagem; Shein → Vestuário.
- Regra `GuilhermeDaSilva`: abaixo de R$ 120 Água; acima de R$ 120 Gás; ambos Família / Casa /
  Moradia. A regra antiga genérica foi desativada sem apagar histórico.
- Transferências `Amanda Bressan de Lima` usam regra própria. **Reaplicar** libera todos os
  lançamentos pendentes ligados à regra, em qualquer mês, e reaplica no próximo acesso; não há
  filtro de mês nessa operação e lançamentos conferidos são preservados.
- Conta Corrente Conjunta, julho/2026: lançamentos de condomínio foram reconhecidos; o de
  R$ 644,88 representa três meses acumulados. Restaurante "StarWars" foi classificado Família /
  Viagem Chile 2026 / Viagens 2026, observação StarWars.
- Valores Quanta entre R$ 319,00 e R$ 343,15 foram identificados como previdência privada de
  Ronaldo já paga: categoria Previdência, projeto Previdência Privada, portfólio Proteção e
  Futuro. Projeto Saúde e projeto Seguro de Vida foram criados.
- Exemplo já ajustado: DELTA VIDEIRA R$ 265,07 — Responsável Ronaldo, categoria Combustível,
  projeto Jeep, portfólio Veículos. Não generalizar para toda descrição DELTA sem revisar.

## Conciliação de fatura em PDF (reescrita em 29/08/2026 — leia antes de mexer)

Tela `/relatorios/conciliar-fatura`. Confere a fatura oficial da Unicred (PDF) contra o que o
Pluggy sincronizou. **A fatura é a autoridade**: ela é a prova do que a operadora cobrou.

### O erro de arquitetura que foi corrigido

Até 29/08/2026 a conciliação era **sem memória**: recalculava tudo por heurística a cada
abertura da tela e não tinha onde registrar decisão humana. Consequências, todas observadas em
produção: resultado mudava sozinho entre uma visita e outra, a mesma cobrança aparecia como
"sobra" em dois meses seguidos, e parcelamento era impossível de resolver.

**Migração 23** criou `cartao.fatura_vinculo` — relação **N:N** entre `fatura_linha` e
`transacao`. O N:N não é luxo: um parcelamento que o Pluggy gravou como UMA transação (valor
cheio, data da compra) corresponde a **uma linha por mês, em faturas diferentes**. Com
"usado/não usado" dentro de uma fatura isso era irrepresentável, e um mês roubava a transação
do outro.

Regras:
- Transação já vinculada não é reivindicada pelo casamento 1:1 nem por avulsa.
- **Exceção deliberada:** o fallback de parcelamento agregado PODE reusar transação já
  vinculada — é o caso legítimo acima.
- Vínculo `origem='manual'` nunca é sobrescrito pelo automático (mesma regra de
  `categoria_manual` e do OK).
- O casamento automático só roda em **POST** (import do PDF ou botão "Vincular
  automaticamente"), nunca em GET. GET que grava faria a tela mudar sozinha e furaria a
  checagem de Origin/Referer.
- **A ordem importa ao rodar o vínculo automático em lote: da fatura mais antiga para a mais
  nova**, porque o bloqueio depende dos vínculos já existentes.

### Datas do ciclo — três armadilhas já resolvidas

1. **A Unicred não imprime a data de fechamento em lugar nenhum do PDF.** Conferido nas 14
   faturas: só existem `REF.:`, `VENCIMENTO:` e o resumo de saldo. Não adianta procurar de novo.
2. **O intervalo vencimento−fechamento NÃO é fixo** (varia de 9 a 14 dias). Não cabe fórmula.
   O usuário conferiu as datas reais no app do Unicred (tela "Melhor dia para compra" = data de
   fechamento) e elas estão em `FECHAMENTOS_CONHECIDOS`, em `fatura_unicred.py`. **Preencher ali
   conforme ele for confirmando novos meses.** Fora da tabela, cai na heurística (última compra
   impressa) com trava: nunca fecha no dia do vencimento ou depois.
3. **`periodo_inicio` é calculado na LEITURA, nunca congelado no import**
   (`_ciclo_inicio_encadeado`). Congelar tornava o resultado dependente da ORDEM de envio dos
   PDFs: quem mandasse da fatura mais nova para a mais antiga ficava com todas no palpite de 35
   dias. Aconteceu de verdade com as 6 faturas de 2025. **O palpite de 35 dias só vale quando
   não existe fatura anterior daquela conta** — nunca como regra.

`cartao.conta.fechamento_fatura` é **coluna morta**: o Pluggy nunca preenche para nenhuma conta
real (a tela `/contas` mostra "fechamento não informado pelo banco" nas 3 conexões). Já foi
tentado e revertido; não voltar a depender dela.

### O que cada número da tela significa (já confundiu o usuário)

- **Total impresso no PDF** — o SALDO TOTAL da fatura.
- **Soma das linhas lidas** — soma das linhas extraídas do PDF, sem "Pagamento Recebido".
  É **fatura contra fatura**, não contra o Pluggy: prova que a leitura do PDF está correta.
  Tem que ser igual ao total impresso (fica vermelho quando não é).
- **Já vinculado ao Pluggy** / **Falta vincular** — aí sim é contra o Pluggy.
  "Falta vincular" **não é** `Total − Soma`; é o quanto da fatura ainda não foi conciliado.

"Pagamento Recebido" é a fatura ANTERIOR sendo quitada; o próprio SALDO TOTAL da Unicred não a
inclui. Ela fica fora das duas somas — se entrar em um lado só, a tela acusa diferença de
dezenas de milhares sem erro nenhum (aconteceu: R$ 16.647,99 falsos).

### Consolidação de data: por que não existe botão em massa

Existiu por algumas horas em 29/08/2026 e **corrompeu 22 datas reais** de agosto/2026,
jogando-as para a data da compra original (algumas em novembro/2025). Causa: numa linha de
parcela a data impressa é a da **COMPRA ORIGINAL**, fixa em toda reimpressão mensal — não a data
da cobrança daquele mês. Foi revertido no mesmo dia pelo log de auditoria (que guardava
antes/depois de cada uma).

Decisão do usuário: consolidação de data, se voltar, é **por lançamento, um a um**, dentro do
painel de vínculo, junto com observação — nunca em lote por fatura. **Não reintroduzir sem
uma fonte de data mensal confiável, que a fatura não fornece.**

## Regime de caixa para parcelamento (decidido e aplicado em 29/08/2026)

**Decisão do usuário:** parcelamento vira despesa **mês a mês, conforme a fatura cobra** —
regime de caixa, não competência. A despesa acontece quando o dinheiro sai.

O problema que isso resolve: o Pluggy grava parte dos parcelamentos como UMA transação no valor
cheio, na data da compra (OTICA CALLIARI, R$3.160 em 02/11/2025, 10× R$316). Contar assim
**inflava o mês da compra e deixava os outros nove vazios**. Novembro/2025 sozinho tinha
R$ 18.498,63 de despesa que não saiu naquele mês.

Como funciona (migração 24 + `_sincronizar_parcelas_de_agregado` em `views/relatorios.py`):

1. Toda transação vinculada a **2+ linhas de fatura** é um agregado, e recebe
   `transacao.somente_conciliacao = true`. Ela continua existindo, visível e vinculável — só
   sai do resultado.
2. Cada linha de fatura ligada a esse agregado vira um lançamento próprio, **no valor e no mês
   em que a fatura cobrou**, herdando categoria e dimensões do agregado.
3. Idempotente por `fatura_linha.transacao_id_criado` — rodar de novo não duplica.

**`somente_conciliacao` NÃO é `duplicada`.** A compra agregada é legítima; ela só não é a
cobrança. Marcar como duplicada mentiria sobre a natureza do dado e atrapalharia a revisão.

**A exclusão mora na view `cartao.lancamento_financeiro`**, que é o ponto único por onde passam
DRE, relatórios, totais de Lançamentos e pendências — mexer lá vale para todos de uma vez. A
tela de Lançamentos e a conciliação leem `cartao.transacao` direto, por isso o agregado continua
aparecendo e podendo ser vinculado.

**Data da parcela gerada = `periodo_fim` da fatura que a cobrou.** Não dá para usar a data
impressa na linha: numa parcela ela é a da COMPRA ORIGINAL, fixa em toda reimpressão mensal.

Resultado medido em produção (92 agregados marcados, 331 parcelas geradas):
`nov/25: 43.904,64 → 25.406,01` · `ago/26: 55.395,59 → 60.862,16`. A despesa saiu do mês da
compra e foi para os meses em que foi cobrada.

**Sincronização do Pluggy não desfaz nada disto** (verificado em 29/08/2026, 2.336 transações
atualizadas, 0 novas: DRE, vínculos, duplicidades e conciliação idênticos antes/depois). O
`UPSERT` do worker só escreve `status`, `valor_brl`, `data_transacao` e os carimbos — não toca em
`somente_conciliacao`, `substituido_por`, `duplicada`, categoria nem dimensões. **Atenção:**
`data_transacao` PODE mudar se o Pluggy corrigir fuso/data, e isso move o lançamento para dentro
ou fora do ciclo de uma fatura. Os vínculos não se perdem (são por id), mas a lista de órfãos
pode oscilar — se acontecer, rodar o vínculo automático de novo.

**Consequência esperada, não é erro:** a despesa total caiu R$ 15.956,42. Parcela que só será
cobrada em fatura futura deixou de contar antecipadamente — correto no regime de caixa; ela
entra quando a fatura do mês for importada. **Mas atenção:** parcelamento com parcela anterior a
jul/2025 perde essa despesa, porque não existe fatura importada cobrindo o período. Importar
faturas mais antigas recupera automaticamente (a sincronização é idempotente).

## Hierarquia de fontes — CARTÃO DE CRÉDITO UNICRED (aprovada em 29/08/2026)

**Escopo:** só o cartão de crédito da Unicred. Cartão Nubank e conta corrente ainda não foram
avaliados e provavelmente precisam de regras próprias — conta corrente não tem fatura, então a
hierarquia lá nasce diferente (o extrato do Pluggy vira a única fonte de "houve cobrança").

> **A fatura manda sobre o que foi cobrado. O Pluggy manda sobre o que aconteceu.
> O usuário manda sobre o que significa.**

| Campo | Manda | Por quê |
|---|---|---|
| A cobrança existiu? | **Fatura** | É a prova do que a operadora cobrou. Sem linha, não houve cobrança. |
| Valor cobrado | **Fatura** | Idem. |
| Mês da parcela | **Fatura** (`periodo_fim`) | A data impressa na linha é a da compra original. |
| Data/hora da compra à vista | **Pluggy** | Traz data e hora reais; a fatura só o dia. |
| Estabelecimento (detalhe) | **Pluggy** | Traz cidade/país; a fatura abrevia. |
| Compra ainda não faturada | **Pluggy** | Comprou após o fechamento: existe, sem fatura ainda. |
| Categoria, dimensões, observação | **Usuário** > regra > Pluggy | Regra antiga do projeto. |
| Conferida (OK) | **Só usuário** | Regra antiga do projeto. |

Nenhuma fonte sobrescreve as outras fora do seu campo.

### Os três estados de um lançamento (não confundir)

- **`somente_conciliacao`** — registro de conciliação, não é evento de caixa. Hoje: a compra
  parcelada agregada. Visível, vinculável, fora do resultado.
- **`substituido_por`** (uuid → outra transação) — este lançamento é o **mesmo evento real** que
  outro; só o outro conta, e o vínculo 1-para-1 diz qual. Cobre o *pending → posted* (o eco
  aponta para a compra consolidada) e a *parcela mensal repetida* (aponta para a parcela que a
  fatura gerou naquele mês).
- **`duplicada`** — só o que sobrar: mesma cobrança duas vezes, sem estorno e sem par
  identificável. Se nada cair aqui na prática, o campo pode ser aposentado.

**Cobrança dupla REAL da operadora não usa nenhum dos três.** Ela vem como cobrança + estorno,
os dois lançamentos legítimos, que se anulam sozinhos no resultado — marcar qualquer um quebraria
a conta (esconderia a cobrança e deixaria o estorno negativo solto). Ela aparece **só** na
conciliação do PDF, no bloco "cobranças repetidas na própria fatura", que mostra o estorno e tem
o "conferido" para registrar a revisão humana.

### Tela `/relatorios/duplicidades-fatura`

Classifica no servidor (nunca por heurística sobre HTML) em quatro baldes: *parcela cobrada de
novo*, *eco pending → posted*, *precisa de revisão* e *aguardando a próxima fatura* (compra perto
do fechamento, que entra na fatura seguinte — não é duplicidade e não tem ação).

**Armadilha já resolvida:** agrupar parcelamento por `lojista + nº de parcelas` COLIDE — MECANICA
HOCHIOVE tem 6× R$583,33 e 6× R$1.316,66, e SESI FARMACIA tem vários. **O valor da parcela entra
na chave.** Foi essa colisão que fez o primeiro levantamento subcontar (R$ 9.907,69 em vez dos
R$ 18.915,71 reais).

**Outra armadilha:** `"Parcelado Lojista - Visa - X"` é o parcelamento inteiro (agregado);
`"Parcela Lojista Visa - X"` é a cobrança de UMA parcela. Só a forma mensal pode entrar em
"evidência inequívoca" — um agregado sem vínculo costuma ser parcelamento novo cujas parcelas
ainda vão aparecer, e marcá-lo apagaria compra real.

### Resultado aplicado em 29/08/2026

74 parcelas repetidas + 35 ecos vinculados via `substituido_por`. Despesa de 2026 caiu de
R$ 493.358,27 para **R$ 474.442,56** (−R$ 18.915,71). 2025 não mudou: o fenômeno só existe a
partir de **abril/2026**, quando o Pluggy mudou o comportamento nessa conta e passou a mandar as
mensais além do agregado. Naquele estágio intermediário sobraram 11 lançamentos para revisão
humana (R$ 1.678,72) e 7 aguardando a próxima fatura (R$ 1.232,52); as etapas posteriores
resolveram o restante e encerraram a tela de duplicidades sem pendência conhecida.

## Incidente de parcelamentos contados em dobro — identificado e resolvido em 29/08/2026

Depois que o vínculo persistente entrou, ficou visível que **26 parcelamentos estavam no Pluggy
com as duas representações ao mesmo tempo**: a compra inteira (valor cheio) E as cobranças
mensais. Como o agregado já cobre todas as parcelas, cada mensal por cima entra duas vezes na
despesa e **infla o DRE**.

São dois mecanismos distintos:

**Família 1 — "eco da compra": 21 lançamentos, R$ 2.268,07.** Transação individual de UMA
parcela, **2 dias antes** do agregado (19 dos 21 casos). É o ciclo PENDING → POSTED que não
fecha: o Pluggy registra a autorização e depois registra de novo consolidado, sem remover a
primeira. Sinal claro: pares com 1 centavo de diferença (R$43,34/R$43,33 PITTOLCALCADOS,
R$67,19/R$67,18 HNA, R$31,08/R$31,06 FARMACIA SAGRADO) — arredondamento da parcela.

**Família 2 — "mensais tardias": 34 lançamentos, R$ 7.639,62.** Cobranças **sempre no dia 12**
(12/06, 12/07, 12/08 de 2026), até 283 dias depois do agregado. **Nenhuma existe antes de
junho/2026** — o Pluggy mudou o comportamento nessa conta nessa data e passou a emitir as
parcelas mensais além do agregado. É por isso que o órfão é quase sempre 12/07/2026: no ciclo de
agosto chegam duas mensais para uma única parcela impressa.

Caso exemplar rastreado ponta a ponta — **OTICA CALLIARI, Ronaldo, 10× R$316 = R$3.160**,
comprado em 02/11/2025. A fatura cobrou as 10 parcelas certinho. O Pluggy mandou o agregado de
R$3.160 **mais** R$316 em 12/06, 12/07 e 12/08. Total no sistema: R$4.108. Sobrando R$948.

**Anomalia separada revisada durante a conciliação:** FARM GEREMIAS (Andrea) 3× R$63,30 tinha
**dois agregados** (26/11/2025 e 10/07/2026, ambos R$189,90) e linhas duplicadas nas faturas.
Foi um dos casos que exigiram decisão humana; consultar o vínculo e o log, não refazer por
heurística se o mesmo padrão reaparecer.

O primeiro levantamento encontrou R$ 9.907,69, mas estava incompleto: parcelamentos diferentes
do mesmo lojista e com o mesmo número de parcelas colidiam na chave. Depois de incluir o valor da
parcela, o excesso real ficou em **R$ 18.915,71**. A solução final não usa `duplicada`: 74 parcelas
repetidas e 35 ecos foram ligados ao lançamento que os substitui por `substituido_por`. Assim o
mesmo evento conta uma vez, continua visível e mantém uma relação auditável e reversível.

**Matcher já corrigido (commit `87a664b`):** quando o parcelamento TEM agregado, todas as linhas
do grupo apontam para ele (`_melhor_agregado` roda antes do casamento 1:1). Antes algumas
parcelas ficavam presas a uma mensal de mesmo valor — escolha arbitrária que ainda escondia a
duplicata atrás de um vínculo. Com a correção os órfãos de agosto/2026 subiram de 29 para 51:
**são as duplicatas ficando visíveis**, não regressão. Refazer vínculos de uma fatura já
existente: `POST /api/fatura/<id>/vincular-automatico` com `{"refazer": true}` — apaga só
`origem='automatico'`, nunca o manual.

**Por que o regime de caixa sozinho não resolvia:** as parcelas geradas pela fatura substituem o
agregado (que saiu do resultado), mas as mensais e ecos do Pluggy continuavam por cima. Foi o
vínculo `substituido_por`, e não uma exclusão ou marca genérica de duplicidade, que retirou os
R$ 18.915,71 excedentes do resultado.

## Cobranças que só existem na fatura (aplicado em 29/08/2026)

Terceiro tipo de pendência, ao lado das duplicidades: **a operadora cobrou e o Pluggy nunca
sincronizou**. A despesa (ou o crédito) simplesmente não existia no resultado.

Rota `POST /api/faturas/criar-cobrancas-sem-pluggy` (`preview: true` levanta sem gravar; `ano`
limita o alcance). **Trava de segurança:** recusa (409) se ainda houver lançamento do Pluggy sem
vínculo esperando decisão — sem órfão do outro lado, criar pela fatura não pode duplicar.

Tipos com categoria fixa, conferidos um a um contra o PDF: `Unicred TAG` → Pedágio,
`Anuidade - bonificação` → Tarifas do Cartão (é o crédito que estorna a anuidade, e o Pluggy
mandava só a cobrança), `IOF compra internacional` → IOF, `ESTORNO` → sem categoria fixa.
O resto nasce sem categoria e passa por `aplicar_regras()`.

**Resultado:** 1.135 lançamentos criados (80 + 15 em 2026 + 1.040 em 2025), R$ 159.464,93.
**As 20 faturas (01/2025 a 08/2026) fecham 100%** — nenhuma linha sem vínculo, nenhum órfão do
Pluggy. DRE: 2025 de R$ 167.590,71 para R$ 309.144,60; 2026 de R$ 475.022,75 para
R$ 487.279,16; e R$ 6.106,42 caíram em 2024 (o ciclo da fatura 01/2025 começa em 17/12/2024).
O salto de 2025 é o número real: antes o ano estava subestimado em quase metade, porque o
Pluggy só passou a sincronizar esse cartão em **22/07/2025**.

## Lançamento "fora do resultado" e como a tela mostra isso

A tela de Lançamentos lê `cartao.transacao` **direto**, então mostra também o que a view
financeira exclui: `substituido_por` (mesmo evento que outro) e `somente_conciliacao` (a compra
parcelada inteira). Sem marca visual, dois lançamentos de mesmo valor e data aparecem lado a lado
sem pista de que só um conta — e quem revisa conclui que há duplicidade no DRE. Aconteceu:
SESI FARMACIA 12/08/2026, a parcela gerada pela fatura e a cobrança do Pluggy, ambas R$ 41,23.

Hoje a linha fica esmaecida, com a descrição riscada e um selo **"fora do resultado"** cujo
tooltip diz o motivo (classe `fora-resultado` + `.selo-fora` no `app.css`). **Ao criar um estado
novo que tire lançamento do resultado, marcar aqui também** — senão a tela mente por omissão.

### Defasagem de um mês no parcelamento recém-comprado (comportamento esperado)

Enquanto o agregado atende **uma única** linha de fatura, ele não é reconhecido como agregado
(isso exige 2+), não vira `somente_conciliacao` e **o valor cheio conta no mês da compra** — o
oposto do regime de caixa. Corrige-se sozinho quando a fatura do mês seguinte traz a Parc.2/N.
Exemplo: `08/08/2026 Parcelado Lojista - SESI FARMACIA R$ 235,48` (6× R$ 39,25) contando inteiro
em agosto. Se algum dia incomodar no fechamento mensal, dá para antecipar gerando as parcelas
futuras a partir do número de parcelas impresso na fatura.

## Revisão técnica do conjunto publicado — 29/08/2026

Revisão feita sobre `cfa183e..7c1ce99`, seguida das correções de permissões, upload e precisão
monetária, com suíte local de **212 testes aprovados e 6 ignorados**. Pontos positivos que devem ser
preservados:

- a conciliação deixou de ser uma heurística sem memória e passou a guardar vínculos N:N;
- import, criação de parcelas e reenvio do mesmo PDF têm travas de idempotência;
- `somente_conciliacao`, `substituido_por` e `duplicada` representam causas diferentes, em vez
  de esconder tudo sob uma única marca;
- a view financeira centraliza o que entra no DRE, reduzindo o risco de cada relatório calcular
  um número diferente;
- o Pluggy continua sem autoridade para sobrescrever classificação e OK humanos;
- houve melhoria de auditoria, testes sintéticos dos casos reais e proteção contra exclusão de
  dimensões ainda em uso.

Pontos de atenção encontrados, por prioridade:

1. **Resolvido — “ver relatório” foi separado de “editar conciliação”.** A migração 26 criou
   `conciliacao_editar`, preservou o acesso de Administradores/Operadores e retirou as ações de
   gravação do perfil Somente leitura. Há testes cobrindo todas as rotas mutáveis dessa tela.
2. **Resolvido — PDF limitado e validado antes de processar.** O servidor recusa acima de 10 MB,
   conteúdo sem assinatura `%PDF-` e documentos acima de 50 páginas. Há mensagem clara e testes
   inclusive para o caminho 413; o original continua armazenado somente após o parser aceitar.
3. **Parcialmente resolvido — ampliar testes automatizados do parser.** Já há teste sintético de
   referência, vencimento, titular, parcela, valor e estorno, além das travas de entrada. Ainda
   faltam casos de moeda estrangeira e variações de múltiplas páginas; a validação dos PDFs reais
   continua necessária. Alteração de layout da Unicred deve falhar no CI.
4. **Resolvido — conciliação usa `Decimal`/centavos exatos.** O parser preserva `Decimal` até o
   banco; matcher, tolerâncias, estornos e totais trabalham com inteiros em centavos. `float` só
   aparece na saída para manter o contrato numérico das telas/APIs. Há testes de erro binário,
   soma de mil centavos, arredondamento `ROUND_HALF_UP` e limite exato da tolerância de R$ 1,00.
5. **Operacional — manter validação pós-deploy e planejar staging.** A suíte está saudável, mas
   `main` publica direto no sistema familiar. Até existir staging, qualquer mudança em matcher,
   migração ou view financeira precisa registrar os totais antes/depois e validar as telas
   logadas após a troca do container.

Validação do deploy `5e12938` (precisão monetária): as **20 faturas** de 01/2025 a 08/2026
continuaram com ✓/100%. O DRE 2026 ficou idêntico antes e depois: receitas **R$ 458.242,64**,
despesas **R$ 487.279,16**, resultado **R$ -29.036,52** e investido/bens **R$ 16.080,86**.

## Pendências conhecidas

### Ação do usuário (nada disso o Claude pode fazer sozinho)

- **Rotar o token do Coolify.** O token foi colado no chat durante a sessão de 21/08/2026 e
  deve ser considerado comprometido. Gerar novo em Coolify → Keys & Tokens e revogar o antigo.
- **Revisar `/pendencias`.** O último retrato visto em produção (24/08/2026) mostrava **1
  categoria sem natureza e 4 categorias de despesa sem centro de custo**. Os nomes/contagens
  mudam conforme o Pluggy traz categorias e o usuário classifica; abrir a tela de novo antes de
  agir. Categoria sem natureza assume `despesa` e pode inflar o DRE.

### A validar com o usuário (dado que falta)

**Andar de cima da residência alugado para a BRDrive.** A casa tem dois andares:
a família mora no porão e a parte de cima é alugada para a BRDrive por
R$ 1.500–1.700/mês. Isso significa que (a) há receita de aluguel a identificar
nos recebimentos da BRDrive, hoje possivelmente confundida com pró-labore, e
(b) parte da manutenção da casa é custo desse aluguel, não despesa doméstica.
Levantado em 22/08/2026, a ajustar depois.

**Duplicidades antigas.** O Pluggy já mandou o mesmo débito duas vezes (Cond Sta
Lúcia, 21/11/2025 — conferido na conta, ocorreu uma vez só). A tela de
Lançamentos agora avisa quando encontra repetição no mês aberto, mas **os meses
anteriores nunca foram varridos**. Vale uma passada mês a mês.
O caso sistemático dos parcelamentos Unicred de 2026 foi resolvido separadamente com
`substituido_por`; não confundir com estas suspeitas históricas ainda não revisadas.

**Horários 00:00 e diferença de três horas.** Em Conta Corrente Ronaldo/Andrea há movimentos
que chegam às 00:00 e, em agosto, suspeitas com diferença de 3h. Não usar horário isoladamente
para apagar/mesclar: pode ser ausência de horário na origem ou conversão de fuso. Ronaldo decidiu
revisar e marcar duplicidades manualmente. O sistema deve continuar trazendo tudo do Pluggy.

**Depósitos em espécie sem origem identificada.** A categoria `Transfer - Cash`
tem 32 lançamentos; os maiores em 2026 são +R$ 16.197,64 (13/07), +R$ 12.029,00
(10/08) e +R$ 8.072,30 (21/07). Estão em natureza `fluxo`, então **entram como
receita**. Ronaldo não soube dizer a origem de cabeça (22/08/2026) — precisa ser
caso a caso. Enquanto não for, esses valores podem estar inflando a receita.

### Retomada recomendada (atualizada em 29/08/2026, fim da sessão)

**O cartão de crédito Unicred está fechado ponta a ponta.** As 20 faturas (01/2025 a 08/2026)
fecham 100%, a tela de duplicidades está limpa e a sincronização do Pluggy foi validada como
não-destrutiva. Não há pendência conhecida nessa frente.

Próximas frentes, nesta ordem:

1. **Cartão Nubank.** Avaliar se os mesmos fenômenos existem (parcelamento agregado, eco
   pending→posted, cobrança só na fatura). O parser de PDF é específico da Unicred — se o Nubank
   for conciliado por fatura, precisa de parser próprio; se não, a hierarquia de fontes muda.
2. **Conta corrente.** **Não tem fatura**, então a hierarquia nasce diferente: o extrato do
   Pluggy vira a única fonte de "houve cobrança", e provavelmente aparecem outros fenômenos
   (PIX, transferência entre contas próprias, depósito em espécie).
3. **Conferir o DRE mês a mês** agora que a base do cartão está consistente.
4. **`/pendencias`**: os 1.135 lançamentos criados pela fatura nasceram sem categoria (fora os
   tipos com padrão conferido) e passaram por `aplicar_regras()`. O que sobrou sem categoria
   entra no DRE como despesa por padrão — revisar.

### Ideias guardadas (decidir quando fizer sentido)

**Lançamentos recorrentes / previstos.** Há gastos que se repetem em valor e
intervalo fixos — a mesada de R$ 100 semanal é o caso mais claro, mas também
assinaturas, seguros e parcelas. O sistema hoje só registra o que já aconteceu.
Valeria um campo/marcação que identifique o lançamento como recorrente e permita
**projetar os próximos com base no histórico** — para saber o compromisso do mês
antes de ele acontecer. Levantado por Ronaldo em 22/08/2026, ao conciliar julho.

**Backup fora do servidor.** O backup atual no mesmo servidor foi aceito como primeira camada,
mas não protege contra perda do próprio servidor. Futuramente copiar para armazenamento externo.
Teste de restauração foi explicitamente adiado pelo usuário; não executar sem nova autorização.

### Técnicas

**Escape em JS montado no cliente:** três telas montam HTML no navegador com `innerHTML`
a partir de dados que chegam por AJAX (`lancamentos.js`, `relatorios.js`, `categorias.js`).
Aí o Jinja não protege — quem escapa é o `escHtml()` do `tabelas.js`, no ponto onde o
`innerHTML` é montado. A regra combinada: **o servidor manda texto puro** (`cat_pt_puro`,
sem `esc()`) **e o JS escapa**. Não escapar no servidor também: gera escape duplo, e em
rótulo de gráfico (Chart.js desenha em canvas) apareceria `&amp;` literal na tela.

**Um processo Gunicorn, com threads (não aumente `-w`):** `core.py` guarda os apelidos de categoria
(`CATEGORIA_PT_DB`) em memória e só recarrega depois de um POST. Com mais de um processo, cada
um tem a sua cópia: renomear uma categoria atualiza a de quem atendeu o POST e o outro segue
servindo o nome antigo por tempo indeterminado. Aconteceu em produção com `-w 2`. Se um dia
precisar de mais paralelismo, aumente `--threads` (memória compartilhada), não `-w` — ou tire
o cache de memória e leia do banco a cada requisição.

**Gunicorn com `--preload` (não remova o preload):** o app principal roda em
`gunicorn --preload -w 1 --threads 4 --timeout 120`. O `--preload` não é detalhe de performance —
`core.py` chama `migrate()` no import, então sem ele **cada worker roda a migração ao mesmo
tempo no boot** e as DDL competem entre si (medido: 3 workers = 3 imports; com preload = 1).
É seguro porque nenhuma conexão de banco fica aberta em variável de módulo. Se algum dia
alguém criar um pool global, o preload passa a compartilhar socket entre os processos filhos
e vira bug. O `--timeout 120` existe porque "Atualizar agora" chama o worker de sync com
timeout de 60s — o padrão do gunicorn (30s) mataria o processo antes.

**Sem ambiente de staging:** todo push na `main` vai direto para o app que a família usa.
Mitigado hoje pelos testes e pela validação pós-deploy, mas o risco existe.

**Operacional:** toda vez que uma nova conexão bancária for adicionada no Pluggy, o `item_id`
precisa entrar manualmente na env `PLUGGY_ITEM_ID` do worker (`hdgffcvh3ljqe61dczztaycz`) —
a auto-descoberta só funciona para conexões que já sincronizaram alguma vez.

**Colunas órfãs:** `conta.dia_fechamento` e `conta.dia_vencimento` existem no banco (migração v3)
mas não são lidas nem gravadas por ninguém — foram uma tentativa de sobrescrita manual das datas
de fatura, descontinuada. Ficaram porque reescrever migração já aplicada criaria divergência de
schema entre bancos.

**Decisão sobre o worker (21/08/2026):** o worker fica **acessível publicamente**, sem restrição
de IP. Tentamos aplicar uma `ipallowlist` no Traefik e ela **quebrou o sync** (403): o app chama
o worker pela URL pública e o Traefik enxerga um IP interno do Docker, não o IP público do
servidor (45.163.12.5). Foi revertido. A proteção real hoje é por chave (`SYNC_SECRET`), não por
rede. Se algum dia quiser fechar de verdade: fazer o app chamar o worker pela rede interna
(`http://<container>:8000/sync`) e remover o domínio público dele.

## Armadilhas do matcher de fatura (todas já custaram caro — não repetir)

Cada uma destas causou erro real e tem teste em `tests/test_fatura_vinculo.py`. Antes de mexer
em `_conciliar_linhas`, `_classificar_orfaos` ou `_vincular_automatico`, leia esta lista.

**1. Chave de agrupamento sem o valor.** Agrupar parcelamento por `titular + lojista + nº de
parcelas` **colide**: o mesmo lojista tem mais de um parcelamento com o mesmo número de parcelas
e valores diferentes (MECANICA HOCHIOVE: 2× R$135,00 e 2× R$233,50 na MESMA fatura; SESI FARMACIA
tem vários). Colidindo, o valor da parcela vira a média, o valor cheio esperado sai errado e
nenhum agregado é encontrado — os dois viram órfãos. **O valor entra sempre na chave.** Esse erro
apareceu duas vezes no mesmo dia: no código de produção e na análise que eu fiz por fora.

**2. Um cursor por conexão — consumir antes da próxima consulta.** Inserir um `cur.execute` entre
o `execute` e o `fetchall` de outra consulta faz a primeira sumir **em silêncio**: a tela mostrou
"nenhuma cobrança em dobro" com tudo zerado, HTTP 200, sem erro nenhum.

**3. Lançamento nascido da fatura não é candidato.** A parcela gerada tem o valor exato da parcela
e cai dentro do ciclo, então disputa a linha com o agregado do Pluggy e vira órfã. Ela não é um
lançamento do Pluggy — ela **é** a fatura. Filtrada por `fatura_linha.transacao_id_criado`.

**4. Vínculo `origem='fatura'` não bloqueia o vínculo com o agregado.** São os dois lados da
conciliação: a parcela gerada é o evento de caixa, o agregado é o registro da compra. Tratar a
linha como "já vinculada" fazia o agregado nunca ser reencontrado e o regime de caixa parar de
se aplicar.

**5. "Refazer vínculos" só apaga `origem='automatico'`.** Apagar o vínculo `fatura` deixava a
parcela gerada órfã para sempre (a geração é idempotente por `transacao_id_criado` e não recria).
Deu 340 falsos positivos numa rodada. Hoje `_sincronizar_parcelas_de_agregado` recria o que faltar.

**6. Janela de candidatos vai além do fim do ciclo.** O Pluggy às vezes data a compra 1–2 dias
depois do que a fatura imprime (D MORI: fatura 11/02, Pluggy 12/02). Sem folga, a transação nem
entra como candidata. Hoje busca 3 dias além; só a avulsa aproveita.

**7. Comparar descrição inteira nunca casa o par do mesmo evento.** O Pluggy grava o mesmo evento
com prefixos diferentes (`"Compra Exterior R$ - Visa - X"` vs `"Compra Exterior - Visa - X ...COMUS"`).
`_tokens_significativos()` remove o prefixo genérico e compara só o que identifica o
estabelecimento — exige 2+ tokens em comum, mesmo valor e ±1 dia.

**8. `"Parcelado Lojista"` ≠ `"Parcela Lojista"`.** O primeiro é o parcelamento inteiro (agregado);
o segundo é a cobrança de UMA parcela. Só a forma mensal pode entrar em "evidência inequívoca":
um agregado sem vínculo costuma ser parcelamento novo cujas parcelas ainda vão aparecer, e
marcá-lo apagaria compra real.

**9. Reenviar o PDF apaga os vínculos da fatura.** As linhas são apagadas e recriadas com ids
novos, e o `ON DELETE CASCADE` leva `fatura_vinculo` junto. O `transacao_id_criado` sobrevive
pela chave natural, mas o vínculo não — e a geração de parcelas é idempotente, então não repõe.
A fatura 01/2026, reenviada pelo usuário, ficou com 33 parcelas geradas aparecendo como órfãs.
Hoje `_revincular_lancamentos_da_fatura()` roda no import e na sincronização de parcelas.

**10. Cada tela tem que aplicar os MESMOS filtros de "já resolvido".** A lista de órfãos da
conciliação não excluía `substituido_por` nem `somente_conciliacao`, então repetia como pendência
tudo o que a tela de duplicidades já tinha resolvido: 57 falsos pendentes em 08/2026 enquanto a
outra tela dizia "nada pendente". Ao criar um estado novo, procurar TODAS as consultas que
listam pendência.

**11. Eco de parcelamento NOVO precisa de regra própria.** Enquanto o agregado atende UMA linha
só, ele não é reconhecido como agregado (isso exige 2+) e o eco escapa das outras regras. A regra
que resolve: existe linha de fatura do mesmo estabelecimento **já vinculada**, e o órfão vale ou
a parcela dela ou o parcelamento inteiro, dentro de 5 dias. A comparação usa a `descricao_base`
da **linha**, não a do agregado — `"PARC=106ANJOS DE QUINTA"` não casa com `"ANJOS DE QUINTAL"`.

**12. Valor negativo não casa por descrição.** O Pluggy chama o mesmo pagamento de
`"Pagamento recebido"` e de `"Pag de Fatura Via Deb Aut"` — zero palavras em comum, então tokens
não servem. Para negativo o par é **valor idêntico no MESMO dia**, com a outra gravação já
vinculada à fatura; exige dia exato justamente por não ter o reforço da descrição. Como o par tem
o mesmo sinal, um estorno (sinal oposto ao da cobrança) nunca casa por aqui.

**13. Lançamento criado pela fatura: parcela usa o MÊS COBRADO.** Datar pela data impressa joga
a despesa no mês da compra — o oposto do regime de caixa. Errei nisso: criar as linhas de faturas
de 2026 mandou R$ 11.027,44 para 2025. Parcela usa `periodo_fim` da fatura; compra avulsa usa a
data impressa, que ali é a da própria cobrança.

**14. Commit também quando só houve correção.** O `UPDATE` que conserta datas só era commitado
quando havia linha nova criada. Sem linha nova, a rota respondia "11 datas corrigidas" e **nada
era gravado** — em silêncio, com sucesso na resposta.

**15. "Pagamento Recebido" nunca vira lançamento nem trava o fecha 100%.** É a fatura anterior
sendo quitada; já fica fora das duas somas. Cobrar vínculo dela travava as 7 faturas do início de
2025, onde era a única linha pendente e o Pluggy não tinha o pagamento.

**16. Estorno só anula quando os dois lados ainda contam.** Se o negativo já foi excluído
(duplicada, substituido_por, somente_conciliacao), o par deixou de se anular e a cobrança positiva
ficou sozinha no resultado — ela tem que seguir para as outras regras, não ser dada como resolvida.

**17. Cobrança estornada não se marca.** Se existe um negativo de mesmo valor no mesmo dia, os dois
lançamentos são legítimos e se anulam sozinhos. Marcar um deixaria o estorno negativo solto.

## Lições da sessão de 29/08/2026 (incidente real — não repetir)

Nesta sessão o Claude **corrompeu 22 datas de lançamentos reais em produção** e, antes disso,
subiu duas correções baseadas em hipótese não verificada. Vale mais que qualquer regra abstrata:

1. **Testar hipótese localmente, nunca em produção.** Os dois erros do dia vieram do mesmo
   vício: deployar para descobrir se a teoria estava certa. O `consolidar-datas` corrompeu dado
   real; o fix do "mês vizinho" foi subido com base num caso (SAMILA) que não generalizava para
   o caso que se queria resolver (AQUAMATER), e não resolveu nada.
2. **Escrever o teste do caso real ANTES de mexer no algoritmo.** Quando os testes de
   `tests/test_fatura_vinculo.py` foram escritos, dois bugs latentes apareceram na hora — um
   deles (`ciclo_fim` vindo de `max(datas)` das linhas) estava em produção havia dias, invisível.
3. **Quando o número contradiz o que já se viu na tela, o erro provavelmente é da análise.**
   A primeira contagem de parcelamentos duplicados deu "0", contradizendo o AQUAMATER já visto.
   Causa: a célula de valor do vínculo tem `colspan="2"`, então o índice do `children[]` estava
   deslocado e todos os valores vinham zerados. Conferir contra um caso conhecido antes de
   confiar em qualquer levantamento.
4. **O log de auditoria salvou o dia.** Foi só porque cada alteração gravava antes/depois que
   deu para reverter as 22 datas exatamente. Não enfraquecer a auditoria.

## Como trabalhar neste projeto (fluxo que deu certo)

Nenhuma skill "empacotada" foi usada — é engenharia direta (Python/Flask/SQL/Coolify API).
As skills de BRDrive disponíveis no ambiente (vendas, propostas) são de outro contexto e não
têm relação com este projeto.

O ciclo usado na sessão 2, que vale repetir:
1. **Ler o código antes de mudar** — várias vezes a causa raiz era diferente da aparente
   (ex: coluna que "não reduzia" era um `max-width:0` conflitante; divisor "sumido" entre
   Origem e Categoria era `display:flex` vazando de `.cel-origem` para o `<th>`).
2. `python3 -m py_compile app.py bussola/app.py` e `pytest tests/ -q` antes de commitar.
3. Commit + push (o webhook faz o deploy sozinho), **e então validar em produção** —
   status no Coolify, `/health`, logs (procurar traceback e `Aviso: falha ao rodar migracao`)
   e teste real da tela pelo navegador.
4. **Testar de verdade, não só ler o código.** O teste com payload real de XSS encontrou 3
   pontos que a varredura por grep não pegou. Testar em produção também já mostrou que o
   `localStorage` precisa ser limpo **antes** de recarregar, senão o estado antigo em memória
   falseia o resultado.
5. Limpar dados de teste depois (categoria de teste, teto de teste, `localStorage`).

## Como continuar no Claude Code

1. Instalar: `npm install -g @anthropic-ai/claude-code` (requer Node.js).
2. Clonar o repositório localmente:
   ```bash
   git clone https://github.com/ronaldinhodelima/pe-de-meia.git
   cd pe-de-meia
   ```
3. Este arquivo (`CLAUDE.md`) deve ficar na raiz do repositório — o Claude Code lê
   automaticamente ao iniciar uma sessão nessa pasta.
4. Autenticação git: configurar um Personal Access Token do GitHub (ou SSH key) uma única vez
   no `git credential helper` local, para não precisar colar o token a cada push.
5. Para deploys, seguir o fluxo descrito em "Repositório e deploy" acima — o token do Coolify
   precisa ser configurado como variável de ambiente local (não commitado).

## Deploy automático

Webhook do GitHub -> Coolify configurado em 20/08/2026. Todo push na `main` dispara build/deploy sozinho, sem precisar chamar a API do Coolify manualmente.

## Classificação: natureza e centro de custo (decisão de 21/08/2026)

A tela **`/pendencias`** ("Pendências de classificação", no menu Configurações) materializa
tudo isto: lista categoria sem natureza e categoria de despesa sem centro de custo, com ação
direta em cada linha, e conta os lançamentos que ainda usam natureza manual. Uma faixa de
alerta aparece no DRE quando há pendência que distorce número — natureza manual sozinha
**não** dispara alerta, porque não é erro.

Como garantir que os números do DRE são reais, sem despesa inflada:

- **A natureza vem da categoria, não do lançamento.** Para classificar uma operação fora do
  padrão (ex: um PIX de R$ 98 mil que foi a compra de um terreno), o caminho correto é **mover
  o lançamento para uma categoria com a natureza certa** ("Imóveis / Terrenos" → `bem`). Assim
  não importa se o meio foi PIX, cartão ou dinheiro — a categoria carrega a natureza.
- O campo "natureza" no modal do lançamento (`transacao.natureza`) ainda existe e continua
  sobrepondo a da categoria, mas é a via **antiga**: fica invisível para quem olha a categoria
  depois. A tela `/pendencias` conta quantos lançamentos ainda usam isso.
- **Categoria sem natureza é o problema mais grave**: o app assume `despesa` por padrão
  (`NATUREZA_PADRAO`), então uma categoria nova inventada pelo Pluggy entra como despesa
  *silenciosamente*. Não dá para bloquear o Pluggy de criar categorias — a solução é alertar
  (`/pendencias` + faixa no DRE) para o usuário decidir: definir natureza, renomear ou ocultar.
- **Centro de custo só se aplica a categorias de despesa.** Vincular receita ou transferência a
  um centro de custo não faz sentido contábil — centro de custo é análise de gasto. Por isso
  `/pendencias` só cobra vínculo das categorias com natureza `despesa`.

## Colunas ajustáveis nas tabelas

Utilitário compartilhado no `<script>` do `BASE_CSS` (roda em todas as telas). Para ligar numa
tabela nova, basta: `<table class="compacta ajustavel" data-tabela="chave-unica">`. O resto é
automático — atribui `data-col` por índice quando o HTML não traz, injeta o botão "Redefinir
colunas" e se ativa no `DOMContentLoaded`.

- `data-sem-ordenar` / `data-sem-reordenar` desligam recursos individualmente. Usado no
  **Centro de Custos**, que é hierárquico (linhas de grupo usam `colspan`): ordenar embaralharia
  a hierarquia e reordenar colunas quebraria essas linhas.
- Preferências (ordem, largura, ordenação) ficam no `localStorage`, chave `pedemeia_tabela_<chave>`.
- Redimensionar é **estilo planilha**: a coluna vizinha compensa, então a soma nunca muda e a
  tabela nunca estoura a largura da tela (a de Lançamentos não tem scroll horizontal).
- Ordenação numérica entende `R$ 1,234.56` (formato do `:,.2f` do Python, que é o usado no app)
  **e** `R$ 1.234,56` — o separador decimal é o último `.` ou `,` do texto.

Duas armadilhas já resolvidas, que voltam a morder se alguém mexer:
- Quando um filtro recarrega a tabela por AJAX (`aplicarFiltros` faz `replaceWith`), **é preciso
  chamar `ativarTabelaAjustavel()` de novo** — o elemento antigo vai embora levando os listeners.
- CSS de célula não pode vazar para o `<th>`: `.cel-origem { display:flex }` (pensado para o selo
  do banco na célula) tirava o cabeçalho do grid da tabela e fazia a coluna seguinte desenhar por
  cima. Por isso a regra é `td.cel-origem` e há um `display:table-cell !important` defensivo nos
  `th[data-col]`.

## Testes automatizados

Em 29/08/2026 a suíte local está em **212 aprovados e 6 ignorados**. Ela cobre a regra de ouro
do DRE, helpers puros, segurança/XSS, permissões, estrutura de rotas/templates, concorrência,
auditoria, regras automáticas, rateio, conciliação de fatura e fluxos com PostgreSQL temporário.

`tests/test_fatura_vinculo.py` reproduz, com dados sintéticos e cursor dublado (roda sem
Postgres), os casos reais que quebraram a conciliação: parcela 1:1 dentro do ciclo, transação já
vinculada que não pode ser roubada, parcelamento agregado que PODE ser reusado, "Pagamento
Recebido" fora das duas somas, encadeamento das datas e precisão monetária em centavos. **Foi
escrevendo esses testes que dois
bugs latentes apareceram** — ver "Lições da sessão de 29/08/2026".

Validação do parser contra dado real (fazer de novo se mexer em `fatura_unicred.py`): parsear os
PDFs e conferir que a soma das linhas, sem "Pagamento Recebido", bate com o total impresso. Em
29/08/2026 bateu **centavo a centavo nas 14 faturas** (set/2025 a ago/2026). Os seis testes
ignorados dependem de condições/serviços que não estão disponíveis em toda execução; conferir o
motivo do `skip`, não tratar automaticamente como falha.

Há testes de integração com PostgreSQL temporário, mas isso não substitui a validação logada em
produção: configuração, dados reais, rede do Coolify e comportamento do Pluggy são diferentes.

Rodar localmente:
```bash
pip install pytest flask psycopg2-binary
pytest tests/ -v
```

## Decisões consolidadas da revisão de agosto/2026

Estas são regras funcionais aprovadas pelo usuário e devem ser preservadas em mudanças futuras:

- **OK é uma assinatura humana.** Sincronização, regra automática, edição de categoria,
  dimensão ou observação nunca pode marcar nem desmarcar `conferida`. Para retirar um OK ou
  marcar duplicidade, a tela exige confirmação explícita. Lançamentos conferidos aparecem em
  cinza-claro, não em verde.
- **Rateio não duplica dinheiro.** Quando um único débito pertence a mais de uma pessoa ou
  classificação, o pai continua sendo o registro bancário e as partes aparecem recolhidas
  abaixo dele com botão `+`/`−`, descritas como `<descrição original> — Parte N`. As partes
  devem somar exatamente o total (inclusive o sinal) e substituem o pai no DRE/relatórios.
  Podem ser alteradas com o pai em OK sem apagar essa
  assinatura, mas o servidor só aceita o conjunto completo, fechado e com campos obrigatórios;
  desfazer o rateio por inteiro exige retirar o OK antes.
  Valor, categoria, dimensões e observação são editados diretamente nas linhas das partes e
  salvos juntos; o OK do pai fica desabilitado enquanto a soma não fechar ou faltar campo
  obrigatório. O OK continua sendo marcado apenas pelo usuário depois de conferir tudo.
- **Pluggy é a origem bancária, não a dona da classificação.** A sincronização pode atualizar
  apenas os campos bancários mutáveis. Nunca pode sobrescrever categoria ajustada manualmente,
  Responsável, Projeto, Portfólio, observação, OK ou a marcação de duplicidade.
- **Não eliminar movimentos repetidos vindos do Pluggy.** IDs distintos recebidos da operadora
  devem ser importados, pois podem representar cobrança realmente duplicada. A duplicidade é
  decidida pelo usuário e a marcação apenas exclui a linha dos totais.
- **DRE considera todos os lançamentos do período.** Foi desfeita a tentativa de limitar o DRE
  a `POSTED` até a data atual. A natureza contábil continua vindo da categoria, salvo o legado
  de natureza específica do lançamento.
- **Sessão:** duração de 24 horas, cookie `Secure`, `HttpOnly` e `SameSite=Lax`. Por decisão do
  usuário, os acessos administrativos de emergência continuam no ambiente e a senha mínima
  permanece com seis caracteres.
- **Auditoria:** Logs fica dentro de Relatórios. Registrar acessos, alterações com antes/depois,
  sincronizações e falhas, sem gravar senhas, chaves ou outros segredos.
- **E-mail operacional:** `ronaldo@brdrive.net`; envio de teste confirmado pelo usuário.
- **Backup:** cópia no próprio servidor é aceita, com retenção configurada. Não é necessário
  executar teste de restauração agora; não confundir isso com garantia de recuperação externa.
- **Navegação:** alterações de tela/filtro/URL devem criar histórico real, para o botão Voltar
  retornar ao estado anterior dentro do sistema. Troca de mês em Lançamentos faz recarga completa.

### Decisões da sessão de 29/08/2026 (conciliação de fatura)

- **A fatura é a fonte quando o Pluggy não sincroniza.** Se a operadora cobrou, o dinheiro saiu:
  a linha vira lançamento. Vale para os dois sentidos — pedágio e IOF **aumentam** a despesa, a
  bonificação da anuidade **reduz** (é o crédito que o Pluggy nunca mandava, deixando só a
  cobrança).
- **Criar pela fatura só é seguro com o outro lado zerado.** A rota recusa enquanto houver
  lançamento do Pluggy sem vínculo esperando decisão — sem órfão do outro lado, não há como
  duplicar.
- **2026 tem que fechar 100%; 2025 é histórico.** As faturas de 2025 foram importadas para dar
  contexto. Ambos os anos acabaram fechando, mas a prioridade declarada é 2026.
- **Vínculo manual de "mesmo evento"**: o usuário pode apontar os dois lados na mão quando
  nenhuma regra alcança, inclusive para desfazer um "duplicado" marcado por engano.


- **A fatura em PDF é a autoridade.** Se está no Pluggy, tem que bater com a fatura de alguma
  forma — não basta "fechar o valor", cada lançamento precisa de vínculo ou de explicação.
- **Vínculo automático grava sozinho quando não há ambiguidade** (decisão do usuário, entre
  "gravar sozinho" e "só sugerir"). Ambíguo fica pendente esperando ele. Vínculo automático pode
  ser desfeito a qualquer momento; vínculo manual nunca é sobrescrito.
- **Consolidação de data é por lançamento, um a um** — nunca em lote por fatura. Exige conferência
  manual e vai junto com observação/vínculo. Ficou para ser desenhada com calma dentro do painel
  de vínculo (o usuário pediu explicitamente "pensar melhor como fazer e usarmos").
- **A tela é organizada em torno da fatura**, com `+` por linha abrindo os lançamentos vinculados,
  e vínculo possível nos dois sentidos (da linha para o lançamento e do lançamento para a linha).
- **Marcação de duplicidade continua sendo só do usuário**, inclusive nos R$ 9.907,69 de
  parcelamento contado em dobro. O Claude levanta, evidencia e explica; não marca.

### Regras automáticas

- Podem filtrar por trecho da descrição e, opcionalmente, por valor absoluto (`<`, `<=`, `>`,
  `>=` ou `=`). A prévia mostra quais lançamentos pendentes e ainda não ajustados manualmente
  receberão a nova regra.
- Nunca se aplicam a lançamento conferido nem a categoria escolhida manualmente.
- Regra aprovada para descrições contendo `GuilhermeDaSilva`:
  - valor menor que R$ 120,00: categoria **Água**, Responsável **Família**, Projeto **Casa**,
    Portfólio **Moradia**;
  - valor maior que R$ 120,00: categoria **Gás**, com as mesmas dimensões;
  - R$ 120,00 exatos ficam sem classificação automática até nova decisão.
- A antiga categoria **Água / Gás** passa a se chamar apenas **Gás**; Água é uma categoria
  própria. Ambas são despesas do centro de custo Moradia & Utilidades / Casa.
