(function () {
  const configEl = document.querySelector('script[data-config-fatura]');
  let config = {};
  try { config = configEl ? JSON.parse(configEl.textContent) : {}; } catch (e) {}

  function ir(params) {
    const atual = new URL(window.location.href);
    Object.keys(params).forEach(k => {
      if (params[k] === null) atual.searchParams.delete(k);
      else atual.searchParams.set(k, params[k]);
    });
    window.location.assign(atual.pathname + '?' + atual.searchParams.toString());
  }

  const conta = document.getElementById('faturaConta');
  const fatura = document.getElementById('faturaSelecionada');
  const status = document.getElementById('faturaStatus');
  if (conta) conta.addEventListener('change', () => ir({account_id: conta.value, fatura_id: null, status: 'todas'}));
  if (fatura) fatura.addEventListener('change', () => {
    if (fatura.value === 'andamento') ir({andamento: '1', fatura_id: null, status: 'todas'});
    else ir({fatura_id: fatura.value, andamento: null, account_id: null, status: 'todas'});
  });
  if (status) status.addEventListener('change', () => ir({status: status.value}));
  document.addEventListener('click', evento => {
    const card = evento.target.closest('[data-filtro]');
    if (card) ir({status: card.dataset.filtro});
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

  const tabelaFatura = document.querySelector('.fatura-tabela');
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
      const texto = (celula?.textContent || '').replace(/[^0-9,.-]/g, '').replace(/,/g, '');
      return Number(texto) || 0;
    }
    if (chave === 'ok') return linha.querySelector('[data-ok-lancamento]')?.checked ? 1 : 0;
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

  if (tabelaFatura) tabelaFatura.querySelectorAll('th[data-ordenar]').forEach(cabecalho => {
    cabecalho.setAttribute('role', 'button');
    cabecalho.addEventListener('click', () => ordenarFatura(cabecalho));
    cabecalho.addEventListener('keydown', evento => {
      if (evento.key !== 'Enter' && evento.key !== ' ') return;
      evento.preventDefault();
      ordenarFatura(cabecalho);
    });
  });

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

  document.querySelectorAll('[data-expande]').forEach(botao => botao.addEventListener('click', evento => {
    evento.stopPropagation();
    alternarLinha(botao.dataset.expande);
  }));
  document.querySelectorAll('[data-toggle-linha]').forEach(linha => linha.addEventListener('click', evento => {
    if (evento.target.closest('button,input,select,textarea,a,label')) return;
    alternarLinha(linha.dataset.toggleLinha);
  }));
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
  document.querySelectorAll('[data-ok-lancamento]').forEach(campo => campo.addEventListener('change', async () => {
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
      if (json.bloqueada) {
        const nomes = (json.faltando || []).map(String).join(', ');
        let motivo = 'O OK só é liberado quando a classificação estiver completa' + (nomes ? ': ' + nomes + '.' : '.');
        if (json.rateio_invalido) motivo = 'O OK só é liberado quando o rateio estiver completo e fechar exatamente com o lançamento.';
        if (json.pendente_banco) motivo = 'O banco ainda informa que este lançamento está pendente. Aguarde a confirmação bancária para marcar OK.';
        // sem esta linha a tela mandava completar a classificacao mesmo com
        // `faltando` vazio, e o motivo real era a trava da secao 7.5
        if (json.sem_pdf_conciliado) motivo = 'Este lançamento de cartão ainda não está vinculado a nenhuma linha da fatura em PDF. Importe a fatura que cobra este período, ou faça o vínculo em Conciliar fatura.';
        alert(motivo);
      }
      await atualizarResumoPagina(novo && status && status.value === 'pendente_ok');
      aplicarBuscaFatura();
    } catch (e) {
      campo.checked = !novo; campo.disabled = false; alert(e.message);
    }
  }));

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

  function atualizarDestaquesObrigatorios(editor) {
    const categoria = editor.querySelector('[data-campo="categoria"]');
    if (categoria) categoria.classList.toggle('classificacao-faltando', !categoria.value);
    const obrigatorias = new Set((config.dimensoes_obrigatorias || []).map(String));
    editor.querySelectorAll('[data-dimensao]').forEach(campo => {
      campo.classList.toggle(
        'classificacao-faltando', obrigatorias.has(String(campo.dataset.dimensao)) && !campo.value
      );
    });
  }

  function atualizarAvisoClassificacao(editor) {
    // O editor pode ser a PROPRIA linha (classificacao inline) ou o painel
    // aberto abaixo dela. Nos dois casos o aviso mora na linha.
    let destino = null;
    if (editor.matches('tr[data-linha]')) {
      destino = editor.querySelector('[data-classificacao]');
    } else {
      const detalhe = editor.closest('tr.vinculos-detalhe');
      const linha = detalhe && detalhe.previousElementSibling;
      destino = linha && linha.querySelector('[data-classificacao]');
    }
    if (!destino) return;

    const faltando = [];
    const categoria = editor.querySelector('[data-campo="categoria"]');
    if (categoria && !categoria.value) faltando.push('Categoria');
    const obrigatorias = new Set((config.dimensoes_obrigatorias || []).map(String));
    editor.querySelectorAll('[data-dimensao]').forEach(campo => {
      if (!obrigatorias.has(String(campo.dataset.dimensao)) || campo.value) return;
      faltando.push(campo.dataset.dimensaoNome || 'Classificação');
    });

    if (faltando.length) {
      const aviso = document.createElement('span');
      aviso.className = 'estado';
      aviso.style.color = 'var(--warn)';
      aviso.textContent = 'Faltam: ' + faltando.join(', ');
      destino.replaceChildren(aviso);
    } else if (destino.textContent.trim().startsWith('Faltam:')) {
      destino.replaceChildren();
    }
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
      if (classificacao && classificacaoNova) classificacao.innerHTML = classificacaoNova.innerHTML;
      const ok = linha.querySelector('[data-ok-lancamento]');
      const okNovo = nova.querySelector('[data-ok-lancamento]');
      if (ok && okNovo) {
        ok.checked = okNovo.checked;
        ok.title = okNovo.title;
      }
    });
    document.querySelectorAll('[data-editor]').forEach(editor => {
      const novo = doc.querySelector('[data-editor="' + CSS.escape(editor.dataset.editor) + '"]');
      if (!novo || editor.dataset.salvando === '1') return;
      editor.querySelectorAll('[data-campo],[data-dimensao]').forEach(campo => {
        const seletor = campo.dataset.campo
          ? '[data-campo="' + CSS.escape(campo.dataset.campo) + '"]'
          : '[data-dimensao="' + CSS.escape(campo.dataset.dimensao) + '"]';
        const campoNovo = novo.querySelector(seletor);
        if (campoNovo) campo.value = campoNovo.value;
      });
    });
  }

  function salvarEditor(editor, alterado) {
    const id = editor.dataset.editor;
    const aviso = editor.querySelector('[data-status]');
    const payload = payloadEditor(editor, alterado);
    atualizarDestaquesObrigatorios(editor);
    atualizarAvisoClassificacao(editor);
    const versao = String((Number(editor.dataset.versaoSalva || 0) + 1));
    editor.dataset.versaoSalva = versao;
    const anterior = filaSalvar[id] || Promise.resolve();
    const atual = anterior.catch(() => {}).then(async () => {
      editor.dataset.salvando = '1';
      aviso.textContent = 'Salvando…'; aviso.classList.remove('erro');
      try {
        const resp = await fetch('/api/transacao/' + encodeURIComponent(id), {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
        });
        const json = await resp.json();
        if (!resp.ok || !json.ok) throw new Error(json.erro || 'Não foi possível salvar.');
        if (editor.dataset.versaoSalva === versao) aviso.textContent = 'Salvo automaticamente';
        try { await atualizarResumoPagina(); } catch (e) {
          if (editor.dataset.versaoSalva === versao) aviso.textContent = 'Salvo; resumo atualiza ao reabrir';
        }
        aplicarBuscaFatura();
      } catch (e) {
        aviso.textContent = e.message; aviso.classList.add('erro');
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
  document.querySelectorAll('[data-editor]').forEach(editor => {
    atualizarDestaquesObrigatorios(editor);
    atualizarAvisoClassificacao(editor);
    editor.querySelectorAll('select[data-campo],select[data-dimensao]').forEach(campo => {
      campo.addEventListener('focus', () => { campo.dataset.valorAnterior = campo.value; });
      campo.addEventListener('change', () => {
        if (campo.value === '__novo__') cadastrarNovo(campo);
        else salvarEditor(editor, campo);
      });
    });
    editor.querySelectorAll('input[data-campo="observacao"]').forEach(campo => {
      campo.addEventListener('input', () => {
        clearTimeout(temporizadores.get(campo));
        temporizadores.set(campo, setTimeout(() => salvarEditor(editor, campo), 650));
      });
      campo.addEventListener('blur', () => {
        clearTimeout(temporizadores.get(campo));
        salvarEditor(editor, campo);
      });
    });
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
      contagem.textContent = n === 0 ? 'Nenhum lançamento selecionado'
                           : n === 1 ? '1 lançamento selecionado'
                                     : n + ' lançamentos selecionados';
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
