// Nucleo compartilhado da edicao em lote (Resumida e Detalhada).
//
// Existe para que as duas telas NAO tenham cada uma a sua copia das regras:
// duas implementacoes do mesmo comportamento divergem na primeira regra nova -
// foi assim que nasceram os 57 falsos pendentes (secao 6.5 n.10).
//
// Aqui nao ha endpoint de lote. Cada selecionado passa pelo MESMO
// POST /api/transacao/<id> de uma edicao normal, com as mesmas validacoes, a
// mesma auditoria e a mesma propagacao para familia de parcelas.
(function () {
  const SIMULTANEOS = 4;

  function motivoRecusa(d) {
    if (d.rateio_invalido) return 'rateio incompleto';
    if (d.pendente_banco) return 'ainda pendente no banco';
    if (d.sem_pdf_conciliado) return 'sem vínculo com a fatura em PDF';
    return 'faltam campos obrigatórios';
  }

  // `alvos` sao {id, descricao, observacaoAtual, aplicar(payload, resposta)}.
  async function aplicar(alvos, base, opcoes) {
    opcoes = opcoes || {};
    const progresso = opcoes.progresso || function () {};
    const falhas = [];
    let aplicados = 0;
    let feitos = 0;

    async function um(alvo) {
      const payload = Object.assign({}, base);
      if ('_observacao' in payload) {
        delete payload._observacao;
        // A observacao pertence ao usuario (secao 7.3): so sobrescreve com
        // intencao explicita; caso contrario, apenas preenche quem esta vazia.
        if (opcoes.substituirObservacao || !(alvo.observacaoAtual || '').trim()) {
          payload.observacao = base._observacao;
        }
      }
      if (!Object.keys(payload).length) { feitos++; return; }
      try {
        const r = await fetch('/api/transacao/' + encodeURIComponent(alvo.id), {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const d = await r.json();
        if (!d.ok) { falhas.push(alvo.descricao + ': ' + (d.erro || 'falha')); return; }
        if (alvo.aplicar) alvo.aplicar(payload, d);
        if (d.bloqueada) falhas.push(alvo.descricao + ': OK recusado — ' + motivoRecusa(d));
        aplicados++;
      } catch (e) {
        falhas.push(alvo.descricao + ': erro de rede');
      } finally {
        feitos++;
        progresso(feitos, alvos.length);
      }
    }

    // Poucas em paralelo: com o ano inteiro selecionado, uma de cada vez fica
    // lento demais, e muitas de uma vez enfileiram no unico processo Gunicorn.
    const fila = alvos.slice();
    const trilhas = [];
    for (let i = 0; i < Math.min(SIMULTANEOS, fila.length); i++) {
      trilhas.push((async function () {
        while (fila.length) await um(fila.shift());
      })());
    }
    await Promise.all(trilhas);

    let texto = aplicados + ' de ' + alvos.length + ' atualizados.';
    if (falhas.length) {
      texto += ' Não foi possível em ' + falhas.length + ': ' + falhas.join(' · ');
    }
    return {aplicados: aplicados, falhas: falhas, texto: texto};
  }

  // Sair da barra e o mesmo comportamento nas duas telas, entao mora aqui pelo
  // mesmo motivo que `aplicar`: duas copias divergem na primeira regra nova.
  // `fechar` limpa a selecao; a barra some por consequencia disso.
  function ligarFechar(barra, fechar) {
    const botao = barra.querySelector('.barra-lote-fechar');
    if (botao) botao.addEventListener('click', fechar);

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' || barra.hidden) return;
      // Esc ja pertence a quem esta em foco: combobox cancela a lista (e chama
      // preventDefault), a busca da fatura limpa o campo, o modal fecha. A
      // barra e o ultimo da fila - so age quando ninguem mais tratou.
      if (e.defaultPrevented) return;
      if (document.querySelector('.modal-bg.show')) return;
      const alvo = e.target;
      if (alvo && alvo.closest && alvo.closest('input, select, textarea, .pdm-combobox')) return;
      fechar();
    });
  }

  window.pdmLote = {aplicar: aplicar, ligarFechar: ligarFechar};
})();
