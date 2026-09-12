// ---- numero do card: encolhe SO se o texto nao couber, nunca corta ----
// O tamanho de base vem do CSS (container query, .card .val em app.css) -
// ja responsivo a largura do card. Isto e so a rede de seguranca para o que
// o CSS nao enxerga: o COMPRIMENTO do valor. Um total anual de 6 digitos
// num card estreito estourava e cortava com reticencias - dado financeiro
// escondido, o pior defeito possivel aqui (secao 1.1: numeros tem que ser
// reais e visiveis). O piso e --titulo-lg: nunca menor que o tamanho
// original, de antes desta tela ganhar o numero maior.
window.ajustarNumerosDosCards = function(escopo) {
  // 0.8x, nao 1x: o piso "nunca menor que hoje" cobre o caso comum, mas um
  // total anual de 6 digitos num card de 140px (o minimo do grid mobile,
  // secao 7.8-B) ainda estourava mesmo no piso cheio - preferir a fonte um
  // pouco menor a esconder parte de um valor financeiro real (secao 1.1).
  const piso = (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--titulo-lg')) || 21) * 0.8;
  (escopo || document).querySelectorAll('.card .val').forEach(function(el) {
    el.style.fontSize = '';
    let tamanho = parseFloat(getComputedStyle(el).fontSize);
    if (!tamanho) return;
    let tentativas = 0;
    while (el.scrollWidth > el.clientWidth + 1 && tamanho > piso && tentativas < 24) {
      tamanho -= 1;
      el.style.fontSize = tamanho + 'px';
      tentativas++;
    }
  });
};
// O topbar (e este script) vem ANTES do conteudo da pagina no HTML - core.py
// injeta {{ topbar|safe }} antes do bloco `corpo` (base.html). Chamando aqui
// direto, na primeira vez, os cards da pagina ainda nem existem no DOM: o
// querySelectorAll nao acha nada e nenhum numero encolhe. DOMContentLoaded
// espera o parser terminar o documento inteiro.
document.addEventListener('DOMContentLoaded', window.ajustarNumerosDosCards);
window.addEventListener('resize', function() {
  clearTimeout(window._pdmCardsResizeT);
  window._pdmCardsResizeT = setTimeout(window.ajustarNumerosDosCards, 150);
});

// ---- tooltip proprio: o balao nativo do navegador so aparece depois de ~1s ----
(function() {
  let el = null, timer = null;
  function criar() {
    if (!el) {
      el = document.createElement('div');
      el.id = 'tooltip';
      document.body.appendChild(el);
    }
    return el;
  }
  function posicionar(e, t) {
    const m = 14;
    let x = e.clientX + m, y = e.clientY + m;
    const r = t.getBoundingClientRect();
    if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - m;
    if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - m;
    t.style.left = Math.max(6, x) + 'px';
    t.style.top = Math.max(6, y) + 'px';
  }
  document.addEventListener('mouseover', function(e) {
    const alvo = e.target.closest('[data-tip]');
    if (!alvo) return;
    const texto = alvo.getAttribute('data-tip');
    if (!texto) return;
    clearTimeout(timer);
    timer = setTimeout(function() {
      const t = criar();
      t.textContent = texto;
      t.classList.add('show');
      posicionar(e, t);
    }, 120);
  });
  document.addEventListener('mousemove', function(e) {
    if (el && el.classList.contains('show')) posicionar(e, el);
  });
  document.addEventListener('mouseout', function(e) {
    if (!e.target.closest('[data-tip]')) return;
    clearTimeout(timer);
    if (el) el.classList.remove('show');
  });
  // No toque nao existe hover, e o sistema inteiro explica estado por tooltip:
  // os pontos da linha, o avatar do titular, o F/P, o "Faltam:" cortado com
  // reticencias. Sem isto, essa informacao simplesmente nao existe no celular.
  // O tooltip nao intercepta o toque (pointer-events:none), entao a acao da
  // linha continua acontecendo normalmente.
  const noToque = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
  document.addEventListener('click', function(e) {
    clearTimeout(timer);
    const alvo = noToque && e.target.closest && e.target.closest('[data-tip]');
    const texto = alvo && alvo.getAttribute('data-tip');
    if (!texto) {
      if (el) el.classList.remove('show');
      return;
    }
    const t = criar();
    t.textContent = texto;
    t.classList.add('show');
    const r = alvo.getBoundingClientRect();
    posicionar({clientX: r.left + r.width / 2, clientY: r.bottom}, t);
  }, true);
  document.addEventListener('scroll', function() {
    if (el) el.classList.remove('show');
  }, true);
})();

// No celular o menu inteiro recolhe atras de um botao. O mesmo HTML das duas
// larguras: uma barra separada para telefone divergiria da do desktop no
// primeiro item novo.
function menuMobile(btn) {
  const menu = document.getElementById('navMenu');
  if (!menu) return;
  const abrir = !menu.classList.contains('aberto');
  menu.classList.toggle('aberto', abrir);
  btn.setAttribute('aria-expanded', abrir ? 'true' : 'false');
  btn.setAttribute('aria-label', abrir ? 'Fechar menu' : 'Abrir menu');
}

// menu do topo: abre/fecha no clique e fecha ao clicar fora ou apertar Esc
function menuToggle(e, btn) {
  e.stopPropagation();
  const drop = btn.closest('.dropdown');
  const abrir = !drop.classList.contains('aberto');
  document.querySelectorAll('.dropdown.aberto').forEach(d => d.classList.remove('aberto'));
  if (abrir) drop.classList.add('aberto');
  btn.blur();
}
document.addEventListener('click', function(e) {
  if (!e.target.closest('.dropdown')) {
    document.querySelectorAll('.dropdown.aberto').forEach(d => d.classList.remove('aberto'));
  }
});
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') document.querySelectorAll('.dropdown.aberto').forEach(d => d.classList.remove('aberto'));
});

function syncEhSucesso(status) {
  if (!status) return false;
  const s = String(status).toLowerCase();
  return s === 'ok' || s === 'success' || s === 'sucesso';
}
function syncEhAviso(status) {
  return String(status || '').toLowerCase() === 'warning';
}
function syncClasse(status) {
  if (syncEhSucesso(status)) return 'ok';
  if (syncEhAviso(status)) return 'aviso';
  return status ? 'erro' : '';
}
function syncFormatarTexto(d) {
  if (!d.executado_em) return d.status ? 'Falha na sincronização' : 'Sem sincronização registrada';
  let txt = 'Atualizado em ' + d.executado_em;
  if (syncEhAviso(d.status)) txt += ' (atenção)';
  else if (d.status && !syncEhSucesso(d.status)) txt += ' (erro)';
  return txt;
}
async function syncCarregarStatus() {
  // o widget so existe para quem tem permissao de sincronizar
  if (!document.getElementById('syncTexto')) return;
  try {
    const r = await fetch('/api/sync-status');
    const d = await r.json();
    document.getElementById('syncTexto').textContent = syncFormatarTexto(d);
    const dot = document.getElementById('syncDot');
    dot.className = 'sync-dot ' + syncClasse(d.status);
  } catch (e) {
    document.getElementById('syncTexto').textContent = 'Status indisponível';
  }
}
async function dispararSync() {
  const btn = document.getElementById('syncBtn');
  const dot = document.getElementById('syncDot');
  btn.disabled = true;
  btn.textContent = 'Atualizando...';
  dot.className = 'sync-dot rodando';
  document.getElementById('syncTexto').textContent = 'Sincronizando com o Pluggy...';
  try {
    const r = await fetch('/api/sync-agora', { method: 'POST' });
    const d = await r.json();
    document.getElementById('syncTexto').textContent = syncFormatarTexto(d);
    dot.className = 'sync-dot ' + syncClasse(d.status);
  } catch (e) {
    document.getElementById('syncTexto').textContent = 'Falha ao atualizar';
    dot.className = 'sync-dot erro';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Atualizar agora';
  }
}
syncCarregarStatus();

// ---- modo escuro: "sistema" (padrao) segue o SO; o botao grava a escolha ----
function temaEfetivo() {
  var atual = document.documentElement.getAttribute('data-theme');
  if (atual === 'dark' || atual === 'light') return atual;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function atualizarIconeTema() {
  var btn = document.getElementById('temaToggle');
  if (!btn) return;
  var escuro = temaEfetivo() === 'dark';
  btn.textContent = escuro ? '☀️' : '🌙';
  btn.title = escuro ? 'Mudar para modo claro' : 'Mudar para modo escuro';
  btn.setAttribute('aria-label', btn.title);
}
function alternarTema() {
  var novo = temaEfetivo() === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', novo);
  try { localStorage.setItem('pdm_tema', novo === 'dark' ? 'escuro' : 'claro'); } catch (e) {}
  atualizarIconeTema();
}
atualizarIconeTema();
