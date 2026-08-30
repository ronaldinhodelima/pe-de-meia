(function () {
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
  document.querySelectorAll('[data-filtro]').forEach(card => card.addEventListener('click', () => ir({status: card.dataset.filtro})));

  document.querySelectorAll('[data-expande]').forEach(botao => botao.addEventListener('click', () => {
    const detalhe = document.getElementById('vinculos-' + botao.dataset.expande);
    if (!detalhe) return;
    detalhe.hidden = !detalhe.hidden;
    botao.textContent = detalhe.hidden ? '+' : '−';
    botao.setAttribute('aria-expanded', detalhe.hidden ? 'false' : 'true');
  }));

  document.querySelectorAll('[data-ok-fatura]').forEach(campo => campo.addEventListener('change', async () => {
    const novo = campo.checked;
    let confirmar = false;
    if (!novo) {
      confirmar = window.confirm('Confirma retirar o OK desta linha da fatura?');
      if (!confirmar) { campo.checked = true; return; }
    }
    campo.disabled = true;
    try {
      const resp = await fetch('/api/fatura-linha/' + campo.dataset.okFatura + '/conferida', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({conferida: novo, confirmar_desmarcacao: confirmar})
      });
      const json = await resp.json();
      if (!resp.ok || !json.ok) throw new Error(json.erro || 'Não foi possível salvar.');
      guardarPosicaoAtual(); window.location.reload();
    } catch (e) {
      campo.checked = !novo; campo.disabled = false; alert(e.message);
    }
  }));

  document.querySelectorAll('[data-salvar]').forEach(botao => botao.addEventListener('click', async () => {
    const id = botao.dataset.salvar;
    const editor = document.querySelector('[data-editor="' + CSS.escape(id) + '"]');
    const aviso = editor.querySelector('[data-status]');
    const payload = {dimensoes: {}};
    editor.querySelectorAll('[data-campo]').forEach(c => payload[c.dataset.campo] = c.value);
    editor.querySelectorAll('[data-dimensao]').forEach(c => payload.dimensoes[c.dataset.dimensao] = c.value || null);
    botao.disabled = true; aviso.textContent = 'salvando…'; aviso.classList.add('show');
    try {
      const resp = await fetch('/api/transacao/' + encodeURIComponent(id), {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      const json = await resp.json();
      if (!resp.ok || !json.ok) throw new Error(json.erro || 'Não foi possível salvar.');
      sessionStorage.setItem('faturaAbrirLinha', editor.closest('tr').previousElementSibling.dataset.linha || '');
      guardarPosicaoAtual(); window.location.reload();
    } catch (e) {
      aviso.textContent = e.message; aviso.style.color = 'var(--bad)'; botao.disabled = false;
    }
  }));

  const reabrir = sessionStorage.getItem('faturaAbrirLinha');
  if (reabrir) {
    sessionStorage.removeItem('faturaAbrirLinha');
    const botao = document.querySelector('[data-expande="' + CSS.escape(reabrir) + '"]');
    if (botao) { botao.click(); botao.scrollIntoView({block: 'center'}); }
  }
})();
