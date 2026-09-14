/* Centro de Custos: desenha a arvore, salva sozinho e arrasta ficha entre
   subgrupos.

   Um desenho so, aqui: o Jinja entrega o ESTADO e nao o HTML da arvore. Se o
   template montasse a arvore tambem, as duas versoes divergiriam na primeira
   regra nova - e a divergencia so apareceria depois de uma edicao, com a tela
   mostrando uma coisa e o DRE contando outra.

   Toda gravacao passa pelo mesmo `salvar()`, que devolve o estado inteiro
   recalculado pelo servidor. A tela nunca adivinha o resultado: qual regra
   vence depende das outras regras da mesma categoria, entao prever aqui daria
   a resposta errada justamente no caso que motivou a tela. */
(function () {
  'use strict';

  const raiz = document.getElementById('ccArvore');
  const zonaSoltas = document.getElementById('ccSoltas');
  if (!raiz || !zonaSoltas) return;

  let estado = {grupos: [], dimensoes: [], categorias: []};
  try {
    estado = JSON.parse(document.getElementById('ccEstado').textContent);
  } catch (e) {
    estado = {grupos: [], dimensoes: [], categorias: []};
  }

  // formulario aberto (subgrupo_id) e regra em edicao (regra_id): sao estado da
  // TELA, nao do dado - por isso vivem aqui e nao voltam do servidor
  let formAberto = null;
  let regraEmEdicao = null;

  function toast(msg, tipo) {
    if (window.pdmToast) window.pdmToast(msg, tipo);
  }

  // Chevron desenhado, e nao o glifo "⌄": aquele caractere tem a baseline
  // pendurada e assenta abaixo da linha da ficha por mais que o flex
  // centralize a CAIXA - centralizar a caixa nao centraliza o desenho dentro
  // dela. O tamanho vem do CSS (.cc-icone svg), para nao nascer valor cru aqui.
  const CHEVRON = '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor"'
    + ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6.5 8 10.5 12 6.5"/></svg>';

  async function salvar(payload) {
    try {
      const resp = await fetch('/api/centro-custo', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const dados = await resp.json().catch(() => ({}));
      if (!resp.ok || !dados.ok) {
        toast(dados.erro || 'Não foi possível salvar.', 'erro');
        return false;
      }
      estado = dados.estado;
      desenhar();
      toast(dados.aviso || 'Salvo', 'ok');
      return true;
    } catch (e) {
      toast('Falha de conexão ao salvar.', 'erro');
      return false;
    }
  }

  // ---------- desenho ----------

  function fichaHtml(regra) {
    const especifica = (regra.condicoes.length ? ' cc-especifica' : '')
      + (regra.id === regraEmEdicao ? ' cc-em-edicao' : '');
    const condicoes = regra.condicoes.map(c =>
      `<span class="cc-cond"><b>${escHtml(c.dimensao_nome)}</b> ${escHtml(c.valor_nome)}</span>`
    ).join('');
    return `
      <div class="cc-ficha${especifica}" draggable="true" data-regra="${regra.id}">
        <div class="cc-ficha-corpo">
          <span class="cc-ficha-cat" title="${escHtml(regra.rotulo)}">${escHtml(regra.categoria_nome)}</span>
          ${condicoes ? `<div class="cc-condicoes">${condicoes}</div>` : ''}
        </div>
        <button type="button" class="cc-icone" data-editar="${regra.id}"
                aria-expanded="${regraEmEdicao === regra.id}"
                title="Condições de Projeto, Portfólio ou outra dimensão" aria-label="Editar condições">${CHEVRON}</button>
        <button type="button" class="cc-icone cc-perigo" data-excluir-regra="${regra.id}"
                title="Desvincular" aria-label="Desvincular">×</button>
      </div>`;
  }

  function opcoesCategoria() {
    // a categoria ja vinculada continua na lista: o mesmo "Seguros" pode ter
    // uma regra por condicao em cada centro, que e o ponto da tela
    return estado.categorias.map(c =>
      `<option value="${escHtml(c.chave)}">${escHtml(c.nome)}${c.vinculada ? ' ·' : ''}</option>`
    ).join('');
  }

  function editorCondicaoHtml(cond) {
    const dims = estado.dimensoes.map(d =>
      `<option value="${d.id}"${cond && cond.dimensao_id === d.id ? ' selected' : ''}>${escHtml(d.nome)}</option>`
    ).join('');
    const dimAtual = estado.dimensoes.find(d => cond && d.id === cond.dimensao_id) || estado.dimensoes[0];
    const valores = ((dimAtual && dimAtual.valores) || []).map(v =>
      `<option value="${v.id}"${cond && cond.valor_id === v.id ? ' selected' : ''}>${escHtml(v.nome)}</option>`
    ).join('');
    return `
      <div class="cc-cond-editor" data-cond>
        <select class="campo-caixa" data-cond-dim aria-label="Dimensão">${dims}</select>
        <select class="campo-caixa" data-cond-valor aria-label="Valor">${valores}</select>
        <button type="button" class="cc-icone cc-perigo" data-remover-cond title="Remover condição" aria-label="Remover condição">×</button>
      </div>`;
  }

  function formHtml(subgrupoId, regra) {
    const condicoes = (regra ? regra.condicoes : []).map(editorCondicaoHtml).join('');
    return `
      <div class="cc-form" data-form="${subgrupoId}" ${regra ? `data-regra-edicao="${regra.id}"` : ''}>
        ${regra ? `<span class="cc-form-rotulo">Condições de ${escHtml(regra.categoria_nome)}</span>` : `
        <label class="cc-form-rotulo">Categoria
          <select class="campo-caixa" data-nova-categoria style="width:100%;margin-top:4px">${opcoesCategoria()}</select>
        </label>`}
        <div data-lista-cond>${condicoes}</div>
        <button type="button" class="cc-add" data-add-cond>+ condição (Projeto, Portfólio…)</button>
        <div class="cc-form-acoes">
          <button type="button" class="ver-btn" data-cancelar>Cancelar</button>
          <button type="button" class="btn-primario" data-confirmar>${regra ? 'Salvar' : 'Vincular'}</button>
        </div>
      </div>`;
  }

  function trilhaHtml(sub) {
    // O editor nasce LOGO ABAIXO da ficha que o abriu (pedido do usuario,
    // 14/09/2026), empurrando o resto da trilha para baixo. No fim da lista ele
    // aparecia longe do item escolhido, e em trilha cheia era preciso procurar
    // qual ficha estava sendo editada.
    const fichas = sub.regras.map(regra => {
      const editor = regra.id === regraEmEdicao ? formHtml(sub.id, regra) : '';
      return fichaHtml(regra) + editor;
    }).join('');
    return `
      <div class="cc-trilha" data-subgrupo="${sub.id}">
        <div class="cc-trilha-topo">
          <input class="cc-trilha-nome" value="${escHtml(sub.nome)}" data-nome-subgrupo="${sub.id}" aria-label="Nome do subgrupo">
          <button type="button" class="cc-icone cc-perigo" data-excluir-subgrupo="${sub.id}"
                  title="Excluir subgrupo" aria-label="Excluir subgrupo">×</button>
        </div>
        <div class="cc-fichas" data-fichas="${sub.id}">
          ${fichas || '<div class="cc-vazio">Sem categorias. Arraste uma para cá.</div>'}
        </div>
        ${formAberto === sub.id ? formHtml(sub.id, null) : `
        <button type="button" class="cc-add" data-abrir-form="${sub.id}">+ categoria</button>`}
      </div>`;
  }

  function grupoHtml(grupo) {
    const regras = grupo.subgrupos.reduce((n, s) => n + s.regras.length, 0);
    const trilhas = grupo.subgrupos.map(trilhaHtml).join('');
    return `
      <article class="cc-grupo" data-grupo="${grupo.id}">
        <header class="cc-grupo-topo">
          <input class="cc-grupo-nome" value="${escHtml(grupo.nome)}" data-nome-grupo="${grupo.id}" aria-label="Nome do centro de custo">
          <span class="cc-contador">${grupo.subgrupos.length} subgrupo${grupo.subgrupos.length === 1 ? '' : 's'} · ${regras} regra${regras === 1 ? '' : 's'}</span>
          <button type="button" class="cc-icone cc-perigo" data-excluir-grupo="${grupo.id}"
                  title="Excluir centro de custo" aria-label="Excluir centro de custo">×</button>
        </header>
        <div class="cc-trilhas">
          ${trilhas}
          <div class="cc-trilha">
            <span class="cc-form-rotulo">Novo subgrupo</span>
            <input class="campo-caixa" data-novo-subgrupo="${grupo.id}" placeholder="Nome do subgrupo" aria-label="Nome do novo subgrupo">
            <button type="button" class="cc-add" data-criar-subgrupo="${grupo.id}">+ adicionar</button>
          </div>
        </div>
      </article>`;
  }

  function desenhar() {
    raiz.innerHTML = estado.grupos.map(grupoHtml).join('')
      || '<div class="cc-vazio">Nenhum centro de custo ainda. Crie o primeiro acima.</div>';

    const soltas = estado.categorias.filter(c => !c.vinculada);
    zonaSoltas.innerHTML = `
      <h3>Categorias sem centro de custo</h3>
      <div class="cc-sub">Não entram nos totais por grupo do DRE. Arraste uma ficha para cá para desvincular.</div>
      <div class="cc-soltas-lista">
        ${soltas.map(c => `<span class="cc-solta">${escHtml(c.nome)}</span>`).join('')
          || '<span class="cc-vazio">Todas as categorias de despesa têm centro de custo.</span>'}
      </div>`;
  }

  // ---------- arrastar e soltar ----------
  // A ficha e a unidade que se move: arrastar = trocar o subgrupo da regra.
  // Soltar na zona de soltas apaga a regra (a categoria volta a nao ter centro).

  let arrastando = null;

  document.addEventListener('dragstart', function (e) {
    const ficha = e.target.closest && e.target.closest('.cc-ficha');
    if (!ficha) return;
    arrastando = Number(ficha.dataset.regra);
    ficha.classList.add('cc-arrastando');
    e.dataTransfer.effectAllowed = 'move';
    // Firefox so inicia o arrasto com algum dado no dataTransfer
    e.dataTransfer.setData('text/plain', String(arrastando));
  });

  document.addEventListener('dragend', function () {
    arrastando = null;
    document.querySelectorAll('.cc-arrastando').forEach(el => el.classList.remove('cc-arrastando'));
    document.querySelectorAll('.cc-alvo').forEach(el => el.classList.remove('cc-alvo'));
  });

  function alvoDe(e) {
    if (!e.target.closest) return null;
    return e.target.closest('.cc-trilha[data-subgrupo]') || e.target.closest('#ccSoltas');
  }

  document.addEventListener('dragover', function (e) {
    if (arrastando === null) return;
    const alvo = alvoDe(e);
    if (!alvo) return;
    e.preventDefault();               // sem isto o drop nunca acontece
    e.dataTransfer.dropEffect = 'move';
    document.querySelectorAll('.cc-alvo').forEach(el => {
      if (el !== alvo) el.classList.remove('cc-alvo');
    });
    alvo.classList.add('cc-alvo');
  });

  document.addEventListener('drop', function (e) {
    if (arrastando === null) return;
    const alvo = alvoDe(e);
    if (!alvo) return;
    e.preventDefault();
    const regraId = arrastando;
    arrastando = null;
    alvo.classList.remove('cc-alvo');
    if (alvo.id === 'ccSoltas') {
      salvar({acao: 'excluir_regra', regra_id: regraId});
      return;
    }
    salvar({acao: 'mover_regra', regra_id: regraId, subgrupo_id: Number(alvo.dataset.subgrupo)});
  });

  // ---------- cliques ----------
  // Delegacao: a arvore inteira e redesenhada a cada gravacao, entao listener
  // preso ao elemento morreria junto com ele (a licao do seletor trocado por
  // AJAX, seccao 7.1).

  document.addEventListener('click', function (e) {
    const alvo = e.target.closest && e.target.closest('[data-cc-acao],[data-abrir-form],[data-cancelar],'
      + '[data-confirmar],[data-add-cond],[data-remover-cond],[data-editar],[data-excluir-regra],'
      + '[data-excluir-subgrupo],[data-excluir-grupo],[data-criar-subgrupo]');
    if (!alvo) return;

    if (alvo.dataset.ccAcao === 'criar_grupo') {
      const campo = document.getElementById('ccNovoGrupo');
      const nome = campo.value.trim();
      if (!nome) { campo.focus(); return; }
      campo.value = '';
      salvar({acao: 'criar_grupo', nome: nome});
      return;
    }

    if (alvo.dataset.criarSubgrupo) {
      const grupoId = alvo.dataset.criarSubgrupo;
      const campo = raiz.querySelector(`[data-novo-subgrupo="${grupoId}"]`);
      const nome = campo.value.trim();
      if (!nome) { campo.focus(); return; }
      salvar({acao: 'criar_subgrupo', grupo_id: Number(grupoId), nome: nome});
      return;
    }

    if (alvo.dataset.abrirForm) {
      formAberto = Number(alvo.dataset.abrirForm);
      regraEmEdicao = null;
      desenhar();
      const select = raiz.querySelector('[data-nova-categoria]');
      if (select) select.focus();
      return;
    }

    if (alvo.dataset.editar) {
      regraEmEdicao = Number(alvo.dataset.editar);
      formAberto = null;
      desenhar();
      return;
    }

    if (alvo.hasAttribute('data-cancelar')) {
      formAberto = null;
      regraEmEdicao = null;
      desenhar();
      return;
    }

    if (alvo.hasAttribute('data-add-cond')) {
      const lista = alvo.closest('.cc-form').querySelector('[data-lista-cond]');
      lista.insertAdjacentHTML('beforeend', editorCondicaoHtml(null));
      return;
    }

    if (alvo.hasAttribute('data-remover-cond')) {
      alvo.closest('[data-cond]').remove();
      return;
    }

    if (alvo.hasAttribute('data-confirmar')) {
      const form = alvo.closest('.cc-form');
      const condicoes = [...form.querySelectorAll('[data-cond]')].map(linha => ({
        dimensao_id: Number(linha.querySelector('[data-cond-dim]').value),
        valor_id: Number(linha.querySelector('[data-cond-valor]').value),
      })).filter(c => c.dimensao_id && c.valor_id);
      const edicao = form.dataset.regraEdicao;
      formAberto = null;
      regraEmEdicao = null;
      if (edicao) {
        salvar({acao: 'atualizar_condicoes', regra_id: Number(edicao), condicoes: condicoes});
      } else {
        salvar({
          acao: 'criar_regra',
          categoria: form.querySelector('[data-nova-categoria]').value,
          subgrupo_id: Number(form.dataset.form),
          condicoes: condicoes,
        });
      }
      return;
    }

    if (alvo.dataset.excluirRegra) {
      salvar({acao: 'excluir_regra', regra_id: Number(alvo.dataset.excluirRegra)});
      return;
    }

    // Excluir subgrupo e centro de custo mexem no que o DRE soma: confirma.
    if (alvo.dataset.excluirSubgrupo) {
      if (!confirm('Excluir o subgrupo? As categorias dele ficam sem centro de custo.')) return;
      salvar({acao: 'excluir_subgrupo', subgrupo_id: Number(alvo.dataset.excluirSubgrupo)});
      return;
    }

    if (alvo.dataset.excluirGrupo) {
      if (!confirm('Excluir o centro de custo inteiro, com os subgrupos dele?')) return;
      salvar({acao: 'excluir_grupo', grupo_id: Number(alvo.dataset.excluirGrupo)});
    }
  });

  // trocar a dimensao troca a lista de valores: um valor so existe dentro da
  // dimensao dele, e o servidor recusa o par errado
  document.addEventListener('change', function (e) {
    const select = e.target.closest && e.target.closest('[data-cond-dim]');
    if (!select) return;
    const dim = estado.dimensoes.find(d => d.id === Number(select.value));
    const destino = select.closest('[data-cond]').querySelector('[data-cond-valor]');
    destino.innerHTML = ((dim && dim.valores) || []).map(v =>
      `<option value="${v.id}">${escHtml(v.nome)}</option>`
    ).join('');
  });

  // ---------- nome inline ----------
  // Enter grava e sai; Esc desfaz. Sem botao Salvar: o campo JA e o dado.

  function nomeDe(campo) {
    if (campo.dataset.nomeGrupo) {
      return {acao: 'renomear_grupo', grupo_id: Number(campo.dataset.nomeGrupo)};
    }
    if (campo.dataset.nomeSubgrupo) {
      return {acao: 'renomear_subgrupo', subgrupo_id: Number(campo.dataset.nomeSubgrupo)};
    }
    return null;
  }

  document.addEventListener('focusin', function (e) {
    if (e.target.dataset && (e.target.dataset.nomeGrupo || e.target.dataset.nomeSubgrupo)) {
      e.target.dataset.valorAnterior = e.target.value;
    }
  });

  document.addEventListener('focusout', function (e) {
    const campo = e.target;
    if (!campo.dataset) return;
    const acao = nomeDe(campo);
    if (!acao) return;
    const nome = campo.value.trim();
    if (!nome) { campo.value = campo.dataset.valorAnterior || ''; return; }
    if (nome === campo.dataset.valorAnterior) return;
    salvar(Object.assign(acao, {nome: nome}));
  });

  document.addEventListener('keydown', function (e) {
    const campo = e.target;
    if (!campo.dataset) return;
    if (campo.dataset.nomeGrupo || campo.dataset.nomeSubgrupo) {
      if (e.key === 'Enter') { e.preventDefault(); campo.blur(); }
      if (e.key === 'Escape') { campo.value = campo.dataset.valorAnterior || ''; campo.blur(); }
      return;
    }
    if (e.key !== 'Enter') return;
    if (campo.id === 'ccNovoGrupo') {
      e.preventDefault();
      const nome = campo.value.trim();
      if (nome) { campo.value = ''; salvar({acao: 'criar_grupo', nome: nome}); }
      return;
    }
    if (campo.dataset.novoSubgrupo) {
      e.preventDefault();
      const nome = campo.value.trim();
      if (nome) salvar({acao: 'criar_subgrupo', grupo_id: Number(campo.dataset.novoSubgrupo), nome: nome});
    }
  });

  desenhar();
})();
