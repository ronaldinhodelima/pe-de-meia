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
  if (fatura) fatura.addEventListener('change', () => ir({fatura_id: fatura.value, account_id: null, status: 'todas'}));
  if (status) status.addEventListener('change', () => ir({status: status.value}));
  document.addEventListener('click', evento => {
    const card = evento.target.closest('[data-filtro]');
    if (card) ir({status: card.dataset.filtro});
  });

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

  document.querySelectorAll('[data-ok-lancamento]').forEach(campo => campo.addEventListener('change', async () => {
    const novo = campo.checked;
    let confirmar = false;
    if (!novo) {
      confirmar = window.confirm('Confirma desmarcar o OK deste lançamento?');
      if (!confirmar) { campo.checked = true; return; }
    }
    campo.disabled = true;
    try {
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
        alert('O OK só é liberado quando a classificação estiver completa' + (nomes ? ': ' + nomes : '.'));
      }
      await atualizarResumoPagina();
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
          if (destino) destino.value = String(portfolio);
          payload.dimensoes[config.dim_id_portfolio] = String(portfolio);
        }
      }
    }
    return payload;
  }

  async function atualizarResumoPagina() {
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
      if (!nova) return;
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

  const filaSalvar = {};
  function salvarEditor(editor, alterado) {
    const id = editor.dataset.editor;
    const aviso = editor.querySelector('[data-status]');
    const payload = payloadEditor(editor, alterado);
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
          outro.insertBefore(opcao, outro.querySelector('option[value="__novo__"]'));
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

})();
