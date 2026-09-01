# Pé de Meia — contexto do projeto

**Última revisão:** 01/09/2026 · **Schema:** migração 47 · **Testes:** 270 aprovados, 6 ignorados
· **Produção:** https://pedemeia.brdrive.net

Sistema financeiro pessoal/familiar da família Ronaldo. Sincroniza cartão de crédito e conta
corrente via Open Finance (Pluggy) do Unicred e Nubank (duas contas Nubank: Ronaldo e Andrea),
e concilia o cartão Unicred contra a fatura oficial em PDF.

Este arquivo existe para que qualquer sessão do Claude retome o projeto sem redescobrir decisões
já tomadas. **Leia inteiro antes de mexer em qualquer coisa.** Ele é normativo, não é diário:
se algo aqui não for mais verdade, corrija em vez de acrescentar uma seção nova contradizendo a
anterior.

---

# 1. Regras invioláveis

## 1.1 Regra de ouro (instrução permanente do usuário)

> "Sempre que formos falar em financeiro, preciso que traga as considerações do DRE, do conceito
> de DRE financeiro, não podemos ter dados mascarados ou informações que inflem os lançamentos.
> Os números precisam ser reais."

Na prática:

- Resultado = Receitas − Despesas. Só isso é "resultado".
- Investimento, compra de bem (terreno, veículo, imóvel), pagamento de fatura de cartão e
  transferência entre contas próprias **não são despesa** — só trocam a forma do patrimônio.
- Juros e tarifas **são despesa de verdade** (o dinheiro sai e não volta).
- Terreno não deprecia. Depreciação não está implementada: bens ficam fora do resultado e não
  geram despesa.
- Toda vez que mexer em relatório, DRE ou natureza de categoria, **explicar o raciocínio
  contábil** — nunca só aplicar.

## 1.2 O OK é uma assinatura humana

**Nunca marcar nem desmarcar o "conferido" de um lançamento.** Só o Ronaldo ou outro usuário
marca. O Claude pode ajustar categoria, dimensão, natureza e observação; o check é a confirmação
humana de que o lançamento foi conciliado. Lançamento manual criado pelo Claude nasce desmarcado.

Sincronização, regra automática, migração, importação e edição de qualquer campo **nunca** podem
alterar `conferida`, `conferida_por` ou `conferida_em`. Retirar um OK ou marcar duplicidade exige
confirmação explícita na tela.

## 1.3 Marcação de duplicidade é decisão do usuário

O Claude levanta, evidencia e explica; não marca. Vale inclusive quando a evidência parece
inequívoca. IDs distintos recebidos da operadora são sempre importados — podem representar
cobrança realmente duplicada.

## 1.4 Nunca inventar número ou informação

Se não souber, perguntar. Não estimar, não arredondar para "fechar", não deduzir de memória.
Quando um levantamento contradiz o que já se viu na tela, **o erro provavelmente é do
levantamento** — conferir contra um caso conhecido antes de confiar.

## 1.5 Preferências de estilo do usuário (Ronaldo)

- Respostas diretas, sem enrolação, com tópicos quando fizer sentido.
- Interface do sistema em português.
- Ao classificar lançamento, **avaliar a melhor forma de distribuir e organizar** — categoria,
  natureza, dimensão e projeto — em vez de só encaixar no que já existe.

## 1.6 Regra permanente de publicação

Todo deploy atualiza este arquivo com decisões, comportamento entregue, migrações, validações e
pendências. Antes de publicar: suíte pytest completa, `py_compile`, `git diff --check`. Depois do
deploy: conferir os logs, abrir a tela afetada em produção e comparar os números anotados antes.
**Não marcar/desmarcar OK real apenas para testar.**

---

# 2. Stack, arquitetura e deploy

## 2.1 Camadas

- `app.py` — cria o Flask app, sessão/auditoria, filtros Jinja, registra blueprints.
- `core.py` — constantes, acesso ao banco, permissões, migrações, helpers.
  **Não importa nada de `views/` nem de `app.py`.**
- `views/` — blueprints `auth`, `sistema`, `lancamentos`, `relatorios`, `cadastros`, `usuarios`,
  `logs`. Cada módulo importa do `core` só os nomes que usa.
- `templates/` (Jinja2) e `static/` (CSS, JS, logos). Sem framework front-end — JS puro.
- `bussola/app.py` — worker de sincronização Pluggy (serviço separado).

A dependência corre sempre `app.py → views/ → core.py`. Inverter recria o import circular.
`tests/test_estrutura.py` mantém a lista autoritativa de rotas e acusa rota perdida ou inesperada.

Banco: PostgreSQL, schema `cartao.*`, dentro do Coolify (host interno de rede Docker, não
acessível de fora).

## 2.2 Regras de código que já quebraram produção

**Nenhuma tela monta HTML por f-string no Python.** Se aparecer uma, é regressão.

**O escaping do Jinja é automático:** nada de `esc()` dentro de valor que vai para template
(gera `&quot;` visível). Quando o valor já é HTML confiável montado no Python, marque `|safe`
explicitamente — assim fica claro que foi decisão, não esquecimento.

**Nunca interpole dado dentro de `onclick`/`onchange`/`onsubmit`.** `|tojson` é o escape certo
dentro de `<script>` e **errado** dentro de atributo: não escapa aspas duplas, então
`onclick="f({{ id|tojson }})"` fecha o atributo cedo, o handler não compila e **falha em
silêncio**. Foi assim que Lançamentos parou de abrir detalhes e o "Excluir" de `/usuarios` passou
a apagar sem confirmar. Para levar dado ao JS: `data-attribute` + delegação de evento, ou um bloco
`<script type="application/json">`.

**Escape em JS montado no cliente:** `lancamentos.js`, `relatorios.js` e `categorias.js` montam
HTML com `innerHTML` a partir de AJAX. Ali o Jinja não protege — quem escapa é `escHtml()` do
`tabelas.js`. A regra combinada: **o servidor manda texto puro e o JS escapa.** Escapar dos dois
lados gera escape duplo, e em rótulo de gráfico (canvas) apareceria `&amp;` literal.

**Um processo Gunicorn, com threads — não aumente `-w`.** `core.py` guarda apelidos de categoria
em memória e só recarrega após um POST. Com mais de um processo, cada um tem sua cópia e um segue
servindo o nome antigo. Aconteceu em produção com `-w 2`. Se precisar de paralelismo, aumente
`--threads`, não `-w`.

**Gunicorn com `--preload` — não remova.** `core.py` chama `migrate()` no import; sem preload cada
worker roda a migração ao mesmo tempo no boot e as DDL competem. É seguro porque nenhuma conexão
fica aberta em variável de módulo — se alguém criar um pool global, o preload passa a compartilhar
socket entre filhos e vira bug. O `--timeout 120` existe porque "Atualizar agora" chama o worker
com timeout de 60s.

**Estado compartilhado:** `CATEGORIA_PT_DB` e `CATEGORIAS_OCULTAS` são recarregados em runtime.
Como os blueprints fazem `from core import ...`, `recarregar_categorias_db()` **altera os
dicionários no lugar** (`clear()` + `update()`). Trocar por reatribuição congelaria os nomes na
versão do boot, sem erro nenhum. `tests/test_core_estado.py` trava isso checando a identidade.

**Helper usado por mais de uma migração mora no módulo**, nunca dentro do bloco
`if versao_atual < N`. Num banco já migrado aquele bloco não roda e a migração seguinte quebra com
`NameError` — erro que não aparece em banco novo, que é onde os testes olham.

**O JS da visão detalhada usa parâmetro de versão no `src`.** Sempre renovar quando o
comportamento mudar, evitando HTML novo com script antigo no cache.

## 2.3 Repositório e deploy

- **GitHub**: `ronaldinhodelima/pe-de-meia`, branch `main`.
- **Coolify** (`https://coolify.brdrive.net`, projeto Ronaldinho):
  - App principal `conferencia-cartao-app`, uuid `nvbnzjhig1og7s0gn5nrbxjo`,
    domínio **https://pedemeia.brdrive.net**.
  - Worker de sync `bussola-financeira-app-v2`, uuid `hdgffcvh3ljqe61dczztaycz`.
    **Esse domínio já mudou sozinho uma vez** e quebrou "Atualizar agora" porque a URL estava
    hardcoded em `BUSSOLA_SYNC_URL`. Se o sync der 404/502, conferir isso primeiro.
- **Push na `main` dispara o webhook → Coolify.** Acompanhar build, troca de container e logs;
  push não é conclusão. Token do Coolify e credenciais ficam nas variáveis de ambiente do
  Coolify — nunca no código nem no git.
- **Ao criar pasta nova, adicione o `COPY` no Dockerfile** — o container sobe sem ela e quebra só
  na hora de servir a tela.
- **Não há ambiente de staging.** Todo push na `main` vai direto para o app que a família usa.

## 2.4 Operacional

Toda nova conexão bancária no Pluggy precisa do `item_id` na env `PLUGGY_ITEM_ID` do worker — a
auto-descoberta só funciona para conexões que já sincronizaram alguma vez.

**Decisão sobre o worker (21/08/2026):** fica acessível publicamente. Uma `ipallowlist` no Traefik
foi tentada e **quebrou o sync** (403): o app chama o worker pela URL pública e o Traefik vê um IP
interno do Docker. Foi revertido. A proteção real é por chave (`SYNC_SECRET`). Para fechar de
verdade: fazer o app chamar o worker pela rede interna e remover o domínio público.

---

# 3. Banco de dados (schema `cartao`)

| Tabela | Para que serve |
|---|---|
| `pluggy_item` | cada conexão bancária |
| `conta` | contas de cada item (corrente, cartão, "manual"/dinheiro) |
| `transacao` | lançamentos; chave `transacao_id` do Pluggy evita duplicidade |
| `sync_log` | auditoria das rodadas de sincronização |
| `categoria_natureza` | natureza contábil de cada categoria — base do DRE |
| `categoria` / `categoria_oculta` | renomeações e categorias escondidas pelo usuário |
| `grupo_custo` / `subgrupo_custo` / `categoria_subgrupo` | centro de custo |
| `usuario` | login (PBKDF2-HMAC-SHA256, 200k iterações), perfil e permissões |
| `item_titular` | de quem é cada conexão |
| `investimento` / `investimento_saldo` | posições e histórico diário |
| `cartao_nome` | apelido por 4 últimos dígitos |
| `regra_classificacao` / `regra_dimensao_valor` | regras automáticas |
| `dimensao` / `dimensao_valor` / `transacao_dimensao` | dimensões livres + tetos de gasto |
| `transacao_rateio` / `transacao_rateio_dimensao` | partes internas de um lançamento |
| `fatura_importada` / `fatura_linha` / `fatura_vinculo` | conciliação do PDF |
| `schema_version` | controle de migração |

**Um cartão de crédito (uma linha em `conta`) pode ter vários cartões físicos/virtuais.** A
`conta` guarda só o final do principal; os adicionais só aparecem em
`transacao.numero_cartao_final` — é dali que `/contas` descobre quais cartões existem.

**Colunas mortas, não voltar a depender delas:** `conta.fechamento_fatura` (o Pluggy nunca
preenche para nenhuma conta real), `conta.dia_fechamento` e `conta.dia_vencimento` (tentativa
descontinuada). Ficaram porque reescrever migração aplicada criaria divergência de schema.

**`transacao_dimensao.valor_id` é nulável e tem `ON DELETE SET NULL`.** Apagar um valor de
dimensão deixa a linha para trás com valor nulo. Testar só "a chave existe" dá a dimensão como
preenchida enquanto a tela mostra "(não definido)" — usar `_dimensao_vazia()`.

**Migrações:** cada bloco `if versao_atual < N` roda uma vez só. **Nunca reescrever migração já
aplicada** — criaria divergência de schema entre bancos. Antes de alteração de dados em lote,
criar tabela de backup no mesmo Postgres (`*_backup_vN`) e gravar auditoria com o resultado.

---

# 4. Modelo financeiro

## 4.1 Naturezas (base do DRE)

São **seis**: `despesa`, `receita`, `investimento`, `bem`, `transferencia` e `fluxo` — este
último é o padrão, e a direção do lançamento decide se é receita ou despesa (usado para
PIX/TED/dinheiro). As três neutras (`investimento`, `bem`, `transferencia`) ficam fora do
resultado.

**A natureza vem da categoria, não do lançamento.** Para classificar uma operação fora do padrão
(um PIX de R$ 98 mil que foi a compra de um terreno), o caminho correto é **mover o lançamento
para uma categoria com a natureza certa** ("Imóveis / Terrenos" → `bem`). Assim não importa se o
meio foi PIX, cartão ou dinheiro.

O campo `transacao.natureza` ainda existe e sobrepõe a da categoria, mas é a via **antiga**: fica
invisível para quem olha a categoria depois. `/pendencias` conta quantos lançamentos ainda usam.

**Categoria sem natureza é o problema mais grave:** o app assume `despesa` (`NATUREZA_PADRAO`),
então categoria nova inventada pelo Pluggy entra como despesa *silenciosamente*. Não dá para
impedir o Pluggy de criar categorias — a solução é alertar (`/pendencias` + faixa no DRE) para o
usuário decidir: definir natureza, renomear ou ocultar.

**Centro de custo só se aplica a categorias de despesa.** Vincular receita ou transferência a
centro de custo não faz sentido contábil — por isso `/pendencias` só cobra vínculo das categorias
com natureza `despesa`.

## 4.2 O que entra no DRE

- **Todo lançamento real do período**, independentemente de `POSTED` ou da data atual. Foi
  desfeita a tentativa de limitar a `POSTED` até hoje.
- **Não entram:** duplicados confirmados, registros `somente_conciliacao`, lançamentos
  `substituido_por` e naturezas neutras.
- Um lançamento rateado conta **uma vez**: as partes substituem o pai.
- Os cards de receitas/despesas/resultado usam exatamente essa mesma regra.

**A exclusão mora na view `cartao.lancamento_financeiro`**, ponto único por onde passam DRE,
relatórios, totais de Lançamentos e pendências — mexer lá vale para todos de uma vez. As telas de
Lançamentos e de conciliação leem `cartao.transacao` direto, por isso mostram também o que a view
exclui (ver §7.4).

Distinguir sempre **"recebidos"** (todos os registros do banco) de **"contabilizados"** (os que
participam do resultado).

## 4.3 Os três estados que tiram um lançamento do resultado

Cada um significa exatamente uma coisa. Não confundir:

- **`somente_conciliacao`** — registro de conciliação, não é evento de caixa. Hoje: a compra
  parcelada agregada. Visível, vinculável, fora do resultado.
- **`substituido_por`** (uuid → outra transação) — este lançamento é o **mesmo evento real** que
  outro; só o outro conta. Cobre o *pending → posted* e a *parcela mensal repetida*.
- **`duplicada`** — só o que sobrar: mesma cobrança duas vezes, sem estorno e sem par
  identificável.

**Cobrança dupla REAL da operadora não usa nenhum dos três.** Ela vem como cobrança + estorno,
ambos legítimos, que se anulam sozinhos no resultado — marcar qualquer um quebraria a conta
(esconderia a cobrança e deixaria o estorno negativo solto). Aparece só na conciliação do PDF, no
bloco "cobranças repetidas na própria fatura", que tem o "conferido" para registrar a revisão.

**Todo estado que tira lançamento do resultado precisa do caminho de volta desde o início.** Um
estado que só se aplica e nunca se retira vira dado perdido silencioso — ver §6.6, onde isso
custou R$ 1.167,38 sumidos do DRE.

Antes de gravar qualquer `substituido_por`, validar conta, proximidade de data, estabelecimento e
valor. Compras positivas exigem **pelo menos dois termos significativos** do estabelecimento em
comum. Pagamentos/créditos sem termos comuns exigem **mesmo dia e mesmo valor**.

## 4.4 Rateio

Quando um único débito pertence a mais de uma pessoa ou classificação, o pai continua sendo o
registro bancário e as partes aparecem recolhidas abaixo dele com `+`/`−`, descritas como
`<descrição original> — Parte N`.

- As partes somam **exatamente** o total, inclusive o sinal, em centavos exatos, e substituem o
  pai no DRE/relatórios. O registro bancário pai nunca é alterado ou duplicado.
- Valor, categoria, dimensões e observação são editados nas próprias linhas e salvos juntos: o
  `✓` de qualquer parte salva **o conjunto inteiro**.
- Quando a soma diverge, as partes ficam vermelho-claro, aparece `Rateado R$ X de R$ Y` e
  Salvar/OK ficam bloqueados.
- Pode-se editar rateio com o pai em OK sem apagar a assinatura, desde que o conjunto continue
  completo e válido; **desfazer o rateio inteiro exige retirar o OK antes**.
- Primeiro caso validado: DEB MONGERAL R$ 705,28 → R$ 505,46 Ronaldo + R$ 199,82 Andrea.

## 4.5 Regime de caixa para parcelamento

**Decisão do usuário (29/08/2026):** parcelamento vira despesa **mês a mês, conforme a fatura
cobra** — regime de caixa, não competência. A despesa acontece quando o dinheiro sai.

O problema que resolve: o Pluggy grava parte dos parcelamentos como UMA transação no valor cheio,
na data da compra. Contar assim **inflava o mês da compra e deixava os outros vazios** —
novembro/2025 sozinho tinha R$ 18.498,63 que não saíram naquele mês.

Como funciona (`_sincronizar_parcelas_de_agregado`):

1. Transação vinculada a **2+ linhas de fatura** é um agregado e recebe `somente_conciliacao`.
   Continua existindo, visível e vinculável — só sai do resultado.
2. Cada linha de fatura ligada a ele vira um lançamento próprio, **no valor e no mês em que a
   fatura cobrou**, herdando categoria e dimensões.
3. Idempotente por `fatura_linha.transacao_id_criado` — rodar de novo não duplica.
4. **Também desmarca**: quando o conjunto de vínculos muda e a transação deixa de ser agregado, a
   marca é retirada — com a trava de nunca desmarcar quem já gerou parcela (§6.6).

**Reconhecimento com uma única linha:** não é preciso esperar duas faturas. Quando
`valor do Pluggy = valor do PDF × total de parcelas` (tolerância de R$ 1,00), já vira agregado.
Exemplo validado: ANJOS DE QUINTAL, R$ 2.160,00 em 6×; o DRE contabiliza R$ 360,00/mês e
R$ 2.160,00 fica só como registro técnico, nunca somado de novo.

**Data da parcela gerada = `periodo_fim` da fatura que a cobrou.** Não usar a data impressa: numa
parcela ela é a da COMPRA ORIGINAL, fixa em toda reimpressão mensal.

Resultado medido: `nov/25: 43.904,64 → 25.406,01`, `ago/26: 55.395,59 → 60.862,16`.

**Consequência esperada, não é erro:** parcela que só será cobrada em fatura futura deixou de
contar antecipadamente. Parcelamento com parcela anterior a jul/2025 perde essa despesa, porque
não existe fatura importada cobrindo o período; importar faturas mais antigas recupera
automaticamente.

**"Revisar parcelamentos" não é consulta sem efeito** — redistribui o DRE entre meses. A tela roda
antes o mesmo código em modo `preview` (estritamente somente leitura), mostra quantas compras e
parcelas serão afetadas e exige confirmação explícita. É limitada ao cartão da fatura aberta.

## 4.6 Sincronização do Pluggy

**Pluggy é a origem bancária, não o dono da classificação.** O `UPSERT` do worker só escreve
`status`, `valor_brl`, `data_transacao` e os carimbos. Nunca pode sobrescrever categoria manual,
Responsável, Projeto, Portfólio, observação, OK, duplicidade, rateio, `somente_conciliacao` ou
`substituido_por`.

Verificado em 29/08/2026 (2.336 transações atualizadas, 0 novas): DRE, vínculos, duplicidades e
conciliação idênticos antes/depois.

**Atenção:** `data_transacao` PODE mudar se o Pluggy corrigir fuso/data, e isso move o lançamento
para dentro ou fora do ciclo de uma fatura. Os vínculos não se perdem (são por id), mas a lista de
órfãos pode oscilar — se acontecer, rodar o vínculo automático de novo.

**Correção de horário Unicred (migração 43):** o Pluggy entregava +3 horas nessa conta.
Referência confirmada na Visa: DELTA VIDEIRA, R$ 220,01, 13/08/2026 às **15:49**, que o sistema
mostrava como 18:49. A migração subtraiu 3h **só** de registros Pluggy da Unicred Conjunta,
guardando o estado em `cartao.horario_backup_v43`; o worker aplica a mesma normalização a novas
sincronizações. **Horários exatamente 00:00 são preservados** — representam data sem hora
confiável, e mover levaria ao dia anterior. **Não aplicar a Nubank ou conta corrente sem antes
validar um evento concreto no app da instituição.**

---

# 5. Hierarquia de fontes — CARTÃO DE CRÉDITO UNICRED

**Escopo:** só o cartão de crédito da Unicred (`b6243125-dca2-42b2-8c20-0825782c6d8d`). Nubank e
conta corrente ainda não foram avaliados e provavelmente precisam de regras próprias — conta
corrente não tem fatura, então o extrato do Pluggy vira a única fonte de "houve cobrança".

> **A fatura manda sobre o que foi cobrado. O Pluggy manda sobre o que aconteceu.
> O usuário manda sobre o que significa.**

| Campo | Manda | Por quê |
|---|---|---|
| A cobrança existiu? | **Fatura** | é a prova do que a operadora cobrou |
| Valor cobrado | **Fatura** | idem |
| Mês da parcela | **Fatura** (`periodo_fim`) | a data impressa é a da compra original |
| Data/hora da compra à vista | **Pluggy** | traz data e hora reais; a fatura só o dia |
| Estabelecimento (detalhe) | **Pluggy** | traz cidade/país; a fatura abrevia |
| Compra ainda não faturada | **Pluggy** | comprou após o fechamento: existe, sem fatura ainda |
| Categoria, dimensões, observação | **Usuário** > regra > Pluggy | |
| Conferida (OK) | **Só usuário** | |

Nenhuma fonte sobrescreve as outras fora do seu campo.

**A fatura é a fonte quando o Pluggy não sincroniza.** Se a operadora cobrou, o dinheiro saiu: a
linha vira lançamento. Vale nos dois sentidos — pedágio e IOF **aumentam** a despesa, a
bonificação da anuidade **reduz** (é o crédito que o Pluggy nunca mandava, deixando só a
cobrança).

**Criar lançamento pela fatura só é seguro com o outro lado zerado.** A rota
`POST /api/faturas/criar-cobrancas-sem-pluggy` recusa (409) enquanto houver lançamento do Pluggy
sem vínculo esperando decisão — sem órfão do outro lado, não há como duplicar. `preview: true`
levanta sem gravar; `ano` limita o alcance.

Tipos com categoria fixa, conferidos um a um contra o PDF: `Unicred TAG` → Pedágio,
`Anuidade - bonificação` → Tarifas do Cartão, `IOF compra internacional` → IOF, `ESTORNO` → sem
categoria fixa. O resto nasce sem categoria e passa por `aplicar_regras()`.

Resultado da aplicação (29/08/2026): 1.135 lançamentos criados, R$ 159.464,93. O salto de 2025 é
real — o Pluggy só passou a sincronizar esse cartão em **22/07/2025**, então o ano estava
subestimado em quase metade.

---

# 6. Conciliação de fatura em PDF

Tela `/relatorios/conciliar-fatura`. Confere a fatura oficial da Unicred contra o que o Pluggy
sincronizou. **A fatura é a autoridade**: ela é a prova do que a operadora cobrou.

## 6.1 O erro de arquitetura já corrigido

Até 29/08/2026 a conciliação era **sem memória**: recalculava tudo por heurística a cada abertura
e não tinha onde registrar decisão humana. O resultado mudava sozinho entre uma visita e outra, a
mesma cobrança aparecia como sobra em dois meses seguidos, e parcelamento era impossível de
resolver.

`cartao.fatura_vinculo` (migração 23) é uma relação **N:N** entre `fatura_linha` e `transacao`. O
N:N não é luxo: um parcelamento que o Pluggy gravou como UMA transação corresponde a **uma linha
por mês, em faturas diferentes**. Com "usado/não usado" dentro de uma fatura isso era
irrepresentável, e um mês roubava a transação do outro.

Regras:

- Transação já vinculada não é reivindicada pelo casamento 1:1 nem por avulsa.
- **Exceção deliberada:** o fallback de parcelamento agregado PODE reusar transação já vinculada.
- Vínculo `origem='manual'` nunca é sobrescrito pelo automático.
- O casamento automático só roda em **POST**, nunca em GET. GET que grava faria a tela mudar
  sozinha e furaria a checagem de Origin/Referer.
- **Ao rodar o vínculo automático em lote, ir da fatura mais antiga para a mais nova** — o
  bloqueio depende dos vínculos já existentes.
- Refazer vínculos: `POST /api/fatura/<id>/vincular-automatico` com `{"refazer": true}`.

## 6.2 Datas do ciclo — três armadilhas resolvidas

1. **A Unicred não imprime a data de fechamento em lugar nenhum do PDF.** Conferido nas 14
   faturas: só existem `REF.:`, `VENCIMENTO:` e o resumo de saldo. Não procurar de novo.
2. **O intervalo vencimento−fechamento NÃO é fixo** (varia de 9 a 14 dias). Não cabe fórmula. O
   usuário conferiu as datas reais no app do Unicred (tela "Melhor dia para compra") e elas estão
   em `FECHAMENTOS_CONHECIDOS`, em `fatura_unicred.py`. **Preencher ali conforme ele confirmar
   novos meses.** Fora da tabela, cai na heurística (última compra impressa) com trava: nunca
   fecha no dia do vencimento ou depois.
3. **`periodo_inicio` é calculado na LEITURA, nunca congelado no import**
   (`_ciclo_inicio_encadeado`). Congelar tornava o resultado dependente da ORDEM de envio dos
   PDFs — aconteceu com as 6 faturas de 2025. **O palpite de 35 dias só vale quando não existe
   fatura anterior daquela conta.**

## 6.3 O que cada número da tela significa

- **Total impresso no PDF** — o SALDO TOTAL da fatura.
- **Soma das linhas lidas** — soma das linhas extraídas, sem "Pagamento Recebido". É **fatura
  contra fatura**: prova que a leitura do PDF está correta. Tem que ser igual ao total impresso.
- **Já vinculado ao Pluggy** / **Falta vincular** — aí sim é contra o Pluggy. "Falta vincular"
  **não é** `Total − Soma`; é o quanto da fatura ainda não foi conciliado.
- **Despesas no DRE** é um subconjunto explicado por natureza. Exibir também **Fora do DRE** em
  vez de forçar os dois números a serem iguais.

"Pagamento Recebido" é a fatura ANTERIOR sendo quitada; o próprio SALDO TOTAL da Unicred não a
inclui. Fica fora das duas somas — se entrar em um lado só, a tela acusa diferença de dezenas de
milhares sem erro nenhum (aconteceu: R$ 16.647,99 falsos). **Nunca vira lançamento e nunca trava
o "fecha 100%"** — cobrar vínculo dela travava as 7 faturas do início de 2025.

**As duas telas medem coisas diferentes.** A conciliação pergunta *"a linha tem vínculo?"*
(`tem_vinculo = bool(vinculos) or bool(transacao_id_criado)`). A detalhada pergunta *"sobrou um
lançamento que conta?"*. Uma linha pode ter vínculo e mesmo assim não ter lançamento elegível —
foi o que revelou o bug de §6.6. Ao investigar divergência entre elas, lembrar disso.

## 6.4 Consolidação de data: por que não existe botão em massa

Existiu por algumas horas em 29/08/2026 e **corrompeu 22 datas reais** de agosto/2026, jogando-as
para a data da compra original (algumas em novembro/2025). Causa: numa linha de parcela a data
impressa é a da **COMPRA ORIGINAL**, fixa em toda reimpressão mensal. Foi revertido no mesmo dia
pelo log de auditoria.

Decisão do usuário: consolidação de data, se voltar, é **por lançamento, um a um**, dentro do
painel de vínculo, junto com observação — nunca em lote por fatura. **Não reintroduzir sem uma
fonte de data mensal confiável, que a fatura não fornece.**

## 6.5 Armadilhas do matcher (todas custaram caro — não repetir)

Cada uma tem teste em `tests/test_fatura_vinculo.py`. Antes de mexer em `_conciliar_linhas`,
`_classificar_orfaos` ou `_vincular_automatico`, leia esta lista.

1. **Chave de agrupamento sem o valor.** Agrupar parcelamento por `titular + lojista + nº de
   parcelas` **colide**: MECANICA HOCHIOVE tem 2× R$135,00 e 2× R$233,50 na MESMA fatura. Colidindo,
   o valor da parcela vira a média e nenhum agregado é encontrado. **O valor entra sempre na
   chave.** Esse erro apareceu duas vezes no mesmo dia — no código e na análise feita por fora.
2. **Um cursor por conexão — consumir antes da próxima consulta.** Inserir um `cur.execute` entre
   o `execute` e o `fetchall` de outra consulta faz a primeira sumir **em silêncio**: a tela
   mostrou "nenhuma cobrança em dobro" com tudo zerado, HTTP 200, sem erro.
3. **Lançamento nascido da fatura não é candidato.** A parcela gerada tem o valor exato e cai
   dentro do ciclo, então disputa a linha com o agregado e vira órfã. Ela não é um lançamento do
   Pluggy — ela **é** a fatura. Filtrada por `fatura_linha.transacao_id_criado`.
4. **Vínculo `origem='fatura'` não bloqueia o vínculo com o agregado.** São os dois lados: a
   parcela gerada é o evento de caixa, o agregado é o registro da compra.
5. **"Refazer vínculos" só apaga `origem='automatico'`.** Apagar o vínculo `fatura` deixava a
   parcela gerada órfã para sempre (a geração é idempotente e não recria). Deu 340 falsos
   positivos numa rodada.
6. **Janela de candidatos vai além do fim do ciclo.** O Pluggy às vezes data a compra 1–2 dias
   depois do que a fatura imprime (D MORI: fatura 11/02, Pluggy 12/02). Busca 3 dias além.
7. **Comparar descrição inteira nunca casa o par do mesmo evento.** O Pluggy grava o mesmo evento
   com prefixos diferentes. `_tokens_significativos()` remove o prefixo genérico e exige 2+ tokens
   em comum, mesmo valor e ±1 dia.
8. **`"Parcelado Lojista"` ≠ `"Parcela Lojista"`.** O primeiro é o parcelamento inteiro
   (agregado); o segundo é a cobrança de UMA parcela. Só a forma mensal pode entrar em "evidência
   inequívoca" — um agregado sem vínculo costuma ser parcelamento novo cujas parcelas ainda vão
   aparecer, e marcá-lo apagaria compra real.
9. **Reenviar o PDF apaga os vínculos da fatura.** As linhas são recriadas com ids novos e o
   `ON DELETE CASCADE` leva `fatura_vinculo` junto. `_revincular_lancamentos_da_fatura()` roda no
   import e na sincronização de parcelas.
10. **Cada tela tem que aplicar os MESMOS filtros de "já resolvido".** A lista de órfãos não
    excluía `substituido_por` nem `somente_conciliacao`: 57 falsos pendentes em 08/2026 enquanto a
    outra tela dizia "nada pendente". **Ao criar um estado novo, procurar TODAS as consultas que
    listam pendência.**
11. **Eco de parcelamento NOVO precisa de regra própria.** Enquanto o agregado atende UMA linha
    só, ele não é reconhecido como agregado e o eco escapa das outras regras. A regra: existe
    linha de fatura do mesmo estabelecimento **já vinculada**, e o órfão vale a parcela dela ou o
    parcelamento inteiro, dentro de 5 dias. A comparação usa a `descricao_base` da **linha**.
12. **Valor negativo não casa por descrição.** O Pluggy chama o mesmo pagamento de "Pagamento
    recebido" e de "Pag de Fatura Via Deb Aut" — zero palavras em comum. Para negativo o par é
    **valor idêntico no MESMO dia**, com a outra gravação já vinculada.
13. **Lançamento criado pela fatura: parcela usa o MÊS COBRADO.** Datar pela data impressa mandou
    R$ 11.027,44 para 2025. Parcela usa `periodo_fim`; compra avulsa usa a data impressa.
14. **Commit também quando só houve correção.** O `UPDATE` que conserta datas só era commitado
    quando havia linha nova. Sem linha nova, a rota respondia "11 datas corrigidas" e **nada era
    gravado**, em silêncio, com sucesso na resposta.
15. **Estorno só anula quando os dois lados ainda contam.** Se o negativo já foi excluído, o par
    deixou de se anular e a cobrança positiva ficou sozinha — ela tem que seguir para as outras
    regras, não ser dada como resolvida.
16. **Cobrança estornada não se marca.** Se existe um negativo de mesmo valor no mesmo dia, os
    dois são legítimos e se anulam sozinhos.
17. **Coincidência de valor total nunca é prova de família.** A parcela 1/5 de MERCADOLIVRE foi
    ligada a YELLOW BOX PIZZARIA porque ambas davam R$ 164,00. O matcher agora exige, além do
    valor, ao menos um token significativo do estabelecimento em comum.

## 6.6 A marca de agregado sem caminho de volta (migração 44)

**O defeito.** `somente_conciliacao` só era POSTA, nunca retirada. Quando o conjunto de vínculos
mudava depois (refazer vínculos, reenvio de PDF, desvincular na mão), a transação deixava de ser
agregado e **continuava fora do resultado para sempre**, sem nenhuma parcela ocupando o lugar
dela.

**O estrago.** Cinco compras **à vista** sumiram do DRE — R$ 1.167,38: SUPERVIZA R$ 584,83
(jan/2026), DELTA VIDEIRA R$ 83,30 (dez/2025), SUPERVIZA R$ 268,75 e MP *PRODUTOS R$ 105,00
(out/2025), POSTO CANOAS R$ 125,50 (set/2025).

**Como se manifestava.** A linha do PDF *tinha* vínculo, com um lançamento correto do Pluggy. Mas
esse único vínculo era inelegível, então a detalhada não achava lançamento principal e escrevia
**"Validar: falta vínculo"** — mensagem enganosa: não faltava vínculo, faltava um lançamento
**contabilizável**.

**A correção.** A sincronização agora desmarca, com a trava: **nunca desmarcar quem já teve
parcela gerada** a partir das suas linhas — se as parcelas existem, são elas que contam. O retorno
antecipado "sem agregado nenhum" vale só para a prévia, porque sem agregado ainda pode haver marca
obsoleta a retirar. Migração 44 corrigiu o dado, backup em `cartao.agregado_backup_v44`,
**5 lançamentos devolvidos — exatamente os 5 previstos**.

## 6.7 Duplicidades — `/relatorios/duplicidades-fatura`

Classifica no servidor (nunca por heurística sobre HTML) em baldes: *parcela cobrada de novo*,
*eco pending → posted*, *cobrança estornada* (sem ação), *precisa de revisão* e *aguardando a
próxima fatura* (compra perto do fechamento — não é duplicidade, não tem ação).

**Os dois mecanismos identificados:**

- **Eco da compra (pending → posted):** transação individual de UMA parcela, **2 dias antes** do
  agregado. O Pluggy registra a autorização e depois registra de novo consolidado, sem remover a
  primeira. Sinal claro: pares com 1 centavo de diferença (arredondamento da parcela).
- **Mensais tardias:** cobranças **sempre por volta do dia 12**, até 283 dias depois do agregado.
  **Nenhuma existe antes de junho/2026** — o Pluggy mudou o comportamento nessa conta e passou a
  emitir as parcelas mensais **além** do agregado.

Caso exemplar: OTICA CALLIARI, 10× R$316 = R$3.160, comprado em 02/11/2025. A fatura cobrou as 10
parcelas certinho; o Pluggy mandou o agregado **mais** R$316 em 12/06, 12/07 e 12/08.

Aplicado em 29/08/2026: 74 parcelas repetidas + 35 ecos vinculados via `substituido_por`. Despesa
de 2026 caiu de R$ 493.358,27 para **R$ 474.442,56** (−R$ 18.915,71). 2025 não mudou.

**Eco técnico não é divergência:** dois registros Pluggy com o mesmo instante, cartão e valor,
para uma única cobrança oficial no PDF — um contabilizado, o outro preservado para auditoria. A
equivalência só elimina o alerta quando data/hora, valor e final do cartão coincidem.


---

# 7. Telas e comportamento de interface

## 7.1 Lançamentos: Resumida e Detalhada

São **duas visualizações do mesmo dado**, escolhidas explicitamente pelo usuário. A Resumida
privilegia classificação rápida; a Detalhada (`/lancamentos/fatura`) privilegia fatura,
procedência, registros agregados e auditoria. **Ambas leem e gravam os mesmos campos** — não
duplicar categoria, Responsável, Projeto, Portfólio, observação ou OK em tabela própria.

- A detalhada exige uma única origem de cartão e abre a fatura mais recente. Sem `account_id` na
  URL, escolhe **a conta de crédito que tem fatura importada** — antes caía no primeiro cartão da
  lista (Nubank, sem fatura) e a tela abria só com o erro, sem seletor, sem saída.
- O botão Resumida volta ao intervalo oficial daquela fatura, preservando a origem.
- Cada linha do PDF pode ter vários registros técnicos agregados, todos preservados. **Apenas o
  lançamento financeiro principal é editável e contabilizado.** Clicar em qualquer área não
  interativa da linha abre/recolhe os detalhes.
- Ordem visual fixa dentro do grupo: lançamento principal, registros agregados um abaixo do
  outro, detalhes técnicos de cada um e, **por último**, o editor único de classificação. Nunca
  intercalar a classificação entre os registros — impede comparar candidatos em divergências.
- Cada registro mostra a fonte: **`F`** = criado pela fatura em PDF, **`P`** = trazido pelo
  Pluggy, com tooltip CSS imediato (não o `title` nativo).
- **Titular/cartão** identifica quem realizou a compra e é separado da dimensão financeira
  **Responsável**.
- Cabeçalhos ordenáveis (Data, Descrição, Titular/Cartão, Parcela, Valor PDF, Classificação, OK);
  ordenar é local, não recarrega e não desmonta o grupo.
- Pesquisa local filtra as linhas já carregadas; linha principal e agregados aparecem ou somem
  juntos. `Esc` limpa.
- No filtro **Pendentes de OK**, ao marcar, a linha sai da fila **somente depois da confirmação do
  servidor**, preservando filtros e rolagem. Nos demais filtros, marcar OK mantém a linha visível.
- Navegação de mês, filtros e troca de tela criam **histórico real** — o botão Voltar do navegador
  retorna ao estado anterior, com a rolagem preservada.

## 7.2 Classificação obrigatória e famílias de parcelas

**Categoria, Responsável, Projeto e Portfólio são obrigatórios para liberar o OK.** Observação é
pessoal e opcional. A mensagem `Faltam: ...` atualiza durante o preenchimento, sem recarregar.

Alterações de classificação e observação **salvam automaticamente** (selects na mudança;
observação com espera curta e ao sair do campo), atualizando cards e estado em segundo plano sem
perder linha aberta, rolagem ou foco. Projeto aplica seu Portfólio padrão antes do único
salvamento. Projeto e Portfólio permitem cadastro rápido sem sair da tela.

**Em famílias de parcelas confirmadas por vínculos persistidos**, Categoria, Responsável, Projeto,
Portfólio e Observação são compartilhados entre todas as parcelas — editar uma aplica o conjunto
completo às demais. **O OK nunca é propagado**: assina a conferência de cada cobrança mensal.

**A família é determinada exclusivamente pelos vínculos persistidos da fatura com o agregado.**
Nunca inferir família por descrição, data ou valor semelhante. Só preencher automaticamente quando
houver um único valor não vazio e inequívoco; em conflito, deixar vazio para revisão humana. Nunca
sobrescrever campo já preenchido.

## 7.3 Observação pessoal × informação interna

`transacao.observacao` **pertence exclusivamente ao usuário**. Nenhuma rotina de importação,
conciliação, parcelamento ou duplicidade pode escrever mensagem técnica nesse campo.

Mensagens do sistema usam `transacao.observacao_sistema`, ocultas por padrão, visíveis só em
"Informações internas do sistema", não editáveis e nunca na coluna Observação.

A migração 32 moveu **apenas textos completos reconhecidos**. Não usar busca aproximada nem
`LIKE '%fatura%'`: apagaria nota pessoal. Se uma anotação não for correspondência exata dos
modelos técnicos documentados, ela fica em `observacao`, mesmo mencionando fatura ou Pluggy.

## 7.4 Lançamento "fora do resultado"

A tela de Lançamentos lê `cartao.transacao` **direto**, então mostra também o que a view financeira
exclui. Sem marca visual, dois lançamentos de mesmo valor e data aparecem lado a lado sem pista de
que só um conta — e quem revisa conclui que há duplicidade no DRE. Aconteceu com SESI FARMACIA
12/08/2026, ambos R$ 41,23.

Hoje a linha fica esmaecida, com a descrição riscada e um selo **"fora do resultado"** cujo tooltip
diz o motivo (classe `fora-resultado` + `.selo-fora`). **Ao criar um estado novo que tire
lançamento do resultado, marcar aqui também** — senão a tela mente por omissão.

Um registro `substituido_por` só é recolhido sob o lançamento que conta quando o vínculo explícito
existir e ambos estiverem no filtro atual; o mesmo para `somente_conciliacao`, e apenas quando
houver um único destino visível e inequívoco.

### Defasagem de um mês no parcelamento recém-comprado (esperado)

Enquanto o agregado atende **uma única** linha de fatura e o valor não bate com
`parcela × total`, ele não vira `somente_conciliacao` e **o valor cheio conta no mês da compra**.
Corrige-se sozinho quando a fatura seguinte traz a Parc.2/N.

## 7.5 Fatura em andamento

Para o ciclo ainda sem PDF, a Detalhada abre `Fatura <mês> · Em andamento`. O período começa no
dia seguinte ao fim da última fatura oficial e termina hoje. Mostra lançamentos do Pluggy, total
provisório, DRE provisório, classificação e pendências, **sempre com aviso de que o PDF não
existe** e valores identificados como provisórios.

**Um novo OK de cartão de crédito exige vínculo persistido com uma linha do PDF.** Na fatura em
andamento o OK fica desabilitado na interface **e protegido no servidor** — a API recusa tentativas
vindas de outra tela ou chamada direta. Classificação pode ser preparada antes; a conferência só
depois da conciliação. OKs históricos não são apagados.

Links usam **dois parâmetros reais**: `andamento=1&account_id=<conta>`. Nunca montar `&amp;` dentro
de expressão Jinja autoescapada — produz `&amp;` literal no endereço e perde a conta.

## 7.6 Semântica visual

- **Verde** apenas para fechado/completo; **amarelo** para revisão humana pendente; **vermelho**
  para divergência real; **roxo** para investimento ou natureza fora do DRE; **neutro** para
  totais informativos. Despesa normal não deve parecer erro só por ser despesa.
- **Cor nunca é a única explicação de estado:** pontos no início da linha, tooltip com todas as
  situações, legenda e filtros equivalentes.
- Linha com OK usa cinza-claro. Pendente no banco **não** colore o fundo (bloqueia o OK e aparece
  na legenda/dica).
- Diferença de até **R$ 1,00** pode ser arredondamento; acima é divergência vermelha. Mesmo abaixo
  do limite, preservar os valores originais e nunca esconder diferença de vínculo ou quantidade.
- Compra parcelada agregada **não é divergência**: quando o total equivale a
  `valor da parcela × número de parcelas` (±R$ 1,00), o card não fica vermelho.
- A coluna de classificação é silenciosa quando a despesa está completa. Faltando dado, lista
  `Faltam: ...`; natureza neutra explica que fica fora do DRE; rateio misto informa isso.

## 7.7 Comboboxes pesquisáveis

Categoria, Responsável, Projeto e Portfólio usam o componente compartilhado `static/combobox.js`
nas duas visualizações. O `<select>` original **continua como fonte de verdade**, preservando APIs,
permissões, validações e salvamento automático.

- Filtra por qualquer parte do texto, ignorando acento e caixa. Setas percorrem, Enter confirma,
  Tab confirma a opção destacada e segue, Shift+Tab volta, Escape cancela. **Nunca criar ou
  escolher silenciosamente uma opção que não esteja destacada.**
- Visual final: 26px, campo transparente, borda normalmente invisível, sombra mínima; hover/foco
  em cinza neutro (`#f2f2f0`) com contorno translúcido e escala 1,012. Obrigatório incompleto
  mantém sinalização vermelha discreta. **Não exibir o lembrete textual `Enter`.**
- O campo nativo fica oculto **também da árvore de acessibilidade** — leitores de tela encontram
  só o combobox, sem controles duplicados.
- Aplica-se a qualquer `select` com `data-pdm-combobox` ou `data-lazy-options`, inclusive inseridos
  dinamicamente. **Nunca usar quantidade de opções como critério automático** — quebrou o
  alinhamento dos filtros Fatura e Status. Seletores de navegação (cartão, fatura, status, ano,
  tipo) continuam nativos, protegidos com `data-pdm-native`.
- Campos pesquisáveis não podem causar rolagem horizontal. Em `/pendencias`, scripts de ações em
  lote precisam verificar se `formLote` existe — a seção é condicional.

## 7.8 Colunas ajustáveis

Utilitário compartilhado, ligado com
`<table class="compacta ajustavel" data-tabela="chave-unica">`. Redimensionar é **estilo
planilha**: a vizinha compensa, a soma nunca muda e a tabela não estoura a tela. Preferências no
`localStorage` (`pedemeia_tabela_<chave>`).

- `data-sem-ordenar` / `data-sem-reordenar` desligam recursos. Usados no **Centro de Custos**, que
  é hierárquico: ordenar embaralharia a hierarquia.
- Ordenação numérica entende `R$ 1,234.56` e `R$ 1.234,56` — o separador decimal é o último `.` ou
  `,` do texto.
- **Quando um filtro recarrega a tabela por AJAX, chamar `ativarTabelaAjustavel()` de novo** — o
  elemento antigo vai embora levando os listeners.
- CSS de célula não pode vazar para o `<th>`: `.cel-origem { display:flex }` tirava o cabeçalho do
  grid e a coluna seguinte desenhava por cima. Por isso a regra é `td.cel-origem`, com
  `display:table-cell !important` defensivo nos `th[data-col]`.

## 7.9 Identidade visual

Nome **Pé de Meia**; logo oficial (meia de tricô com dinheiro) em fundo claro sólido no topbar e no
login, embutida em base64 no Python. Bancos identificados por "selo" colorido em CSS puro (cor da
marca + sigla de 2 letras) — o Pluggy não fornece logo utilizável. Tooltips customizados de 120ms,
mais rápidos que o `title` nativo.

**Favicon:** fica em `static/favicon.png`. Se sumir após deploy, verificar a referência versionada
em `templates/base.html` e renovar o parâmetro de cache — **não recriar a imagem**.

---

# 8. Classificação automática

## 8.1 Regras

- Filtram por trecho da descrição e, opcionalmente, por valor absoluto (`<`, `<=`, `>`, `>=`, `=`).
  A prévia mostra quais lançamentos pendentes receberão a regra.
- **Nunca se aplicam a lançamento conferido nem a categoria escolhida manualmente.**
- `regra_classificacao.account_id` limita a regra à origem; regra sem origem é geral. **Regras que
  poderiam confundir conta corrente com cartão devem ter origem vinculada.**
- Regras são globais e gravadas sobre a transação: uma regra criada em qualquer visualização vale
  para ambas. O botão `+` só abre o cadastro já preenchido.
- **Reaplicar** libera todos os pendentes ligados à regra, em qualquer mês; conferidos são
  preservados.

## 8.2 Consenso dos lançamentos com OK (`_consenso_por_lojista`)

Aprende **só com lançamento conferido** — o OK é a única fonte sobre o que cada gasto significa.
Exige **unanimidade e no mínimo duas evidências**.

**Decide campo a campo, não pelo conjunto.** Um lojista pode ter categoria unânime e nenhum projeto
consensual — é o posto que abastece o Jeep e o Tracker. Exigir os quatro campos juntos jogava a
categoria fora junto com o projeto.

**Canonização do lojista:** a mesma loja chega com e sem sufixo de cidade/país (`DELTA VIDEIRA` e
`DELTA VIDEIRA VIDEIRA BR`). Usa a menor chave que seja prefixo da outra, **cortando sempre em
limite de palavra** — sem isso `ESTACAO` engoliria `HIPER CENTER ESTACAO`. Sem canonizar, o alcance
cai pela metade.

**Nunca propaga projeto que começa com "Viagem "** — viagem é evento datado: o mesmo hotel ou Uber
reaparece em outra viagem e o projeto antigo fica errado.

Aplica **apenas em campo vazio**, e vazio inclui `transacao_dimensao` com `valor_id` NULL. O
`ON CONFLICT` faz `DO UPDATE ... WHERE valor_id IS NULL`, nunca sobrescrevendo valor preenchido.

**Segundo eixo: consenso por categoria (`_consenso_por_categoria`).** Resolve o caso "só a
categoria está preenchida": quem tem OK dentro de uma categoria decide o Responsável/Projeto/
Portfólio unânime **dela**. Mesmas travas do eixo do lojista, com **mínimo de 3 evidências** — a
categoria é um agrupamento largo, e duas coincidências nela dizem menos que duas no mesmo lojista.
Nunca decide categoria, que é a própria chave. O eixo do lojista é mais específico e decide
primeiro; o da categoria só entra onde ele não tinha nada a dizer.

Recusados nesse eixo, conferidos um a um na prévia: **`Leisure`** (tinha Portfólio "Viagens"
unânime, mas lazer local não é viagem — o show do Iron Maiden é "Eventos") e **`Insurance`**
(tinha "Veículos", e seguro também pode ser de vida ou residencial).

**Completar pela conferência é ação repetível, não migração.** O consenso é apurado ANTES das
próprias gravações e cresce a cada OK novo assinado — cada passada muda o resultado da seguinte.
Por isso `aplicar_consenso_classificacao()` mora no módulo e tem botão em `/pendencias`, com
prévia obrigatória (`preview=True` faz `rollback`, não deixa rastro) antes de liberar o Aplicar.
Resultado da 48: 184 dimensões e 16 categorias em 112 lançamentos, **`tocados_com_ok: 0`**.

**A prévia é somente leitura:** `GET /api/classificacao/consenso-preview` mostra, sem gravar,
quanto cada eixo preencheria por campo e o consenso achado em cada categoria. Como não há
staging, é o único jeito de olhar o dado real antes de uma alteração em lote — usar sempre antes
de escrever a próxima migração de classificação.

**Consenso unânime não é prova de acerto** — pode ser erro repetido. Padrões reprovados na revisão
humana de 01/09/2026 ficam na lista de recusados: `POUSADA FOGO*RESE` (pousada marcada como
Combustível) e `ESTACAO` (ambíguo, recusado desde a migração 42).

## 8.3 Estornos e cancelamentos

- **Anuidade, tarifa e bonificação não são IOF.** `ANUIDADE` e `Est.Tarifa manutencao de conta` na
  Unicred Conjunta usam **Tarifas do Cartão / Família / Serviços Financeiros / Vida Familiar**.
- A cobrança entra como despesa e o crédito/bonificação reduz a mesma despesa. **Manter as duas
  linhas** para auditoria; elas se anulam no DRE quando os valores forem iguais.
- **IOF só por regra quando a descrição mencionar IOF explicitamente.** Nunca inferir pelo sinal
  negativo nem por "tarifa", "anuidade", "bonificação" ou "estorno".
- Para outros estornos, herdar a classificação do original **apenas quando houver exatamente um
  candidato**: mesma origem, mesmo cartão quando informado, valor exatamente oposto, no máximo 30
  dias. Persistir em `estorno_origem_id`. Zero ou mais de um candidato: não adivinhar.
- A herança copia Categoria, Responsável, Projeto e Portfólio; **nunca copia Observação nem marca
  OK.**

## 8.4 Classificações decididas pelo usuário

Decisões tomadas em 01/09/2026, sobre lojistas cujos OK divergiam entre si. **Aqui a fonte é a
decisão do usuário, que vale inclusive contra a maioria dos OK já gravados** — por isso a migração
46 corrigiu também lançamento conferido, sem tocar no OK.

| Lojista | Categoria | Responsável | Projeto | Observação |
|---|---|---|---|---|
| CATIVA | Viagem | — | — | |
| LETICIAKAYSER | Beleza | Andrea | Compras Pessoais | unha, despesa pessoal |
| MP *REGIBARBERSHOP | Beleza | Ronaldo | Compras Pessoais | barbearia, despesa pessoal |
| LISCIA | Beleza | **não definir** | — | depilação: pode ser Ronaldo, Amanda ou Andrea |
| MP *PRODUTOS | Restaurantes | Ronaldo | Refeições fora | `futebol quarta` |
| PANIFICADORA E CONFEIT | Restaurantes | — | Refeições fora | |
| APPLE.COM/BILL | Serviços Digitais | — | — | |
| TOTAL SPORTES | Vestuário | — | — | |

**GuilhermeDaSilva:** abaixo de R$ 120,00 é **Água**, acima é **Gás**; sempre Família / Casa /
Vida Familiar. **R$ 120,00 exatos seguem sem decisão** e não são tocados.

Decisões anteriores que continuam valendo: farmácia cotidiana é Saúde (projetos explícitos de
viagem ou cirurgia são preservados); anuidades e bonificações Unicred são Tarifas do Cartão /
Família / Serviços Financeiros / Vida Familiar; EVENTIM e SAN JUAN do show vão para Iron Maiden
2026 / Eventos; `Reformas da casa` sempre aponta para Imóveis.

**Contextos que exigem decisão antes de virar regra** — não automatizar por descrição: Apple,
Google, Mercado Livre (marketplace), combustível, mecânica, estorno e IOF.


---

# 9. Segurança, permissões e auditoria

## 9.1 Permissões

Perfis: `admin` (tudo), `operador` (lançamentos + relatórios + sincronizar, sem cadastros/
usuários), `leitura` (só ver). As 8 permissões granulares: `lancamentos_ver`,
`lancamentos_editar`, `lancamentos_conferir`, `lancamentos_manual`, `relatorios`, `cadastros`,
`sincronizar`, `usuarios`. Decorator `@requer(permissao)` protege a rota; `pode(permissao)`
controla o que aparece na interface.

Usuários: `ronaldo` (admin), `andrea` (admin), `amanda` (operador).

## 9.2 Sessão e proteções

- Sessão de 24 horas, renovada com uso. Cookie `Secure`, `HttpOnly`, `SameSite=Lax`.
- Login limita tentativas por usuário e IP em janela de 15 minutos, com mensagem genérica.
- Senhas em PBKDF2-HMAC-SHA256, 200k iterações. **Mínimo de 6 caracteres, por decisão do
  usuário** — não alterar.
- Requisições mutáveis exigem Origin/Referer do próprio site. Respostas incluem HSTS, CSP,
  proteção contra iframe/MIME sniffing e `Cache-Control: no-store`.
- `/sync` do worker exige `X-Sync-Secret` (env `SYNC_SECRET`, igual nos dois serviços).
- **Credenciais administrativas de emergência ficam apenas nas variáveis do Coolify.** Não alterar
  esses acessos sem pedido explícito.
- XSS: corrigido em todo o app e revalidado com payload real. O caso mais grave era `json.dumps()`
  dentro de `<script>` — não escapa `</`, então uma descrição contendo `</script>` executava JS.

## 9.3 Auditoria e backup

Logs ficam dentro de Relatórios e registram acesso, alteração com antes/depois, falha, sync,
regras e migrações. Senhas e tokens são sanitizados. Rateios e migrações também geram auditoria.

**Não enfraquecer a auditoria.** Foi só porque cada alteração gravava antes/depois que deu para
reverter exatamente as 22 datas corrompidas em 29/08/2026.

E-mail operacional: `ronaldo@brdrive.net`. Backup no mesmo servidor foi aceito como primeira
camada; teste de restauração foi **adiado explicitamente pelo usuário** — não executar sem nova
autorização. Backup fora do servidor continua desejável.

**Antes de qualquer alteração de dados em lote, criar ponto de reversão** no mesmo Postgres.
**Nunca apagar lançamento do Pluggy** — preservar a origem para auditoria e marcar
duplicidade/substituição só com decisão explícita ou prova segura.

---

# 10. Testes e método de trabalho

## 10.1 Suíte

**270 aprovados e 6 ignorados** (01/09/2026). Cobre a regra de ouro do DRE, helpers puros,
segurança/XSS, permissões, estrutura de rotas/templates, concorrência, auditoria, regras
automáticas, rateio, conciliação de fatura, consenso de classificação e fluxos com PostgreSQL
temporário. Os 6 ignorados dependem de serviços indisponíveis em toda execução — conferir o motivo
do `skip`, não tratar como falha.

```bash
pytest tests/ -v
```

- `tests/test_fatura_vinculo.py` reproduz, com cursor dublado (roda sem Postgres), os casos reais
  que quebraram a conciliação. **Foi escrevendo esses testes que dois bugs latentes apareceram.**
- `tests/test_consenso_classificacao.py` roda a lógica de consenso de verdade, com dados
  sintéticos. Existe porque o bug do `valor_id` NULL passou por toda a suíte estrutural sem ser
  notado: o código "parecia certo" e só o dado real revelava.
- Validação do parser contra dado real (refazer se mexer em `fatura_unicred.py`): parsear os PDFs e
  conferir que a soma das linhas, sem "Pagamento Recebido", bate com o total impresso. Em
  29/08/2026 bateu **centavo a centavo nas 14 faturas**.

Teste de integração não substitui validação logada em produção: configuração, dados reais, rede do
Coolify e comportamento do Pluggy são diferentes.

## 10.2 Ciclo de trabalho que deu certo

1. **Ler o código antes de mudar** — várias vezes a causa raiz era diferente da aparente.
2. `python3 -m py_compile` + `pytest tests/ -q` antes de commitar.
3. Commit + push, **e então validar em produção**: status no Coolify, `/health`, logs (procurar
   traceback e `Aviso: falha ao rodar migracao`) e a tela real pelo navegador.
4. **Testar de verdade, não só ler o código.** O teste com payload real de XSS encontrou 3 pontos
   que o grep não pegou. Limpar o `localStorage` **antes** de recarregar, senão o estado antigo em
   memória falseia o resultado.
5. Limpar dados de teste depois.

## 10.3 Como cortar código sem quebrar nada

Duas vezes um corte por busca de texto apagou código vizinho — uma levou a rota `/dre` inteira,
outra derrubou `/relatorios` em produção. O que funciona:

1. Delimitar a função pelo **AST** (`node.end_lineno`), nunca por "até o próximo `@app.route`" —
   auxiliares sem decorator moram entre as rotas e são engolidos.
2. Depois do corte, **comparar as definições de topo antes/depois**. Contar rotas não basta,
   justamente porque o que se perde costuma ser função sem decorator.
3. **302 não é prova de que a tela funciona** — é o redirect de login. **Depois do deploy, abrir
   todas as telas**: variável usada mas atribuída só dentro de um `if` passa por `py_compile`,
   passa pelos testes (que não executam view) e só aparece quando alguém abre a tela.
4. **`replace` em código só com `assert` de que casou.** Um `replace` silencioso que não casa
   deixa o código velho no lugar e a edição parece ter funcionado.
5. Em tela com número, **anotar os valores em produção antes do deploy** e comparar depois.

## 10.4 Lições de incidentes reais

1. **Testar hipótese localmente, nunca em produção.** Os dois erros de 29/08/2026 vieram do mesmo
   vício: deployar para descobrir se a teoria estava certa. O `consolidar-datas` corrompeu 22 datas
   reais.
2. **Escrever o teste do caso real ANTES de mexer no algoritmo.**
3. **Quando o número contradiz o que já se viu na tela, o erro provavelmente é da análise.** Uma
   contagem deu "0" contradizendo um caso já visto: a célula tinha `colspan="2"` e o índice do
   `children[]` estava deslocado.
4. **Conferir o nome exato do parâmetro antes de confiar num levantamento.** Em 01/09/2026 usei
   `status=conferidas` e `status=pendentes`; os valores válidos são `conferida` e `pendente`, e
   qualquer outro cai no default `todas`. Relatei como "OK incompletos" um levantamento que era de
   **todos** os lançamentos, e cheguei a diagnosticar como bug um comportamento correto do código.
   **Validar o filtro contra um total conhecido antes de tirar conclusão dele.**
5. **Registro técnico não é lançamento a classificar.** Ao medir completude, excluir
   `somente_conciliacao`, `substituido_por` e `duplicada` — eles estão fora do resultado por
   construção e nunca vão ter classificação completa.

---

# 11. Estado atual e pendências

## 11.1 Cartão Unicred Conjunta — fechado

**As 20 faturas de 01/2025 a 08/2026 fecham 100%**: nenhuma linha sem vínculo, nenhum órfão do
Pluggy, zero divergência, e o "Despesas no DRE" de cada uma bate com o total do PDF — exceto
maio/2026, onde R$ 66,55 estão legitimamente em "Fora do DRE" por natureza.

Estado da classificação (01/09/2026, excluindo registros técnicos):

| | Total | Com OK | Sem OK |
|---|---|---|---|
| Lançamentos reais | 2.722 | 680 | 2.042 |
| Classificação incompleta | 1.435 | **0** | 1.435 |
| Sem categoria | 610 | 0 | 610 |

**Todos os 680 lançamentos com OK estão com os quatro campos completos** — coerente com a regra de
obrigatoriedade. O trabalho que falta está inteiramente nos que **não** têm OK.

**O consenso automático está exaurido:** dos 1.435 incompletos, apenas ~10 têm consenso disponível.
O resto são lojistas onde falta justamente o campo que varia por contexto — UNICRED TAG (77),
DELTA VIDEIRA (68), APPLE.COM/BILL (29), LISCIA (27): têm categoria, falta Responsável/Projeto/
Portfólio, que dependem de qual viagem, qual veículo, quem usou. **Não têm solução automática** —
precisam de decisão caso a caso.

## 11.2 Pendências que dependem do usuário

- **Rotar o token do Coolify.** Foi colado no chat em 21/08/2026 e deve ser considerado
  comprometido. Gerar novo em Coolify → Keys & Tokens e revogar o antigo. **Aconteceu de novo em
  01/09/2026**, outro token colado em texto puro no chat para investigar um deploy travado — mesma
  regra se aplica, esse também precisa ser rotacionado.
- **Revisar `/pendencias`.** Categoria sem natureza assume `despesa` e pode inflar o DRE. Abrir a
  tela antes de agir — os números mudam conforme o Pluggy traz categorias.
- **Classificar o que não tem consenso**, caso a caso, principalmente pedágio, combustível e
  serviços digitais.
- **LISCIA sem vínculo, julho/2026:** `Parcela Lojista Visa - LISCIA` R$ 107,50 em 12/06/2026. O
  parcelamento LISCIA R$ 215,00 em 2× já está completo — Parc.1/2 cobrada em maio e Parc.2/2 em
  junho, ambas vinculadas e contabilizadas. A fatura de julho **não tem linha de LISCIA**. É o
  padrão das mensais tardias (dia 12): o Pluggy repetindo a Parc.2/2. A tela de duplicidades já o
  classifica como "Parcela cobrada de novo" e oferece o vínculo — **falta só a decisão do
  usuário**, porque marcação de duplicidade é dele (§1.3).

## 11.2-A Vínculos que ligam estabelecimentos diferentes (varredura de 01/09/2026)

`GET /api/fatura/vinculos-suspeitos` lista, sem desfazer nada, os vínculos em que a linha da
fatura e a transação não têm **um único termo do estabelecimento em comum**. É a armadilha nº 17
da §6.5 aplicada para trás: a correção de lá passou a impedir vínculos novos, mas **não varreu os
já gravados**.

Resultado: 3.058 vínculos avaliados, **33 suspeitos**, 18 deles com agregado fora do DRE.

**Três erros reais, todos com a mesma assinatura** — parcela × total bate com uma compra alheia
dentro da tolerância de R$ 1,00:

| Parcelas | Agregado a que grudaram | Diferença |
|---|---|---|
| TOTAL SPORTES 10 × R$ 44,99 = R$ 449,90 | ORAL UNIC ODONTOL R$ 450,00 | R$ 0,10 |
| TOTAL SPORTES 10 × R$ 71,79 = R$ 717,90 | **SUPERVIZA R$ 718,40 (à vista)** | R$ 0,50 |
| ATIVA 4 × R$ 65,62 = R$ 262,48 | **POSTOS NOTA LTDA R$ 262,04 (à vista)** | R$ 0,44 |

SUPERVIZA e POSTOS NOTA são **"A vista sem juros"**: compra à vista não pode ser agregado de
parcelamento de forma nenhuma, e as duas estão fora do resultado — **R$ 980,44**, mesma classe de
defeito da §6.6. ORAL UNIC é "Parcelado Lojista", então pode ser agregado legítimo que ganhou
vínculos errados **além** dos certos; conferir antes de concluir.

**Três pares simplesmente trocados entre si**, mesmo valor, inócuos no total mas com a
classificação indo para o lojista errado: MERCEA POMARES ↔ MERCEARIA SOUZA / Unicred TAG (R$ 3,50),
XIMANGO ↔ ALLPARK (R$ 25,00), SMARTYZRBSB ↔ PANIFICADORA (R$ 40,00).

**Falsos positivos conhecidos da varredura**, não mexer: `Pagamento Recebido` ↔ `Pag de Fatura Via
Deb Aut` (§6.5 nº 12), `Anuidade - bonificação` ↔ `Est.Tarifa manutencao de conta` (§8.3), e
grafias coladas que o tokenizador não casa (`PARC=106ANJOS DE QUINTA` ↔ `ANJOS DE QUINTAL`,
`CRISTIANZANELATTO` ↔ `CristianZanelattoVIDEIRA`).

**Corrigido pela migração 50** (aprovada pelo usuário em 01/09/2026), sobre uma **lista explícita**
de estabelecimentos — a varredura tem falsos positivos legítimos que não podem ser desfeitos. Ela
apaga só os vínculos cruzados e depois reavalia quem ainda é agregado, mantendo a trava da §6.6:
**nunca desmarcar quem já gerou parcela**. Os três pares trocados voltam a ficar sem vínculo; o
vínculo automático da tela de conciliação os refaz certos, porque agora a regra do token existe.

**Desfazer vínculo muda o que entra no DRE** — o agregado volta ou sai do resultado. É decisão do
usuário, como a marcação de duplicidade (§1.3).

## 11.3 A validar com o usuário (dado que falta)

**Andar de cima da residência alugado para a BRDrive.** A casa tem dois andares: a família mora no
porão e a parte de cima é alugada para a BRDrive por R$ 1.500–1.700/mês. Isso significa que (a) há
receita de aluguel a identificar nos recebimentos da BRDrive, hoje possivelmente confundida com
pró-labore, e (b) parte da manutenção da casa é custo desse aluguel, não despesa doméstica.

**Depósitos em espécie sem origem identificada.** `Transfer - Cash` tem 32 lançamentos; os maiores
de 2026 são +R$ 16.197,64 (13/07), +R$ 12.029,00 (10/08) e +R$ 8.072,30 (21/07). Estão em natureza
`fluxo`, então **entram como receita**. Ronaldo não soube dizer a origem de cabeça — enquanto não
for caso a caso, podem estar inflando a receita.

**Duplicidades antigas em conta corrente.** O Pluggy já mandou o mesmo débito duas vezes (Cond Sta
Lúcia, 21/11/2025 — ocorreu uma vez só). A tela avisa sobre o mês aberto, mas **os meses anteriores
nunca foram varridos**.

**Horários 00:00 e diferença de três horas em conta corrente.** Não usar horário isoladamente para
apagar/mesclar: pode ser ausência de horário na origem ou conversão de fuso. Ronaldo decidiu
revisar e marcar manualmente.

**FARM GEREMIAS (Andrea)** 3× R$ 63,30 tem **dois agregados** (26/11/2025 e 10/07/2026, ambos
R$ 189,90) e linhas duplicadas nas faturas. Pode ser duas compras iguais ou duplicidade da
operadora.

**Categorias que os OK do mesmo lojista contradizem.** `consenso-preview` devolve
`categoria_divergente_entre_oks`. **A maioria é falso positivo**: o IOF chega com a mesma descrição
do lojista, então `NOVOTEL` = Accomodation + `Tax on financial operations` são a compra e o IOF
dela, ambos certos (§8.3). Divergências reais a decidir: AQUAMATER (Academia 14 × Shopping 1),
AZULEQVY2E e LATAM AIR (Airport and airlines × Viagem), ORTOCLINICA (Healthcare × Hospital clinics
and labs), GUILHERMEDASILVA (Agua 15 × Agua / Gas 8 — a categoria "Agua / Gas" parece resíduo de
antes da regra por valor da §8.4). MERCADO*MERCADOLIVRE (Houseware 13 × Vehicle maintenance 6) é
divergência **legítima**: marketplace, e a §8.4 manda não automatizar.

**Nomes candidatos a normalização editorial**, não renomear sem aprovação: `reformas`, `bgs 2026`,
`viagem atacama`, `Colegio Salvatoriano`, `Jantas`.

## 11.4 Próximas frentes, nesta ordem

1. **Cartão Nubank.** Avaliar se os mesmos fenômenos existem (parcelamento agregado, eco
   pending→posted, cobrança só na fatura). O parser de PDF é específico da Unicred — se o Nubank
   for conciliado por fatura, precisa de parser próprio; se não, a hierarquia de fontes muda.
2. **Conta corrente.** **Não tem fatura**, então a hierarquia nasce diferente: o extrato do Pluggy
   vira a única fonte de "houve cobrança", e provavelmente aparecem outros fenômenos (PIX,
   transferência entre contas próprias, depósito em espécie).
3. **Conferir o DRE mês a mês** agora que a base do cartão está consistente.
4. **`/pendencias`**: os 1.135 lançamentos criados pela fatura nasceram sem categoria e o que
   sobrou entra no DRE como despesa por padrão.

**Não transportar regras entre origens.** Criar regras e eventuais correções de horário
específicas por origem; nunca copiar em massa a lógica da Unicred sem validação própria.

## 11.5 Ideias guardadas

**Lançamentos recorrentes / previstos.** Gastos que se repetem em valor e intervalo fixos — mesada
de R$ 100 semanal, assinaturas, seguros, parcelas. O sistema só registra o que já aconteceu.
Valeria marcar o lançamento como recorrente e **projetar os próximos com base no histórico**, para
saber o compromisso do mês antes de ele acontecer.

**Depreciação de bens.** Hoje bens ficam fora do resultado e não geram despesa. Só entraria para
bens que perdem valor com o tempo — terreno não deprecia.

---

# 12. Histórico de migrações relevantes

Consultar `cartao.schema_version` e o audit log para o estado real. Migração **nunca é reescrita**.

| # | O que fez |
|---|---|
| 23 | `fatura_vinculo` — relação N:N entre linha da fatura e transação |
| 24 | `somente_conciliacao` + regime de caixa para parcelamento |
| 25 | `substituido_por` — vínculo 1-para-1 de mesmo evento |
| 29–31 | unifica o OK em `transacao.conferida`; importa os ajustes antigos para a tela nova |
| 32–33 | separa observação pessoal de `observacao_sistema` |
| 35–41 | padronização e correções pontuais da Unicred (ILLUMINATO, Mercado Livre, estornos) |
| 42 | primeiro consenso dos OK: 11 regras, backup em `classificacao_backup_v42` |
| 43 | corrige −3h nos horários Pluggy da Unicred; backup em `horario_backup_v43` |
| 44 | devolve ao DRE o agregado que não é mais agregado (§6.6); `agregado_backup_v44` |
| 45 | publica em 2025 o consenso dos OK; `classificacao_backup_v45` |
| 46 | aplica as decisões do usuário sobre lojistas divergentes (§8.4); `classificacao_backup_v46` |
| 47 | trata `valor_id` NULL como vazio e completa a classificação; `classificacao_backup_v47` |
| 48 | segundo eixo de consenso, por categoria; `classificacao_backup_v48` |
| 49 | segunda passada do consenso; `classificacao_backup_v49` |
| 50 | desfaz vínculos entre estabelecimentos diferentes; `vinculo_backup_v50` + `agregado_backup_v50` |
