// Tela de Relatórios: filtros em chip, gráfico (Chart.js) e totais agrupados.
// Todo o conteúdo vem por AJAX de /relatorios/dados e /relatorios/lancamentos,
// então este arquivo nao depende de nada interpolado pelo Python.
// ---- chip filters: dropdown com busca, checkbox toggle e navegacao por teclado ----

// ---- filtros aplicados em tempo real via AJAX (o dropdown nao fecha) ----
function fmtMoeda(v) {
  return 'R$ ' + Number(v).toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});
}
function atualizarChipLabels() {
  document.querySelectorAll('.chipfilter').forEach(cf => {
    const btn = cf.querySelector('.chip-btn');
    if (!btn) return;   // defensivo: so trata caixa de filtro de verdade
    const label = btn.dataset.label;
    const n = cf.querySelectorAll('input[type=checkbox][name]:checked').length;
    btn.classList.toggle('ativo', n > 0);
    // sem o '+': o botao abre um filtro, nao adiciona nada
    btn.innerHTML = label + (n ? ' (' + n + ')' : '') +
      (n ? '<span class="chip-clear" onclick="cfClear(event, this)">&times;</span>' : '');
  });
  // o atalho acende quando o recorte na tela e exatamente o dele: visao neutra
  // e aquela categoria sozinha. Sem isso, dois atalhos pareceriam ligados ao
  // mesmo tempo depois de um clique.
  const visaoNeutra = document.getElementById('selVisao').value === 'neutro';
  const cats = Array.from(document.querySelectorAll('.chipfilter input[name="categoria"]:checked'))
                    .map(cb => cb.value);
  document.querySelectorAll('[data-atalho-categoria]').forEach(botao => {
    botao.classList.toggle('ativo',
      visaoNeutra && cats.length === 1 && cats[0] === botao.dataset.atalhoCategoria);
  });
  // chips pequenos mostrando tudo que esta selecionado
  const cont = document.getElementById('chipsSel');
  if (cont) {
    // [name] exigido: checkbox sem nome (ex: o do menu de colunas) nao e filtro
    const marcados = Array.from(document.querySelectorAll('.chipfilter input[type=checkbox][name]:checked'));
    // curto/completo saem do DOM via textContent, que DECODIFICA o que o Jinja
    // escapou - voltar isso cru para innerHTML reabriria o XSS. Ex: um valor de
    // dimensao chamado "<img src=x onerror=...>" criado em /dimensoes.
    cont.innerHTML = marcados.map(cb => {
      const lbl = cb.closest('.chip-opt');
      const curto = lbl.dataset.curto || textoDaOpcao(lbl);
      const completo = lbl.getAttribute('data-tip') || curto;
      // nome/valor vao em data-attribute e o clique e tratado por delegacao:
      // interpolado num onclick, um nome com aspas fecharia o atributo cedo e o
      // handler falharia em silencio (secao 2.2)
      return '<span class="chip-tag" title="' + escHtml(completo) + '"><span>' + escHtml(curto) + '</span>' +
             '<b class="chip-x" data-nome="' + escHtml(cb.name) + '" data-valor="' + escHtml(cb.value) + '">&times;</b></span>';
    }).join('');
  }
}
document.addEventListener('click', function (e) {
  const x = e.target.closest('.chip-x');
  if (x) desmarcarFiltro(x.dataset.nome, x.dataset.valor);
  const atalho = e.target.closest('[data-atalho-categoria]');
  if (atalho) conciliarCategoria(atalho.dataset.atalhoCategoria);
});
// O atalho NAO monta uma URL propria: ele mexe nos mesmos controles e chama o
// mesmo aplicarFiltros(). Um segundo caminho de filtrar comecaria igual e
// divergiria na primeira regra nova (secao 7.2-A) - e aqui divergir significa o
// botao mostrar um recorte diferente do que os filtros dizem estar aplicado.
function conciliarCategoria(chave) {
  document.getElementById('selVisao').value = 'neutro';
  // mes a mes: a pergunta e "o que saiu e o que voltou em cada mes"
  const agrupar = document.getElementById('selAgrupar');
  if (Array.from(agrupar.options).some(o => o.value === 'mes')) agrupar.value = 'mes';
  // a categoria do atalho passa a ser a UNICA marcada: somar outra contraparte
  // no mesmo saldo nao concilia coisa nenhuma
  let achou = false;
  document.querySelectorAll('.chipfilter input[type=checkbox][name="categoria"]').forEach(cb => {
    cb.checked = cb.value === chave;
    if (cb.checked) achou = true;
  });
  // periodo e demais filtros ficam como estao: o usuario ja escolheu a janela
  if (!achou) return;
  aplicarFiltros();
}
function desmarcarFiltro(nome, valor) {
  // comparacao em JS em vez de seletor CSS: valor com aspas quebraria o seletor
  const cb = Array.from(document.querySelectorAll('.chipfilter input[type=checkbox][name]'))
                  .find(c => c.name === nome && c.value === valor);
  if (cb) { cb.checked = false; aplicarFiltros(); }
}
function coletarQuery() {
  const params = new URLSearchParams();
  params.set('visao', document.getElementById('selVisao').value);
  params.set('agrupar', document.getElementById('selAgrupar').value);
  // [name] exigido: .chip-opt e usada tambem pelos itens do menu de colunas, cujos
  // checkboxes nao tem nome e nao sao filtro
  document.querySelectorAll('.chip-opt input[type=checkbox][name]:checked').forEach(cb => params.append(cb.name, cb.value));
  const di = document.getElementById('inputDataIni').value;
  const df = document.getElementById('inputDataFim').value;
  if (di) params.set('data_ini', di);
  if (df) params.set('data_fim', df);
  return params;
}
function aplicarFiltros() {
  atualizarChipLabels();
  const params = coletarQuery();
  const novaUrl = '/relatorios?' + params.toString();
  if (novaUrl !== window.location.pathname + window.location.search) {
    history.pushState({pedemeia: true}, '', novaUrl);
  }
  carregarDados(params);
}
window.addEventListener('popstate', function () {
  guardarPosicaoAtual();
  window.location.reload();
});
function carregarDados(params) {
  fetch('/relatorios/dados?' + params.toString()).then(r => r.json()).then(renderResultado);
}
// "Saldo" e o nome certo da visao neutra: ali saida e entrada se somam com o
// sinal, e o que sobra e o que ainda esta em aberto. Chamar de "total" sugeriria
// volume, e zero passaria a parecer "nao houve nada" em vez de "fechou".
const LABEL_VISAO = { despesa: 'Total de despesas', receita: 'Total de receitas',
                      investimento: 'Investido / adquirido', tudo: 'Fluxo de caixa (líquido)',
                      neutro: 'Saldo em aberto (saídas − entradas)' };
function renderResultado(data) {
  // lido pelo renderGrupos: a visao decide se a linha mostra os dois lados
  window.__visao = data.visao;
  document.getElementById('totalGeral').textContent = fmtMoeda(data.total_geral);
  document.getElementById('labelTotal').textContent = LABEL_VISAO[data.visao] || 'Total no filtro';
  const lados = document.getElementById('totalLados');
  if (lados) {
    lados.hidden = data.visao !== 'neutro';
    if (data.visao === 'neutro') {
      lados.innerHTML = '<span style="color:var(--bad)">↑ saiu ' + fmtMoeda(data.saidas_geral) + '</span>' +
                        ' · <span style="color:var(--good)">↓ entrou ' + fmtMoeda(data.entradas_geral) + '</span>';
    }
  }
  document.getElementById('qtdGeral').textContent = data.qtd_geral;
  const ehPeriodo = data.agrupar === 'mes';
  document.getElementById('graficoTitulo').textContent =
    ehPeriodo ? 'Evolução mês a mês' : 'Gráfico (' + data.agrupar_label + ')';
  renderGrupos(data.grupos, ehPeriodo);
  renderChart(data.grupos, ehPeriodo);
}

// ---- lista de totais agrupados, clicavel para ver os lancamentos de cada grupo ----
window.__grupos = [];
function renderGrupos(grupos, ehPeriodo) {
  // o grafico fica na ordem cronologica (linha do tempo); ja a lista abaixo
  // mostra o mes mais recente no topo, que e o que se quer olhar primeiro
  const lista = ehPeriodo ? grupos.slice().reverse() : grupos;
  window.__grupos = lista;
  const cont = document.getElementById('gruposCont');
  if (!lista.length) {
    cont.innerHTML = '<div style="color:var(--ink-faint);padding:10px 0">Nenhum lancamento encontrado com esses filtros.</div>';
    return;
  }
  // na linha do tempo a barra fica proporcional ao maior mes (fica legivel),
  // e mostramos a variacao em relacao ao mes anterior
  const maxTotal = Math.max.apply(null, lista.map(g => Math.abs(g.total)).concat([1]));
  cont.innerHTML = lista.map((g, i) => {
    const larguraBarra = ehPeriodo ? (Math.abs(g.total) / maxTotal * 100) : Math.max(g.pct, 0);
    let direita = '<strong>' + fmtMoeda(g.total) + '</strong> <span style="color:var(--ink-faint)">' + g.pct + '%</span>';
    // Na visao neutra o SINAL e o que separa os lados, e ver so o saldo esconde
    // metade da historia: zero tanto pode ser "fechou certinho" quanto "nao
    // aconteceu nada". Saidas e entradas aparecem ao lado do saldo, e uma
    // categoria so - "BRDrive" - basta, sem o usuario dizer o lado (16/09/2026).
    if (window.__visao === 'neutro') {
      direita = '<span style="color:var(--bad)" title="saiu">↑ ' + fmtMoeda(g.saidas) + '</span>' +
                ' <span style="color:var(--good)" title="entrou">↓ ' + fmtMoeda(g.entradas) + '</span>' +
                ' <strong title="saldo em aberto (saídas − entradas)">' + fmtMoeda(g.total) + '</strong>';
    }
    // lista invertida: o mes anterior e o de baixo (i + 1)
    if (ehPeriodo && i < lista.length - 1) {
      const ant = lista[i + 1].total;
      if (ant) {
        const varPct = (g.total - ant) / Math.abs(ant) * 100;
        const cor = varPct > 0 ? 'var(--bad)' : 'var(--good)';
        const sinal = varPct > 0 ? '▲' : '▼';
        direita = '<strong>' + fmtMoeda(g.total) + '</strong> ' +
                  '<span style="color:' + cor + ';font-size:var(--fonte-sm)" title="variação em relação ao mês anterior">' +
                  sinal + ' ' + Math.abs(varPct).toFixed(1) + '%</span>';
      }
    }
    return '<div>' +
      '<div class="rel-grupo-row" style="cursor:pointer" onclick="toggleGrupoDetalhe(' + i + ')">' +
        '<div style="flex:1">' +
          '<div style="display:flex;justify-content:space-between">' +
            '<span>' + (g.selo || '') + escHtml(g.nome) + ' <span style="color:var(--ink-faint)">(' + escHtml(g.qtd) + ')</span></span>' +
            '<span>' + direita + '</span>' +
          '</div>' +
          '<div class="barra"><div style="width:' + larguraBarra + '%"></div></div>' +
        '</div>' +
      '</div>' +
      '<div class="rel-grupo-detalhe" id="grupoDetalhe' + i + '" style="display:none"></div>' +
    '</div>';
  }).join('');
}
function toggleGrupoDetalhe(i) {
  const el = document.getElementById('grupoDetalhe' + i);
  const abrir = el.style.display === 'none';
  document.querySelectorAll('.rel-grupo-detalhe').forEach(d => { if (d !== el) d.style.display = 'none'; });
  if (!abrir) { el.style.display = 'none'; return; }
  el.style.display = 'block';
  if (el.dataset.loaded === '1') return;
  el.innerHTML = '<div style="padding:10px;color:var(--ink-faint);font-size:var(--fonte-md)">Carregando...</div>';
  const g = window.__grupos[i];
  const params = coletarQuery();
  if (g.valor === null || g.valor === undefined) { params.set('valor_none', '1'); }
  else { params.set('valor', g.valor); }
  fetch('/relatorios/lancamentos?' + params.toString())
    .then(r => r.json())
    .then(data => {
      el.dataset.loaded = '1';
      if (!data.lancamentos.length) {
        el.innerHTML = '<div style="padding:10px;color:var(--ink-faint);font-size:var(--fonte-md)">Nenhum lancamento.</div>';
        return;
      }
      // descricao, origem e categoria sao texto do banco (Pluggy ou digitado) e
      // entram em innerHTML - tem que passar por escHtml. So o selo e HTML do app.
      el.innerHTML = '<table class="rel-mini-table"><thead><tr><th>Data</th><th>Descrição</th><th>Origem</th><th>Categoria</th><th>Valor</th></tr></thead><tbody>' +
        data.lancamentos.map(l => (
          '<tr><td>' + escHtml(l.data) + '</td><td>' + escHtml(l.descricao) + '</td>' +
          '<td data-tip="' + escHtml(l.origem_completa || '') + '">' +
            (l.origem_selo || '') + escHtml(l.origem) + '</td>' +
          '<td>' + escHtml(l.categoria) + '</td>' +
          '<td class="valor">' + fmtMoeda(l.valor) + '</td></tr>'
        )).join('') +
        '</tbody></table>' +
        (data.total >= 300 ? '<div style="padding:8px 10px;color:var(--ink-faint);font-size:var(--fonte-sm)">Mostrando os 300 lancamentos mais recentes deste grupo.</div>' : '');
    });
}

// ---- grafico dinamico conforme os filtros aplicados ----
let chartInstance = null;
let chartTipoAtual = null;
function renderChart(grupos, ehPeriodo) {
  if (!window.Chart) return;
  const labels = grupos.map(g => g.nome);
  const valores = grupos.map(g => g.total);
  // linha do tempo (mes a mes) fica melhor como linha; os demais, como barras
  const tipo = ehPeriodo ? 'line' : 'bar';
  const corAcento = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#0e7490';
  const corAcentoSuave = getComputedStyle(document.documentElement).getPropertyValue('--accent-soft').trim() || '#e0f4f6';
  const dataset = ehPeriodo
    ? { label: 'Total (R$)', data: valores, borderColor: corAcento, backgroundColor: corAcentoSuave,
         fill: true, tension: .3, pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: corAcento, borderWidth: 2 }
    : { label: 'Total (R$)', data: valores, backgroundColor: corAcento, borderRadius: 4, maxBarThickness: 46 };

  if (chartInstance && chartTipoAtual === tipo) {
    chartInstance.data.labels = labels;
    chartInstance.data.datasets[0] = dataset;
    chartInstance.update();
    return;
  }
  if (chartInstance) chartInstance.destroy();
  chartTipoAtual = tipo;
  chartInstance = new Chart(document.getElementById('chartGrupos'), {
    type: tipo,
    data: { labels: labels, datasets: [dataset] },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: c => fmtMoeda(c.parsed.y) } }
      },
      scales: {
        y: { beginAtZero: true, ticks: { callback: v => 'R$ ' + Number(v).toLocaleString('pt-BR') } }
      }
    }
  });
}

document.addEventListener('DOMContentLoaded', function() {
  // abrindo por um link ja filtrado, o atalho correspondente ja nasce aceso
  atualizarChipLabels();
  carregarDados(new URLSearchParams(window.location.search));
});
