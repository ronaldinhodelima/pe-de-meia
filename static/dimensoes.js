// Modal que lista os lancamentos vinculados a um valor de dimensao (ou a todos os
// valores de uma dimensao) "protegido/a" - impedidos de excluir por ter lancamento
// vinculado. Mesmo padrao de categorias.js. Usa escHtml() de tabelas.js.
function fecharModalLancDim() {
  document.getElementById('modalLancDimBg').classList.remove('show');
}

function carregarLancDim(query, titulo) {
  const corpo = document.getElementById('modalLancDimBody');
  document.getElementById('modalLancDimTitulo').textContent = titulo;
  corpo.innerHTML = '<div style="padding:12px 0;color:var(--ink-faint);font-size:var(--fonte-md)">Carregando…</div>';
  document.getElementById('modalLancDimBg').classList.add('show');
  fetch('/api/dimensao-lancamentos?' + query)
    .then(r => r.json())
    .then(lista => {
      if (!lista.length) {
        corpo.innerHTML = '<div style="padding:12px 0;color:var(--ink-faint);font-size:var(--fonte-md)">Nenhum lançamento encontrado.</div>';
        return;
      }
      corpo.innerHTML = lista.map(l =>
        '<div class="row">' +
        '<span>' + escHtml(l.data) + ' — ' + escHtml(l.descricao) + '</span>' +
        '<span>R$ ' + l.valor.toLocaleString('pt-BR', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + '</span></div>'
      ).join('');
    })
    .catch(() => {
      corpo.innerHTML = '<div style="padding:12px 0;color:var(--bad)">Erro ao carregar os lançamentos.</div>';
    });
}

function verLancamentosValor(btn) {
  const valorId = btn.dataset.valor;
  const nome = btn.closest('tr').querySelector('input[name=nome]').value;
  carregarLancDim('valor_id=' + encodeURIComponent(valorId), 'Lançamentos — ' + nome);
}

function verLancamentosDimensao(btn) {
  const dimensaoId = btn.dataset.dimensao;
  const nome = btn.closest('details').querySelector('input[name=nome]').value;
  carregarLancDim('dimensao_id=' + encodeURIComponent(dimensaoId), 'Lançamentos — ' + nome);
}
