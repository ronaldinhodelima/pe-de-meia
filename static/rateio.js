// Edicao das partes de um rateio, nas linhas abaixo do lancamento pai.
//
// Compartilhado pelas duas telas de Lancamentos pelo mesmo motivo do lote.js:
// quando o comportamento e o mesmo, o codigo tem que ser o mesmo - duas
// implementacoes divergem na primeira regra nova (secao 7.1). O que muda entre
// as telas e so COMO se acha a linha pai e o que se repinta nela; isso entra
// por configuracao, nao por copia.
//
// Nao existe segundo caminho de gravacao: tudo passa pelo mesmo
// POST /api/transacao/<id>/rateios de sempre, com as mesmas validacoes e a
// mesma auditoria.
(function () {
  let ctx = {
    // a linha do lancamento pai, a partir do id da transacao
    paiDe: function (id) { return document.querySelector('tr[data-id="' + id + '"]'); },
    config: function () { return window.configLancamentos || {}; },
    // repintura propria da tela (o OK do pai, o resumo "Rateado R$ X de R$ Y")
    aoValidar: function () {},
    // o que fazer depois de gravar. Sem padrao que recarregue: recarregar sem
    // guardar a posicao joga quem estava no meio da lista de volta ao topo,
    // e cada tela guarda a posicao do seu jeito.
    aposSalvar: function () {},
  };

  function configurar(opcoes) { ctx = Object.assign({}, ctx, opcoes || {}); }

  function linhasDe(id) {
    return Array.from(document.querySelectorAll(
      'tr.rateio-row[data-rateio-parent="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]'));
  }

  function ler(id) {
    return linhasDe(id).map(function (linha) {
      const dimensoes = {};
      linha.querySelectorAll('.rateio-dim-select').forEach(function (sel) {
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

  // As partes somam EXATAMENTE o valor do pai, em centavos (secao 4.4). Somar
  // em ponto flutuante deixaria passar diferenca de centavo por arredondamento.
  function validar(id) {
    const pai = ctx.paiDe(id);
    const partes = ler(id);
    const totalCentavos = Math.round(Number(pai && pai.dataset.rateioTotal || 0) * 100);
    const obrigatorias = ctx.config().dimensoes_obrigatorias || [];
    let somaCentavos = 0;
    let camposValidos = partes.length >= 2;
    partes.forEach(function (parte) {
      const valor = Number(String(parte.valor || '').replace(',', '.'));
      if (!Number.isFinite(valor) || valor <= 0 || !parte.categoria) camposValidos = false;
      else somaCentavos += Math.round(valor * 100);
      obrigatorias.forEach(function (dimId) {
        if (!(parte.dimensoes || {})[dimId]) camposValidos = false;
      });
    });
    return {
      valido: camposValidos && somaCentavos === totalCentavos,
      somaCentavos: somaCentavos,
      totalCentavos: totalCentavos,
    };
  }

  function atualizar(id, alterado) {
    const pai = ctx.paiDe(id);
    if (!pai) return false;
    const estado = validar(id);
    linhasDe(id).forEach(function (linha) {
      if (alterado) linha.classList.add('rateio-alterado');
      linha.classList.toggle('rateio-invalido', !estado.valido);
      const valor = linha.querySelector('.rateio-valor-inline');
      if (valor) valor.classList.toggle('invalido', !estado.valido);
      const botao = linha.querySelector('.rateio-salvar-inline');
      if (botao) botao.disabled = !ctx.config().pode_editar || !estado.valido;
    });
    pai.dataset.rateioValido = estado.valido ? '1' : '0';
    ctx.aoValidar(id, estado, pai);
    return estado.valido;
  }

  // Ponto unico do POST/DELETE de rateio. Todo caminho da interface passa por
  // aqui - a edicao nas linhas, a criacao pelo quadro e o desfazer. Um segundo
  // fetch para o mesmo endpoint comecaria igual e divergiria na primeira regra
  // nova (secao 7.2-A).
  function gravar(id, corpo) {
    const opcoes = corpo === null
      ? {method: 'DELETE'}
      : {method: 'POST', headers: {'Content-Type': 'application/json'},
         body: JSON.stringify(corpo)};
    return fetch('/api/transacao/' + encodeURIComponent(id) + '/rateios', opcoes)
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.erro || 'Não foi possível salvar o rateio.');
        return res;
      });
  }

  function salvarPartes(id, partes) { return gravar(id, {partes: partes}); }
  function remover(id) { return gravar(id, null); }

  function salvar(id) {
    if (!atualizar(id, false)) {
      alert('Ajuste as partes até a soma fechar exatamente o valor do lançamento e preencha os campos obrigatórios.');
      return;
    }
    const linhas = linhasDe(id);
    linhas.forEach(function (linha) {
      const botao = linha.querySelector('.rateio-salvar-inline');
      if (botao) { botao.disabled = true; botao.textContent = '…'; }
    });
    // o clique no ✓ e a acao humana: com o rateio completo, ele assina o OK do
    // lancamento pai (secao 4.4). O servidor confere as condicoes e nunca
    // sobrescreve assinatura existente.
    gravar(id, {partes: ler(id), conferir: !!ctx.config().pode_conferir})
    .then(function (res) {
      if (window.pdmToastAposRecarregar) {
        // esta acao recarrega a pagina: a confirmacao precisa sobreviver a ela
        window.pdmToastAposRecarregar(
          res.conferida ? 'Rateio salvo e lançamento conferido' : 'Rateio salvo');
      }
      ctx.aposSalvar(id, res);
    }).catch(function (e) {
      linhas.forEach(function (linha) {
        const botao = linha.querySelector('.rateio-salvar-inline');
        if (botao) { botao.disabled = false; botao.textContent = '✓'; }
      });
      alert(e.message || 'Não foi possível salvar o rateio.');
    });
  }

  // ---- quadro de criacao / alteracao do conjunto --------------------------
  // Mudar QUANTAS partes existem nao cabe nas linhas: e uma acao sobre o
  // lancamento, nao a edicao de um campo. O quadro nasceu dentro do modal da
  // Resumida; aqui ele fica independente de onde e desenhado, para servir
  // tambem ao painel da Detalhada.
  const rascunhos = {};

  function opcoes(lista, atual, vazio) {
    const esc = window.escHtml || function (t) { return String(t == null ? '' : t); };
    let html = vazio ? '<option value="">' + esc(vazio) + '</option>' : '';
    (lista || []).forEach(function (item) {
      const valor = String(item.valor !== undefined ? item.valor : item.id);
      const rotulo = item.rotulo !== undefined ? item.rotulo : item.nome;
      html += '<option value="' + esc(valor) + '"'
        + (String(atual || '') === valor ? ' selected' : '') + '>' + esc(rotulo) + '</option>';
    });
    return html;
  }

  function lerQuadro(box) {
    return Array.from(box.querySelectorAll('.modal-rateio-parte')).map(function (parte) {
      const dimensoes = {};
      parte.querySelectorAll('.rateio-dim').forEach(function (s) {
        dimensoes[s.dataset.dim] = s.value || null;
      });
      return {
        valor: parte.querySelector('.rateio-valor').value,
        categoria: parte.querySelector('.rateio-categoria').value,
        observacao: parte.querySelector('.rateio-observacao').value,
        dimensoes: dimensoes,
      };
    });
  }

  // A soma tem que fechar EXATAMENTE o valor do lancamento (secao 4.4); em
  // centavos, nunca em ponto flutuante.
  function conferirQuadro(box, total) {
    const soma = Array.from(box.querySelectorAll('.rateio-valor'))
      .reduce(function (n, i) { return n + Number(i.value || 0); }, 0);
    const fecha = Math.round(soma * 100) === Math.round(total * 100);
    const aviso = box.querySelector('.rateio-fechamento');
    if (aviso) {
      aviso.textContent = fecha ? ''
        : 'Rateado ' + window.pdmMoedaBr(soma) + ' de ' + window.pdmMoedaBr(total);
      aviso.classList.toggle('invalido', !fecha);
    }
    box.classList.toggle('rateio-invalido', !fecha);
    box.querySelectorAll('.rateio-valor').forEach(function (input) {
      input.classList.toggle('invalido', !fecha);
    });
    const salvar = box.querySelector('[data-rateio-salvar]');
    if (salvar) salvar.disabled = !fecha;
    return fecha;
  }

  function montarQuadro(box, opcoesQuadro) {
    const cfg = ctx.config();
    const id = opcoesQuadro.id;
    const total = Number(opcoesQuadro.total || 0);
    const existentes = opcoesQuadro.existentes || [];
    const esc = window.escHtml || function (t) { return String(t == null ? '' : t); };
    box.classList.remove('rateio-invalido');
    if (rascunhos[id] === undefined && existentes.length) {
      rascunhos[id] = existentes.map(function (p) {
        return {valor: p.valor, categoria: p.categoria,
                observacao: p.observacao || '', dims: Object.assign({}, p.dims || {})};
      });
    }
    if (rascunhos[id] === undefined) {
      box.innerHTML = '<div class="modal-rateio-topo"><strong>Rateio do lançamento</strong>'
        + '<button type="button" class="ver-btn" data-rateio-iniciar>Dividir em 2 partes</button></div>';
      box.querySelector('[data-rateio-iniciar]').addEventListener('click', function () {
        // parte de uma divisao pela metade, herdando a classificacao que o
        // lancamento ja tem: e o ponto de partida, e continua editavel
        const centavos = Math.round(total * 100);
        const primeira = Math.floor(centavos / 2);
        const base = {categoria: opcoesQuadro.categoria || '',
                      dims: Object.assign({}, opcoesQuadro.dims || {})};
        rascunhos[id] = [
          {valor: primeira / 100, observacao: '', categoria: base.categoria, dims: Object.assign({}, base.dims)},
          {valor: (centavos - primeira) / 100, observacao: '', categoria: base.categoria, dims: Object.assign({}, base.dims)},
        ];
        montarQuadro(box, opcoesQuadro);
      });
      return;
    }
    const categorias = (cfg.categorias || []).map(function (c) {
      return {valor: c.chave, rotulo: c.nome};
    });
    let partesHtml = '';
    rascunhos[id].forEach(function (p, indice) {
      let dimsHtml = '';
      Object.entries(cfg.dimensoes || {}).forEach(function (par) {
        const dimId = par[0], valores = par[1];
        const nome = (cfg.dimensoes_nomes || {})[dimId] || 'Dimensão';
        dimsHtml += '<div class="row"><span>' + esc(nome) + '</span><span>'
          + '<select data-pdm-combobox class="rateio-dim" aria-label="' + esc(nome) + '" data-dim="' + esc(dimId) + '">'
          + opcoes(valores, (p.dims || {})[dimId], '(não definido)') + '</select></span></div>';
      });
      partesHtml += '<div class="modal-rateio-parte" data-indice="' + indice + '">'
        + '<div class="rateio-parte-titulo"><span>Parte ' + (indice + 1) + '</span><strong>'
        + (rascunhos[id].length > 2 ? '<button type="button" class="ver-btn" data-rateio-remover="' + indice + '">Remover</button>' : '')
        + '</strong></div>'
        + '<div class="row"><span>Valor (R$)</span><span><input class="rateio-valor" type="number" min="0.01" step="0.01" aria-label="Valor da parte ' + (indice + 1) + '" value="' + Number(p.valor || 0).toFixed(2) + '"></span></div>'
        + '<div class="row"><span>Categoria</span><span><select data-pdm-combobox aria-label="Categoria" class="rateio-categoria">' + opcoes(categorias, p.categoria, '(sem categoria)') + '</select></span></div>'
        + dimsHtml
        + '<div class="row"><span>Observação</span><span><input class="rateio-observacao" aria-label="Observação" maxlength="500" value="' + esc(p.observacao || '') + '"></span></div>'
        + '</div>';
    });
    box.innerHTML = '<div class="modal-rateio-topo"><strong>Rateio do lançamento</strong>'
      + '<span class="rateio-fechamento"></span></div>'
      + '<div class="modal-rateio-partes">' + partesHtml + '</div>'
      + '<div class="rateio-status rateio-fechamento"></div>'
      + '<div class="modal-rateio-acoes"><button type="button" class="ver-btn" data-rateio-adicionar>+ Parte</button>'
      + (existentes.length ? '<button type="button" class="ver-btn" data-rateio-desfazer>Desfazer rateio</button>' : '')
      + '<button type="button" class="ver-btn" data-rateio-salvar>Salvar rateio</button></div>';

    box.querySelectorAll('.rateio-valor').forEach(function (campo) {
      campo.addEventListener('input', function () { conferirQuadro(box, total); });
    });
    box.querySelector('[data-rateio-adicionar]').addEventListener('click', function () {
      rascunhos[id] = lerQuadro(box).map(function (p) {
        return {valor: p.valor, categoria: p.categoria, observacao: p.observacao, dims: p.dimensoes};
      });
      rascunhos[id].push({valor: 0, categoria: '', observacao: '', dims: {}});
      montarQuadro(box, opcoesQuadro);
    });
    box.querySelectorAll('[data-rateio-remover]').forEach(function (botao) {
      botao.addEventListener('click', function () {
        if (rascunhos[id].length <= 2) return;
        rascunhos[id] = lerQuadro(box).map(function (p) {
          return {valor: p.valor, categoria: p.categoria, observacao: p.observacao, dims: p.dimensoes};
        });
        rascunhos[id].splice(Number(botao.dataset.rateioRemover), 1);
        montarQuadro(box, opcoesQuadro);
      });
    });
    const status = box.querySelector('.rateio-status');
    box.querySelector('[data-rateio-salvar]').addEventListener('click', function () {
      if (!conferirQuadro(box, total)) return;
      status.textContent = 'Salvando...';
      salvarPartes(id, lerQuadro(box)).then(function () {
        if (window.pdmToastAposRecarregar) window.pdmToastAposRecarregar('Rateio salvo');
        delete rascunhos[id];
        ctx.aposSalvar(id, {});
      }).catch(function (e) { status.textContent = e.message || 'Não foi possível salvar.'; });
    });
    const desfazer = box.querySelector('[data-rateio-desfazer]');
    if (desfazer) desfazer.addEventListener('click', function () {
      if (!confirm('Desfazer o rateio e voltar ao lançamento simples?')) return;
      remover(id).then(function () {
        if (window.pdmToastAposRecarregar) window.pdmToastAposRecarregar('Rateio desfeito');
        delete rascunhos[id];
        ctx.aposSalvar(id, {});
      }).catch(function (e) { alert(e.message || 'Não foi possível desfazer.'); });
    });
    if (window.pdmCombobox && window.pdmCombobox.iniciar) window.pdmCombobox.iniciar(box);
    conferirQuadro(box, total);
  }

  window.pdmRateio = {
    configurar: configurar, linhas: linhasDe, ler: ler,
    validar: validar, atualizar: atualizar, salvar: salvar,
    salvarPartes: salvarPartes, remover: remover,
    montarQuadro: montarQuadro, lerQuadro: lerQuadro,
    // o esboco de um rateio ainda nao salvo morre quando se sai do lancamento:
    // reabrir tem que partir do que esta no banco, nao do que ficou pela metade
    limparRascunho: function (id) {
      if (id === undefined) { Object.keys(rascunhos).forEach(function (k) { delete rascunhos[k]; }); }
      else delete rascunhos[id];
    },
  };
})();
