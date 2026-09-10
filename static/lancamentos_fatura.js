(function () {
  const configEl = document.querySelector('script[data-config-fatura]');
  let config = {};
  try { config = configEl ? JSON.parse(configEl.textContent) : {}; } catch (e) {}

  // O filtro Fatura carrega a URL de cada ciclo pronta do servidor: montada
  // aqui tambem, ela discordaria da que as setas usam na primeira regra nova
  // (`_url_da_fatura` e o ponto unico). A opcao "Periodo inteiro" traz a URL do
  // ciclo desta fatura, entao sair dela nao joga o usuario para outro mes.
  // `onchange` no proprio elemento, e nao `addEventListener`: filtrar troca a
  // barra inteira por AJAX, e o listener anexado morreria junto com o elemento
  // antigo - o seletor pararia de responder, em silencio (secao 7.1).
  window.irParaFatura = function (seletor) {
    if (!seletor || !seletor.value) return;
    if (typeof guardarPosicaoAtual === 'function') guardarPosicaoAtual();
    window.location.assign(seletor.value);
  };

  // Trocar o Status mexe SO no status: tudo o mais que esta na URL - a fatura
  // em foco, o periodo, as origens - continua valendo. Vale nos dois recortes,
  // porque os dois tem os mesmos blocos na tela.
  window.aplicarFiltroStatus = function () {
    const status = document.getElementById('periodoStatus');
    if (!status) return;
    const url = new URL(window.location.href);
    url.searchParams.set('status', status.value);
    return trocarBlocos(url.pathname + '?' + url.searchParams.toString());
  };

  // O card e a porta de entrada para as proprias linhas: ele escolhe o Status,
  // e dai em diante e o mesmo caminho do seletor. Antes ele recarregava a
  // pagina enquanto o seletor ao lado trocava a lista no lugar - dois
  // comportamentos para a mesma acao.
  document.addEventListener('click', evento => {
    const card = evento.target.closest('[data-filtro]');
    if (!card) return;
    const status = document.getElementById('periodoStatus');
    if (!status) return;
    status.value = card.dataset.filtro;
    aplicarFiltroStatus();
  });

  // ---- rateio: as partes moram no nucleo compartilhado (rateio.js) ---------
  // Mesmo comportamento da Resumida, mesmo codigo. O que muda aqui e so como se
  // acha a linha pai e o que se repinta nela.
  if (window.pdmRateio) {
    window.pdmRateio.configurar({
      paiDe: function (id) {
        return document.querySelector('tr[data-linha][data-id="' + id + '"]');
      },
      config: function () { return config; },
      aoValidar: function (id, estado, pai) {
        const ok = pai.querySelector('[data-ok-lancamento]');
        if (ok) {
          ok.disabled = !config.pode_conferir || !estado.valido;
          ok.title = estado.valido ? 'OK do lançamento'
            : 'Ajuste as partes até o rateio fechar o valor do lançamento';
        }
        const pendencia = pai.querySelector('[data-classificacao]');
        if (pendencia) {
          pendencia.innerHTML = estado.valido ? ''
            : '<span class="estado invalido">Rateado '
              + window.pdmMoedaBr(estado.somaCentavos / 100) + ' de '
              + window.pdmMoedaBr(estado.totalCentavos / 100) + '</span>';
        }
      },
      aposSalvar: function () {
        if (typeof guardarPosicaoAtual === 'function') guardarPosicaoAtual();
        window.location.reload();
      },
    });
    document.addEventListener('click', function (e) {
      const salvar = e.target.closest && e.target.closest('.rateio-salvar-inline');
      if (salvar) {
        const parte = salvar.closest('tr[data-rateio-parent]');
        e.stopPropagation();
        if (parte) window.pdmRateio.salvar(parte.dataset.rateioParent);
        return;
      }
      const toggle = e.target.closest && e.target.closest('.rateio-toggle');
      if (!toggle) return;
      e.stopPropagation();
      const abrir = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', abrir ? 'true' : 'false');
      toggle.textContent = abrir ? '−' : '+';
      window.pdmRateio.linhas(toggle.dataset.rateioId).forEach(function (linha) {
        linha.hidden = !abrir;
      });
      if (abrir) window.pdmRateio.atualizar(toggle.dataset.rateioId, false);
    });
    document.addEventListener('change', function (e) {
      if (!e.target.closest || !e.target.closest('tr[data-rateio-parent]')) return;
      const parte = e.target.closest('tr[data-rateio-parent]');
      window.pdmRateio.atualizar(parte.dataset.rateioParent, true);
    });
    document.addEventListener('input', function (e) {
      if (!e.target.matches('.rateio-valor-inline, .rateio-obs-inline')) return;
      const parte = e.target.closest('tr[data-rateio-parent]');
      if (parte) window.pdmRateio.atualizar(parte.dataset.rateioParent, true);
    });
  }

  // ---- acoes do lancamento, no rodape do painel ---------------------------
  // Rateio e exclusao nao sao campos: sao acoes sobre o lancamento, raras, e
  // sem lugar numa linha que ja tem dez colunas. O painel continua sendo
  // auditoria; elas ficam num rodape separado (decisao do usuario).
  function montarQuadroDeRateio(quadro) {
    if (!window.pdmRateio || quadro.dataset.montado === '1') return;
    quadro.dataset.montado = '1';
    const id = quadro.dataset.rateioQuadro;
    const linha = document.querySelector('tr[data-editor="' + id + '"], tr[data-linha][data-id="' + id + '"]');
    const dims = {};
    if (linha) linha.querySelectorAll('[data-dimensao]').forEach(function (sel) {
      dims[sel.dataset.dimensao] = sel.value || '';
    });
    const categoria = linha && linha.querySelector('[data-campo="categoria"]');
    window.pdmRateio.montarQuadro(quadro, {
      id: id,
      total: Number(quadro.dataset.rateioTotal || 0),
      existentes: window.pdmRateio.linhas(id).map(function (parte) {
        const d = {};
        parte.querySelectorAll('.rateio-dim-select').forEach(function (sel) {
          d[sel.dataset.dim] = sel.value || '';
        });
        return {
          valor: Number(parte.querySelector('.rateio-valor-inline').value || 0),
          categoria: parte.querySelector('.rateio-cat-select').value,
          observacao: parte.querySelector('.rateio-obs-inline').value,
          dims: d,
        };
      }),
      categoria: categoria ? categoria.value : '',
      dims: dims,
    });
  }

  // "Ratear" abre o quadro; ele nao nasce montado porque traz um select de
  // categoria e um por dimensao, e deixa-lo aberto sempre custava 111px em
  // todo lancamento, para uma acao rara.
  document.addEventListener('click', function (e) {
    const abrir = e.target.closest && e.target.closest('[data-abrir-rateio]');
    if (abrir) {
      e.stopPropagation();
      const painel = abrir.closest('.vinculos-detalhe');
      const quadro = painel && painel.querySelector(
        '[data-rateio-quadro="' + CSS.escape(abrir.dataset.abrirRateio) + '"]');
      if (!quadro) return;
      quadro.hidden = !quadro.hidden;
      abrir.classList.toggle('ativo', !quadro.hidden);
      if (!quadro.hidden) montarQuadroDeRateio(quadro);
      return;
    }
    const excluir = e.target.closest && e.target.closest('[data-excluir-lancamento]');
    if (!excluir) return;
    e.stopPropagation();
    if (!confirm('Excluir este lançamento? Não dá para desfazer.')) return;
    excluir.disabled = true;
    fetch('/api/lancamento-manual/' + encodeURIComponent(excluir.dataset.excluirLancamento),
          {method: 'DELETE'})
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.erro || 'Não foi possível excluir.');
        if (window.pdmToastAposRecarregar) window.pdmToastAposRecarregar('Lançamento excluído');
        if (typeof guardarPosicaoAtual === 'function') guardarPosicaoAtual();
        window.location.reload();
      })
      .catch(function (erro) {
        excluir.disabled = false;
        alert(erro.message || 'Não foi possível excluir.');
      });
  });

  // ---- recorte por periodo -------------------------------------------------
  // A tela nasceu presa a uma fatura de cartao. Recortando por periodo ela
  // alcanca conta corrente, dinheiro e lancamento manual, que nao pertencem a
  // fatura nenhuma. Os campos sao os mesmos da Resumida, de proposito.
  const mesInput = document.getElementById('mesInput');
  function queryDoPeriodo() {
    const params = new URLSearchParams();
    params.set('recorte', 'periodo');
    const barra = document.querySelector('.fatura-filtros');
    const intervalo = document.getElementById('periodoIntervalo');
    if (mesInput) {
      params.set('mes', mesInput.value);
      if (intervalo && intervalo.checked) {
        params.set('periodo', 'intervalo');
        params.set('data_inicio', document.getElementById('dataInicioInput').value);
        params.set('data_fim', document.getElementById('dataFimInput').value);
      } else {
        params.set('periodo', document.getElementById('periodoAno').checked ? 'ano' : 'mes');
      }
    } else if (barra) {
      // Com uma fatura em foco nao ha controle de periodo na tela: o recorte e
      // o CICLO dela, que o servidor manda na barra. Assim trocar a origem sai
      // da fatura para o periodo dela, e nao para o mes corrente - quem estava
      // olhando julho continua em julho.
      params.set('mes', barra.dataset.mes || '');
      params.set('periodo', barra.dataset.periodo || 'mes');
      if (barra.dataset.periodo === 'intervalo') {
        params.set('data_inicio', barra.dataset.inicio || '');
        params.set('data_fim', barra.dataset.fim || '');
      }
    }
    const status = document.getElementById('periodoStatus');
    // Status da fatura nao existe no periodo (sem_vinculo, requer_validacao,
    // multiplos falam do DOCUMENTO): sair da fatura com um deles na URL cairia
    // no default sem dizer nada. Melhor sair mostrando tudo.
    if (status && mesInput) params.set('status', status.value);
    // [name] exigido: checkbox sem nome (o do menu de colunas) nao e filtro
    document.querySelectorAll('.chipfilter input[type=checkbox][name]:checked')
      .forEach(cb => params.append(cb.name, cb.value));
    return params;
  }

  // A troca de blocos e uma so: filtrar por periodo, por origem ou por status
  // muda os MESMOS pedacos da tela. Escrita duas vezes, a segunda esqueceria um
  // bloco e a tela ficaria meio atualizada, sem erro nenhum.
  function trocarBlocos(url) {
    return window.pdmTrocarPorAjax(url, [
      {seletor: 'table.compacta', aoTrocar: function (tabela) {
        // a tabela nova veio do servidor sem os listeners nem as alcas de
        // redimensionar, que sao criados por JS
        if (window.ativarTabelaAjustavel) window.ativarTabelaAjustavel(tabela, tabela.dataset.tabela);
        tabelaFatura = tabela;
        ligarOrdenacao();
        if (window.atualizarTotaisVisiveis) window.atualizarTotaisVisiveis(tabela);
        // a pintura de pendencia e a pilula "Faltam:" sao estado, nao HTML: o
        // servidor manda a linha, mas quem a repinta e o cliente
        if (window.pdmPrepararLinhas) window.pdmPrepararLinhas(tabela);
      }},
      {seletor: '.cards'},
      // a barra inteira, e nao so o chip: o filtro Fatura aparece e some
      // conforme a origem, e o Status cresce e encolhe junto
      {seletor: '.fatura-filtros'},
      {seletor: 'details.cat-breakdown', preservarAberto: true},
      {seletor: 'details.legenda-lancamentos', preservarAberto: true},
    ], function (doc) {
      const configNovo = doc.querySelector('script[data-config-fatura]');
      if (configNovo) {
        try { config = JSON.parse(configNovo.textContent); } catch (e) {}
      }
      aplicarBuscaFatura();
    });
  }
  // Filtrar troca a lista NO LUGAR, sem recarregar: recarregar joga quem esta
  // no meio da conferencia de volta ao topo. A URL acompanha, entao o Voltar
  // do navegador retorna ao filtro anterior.
  window.aplicarFiltrosPeriodo = function () {
    // Mexer no periodo ou na origem SAI da fatura, e isso e a regra, nao um
    // efeito colateral: a origem nova pode nem ter fatura, e o ciclo em foco
    // pertence a um cartao so.
    const url = '/lancamentos/fatura?' + queryDoPeriodo().toString();
    // Sair de uma fatura troca o RECORTE, nao so a lista: cabecalho, cards e o
    // formulario manual sao outros. A troca por AJAX e bloco a bloco, pelo
    // indice - a fatura tem dois grupos de cards e o periodo um, entao o
    // segundo grupo da fatura ficaria na tela sob a lista do periodo, e o
    // cabecalho continuaria dizendo "Fatura Agosto". Ai recarrega.
    if (!mesInput) {
      if (typeof guardarPosicaoAtual === 'function') guardarPosicaoAtual();
      window.location.assign(url);
      return;
    }
    return trocarBlocos(url);
  };

  // Cada filtro vira uma etapa real do navegador. Voltar recarrega o estado
  // correspondente sem criar uma entrada nova no historico.
  window.addEventListener('popstate', function () {
    if (typeof guardarPosicaoAtual === 'function') guardarPosicaoAtual();
    window.location.reload();
  });
  window.mudarMesPeriodo = function (passo) {
    if (!mesInput) return;
    const ano = document.getElementById('periodoAno');
    if (ano && ano.checked) {
      mesInput.value = (parseInt(mesInput.value.slice(0, 4), 10) + passo) + mesInput.value.slice(4);
    } else {
      const [a, m] = mesInput.value.split('-').map(Number);
      const d = new Date(a, m - 1 + passo, 1);
      mesInput.value = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
    }
    window.aplicarFiltrosPeriodo();
  };
  window.alternarPeriodoAno = function () {
    const intervalo = document.getElementById('periodoIntervalo');
    if (document.getElementById('periodoAno').checked && intervalo) intervalo.checked = false;
    window.aplicarFiltrosPeriodo();
  };
  window.alternarPeriodoIntervalo = function () {
    const ano = document.getElementById('periodoAno');
    const intervalo = document.getElementById('periodoIntervalo');
    if (intervalo.checked && ano) ano.checked = false;
    const wrap = document.getElementById('intervaloWrap');
    if (wrap) wrap.style.display = intervalo.checked ? 'flex' : 'none';
    // sem as duas datas ainda nao ha intervalo para aplicar
    if (!intervalo.checked || (document.getElementById('dataInicioInput').value
        && document.getElementById('dataFimInput').value)) {
      window.aplicarFiltrosPeriodo();
    }
  };
  // O campo de mes e de data abre o calendario ao clicar em qualquer ponto,
  // nao so no icone - o mesmo comportamento da Resumida.
  document.querySelectorAll('#mesInput, #dataInicioInput, #dataFimInput').forEach(campo => {
    campo.addEventListener('click', () => { if (campo.showPicker) { try { campo.showPicker(); } catch (e) {} } });
  });

  function normalizarBusca(texto) {
    return String(texto == null ? '' : texto)
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }

  // O texto de um <select> inclui TODAS as opcoes, nao a escolhida: com a
  // classificacao dentro da linha, usar textContent cru faria toda linha casar
  // com quase qualquer termo. Aqui os selects entram so pela opcao selecionada.
  function textoPesquisavel(no) {
    const clone = no.cloneNode(true);
    clone.querySelectorAll('select').forEach(select => select.remove());
    const partes = [clone.textContent || ''];
    no.querySelectorAll('select').forEach(select => {
      const opcao = select.options[select.selectedIndex];
      if (opcao) partes.push(opcao.textContent || '');
    });
    no.querySelectorAll('input').forEach(input => {
      if (!['checkbox', 'radio', 'hidden'].includes(input.type)) partes.push(input.value || '');
    });
    // O titular virou avatar: o nome so existe no tooltip, e continuar
    // encontravel pela busca e o que torna a troca aceitavel.
    no.querySelectorAll('[data-tip]').forEach(el => partes.push(el.dataset.tip || ''));
    return partes.join(' ');
  }

  function textoFiltravelDoGrupo(linha, detalhe) {
    const partes = [textoPesquisavel(linha)];
    if (detalhe) partes.push(textoPesquisavel(detalhe));
    return normalizarBusca(partes.join(' '));
  }



  const buscaFatura = document.getElementById('buscaFatura');
  const contadorBusca = document.getElementById('buscaFaturaContador');
  function aplicarBuscaFatura() {
    if (!buscaFatura) return;
    const termo = normalizarBusca(buscaFatura.value).trim();
    const linhas = Array.from(document.querySelectorAll('tr[data-linha]'));
    let visiveis = 0;
    linhas.forEach(linha => {
      const detalhe = document.getElementById('vinculos-' + linha.dataset.linha);
      const exibir = !termo || textoFiltravelDoGrupo(linha, detalhe).includes(termo);
      linha.style.display = exibir ? '' : 'none';
      if (detalhe) detalhe.style.display = exibir ? '' : 'none';
      if (exibir) visiveis += 1;
    });
    if (contadorBusca) contadorBusca.textContent = termo ? visiveis + ' de ' + linhas.length : '';
    // o rodape conta o que esta A VISTA, entao acompanha a pesquisa local
    if (window.atualizarTotaisVisiveis) {
      window.atualizarTotaisVisiveis(document.querySelector('.fatura-tabela'));
    }
  }
  if (buscaFatura) {
    buscaFatura.addEventListener('input', aplicarBuscaFatura);
    buscaFatura.addEventListener('keydown', evento => {
      if (evento.key === 'Escape') {
        buscaFatura.value = '';
        aplicarBuscaFatura();
        evento.stopPropagation();
      }
    });
  }

  let tabelaFatura = document.querySelector('.fatura-tabela');
  function valorOrdenacao(linha, chave) {
    // Le por data-col, nunca por indice de celula: a ordem das colunas mudou
    // quando a classificacao veio para a linha, e indice fixo quebraria em
    // silencio - a coluna certa passa a ser lida da errada.
    const celula = linha.querySelector('[data-col="' + chave + '"]');
    if (chave === 'data') {
      const partes = (celula?.textContent || '').trim().split('/').map(Number);
      return partes.length === 3 ? new Date(partes[2], partes[1] - 1, partes[0]).getTime() : 0;
    }
    if (chave === 'valor') {
      // mesma conta do tabelas.js: lida aqui de novo, ela apagava a virgula
      // decimal e ordenava R$ 212,35 como 21.235
      const numero = window.pdmNumeroDeTexto(celula?.textContent || '');
      return numero === null ? 0 : numero;
    }
    if (chave === 'check') return linha.querySelector('[data-ok-lancamento]')?.checked ? 1 : 0;
    if (!celula) return '';
    // Coluna de classificacao: ordena pelo rotulo escolhido, nao pelo id.
    const escolha = celula.querySelector('select');
    if (escolha) return (escolha.selectedOptions[0]?.textContent || '').trim();
    return (celula.textContent || '').trim();
  }



  function ordenarFatura(cabecalho) {
    if (!tabelaFatura) return;
    const chave = cabecalho.dataset.ordenar;
    const direcao = cabecalho.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
    tabelaFatura.querySelectorAll('th[data-ordenar]').forEach(th => th.removeAttribute('aria-sort'));
    cabecalho.setAttribute('aria-sort', direcao);
    const corpo = tabelaFatura.tBodies[0];
    const linhas = Array.from(corpo.querySelectorAll('tr[data-linha]')).map((linha, indice) => ({
      linha, detalhe: document.getElementById('vinculos-' + linha.dataset.linha), indice,
      valor: valorOrdenacao(linha, chave)
    }));
    linhas.sort((a, b) => {
      let comparacao;
      if (typeof a.valor === 'number' && typeof b.valor === 'number') comparacao = a.valor - b.valor;
      else comparacao = String(a.valor).localeCompare(String(b.valor), 'pt-BR', {numeric: true, sensitivity: 'base'});
      if (!comparacao) comparacao = a.indice - b.indice;
      return direcao === 'ascending' ? comparacao : -comparacao;
    });
    linhas.forEach(item => {
      corpo.appendChild(item.linha);
      if (item.detalhe) corpo.appendChild(item.detalhe);
    });
    aplicarBuscaFatura();
  }

  // "+" da coluna Regra: mesmo destino da Resumida (a tela de regras ja
  // preenchida a partir do lancamento), sem reimplementar nada aqui.
  document.addEventListener('click', function (e) {
    const botao = e.target.closest && e.target.closest('.regra-btn');
    if (!botao || !botao.dataset.transacao) return;
    e.stopPropagation();
    window.location.assign('/regras?transacao=' + encodeURIComponent(botao.dataset.transacao));
  });

  // primeira carga: o rodape nasce preenchido, sem esperar uma pesquisa
  if (window.atualizarTotaisVisiveis) window.atualizarTotaisVisiveis(tabelaFatura);

  function ligarOrdenacao() {
  if (tabelaFatura) tabelaFatura.querySelectorAll('th[data-ordenar]').forEach(cabecalho => {
    cabecalho.setAttribute('role', 'button');
    cabecalho.addEventListener('click', () => ordenarFatura(cabecalho));
    cabecalho.addEventListener('keydown', evento => {
      if (evento.key !== 'Enter' && evento.key !== ' ') return;
      evento.preventDefault();
      ordenarFatura(cabecalho);
    });
  });
  }
  ligarOrdenacao();

  const revisarParcelamentos = document.getElementById('revisarParcelamentos');
  const revisarStatus = document.getElementById('revisarParcelamentosStatus');
  let previaParcelamentos = null;
  async function carregarPreviaParcelamentos() {
    if (!revisarParcelamentos) return null;
    revisarParcelamentos.disabled = true;
    try {
      const url = '/api/faturas/sincronizar-parcelas?fatura_id=' +
        encodeURIComponent(revisarParcelamentos.dataset.faturaId);
      const resp = await fetch(url, {cache: 'no-store'});
      const json = await resp.json();
      if (!resp.ok || !json.ok) throw new Error(json.erro || 'Não foi possível analisar.');
      previaParcelamentos = json;
      if (!json.agregados || !json.parcelas_pendentes) {
        revisarParcelamentos.textContent = 'Parcelamentos revisados';
        revisarParcelamentos.title = 'Não há parcelamentos pendentes neste cartão';
        if (revisarStatus) revisarStatus.textContent = 'Nenhuma alteração necessária.';
        return json;
      }
      revisarParcelamentos.textContent = 'Revisar parcelamentos';
      revisarParcelamentos.disabled = false;
      if (revisarStatus) revisarStatus.textContent =
        json.agregados + ' compra(s) · ' + json.parcelas_pendentes + ' parcela(s) pendente(s)';
      return json;
    } catch (erro) {
      revisarParcelamentos.textContent = 'Não foi possível analisar';
      revisarParcelamentos.title = erro.message;
      if (revisarStatus) revisarStatus.textContent = erro.message;
      return null;
    }
  }
  if (revisarParcelamentos) {
    carregarPreviaParcelamentos();
    revisarParcelamentos.addEventListener('click', async () => {
      const previa = previaParcelamentos || await carregarPreviaParcelamentos();
      if (!previa || !previa.agregados || !previa.parcelas_pendentes) return;
      const confirmar = window.confirm(
        'Este cartão possui ' + previa.agregados + ' compra(s) pelo valor total e ' +
        previa.parcelas_pendentes + ' parcela(s) pendente(s).\n\n' +
        'O valor total passará a ser registro técnico fora do DRE, e cada parcela será ' +
        'contabilizada no mês da fatura. Isso pode alterar os totais mensais.\n\n' +
        'Confirma a revisão?'
      );
      if (!confirmar) return;
      revisarParcelamentos.disabled = true;
      revisarParcelamentos.textContent = 'Revisando…';
      const corpo = new URLSearchParams({
        fatura_id: revisarParcelamentos.dataset.faturaId,
        retorno: window.location.pathname + window.location.search
      });
      try {
        const resp = await fetch('/api/faturas/sincronizar-parcelas', {
          method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: corpo.toString()
        });
        if (!resp.ok) {
          const json = await resp.json();
          throw new Error(json.erro || 'Não foi possível revisar.');
        }
        if (typeof guardarPosicaoAtual === 'function') guardarPosicaoAtual();
        window.location.assign(window.location.href);
      } catch (erro) {
        revisarParcelamentos.textContent = 'Revisar parcelamentos';
        revisarParcelamentos.disabled = false;
        if (revisarStatus) revisarStatus.textContent = erro.message;
      }
    });
  }

  function alternarLinha(id) {
    const botao = document.querySelector('[data-expande="' + CSS.escape(id) + '"]');
    const detalhe = document.getElementById('vinculos-' + id);
    if (!detalhe) return;
    detalhe.hidden = !detalhe.hidden;
    if (botao) {
      botao.textContent = detalhe.hidden ? '+' : '−';
      botao.setAttribute('aria-expanded', detalhe.hidden ? 'false' : 'true');
    }
  }

  // DELEGACAO, nunca listener por elemento: filtrar troca a tabela inteira
  // (secao 7.1-A), e `replaceWith` descarta o elemento antigo junto com tudo
  // que estava anexado nele. Ligado um a um, o "+" parava de abrir depois do
  // primeiro filtro e so voltava recarregando a pagina.
  document.addEventListener('click', evento => {
    const botao = evento.target.closest('[data-expande]');
    if (botao) {
      evento.stopPropagation();
      alternarLinha(botao.dataset.expande);
      return;
    }
    const linha = evento.target.closest('[data-toggle-linha]');
    if (!linha) return;
    if (evento.target.closest('button,input,select,textarea,a,label')) return;
    alternarLinha(linha.dataset.toggleLinha);
  });
  document.addEventListener('click', evento => {
    const botao = evento.target.closest('[data-info-target]');
    if (!botao) return;
    evento.stopPropagation();
    const painel = document.getElementById(botao.dataset.infoTarget);
    if (!painel) return;
    painel.hidden = !painel.hidden;
    botao.setAttribute('aria-expanded', painel.hidden ? 'false' : 'true');
    botao.title = painel.hidden ? 'Abrir detalhes da transação' : 'Fechar detalhes da transação';
  });

  const filaSalvar = {};
  document.addEventListener('change', async evento => {
    const campo = evento.target.closest('[data-ok-lancamento]');
    if (!campo) return;
    const novo = campo.checked;
    let confirmar = false;
    if (!novo) {
      confirmar = window.confirm('Confirma desmarcar o OK deste lançamento?');
      if (!confirmar) { campo.checked = true; return; }
    }
    campo.disabled = true;
    try {
      await (filaSalvar[campo.dataset.okLancamento] || Promise.resolve());
      const resp = await fetch('/api/transacao/' + encodeURIComponent(campo.dataset.okLancamento), {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({conferida: novo, confirmar_desmarcacao: confirmar})
      });
      const json = await resp.json();
      if (!resp.ok || !json.ok) throw new Error(json.erro || 'Não foi possível salvar.');
      campo.checked = Boolean(json.conferida);
      campo.disabled = !config.pode_conferir;
      if (!json.bloqueada && window.pdmToast) {
        window.pdmToast(campo.checked ? 'Lançamento conferido' : 'OK retirado');
      }
      if (json.bloqueada) {
        const nomes = (json.faltando || []).map(String).join(', ');
        let motivo = 'O OK só é liberado quando a classificação estiver completa' + (nomes ? ': ' + nomes + '.' : '.');
        if (json.rateio_invalido) motivo = 'O OK só é liberado quando o rateio estiver completo e fechar exatamente com o lançamento.';
        if (json.pendente_banco) motivo = 'O banco ainda informa que este lançamento está pendente. Aguarde a confirmação bancária para marcar OK.';
        // sem esta linha a tela mandava completar a classificacao mesmo com
        // `faltando` vazio, e o motivo real era a trava da secao 7.5
        if (json.sem_pdf_conciliado) motivo = 'Este lançamento de cartão ainda não está vinculado a nenhuma linha da fatura importada. Importe a fatura que cobra este período, ou faça o vínculo em Conciliar fatura.';
        alert(motivo);
      }
      await atualizarResumoPagina(novo && status && status.value === 'pendente_ok');
      aplicarBuscaFatura();
    } catch (e) {
      campo.checked = !novo; campo.disabled = false; alert(e.message);
    }
  });

  function payloadEditor(editor, alterado) {
    const payload = {};
    if (alterado.dataset.campo) payload[alterado.dataset.campo] = alterado.value;
    if (alterado.dataset.dimensao) {
      payload.dimensoes = {[alterado.dataset.dimensao]: alterado.value || null};
      if (alterado.dataset.dimensao === config.dim_id_projeto) {
        const portfolio = (config.projeto_portfolio_map || {})[alterado.value];
        if (portfolio && config.dim_id_portfolio) {
          const destino = editor.querySelector('[data-dimensao="' + CSS.escape(config.dim_id_portfolio) + '"]');
          if (destino) {
            destino.value = String(portfolio);
            if (window.pdmCombobox) window.pdmCombobox.sincronizar(destino);
          }
          payload.dimensoes[config.dim_id_portfolio] = String(portfolio);
        }
      }
    }
    return payload;
  }

  // O editor pode ser a PROPRIA linha (classificacao inline) ou o painel
  // aberto abaixo dela. A flag de obrigatoriedade mora sempre na linha.
  function linhaDoEditor(editor) {
    if (!editor) return null;
    if (editor.matches('tr[data-linha]')) return editor;
    const detalhe = editor.closest('tr.vinculos-detalhe');
    // Pelo id do painel, e nao pelo "elemento de cima": num rateado, entre a
    // linha principal e o painel ficam as linhas das PARTES, e o
    // previousElementSibling devolvia a ultima parte - a pintura e a pilula
    // iriam para a linha errada.
    if (detalhe) {
      return document.querySelector(
        'tr[data-linha="' + CSS.escape(detalhe.id.replace(/^vinculos-/, '')) + '"]');
    }
    return editor.closest('tr[data-linha]');
  }

  // Dimensao obrigatoria NAO se aplica a natureza neutra - pagamento de fatura,
  // transferencia entre contas proprias, bem e investimento nao participam do
  // resultado (secao 4.1). Cobrar Responsavel/Projeto/Portfolio deles pinta de
  // vermelho um campo que o servidor nao exige, e preenche-lo faria o mesmo
  // dinheiro aparecer de novo na visao por dimensao.
  function exigeDimensoes(editor) {
    const linha = linhaDoEditor(editor);
    return !linha || linha.dataset.exigeDimensoes !== '0';
  }

  function dimensoesObrigatorias(editor) {
    if (!exigeDimensoes(editor)) return new Set();
    return new Set((config.dimensoes_obrigatorias || []).map(String));
  }

  function atualizarDestaquesObrigatorios(editor) {
    const categoria = editor.querySelector('[data-campo="categoria"]');
    if (categoria) categoria.classList.toggle('classificacao-faltando', !categoria.value);
    const obrigatorias = dimensoesObrigatorias(editor);
    editor.querySelectorAll('[data-dimensao]').forEach(campo => {
      campo.classList.toggle(
        'classificacao-faltando', obrigatorias.has(String(campo.dataset.dimensao)) && !campo.value
      );
    });
  }

  function atualizarAvisoClassificacao(editor) {
    // O editor pode ser a PROPRIA linha (classificacao inline) ou o painel
    // aberto abaixo dela. Nos dois casos o aviso mora na linha.
    // a linha sai do MESMO ponto de quem pinta: duas formas de achar a linha
    // divergiram, e a daqui apontava para a ultima parte de um rateio
    const linha = linhaDoEditor(editor);
    const destino = linha && linha.querySelector('[data-classificacao]');
    if (!destino) return;

    const faltando = [];
    const categoria = editor.querySelector('[data-campo="categoria"]');
    if (categoria && !categoria.value) faltando.push('Categoria');
    const obrigatorias = dimensoesObrigatorias(editor);
    editor.querySelectorAll('[data-dimensao]').forEach(campo => {
      if (!obrigatorias.has(String(campo.dataset.dimensao)) || campo.value) return;
      faltando.push(campo.dataset.dimensaoNome || 'Classificação');
    });

    // Reaproveita a pilula que ja esta na linha em vez de recria-la: recriar
    // pisca e faz o texto saltar a cada campo preenchido. O CSS reserva a
    // linha, entao a altura da tabela nao muda quando ela some.
    if (faltando.length) {
      let aviso = destino.querySelector('.estado');
      if (!aviso || !destino.dataset.pendencia) {
        aviso = document.createElement('span');
        aviso.className = 'estado pendente';
        destino.replaceChildren(aviso);
        destino.dataset.pendencia = '1';
      }
      aviso.classList.add('pendente');
      const texto = 'Faltam: ' + faltando.join(', ');
      aviso.textContent = texto;
      aviso.dataset.tip = texto;   // o texto pode ser cortado com reticencias
      destino.classList.remove('sumindo');
    } else if (destino.dataset.pendencia || destino.textContent.trim().startsWith('Faltam:')) {
      // Some com transicao: o corte seco no meio do preenchimento e o que da a
      // sensacao de a linha "pular".
      destino.classList.add('sumindo');
      delete destino.dataset.pendencia;
      setTimeout(function () {
        if (!destino.dataset.pendencia) {
          destino.replaceChildren();
          destino.classList.remove('sumindo');
        }
      }, 200);
    }
  }

  // O resumo (cards, pilula, OK) so precisa refletir o fim da rajada. Tabulando
  // por Categoria, Responsavel, Projeto e Portfolio saiam QUATRO leituras da
  // pagina inteira, cada uma chegando com o estado de antes da escolha seguinte.
  let temporizadorResumo = null;
  function agendarResumo(editor) {
    if (editor) editor.dataset.pendente = '1';
    clearTimeout(temporizadorResumo);
    temporizadorResumo = setTimeout(async function () {
      if (editor) delete editor.dataset.pendente;
      try { await atualizarResumoPagina(); } catch (e) { /* volta na proxima */ }
      aplicarBuscaFatura();
    }, 700);
  }

  async function atualizarResumoPagina(ocultarAusentes) {
    const resp = await fetch(window.location.href, {headers: {'X-Parcial': '1'}, cache: 'no-store'});
    if (!resp.ok) return;
    const doc = new DOMParser().parseFromString(await resp.text(), 'text/html');
    const cardsAtuais = document.querySelectorAll('.fatura-cards');
    const cardsNovos = doc.querySelectorAll('.fatura-cards');
    cardsAtuais.forEach((card, i) => {
      if (cardsNovos[i]) card.replaceChildren(...Array.from(cardsNovos[i].childNodes).map(n => n.cloneNode(true)));
    });
    document.querySelectorAll('tr[data-linha]').forEach(linha => {
      const nova = doc.querySelector('tr[data-linha="' + CSS.escape(linha.dataset.linha) + '"]');
      if (!nova) {
        if (ocultarAusentes) {
          const detalhe = document.getElementById('vinculos-' + linha.dataset.linha);
          if (detalhe) detalhe.remove();
          linha.remove();
        }
        return;
      }
      linha.className = nova.className;
      const classificacao = linha.querySelector('[data-classificacao]');
      const classificacaoNova = nova.querySelector('[data-classificacao]');
      if (classificacao && classificacaoNova) {
        classificacao.innerHTML = classificacaoNova.innerHTML;
        // o servidor acabou de redesenhar a pilula: o estado local tem que
        // acompanhar, senao a proxima edicao recria e pisca de novo
        classificacao.classList.remove('sumindo');
        if (classificacao.querySelector('.estado.pendente')) classificacao.dataset.pendencia = '1';
        else delete classificacao.dataset.pendencia;
      }
      const data = linha.querySelector('[data-col="data"]');
      const dataNova = nova.querySelector('[data-col="data"]');
      if (data && dataNova) data.dataset.tip = dataNova.dataset.tip || '';
      const ok = linha.querySelector('[data-ok-lancamento]');
      const okNovo = nova.querySelector('[data-ok-lancamento]');
      if (ok && okNovo) {
        ok.checked = okNovo.checked;
        ok.title = okNovo.title;
      }
    });
    // O resumo traz o estado do servidor - de ANTES do que o usuario acabou de
    // escolher, porque o GET saiu antes. Sobrescrever aqui devolvia o valor
    // antigo ao campo seguinte no meio da tabulacao: a pessoa escolhia Projeto,
    // o resumo da gravacao do Responsavel chegava e apagava a escolha. Por isso
    // esta atualizacao e a ULTIMA da fila e pula tudo que ainda esta em uso.
    document.querySelectorAll('[data-editor]').forEach(editor => {
      const novo = doc.querySelector('[data-editor="' + CSS.escape(editor.dataset.editor) + '"]');
      if (!novo || editor.dataset.salvando === '1' || editor.dataset.pendente === '1') return;
      // foco dentro do editor, ou combobox aberto: o usuario esta ali
      if (editor.contains(document.activeElement)) return;
      if (editor.querySelector('.pdm-combobox.aberto')) return;
      editor.querySelectorAll('[data-campo],[data-dimensao]').forEach(campo => {
        const seletor = campo.dataset.campo
          ? '[data-campo="' + CSS.escape(campo.dataset.campo) + '"]'
          : '[data-dimensao="' + CSS.escape(campo.dataset.dimensao) + '"]';
        const campoNovo = novo.querySelector(seletor);
        // so quando mudou de verdade: reescrever o mesmo valor faz o combobox
        // se re-sincronizar e mover o destaque debaixo do usuario (secao 7.7)
        if (!campoNovo || campo.value === campoNovo.value) return;
        campo.value = campoNovo.value;
        if (window.pdmCombobox) window.pdmCombobox.sincronizar(campo);
      });
    });
  }

  // "Salvo" e confirmacao, nao estado: depois de lido nao acrescenta nada, e
  // deixado na tela vira ruido em toda linha aberta. Some sozinho, com
  // transicao; erro NAO some - ali a mensagem e a unica pista do que houve.
  function mostrarSalvo(aviso, campo) {
    // a confirmacao virou toast (canto superior direito): presa na linha, ela
    // sumia da vista quando a linha saia da tela e mexia no layout ao aparecer
    if (window.pdmToastSalvo) window.pdmToastSalvo(campo);
    clearTimeout(aviso._sumir);
    aviso.textContent = '';
    aviso.classList.remove('erro', 'sumindo');
  }

  function salvarEditor(editor, alterado) {
    // `data-id` e o id do lancamento RATEADO, cuja linha nao tem editor proprio
    // (a classificacao mora nas partes) - mas a descricao dele se edita igual.
    const id = editor.dataset.editor || editor.dataset.id;
    const aviso = editor.querySelector('[data-status]');
    const payload = payloadEditor(editor, alterado);
    // Pendencia de classificacao so se repinta quando o campo alterado E de
    // classificacao. Descricao e observacao nao mudam pendencia nenhuma, e numa
    // linha sem seletores (a do rateado) o recalculo daria "nao falta nada" e
    // APAGARIA o "Faltam: Rateio" de um rateio que ainda nao fecha.
    const mexeNaClassificacao = alterado.matches(SELETOR_CLASSIF);
    if (mexeNaClassificacao) {
      atualizarDestaquesObrigatorios(editor);
      atualizarAvisoClassificacao(editor);
    }
    const versao = String((Number(editor.dataset.versaoSalva || 0) + 1));
    editor.dataset.versaoSalva = versao;
    const anterior = filaSalvar[id] || Promise.resolve();
    const atual = anterior.catch(() => {}).then(async () => {
      editor.dataset.salvando = '1';
      clearTimeout(aviso._sumir);
      aviso.textContent = 'Salvando…'; aviso.classList.remove('erro', 'sumindo');
      try {
        const resp = await fetch('/api/transacao/' + encodeURIComponent(id), {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
        });
        const json = await resp.json();
        if (!resp.ok || !json.ok) throw new Error(json.erro || 'Não foi possível salvar.');
        if (editor.dataset.versaoSalva === versao) mostrarSalvo(aviso, alterado);
        // "Ultima alteracao" do painel reflete esta gravacao na hora, senao ele
        // afirmaria um horario que ja nao e o do dado. A hora vem do SERVIDOR,
        // nunca do relogio do navegador - adiantado, ele mentiria sutilmente.
        if (json.atualizado_em) {
          document.querySelectorAll('[data-atualizado="' + CSS.escape(id) + '"]')
            .forEach(function (el) { el.textContent = json.atualizado_em; });
        }
        // Trocar a categoria pode trocar a NATUREZA, e com ela a
        // obrigatoriedade das dimensoes. So o servidor sabe: releia a flag da
        // resposta em vez de deduzir no cliente.
        if ('exige_dimensoes' in json) {
          const linha = linhaDoEditor(editor);
          if (linha) linha.dataset.exigeDimensoes = json.exige_dimensoes ? '1' : '0';
          if (mexeNaClassificacao) {
            atualizarDestaquesObrigatorios(editor);
            atualizarAvisoClassificacao(editor);
          }
        }
        agendarResumo(editor);
      } catch (e) {
        clearTimeout(aviso._sumir);
        aviso.textContent = e.message; aviso.classList.add('erro'); aviso.classList.remove('sumindo');
        if (window.pdmToast) window.pdmToast(e.message, 'erro');
      } finally {
        if (editor.dataset.versaoSalva === versao) delete editor.dataset.salvando;
      }
    });
    filaSalvar[id] = atual;
    return atual;
  }

  async function cadastrarNovo(select) {
    const anterior = select.dataset.valorAnterior || '';
    const nomeDimensao = select.dataset.dimensaoNome || 'item';
    const nome = window.prompt('Nome do novo ' + nomeDimensao + ':');
    if (!nome || !nome.trim()) { select.value = anterior; return; }
    try {
      const resp = await fetch('/api/dimensao/' + encodeURIComponent(select.dataset.dimensao) + '/valor', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({nome: nome.trim()})
      });
      const json = await resp.json();
      if (!resp.ok || !json.ok) throw new Error(json.erro || 'Não foi possível cadastrar.');
      if (window.pdmToast) window.pdmToast(json.nome + ' cadastrado');
      document.querySelectorAll('[data-dimensao="' + CSS.escape(select.dataset.dimensao) + '"]').forEach(outro => {
        if (!Array.from(outro.options).some(o => o.value === String(json.id))) {
          const opcao = new Option(json.nome, String(json.id));
          const antes = Array.from(outro.options).find(o => (
            o.value && o.value !== '__novo__'
            && json.nome.localeCompare(o.textContent, 'pt-BR', {sensitivity: 'base'}) < 0
          ));
          outro.insertBefore(opcao, antes || outro.querySelector('option[value="__novo__"]'));
        }
      });
      select.value = String(json.id);
      await salvarEditor(select.closest('[data-editor]'), select);
    } catch (e) {
      select.value = anterior;
      alert(e.message);
    }
  }

  const temporizadores = new WeakMap();

  // A pintura de pendencia e a pilula "Faltam:" sao estado da linha, entao
  // precisam ser refeitas quando a tabela e trocada por um filtro.
  function prepararLinhas(raiz) {
    (raiz || document).querySelectorAll('[data-editor]').forEach(editor => {
      atualizarDestaquesObrigatorios(editor);
      atualizarAvisoClassificacao(editor);
    });
  }
  window.pdmPrepararLinhas = prepararLinhas;
  prepararLinhas();

  // Tudo por DELEGACAO: os campos vivem dentro da tabela, e filtrar troca a
  // tabela inteira. Ligados um a um, eles paravam de gravar depois do primeiro
  // filtro - a tela virava somente leitura sem dizer nada, que e pior do que
  // dar erro.
  const SELETOR_CLASSIF = 'select[data-campo],select[data-dimensao]';
  // `focus` nao borbulha; `focusin` e o mesmo evento, borbulhando
  document.addEventListener('focusin', evento => {
    const campo = evento.target.closest(SELETOR_CLASSIF);
    if (campo) campo.dataset.valorAnterior = campo.value;
  });
  document.addEventListener('change', evento => {
    const campo = evento.target.closest(SELETOR_CLASSIF);
    if (!campo) return;
    const editor = campo.closest('[data-editor]');
    if (!editor) return;
    if (campo.value === '__novo__') cadastrarNovo(campo);
    else salvarEditor(editor, campo);
  });
  // A observacao grava sozinha: espera curta enquanto digita e ao sair do campo.
  // A descricao do manual NAO entra aqui - ela mora no painel, atras de um
  // botao, e grava pelo fluxo proprio mais abaixo.
  const SELETOR_TEXTO = 'input[data-campo="observacao"]';
  document.addEventListener('input', evento => {
    const campo = evento.target.closest(SELETOR_TEXTO);
    if (!campo) return;
    const editor = campo.closest('[data-editor]');
    if (!editor) return;
    clearTimeout(temporizadores.get(campo));
    temporizadores.set(campo, setTimeout(() => salvarEditor(editor, campo), 650));
  });
  // `blur` tambem nao borbulha
  document.addEventListener('focusout', evento => {
    const campo = evento.target.closest(SELETOR_TEXTO);
    if (!campo) return;
    const editor = campo.closest('[data-editor]');
    if (!editor) return;
    clearTimeout(temporizadores.get(campo));
    salvarEditor(editor, campo);
  });


  // ---- Descricao do lancamento manual ---------------------------------------
  // Se edita no PAINEL, por um botao, e nunca na linha (decisao do usuario,
  // 10/09/2026): a linha e para ler e classificar, e um campo aberto ali se
  // alterava sem querer. Grava pelo MESMO salvarEditor da linha - nao existe
  // segundo caminho de gravacao - e o servidor recusa a descricao de qualquer
  // lancamento que nao seja manual (secao 4.6).
  document.addEventListener('click', evento => {
    const botao = evento.target.closest('[data-editar-descricao]');
    if (!botao) return;
    evento.stopPropagation();
    const faixa = botao.closest('.vinculo-quem');
    const texto = faixa.querySelector('.vinculo-desc');
    const campo = faixa.querySelector('input[data-campo="descricao"]');
    if (!texto || !campo) return;
    campo.value = texto.textContent;
    texto.hidden = true;
    botao.hidden = true;
    campo.hidden = false;
    campo.focus();
    campo.select();
  });

  async function concluirDescricao(campo, gravar) {
    if (campo.dataset.concluindo) return;
    campo.dataset.concluindo = '1';
    const faixa = campo.closest('.vinculo-quem');
    const texto = faixa.querySelector('.vinculo-desc');
    const botao = faixa.querySelector('[data-editar-descricao]');
    const nova = campo.value.trim();
    // o rateado nao tem `data-editor` na linha, so `data-id`
    const alvo = CSS.escape(campo.dataset.editorDe);
    const linha = document.querySelector('tr[data-editor="' + alvo + '"]')
      || document.querySelector('tr[data-linha][data-id="' + alvo + '"]');
    try {
      if (gravar && linha && nova && nova !== texto.textContent) {
        await salvarEditor(linha, campo);
        const aviso = linha.querySelector('[data-status]');
        // so troca o texto na tela se o servidor aceitou: mostrar uma descricao
        // que nao foi gravada seria a tela mentindo sobre o dado
        if (!(aviso && aviso.classList.contains('erro'))) {
          texto.textContent = nova;
          const loja = linha.querySelector('.desc-loja');
          if (loja) { loja.textContent = nova; loja.dataset.tip = nova; }
        }
      }
    } finally {
      campo.hidden = true;
      texto.hidden = false;
      if (botao) botao.hidden = false;
      delete campo.dataset.concluindo;
    }
  }

  document.addEventListener('keydown', evento => {
    const campo = evento.target.closest && evento.target.closest('input[data-campo="descricao"]');
    if (!campo) return;
    if (evento.key === 'Enter') { evento.preventDefault(); concluirDescricao(campo, true); }
    else if (evento.key === 'Escape') {
      // o Esc e do campo aqui: sem isto, ele tambem fecharia a barra de lote
      evento.preventDefault();
      evento.stopPropagation();
      concluirDescricao(campo, false);
    }
  });
  // `blur` nao borbulha
  document.addEventListener('focusout', evento => {
    const campo = evento.target.closest && evento.target.closest('input[data-campo="descricao"]');
    if (campo && !campo.hidden) concluirDescricao(campo, true);
  });


  // ---- Edicao em lote -----------------------------------------------------
  // Mesma barra da Resumida e o MESMO nucleo (static/lote.js), que por sua vez
  // usa o MESMO POST /api/transacao/<id> de uma edicao normal. Nenhuma regra
  // e reimplementada aqui.
  (function () {
    const barra = document.getElementById('barraLote');
    if (!barra || !window.pdmLote) return;
    const contagem = document.getElementById('loteContagem');
    const saida = document.getElementById('loteResultado');
    const btnSalvar = document.getElementById('loteSalvar');

    function selecionaveis() {
      // Respeita a pesquisa local: a busca esconde o grupo inteiro, e o lote
      // nunca pode alcancar linha que o usuario nao esta vendo.
      return [...document.querySelectorAll('tr[data-linha] .sel-fatura')]
        .filter(cb => {
          const tr = cb.closest('tr');
          return tr && !tr.hidden && tr.style.display !== 'none';
        });
    }
    function marcados() { return selecionaveis().filter(cb => cb.checked); }
    function atualizar() {
      atualizarBotaoSelecao();
      const n = marcados().length;
      // Igual a Resumida: a barra sobrevive a limpeza da selecao enquanto
      // estiver mostrando o resultado da ultima aplicacao.
      barra.hidden = n === 0 && barra.dataset.resultado !== '1';
      contagem.textContent = window.pdmLote.resumoSelecao(
        marcados().map(cb => cb.closest('tr')));
    }

    // Todas, nao so as visiveis: a pesquisa esconde grupos inteiros.
    function desmarcarTudo() {
      document.querySelectorAll('tr[data-linha] .sel-fatura').forEach(cb => { cb.checked = false; });
    }
    function fecharBarra() {
      desmarcarTudo();
      saida.hidden = true;
      barra.dataset.resultado = '';
      atualizar();
    }
    window.pdmLote.ligarFechar(barra, fecharBarra);

    document.addEventListener('change', evento => {
      if (evento.target.classList && evento.target.classList.contains('sel-fatura')) {
        barra.dataset.resultado = '';
        atualizar();
      }
    });
    // Um botao so, como na Resumida: seleciona tudo do filtro e, com tudo
    // marcado, vira "Limpar selecao".
    const btnSelecao = document.getElementById('loteSelecao');
    function tudoMarcado() {
      const total = selecionaveis().length;
      return total > 0 && marcados().length === total;
    }
    function atualizarBotaoSelecao() {
      if (!btnSelecao) return;
      const limpar = tudoMarcado();
      btnSelecao.textContent = limpar ? 'Limpar seleção' : 'Selecionar tudo do filtro';
      btnSelecao.dataset.tip = limpar
        ? 'Desmarca todas as linhas selecionadas'
        : 'Marca todas as linhas que a pesquisa atual está mostrando';
    }
    if (btnSelecao) btnSelecao.addEventListener('click', () => {
      const marcar = !tudoMarcado();
      selecionaveis().forEach(cb => { cb.checked = marcar; });
      if (!marcar) { saida.hidden = true; barra.dataset.resultado = ''; }
      atualizar();
    });

    function montarPayload() {
      const payload = {};
      const cat = document.getElementById('loteCategoria');
      if (cat && cat.value) payload.categoria = cat.value;
      const dims = {};
      barra.querySelectorAll('.lote-dim').forEach(sel => {
        if (sel.value) dims[sel.dataset.dim] = sel.value;
      });
      if (Object.keys(dims).length) payload.dimensoes = dims;
      const obs = document.getElementById('loteObservacao');
      if (obs && obs.value.trim()) payload._observacao = obs.value.trim();
      const ok = document.getElementById('loteOk');
      // Marca o OK; NUNCA desmarca - retirar assinatura e um a um (secao 1.2).
      if (ok && ok.checked) payload.conferida = true;
      return payload;
    }

    btnSalvar.addEventListener('click', async () => {
      const alvos = marcados().map(cb => {
        const tr = cb.closest('tr');
        const editor = document.querySelector('[data-editor="' + CSS.escape(cb.dataset.selLancamento) + '"]');
        const campoObs = editor && editor.querySelector('[data-campo="observacao"]');
        const desc = tr.querySelector('.desc');
        return {
          id: cb.dataset.selLancamento,
          descricao: (desc ? desc.textContent : '').trim().slice(0, 40),
          observacaoAtual: campoObs ? campoObs.value : '',
        };
      });
      if (!alvos.length) return;
      const base = montarPayload();
      if (!Object.keys(base).length) {
        alert('Escolha ao menos um campo para aplicar.');
        return;
      }
      if (!confirm('Aplicar aos ' + alvos.length + ' lançamentos selecionados?')) return;

      btnSalvar.disabled = true;
      saida.hidden = false;
      const r = await window.pdmLote.aplicar(alvos, base, {
        substituirObservacao: document.getElementById('loteObsSubstitui').checked,
        progresso: (feitos, total) => {
          saida.textContent = 'Aplicando ' + feitos + ' de ' + total + '...';
        },
      });
      // A tela nao infere o resultado: recarrega o estado real do servidor,
      // que e quem decide "Faltam:", natureza e OK de cada linha.
      await atualizarResumoPagina(false);
      btnSalvar.disabled = false;
      // Sem recusa, limpa a selecao (a barra fica, com o resultado). Com
      // recusa, mantem: e a lista de quem precisa de nova tentativa.
      if (!r.falhas.length) {
        barra.dataset.resultado = '1';
        desmarcarTudo();
      }
      atualizar();
      saida.hidden = false;
      saida.textContent = r.texto;
      saida.className = 'barra-lote-resultado' + (r.falhas.length ? ' com-falha' : '');
    });

    atualizar();
    const busca = document.querySelector('.busca-fatura input');
    if (busca) busca.addEventListener('input', () => setTimeout(atualizar, 0));
  })();
})();
