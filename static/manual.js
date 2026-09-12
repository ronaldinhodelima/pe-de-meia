// Lancamento manual (dinheiro): o formulario que as DUAS telas de Lancamentos
// usam, com o partial `_form_manual.html`. Ele viveu tempo demais so na
// Resumida - a Detalhada tinha o botao e mandava o usuario para la por um link,
// porque copiar o formulario criaria a segunda implementacao que diverge na
// primeira regra nova (secao 7.1).
//
// Nao existe segundo caminho de gravacao: o mesmo POST /api/lancamento-manual
// de sempre, com as mesmas validacoes e a mesma auditoria.
(function () {
  // chegar com ?manual=1 e o mesmo que ter clicado no "+ manual" da tela
  document.addEventListener('DOMContentLoaded', function () {
    if (new URLSearchParams(window.location.search).get('manual') !== '1') return;
    const f = document.getElementById('formManual');
    if (!f) return;
    f.style.display = 'block';
    f.scrollIntoView({block: 'center'});
    focarData();
  });

  function focarData() {
    const d = document.getElementById('manualData');
    if (d) d.focus();
  }

  window.toggleFormManual = function () {
    const f = document.getElementById('formManual');
    const abrindo = f.style.display === 'none';
    f.style.display = abrindo ? 'block' : 'none';
    if (abrindo) focarData();
  };
  window.salvarManual = function (e) {
    e.preventDefault();
    const statusEl = document.getElementById('manualStatus');
    statusEl.className = '';
    statusEl.textContent = 'Salvando...';
    const dimensoes = {};
    document.querySelectorAll('#formManual .manual-dim').forEach(function (sel) {
      if (sel.value) dimensoes[sel.dataset.dim] = sel.value;
    });
    const conferida = document.getElementById('manualConferida');
    const payload = {
      data: document.getElementById('manualData').value,
      descricao: document.getElementById('manualDescricao').value,
      direcao: document.getElementById('manualDirecao').value,
      valor: document.getElementById('manualValor').value,
      categoria: document.getElementById('manualCategoria').value,
      observacao: document.getElementById('manualObservacao').value,
      dimensoes: dimensoes,
      // O OK aqui e a assinatura de quem esta criando o lancamento - por isso
      // parte SEMPRE de false e so vai marcado quando a pessoa marca a caixa.
      conferida: !!(conferida && conferida.checked)
    };
    fetch('/api/lancamento-manual', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    }).then(r => r.json()).then(d => {
      if (d.ok) {
        if (window.pdmToastAposRecarregar) window.pdmToastAposRecarregar('Lançamento criado');
        if (typeof guardarPosicaoAtual === 'function') guardarPosicaoAtual();
        window.location.reload(); return;
      }
      statusEl.className = 'erro';
      statusEl.textContent = d.erro || 'Falha ao salvar';
      // o servidor diz o que falta; a tela marca os campos em vez de so avisar
      document.querySelectorAll('#formManual .classificacao-faltando')
        .forEach(el => el.classList.remove('classificacao-faltando'));
      (d.faltando_ids || []).forEach(function (dimId) {
        const sel = document.querySelector('#formManual .manual-dim[data-dim="' + dimId + '"]');
        if (sel) sel.classList.add('classificacao-faltando');
        if (window.pdmCombobox && sel) window.pdmCombobox.sincronizar(sel);
      });
      if (d.falta_categoria) {
        const cat = document.getElementById('manualCategoria');
        cat.classList.add('classificacao-faltando');
        if (window.pdmCombobox) window.pdmCombobox.sincronizar(cat);
      }
    }).catch(() => {
      statusEl.className = 'erro';
      statusEl.textContent = 'Falha ao salvar';
    });
    return false;
  };
})();
