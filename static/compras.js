// Compras futuras. Nenhum item daqui vira lancamento: quem faz isso e a tela
// de Lancamentos, e o vinculo aponta para o lancamento REAL depois de existir.
function criarCompra(e) {
  e.preventDefault();
  const status = document.getElementById('compraStatus');
  status.className = '';
  status.textContent = 'Salvando...';
  const dimensoes = {};
  document.querySelectorAll('.compra-dim').forEach(function (sel) {
    if (sel.value) dimensoes[sel.dataset.dim] = sel.value;
  });
  fetch('/api/compra-futura', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      descricao: document.getElementById('compraDescricao').value,
      valor_previsto: document.getElementById('compraValor').value,
      mes_alvo: document.getElementById('compraMes').value,
      prioridade: document.getElementById('compraPrioridade').value,
      observacao: document.getElementById('compraObservacao').value,
      dimensoes: dimensoes
    })
  }).then(r => r.json()).then(d => {
    if (d.ok) { guardarPosicaoAtual(); window.location.reload(); return; }
    status.className = 'erro';
    status.textContent = d.erro || 'Falha ao salvar';
  }).catch(() => { status.className = 'erro'; status.textContent = 'Falha ao salvar'; });
  return false;
}

// Ao marcar "Comprei", o valor real e perguntado ali mesmo, ja preenchido com o
// previsto: na maioria das vezes e so confirmar, e quando difere o numero certo
// entra sem abrir outra tela.
function abrirConfirmacaoCompra(botao) {
  const celula = botao.closest('td');
  const id = botao.dataset.comprar;
  celula.innerHTML =
    '<div class="confirma-compra">' +
      '<label>Valor real (R$)</label>' +
      '<input type="number" step="0.01" min="0" class="compra-valor-real">' +
      '<button type="button" class="btn-primario" data-confirmar-compra="' + id + '">Confirmar</button>' +
      '<button type="button" class="ver-btn" data-cancelar-compra="1">Cancelar</button>' +
    '</div>';
  const campo = celula.querySelector('.compra-valor-real');
  campo.value = botao.dataset.previsto || '';
  campo.focus();
  campo.select();
}

function alterarCompra(id, corpo) {
  return fetch('/api/compra-futura/' + id, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(corpo)
  }).then(r => r.json()).then(d => {
    if (!d.ok) { alert(d.erro || 'Falha ao salvar'); return false; }
    guardarPosicaoAtual();
    window.location.reload();
    return true;
  }).catch(() => { alert('Falha ao salvar'); return false; });
}

document.addEventListener('click', function (e) {
  const comprar = e.target.closest('[data-comprar]');
  if (comprar) {
    abrirConfirmacaoCompra(comprar);
    return;
  }
  const confirmar = e.target.closest('[data-confirmar-compra]');
  if (confirmar) {
    const celula = confirmar.closest('td');
    const campo = celula.querySelector('.compra-valor-real');
    // Marcar como comprada NAO cria lancamento nenhum: o gasto real entra pela
    // sincronizacao ou pelo lancamento manual, como qualquer outro.
    alterarCompra(confirmar.dataset.confirmarCompra, {
      situacao: 'comprada', valor_real: campo.value
    });
    return;
  }
  const cancelar = e.target.closest('[data-cancelar-compra]');
  if (cancelar) { guardarPosicaoAtual(); window.location.reload(); return; }
  const reabrir = e.target.closest('[data-reabrir]');
  if (reabrir) { alterarCompra(reabrir.dataset.reabrir, {situacao: 'aberta'}); return; }
  const excluir = e.target.closest('[data-excluir]');
  if (excluir) {
    if (!confirm('Excluir este item da lista de compras futuras?')) return;
    fetch('/api/compra-futura/' + excluir.dataset.excluir, {method: 'DELETE'})
      .then(r => r.json()).then(d => {
        if (d.ok) { guardarPosicaoAtual(); window.location.reload(); }
        else alert(d.erro || 'Falha ao excluir');
      }).catch(() => alert('Falha ao excluir'));
  }
});
