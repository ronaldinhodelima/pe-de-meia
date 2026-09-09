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

  window.pdmRateio = {
    configurar: configurar, linhas: linhasDe, ler: ler,
    validar: validar, atualizar: atualizar, salvar: salvar,
    salvarPartes: salvarPartes, remover: remover,
  };
})();
