// Toast: a confirmacao efemera que aparece no canto superior direito e some
// sozinha. Existe porque a confirmacao estava presa ao lugar onde a acao
// aconteceu - um "ok" minusculo no fim da linha, invisivel se a linha estava
// fora da tela, e um texto que empurrava o layout enquanto o usuario mirava o
// campo seguinte (secao 7.2).
//
// Uma definicao para todo o sistema: toda acao que grava no banco confirma
// aqui. Duas implementacoes divergiriam na primeira regra nova.
(function () {
  const DURACAO = {sucesso: 2600, erro: 6000, aviso: 4200};
  let caixa = null;
  // O sucesso ocupa UM balao, que se atualiza. Tabular por Categoria,
  // Responsavel, Projeto e Portfolio gerava quatro baloes empilhados, e a
  // pilha descia sobre as primeiras linhas justamente enquanto o usuario
  // preenchia. Erro e aviso continuam podendo empilhar: sao raros, e perder um
  // deles esconde a unica pista do que aconteceu.
  let sucessoAtual = null;

  function container() {
    if (caixa && document.body.contains(caixa)) return caixa;
    caixa = document.createElement('div');
    caixa.className = 'toast-pilha';
    // aria-live polite: o leitor de tela anuncia sem interromper o que o
    // usuario esta fazendo. Nao e alert() - a acao ja terminou.
    caixa.setAttribute('aria-live', 'polite');
    caixa.setAttribute('role', 'status');
    document.body.appendChild(caixa);
    return caixa;
  }

  // tipo: 'sucesso' (padrao), 'erro' ou 'aviso'. Erro dura mais e pode ser
  // fechado no clique: ali a mensagem e a unica pista do que aconteceu.
  function toast(mensagem, tipo) {
    if (!mensagem) return;
    tipo = tipo || 'sucesso';
    if (tipo === 'sucesso' && sucessoAtual && document.body.contains(sucessoAtual)) {
      // mesmo balao, texto novo, tempo reiniciado
      sucessoAtual._texto.textContent = mensagem;
      clearTimeout(sucessoAtual._sumir);
      sucessoAtual._sumir = setTimeout(function () { sair(sucessoAtual); }, DURACAO.sucesso);
      return sucessoAtual;
    }
    const el = document.createElement('div');
    el.className = 'toast toast-' + tipo;
    const icone = document.createElement('span');
    icone.className = 'toast-icone';
    icone.textContent = tipo === 'erro' ? '!' : tipo === 'aviso' ? '!' : '✓';
    const texto = document.createElement('span');
    texto.textContent = mensagem;
    el._texto = texto;
    el.appendChild(icone);
    el.appendChild(texto);
    // so erro e aviso interceptam o clique (para poder fechar). O balao de
    // sucesso e transparente ao ponteiro: no canto superior direito ele cobre
    // Valor, Observacao e OK das primeiras linhas, e engolia o clique delas.
    if (tipo !== 'sucesso') el.addEventListener('click', function () { sair(el); });
    else sucessoAtual = el;
    container().appendChild(el);
    // deixa o navegador pintar o estado inicial antes de animar a entrada
    requestAnimationFrame(function () { el.classList.add('aberto'); });
    const prazo = DURACAO[tipo] || DURACAO.sucesso;
    el._sumir = setTimeout(function () { sair(el); }, prazo);
    return el;
  }

  function sair(el) {
    if (el === sucessoAtual) sucessoAtual = null;
    clearTimeout(el._sumir);
    el.classList.remove('aberto');
    setTimeout(function () { el.remove(); }, 260);
  }

  // Acao que termina recarregando a pagina (o rateio, os formularios de
  // cadastro) perderia o toast no meio do caminho: a mensagem fica guardada e
  // aparece do outro lado. sessionStorage, nao localStorage - e uma
  // confirmacao daquela aba, nao um estado do usuario.
  function toastAposRecarregar(mensagem, tipo) {
    try { sessionStorage.setItem('pdm_toast', JSON.stringify({m: mensagem, t: tipo || 'sucesso'})); }
    catch (e) { /* aba anonima ou storage bloqueado: a acao ja aconteceu */ }
  }

  document.addEventListener('DOMContentLoaded', function () {
    let guardado = null;
    try {
      guardado = sessionStorage.getItem('pdm_toast');
      sessionStorage.removeItem('pdm_toast');
    } catch (e) { return; }
    if (!guardado) return;
    try {
      const dado = JSON.parse(guardado);
      toast(dado.m, dado.t);
    } catch (e) { /* conteudo estranho: ignora em silencio */ }
  });

  // O rotulo do campo sai do proprio `aria-label` (Categoria, Responsavel,
  // Projeto, Portfolio...). Ele ja existe em todos os campos por acessibilidade
  // e vem do BANCO no caso das dimensoes - uma segunda lista de nomes aqui
  // divergiria na primeira dimensao nova.
  function rotuloDoCampo(el) {
    if (!el) return '';
    const aria = el.getAttribute && el.getAttribute('aria-label');
    if (aria) return aria.replace(/^Valor da parte.*$/, 'Valor');
    const campo = el.dataset && el.dataset.campo;
    if (campo === 'observacao') return 'Observação';
    if (campo === 'descricao') return 'Descrição';
    if (el.classList && el.classList.contains('obs-input')) return 'Observação';
    return '';
  }

  // "Salvo · Categoria" diz O QUE foi gravado. So "salvo" obriga o usuario a
  // lembrar em que campo estava - e ele acabou de mexer em quatro.
  function toastSalvo(el, alternativa) {
    const rotulo = rotuloDoCampo(el);
    return toast(rotulo ? 'Salvo · ' + rotulo : (alternativa || 'Lançamento salvo'));
  }

  window.pdmToast = toast;
  window.pdmToastSalvo = toastSalvo;
  window.pdmToastAposRecarregar = toastAposRecarregar;
})();
