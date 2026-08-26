// Tela de Lançamentos. Depende de tabelas.js (escHtml, ativarTabelaAjustavel),
// carregado antes deste arquivo.
//
// Os dados que vinham interpolados pelo Python agora sao lidos de tags
// <script type="application/json"> no HTML - foi o que permitiu este arquivo
// virar estatico. lerJson() e a porta de entrada desses dados.
function lerJson(seletor, padrao) {
  const el = document.querySelector(seletor);
  if (!el) return padrao;
  try { return JSON.parse(el.textContent); } catch (e) { return padrao; }
}

window.configLancamentos = lerJson('script[data-config]', {});

// Cada linha nasce apenas com a opcao selecionada. As listas completas ficam
// uma unica vez no JSON da pagina e entram no select quando ele recebe foco.
// Em um mes com 122 linhas e 3 dimensoes, isso evita centenas de listas iguais.
function hidratarSelect(sel) {
  if (!sel || sel.dataset.hidratado === '1' || sel.disabled) return;
  const atual = sel.value;
  let opcoes;
  if (sel.matches('.cat-select, .rateio-cat-select')) {
    opcoes = [{valor: '', rotulo: '(sem categoria)'}].concat(
      (window.configLancamentos.categorias || []).map(c => ({valor: String(c.chave), rotulo: String(c.nome)}))
    );
  } else if (sel.matches('.dim-select, .rateio-dim-select')) {
    opcoes = [{valor: '', rotulo: '(nao definido)'}].concat(
      ((window.configLancamentos.dimensoes || {})[sel.dataset.dim] || [])
        .map(v => ({valor: String(v.id), rotulo: String(v.rotulo)}))
    );
    const nomeDimensao = (window.configLancamentos.dimensoes_cadastro_rapido || {})[sel.dataset.dim];
    if (nomeDimensao && sel.matches('.dim-select')) {
      opcoes.push({valor: '__novo__', rotulo: '+ Cadastrar novo ' + nomeDimensao + '...'});
    }
  } else {
    return;
  }
  const fragmento = document.createDocumentFragment();
  opcoes.forEach(item => {
    const opcao = document.createElement('option');
    opcao.value = item.valor;
    opcao.textContent = item.rotulo;
    fragmento.appendChild(opcao);
  });
  sel.replaceChildren(fragmento);
  sel.value = atual;
  sel.dataset.hidratado = '1';
}

document.addEventListener('focusin', function (e) {
  if (e.target.matches('#tabela-lancamentos .cat-select, #tabela-lancamentos .dim-select, #tabela-lancamentos .rateio-cat-select, #tabela-lancamentos .rateio-dim-select, .modal-dim-select')) {
    hidratarSelect(e.target);
  }
});
document.addEventListener('pointerdown', function (e) {
  if (e.target.matches('#tabela-lancamentos .cat-select, #tabela-lancamentos .dim-select, #tabela-lancamentos .rateio-cat-select, #tabela-lancamentos .rateio-dim-select, .modal-dim-select')) {
    hidratarSelect(e.target);
    e.target.dataset.valorAnterior = e.target.value;
  }
});

function cadastrarNovoValorDimensao(sel) {
  const nomeDimensao = (window.configLancamentos.dimensoes_cadastro_rapido || {})[sel.dataset.dim] || 'item';
  const nome = prompt('Nome do novo ' + nomeDimensao + ':');
  if (!nome || !nome.trim()) {
    sel.value = sel.dataset.valorAnterior || '';
    return Promise.resolve(false);
  }
  return fetch('/api/dimensao/' + encodeURIComponent(sel.dataset.dim) + '/valor', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({nome: nome.trim()})
  }).then(r => r.json()).then(d => {
    if (!d.ok) throw new Error(d.erro || 'Não foi possível cadastrar.');
    const lista = (window.configLancamentos.dimensoes || {})[sel.dataset.dim] || [];
    if (!lista.some(v => String(v.id) === String(d.id))) lista.push({id: d.id, rotulo: d.nome});
    document.querySelectorAll('.dim-select[data-dim="' + sel.dataset.dim + '"]').forEach(outro => {
      const valor = outro === sel ? String(d.id) : outro.value;
      delete outro.dataset.hidratado;
      hidratarSelect(outro);
      outro.value = valor;
    });
    if (sel.matches('.modal-dim-select')) salvarDimensaoModal(sel);
    else {
      const id = idDaLinha(sel);
      if (id) salvar(id, sel);
    }
    return true;
  }).catch(e => {
    sel.value = sel.dataset.valorAnterior || '';
    alert(e.message || 'Não foi possível cadastrar.');
    return false;
  });
}

// ---- chip filter (origem): filtra sem fechar o painel ----
function cfToggle(btn) {
  const panel = btn.nextElementSibling;
  const abrir = !panel.classList.contains('show');
  document.querySelectorAll('.chip-panel.show').forEach(p => { if (p !== panel) p.classList.remove('show'); });
  if (abrir) {
    panel.classList.add('show');
    const search = panel.querySelector('.chip-search');
    if (search) { search.value = ''; cfFiltrar(search); search.focus(); }
  } else {
    panel.classList.remove('show');
  }
}
document.addEventListener('click', function(e) {
  if (!e.target.closest('.chipfilter') && !e.target.closest('.chip-tag') && !e.target.closest('.menu-colunas')) {
    document.querySelectorAll('.chip-panel.show').forEach(p => p.classList.remove('show'));
  }
});
function cfClear(e, btn) {
  e.stopPropagation();
  const panel = btn.closest('.chipfilter').querySelector('.chip-panel');
  panel.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = false);
  aplicarFiltros();
}
function cfFiltrar(input) {
  const panel = input.closest('.chip-panel');
  const q = input.value.toLowerCase();
  panel.querySelectorAll('.chip-opt').forEach(opt => {
    // sem o contador: buscar "13" nao pode casar com a conta que tem 13 lancamentos
    opt.style.display = textoDaOpcao(opt).toLowerCase().includes(q) ? 'flex' : 'none';
  });
  panel.querySelectorAll('.chip-hover').forEach(o => o.classList.remove('chip-hover'));
}
function cfKeydown(e, input) {
  const panel = input.closest('.chip-panel');
  const visiveis = Array.from(panel.querySelectorAll('.chip-opt')).filter(o => o.style.display !== 'none');
  let idx = visiveis.findIndex(o => o.classList.contains('chip-hover'));
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (idx >= 0) visiveis[idx].classList.remove('chip-hover');
    idx = Math.min(idx + 1, visiveis.length - 1);
    if (visiveis[idx]) visiveis[idx].classList.add('chip-hover');
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (idx >= 0) visiveis[idx].classList.remove('chip-hover');
    idx = Math.max(idx - 1, 0);
    if (visiveis[idx]) visiveis[idx].classList.add('chip-hover');
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (idx >= 0) {
      const cb = visiveis[idx].querySelector('input[type=checkbox]');
      cb.checked = !cb.checked;
      aplicarFiltros();
    }
  } else if (e.key === 'Escape') {
    panel.classList.remove('show');
  }
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
  // chips pequenos ao lado mostrando o que esta selecionado
  const cont = document.getElementById('chipsSel');
  const marcados = Array.from(document.querySelectorAll('.chipfilter input[type=checkbox][name]:checked'));
  // curto/completo saem do DOM via textContent, que DECODIFICA o que o Jinja
  // escapou - voltar isso cru para innerHTML reabriria o XSS. Ex: um valor de
  // dimensao chamado "<img src=x onerror=...>" criado em /dimensoes.
  cont.innerHTML = marcados.map(cb => {
    const lbl = cb.closest('.chip-opt');
    const curto = lbl.dataset.curto || textoDaOpcao(lbl);
    const completo = lbl.getAttribute('data-tip') || curto;
    // o valor vai num data-attribute e o clique e tratado por delegacao. Nao da
    // pra montar onclick="f('...')" aqui: o navegador decodifica a entidade ANTES
    // do JS rodar, entao um valor com aspas escaparia da string mesmo escapado.
    return '<span class="chip-tag" title="' + escHtml(completo) + '"><span>' + escHtml(curto) + '</span>' +
           '<b class="chip-x" data-valor="' + escHtml(cb.value) + '">&times;</b></span>';
  }).join('');
}
document.addEventListener('click', function (e) {
  const x = e.target.closest('.chip-x');
  if (x) desmarcarOrigem(x.dataset.valor);
});
function desmarcarOrigem(valor) {
  // comparacao em JS em vez de seletor CSS: valor com aspas quebraria o seletor
  const cb = Array.from(document.querySelectorAll('.chipfilter input[type=checkbox][name]'))
                  .find(c => c.value === valor);
  if (cb) { cb.checked = false; aplicarFiltros(); }
}

// ---- aplica filtros via AJAX: o dropdown continua aberto ----
function coletarQuery() {
  const params = new URLSearchParams();
  params.set('mes', document.getElementById('mesInput').value);
  if (document.getElementById('periodoIntervalo').checked) {
    params.set('periodo', 'intervalo');
    params.set('data_inicio', document.getElementById('dataInicioInput').value);
    params.set('data_fim', document.getElementById('dataFimInput').value);
  } else {
    params.set('periodo', document.getElementById('periodoAno').checked ? 'ano' : 'mes');
  }
  params.set('status', document.getElementById('statusInput').value);
  // [name] exigido: checkbox sem nome (ex: o do menu de colunas) nao e filtro
  document.querySelectorAll('.chipfilter input[type=checkbox][name]:checked').forEach(cb => params.append(cb.name, cb.value));
  return params;
}
// "Periodo customizado" e "Ano inteiro" sao mutuamente exclusivos - ligar um
// desliga o outro, senao os dois brigam pelo mesmo par mes/periodo na URL.
function alternarPeriodoAno() {
  if (document.getElementById('periodoAno').checked) {
    document.getElementById('periodoIntervalo').checked = false;
    document.getElementById('intervaloWrap').style.display = 'none';
  }
  aplicarFiltros(true);
}
function alternarPeriodoIntervalo() {
  const intervalo = document.getElementById('periodoIntervalo');
  document.getElementById('intervaloWrap').style.display = intervalo.checked ? 'flex' : 'none';
  if (intervalo.checked) {
    document.getElementById('periodoAno').checked = false;
    if (!document.getElementById('dataInicioInput').value || !document.getElementById('dataFimInput').value) return;
  }
  aplicarFiltros(true);
}
// avanca/retrocede um mes no filtro. Usa Date pra virar o ano sozinho
// (dezembro -> janeiro do ano seguinte) em vez de somar no numero do mes.
function mudarMes(delta) {
  const campo = document.getElementById('mesInput');
  const partes = (campo.value || '').split('-').map(Number);
  if (partes.length !== 2 || !partes[0] || !partes[1]) return;
  // com "Ano inteiro" marcado, os botoes ‹ › andam um ano por vez em vez de um mes
  if (document.getElementById('periodoAno').checked) {
    campo.value = (partes[0] + delta) + '-' + String(partes[1]).padStart(2, '0');
  } else {
    const d = new Date(partes[0], partes[1] - 1 + delta, 1);
    campo.value = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
  }
  aplicarFiltros(true);
}
function aplicarFiltros(recarregarPagina) {
  atualizarChipLabels();
  const params = coletarQuery();
  const novaUrl = '/?' + params.toString();
  if (recarregarPagina) {
    guardarPosicaoAtual();
    window.location.assign(novaUrl);
    return;
  }
  if (novaUrl !== window.location.pathname + window.location.search) {
    history.pushState({pedemeia: true}, '', novaUrl);
  }
  fetch(novaUrl, { headers: { 'X-Parcial': '1' } })
    .then(r => r.text())
    .then(html => {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const novaTabela = doc.querySelector('table.compacta');
      const novosCards = doc.querySelector('.cards');
      const novaCat = doc.querySelector('details.cat-breakdown');
      if (novaTabela) {
        document.querySelector('table.compacta').replaceWith(novaTabela);
        // a tabela nova veio do servidor sem os listeners nem as alcas de
        // redimensionar (sao criados por JS) - replaceWith descarta o elemento
        // antigo junto com tudo que estava anexado nele, entao precisa reativar
        ativarTabelaAjustavel(novaTabela, 'lancamentos');
      }
      if (novosCards) document.querySelector('.cards').replaceWith(novosCards);
      const catAtual = document.querySelector('details.cat-breakdown');
      if (novaCat && catAtual) {
        // preserva o estado aberto/fechado escolhido pelo usuario
        novaCat.open = catAtual.open;
        catAtual.replaceWith(novaCat);
      }
      const scriptNovo = doc.querySelector('script[data-detalhes]');
      if (scriptNovo) {
        try { window.detalhes = JSON.parse(scriptNovo.textContent); } catch (e) {}
      }
      const configNova = doc.querySelector('script[data-config]');
      if (configNova) {
        try { window.configLancamentos = JSON.parse(configNova.textContent); } catch (e) {}
      }
    });
}

// Cada filtro/mes vira uma etapa real do navegador. Ao voltar ou avancar,
// recarrega o estado correspondente sem criar uma nova entrada no historico.
window.addEventListener('popstate', function () {
  guardarPosicaoAtual();
  window.location.reload();
});

window.detalhes = lerJson('script[data-detalhes]', {});
let idAtualModal = null;
let acaoConfirmacaoModal = null;
let rateioRascunhoModal = null;

function detalheAtualModal() {
  return idAtualModal ? (window.detalhes[idAtualModal] || {}) : {};
}

function sincronizarControlesModal() {
  const d = detalheAtualModal();
  const conf = document.getElementById('modalConferida');
  const dup = document.getElementById('modalDup');
  if (conf) conf.value = d._conferida ? 'sim' : 'nao';
  if (dup) dup.checked = !!d._duplicada;
  const conferidaPor = document.getElementById('modalConferidaPor');
  const usuarioConferencia = d.conferida_por && d.conferida_por !== '-' ? d.conferida_por : '';
  conferidaPor.textContent = usuarioConferencia ? 'por ' + usuarioConferencia : '';
  conferidaPor.hidden = !d._conferida || !usuarioConferencia;
  const observacao = document.getElementById('modalObservacao');
  observacao.value = d.observacao && d.observacao !== '-' ? d.observacao : '';
}

function verDetalhes(id) {
  const d = window.detalhes[id];
  if (!d) return;
  idAtualModal = id;
  // Valores e status ficam em pares para usar melhor o espaco do modal.
  const html =
    '<div class="row row-pareada">' +
      '<span class="modal-campo"><small>Data</small><strong>' + escHtml(d.data) + '</strong></span>' +
      '<span class="modal-campo"><small>Valor (R$)</small><strong>' + escHtml(d.valor) + '</strong></span>' +
    '</div>' +
    '<div class="row"><span>Descrição</span><span>' + escHtml(d.descricao) + '</span></div>' +
    '<div class="row row-pareada">' +
      '<span class="modal-campo"><small>Valor original</small><strong>' + escHtml(d.valor_original) + '</strong></span>' +
      '<span class="modal-campo"><small>Parcela</small><strong>' + escHtml(d.parcela) + '</strong></span>' +
    '</div>' +
    '<div class="row row-pareada">' +
      '<span class="modal-campo"><small>Status</small><strong>' + escHtml(d.status) + '</strong></span>' +
      '<span class="modal-campo"><small>Tipo</small><strong>' + escHtml(d.tipo) + '</strong></span>' +
    '</div>' +
    '<div class="row"><span>Origem</span><span>' + escHtml(d.origem) + '</span></div>' +
    '<div class="row row-pareada">' +
      '<span class="modal-campo"><small>Visto 1ª vez em</small><strong>' + escHtml(d.primeiro_sincronizado_em) + '</strong></span>' +
      '<span class="modal-campo"><small>Última sincronização</small><strong>' + escHtml(d.sincronizado_em) + '</strong></span>' +
    '</div>';
  document.getElementById('modalBody').innerHTML = html;
  document.getElementById('modalAcoes').style.display = d._manual ? 'block' : 'none';
  const trAtual = document.querySelector('tr[data-id="' + id + '"]');
  // espelha a categoria da linha; mudar aqui muda la e salva
  const selCat = document.getElementById('modalCategoria');
  const catLinha = trAtual ? trAtual.querySelector('.cat-select') : null;
  if (selCat && catLinha) selCat.value = catLinha.value;
  const emRateio = !!(d._rateios && d._rateios.length);
  if (selCat) selCat.disabled = !window.configLancamentos.pode_editar || emRateio;
  const selConferida = document.getElementById('modalConferida');
  if (selConferida) {
    const pendenteSemOk = d._pendente_banco && !d._conferida;
    selConferida.disabled = !window.configLancamentos.pode_conferir || (emRateio && !d._rateio_valido) || pendenteSemOk;
    selConferida.title = emRateio && !d._rateio_valido
      ? 'Ajuste as partes até o rateio fechar o valor do lançamento'
      : pendenteSemOk
      ? 'Pendente no banco — aguarde confirmar para marcar OK' : '';
  }
  document.querySelectorAll('.modal-dim-select').forEach(selModal => {
    const selLinha = trAtual && trAtual.querySelector('.dim-select[data-dim="' + selModal.dataset.dim + '"]');
    const opcao = document.createElement('option');
    opcao.value = selLinha ? selLinha.value : '';
    opcao.textContent = selLinha && selLinha.selectedOptions[0]
      ? selLinha.selectedOptions[0].textContent : '(nao definido)';
    selModal.replaceChildren(opcao);
    selModal.value = opcao.value;
    delete selModal.dataset.hidratado;
    selModal.disabled = !window.configLancamentos.pode_editar || emRateio;
  });
  cancelarConfirmacaoModal();
  sincronizarControlesModal();
  rateioRascunhoModal = null;
  renderizarRateioModal();
  document.getElementById('modalBg').classList.add('show');
}

function opcoesRateio(lista, atual, vazio) {
  let html = vazio ? '<option value="">' + escHtml(vazio) + '</option>' : '';
  (lista || []).forEach(item => {
    const valor = String(item.valor !== undefined ? item.valor : item.id);
    const rotulo = item.rotulo !== undefined ? item.rotulo : item.nome;
    html += '<option value="' + escHtml(valor) + '"' + (String(atual || '') === valor ? ' selected' : '') + '>' + escHtml(rotulo) + '</option>';
  });
  return html;
}

function iniciarRateioModal() {
  const d = detalheAtualModal();
  const total = Math.round(Number(d._valor_rateio || 0) * 100);
  const primeira = Math.floor(total / 2);
  const tr = document.querySelector('tr[data-id="' + idAtualModal + '"]');
  const categoria = tr && tr.querySelector('.cat-select') ? tr.querySelector('.cat-select').value : '';
  const dimensoes = {};
  if (tr) tr.querySelectorAll('.dim-select').forEach(s => { dimensoes[s.dataset.dim] = s.value || ''; });
  rateioRascunhoModal = [
    {valor: primeira / 100, categoria: categoria, observacao: '', dims: {...dimensoes}},
    {valor: (total - primeira) / 100, categoria: categoria, observacao: '', dims: {...dimensoes}},
  ];
  renderizarRateioModal();
}

function adicionarParteRateio() {
  rateioRascunhoModal.push({valor: 0, categoria: '', observacao: '', dims: {}});
  renderizarRateioModal();
}

function removerParteRateio(indice) {
  if (rateioRascunhoModal.length <= 2) return;
  rateioRascunhoModal.splice(indice, 1);
  renderizarRateioModal();
}

function lerRateioModal() {
  return Array.from(document.querySelectorAll('#modalRateioPartes .modal-rateio-parte')).map(parte => {
    const dimensoes = {};
    parte.querySelectorAll('.rateio-dim').forEach(s => { dimensoes[s.dataset.dim] = s.value || null; });
    return {
      valor: parte.querySelector('.rateio-valor').value,
      categoria: parte.querySelector('.rateio-categoria').value,
      observacao: parte.querySelector('.rateio-observacao').value,
      dimensoes: dimensoes,
    };
  });
}

function atualizarFechamentoRateio() {
  const d = detalheAtualModal();
  const total = Number(d._valor_rateio || 0);
  const soma = Array.from(document.querySelectorAll('.rateio-valor')).reduce((n, i) => n + Number(i.value || 0), 0);
  const el = document.getElementById('rateioFechamento');
  if (!el) return;
  const fecha = Math.round(soma * 100) === Math.round(total * 100);
  el.textContent = fecha ? '' : 'Rateado R$ ' + soma.toFixed(2) + ' de R$ ' + total.toFixed(2);
  el.classList.toggle('invalido', !fecha);
  const box = document.getElementById('modalRateioBox');
  if (box) box.classList.toggle('rateio-invalido', !fecha);
  document.querySelectorAll('#modalRateioPartes .rateio-valor').forEach(input => {
    input.classList.toggle('invalido', !fecha);
  });
  const salvar = document.getElementById('salvarRateioModalBtn');
  if (salvar) salvar.disabled = !fecha;
}

function renderizarRateioModal() {
  const box = document.getElementById('modalRateioBox');
  const d = detalheAtualModal();
  if (!box || !idAtualModal) return;
  box.classList.remove('rateio-invalido');
  const existentes = d._rateios || [];
  if (rateioRascunhoModal === null && existentes.length) {
    rateioRascunhoModal = existentes.map(p => ({
      valor: p.valor, categoria: p.categoria, observacao: p.observacao || '', dims: {...(p.dims || {})},
    }));
  }
  if (rateioRascunhoModal === null) {
    box.innerHTML = '<div class="modal-rateio-topo"><strong>Rateio do lançamento</strong>' +
      '<button type="button" class="ver-btn" onclick="iniciarRateioModal()">Dividir em 2 partes</button></div>';
    return;
  }
  const categorias = (window.configLancamentos.categorias || []).map(c => ({valor:c.chave,rotulo:c.nome}));
  let partesHtml = '';
  rateioRascunhoModal.forEach((p, indice) => {
    let dimsHtml = '';
    Object.entries(window.configLancamentos.dimensoes || {}).forEach(([dimId, valores]) => {
      const nome = (window.configLancamentos.dimensoes_nomes || {})[dimId] || 'Dimensão';
      dimsHtml += '<div class="row"><span>' + escHtml(nome) + '</span><span><select class="rateio-dim" data-dim="' + escHtml(dimId) + '">' +
        opcoesRateio(valores, (p.dims || {})[dimId], '(nao definido)') + '</select></span></div>';
    });
    partesHtml += '<div class="modal-rateio-parte" data-indice="' + indice + '">' +
      '<div class="rateio-parte-titulo"><span>Parte ' + (indice + 1) + '</span><strong>' + (rateioRascunhoModal.length > 2 ? '<button type="button" class="ver-btn" onclick="removerParteRateio(' + indice + ')">Remover</button>' : '') + '</strong></div>' +
      '<div class="row"><span>Valor (R$)</span><span><input class="rateio-valor" type="number" min="0.01" step="0.01" value="' + Number(p.valor || 0).toFixed(2) + '" oninput="atualizarFechamentoRateio()"></span></div>' +
      '<div class="row"><span>Categoria</span><span><select class="rateio-categoria">' + opcoesRateio(categorias, p.categoria, '(sem categoria)') + '</select></span></div>' +
      dimsHtml +
      '<div class="row"><span>Observação</span><span><input class="rateio-observacao" maxlength="500" value="' + escHtml(p.observacao || '') + '"></span></div>' +
      '</div>';
  });
  box.innerHTML = '<div class="modal-rateio-topo"><strong>Rateio do lançamento</strong><span id="rateioFechamento" class="rateio-fechamento"></span></div>' +
    '<div id="modalRateioPartes">' + partesHtml + '</div>' +
    '<div id="rateioStatus" class="rateio-fechamento"></div>' +
    '<div class="modal-rateio-acoes"><button type="button" class="ver-btn" onclick="adicionarParteRateio()">+ Parte</button>' +
    (existentes.length ? '<button type="button" class="ver-btn" onclick="excluirRateioModal()">Desfazer rateio</button>' : '') +
    '<button type="button" class="ver-btn" id="salvarRateioModalBtn" onclick="salvarRateioModal()">Salvar rateio</button></div>';
  atualizarFechamentoRateio();
}

function salvarRateioModal() {
  if (!idAtualModal) return;
  const status = document.getElementById('rateioStatus');
  status.textContent = 'Salvando...';
  fetch('/api/transacao/' + encodeURIComponent(idAtualModal) + '/rateios', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({partes:lerRateioModal()})
  }).then(r => r.json()).then(res => {
    if (!res.ok) throw new Error(res.erro || 'Não foi possível salvar.');
    guardarPosicaoAtual(); window.location.reload();
  }).catch(e => { status.textContent = e.message || 'Não foi possível salvar.'; });
}

function excluirRateioModal() {
  if (!idAtualModal || !confirm('Desfazer o rateio e voltar ao lançamento simples?')) return;
  fetch('/api/transacao/' + encodeURIComponent(idAtualModal) + '/rateios', {method:'DELETE'})
    .then(r => r.json()).then(res => {
      if (!res.ok) throw new Error(res.erro || 'Não foi possível desfazer.');
      guardarPosicaoAtual(); window.location.reload();
    }).catch(e => alert(e.message || 'Não foi possível desfazer.'));
}

function abrirConfirmacaoModal(acao) {
  acaoConfirmacaoModal = acao;
  const painel = document.getElementById('modalConfirmacao');
  const titulo = document.getElementById('modalConfirmacaoTitulo');
  const botao = document.getElementById('modalConfirmarBtn');
  if (acao === 'desconferir') {
    titulo.textContent = 'Confirmar desmarcação do OK?';
    botao.textContent = 'Sim, desmarcar OK';
  } else {
    titulo.textContent = 'Confirmar lançamento como duplicado?';
    botao.textContent = 'Sim, marcar duplicada';
  }
  painel.hidden = false;
}

function cancelarConfirmacaoModal(fecharJanela) {
  acaoConfirmacaoModal = null;
  const painel = document.getElementById('modalConfirmacao');
  if (painel) painel.hidden = true;
  if (idAtualModal) sincronizarControlesModal();
  if (fecharJanela) fecharModal();
}

function confirmarAcaoModal() {
  if (!idAtualModal || !acaoConfirmacaoModal) return;
  const acao = acaoConfirmacaoModal;
  const tr = document.querySelector('tr[data-id="' + idAtualModal + '"]');
  if (!tr) return;
  document.getElementById('modalConfirmacao').hidden = true;
  acaoConfirmacaoModal = null;
  if (acao === 'desconferir') {
    const confCheck = tr.querySelector('.conf-check');
    confCheck.checked = false;
    salvar(idAtualModal, confCheck, {confirmarDesmarcacao: true}).then(sincronizarControlesModal);
  } else if (acao === 'duplicar') {
    const dupCheck = tr.querySelector('.dup-check');
    const obsInput = tr.querySelector('.obs-input');
    dupCheck.checked = true;
    if (!obsInput.value.trim()) obsInput.value = DUPLICADA_OBS_PADRAO;
    salvar(idAtualModal, dupCheck, {confirmarDuplicada: true}).then(sincronizarControlesModal);
  }
}

function alterarConferidaModal() {
  if (!idAtualModal) return;
  const d = detalheAtualModal();
  const destino = document.getElementById('modalConferida').value === 'sim';
  if (d._conferida && !destino) {
    document.getElementById('modalConferida').value = 'sim';
    abrirConfirmacaoModal('desconferir');
    return;
  }
  if (!d._conferida && destino) {
    const tr = document.querySelector('tr[data-id="' + idAtualModal + '"]');
    const confCheck = tr && tr.querySelector('.conf-check');
    if (confCheck) {
      confCheck.checked = true;
      salvar(idAtualModal, confCheck).then(sincronizarControlesModal);
    }
  }
}

function salvarCategoriaModal() {
  if (!idAtualModal) return;
  const tr = document.querySelector('tr[data-id="' + idAtualModal + '"]');
  if (!tr) return;
  const selLinha = tr.querySelector('.cat-select');
  hidratarSelect(selLinha);
  selLinha.value = document.getElementById('modalCategoria').value;
  salvar(idAtualModal, selLinha);
  // a categoria carrega a natureza contabil, entao os totais do mes mudam
  guardarPosicaoAtual();
  setTimeout(() => window.location.reload(), 600);
}
function salvarDimensaoModal(selModal) {
  if (selModal.value === '__novo__') {
    cadastrarNovoValorDimensao(selModal);
    return;
  }
  if (!idAtualModal) return;
  const tr = document.querySelector('tr[data-id="' + idAtualModal + '"]');
  if (!tr) return;
  const selLinha = tr.querySelector('.dim-select[data-dim="' + selModal.dataset.dim + '"]');
  hidratarSelect(selLinha);
  selLinha.value = selModal.value;
  salvar(idAtualModal, selLinha);
}
function salvarObservacaoModal() {
  if (!idAtualModal) return;
  const tr = document.querySelector('tr[data-id="' + idAtualModal + '"]');
  if (!tr) return;
  const obsLinha = tr.querySelector('.obs-input');
  obsLinha.value = document.getElementById('modalObservacao').value;
  salvar(idAtualModal, obsLinha).then(sincronizarControlesModal);
}
function toggleDuplicadaModal() {
  if (!idAtualModal) return;
  const marcado = document.getElementById('modalDup').checked;
  const d = detalheAtualModal();
  if (!d._duplicada && marcado) {
    document.getElementById('modalDup').checked = false;
    abrirConfirmacaoModal('duplicar');
    return;
  }
  const tr = document.querySelector('tr[data-id="' + idAtualModal + '"]');
  if (!tr) return;
  const dupCheck = tr.querySelector('.dup-check');
  dupCheck.checked = marcado;
  salvar(idAtualModal, dupCheck).then(sincronizarControlesModal);
}
function fecharModal() {
  document.getElementById('modalBg').classList.remove('show');
  idAtualModal = null;
  acaoConfirmacaoModal = null;
  rateioRascunhoModal = null;
}
// o ESC e tratado no tabelas.js, que so tira a classe .show - aqui a tela limpa
// o proprio estado
window.aoFecharModal = function () { idAtualModal = null; acaoConfirmacaoModal = null; };
function excluirManual() {
  if (!idAtualModal) return;
  const d = window.detalhes[idAtualModal] || {};
  if (!confirm('Excluir definitivamente este lançamento manual?\n\n' + (d.descricao || '') + '  ' + (d.valor || ''))) return;
  fetch('/api/lancamento-manual/' + idAtualModal, { method: 'DELETE' })
    .then(r => r.json())
    .then(res => {
      if (res.ok) { fecharModal(); guardarPosicaoAtual(); window.location.reload(); }
      else alert(res.erro || 'Não foi possível excluir.');
    });
}

function linhasRateioInline(id) {
  return Array.from(document.querySelectorAll('tr.rateio-row[data-rateio-parent="' + id + '"]'));
}

function lerRateioInline(id) {
  return linhasRateioInline(id).map(linha => {
    const dimensoes = {};
    linha.querySelectorAll('.rateio-dim-select').forEach(sel => {
      dimensoes[sel.dataset.dim] = sel.value || null;
    });
    return {
      valor: linha.querySelector('.rateio-valor-inline').value,
      categoria: linha.querySelector('.rateio-cat-select').value,
      observacao: linha.querySelector('.rateio-obs-inline').value,
      dimensoes: dimensoes,
    };
  });
}

function validarRateioInline(id) {
  const pai = document.querySelector('tr[data-id="' + id + '"]');
  const partes = lerRateioInline(id);
  const totalCentavos = Math.round(Number(pai && pai.dataset.rateioTotal || 0) * 100);
  const obrigatorias = window.configLancamentos.dimensoes_obrigatorias || [];
  let somaCentavos = 0;
  let camposValidos = partes.length >= 2;
  partes.forEach(parte => {
    const valor = Number(String(parte.valor || '').replace(',', '.'));
    if (!Number.isFinite(valor) || valor <= 0 || !parte.categoria) camposValidos = false;
    else somaCentavos += Math.round(valor * 100);
    obrigatorias.forEach(dimId => {
      if (!(parte.dimensoes || {})[dimId]) camposValidos = false;
    });
  });
  return {
    valido: camposValidos && somaCentavos === totalCentavos,
    somaCentavos: somaCentavos,
    totalCentavos: totalCentavos,
  };
}

function atualizarValidacaoRateioInline(id, alterado) {
  const pai = document.querySelector('tr[data-id="' + id + '"]');
  if (!pai) return false;
  const estado = validarRateioInline(id);
  const conf = pai.querySelector('.conf-check');
  if (conf) {
    conf.disabled = !window.configLancamentos.pode_conferir || !estado.valido;
    conf.title = estado.valido ? '' : 'Ajuste as partes até o rateio fechar o valor do lançamento';
  }
  const resumo = pai.querySelector('.rateio-resumo');
  if (resumo) {
    const soma = estado.somaCentavos / 100;
    const total = estado.totalCentavos / 100;
    resumo.textContent = estado.valido ? '' : 'Rateado R$ ' + soma.toFixed(2) + ' de R$ ' + total.toFixed(2);
    resumo.classList.toggle('invalido', !estado.valido);
  }
  linhasRateioInline(id).forEach(linha => {
    if (alterado) linha.classList.add('rateio-alterado');
    linha.classList.toggle('rateio-invalido', !estado.valido);
    const valor = linha.querySelector('.rateio-valor-inline');
    if (valor) valor.classList.toggle('invalido', !estado.valido);
    const botao = linha.querySelector('.rateio-salvar-inline');
    if (botao) botao.disabled = !window.configLancamentos.pode_editar || !estado.valido;
  });
  pai.dataset.rateioValido = estado.valido ? '1' : '0';
  if (window.detalhes[id]) window.detalhes[id]._rateio_valido = estado.valido;
  return estado.valido;
}

function salvarRateioInline(id) {
  if (!atualizarValidacaoRateioInline(id, false)) {
    alert('Ajuste as partes até a soma fechar exatamente o valor do lançamento e preencha os campos obrigatórios.');
    return;
  }
  const linhas = linhasRateioInline(id);
  linhas.forEach(linha => {
    const botao = linha.querySelector('.rateio-salvar-inline');
    if (botao) { botao.disabled = true; botao.textContent = '…'; }
  });
  fetch('/api/transacao/' + encodeURIComponent(id) + '/rateios', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({partes: lerRateioInline(id)}),
  }).then(r => r.json()).then(res => {
    if (!res.ok) throw new Error(res.erro || 'Não foi possível salvar o rateio.');
    guardarPosicaoAtual();
    window.location.reload();
  }).catch(e => {
    linhas.forEach(linha => {
      const botao = linha.querySelector('.rateio-salvar-inline');
      if (botao) { botao.disabled = false; botao.textContent = '✓'; }
    });
    alert(e.message || 'Não foi possível salvar o rateio.');
  });
}

// ---- delegacao dos eventos da tabela ----
// Os handlers eram inline (onclick/onchange no HTML) com o id interpolado pelo
// Jinja. O |tojson gera aspas duplas literais, que FECHAM o atributo antes da
// hora: onclick="linhaClick(event, "abc-123")" vira onclick="linhaClick(event, "
// e o resto do id vira atributo solto. Resultado: nem o modal abria nem as
// edicoes salvavam. Com delegacao o id sai do data-id da linha e nada e
// interpolado dentro de atributo - alem de continuar valendo para a tabela que
// o filtro AJAX substitui.
function idDaLinha(el) {
  const tr = el.closest('tr[data-id]');
  return tr ? tr.dataset.id : null;
}

document.addEventListener('click', function (e) {
  const salvarRateio = e.target.closest('.rateio-salvar-inline');
  if (salvarRateio) {
    const linhaParte = salvarRateio.closest('tr[data-rateio-parent]');
    if (linhaParte) salvarRateioInline(linhaParte.dataset.rateioParent);
    return;
  }
  const rateio = e.target.closest('.rateio-toggle');
  if (rateio) {
    const id = rateio.dataset.rateioId;
    const abrir = rateio.getAttribute('aria-expanded') !== 'true';
    document.querySelectorAll('tr[data-rateio-parent="' + id + '"]').forEach(linha => { linha.hidden = !abrir; });
    rateio.textContent = abrir ? '−' : '+';
    rateio.setAttribute('aria-expanded', abrir ? 'true' : 'false');
    return;
  }
  const regra = e.target.closest('.regra-btn');
  if (regra) {
    const id = idDaLinha(regra);
    if (id) window.location.assign('/regras?transacao=' + encodeURIComponent(id));
    return;
  }
  const tr = e.target.closest('#tabela-lancamentos tbody tr[data-id]');
  if (!tr) return;
  if (['SELECT', 'INPUT', 'OPTION', 'BUTTON'].includes(e.target.tagName)) return;
  verDetalhes(tr.dataset.id);
});

document.addEventListener('change', function (e) {
  const el = e.target;
  if (el.matches('#tabela-lancamentos .rateio-cat-select, #tabela-lancamentos .rateio-dim-select, #tabela-lancamentos .rateio-valor-inline, #tabela-lancamentos .rateio-obs-inline')) {
    const linhaParte = el.closest('tr[data-rateio-parent]');
    if (linhaParte) atualizarValidacaoRateioInline(linhaParte.dataset.rateioParent, true);
    return;
  }
  if (!el.matches('#tabela-lancamentos .cat-select, #tabela-lancamentos .dim-select, #tabela-lancamentos .conf-check')) return;
  if (el.matches('.dim-select') && el.value === '__novo__') {
    cadastrarNovoValorDimensao(el);
    return;
  }
  const id = idDaLinha(el);
  if (!id) return;
  // Tirar um OK e uma acao sensivel. Desfaz o clique imediatamente e abre os
  // detalhes completos para o usuario confirmar conscientemente.
  const d = window.detalhes[id] || {};
  if (el.matches('.conf-check') && d._conferida && !el.checked) {
    el.checked = true;
    verDetalhes(id);
    abrirConfirmacaoModal('desconferir');
    return;
  }
  salvar(id, el);
});

document.addEventListener('input', function (e) {
  if (!e.target.matches('#tabela-lancamentos .rateio-valor-inline, #tabela-lancamentos .rateio-obs-inline')) return;
  const linhaParte = e.target.closest('tr[data-rateio-parent]');
  if (linhaParte) atualizarValidacaoRateioInline(linhaParte.dataset.rateioParent, true);
});

// blur nao borbulha; focusout sim
document.addEventListener('focusout', function (e) {
  if (!e.target.matches('#tabela-lancamentos .obs-input')) return;
  const id = idDaLinha(e.target);
  if (id) salvar(id, e.target);
});
const DUPLICADA_OBS_PADRAO = window.configLancamentos.duplicada_obs || '';
const filaSalvar = {};
function salvar(id, el, opcoes) {
  opcoes = opcoes || {};
  const tr = el.closest('tr');
  // Envia somente o campo alterado. Se outra aba mudou observação/projeto/OK,
  // uma categoria antiga desta tela não volta por engano e não apaga aquilo.
  const payload = {};
  if (el.matches('.cat-select')) payload.categoria = el.value;
  else if (el.matches('.dim-select')) payload.dimensoes = {[el.dataset.dim]: el.value || null};
  else if (el.matches('.conf-check')) payload.conferida = el.checked;
  else if (el.matches('.obs-input')) payload.observacao = el.value;
  else if (el.matches('.dup-check')) {
    payload.duplicada = el.checked;
    // Ao marcar duplicidade o modal pode ter preenchido a observação padrão.
    payload.observacao = tr.querySelector('.obs-input').value;
  } else return;
  if (opcoes.confirmarDesmarcacao) payload.confirmar_desmarcacao = true;
  if (opcoes.confirmarDuplicada) payload.confirmar_duplicada = true;
  const anterior = filaSalvar[id] || Promise.resolve();
  // Uma falha anterior não pode bloquear para sempre os próximos salvamentos.
  const atual = anterior.catch(() => {}).then(() => fetch('/api/transacao/' + id, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  })).then(r => r.json()).then(d => {
    if (!d.ok) throw new Error(d.erro || 'Falha ao salvar');
    {
      // O servidor devolve o estado REAL em toda edicao, inclusive categoria,
      // dimensao e observacao. A tela nunca infere nem altera o OK por acidente.
      if ('conferida' in d) {
        const confFinal = !!d.conferida;
        tr.querySelector('.conf-check').checked = confFinal;
        tr.classList.toggle('conferida', confFinal);
        const detalhe = window.detalhes[id];
        if (detalhe) {
          detalhe._conferida = confFinal;
          detalhe.conferida = confFinal ? 'Sim' : 'Não';
          detalhe.conferida_por = d.conferida_por || '-';
        }
      }
      if ('duplicada' in d) {
        const dupFinal = !!d.duplicada;
        tr.querySelector('.dup-check').checked = dupFinal;
        tr.classList.toggle('duplicada', dupFinal);
        const detalhe = window.detalhes[id];
        if (detalhe) detalhe._duplicada = dupFinal;
      }
      if ('observacao' in payload && window.detalhes[id]) {
        window.detalhes[id].observacao = payload.observacao || '-';
      }
      tr.querySelectorAll('.dim-select').forEach(sel => {
        sel.style.borderColor = '';
        sel.style.background = '';
      });
      if (d.bloqueada) {
        (d.faltando || []).forEach(dimId => {
          const sel = tr.querySelector('.dim-select[data-dim="' + dimId + '"]');
          if (sel) { sel.style.borderColor = '#c23c34'; sel.style.background = '#fbeceb'; }
        });
        alert(d.rateio_invalido
          ? 'Não foi possível confirmar: revise o rateio e faça as partes fecharem exatamente com o valor do lançamento.'
          : d.pendente_banco
          ? 'Não foi possível confirmar: esse lançamento ainda está pendente no banco (pode mudar até a fatura fechar). Aguarde virar "Concluída" para marcar OK.'
          : 'Não foi possível confirmar: preencha os campos obrigatórios.');
      }
      const s = document.getElementById('status-' + id);
      if (s) {
        s.textContent = 'ok';
        s.classList.add('show');
        setTimeout(() => s.classList.remove('show'), 1500);
      }
      return true;
    }
  }).catch(() => {
    const detalhe = window.detalhes[id] || {};
    const conf = tr.querySelector('.conf-check');
    const dup = tr.querySelector('.dup-check');
    if (conf) conf.checked = !!detalhe._conferida;
    if (dup) dup.checked = !!detalhe._duplicada;
    tr.classList.toggle('conferida', !!detalhe._conferida);
    tr.classList.toggle('duplicada', !!detalhe._duplicada);
    const s = document.getElementById('status-' + id);
    if (s) {
      s.textContent = 'erro ao salvar';
      s.classList.add('show');
      setTimeout(() => { s.classList.remove('show'); s.textContent = 'ok'; }, 3500);
    }
    return false;
  });
  filaSalvar[id] = atual;
  return atual;
}
function toggleFormManual() {
  const f = document.getElementById('formManual');
  f.style.display = f.style.display === 'none' ? 'block' : 'none';
}
function salvarManual(e) {
  e.preventDefault();
  const statusEl = document.getElementById('manualStatus');
  statusEl.textContent = 'Salvando...';
  const payload = {
    data: document.getElementById('manualData').value,
    descricao: document.getElementById('manualDescricao').value,
    direcao: document.getElementById('manualDirecao').value,
    valor: document.getElementById('manualValor').value,
    categoria: document.getElementById('manualCategoria').value
  };
  fetch('/api/lancamento-manual', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  }).then(r => r.json()).then(d => {
    if (d.ok) { guardarPosicaoAtual(); window.location.reload(); }
    else { statusEl.textContent = d.erro || 'Falha ao salvar'; }
  }).catch(() => { statusEl.textContent = 'Falha ao salvar'; });
  return false;
}
atualizarChipLabels();
