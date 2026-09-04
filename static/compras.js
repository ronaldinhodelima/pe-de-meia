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
    // Marcar como comprada NAO cria lancamento nenhum: o gasto real entra pela
    // sincronizacao ou pelo lancamento manual, como qualquer outro.
    if (!confirm('Marcar como comprada? Isso não cria lançamento — o gasto real entra pela sincronização ou por um lançamento manual.')) return;
    alterarCompra(comprar.dataset.comprar, {situacao: 'comprada'});
    return;
  }
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
