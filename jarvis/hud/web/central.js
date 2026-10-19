/* JARVIS Connect: a Central dentro da HUD (Painel · Integrações · Tarefas · Automações).
   Fala com o servidor pelo mesmo WebSocket da HUD ({tipo: "central", op, ...}). Tudo que vem do servidor pode conter texto
   de terceiros (nome de contato, assunto de e-mail): por isso NADA aqui usa innerHTML; só textContent e elementos. */
"use strict";

const Central = (() => {
  const C = { aberta: false, secao: "painel", pedidos: new Map(), seq: 0, dados: {}, filtro: { busca: "", cat: "", sit: "" },
              statusTarefa: "pendente", meta: null, timerMudou: null };
  const ROTULOS = { painel: "Painel", integracoes: "Integrações", tarefas: "Tarefas", automacoes: "Automações" };
  const ICONE_ABA = { painel: "layout-dashboard", integracoes: "blocks", tarefas: "list-todo", automacoes: "workflow" };
  const SITUACAO = { implementado: "Implementado", em_desenvolvimento: "Em desenvolvimento", planejado: "Planejado" };
  const ESTADO = { conectado: "Conectado", desconectado: "Desconectado", erro: "Erro", aguardando: "Aguardando" };
  const PRIORIDADES = ["urgente", "alta", "normal", "baixa"];
  const DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];
  const ROTULO_EVENTO = { "whatsapp.mensagem": "Mensagem no WhatsApp", "gmail.novo": "E-mail novo",
                          "agenda.proxima": "Compromisso chegando", webhook: "Webhook" };
  const ROTULO_OP = { contem: "contém", nao_contem: "não contém", igual: "é igual a", comeca: "começa com", regex: "regex",
                      de: "é de (quem mandou)", ia: "a IA responde SIM a" };
  const ROTULO_CAMPO = { texto: "Texto", titulo: "Título", descricao: "Descrição", prioridade: "Prioridade", busca: "Busca do Gmail",
                         quantos: "Quantos", dias: "Dias", pedido: "Pedido para a IA", contexto: "Sobre o quê (contexto)",
                         json: "Responder em JSON", para: "Para", assunto: "Assunto", inicio: "Início (AAAA-MM-DDTHH:MM)",
                         duracao_min: "Duração (min)" };

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
        h("div", { class: "c-topo" }, icone("layout-grid", "g"), h("h2", {}, "CENTRAL", h("small", { texto: "JARVIS CONNECT" })),
          h("button", { type: "button", class: "c-fechar", title: "Fechar (Esc)", "aria-label": "Fechar", onclick: fechar }, icone("x"))),
        h("div", { class: "c-abas", role: "tablist" }, abas),
        h("div", { class: "c-corpo", id: "c-corpo" })));
    const modal = h("div", { id: "c-modal", class: "c-modal", hidden: true, onclick: (e) => { if (e.target.id === "c-modal") fecharModal(); } },
      h("div", { class: "caixa", id: "c-caixa", role: "dialog", "aria-modal": "true" }));
    document.body.append(raiz, modal);
    const btn = h("button", { type: "button", class: "btn", id: "btn-central", title: "Central: integrações, tarefas e automações",
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
    mostrar(secao && ROTULOS[secao] ? secao : C.secao);
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
      if (secao === "painel") renderPainel(await pedir("painel"));
      else if (secao === "integracoes") renderIntegracoes(await pedir("integracoes"));
      else if (secao === "tarefas") renderTarefas(await pedir("tarefas", { status: C.statusTarefa }));
      else renderAutomacoes(await pedir("automacoes"));
    } catch (e) {
      if (C.secao === secao) corpo.replaceChildren(h("div", { class: "resposta erro", texto: e.message }));
    }
  }
  const secao = (...filhos) => { const s = h("div", { class: "c-secao" }, ...filhos); $c("c-corpo").replaceChildren(s); return s; };
  function contador(sec, n) { const el = $c("c-n-" + sec); el.hidden = !n; el.textContent = n || ""; }

  /* ---------------- PAINEL */
  function renderPainel(d) {
    C.dados.painel = d;
    contador("tarefas", d.tarefas_pendentes);
    const ponto = $c("c-ponto");
    ponto.className = "ponto " + (d.com_erro.length || d.falhas_hoje ? "erro" : d.integracoes_ativas ? "on" : "");
    const kpi = (valor, rotulo, ic, alerta) => h("div", { class: "kpi" + (alerta ? " alerta" : "") }, icone(ic), h("b", { texto: String(valor) }), h("span", { texto: rotulo }));
    const maxConsumo = Math.max(1, ...d.consumo.map((c) => c.chamadas));
    secao(
      h("div", { class: "kpis" },
        kpi(`${d.integracoes_ativas}/${d.integracoes_total}`, "Integrações ativas", "plug"),
        kpi(d.automacoes_ativas, "Automações ligadas", "workflow"),
        kpi(d.rodando, "Rodando agora", "activity"),
        kpi(d.execucoes_hoje, "Execuções hoje", "zap"),
        kpi(d.tarefas_pendentes, "Tarefas pendentes", "list-todo"),
        kpi(d.tarefas_concluidas, "Concluídas", "circle-check"),
        kpi(d.falhas_hoje, "Falhas hoje", "circle-alert", d.falhas_hoje > 0)),
      h("div", { class: "duas" },
        h("div", {}, h("h3", {}, icone("clock"), "Próximas"),
          d.proximas.length ? d.proximas.map((p) => h("div", { class: "linha-c" }, icone(p.tipo === "tarefa" ? "list-checks" : "workflow"),
            h("span", { texto: p.nome }), h("span", { class: "quando", texto: quando(p.proxima) })))
            : h("div", { class: "c-vazio", texto: "Nada agendado." })),
        h("div", {}, h("h3", {}, icone("circle-alert"), "Falhas e alertas"),
          [...d.com_erro.map((c) => h("div", { class: "linha-c erro" }, icone("plug"), h("span", { texto: `${c.nome}: ${c.detalhe}` }))),
           ...d.alertas.map((a) => h("div", { class: "linha-c " + a.nivel }, icone("circle-alert"), h("span", { texto: a.resumo }),
             h("span", { class: "quando", texto: quando(a.quando) })))].slice(0, 10).concat(
            d.com_erro.length || d.alertas.length ? [] : [h("div", { class: "c-vazio" }, icone("shield-check"), " Tudo certo.")]))),
      h("h3", {}, icone("gauge"), "Consumo de APIs (7 dias)"),
      d.consumo.length ? d.consumo.map((c) => {
        const barra = h("i"); barra.style.width = (100 * c.chamadas / maxConsumo).toFixed(1) + "%";
        return h("div", { class: "barra" }, h("span", { texto: c.conector }), h("span", {}, barra),
          h("em", { texto: `${c.chamadas}${c.erros ? ` · ${c.erros} erro${c.erros > 1 ? "s" : ""}` : ""}` }));
      }) : h("div", { class: "c-vazio", texto: "Nenhuma chamada ainda." }),
      h("h3", {}, icone("history"), "Linha do tempo"),
      d.linha_do_tempo.length ? h("ul", { class: "tempo-l" }, d.linha_do_tempo.map((e) => h("li", { class: e.nivel },
        h("time", { texto: quando(e.quando) }), h("span", { class: "tag", texto: e.conector || e.tipo }), e.resumo)))
        : h("div", { class: "c-vazio", texto: "Ainda não aconteceu nada por aqui." }));
  }

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

  /* ---------------- TAREFAS */
  function renderTarefas(d) {
    C.dados.tarefas = d;
    contador("tarefas", d.resumo.tarefas_pendentes);
    const titulo = h("input", { class: "c-campo", placeholder: "Nova tarefa… (ou diga: “Jarvis, anota…”)", maxlength: "200", "aria-label": "Título da tarefa" });
    const prio = h("select", { class: "c-campo", "aria-label": "Prioridade" }, PRIORIDADES.map((p) => h("option", { value: p, selected: p === "normal" }, p)));
    const data = h("input", { class: "c-campo", type: "datetime-local", "aria-label": "Quando lembrar" });
    const form = h("form", { class: "nova-t", onsubmit: async (e) => {
      e.preventDefault();
      if (!titulo.value.trim()) return titulo.focus();
      try { await pedir("tarefa_salvar", { tarefa: { titulo: titulo.value, prioridade: prio.value, quando: data.value } }); carregar("tarefas", true); }
      catch (e2) { erro(e2); }
    } }, titulo, prio, data, h("button", { type: "submit", class: "btn mini" }, icone("plus"), "Adicionar"));
    const pil = (rotulo, st) => h("button", { type: "button", class: "pilula", "aria-pressed": String(C.statusTarefa === st),
      onclick: () => { C.statusTarefa = st; carregar("tarefas", true); } }, rotulo);
    secao(form, h("div", { class: "filtros" }, pil("Pendentes", "pendente"), pil("Concluídas", "concluida"), pil("Todas", "todas"),
      h("span", { class: "c-dica", texto: `${d.resumo.tarefas_pendentes} pendentes · ${d.resumo.tarefas_concluidas} concluídas` })),
      d.itens.length ? d.itens.map(itemTarefa) : h("div", { class: "c-vazio", texto: "Nenhuma tarefa aqui." }));
  }
  function itemTarefa(t) {
    const check = h("input", { type: "checkbox", checked: t.status === "concluida", "aria-label": "Concluir " + t.titulo,
      onchange: async () => { try { await pedir("tarefa_status", { id: t.id, status: check.checked ? "concluida" : "pendente" }); carregar("tarefas", true); } catch (e) { erro(e); } } });
    return h("div", { class: `tarefa ${t.prioridade} ${t.status}` }, check,
      h("div", { class: "t-corpo" }, h("div", { class: "t-tit", texto: t.titulo }),
        h("div", { class: "t-meta" }, h("span", { texto: t.prioridade }), t.quando ? h("span", {}, icone("bell"), " ", quando(t.quando)) : null,
          t.origem === "automacao" ? h("span", {}, icone("workflow"), " automação") : null,
          t.concluida ? h("span", { texto: "concluída " + quando(t.concluida) }) : null),
        t.descricao ? h("div", { class: "t-desc", texto: t.descricao }) : null),
      h("button", { type: "button", class: "icone-btn", title: "Apagar", "aria-label": "Apagar " + t.titulo,
        onclick: async () => { if (!confirm(`Apagar a tarefa "${t.titulo}"?`)) return; try { await pedir("tarefa_apagar", { id: t.id }); carregar("tarefas", true); } catch (e) { erro(e); } } },
        icone("trash-2")));
  }

  /* ---------------- AUTOMAÇÕES */
  function renderAutomacoes(d) {
    C.dados.automacoes = d; C.meta = d;
    secao(
      h("div", { class: "filtros" }, botao("Nova automação", "plus", () => editor(null)),
        h("span", { class: "c-dica", texto: "Gatilho → Condições → Ações → Resultado. Também dá para pedir por voz: “todo dia às 8h resuma meus e-mails”." })),
      h("h3", {}, icone("sparkles"), "Modelos prontos"),
      h("div", { class: "modelos" }, d.modelos.map((m) => h("button", { type: "button", class: "modelo",
        onclick: () => editor({ nome: m.nome, descricao: m.descricao, fluxo: JSON.parse(JSON.stringify(m.fluxo)) }) },
        icone(m.icone), h("span", {}, h("b", { texto: m.nome }), h("small", { texto: m.descricao }))))),
      h("h3", {}, icone("workflow"), `Suas automações (${d.itens.length})`),
      d.itens.length ? d.itens.map(itemAutomacao) : h("div", { class: "c-vazio", texto: "Nenhuma ainda: comece por um modelo." }));
  }
  function itemAutomacao(a) {
    const chave = h("button", { type: "button", class: "chave", role: "switch", "aria-checked": String(a.ativa), "aria-label": (a.ativa ? "Pausar " : "Ligar ") + a.nome,
      onclick: async () => { try { await pedir("automacao_ativar", { id: a.id, ativa: !a.ativa }); carregar("automacoes", true); } catch (e) { erro(e); } } });
    const ultima = a.ultima ? h("span", { class: a.ultimo_status === "ok" ? "ok-txt" : a.ultimo_status === "ignorada" ? "" : "erro-txt",
      texto: `última: ${quando(a.ultima)} (${a.ultimo_status})` }) : h("span", { texto: "nunca rodou" });
    return h("div", { class: "auto" + (a.ativa ? "" : " pausada") }, chave,
      h("div", {}, h("h4", { texto: a.nome }),
        h("div", { class: "a-meta" }, h("span", {}, icone("timer"), " ", a.gatilho_texto), a.proxima ? h("span", { texto: "próxima: " + quando(a.proxima) }) : null,
          ultima, h("span", { texto: `${a.execucoes} execuções · ${a.falhas} falhas` })),
        a.precisa_aprovar ? h("div", { class: "a-meta alerta-txt" }, icone("shield-check"), " envio aguardando sua aprovação") : null),
      h("div", { class: "a-acoes" },
        botao("Editar", "square-pen", () => editor(a)),
        botao("Testar", "flask-conical", () => testarAutomacao(a)),
        botao("Rodar", "play", async () => { try { const r = await pedir("automacao_rodar", { id: a.id }); aviso(`${a.nome}: ${r.status || "ok"}`); carregar("automacoes", true); } catch (e) { erro(e); } }),
        botao("Histórico", "history", () => historicoAutomacao(a)),
        h("button", { type: "button", class: "icone-btn", title: "Apagar", "aria-label": "Apagar " + a.nome, onclick: async () => {
          if (!confirm(`Apagar a automação "${a.nome}"?`)) return;
          try { await pedir("automacao_apagar", { id: a.id }); carregar("automacoes", true); } catch (e) { erro(e); } } }, icone("trash-2"))));
  }
  function passos(lista) {
    return lista.map((p) => h("div", { class: "passo " + (p.status === "ok" ? "ok" : p.status === "erro" ? "erro" : "") },
      icone(p.status === "ok" ? "circle-check" : p.status === "erro" ? "circle-x" : "circle-alert"),
      h("div", {}, h("b", { texto: `${p.passo}: ${p.status}` }), p.saida ? h("pre", { texto: p.saida }) : null)));
  }
  async function testarAutomacao(a, alvo) {
    const caixa = alvo || abrirModal(h("h3", {}, icone("flask-conical"), "Teste: " + a.nome),
      h("p", { class: "sub-m", texto: "Lê de verdade (e-mails, agenda, IA), mas só DESCREVE o que enviaria ou criaria." }), h("div", { id: "c-teste" }));
    const destino = alvo || $c("c-teste");
    destino.replaceChildren(h("div", { class: "c-vazio" }, icone("loader-circle", "gira"), " Testando…"));
    try {
      const r = await pedir("automacao_testar", { id: a.id });
      destino.replaceChildren(h("div", { class: "resposta " + (r.status === "ok" ? "ok" : r.status === "ignorada" ? "" : "erro"),
        texto: "Resultado: " + r.status }), ...passos(r.passos || []));
      if (!alvo) caixa.append(h("div", { class: "acoes-m" }, botao("Fechar", "x", fecharModal)));
    } catch (e) { destino.replaceChildren(h("div", { class: "resposta erro", texto: e.message })); }
  }
  async function historicoAutomacao(a) {
    abrirModal(h("h3", {}, icone("history"), "Histórico: " + a.nome), h("div", { id: "c-hist" }, h("div", { class: "c-vazio" }, icone("loader-circle", "gira"))));
    try {
      const r = await pedir("automacao_execucoes", { id: a.id });
      $c("c-hist").replaceChildren(...(r.itens.length ? r.itens.map((e) => h("details", { class: "resposta " + (e.status === "ok" ? "ok" : e.status === "ignorada" ? "" : "erro") },
        h("summary", { texto: `${quando(e.inicio)} · ${e.teste ? "teste · " : ""}${e.status}${e.resultado ? " · " + e.resultado.slice(0, 120) : ""}` }), ...passos(e.passos)))
        : [h("div", { class: "c-vazio", texto: "Ainda não rodou." })]),
      h("div", { class: "acoes-m" }, botao("Fechar", "x", fecharModal)));
    } catch (e) { $c("c-hist").replaceChildren(h("div", { class: "resposta erro", texto: e.message })); }
  }

  /* ---------------- editor visual: Gatilho → Condições → Ações → Resultado */
  function editor(auto) {
    const meta = C.meta;
    const est = { id: auto && auto.id, nome: (auto && auto.nome) || "Nova automação", descricao: (auto && auto.descricao) || "",
      fluxo: JSON.parse(JSON.stringify((auto && auto.fluxo) || { gatilho: { tipo: "horario", repetir: "diario", hora: "08:00" }, condicoes: [], acoes: [{ tipo: "avisar", params: { texto: "" } }] })) };
    est.fluxo.condicoes = est.fluxo.condicoes || [];
    est.fluxo.modo_condicoes = est.fluxo.modo_condicoes || "todas";
    const area = h("div", { class: "fluxo" });
    const resultado = h("div", {}, h("div", { class: "c-dica", texto: "Salve e teste antes de ligar: o teste não envia nem cria nada." }));
    let ultimoFoco = null;

    const campo = (rotulo, el, largo) => h("label", { class: largo ? "largo" : "" }, rotulo, el);
    const entrada = (valor, aoMudar, attrs) => {
      const el = h("input", { class: "c-campo", valor: valor == null ? "" : String(valor), ...(attrs || {}),
        oninput: (e) => aoMudar(e.target.value), onfocus: (e) => { ultimoFoco = e.target; } });
      return el;
    };
    const area_t = (valor, aoMudar) => h("textarea", { class: "c-campo", rows: "2", valor: valor == null ? "" : String(valor),
      oninput: (e) => aoMudar(e.target.value), onfocus: (e) => { ultimoFoco = e.target; } });
    const escolha = (valor, opcoes, aoMudar) => h("select", { class: "c-campo", onchange: (e) => aoMudar(e.target.value) },
      opcoes.map(([v, r]) => h("option", { value: v, selected: String(v) === String(valor) }, r)));
    const variaveis = () => {
      const g = est.fluxo.gatilho;
      const vs = ["data", "hora", "amanha"];
      if (g.evento === "whatsapp.mensagem") vs.push("mensagem.texto", "mensagem.nome", "mensagem.de");
      if (g.evento === "gmail.novo") vs.push("email.de", "email.assunto", "email.trecho");
      if (g.evento === "agenda.proxima") vs.push("evento.titulo", "evento.hora", "evento.minutos", "evento.local");
      for (const a of est.fluxo.acoes) if (a.salvar_como) vs.push("resultado." + a.salvar_como);
      return h("div", { class: "vars" }, h("span", { class: "c-dica", texto: "Variáveis (clique para inserir):" }),
        vs.map((v) => h("code", { onmousedown: (e) => e.preventDefault(), onclick: () => inserir("{" + v + "}") }, "{" + v + "}")));
    };
    function inserir(txt) {
      if (!ultimoFoco) return;
      const el = ultimoFoco, ini = el.selectionStart ?? el.value.length, fim = el.selectionEnd ?? el.value.length;
      el.value = el.value.slice(0, ini) + txt + el.value.slice(fim);
      el.dispatchEvent(new Event("input")); el.focus(); el.selectionStart = el.selectionEnd = ini + txt.length;
    }

    function noGatilho() {
      const g = est.fluxo.gatilho;
      const campos = [campo("Tipo", escolha(g.tipo, [["horario", "Horário"], ["evento", "Evento"], ["manual", "Manual"]], (v) => {
        est.fluxo.gatilho = v === "horario" ? { tipo: "horario", repetir: "diario", hora: "08:00" } : v === "evento" ? { tipo: "evento", evento: "whatsapp.mensagem" } : { tipo: "manual" };
        desenhar();
      }))];
      if (g.tipo === "horario") {
        campos.push(campo("Repetir", escolha(g.repetir, [["nunca", "Uma vez"], ["diario", "Todo dia"], ["semanal", "Dias da semana"], ["mensal", "Todo mês"], ["intervalo", "A cada N minutos"]],
          (v) => { g.repetir = v; desenhar(); })));
        if (g.repetir === "nunca") campos.push(campo("Quando", entrada((g.quando || "").slice(0, 16), (v) => { g.quando = v; }, { type: "datetime-local" })));
        else if (g.repetir === "intervalo") campos.push(campo("A cada (min)", entrada(g.a_cada_min || 60, (v) => { g.a_cada_min = parseInt(v, 10) || 60; }, { type: "number", min: "5" })));
        else campos.push(campo("Hora", entrada(g.hora || "08:00", (v) => { g.hora = v; }, { type: "time" })));
        if (g.repetir === "mensal") campos.push(campo("Dia do mês", entrada(g.dia || 1, (v) => { g.dia = parseInt(v, 10) || 1; }, { type: "number", min: "1", max: "28" })));
        if (g.repetir === "semanal") {
          g.dias = g.dias || [0];
          campos.push(h("label", { class: "largo" }, "Dias", h("div", { class: "filtros" }, DIAS.map((d, i) => h("button", { type: "button", class: "pilula",
            "aria-pressed": String(g.dias.includes(i)), onclick: () => { g.dias = g.dias.includes(i) ? g.dias.filter((x) => x !== i) : [...g.dias, i].sort(); desenhar(); } }, d)))));
        }
      } else if (g.tipo === "evento") {
        campos.push(campo("Evento", escolha(g.evento, meta.eventos.map((e) => [e, ROTULO_EVENTO[e] || e]), (v) => { g.evento = v; desenhar(); })));
        if (g.evento === "agenda.proxima") campos.push(campo("Minutos antes", entrada(g.minutos_antes || 15, (v) => { g.minutos_antes = parseInt(v, 10) || 15; }, { type: "number", min: "1" })));
      }
      return h("div", { class: "no gatilho" }, h("div", { class: "no-tit" }, icone("zap"), "Gatilho"), h("div", { class: "campos-no" }, campos));
    }
    function noCondicoes() {
      const conds = est.fluxo.condicoes;
      const linhas = conds.map((c, i) => h("div", { class: "cond" },
        entrada(c.campo, (v) => { c.campo = v; }, { placeholder: "campo (ex.: mensagem.texto)", "aria-label": "Campo" }),
        escolha(c.op, meta.operadores.map((o) => [o, ROTULO_OP[o] || o]), (v) => { c.op = v; }),
        entrada(c.valor, (v) => { c.valor = v; }, { placeholder: c.op === "ia" ? "pergunta de sim/não" : "valor (vírgula = qualquer um)", "aria-label": "Valor" }),
        h("button", { type: "button", class: "icone-btn", "aria-label": "Remover condição", onclick: () => { conds.splice(i, 1); desenhar(); } }, icone("x"))));
      return h("div", { class: "no" }, h("div", { class: "no-tit" }, icone("filter"), "Condições",
        h("span", { class: "ctrl" }, conds.length > 1 ? escolha(est.fluxo.modo_condicoes, [["todas", "todas valem"], ["qualquer", "qualquer uma"]], (v) => { est.fluxo.modo_condicoes = v; }) : null)),
        linhas.length ? linhas : h("div", { class: "c-dica", texto: "Sem condições: roda sempre que o gatilho disparar." }),
        botao("Condição", "plus", () => { conds.push({ campo: est.fluxo.gatilho.evento === "gmail.novo" ? "email.assunto" : "mensagem.texto", op: "contem", valor: "" }); desenhar(); }));
    }
    function noAcao(a, i) {
      const tipo = meta.tipos_acao[a.tipo];
      a.params = a.params || {};
      const campos = tipo.campos.map((c) => {
        const valor = a.params[c];
        let el;
        if (c === "texto" || c === "pedido" || c === "contexto" || c === "descricao") el = area_t(valor, (v) => { a.params[c] = v; });
        else if (c === "prioridade") el = escolha(valor || "normal", PRIORIDADES.map((p) => [p, p]), (v) => { a.params[c] = v; });
        else if (c === "json") el = escolha(String(!!valor), [["false", "não"], ["true", "sim"]], (v) => { a.params[c] = v === "true"; });
        else el = entrada(valor, (v) => { a.params[c] = (c === "quantos" || c === "dias" || c === "duracao_min") ? (parseInt(v, 10) || "") : v; },
          c === "para" ? { placeholder: "eu · nome do contato · número/e-mail · {mensagem.de}" } : {});
        return campo(ROTULO_CAMPO[c] || c, el, ["texto", "pedido", "contexto", "descricao"].includes(c));
      });
      const mover = (d) => { const j = i + d; if (j < 0 || j >= est.fluxo.acoes.length) return; [est.fluxo.acoes[i], est.fluxo.acoes[j]] = [est.fluxo.acoes[j], est.fluxo.acoes[i]]; desenhar(); };
      return h("div", { class: "no" },
        h("div", { class: "no-tit" }, icone(tipo.envia ? "message-circle" : a.tipo === "ia" ? "sparkles" : "play"), `Ação ${i + 1}`,
          h("span", { class: "ctrl" },
            h("button", { type: "button", class: "icone-btn", title: "Subir", "aria-label": "Subir ação", onclick: () => mover(-1) }, icone("arrow-down", "vira")),
            h("button", { type: "button", class: "icone-btn", title: "Remover", "aria-label": "Remover ação", onclick: () => { est.fluxo.acoes.splice(i, 1); desenhar(); } }, icone("trash-2")))),
        h("div", { class: "campos-no" }, campo("Tipo", escolha(a.tipo, Object.entries(meta.tipos_acao).map(([k, t]) => [k, t.nome]), (v) => { a.tipo = v; a.params = {}; desenhar(); }), true), campos),
        tipo.envia ? h("p", { class: "c-dica", texto: "Para você (“eu”): qualquer texto, inclusive {resultado.x}. Para outra pessoa: só texto fixo (seguras: {data}, {hora}, {evento.titulo})." }) : null,
        h("div", { class: "opcoes-no" },
          h("label", {}, "salvar como", entrada(a.salvar_como || "", (v) => { a.salvar_como = v.replace(/\W/g, ""); }, { placeholder: "ex.: resumo", class: "c-campo" })),
          i > 0 ? h("label", {}, h("input", { type: "checkbox", checked: !!a.paralelo, onchange: (e) => { a.paralelo = e.target.checked; desenhar(); } }), "junto com a anterior (paralelo)") : null,
          h("label", {}, "tentativas", entrada(a.tentativas || 1, (v) => { a.tentativas = parseInt(v, 10) || 1; }, { type: "number", min: "1", max: "5" })),
          h("label", {}, "se falhar", escolha(a.se_falhar || "parar", [["parar", "parar"], ["continuar", "continuar"]], (v) => { a.se_falhar = v; }))));
    }
    const liga = () => h("div", { class: "liga" }, icone("arrow-down"));
    function desenhar() {
      const grupos = [];
      est.fluxo.acoes.forEach((a, i) => { if (a.paralelo && grupos.length) grupos[grupos.length - 1].push([a, i]); else grupos.push([[a, i]]); });
      area.replaceChildren(noGatilho(), liga(), noCondicoes(),
        ...grupos.flatMap((g) => [liga(), h("div", { class: "grupo" + (g.length > 1 ? " paralelo" : "") }, g.map(([a, i]) => noAcao(a, i)))]),
        botao("Adicionar ação", "plus", () => { est.fluxo.acoes.push({ tipo: "avisar", params: { texto: "" } }); desenhar(); }, "add-acao"),
        liga(), h("div", { class: "no resultado" }, h("div", { class: "no-tit" }, icone("circle-check"), "Resultado"), resultado), variaveis());
    }
    async function salvar(semAprovacao) {
      try {
        const r = await pedir("automacao_salvar", { id: est.id, nome: est.nome, descricao: est.descricao, fluxo: est.fluxo });
        est.id = r.id; est.fluxo = r.fluxo;
        aviso(`Automação "${r.nome}" salva.`);
        carregar("automacoes", true);
        if (semAprovacao) return r;
        if (r.precisa_aprovar) pedirAprovacao(r);
        else resultado.replaceChildren(h("div", { class: "resposta ok", texto: `Salva · ${r.gatilho_texto}${r.proxima ? " · próxima: " + quando(r.proxima) : ""}` }));
        return r;
      } catch (e) { resultado.replaceChildren(h("div", { class: "resposta erro", texto: e.message })); return null; }
    }
    function pedirAprovacao(r, junto) {
      const caixa = h("div", { class: "aprovar" }, h("b", {}, icone("shield-check"), "Aprovar envios"),
        h("div", { texto: "Esta automação vai " + r.resumo_aprovacao.replace(/^criar a automação /, "rodar ") + "." }),
        h("p", { class: "c-dica", texto: "Aprovada, ela envia sozinha só para quem está aqui. Se você mudar o fluxo, precisa aprovar de novo." }),
        h("div", { class: "acoes-m" }, botao("Aprovar e ligar", "check", async () => {
          try { await pedir("automacao_aprovar", { id: r.id, hash: r.hash }); await pedir("automacao_ativar", { id: r.id, ativa: true });
            resultado.replaceChildren(h("div", { class: "resposta ok", texto: "Aprovada e ligada." })); carregar("automacoes", true); }
          catch (e) { resultado.replaceChildren(h("div", { class: "resposta erro", texto: e.message })); }
        }, "perigo")));
      if (junto) resultado.append(caixa); else resultado.replaceChildren(caixa);
    }
    const nome = h("input", { class: "c-campo", valor: est.nome, maxlength: "120", "aria-label": "Nome da automação", oninput: (e) => { est.nome = e.target.value; } });
    const desc = h("input", { class: "c-campo", valor: est.descricao, maxlength: "500", placeholder: "Descrição (opcional)", "aria-label": "Descrição", oninput: (e) => { est.descricao = e.target.value; } });
    desenhar();
    abrirModal(h("h3", {}, icone("workflow"), est.id ? "Editar automação" : "Nova automação"),
      h("div", { class: "form-l" }, nome, desc), area,
      h("div", { class: "acoes-m" },
        botao("Testar", "flask-conical", async () => {
          const r = await salvar(true);
          if (!r) return;
          await testarAutomacao(r, resultado);
          if (r.precisa_aprovar) pedirAprovacao(r, true);       // o teste não tira o pedido de aprovação da tela
        }),
        botao("Salvar", "save", () => salvar()),
        botao("Fechar", "x", fecharModal))).classList.add("larga");
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

  montar();
  return { abrir, fechar, receber, mudou, carregar };
})();
