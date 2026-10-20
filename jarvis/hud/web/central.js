/* A Central dentro da HUD: Agentes IA · Orquestração · Terminal (orquestra.js) e Integrações (JARVIS Connect).
   Fala com o servidor pelo mesmo WebSocket da HUD ({tipo: "central", op, ...}). Tudo que vem do servidor pode conter texto
   de terceiros (nome de contato, assunto de e-mail): por isso NADA aqui usa innerHTML; só textContent e elementos. */
"use strict";

const Central = (() => {
  const C = { aberta: false, secao: "agentes", pedidos: new Map(), seq: 0, dados: {}, filtro: { busca: "", cat: "", sit: "" },
              meta: null, timerMudou: null };
  const ROTULOS = { integracoes: "Integrações" };      // as abas de agentes entram antes (registrarAba)
  const EXTRAS = {};          // abas registradas por outros módulos (ex.: AI Command Center em orquestra.js)
  const ICONE_ABA = { integracoes: "blocks" };
  const SITUACAO = { implementado: "Implementado", em_desenvolvimento: "Em desenvolvimento", planejado: "Planejado" };
  const ESTADO = { conectado: "Conectado", desconectado: "Desconectado", erro: "Erro", aguardando: "Aguardando" };

  /* ---------------- utilidades de DOM (sem innerHTML) */
  function h(tag, attrs, ...filhos) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "texto") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "valor") el.value = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const f of filhos.flat()) if (f != null && f !== false) el.append(f instanceof Node ? f : document.createTextNode(String(f)));
    return el;
  }
  const $c = (id) => document.getElementById(id);
  const quando = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const hoje = new Date();
    const mesmoDia = d.toDateString() === hoje.toDateString();
    return (mesmoDia ? "hoje " : d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) + " ") +
      d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  };
  const botao = (rotulo, ic, aoClicar, classe) => h("button", { type: "button", class: "btn mini " + (classe || ""), onclick: aoClicar },
    ic ? icone(ic) : null, rotulo);

  /* ---------------- conversa com o servidor */
  function pedir(op, extra) {
    return new Promise((resolver, rejeitar) => {
      const pedido = ++C.seq;
      C.pedidos.set(pedido, { resolver, rejeitar });
      enviar({ tipo: "central", op, pedido, ...(extra || {}) });
      setTimeout(() => { if (C.pedidos.delete(pedido)) rejeitar(new Error("o Jarvis não respondeu a tempo")); }, 45000);
    });
  }
  function receber(ev) {
    const p = C.pedidos.get(ev.pedido);
    if (!p) return;
    C.pedidos.delete(ev.pedido);
    ev.ok ? p.resolver(ev.dados) : p.rejeitar(new Error(ev.erro || "erro"));
  }
  function mudou() {          // o motor avisou que algo mudou: recarrega a seção aberta (com folga, para não martelar)
    clearTimeout(C.timerMudou);
    C.timerMudou = setTimeout(() => { if (C.aberta && $c("c-modal").hidden) carregar(C.secao, true); }, 600);
  }
  function erro(e) { aviso("Central: " + (e && e.message || e)); }

  /* ---------------- moldura */
  function montar() {
    const abas = Object.keys(ROTULOS).map((s) => h("button", { type: "button", class: "c-aba", role: "tab", id: "c-aba-" + s,
      "aria-selected": "false", onclick: () => mostrar(s) }, icone(ICONE_ABA[s]), ROTULOS[s], h("span", { class: "n", id: "c-n-" + s, hidden: true })));
    const raiz = h("div", { id: "central", hidden: true, role: "dialog", "aria-modal": "true", "aria-label": "Central do Jarvis",
      onclick: (e) => { if (e.target.id === "central") fechar(); } },
      h("div", { class: "gaveta" },
        h("div", { class: "c-topo" }, icone("layout-grid", "g"), h("h2", {}, "CENTRAL", h("small", { texto: "AGENTES · INTEGRAÇÕES" })),
          h("button", { type: "button", class: "c-fechar", title: "Fechar (Esc)", "aria-label": "Fechar", onclick: fechar }, icone("x"))),
        h("div", { class: "c-abas", role: "tablist" }, abas),
        h("div", { class: "c-corpo", id: "c-corpo" })));
    const modal = h("div", { id: "c-modal", class: "c-modal", hidden: true, onclick: (e) => { if (e.target.id === "c-modal") fecharModal(); } },
      h("div", { class: "caixa", id: "c-caixa", role: "dialog", "aria-modal": "true" }));
    document.body.append(raiz, modal);
    const btn = h("button", { type: "button", class: "btn", id: "btn-central", title: "Central: agentes de IA, orquestração, terminal e integrações",
      onclick: () => abrir() }, icone("layout-grid"), "Central", h("i", { class: "ponto", id: "c-ponto" }));
    document.querySelector(".chips").prepend(btn);
    window.addEventListener("keydown", (e) => {     // antes do Esc da HUD (que é PARAR TUDO): aqui só fecha
      if (e.key !== "Escape" || !C.aberta) return;
      e.stopImmediatePropagation(); e.preventDefault();
      if (!$c("c-modal").hidden) fecharModal(); else fechar();
    }, true);
  }
  function abrir(secao) {
    C.aberta = true;
    $c("central").hidden = false;
    mostrar(secao && ROTULOS[secao] ? secao : ROTULOS[C.secao] ? C.secao : Object.keys(ROTULOS)[0]);
  }
  function fechar() { C.aberta = false; $c("central").hidden = true; fecharModal(); }
  function mostrar(secao) {
    C.secao = secao;
    for (const s of Object.keys(ROTULOS)) $c("c-aba-" + s).setAttribute("aria-selected", String(s === secao));
    carregar(secao);
  }
  async function carregar(secao, silencioso) {
    const corpo = $c("c-corpo");
    if (!silencioso) corpo.replaceChildren(h("div", { class: "c-vazio" }, icone("loader-circle", "gira"), " Carregando…"));
    try {
      if (EXTRAS[secao]) await EXTRAS[secao](api, silencioso);
      else renderIntegracoes(await pedir("integracoes"));
    } catch (e) {
      if (C.secao === secao) corpo.replaceChildren(h("div", { class: "resposta erro", texto: e.message }));
    }
  }
  const secao = (...filhos) => { const s = h("div", { class: "c-secao" }, ...filhos); $c("c-corpo").replaceChildren(s); return s; };
  function contador(sec, n) { const el = $c("c-n-" + sec); if (!el) return; el.hidden = !n; el.textContent = n || ""; }

  /* ---------------- INTEGRAÇÕES */
  function renderIntegracoes(d) {
    C.dados.integracoes = d;
    const pil = (rotulo, campo, valor) => h("button", { type: "button", class: "pilula", "aria-pressed": String(C.filtro[campo] === valor),
      onclick: () => { C.filtro[campo] = C.filtro[campo] === valor ? "" : valor; desenharGrade(); renderFiltros(); } }, rotulo);
    const filtros = h("div", { class: "filtros", id: "c-filtros" });
    function renderFiltros() {
      filtros.replaceChildren(
        h("div", { class: "busca" }, icone("search"), h("input", { type: "search", placeholder: "Buscar integração…", valor: C.filtro.busca,
          "aria-label": "Buscar integração", oninput: (e) => { C.filtro.busca = e.target.value; desenharGrade(); } })),
        ...d.categorias.map((c) => pil(c, "cat", c)),
        pil("Conectados", "sit", "conectado"), pil("Implementados", "sit", "implementado"), pil("Planejados", "sit", "planejado"),
        botao("Atualizar", "refresh-cw", () => pedir("integracoes", { atualizar: true }).then(renderIntegracoes).catch(erro)));
    }
    const grade = h("div", { id: "c-grade" });
    function desenharGrade() {
      const b = C.filtro.busca.trim().toLowerCase();
      const itens = d.itens.filter((i) => (!C.filtro.cat || i.categoria === C.filtro.cat)
        && (!C.filtro.sit || (C.filtro.sit === "conectado" ? i.estado === "conectado" : i.situacao === C.filtro.sit))
        && (!b || (i.nome + " " + i.descricao + " " + i.categoria).toLowerCase().includes(b)));
      const partes = [];
      for (const cat of d.categorias) {
        const daCat = itens.filter((i) => i.categoria === cat);
        if (!daCat.length) continue;
        partes.push(h("div", { class: "cat-titulo", texto: cat }), h("div", { class: "grade" }, daCat.map(cartaoIntegracao)));
      }
      grade.replaceChildren(...(partes.length ? partes : [h("div", { class: "c-vazio", texto: "Nenhuma integração com esse filtro." })]));
    }
    renderFiltros(); desenharGrade();
    secao(filtros, grade,
      h("p", { class: "c-dica", texto: "Implementado = funciona hoje com as credenciais certas · Em desenvolvimento = parte pronta · Planejado = só no catálogo, com documentação e requisitos levantados." }));
  }
  function cartaoIntegracao(i) {
    const acoes = [];
    if (i.situacao !== "planejado") {
      if (i.pode_conectar && i.estado !== "conectado") acoes.push(botao("Conectar", "link", () => conectarIntegracao(i)));
      if (i.campos.length) acoes.push(botao("Configurar", "settings", () => detalhesIntegracao(i, true)));
      acoes.push(botao("Testar", "flask-conical", () => testarIntegracao(i)));
    }
    acoes.push(botao("Detalhes", "chevron-right", () => detalhesIntegracao(i)));
    return h("div", { class: "integ " + i.situacao },
      h("div", { class: "topo-i" }, h("span", { class: "logo-i" }, icone(i.icone, "g")),
        h("div", {}, h("h4", { texto: i.nome }), h("div", { class: "auth-l" }, h("span", { class: "auth", texto: i.autenticacao_nome }),
          h("span", { class: "selo " + i.situacao, texto: SITUACAO[i.situacao] })))),
      h("p", { texto: i.descricao }),
      h("div", { class: "rodape-i" }, h("span", { class: "estado " + i.estado, title: i.detalhe, texto: ESTADO[i.estado] || i.estado }),
        i.ultima_sincronizacao ? h("span", { class: "sinc", title: "Último teste/sincronização", texto: (i.ultima_sincronizacao.ok ? "✓ " : "✕ ") + quando(i.ultima_sincronizacao.quando) }) : null),
      i.detalhe && i.situacao !== "planejado" ? h("span", { class: "auth", texto: i.detalhe }) : null,
      h("div", { class: "rodape-i" }, acoes));
  }
  async function testarIntegracao(i) {
    aviso(`Testando ${i.nome}…`);
    try {
      const r = await pedir("testar", { id: i.id });
      aviso(`${i.nome}: ${r.mensagem}`);
      carregar("integracoes", true);
    } catch (e) { erro(e); }
  }
  async function conectarIntegracao(i) {
    abrirModal(h("h3", {}, icone(i.icone), i.nome), h("div", { class: "c-vazio" }, icone("loader-circle", "gira"), " Iniciando a autorização…"));
    try {
      const r = await pedir("conectar", { id: i.id });
      const corpo = [h("h3", {}, icone(i.icone), i.nome), h("div", { class: "resposta ok", texto: r.mensagem })];
      if (r.qrcode) {
        const img = h("img", { alt: "QR code para parear o WhatsApp" });
        img.src = String(r.qrcode).startsWith("data:image/") ? r.qrcode : "data:image/png;base64," + r.qrcode;
        corpo.push(h("div", { class: "qr" }, img));
      }
      if (r.codigo) corpo.push(h("p", { class: "c-dica", texto: "Ou use o código de pareamento: " + r.codigo }));
      corpo.push(h("div", { class: "acoes-m" }, botao("Testar conexão", "flask-conical", () => { fecharModal(); testarIntegracao(i); }),
        botao("Fechar", "x", fecharModal)));
      abrirModal(...corpo);
      carregar("integracoes", true);
    } catch (e) {
      abrirModal(h("h3", {}, icone(i.icone), i.nome), h("div", { class: "resposta erro", texto: e.message }),
        h("div", { class: "acoes-m" }, botao("Fechar", "x", fecharModal)));
    }
  }
  async function detalhesIntegracao(i, focarForm) {
    const ficha = h("dl", { class: "ficha" },
      h("dt", { texto: "Situação" }), h("dd", { texto: SITUACAO[i.situacao] }),
      h("dt", { texto: "Estado" }), h("dd", {}, h("span", { class: "estado " + i.estado, texto: ESTADO[i.estado] }), " ", i.detalhe || ""),
      h("dt", { texto: "Autenticação" }), h("dd", { texto: i.autenticacao_nome }),
      i.recursos.length ? [h("dt", { texto: "Recursos" }), h("dd", {}, h("ul", {}, i.recursos.map((r) => h("li", { texto: r }))))] : null,
      i.permissoes.length ? [h("dt", { texto: "Permissões" }), h("dd", {}, h("ul", {}, i.permissoes.map((r) => h("li", { texto: r }))))] : null,
      i.requisitos ? [h("dt", { texto: "Requisitos" }), h("dd", { texto: i.requisitos })] : null,
      i.custo ? [h("dt", { texto: "Custo" }), h("dd", { texto: i.custo })] : null,
      i.limites ? [h("dt", { texto: "Limitações" }), h("dd", { texto: i.limites })] : null,
      i.docs ? [h("dt", { texto: "Documentação" }), h("dd", {}, /^https:\/\//.test(i.docs)
        ? h("a", { href: i.docs, target: "_blank", rel: "noopener noreferrer" }, i.docs, " ", icone("external-link")) : i.docs)] : null);
    const partes = [h("h3", {}, icone(i.icone), i.nome), h("p", { class: "sub-m", texto: i.descricao }), ficha];
    let form = null;
    if (i.campos.length && i.situacao !== "planejado") {
      form = h("form", { class: "form-l", onsubmit: async (e) => {
        e.preventDefault();
        const valores = {};
        for (const c of i.campos) valores[c.id] = form.elements[c.id].value;
        try { const r = await pedir("configurar", { id: i.id, valores }); aviso(r.mensagem); fecharModal(); carregar("integracoes", true); }
        catch (e2) { erro(e2); }
      } }, i.campos.map((c) => h("label", {}, c.rotulo, h("input", { class: "c-campo", name: c.id, type: c.segredo ? "password" : "text",
        valor: i.valores[c.id] || "", autocomplete: "off", spellcheck: "false" }), c.dica ? h("small", { texto: c.dica }) : null)),
        h("p", { class: "c-dica", texto: "Chaves e tokens ficam no cofre criptografado deste PC (DPAPI do Windows). A HUD só vê os 4 últimos caracteres." }),
        h("div", { class: "acoes-m" }, h("button", { type: "submit", class: "btn mini" }, icone("save"), "Salvar")));
      partes.push(h("h3", { class: "no-tit" }, icone("settings"), "Configuração"), form);
    }
    const historico = h("div", {}, h("div", { class: "c-vazio" }, icone("loader-circle", "gira"), " Histórico…"));
    partes.push(h("h3", { class: "no-tit" }, icone("history"), "Histórico de eventos"), historico);
    const acoes = [];
    if (i.situacao !== "planejado") {
      if (i.pode_conectar) acoes.push(botao("Conectar", "link", () => conectarIntegracao(i)));
      acoes.push(botao("Testar conexão", "flask-conical", () => { fecharModal(); testarIntegracao(i); }));
      if (i.estado !== "desconectado" || i.campos.length) acoes.push(botao("Desconectar", "unlink", async () => {
        if (!confirm(`Desconectar ${i.nome}? As credenciais deste serviço serão apagadas deste PC.`)) return;
        try { const r = await pedir("desconectar", { id: i.id }); aviso(r.mensagem); fecharModal(); carregar("integracoes", true); } catch (e) { erro(e); }
      }, "perigo"));
    }
    acoes.push(botao("Fechar", "x", fecharModal));
    partes.push(h("div", { class: "acoes-m" }, acoes));
    abrirModal(...partes);
    if (focarForm && form) form.querySelector("input").focus();
    try {
      const r = await pedir("historico", { id: i.id });
      historico.replaceChildren(r.eventos.length ? h("ul", { class: "tempo-l" }, r.eventos.map((e) => h("li", { class: e.nivel },
        h("time", { texto: quando(e.quando) }), e.resumo))) : h("div", { class: "c-vazio", texto: "Sem eventos ainda." }));
    } catch (e) { historico.replaceChildren(h("div", { class: "resposta erro", texto: e.message })); }
  }

  /* ---------------- modal */
  function abrirModal(...filhos) {
    const caixa = $c("c-caixa");
    caixa.className = "caixa";
    caixa.replaceChildren(...filhos);
    $c("c-modal").hidden = false;
    return caixa;
  }
  function fecharModal() { $c("c-modal").hidden = true; $c("c-caixa").replaceChildren(); }

  /* ---------------- extensões: outros módulos ganham abas próprias na Central */
  function registrarAba(id, rotulo, ic, carregarAba) {
    ROTULOS[id] = rotulo; ICONE_ABA[id] = ic; EXTRAS[id] = carregarAba;
    const aba = h("button", { type: "button", class: "c-aba", role: "tab", id: "c-aba-" + id, "aria-selected": "false",
      onclick: () => mostrar(id) }, icone(ic), rotulo, h("span", { class: "n", id: "c-n-" + id, hidden: true }));
    document.querySelector("#central .c-abas").insertBefore(aba, $c("c-aba-integracoes"));
  }
  const api = { h, pedir, secao, botao, abrirModal, fecharModal, quando, erro, contador, $c,
                aberta: () => C.aberta, secaoAtual: () => C.secao, recarregar: (s) => carregar(s || C.secao, true) };

  montar();
  return { abrir, fechar, receber, mudou, carregar, registrarAba, api };
})();
