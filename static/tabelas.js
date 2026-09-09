// Numero escrito como valor monetario ou percentual, ou null quando o texto
// nao e numero. O separador decimal e o ULTIMO '.' ou ',' que aparecer - assim
// funciona tanto no formato ingles (1,234.56) quanto no brasileiro (1.234,56),
// sem depender de qual esta em uso. Publico porque a ordenacao das duas telas
// de lancamentos le o mesmo texto: duas contas divergiriam.
window.pdmNumeroDeTexto = function (texto) {
  const limpo = String(texto == null ? '' : texto).trim().replace(/[R$\s%]/g, '');
  const ultVirgula = limpo.lastIndexOf(',');
  const ultPonto = limpo.lastIndexOf('.');
  const numerico = ultVirgula > ultPonto
    ? limpo.replace(/\./g, '').replace(',', '.')   // decimal e virgula
    : limpo.replace(/,/g, '');                     // decimal e ponto (ou sem decimal)
  if (numerico === '' || numerico === '-' || isNaN(Number(numerico))) return null;
  return Number(numerico);
};

// Escapa texto antes de jogar em innerHTML. Fica aqui por ser compartilhado:
// varias telas montam HTML no cliente a partir de dado vindo do banco.
function escHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[c]);
}

// ---------- colunas ajustaveis (compartilhado por todas as telas) ----------
// Marque a tabela com class="ajustavel" e data-tabela="chave-unica" que o resto
// e automatico: as colunas ganham data-col por indice (se ainda nao tiverem),
// o botao "Redefinir colunas" e injetado acima da tabela e as preferencias de
// ordem/largura/ordenacao ficam no localStorage por chave.
// Opcional: data-sem-ordenar / data-sem-reordenar para tabelas hierarquicas
// (linhas com colspan), onde ordenar ou trocar colunas de lugar nao faz sentido.
function redefinirColunas(chave) {
  localStorage.removeItem('pedemeia_tabela_' + chave);
  guardarPosicaoAtual();
  window.location.reload();
}

// No celular a tabela vira lista de cartoes (secao 7.8-B). Para cada celula
// dizer o que e sem o cabecalho, ela leva o rotulo da propria coluna - lido do
// <th> correspondente, e nao escrito na mao no template: as colunas de dimensao
// sao dinamicas (Responsavel, Projeto, Portfolio vem do banco).
function rotularCelulas(table) {
  const thead = table.querySelector('thead tr');
  if (!thead) return;
  const rotulos = {};
  [...thead.children].forEach(th => {
    const col = th.dataset.col;
    if (col) rotulos[col] = (th.textContent || '').trim();
  });
  table.querySelectorAll('tbody td[data-col]').forEach(td => {
    const texto = rotulos[td.dataset.col];
    if (texto) td.setAttribute('data-rotulo', texto);
  });
}

function ativarTabelaAjustavel(table, chave, opcoes) {
  if (!table) return;
  opcoes = opcoes || {};
  const podeOrdenar = !opcoes.semOrdenar && !table.hasAttribute('data-sem-ordenar');
  const podeReordenar = !opcoes.semReordenar && !table.hasAttribute('data-sem-reordenar');
  const thead = table.querySelector('thead tr');
  if (!thead) return;
  const CHAVE = 'pedemeia_tabela_' + chave;

  // 1) garante data-col em todo th/td (por indice, quando o HTML nao trouxe).
  // Roda antes de aplicar a ordem salva, entao o DOM ainda esta na ordem do servidor.
  const thsOriginais = [...thead.children];
  thsOriginais.forEach((th, i) => { if (!th.dataset.col) th.dataset.col = 'c' + i; });
  const ordemOriginal = thsOriginais.map(th => th.dataset.col);
  table.querySelectorAll('tbody tr').forEach(tr => {
    const tds = [...tr.children];
    // linha com colspan (ex: cabecalho de grupo) tem contagem diferente - fica de fora
    if (tds.length !== thsOriginais.length) return;
    tds.forEach((td, i) => { if (!td.dataset.col) td.dataset.col = ordemOriginal[i]; });
  });

  let estado;
  try { estado = JSON.parse(localStorage.getItem(CHAVE) || '{}'); } catch (e) { estado = {}; }
  // Coluna que nasce escondida (`data-oculta-padrao` no <th>): vale so na
  // PRIMEIRA vez. Depois manda o que o usuario escolheu - reaplicar o padrao a
  // cada carga desfaria a escolha dele em silencio.
  if (!estado.ocultas) {
    estado.ocultas = [...thead.querySelectorAll('th[data-oculta-padrao]')]
      .map(function (th) { return th.dataset.col; })
      .filter(Boolean);
  }
  function salvarEstado() { localStorage.setItem(CHAVE, JSON.stringify(estado)); }
  function colunasNaOrdemAtual() {
    return [...thead.querySelectorAll('th[data-col]')].map(th => th.dataset.col);
  }
  // Com `table-layout: fixed` e largura automatica, o navegador trata as
  // larguras das colunas como PROPORCAO e redistribui dentro do espaco
  // disponivel: alargar uma encolhia as outras e a tabela nunca crescia. Para
  // ela empurrar de verdade, a tabela precisa dizer a propria largura.
  function ajustarLarguraDaTabela() {
    let soma = 0;
    thead.querySelectorAll('th[data-col]').forEach(function (th) {
      if (th.classList.contains('coluna-oculta')) return;
      soma += parseFloat(th.style.width) || th.getBoundingClientRect().width;
    });
    // `!important` porque a regra do CSS tambem e `!important`, e regra com
    // `!important` vence estilo inline SEM ele - a largura era escrita e
    // simplesmente ignorada
    if (soma) table.style.setProperty('width', Math.round(soma) + 'px', 'important');
    else table.style.removeProperty('width');
  }

  function aplicarLargura(col, px) {
    const th = thead.querySelector('th[data-col="' + col + '"]');
    if (th) th.style.width = px + 'px';
    table.querySelectorAll('td[data-col="' + col + '"]').forEach(td => { td.style.width = px + 'px'; });
  }
  // ---- ocultar coluna ----
  // Cada dimensao nova (Responsável, Projeto, Veículo...) vira mais uma coluna, e a
  // tabela chega ao limite da tela rapido. Em vez de limitar quantas dimensoes
  // existem, o usuario esconde as que nao usa no dia a dia - o dado continua la e
  // continua editavel pelo modal de detalhes.
  function aplicarOcultas() {
    const ocultas = estado.ocultas || [];
    colunasNaOrdemAtual().forEach(function (col) {
      const esconder = ocultas.indexOf(col) !== -1;
      // classe, nao style inline: o CSS tem display:table-cell !important nos
      // th[data-col] e o inline perderia - o cabecalho ficava e o corpo sumia
      const th = thead.querySelector('th[data-col="' + col + '"]');
      if (th) th.classList.toggle('coluna-oculta', esconder);
      table.querySelectorAll('td[data-col="' + col + '"]').forEach(function (td) {
        td.classList.toggle('coluna-oculta', esconder);
      });
    });
  }
  function alternarColuna(col, mostrar) {
    estado.ocultas = (estado.ocultas || []).filter(function (c) { return c !== col; });
    if (!mostrar) estado.ocultas.push(col);
    salvarEstado();
    aplicarOcultas();
    ajustarLarguraDaTabela();
    atualizarDicasDeTruncamento(table);   // esconder coluna muda a largura das outras
  }

  function reordenarLinhas() {
    const ordem = colunasNaOrdemAtual();
    table.querySelectorAll('tbody tr').forEach(tr => {
      const mapaTd = {};
      tr.querySelectorAll('td[data-col]').forEach(td => { mapaTd[td.dataset.col] = td; });
      ordem.forEach(col => { if (mapaTd[col]) tr.appendChild(mapaTd[col]); });
    });
  }

  // 2) barra acima da tabela: campo de filtro na esquerda, "Redefinir colunas" na
  //    direita. Injetada automaticamente, uma vez por tabela.
  // A barra guarda closures apontando para a tabela em que foi criada. Quando o
  // filtro AJAX faz replaceWith, a tabela e outra e a barra antiga passaria a
  // controlar um elemento fora do DOM - o menu de colunas parava de funcionar
  // (as colunas ja escondidas continuavam certas, porque a ativacao nova
  // reaplica o estado salvo, mas clicar no menu nao fazia mais nada).
  // Por isso a barra guarda a tabela que serve, e e refeita quando muda.
  // Quando a tabela mora numa caixa que rola de lado, a barra vai ANTES da
  // caixa: dentro dela, "Filtrar" e "Colunas" sairiam da tela junto com as
  // colunas da direita.
  const caixa = table.parentNode && table.parentNode.classList.contains('tabela-scroll')
    ? table.parentNode : table;
  const barraAtual = caixa.previousElementSibling &&
    caixa.previousElementSibling.classList.contains('barra-colunas')
      ? caixa.previousElementSibling : null;
  let filtroAnterior = '';
  if (barraAtual && barraAtual.__tabela !== table) {
    const campoAntigo = barraAtual.querySelector('.filtro-tabela');
    if (campoAntigo) filtroAnterior = campoAntigo.value;   // nao perde o que foi digitado
    barraAtual.remove();
  }

  if (!caixa.previousElementSibling || !caixa.previousElementSibling.classList.contains('barra-colunas')) {
    const barra = document.createElement('div');
    barra.className = 'barra-colunas';

    const esq = document.createElement('div');
    esq.className = 'barra-colunas-esq';
    const externa = table.dataset.buscaExterna
      ? document.querySelector(table.dataset.buscaExterna) : null;
    let busca = null, contador = null;
    if (externa) {
      externa.querySelectorAll('input, span').forEach(function (el) { esq.appendChild(el); });
      const campo = esq.querySelector('input');
      if (campo) {
        campo.classList.add('filtro-tabela', 'campo-caixa');
        campo.placeholder = 'Filtrar';
        campo.setAttribute('aria-label', 'Filtrar');
      }
      externa.remove();
    } else {
      busca = document.createElement('input');
      busca.type = 'search';
      busca.className = 'filtro-tabela campo-caixa';
      busca.placeholder = 'Filtrar';
      busca.setAttribute('aria-label', 'Filtrar');
      contador = document.createElement('span');
      contador.className = 'barra-colunas-contador';
      esq.appendChild(busca);
      esq.appendChild(contador);
    }

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ver-btn';
    btn.title = 'Volta a ordem, largura, ordenação e visibilidade das colunas ao padrão';
    btn.textContent = '↺ Redefinir';
    btn.addEventListener('click', function () { redefinirColunas(chave); });

    const dir = document.createElement('div');
    dir.style.cssText = 'display:flex;gap:8px;align-items:center';
    // tabela hierarquica tem linha com colspan, que nao acompanha coluna escondida
    if (podeReordenar) dir.appendChild(menuColunas());
    dir.appendChild(btn);

    barra.appendChild(esq);
    barra.appendChild(dir);
    barra.__tabela = table;
    caixa.parentNode.insertBefore(barra, caixa);
    if (busca) {
      ativarFiltroTabela(table, busca, contador);
      if (filtroAnterior) {
        busca.value = filtroAnterior;
        busca.dispatchEvent(new Event('input'));
      }
    }
  }
  atualizarTotaisVisiveis(table);

  function menuColunas() {
    const caixa = document.createElement('div');
    // classe propria, NAO 'chipfilter': aquela e varrida por atualizarChipLabels()
    // e por coletarQuery(), que esperam um .chip-btn e checkboxes com name. O menu
    // de colunas nao tem nem um nem outro - com a classe errada, o filtro de
    // origem parava de funcionar (TypeError antes do fetch).
    caixa.className = 'menu-colunas';
    const painel = document.createElement('div');
    painel.className = 'chip-panel';
    const abrir = document.createElement('button');
    abrir.type = 'button';
    abrir.className = 'ver-btn';
    abrir.textContent = '☰ Colunas';
    abrir.title = 'Escolher quais colunas aparecem';
    // toggle proprio: cfToggle() mora em lancamentos.js/relatorios.js e nao
    // existe nas demais telas, onde este menu tambem aparece
    abrir.addEventListener('click', function (e) {
      e.stopPropagation();
      const aberto = painel.classList.contains('show');
      document.querySelectorAll('.chip-panel.show').forEach(function (p) { p.classList.remove('show'); });
      if (!aberto) painel.classList.add('show');
    });
    document.addEventListener('click', function (e) {
      if (!caixa.contains(e.target)) painel.classList.remove('show');
    });

    const lista = document.createElement('div');
    lista.className = 'chip-list';
    colunasNaOrdemAtual().forEach(function (col) {
      const th = thead.querySelector('th[data-col="' + col + '"]');
      // coluna sem titulo (a de status, no fim da tabela) nao entra no menu: nao
      // ha rotulo para mostrar, e esconde-la nao ajuda em nada
      const rotulo = th ? th.textContent.trim() : '';
      if (!rotulo) return;
      const item = document.createElement('label');
      item.className = 'chip-opt';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = (estado.ocultas || []).indexOf(col) === -1;
      cb.addEventListener('change', function () { alternarColuna(col, cb.checked); });
      item.appendChild(cb);
      item.appendChild(document.createTextNode(' ' + rotulo));
      lista.appendChild(item);
    });
    painel.appendChild(lista);
    caixa.appendChild(abrir);
    caixa.appendChild(painel);
    return caixa;
  }

  // 3) ordem salva
  if (podeReordenar && estado.ordem && estado.ordem.length) {
    const mapaTh = {};
    thead.querySelectorAll('th[data-col]').forEach(th => { mapaTh[th.dataset.col] = th; });
    estado.ordem.forEach(col => { if (mapaTh[col]) thead.appendChild(mapaTh[col]); });
    reordenarLinhas();
  }

  // 4) larguras normalizadas para caber exatamente no container (sem rolagem).
  // Medir a propria tabela nao serve: com table-layout:fixed ela ja estoura pra
  // caber a soma das colunas, entao o alvo sairia errado. O pai nao estoura.
  const temLarguraSalva = !!(estado.larguras && Object.keys(estado.larguras).length);
  if (temLarguraSalva) {
    // o que o usuario escolheu vale como escolhido, sem reescala
    Object.keys(estado.larguras).forEach(col => aplicarLargura(col, estado.larguras[col]));
    ajustarLarguraDaTabela();
  } else {
    const larguraBase = {};
    thead.querySelectorAll('th[data-col]').forEach(th => {
      larguraBase[th.dataset.col] = th.getBoundingClientRect().width;
    });
    const soma = Object.values(larguraBase).reduce((a, b) => a + b, 0);
    const alvo = table.parentElement.clientWidth;
    if (soma > 0 && alvo > 0) {
      const fator = alvo / soma;
      Object.keys(larguraBase).forEach(col => aplicarLargura(col, Math.max(40, larguraBase[col] * fator)));
    }
  }

  // 5) redimensionar: arrastar tira/da espaco da coluna vizinha (soma constante)
  let redimensionandoAgora = false;
  thead.querySelectorAll('th[data-col]').forEach(th => {
    if (th.querySelector('.col-resize-handle')) return;
    const alca = document.createElement('span');
    alca.className = 'col-resize-handle';
    alca.draggable = false;
    th.appendChild(alca);
    alca.addEventListener('click', function (e) { e.stopPropagation(); });
    alca.addEventListener('mousedown', function (e) {
      e.preventDefault();
      e.stopPropagation();
      redimensionandoAgora = true;
      const startX = e.clientX;
      const larguraInicial = th.getBoundingClientRect().width;
      // fixa a largura ATUAL de todas as colunas antes de mexer: sem isso, com
      // table-layout:fixed, as que nao tinham largura propria se redistribuem
      // sozinhas e a tabela inteira "anda" ao arrastar uma so
      thead.querySelectorAll('th[data-col]').forEach(function (outro) {
        if (!outro.style.width) aplicarLargura(outro.dataset.col, outro.getBoundingClientRect().width);
      });
      function mover(e2) {
        const nova = larguraInicial + (e2.clientX - startX);
        if (nova < 40) return;
        aplicarLargura(th.dataset.col, nova);
        ajustarLarguraDaTabela();
      }
      function soltar() {
        document.removeEventListener('mousemove', mover);
        document.removeEventListener('mouseup', soltar);
        estado.larguras = estado.larguras || {};
        // grava TODAS: as outras foram fixadas no inicio do arrasto, e sem
        // gravar elas a proxima carga recalcularia larguras diferentes
        thead.querySelectorAll('th[data-col]').forEach(function (outro) {
          estado.larguras[outro.dataset.col] = outro.getBoundingClientRect().width;
        });
        ajustarLarguraDaTabela();
        salvarEstado();
        // a coluna mudou de largura: o que cabia pode ter passado a nao caber
        atualizarDicasDeTruncamento(table);
        // rede de seguranca: normalmente a trava e consumida pelo handler de click
        // do th (a alca se move junto com a coluna, entao o click pode cair fora dela)
        setTimeout(function () { redimensionandoAgora = false; }, 300);
      }
      document.addEventListener('mousemove', mover);
      document.addEventListener('mouseup', soltar);
    });
  });

  // 6) reordenar arrastando o cabecalho
  if (podeReordenar) {
    let arrastando = null;
    thead.querySelectorAll('th[data-col]').forEach(th => {
      th.draggable = true;
      th.addEventListener('dragstart', function () { arrastando = th; th.classList.add('arrastando'); });
      th.addEventListener('dragend', function () {
        th.classList.remove('arrastando');
        thead.querySelectorAll('th[data-col]').forEach(t => t.classList.remove('arrastar-sobre'));
      });
      th.addEventListener('dragover', function (e) {
        e.preventDefault();
        if (th !== arrastando) th.classList.add('arrastar-sobre');
      });
      th.addEventListener('dragleave', function () { th.classList.remove('arrastar-sobre'); });
      th.addEventListener('drop', function (e) {
        e.preventDefault();
        th.classList.remove('arrastar-sobre');
        if (!arrastando || arrastando === th) return;
        const rect = th.getBoundingClientRect();
        const antes = (e.clientX - rect.left) < rect.width / 2;
        th.parentNode.insertBefore(arrastando, antes ? th : th.nextSibling);
        reordenarLinhas();
        estado.ordem = colunasNaOrdemAtual();
        salvarEstado();
      });
    });
  }

  // 7) ordenar clicando no titulo
  if (podeOrdenar) {
    function valorOrdenavel(td) {
      if (!td) return '';
      if (td.dataset.sort !== undefined && td.dataset.sort !== '') return parseFloat(td.dataset.sort);
      const sel = td.querySelector('select');
      if (sel) return (sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : '').toLowerCase();
      const inp = td.querySelector('input[type=text]');
      if (inp) return inp.value.toLowerCase();
      const txt = td.textContent.trim();
      const numero = window.pdmNumeroDeTexto(txt);
      if (numero !== null) return numero;
      return txt.toLowerCase();
    }
    function ordenarLinhas(col, dir) {
      const tbody = table.querySelector('tbody');
      if (!tbody) return;
      const todas = [...tbody.querySelectorAll('tr')];
      const linhas = todas.filter(function (tr) {
        return !tr.dataset.rateioParent && !tr.dataset.tecnicoParent;
      });
      linhas.sort(function (a, b) {
        const va = valorOrdenavel(a.querySelector('td[data-col="' + col + '"]'));
        const vb = valorOrdenavel(b.querySelector('td[data-col="' + col + '"]'));
        const cmp = (typeof va === 'number' && typeof vb === 'number') ? va - vb : String(va).localeCompare(String(vb));
        return dir === 'asc' ? cmp : -cmp;
      });
      // Filhos de rateio e registros tecnicos permanecem imediatamente abaixo
      // do seu lancamento principal, mesmo depois de ordenar a tabela.
      linhas.forEach(function (tr) {
        tbody.appendChild(tr);
        const id = tr.dataset.id;
        if (!id) return;
        todas.filter(function (filha) {
          return filha.dataset.rateioParent === id || filha.dataset.tecnicoParent === id;
        }).forEach(function (filha) { tbody.appendChild(filha); });
      });
    }
    function atualizarIndicadores() {
      thead.querySelectorAll('th[data-col]').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        if (estado.sort && estado.sort.col === th.dataset.col) {
          th.classList.add(estado.sort.dir === 'asc' ? 'sort-asc' : 'sort-desc');
        }
      });
    }
    thead.querySelectorAll('th[data-col]').forEach(th => {
      th.addEventListener('click', function (e) {
        if (redimensionandoAgora) { redimensionandoAgora = false; return; }
        if (e.target.classList.contains('col-resize-handle')) return;
        const col = th.dataset.col;
        const dir = (estado.sort && estado.sort.col === col && estado.sort.dir === 'asc') ? 'desc' : 'asc';
        estado.sort = { col: col, dir: dir };
        salvarEstado();
        ordenarLinhas(col, dir);
        atualizarIndicadores();
      });
    });
    if (estado.sort) ordenarLinhas(estado.sort.col, estado.sort.dir);
    atualizarIndicadores();
  }

  // por ultimo: a preferencia de coluna escondida vale desde o carregamento
  aplicarOcultas();
  atualizarDicasDeTruncamento(table);
  rotularCelulas(table);
}

// ativa sozinho toda tabela marcada com class="ajustavel" e data-tabela="chave"
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('table.ajustavel[data-tabela]').forEach(function (t) {
    ativarTabelaAjustavel(t, t.dataset.tabela);
  });
});

// ---- dica automatica no que ficou cortado ----
// A descricao ja tinha data-tip vindo do servidor. Aqui a mesma coisa vale para
// qualquer celula, campo ou seletor cujo conteudo nao caiba na largura atual -
// inclusive depois de o usuario redimensionar ou esconder uma coluna.
//
// Só marca o que está REALMENTE cortado: dica em texto que ja aparece inteiro
// vira ruido. Por isso mede, em vez de marcar tudo.
let _reguaTexto = null;
function larguraDoTexto(texto, estilo) {
  if (!_reguaTexto) {
    _reguaTexto = document.createElement('span');
    _reguaTexto.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;left:-9999px;top:0';
    document.body.appendChild(_reguaTexto);
  }
  _reguaTexto.style.font = estilo.font;
  _reguaTexto.style.letterSpacing = estilo.letterSpacing;
  _reguaTexto.textContent = texto;
  return _reguaTexto.offsetWidth;
}

function marcarDica(el, texto) {
  if (!texto) return;
  el.setAttribute('data-tip', texto);
  el.setAttribute('data-tip-auto', '1');   // marca as nossas, para poder tirar depois
}
function limparDicaAuto(el) {
  if (el.getAttribute('data-tip-auto')) {
    el.removeAttribute('data-tip');
    el.removeAttribute('data-tip-auto');
  }
}

function atualizarDicasDeTruncamento(table) {
  table.querySelectorAll('tbody td[data-col]').forEach(function (td) {
    // celula de texto puro: o proprio scrollWidth denuncia o corte
    if (!td.querySelector('input, select')) {
      // nao mexe em data-tip vindo do servidor (descricao, data, origem)
      if (!td.getAttribute('data-tip') || td.getAttribute('data-tip-auto')) {
        const cortado = td.scrollWidth > td.clientWidth + 1;
        cortado ? marcarDica(td, td.textContent.trim()) : limparDicaAuto(td);
      }
      return;
    }
    td.querySelectorAll('input[type=text], select').forEach(function (campo) {
      const estilo = window.getComputedStyle(campo);
      const texto = campo.tagName === 'SELECT'
        ? ((campo.options[campo.selectedIndex] || {}).textContent || '').trim()
        : campo.value;
      if (!texto) { limparDicaAuto(campo); return; }
      const reservado = parseFloat(estilo.paddingLeft) + parseFloat(estilo.paddingRight) +
        (campo.tagName === 'SELECT' ? 22 : 2);   // o seletor ainda gasta espaco com a seta
      const cabe = larguraDoTexto(texto, estilo) <= campo.clientWidth - reservado;
      cabe ? limparDicaAuto(campo) : marcarDica(campo, texto);
    });
  });
}

// ---- ESC fecha qualquer modal aberto (compartilhado por todas as telas) ----
// Todas as telas usam a mesma marcacao .modal-bg + classe .show, entao um unico
// handler cobre os detalhes do lancamento, a lista de lancamentos da categoria e
// qualquer modal que venha depois. Cada tela limpa o proprio estado em
// window.aoFecharModal (ex: o id do lancamento que estava aberto).
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  const abertos = document.querySelectorAll('.modal-bg.show');
  if (!abertos.length) return;
  abertos.forEach(function (m) { m.classList.remove('show'); });
  if (typeof window.aoFecharModal === 'function') window.aoFecharModal();
});

// ---- manter a posicao da pagina ao salvar ----
// Quase toda tela de cadastro reenvia o formulario e a view devolve a pagina
// inteira (nao ha redirect). Para o navegador e um documento novo, e documento
// novo abre no topo - quem estava editando um item la embaixo perde o lugar a
// cada alteracao.
//
// Guardamos a posicao no sessionStorage e voltamos para ela quando a pagina nova
// chega. Ativa sozinho em todas as telas (ver o final do arquivo), entao tela
// nova ja nasce com o comportamento certo.
const POS_CHAVE = 'pedemeia_pos_' + location.pathname;
const POS_VALIDADE_MS = 15000;

// Chame antes de um window.location.reload() feito por JS: recarregar por codigo
// nao dispara o evento submit, entao a posicao nao seria guardada sozinha.
function guardarPosicaoAtual() {
  try {
    sessionStorage.setItem(POS_CHAVE, JSON.stringify({
      y: window.scrollY,
      // o <details> de ajuda fica no topo de varias telas: se voltasse fechado,
      // tudo abaixo subiria e a rolagem cairia no lugar errado
      abertos: Array.from(document.querySelectorAll('details')).map(function (d) { return d.open; }),
      em: Date.now(),
    }));
  } catch (e) { /* sessionStorage indisponivel: so perde a posicao */ }
}

function manterPosicaoAoSalvar() {
  document.addEventListener('submit', guardarPosicaoAtual);

  let estado = null;
  try {
    estado = JSON.parse(sessionStorage.getItem(POS_CHAVE) || 'null');
    sessionStorage.removeItem(POS_CHAVE);
  } catch (e) { return; }
  if (!estado) return;
  // envio cancelado (confirm recusado) deixa a posicao guardada sem navegacao
  // nenhuma - sem esta checagem, a proxima visita a tela daria um pulo sozinho
  if (Date.now() - (estado.em || 0) > POS_VALIDADE_MS) return;

  document.querySelectorAll('details').forEach(function (d, i) {
    if ((estado.abertos || [])[i]) d.open = true;
  });
  // espera o layout assentar (larguras de coluna sao aplicadas por JS) antes de rolar
  requestAnimationFrame(function () { window.scrollTo(0, estado.y || 0); });
}

document.addEventListener('DOMContentLoaded', manterPosicaoAoSalvar);


// Texto de uma opcao de chip SEM o contador de lancamentos. O numero mora dentro
// do span de texto para fluir junto da frase, entao textContent o inclui - e ele
// nao pode vazar para o chip pequeno de selecionado nem para a busca do painel.
function textoDaOpcao(label) {
  const clone = label.cloneNode(true);
  clone.querySelectorAll('.chip-qtd').forEach(function (q) { q.remove(); });
  return clone.textContent.trim();
}

// ---- filtro de conteudo da tabela ----
// Filtra no cliente: esconde as linhas que nao casam, sem ida ao servidor. So
// enxerga o que ja esta carregado na tela - em Lancamentos, por exemplo, filtra
// dentro do mes aberto, nao no historico inteiro.
function normalizarBusca(texto) {
  return String(texto == null ? '' : texto)
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();
}

// Texto que representa a linha para efeito de busca. Nao da para usar o
// textContent puro: as celulas trazem <select> cujas opcoes listam TODAS as
// categorias, entao qualquer busca casaria com todas as linhas. Aqui as opcoes
// sao descartadas e entram, no lugar, o valor dos campos e a opcao escolhida.
function textoFiltravelDaLinha(tr) {
  if (tr.__textoFiltro != null) return tr.__textoFiltro;
  const partes = [];
  tr.querySelectorAll('td').forEach(function (td) {
    const clone = td.cloneNode(true);
    clone.querySelectorAll('select').forEach(function (s) { s.remove(); });
    partes.push(clone.textContent);
  });
  tr.querySelectorAll('input').forEach(function (i) {
    if (['checkbox', 'radio', 'hidden'].indexOf(i.type) === -1) partes.push(i.value);
  });
  tr.querySelectorAll('select').forEach(function (s) {
    const opt = s.options[s.selectedIndex];
    if (opt) partes.push(opt.textContent);
  });
  tr.__textoFiltro = normalizarBusca(partes.join(' '));
  return tr.__textoFiltro;
}

// Valor em portugues. Mora aqui porque as duas telas de lancamentos e a barra
// de lote precisam do mesmo formato - o toLocaleString solto em cada arquivo
// divergiria na primeira opcao diferente.
function pdmMoeda(v) {
  const n = Number(v) || 0;
  return (n < 0 ? '- R$ ' : 'R$ ') + Math.abs(n).toLocaleString('pt-BR', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}
window.pdmMoeda = pdmMoeda;

// Rodape: quantidade e soma do que esta A VISTA. Le `data-valor` da <tr>, que o
// servidor ja escreve - somar o texto da celula dependeria da coluna Valor
// existir e de ela nao estar oculta.
function atualizarTotaisVisiveis(table) {
  const rodape = table && table.querySelector('[data-total-rodape]');
  if (!rodape) return;
  let n = 0, soma = 0;
  table.querySelectorAll('tbody tr[data-valor]').forEach(function (tr) {
    if (tr.hidden || tr.style.display === 'none') return;
    n++;
    soma += parseFloat(tr.dataset.valor) || 0;
  });
  const qtd = rodape.querySelector('[data-total-qtd]');
  const val = rodape.querySelector('[data-total-valor]');
  if (qtd) qtd.textContent = n === 1 ? '1 lançamento' : n + ' lançamentos';
  if (val) val.textContent = pdmMoeda(soma);
}
window.atualizarTotaisVisiveis = atualizarTotaisVisiveis;

function ativarFiltroTabela(table, campo, contador) {
  const corpo = table.tBodies[0];
  if (!corpo) return;
  // tabela hierarquica (Centro de Custos): a linha com colspan abre um bloco e as
  // seguintes sao filhas dela. Esconder linha solta quebraria a arvore - um
  // subgrupo apareceria sem o centro de custo dele.
  const hierarquica = !!corpo.querySelector('tr > td[colspan]');

  // valor editado invalida o texto guardado daquela linha
  table.addEventListener('input', function (e) {
    const tr = e.target.closest('tr');
    if (tr) tr.__textoFiltro = null;
  });
  table.addEventListener('change', function (e) {
    const tr = e.target.closest('tr');
    if (tr) tr.__textoFiltro = null;
  });

  function aplicar() {
    const q = normalizarBusca(campo.value).trim();
    const dados = Array.from(corpo.rows);

    if (!q) {
      dados.forEach(function (tr) { tr.style.display = ''; });
      contador.textContent = '';
      atualizarTotaisVisiveis(table);
      return;
    }

    const casa = dados.map(function (tr) { return textoFiltravelDaLinha(tr).indexOf(q) !== -1; });
    const visivel = casa.slice();

    if (hierarquica) {
      let inicioBloco = -1;
      dados.forEach(function (tr, i) {
        const ehCabecalho = !!tr.querySelector(':scope > td[colspan]');
        if (ehCabecalho) inicioBloco = i;
        // filho que casa traz o cabecalho do bloco junto
        if (!ehCabecalho && casa[i] && inicioBloco >= 0) visivel[inicioBloco] = true;
      });
      // cabecalho que casa mostra o bloco inteiro
      let atual = -1;
      dados.forEach(function (tr, i) {
        const ehCabecalho = !!tr.querySelector(':scope > td[colspan]');
        if (ehCabecalho) atual = casa[i] ? i : -1;
        else if (atual >= 0) visivel[i] = true;
      });
    }

    let n = 0;
    dados.forEach(function (tr, i) {
      tr.style.display = visivel[i] ? '' : 'none';
      if (visivel[i]) n++;
    });
    contador.textContent = n + ' de ' + dados.length;
    atualizarTotaisVisiveis(table);
  }

  campo.addEventListener('input', aplicar);
  campo.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { campo.value = ''; aplicar(); e.stopPropagation(); }
  });
}

// Filtro em chip (o dropdown de Origem, de Categoria...). Mora aqui porque
// TODAS as telas que usam chip_filter_html precisam dele - estava copiado em
// lancamentos.js e relatorios.js, e as duas copias ja tinham divergido: so uma
// fechava o painel depois de aplicar. O que aplica o filtro e o `onchange` que
// cada tela declara no proprio checkbox; aqui a gente so dispara o evento.
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
document.addEventListener('change', function (e) {
  const painel = e.target.closest && e.target.closest('.chip-panel');
  if (!painel || e.target.type !== 'checkbox') return;
  // fecha DEPOIS de aplicar, e so quando a escolha ja foi registrada: fechar
  // antes tiraria da tela a lista que o usuario esta conferindo
  setTimeout(function () { painel.classList.remove('show'); }, 120);
});

function cfClear(e, btn) {
  e.stopPropagation();
  const panel = btn.closest('.chipfilter').querySelector('.chip-panel');
  const marcados = panel.querySelectorAll('input[type=checkbox]');
  marcados.forEach(cb => cb.checked = false);
  if (marcados[0]) marcados[0].dispatchEvent(new Event('change', {bubbles: true}));
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
      cb.dispatchEvent(new Event('change', {bubbles: true}));
    }
  } else if (e.key === 'Escape') {
    panel.classList.remove('show');
  }
}
