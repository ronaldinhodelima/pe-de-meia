// Campo de seleção pesquisável do Pé de Meia.
// Mantém o <select> como fonte de verdade para reaproveitar validação,
// salvamento automático e formulários já existentes.
(function () {
  'use strict';

  function normalizar(texto) {
    return String(texto || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR');
  }

  function opcoesDo(select) {
    return Array.from(select.options).map(function (option) {
      return {value: option.value, label: option.textContent.trim(), disabled: option.disabled};
    });
  }

  // Gancho das opcoes sob demanda: a tela que tem seletores com
  // `data-lazy-options` define `window.hidratarSelect`, que monta a lista
  // completa a partir do proprio config. Chamado antes de a lista aparecer,
  // porque `opcoesDo` le as opcoes do <select> - sem isto, a caixa abriria so
  // com a opcao escolhida.
  function hidratarSeNecessario(select) {
    if (typeof window.hidratarSelect === 'function') window.hidratarSelect(select);
  }

  function deveMelhorar(select) {
    if (!select || select.matches('[data-pdm-native], [multiple]')) return false;
    return select.hasAttribute('data-pdm-combobox');
  }

  function melhorarSelect(select) {
    if (!select || select.dataset.comboboxPronto === '1') return;
    select.dataset.comboboxPronto = '1';

    var wrapper = document.createElement('div');
    wrapper.className = 'pdm-combobox';
    ['width', 'minWidth', 'maxWidth'].forEach(function (propriedade) {
      if (select.style[propriedade]) wrapper.style[propriedade] = select.style[propriedade];
    });
    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'pdm-combobox-input';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-label', select.getAttribute('aria-label') || select.dataset.dimensaoNome || 'Selecionar opção');
    var lista = document.createElement('div');
    lista.className = 'pdm-combobox-lista';
    lista.setAttribute('role', 'listbox');
    lista.hidden = true;

    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);
    wrapper.appendChild(input);
    wrapper.appendChild(lista);
    select.classList.add('pdm-select-original');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');

    var filtradas = [];
    var ativo = -1;
    // O usuario mexeu na lista (setas ou filtro) desde que ela abriu? Enquanto
    // nao mexeu, o destaque continua seguindo o valor real do <select>.
    var navegouNaLista = false;

    function selecionada() {
      return select.options[select.selectedIndex] || null;
    }

    function sincronizar() {
      var atual = selecionada();
      input.value = atual ? atual.textContent.trim() : '';
      input.disabled = select.disabled;
      wrapper.classList.toggle('pdm-combobox-disabled', select.disabled);
      wrapper.classList.toggle('classificacao-faltando', select.classList.contains('classificacao-faltando'));
      // O valor pode mudar POR FORA enquanto a lista esta aberta: o Portfolio
      // padrao chega quando a gravacao do Projeto responde, e o usuario ja
      // tabulou para ca antes disso. Sem re-destacar, o campo mostrava
      // "Imoveis" com "(nao definido)" destacado na lista - e o Tab confirma a
      // DESTACADA (secao 7.7), apagando o valor que a regra tinha acabado de
      // preencher.
      realinharDestaque();
    }

    // Poe o destaque na opcao que corresponde ao valor atual do <select>.
    // So mexe quando a lista esta aberta e o usuario ainda nao navegou nem
    // filtrou - navegacao dele tem precedencia sobre o valor de fundo.
    function realinharDestaque() {
      if (lista.hidden || !filtradas.length || navegouNaLista) return;
      var indice = filtradas.findIndex(function (item) { return item.value === select.value; });
      if (indice >= 0 && indice !== ativo) marcarAtivo(indice);
    }

    function fechar(restaurar) {
      lista.hidden = true;
      lista.replaceChildren();
      input.setAttribute('aria-expanded', 'false');
      wrapper.classList.remove('aberto');
      ativo = -1;
      navegouNaLista = false;
      if (restaurar) sincronizar();
    }

    function marcarAtivo(indice) {
      ativo = Math.max(0, Math.min(indice, filtradas.length - 1));
      lista.querySelectorAll('[role="option"]').forEach(function (item, i) {
        item.classList.toggle('ativo', i === ativo);
        item.setAttribute('aria-selected', i === ativo ? 'true' : 'false');
        if (i === ativo) item.scrollIntoView({block: 'nearest'});
      });
    }

    function escolher(item) {
      if (!item || item.disabled) return;
      select.dataset.valorAnterior = select.value;
      select.value = item.value;
      select.dispatchEvent(new Event('change', {bubbles: true}));
      sincronizar();
      fechar(false);
    }

    function renderizar(consulta) {
      hidratarSeNecessario(select);
      var termo = normalizar(consulta);
      filtradas = opcoesDo(select).filter(function (item) {
        return !item.disabled && (!termo || normalizar(item.label).includes(termo));
      });
      lista.replaceChildren();
      filtradas.forEach(function (item, indice) {
        var opcao = document.createElement('div');
        opcao.className = 'pdm-combobox-opcao';
        opcao.setAttribute('role', 'option');
        opcao.textContent = item.label;
        opcao.addEventListener('mousedown', function (evento) {
          evento.preventDefault();
          escolher(item);
        });
        lista.appendChild(opcao);
      });
      if (!filtradas.length) {
        var vazio = document.createElement('div');
        vazio.className = 'pdm-combobox-vazio';
        vazio.textContent = 'Nenhuma opção encontrada';
        lista.appendChild(vazio);
        ativo = -1;
      } else {
        var indiceAtual = filtradas.findIndex(function (item) { return item.value === select.value; });
        marcarAtivo(indiceAtual >= 0 ? indiceAtual : 0);
      }
      lista.hidden = false;
      var caixa = input.getBoundingClientRect();
      lista.style.width = caixa.width + 'px';
      lista.style.left = caixa.left + 'px';
      var altura = Math.min(lista.scrollHeight, 240);
      lista.style.top = (
        window.innerHeight - caixa.bottom < altura + 8 && caixa.top > altura + 8
          ? caixa.top - altura - 4
          : caixa.bottom + 4
      ) + 'px';
      input.setAttribute('aria-expanded', 'true');
      wrapper.classList.add('aberto');
    }

    input.addEventListener('focus', function () {
      if (input.disabled) return;
      select.dataset.valorAnterior = select.value;
      navegouNaLista = false;
      renderizar('');
      input.select();
    });
    input.addEventListener('input', function () { navegouNaLista = true; renderizar(input.value); });
    input.addEventListener('keydown', function (evento) {
      if (evento.key === 'ArrowDown' || evento.key === 'ArrowUp') {
        evento.preventDefault();
        if (lista.hidden) renderizar(input.value);
        navegouNaLista = true;
        if (filtradas.length) marcarAtivo(ativo + (evento.key === 'ArrowDown' ? 1 : -1));
      } else if (evento.key === 'Enter') {
        if (!lista.hidden && ativo >= 0) {
          evento.preventDefault();
          escolher(filtradas[ativo]);
        }
      } else if (evento.key === 'Tab') {
        // Tab confirma o que o usuario DESTACOU (secao 7.7). Se ele apenas
        // passou pelo campo, sem seta e sem filtrar, nao ha escolha para
        // confirmar: gravar aqui transformaria "tabular" em "editar", e era
        // por esse caminho que o Portfolio preenchido pela regra virava
        // "(nao definido)".
        if (!lista.hidden && ativo >= 0 && navegouNaLista) escolher(filtradas[ativo]);
        else fechar(true);
      } else if (evento.key === 'Escape') {
        evento.preventDefault();
        fechar(true);
        input.select();
      }
    });
    select.addEventListener('change', sincronizar);
    select.addEventListener('pdm-combobox-sync', sincronizar);
    new MutationObserver(sincronizar).observe(select, {
      attributes: true, attributeFilter: ['disabled', 'class'], childList: true, subtree: true
    });
    sincronizar();
  }

  function iniciar(raiz) {
    var escopo = raiz || document;
    if (escopo.matches && escopo.matches('select') && deveMelhorar(escopo)) melhorarSelect(escopo);
    if (escopo.querySelectorAll) {
      escopo.querySelectorAll('select').forEach(function (select) {
        if (deveMelhorar(select)) melhorarSelect(select);
      });
    }
  }

  document.addEventListener('mousedown', function (evento) {
    document.querySelectorAll('.pdm-combobox.aberto').forEach(function (wrapper) {
      if (!wrapper.contains(evento.target)) {
        var input = wrapper.querySelector('.pdm-combobox-input');
        var select = wrapper.querySelector('select');
        var atual = select.options[select.selectedIndex];
        wrapper.querySelector('.pdm-combobox-lista').hidden = true;
        wrapper.classList.remove('aberto');
        input.setAttribute('aria-expanded', 'false');
        input.value = atual ? atual.textContent.trim() : '';
      }
    });
  });
  window.addEventListener('scroll', function () {
    document.querySelectorAll('.pdm-combobox.aberto').forEach(function (wrapper) {
      var input = wrapper.querySelector('.pdm-combobox-input');
      var caixa = input.getBoundingClientRect();
      var lista = wrapper.querySelector('.pdm-combobox-lista');
      lista.style.left = caixa.left + 'px';
      lista.style.top = (caixa.bottom + 4) + 'px';
      lista.style.width = caixa.width + 'px';
    });
  }, true);
  document.addEventListener('DOMContentLoaded', function () {
    iniciar(document);
    new MutationObserver(function (mudancas) {
      mudancas.forEach(function (mudanca) {
        if (mudanca.target && mudanca.target.matches && mudanca.target.matches('select')) iniciar(mudanca.target);
        mudanca.addedNodes.forEach(function (no) {
          if (no.nodeType === 1) iniciar(no);
        });
      });
    }).observe(document.body, {childList: true, subtree: true});
  });
  window.pdmCombobox = {
    iniciar: iniciar,
    normalizar: normalizar,
    sincronizar: function (select) {
      if (select) select.dispatchEvent(new Event('pdm-combobox-sync'));
    }
  };
})();
