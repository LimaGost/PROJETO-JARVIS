/* AI Command Center: três abas na Central (Agentes IA · Orquestração · Terminal).
   Tudo pelo backend (ops "orq_*" no WebSocket da HUD); nada é executado no navegador. O que vem do servidor pode
   conter texto de agentes e de projetos: nada aqui usa innerHTML (só textContent e elementos). */
"use strict";

const Orquestra = (() => {
  const A = Central.api;
  const h = A.h;
  const O = { exe: null, filtro: "", ultimoLog: 0, sessao: null, timers: {}, estado: null, pastaTerm: "" };
  const ESTADO_AG = { disponivel: "Disponível", executando: "Executando", aguardando: "Aguardando", desconectado: "Desconectado", erro: "Erro" };
  const STATUS_EXE = { planejada: "Planejada", aguardando_aprovacao: "Aguardando aprovação", executando: "Executando", concluida: "Concluída",
                       falhou: "Falhou", cancelada: "Cancelada", desfeita: "Desfeita", interrompida: "Interrompida" };
  const STATUS_PASSO = { pendente: "Pendente", executando: "Executando", concluido: "Concluído", falhou: "Falhou", cancelado: "Cancelado",
                         pulado: "Pulado", bloqueado: "Bloqueado" };
  const CAT = { analise: "Análise", implementacao: "Implementação", depuracao: "Depuração", refatoracao: "Refatoração", testes: "Testes",
                documentacao: "Documentação", planejamento: "Planejamento", consulta: "Consulta", revisao: "Revisão", julgamento: "Julgamento" };
  const MODOS = [["automatico", "Automático", "zap"], ["supervisionado", "Supervisionado", "eye"], ["manual", "Manual", "split"]];
  const CUSTO = { assinatura: "assinatura", por_token: "por token", gratis: "grátis", variavel: "variável" };
  const usd = (v) => { const n = Number(v || 0); return "US$ " + n.toFixed(n === 0 || n >= 1 ? 2 : 4); };
  const ORDEM_AG = { executando: 0, disponivel: 1, aguardando: 2, erro: 3, desconectado: 4 };
  const dur = (s) => (s >= 90 ? `${(s / 60).toFixed(1)} min` : `${Math.round(s)} s`);
  const ic = (n, c) => icone(n, c);
  const agendar = (nome, fn, ms) => { clearTimeout(O.timers[nome]); O.timers[nome] = setTimeout(fn, ms); };
  const nomeAg = (d, id) => (d && d.nomes && d.nomes[id]) || id || "roteador";

  /* ---------------- markdown simples, sem innerHTML */
  function inline(t) {
    const partes = [], re = /(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_)/g;
    let i = 0, m;
    while ((m = re.exec(t))) {
      if (m.index > i) partes.push(t.slice(i, m.index));
      const s = m[0];
      partes.push(s.startsWith("**") ? h("b", {}, s.slice(2, -2)) : s.startsWith("`") ? h("code", {}, s.slice(1, -1)) : h("i", {}, s.slice(1, -1)));
      i = m.index + s.length;
    }
    if (i < t.length) partes.push(t.slice(i));
    return partes;
  }
  function markdown(texto) {
    const raiz = h("div", { class: "md" });
    let tabela = null, codigo = null;
    for (const linha of String(texto || "").split("\n")) {
      if (linha.trim().startsWith("```")) { if (codigo) { raiz.append(codigo); codigo = null; } else codigo = h("pre", {}); continue; }
      if (codigo) { codigo.append(linha + "\n"); continue; }
      const t = linha.trim();
      if (/^\|.*\|$/.test(t)) {
        if (/^\|[-| :]+\|$/.test(t)) continue;
        const cels = t.slice(1, -1).split("|").map((c) => c.trim());
        if (!tabela) { tabela = h("table", {}); raiz.append(h("div", { class: "tab-rol" }, tabela)); tabela.append(h("tr", {}, cels.map((c) => h("th", {}, inline(c))))); }
        else tabela.append(h("tr", {}, cels.map((c) => h("td", {}, inline(c)))));
        continue;
      }
      tabela = null;
      if (/^#{1,4}\s/.test(t)) raiz.append(h("h5", {}, inline(t.replace(/^#+\s*/, ""))));
      else if (/^[-*]\s/.test(t)) raiz.append(h("div", { class: "li" }, "• ", inline(t.slice(2))));
      else if (!t) raiz.append(h("div", { class: "esp" }));
      else raiz.append(h("p", {}, inline(linha)));
    }
    if (codigo) raiz.append(codigo);
    return raiz;
  }

  /* ---------------- tempo correndo nos cartões */
  setInterval(() => {
    for (const el of document.querySelectorAll(".tempo-vivo")) {
      if (el.dataset.pausado === "1") continue;
      el.textContent = dur(Number(el.dataset.base) + (Date.now() - Number(el.dataset.desde)) / 1000);
    }
  }, 1000);
  const tempoVivo = (s, pausado) => h("span", { class: "tempo-vivo", "data-base": String(s), "data-desde": String(Date.now()),
    "data-pausado": pausado ? "1" : "0" }, dur(s));

  /* ================================================================ ABA: AGENTES IA */
  async function abaAgentes() {
    const e = await A.pedir("orq_estado");
    O.estado = e;
    A.contador("agentes", e.agentes.filter((a) => a.estado === "executando").length);
    const c = e.custos, lim = c.limites;
    const visiveis = e.agentes.filter((a) => a.ativo !== false);
    A.secao(
      h("div", { class: "filtros" },
        h("span", { class: "c-dica", texto: "Modo:" }),
        MODOS.map(([id, rot, icn]) => h("button", { type: "button", class: "pilula", "aria-pressed": String(e.modo === id),
          title: id === "automatico" ? "Planeja e executa sozinho (nos projetos autorizados), respeitando os limites" :
            id === "supervisionado" ? "Mostra o plano e espera você aprovar" : "Você escolhe os agentes de cada passo",
          onclick: () => trocarModo(id) }, ic(icn), " " + rot)),
        h("span", { class: "gasto", title: "Estimado hoje (inclui a assinatura) · cobrado nas chaves de API / limite do dia",
          texto: `Hoje: ${usd(c.hoje.custo)} · cobrado ${usd(c.hoje_no_limite)} de ${usd(lim.dia)}` }),
        A.botao("Limites", "coins", () => modalLimites(c))),
      e.ativas.length ? [h("h3", {}, ic("workflow"), "Em andamento"), h("div", { class: "lista-exe" }, e.ativas.map(linhaExecucao))] : null,
      h("div", { class: "grade" }, [...visiveis].sort((x, y) => (ORDEM_AG[x.estado] ?? 9) - (ORDEM_AG[y.estado] ?? 9)).map(cartaoAgente)));
  }
  async function trocarModo(id) {
    if (id === "automatico" && !confirm("No modo automático os agentes executam sem esperar aprovação nos projetos que você já " +
      "autorizou (com checkpoint para desfazer e limites de custo). Continuar?")) return;
    try { await A.pedir("orq_modo", { modo: id }); A.recarregar(); } catch (e) { A.erro(e); }
  }
  /* uma frase de estado, em português de gente */
  function situacao(a) {
    if (a.trabalhos.length) return ["executando", "Trabalhando"];
    if (a.entrando) return ["aguardando", "Esperando você entrar…"];
    if (a.estado === "disponivel") return ["conectado", "Pronto"];
    if (!a.instalado) return ["desconectado", "Não instalado"];
    if (a.estado === "desconectado") return ["aguardando", a.pode_login ? "Precisa entrar" : (a.detalhe || "Desconectado")];
    return ["erro", a.detalhe || "Com problema"];
  }
  async function entrar(a) {
    try { aviso((await A.pedir("orq_cli_login", { id: a.id })).mensagem); A.recarregar(); } catch (e) { A.erro(e); }
  }
  function cartaoAgente(a) {
    const [classe, frase] = situacao(a);
    const acoes = [];
    if (!a.trabalhos.length) {
      if (a.instalado && a.estado !== "disponivel" && a.pode_login && !a.entrando) acoes.push(A.botao("Entrar", "log-in", () => entrar(a)));
      else if (!a.instalado && a.instalar_cmd) acoes.push(A.botao("Instalar", "download", () => instalar(a)));
      if (["openrouter", "openai"].includes(a.id) || (a.id === "gemini" && a.estado !== "disponivel"))
        acoes.push(A.botao(a.estado === "disponivel" && a.id !== "gemini" ? "Trocar chave" : "Chave", "key-round", () => modalChave(a)));
    }
    acoes.push(A.botao("Detalhes", "chevron-right", () => detalhesAgente(a)));
    const trabalhos = a.trabalhos.map((t) => h("div", { class: "trab" },
      h("div", {}, h("b", { texto: t.titulo }), " ", tempoVivo(t.tempo_s, t.pausado)),
      h("div", { class: "rodape-i" },
        t.pausado ? A.botao("Retomar", "play", () => controlar("orq_retomar", t.execucao, t.passo))
          : (a.suporta_pausar ? A.botao("Pausar", "pause", () => controlar("orq_pausar", t.execucao, t.passo)) : null),
        A.botao("Cancelar", "x", () => controlar("orq_cancelar_passo", t.execucao, t.passo), "perigo"),
        A.botao("Ver", "eye", () => abrirExecucao(t.execucao)))));
    return h("div", { class: "integ agente " + a.estado },
      h("div", { class: "topo-i" }, h("span", { class: "logo-i" }, ic(a.icone, "g")),
        h("div", {}, h("h4", { texto: a.nome }), h("span", { class: "auth", texto: a.provedor }),
          a.simulado ? h("span", { class: "selo simulado", title: "CLI simulada da demonstração: não é a IA de verdade", texto: "SIMULADO" }) : null)),
      h("span", { class: "estado " + classe, title: a.detalhe || "", texto: frase }),
      trabalhos.length ? h("div", { class: "trabalhos" }, trabalhos) : null,
      h("div", { class: "rodape-i" }, acoes));
  }
  const estadoCss = (e) => ({ disponivel: "conectado", executando: "aguardando", aguardando: "aguardando", desconectado: "desconectado", erro: "erro" }[e] || "");
  async function controlar(op, execucao, passo) {
    try { await A.pedir(op, { id: execucao, passo }); agendar("rec", () => A.recarregar(), 300); } catch (e) { A.erro(e); }
  }
  function detalhesAgente(a) {
    const barras = Object.entries(a.capacidades).sort((x, y) => y[1] - x[1]).map(([c, v]) => {
      const b = h("i"); b.style.width = (v * 100).toFixed(0) + "%";
      return h("div", { class: "barra" }, h("span", { texto: CAT[c] || c }), h("span", {}, b), h("em", { texto: v.toFixed(2) }));
    });
    A.abrirModal(h("h3", {}, ic(a.icone), a.nome), h("p", { class: "sub-m", texto: a.descricao }),
      h("dl", { class: "ficha" },
        h("dt", { texto: "Estado" }), h("dd", { texto: `${ESTADO_AG[a.estado] || a.estado} · ${a.detalhe || ""}` }),
        h("dt", { texto: "Hoje" }), h("dd", { texto: `${usd((a.hoje || {}).custo || 0)} · ${((a.hoje || {}).entrada || 0) + ((a.hoje || {}).saida || 0)} tokens · acertou ${a.historico.sucessos} de ${a.historico.execucoes}` }),
        h("dt", { texto: "Versão" }), h("dd", { texto: a.versao ? `${a.versao}${a.compativel ? "" : " (precisa de " + a.versao_minima + "+)"}` : "-" }),
        h("dt", { texto: "Custo" }), h("dd", { texto: `${CUSTO[a.custo_modo] || a.custo_modo}${a.metodo ? " · login: " + a.metodo : ""}` }),
        h("dt", { texto: "Pode" }), h("dd", { texto: [a.pode_ler_projeto ? "ler o projeto" : "só texto", a.pode_editar ? "editar arquivos" : null,
          a.suporta_retomar ? "retomar sessão" : null, a.suporta_pausar ? "pausar" : null].filter(Boolean).join(" · ") }),
        h("dt", { texto: "Latência" }), h("dd", { texto: a.latencia }),
        a.modelos.length ? [h("dt", { texto: "Modelos" }), h("dd", { texto: a.modelos.join(", ") })] : null,
        a.instalar ? [h("dt", { texto: "Instalar" }), h("dd", {}, h("code", { texto: a.instalar }))] : null,
        a.login ? [h("dt", { texto: "Login" }), h("dd", { texto: a.login })] : null,
        a.docs ? [h("dt", { texto: "Documentação" }), h("dd", {}, h("a", { href: a.docs, target: "_blank", rel: "noopener noreferrer" }, a.docs))] : null),
      h("h3", { class: "no-tit" }, ic("activity"), "Capacidades declaradas"), h("div", {}, barras),
      h("p", { class: "c-dica", texto: "O roteador soma: capacidade declarada + semelhança com o pedido + histórico real + custo + latência." }),
      h("div", { class: "acoes-m" },
        A.botao("Verificar de novo", "refresh-cw", async () => { try { await A.pedir("orq_agente_verificar", { id: a.id }); A.fecharModal(); A.recarregar(); } catch (e) { A.erro(e); } }),
        A.botao("Fechar", "x", A.fecharModal)));
  }
  function modalChave(a) {
    const campo = h("input", { class: "c-campo", type: "password", autocomplete: "off", placeholder: "cole a chave aqui" });
    A.abrirModal(h("h3", {}, ic("key-round"), `Chave: ${a.nome}`),
      h("p", { class: "sub-m", texto: "Fica no cofre criptografado deste PC (DPAPI). Não vai para o config.yaml, os logs nem o contexto de outros modelos." }),
      h("div", { class: "form-l" }, campo),
      h("div", { class: "acoes-m" },
        A.botao("Remover chave", "trash-2", async () => { try { await A.pedir("orq_chave", { provedor: a.id, apikey: "" }); A.fecharModal(); A.recarregar(); } catch (e) { A.erro(e); } }, "perigo"),
        A.botao("Salvar", "save", async () => { try { const r = await A.pedir("orq_chave", { provedor: a.id, apikey: campo.value }); aviso(`Chave salva (${r.mascara}).`); A.fecharModal(); A.recarregar(); } catch (e) { A.erro(e); } })));
    campo.focus();
  }
  function modalLimites(c) {
    const lim = c.limites;
    const campo = (v) => h("input", { class: "c-campo", type: "number", min: "0", step: "0.5", valor: String(v) });
    const t = campo(lim.tarefa), d = campo(lim.dia), m = campo(lim.mes);
    const assin = h("input", { type: "checkbox", checked: lim.assinatura_conta });
    A.abrirModal(h("h3", {}, ic("coins"), "Limites de custo"),
      h("p", { class: "sub-m", texto: "Por tarefa vale para o custo estimado (inclusive na assinatura: trava um agente em loop). Dia e mês valem para o que é cobrado (chaves de API)." }),
      h("div", { class: "form-l" }, h("label", {}, "Por tarefa (US$)", t), h("label", {}, "Por dia (US$)", d), h("label", {}, "Por mês (US$)", m),
        h("label", {}, h("span", {}, assin, " contar também o uso da assinatura no limite do dia/mês"))),
      h("div", { class: "acoes-m" }, A.botao("Salvar", "save", async () => {
        try { await A.pedir("orq_custos", { limite_tarefa_usd: t.value, limite_dia_usd: d.value, limite_mes_usd: m.value, assinatura_conta_no_limite: assin.checked });
          A.fecharModal(); A.recarregar(); } catch (e) { A.erro(e); }
      })));
  }

  /* ================================================================ ABA: ORQUESTRAÇÃO */
  function linhaExecucao(x) {
    return h("button", { type: "button", class: "exe " + x.status + (O.exe === x.id ? " ativa" : ""), onclick: () => abrirExecucao(x.id) },
      h("span", { class: "st " + x.status, texto: STATUS_EXE[x.status] || x.status }),
      h("b", { texto: `#${x.id}` }), h("span", { class: "ped", texto: x.pedido }),
      h("small", { texto: [x.projeto, x.agentes.join(", "), `${x.concluidos}/${x.passos}`, usd(x.custo_usd)].filter(Boolean).join(" · ") }));
  }
  const listaExecucoes = (itens) => itens.length ? h("div", { class: "lista-exe" }, itens.map(linhaExecucao)) : h("div", { class: "c-vazio", texto: "Nenhuma ainda." });
  async function carregarLista() {
    const alvo = A.$c("orq-lista");
    if (alvo) try { alvo.replaceChildren(listaExecucoes((await A.pedir("orq_execucoes")).itens)); } catch (e) { /* a próxima atualização tenta de novo */ }
  }
  function abrirExecucao(id) { O.exe = id; O.ultimoLog = 0; O.filtro = ""; Central.abrir("orquestracao"); }

  async function abaOrquestracao() {
    const [lista, projetos] = await Promise.all([A.pedir("orq_execucoes"), A.pedir("orq_projetos")]);
    const pedido = h("textarea", { class: "c-campo", rows: "3", maxlength: "4000",
      placeholder: "Ex.: analise meu projeto, encontre problemas, corrija os erros e melhore o desempenho · peça ao Claude Code para revisar e ao Codex para implementar · faça os agentes discutirem e escolha a melhor" });
    const opcoes = [["", "sem projeto (pergunta/plano)"]];
    if (projetos.vscode) opcoes.push([projetos.vscode, `aberto no VS Code: ${projetos.vscode.split(/[\\/]/).pop()}`]);
    for (const p of projetos.autorizados) if (p.caminho !== projetos.vscode) opcoes.push([p.caminho, `${p.nome} (autorizado)`]);
    const padrao = projetos.vscode || (projetos.autorizados[0] || {}).caminho || "";
    const proj = h("select", { class: "c-campo", "aria-label": "Projeto" }, opcoes.map(([v, r]) => h("option", { value: v, selected: v === padrao }, r)));
    const modo = h("select", { class: "c-campo", "aria-label": "Modo" }, h("option", { value: "" }, "modo atual"),
      MODOS.map(([id, rot]) => h("option", { value: id }, rot)));
    const form = h("form", { class: "nova-orq", onsubmit: async (e) => {
      e.preventDefault();
      if (!pedido.value.trim()) return pedido.focus();
      const btn = form.querySelector("button[type=submit]"); btn.disabled = true;
      btn.replaceChildren(ic("loader-circle", "gira"), " Planejando…");
      try {
        const r = await A.pedir("orq_criar", { texto: pedido.value, projeto: proj.value, modo: modo.value });
        aviso(r.status === "aguardando_aprovacao" ? `Plano #${r.id} pronto: confira e aprove.` : `Orquestração #${r.id} começou.`);
        abrirExecucao(r.id);
      } catch (err) { A.erro(err); btn.disabled = false; btn.replaceChildren(ic("route"), " Planejar"); }
    } }, pedido, h("div", { class: "linha-form" }, proj, modo, h("button", { type: "submit", class: "btn mini" }, ic("route"), " Planejar")));
    const detalhe = h("div", { id: "orq-detalhe", class: "orq-detalhe" },
      O.exe ? h("div", { class: "c-vazio" }, ic("loader-circle", "gira"), " Carregando…")
        : h("div", { class: "c-vazio", texto: "Escolha uma execução ou faça um pedido. O Jarvis planeja, escolhe os agentes e mostra o fluxo aqui." }));
    A.secao(h("div", { class: "orq-layout" },
      h("div", { class: "orq-col" }, h("h3", {}, ic("sparkles"), "Novo pedido"), form,
        h("h3", {}, ic("history"), "Execuções"),
        h("div", { id: "orq-lista" }, listaExecucoes(lista.itens))),
      detalhe));
    if (O.exe) await carregarDetalhe();
  }

  async function carregarDetalhe() {
    const alvo = A.$c("orq-detalhe");
    if (!alvo || !O.exe) return;
    let d;
    try { d = await A.pedir("orq_execucao", { id: O.exe }); } catch (e) { alvo.replaceChildren(h("div", { class: "resposta erro", texto: e.message })); return; }
    O.ultimoLog = d.logs.length ? d.logs[d.logs.length - 1].id : 0;
    O.detalhe = d;
    alvo.replaceChildren(...renderDetalhe(d));
    const cons = A.$c("orq-console");
    if (cons) cons.scrollTop = cons.scrollHeight;
  }

  function renderDetalhe(d) {
    const aguardando = d.status === "aguardando_aprovacao" || d.status === "interrompida";
    const acoes = [];
    let autorizar = null;
    if (aguardando) {
      if (d.projeto && !d.autorizado && d.passos.some((p) => p.escrita))
        autorizar = h("label", { class: "chk" }, h("input", { type: "checkbox", checked: d.modo === "automatico" }), " autorizar o projeto (próximas vezes no automático não pergunta)");
      acoes.push(A.botao(d.status === "interrompida" ? "Retomar" : "Aprovar e executar", "play", async () => {
        try { await A.pedir("orq_aprovar", { id: d.id, autorizar: autorizar ? autorizar.querySelector("input").checked : false }); carregarDetalhe(); } catch (e) { A.erro(e); }
      }, "perigo"));
    }
    if (d.rodando) {
      acoes.push(A.botao("Pausar", "pause", () => controlar("orq_pausar", d.id)), A.botao("Retomar", "play", () => controlar("orq_retomar", d.id)));
    }
    if (d.rodando || aguardando) acoes.push(A.botao("Cancelar", "x", () => controlar("orq_cancelar", d.id), "perigo"));
    if (d.checkpoint && !d.rodando && d.status !== "desfeita") acoes.push(A.botao("Desfazer", "undo-2", () => desfazer(d)));
    const topo = h("div", { class: "orq-topo" },
      h("div", {}, h("h4", {}, h("span", { class: "st " + d.status, texto: STATUS_EXE[d.status] || d.status }), ` #${d.id} · `, d.pedido),
        h("small", { texto: [d.projeto_nome ? "projeto " + d.projeto_nome : "sem projeto", "modo " + d.modo,
          `estimado ${usd(d.custo_usd)} · cobrado ${usd(d.cobrado_usd)}`, `${d.tokens_entrada + d.tokens_saida} tokens`].join(" · ") })),
      h("div", { class: "rodape-i" }, acoes));
    const partes = [topo];
    if (d.motivo_espera) partes.push(h("div", { class: "aprovar" }, h("b", {}, ic("shield-check"), "Esperando você"), h("div", { texto: d.motivo_espera }), autorizar));
    else if (d.erro && d.status !== "aguardando_aprovacao") partes.push(h("div", { class: "resposta erro", texto: d.erro }));
    partes.push(h("h3", {}, ic("network"), "Fluxo"), fluxo(d, aguardando));
    partes.push(h("h3", {}, ic("square-terminal"), "Ao vivo"), consoleLogs(d));
    if (d.relatorio) partes.push(h("h3", {}, ic("list-checks"), "Relatório"), h("div", { class: "relatorio" }, markdown(d.relatorio)));
    return partes;
  }

  function fluxo(d, editavel) {
    const porChave = Object.fromEntries(d.passos.map((p) => [p.chave, p]));
    const agentes = Object.entries(d.nomes);
    const colunas = [h("div", { class: "col" }, h("div", { class: "no-f jarvis" }, ic("brain", "g"), h("b", { texto: "Jarvis" }), h("small", { texto: "entende, planeja, escolhe" })))];
    for (const nivel of d.niveis) {
      colunas.push(h("div", { class: "seta" }, ic("arrow-right")));
      colunas.push(h("div", { class: "col" }, nivel.map((k) => porChave[k]).filter(Boolean).map((p) => {
        let agente;
        if (editavel && p.categoria !== "testes") {
          agente = h("select", { class: "c-campo mini-sel", "aria-label": "Agente do passo " + p.titulo,
            onchange: async (e) => { try { await A.pedir("orq_trocar_agente", { id: d.id, passo: p.chave, agente: e.target.value }); carregarDetalhe(); } catch (err) { A.erro(err); } } },
            h("option", { value: "" }, "roteador escolhe"), agentes.filter(([id]) => id !== "testes").map(([id, nome]) => h("option", { value: id, selected: p.agente === id && p.agente_fixo }, nome)));
        } else agente = h("span", { class: "ag", texto: nomeAg(d, p.agente) });
        const tempo = p.status === "executando" && p.inicio ? tempoVivo((Date.now() - new Date(p.inicio).getTime()) / 1000) : null;
        return h("div", { class: "no-f " + p.status, title: p.motivo || p.erro || "" },
          h("div", { class: "no-cab" }, h("span", { class: "cat", texto: CAT[p.categoria] || p.categoria }), p.escrita ? h("span", { class: "esc", title: "altera arquivos", texto: "escrita" }) : null,
            h("span", { class: "st " + p.status, texto: STATUS_PASSO[p.status] || p.status })),
          h("b", { texto: p.titulo }), agente,
          h("small", { texto: [tempo ? "" : null, p.tentativas > 1 ? `${p.tentativas} tentativas` : null, p.custo_usd ? usd(p.custo_usd) : null,
            p.arquivos.length ? `${p.arquivos.length} arquivo(s)` : null, (p.dados && p.dados.resumo) || null].filter((x) => x).join(" · ") }), tempo,
          p.erro && p.status !== "concluido" ? h("small", { class: "erro-txt", texto: p.erro.slice(0, 160) }) : null,
          p.status === "executando" ? h("div", { class: "rodape-i" }, A.botao("Ver", "eye", () => { O.filtro = p.chave; carregarDetalhe(); }),
            A.botao("Cancelar", "x", () => controlar("orq_cancelar_passo", d.id, p.chave), "perigo")) : null,
          p.status !== "pendente" && p.status !== "executando" ? A.botao("Ver log", "eye", () => { O.filtro = p.chave; carregarDetalhe(); }) : null);
      })));
    }
    colunas.push(h("div", { class: "seta" }, ic("arrow-right")));
    colunas.push(h("div", { class: "col" }, h("div", { class: "no-f fim " + (d.relatorio ? "concluido" : "") }, ic("list-checks", "g"), h("b", { texto: "Relatório" }), h("small", { texto: "valida e consolida" }))));
    return h("div", { class: "fluxo-orq" }, colunas);
  }

  function consoleLogs(d) {
    const filtro = h("select", { class: "c-campo mini-sel", "aria-label": "Filtrar por passo", onchange: (e) => { O.filtro = e.target.value; carregarDetalhe(); } },
      h("option", { value: "" }, "todos os passos"), d.passos.map((p) => h("option", { value: p.chave, selected: O.filtro === p.chave }, p.titulo)));
    const cons = h("div", { class: "console", id: "orq-console", role: "log", "aria-live": "polite" },
      d.logs.filter((l) => !O.filtro || l.passo === O.filtro).map(linhaLog));
    return h("div", {}, h("div", { class: "filtros" }, filtro, h("span", { class: "c-dica", texto: "Tudo o que os agentes fazem fica auditado (sem segredos)." })), cons);
  }
  function linhaLog(l) {
    return h("div", { class: "lg " + l.tipo }, h("span", { class: "q", texto: (l.quando || "").slice(11, 19) }),
      h("span", { class: "p", texto: l.passo || "jarvis" }), l.agente ? h("span", { class: "a", texto: l.agente }) : null,
      h("span", { class: "t", texto: l.tipo }), h("span", { class: "c", texto: l.conteudo }));
  }

  async function desfazer(d) {
    try {
      const pre = await A.pedir("orq_a_desfazer", { id: d.id });
      A.abrirModal(h("h3", {}, ic("undo-2"), `Desfazer a orquestração #${d.id}`),
        h("p", { class: "sub-m", texto: "Volta os arquivos do projeto ao checkpoint git de antes dos agentes. O seu histórico do git não muda." }),
        h("dl", { class: "ficha" }, h("dt", { texto: "Voltam" }), h("dd", { texto: pre.voltam.join(", ") || "nada" }),
          h("dt", { texto: "Somem (criados depois)" }), h("dd", { texto: pre.somem.join(", ") || "nada" })),
        h("div", { class: "acoes-m" }, A.botao("Cancelar", "x", A.fecharModal),
          A.botao("Desfazer agora", "undo-2", async () => { try { const r = await A.pedir("orq_desfazer", { id: d.id, confirmado: true });
            aviso(`Desfeito: ${r.restaurados.length} restaurado(s), ${r.apagados.length} apagado(s).`); A.fecharModal(); carregarDetalhe(); } catch (e) { A.erro(e); } }, "perigo")));
    } catch (e) { A.erro(e); }
  }

  /* ================================================================ ABA: TERMINAL, CLIs, MCP, PROJETOS */
  async function abaTerminal() {
    const [clis, est, mcp, projetos] = await Promise.all([A.pedir("orq_clis"), A.pedir("orq_terminal_estado"), A.pedir("orq_mcp_estado"), A.pedir("orq_projetos")]);
    O.pastaTerm = O.pastaTerm || (est.pastas[0] || {}).caminho || "";
    const pasta = h("select", { class: "c-campo", "aria-label": "Pasta", onchange: (e) => { O.pastaTerm = e.target.value; } },
      est.pastas.map((p) => h("option", { value: p.caminho, selected: p.caminho === O.pastaTerm }, p.nome)));
    const cmd = h("input", { class: "c-campo mono", placeholder: "comando (PowerShell no Windows) · ex.: git status, pytest -q, npm test", autocomplete: "off", spellcheck: "false" });
    const form = h("form", { class: "linha-form", onsubmit: async (e) => { e.preventDefault(); rodarComando(pasta.value, cmd.value); cmd.value = ""; } },
      pasta, cmd, h("button", { type: "submit", class: "btn mini" }, ic("play"), " Rodar"));
    const sessoes = est.sessoes.map((s) => h("button", { type: "button", class: "exe " + (O.sessao === s.id ? "ativa" : ""), onclick: () => { O.sessao = s.id; mostrarSaida(); } },
      h("span", { class: "st " + (s.estado === "rodando" ? "executando" : s.codigo === 0 ? "concluida" : "falhou"), texto: s.estado === "rodando" ? "rodando" : `código ${s.codigo}` }),
      h("span", { class: "ped mono", texto: s.comando }), h("small", { texto: `${s.pasta.split(/[\\/]/).pop()} · ${dur(s.tempo_s || 0)}` })));
    A.secao(
      h("h3", {}, ic("bot"), "CLIs de IA"),
      h("div", { class: "tab-rol" }, h("table", { class: "tabela" },
        h("tr", {}, ["CLI", "Instalada", "Versão", "Login", ""].map((t) => h("th", { texto: t }))),
        clis.itens.map((c) => h("tr", {},
          h("td", {}, ic(c.icone), " ", c.nome), h("td", { texto: c.instalado ? "sim" : "não" }),
          h("td", { texto: c.versao ? `${c.versao}${c.compativel ? "" : " (antiga)"}` : "-" }),
          h("td", {}, h("span", { class: "estado " + estadoCss(c.estado), texto: c.detalhe || ESTADO_AG[c.estado] })),
          h("td", { class: "acoes-t" },
            A.botao("Verificar", "refresh-cw", async () => { try { await A.pedir("orq_agente_verificar", { id: c.id }); A.recarregar(); } catch (e) { A.erro(e); } }),
            c.instalado && c.pode_login ? A.botao("Login", "log-in", async () => { try { aviso((await A.pedir("orq_cli_login", { id: c.id })).mensagem); } catch (e) { A.erro(e); } }) : null,
            !c.instalado && c.instalar_cmd ? A.botao("Instalar", "download", () => instalar(c)) : null,
            c.instalado && c.tem_doctor ? A.botao("Doctor", "stethoscope", () => doctor(c)) : null))))),
      h("h3", {}, ic("square-terminal"), "Terminal"),
      h("p", { class: "c-dica", texto: "Roda no backend, só em pastas autorizadas, com ambiente sem chaves. Comando destrutivo ou que sai para fora pede confirmação." }),
      form,
      h("div", { class: "orq-layout term" },
        h("div", { class: "orq-col" }, est.sessoes.length ? h("div", { class: "lista-exe" }, sessoes) : h("div", { class: "c-vazio", texto: "Nenhum comando ainda." })),
        h("div", { class: "orq-detalhe" }, h("div", { class: "filtros", id: "term-cab" }), h("div", { class: "console mono", id: "term-saida", role: "log" }))),
      h("h3", {}, ic("folder-check"), "Projetos autorizados para os agentes"),
      projetosAutorizados(projetos),
      h("h3", {}, ic("server"), "Servidores MCP"),
      servidoresMcp(mcp));
    if (O.sessao) mostrarSaida();
  }
  async function rodarComando(pasta, comando, confirmado) {
    if (!comando.trim()) return;
    try {
      const r = await A.pedir("orq_terminal_exec", { pasta, comando, confirmado: !!confirmado });
      if (r.precisa_confirmar) {
        if (confirm(`Este comando ${r.motivo}.\n\n${r.comando}\n\nRodar mesmo assim?`)) return rodarComando(pasta, comando, true);
        return;
      }
      O.sessao = r.id;
      await A.recarregar("terminal");
    } catch (e) { A.erro(e); }
  }
  async function mostrarSaida() {
    const alvo = A.$c("term-saida"), cab = A.$c("term-cab");
    if (!alvo || !O.sessao) return;
    try {
      const s = await A.pedir("orq_terminal_saida", { id: O.sessao });
      cab.replaceChildren(...[h("span", { class: "mono", texto: s.comando }), h("span", { class: "c-dica", texto: `${s.estado}${s.codigo != null ? " · código " + s.codigo : ""}` }),
        s.estado === "rodando" ? A.botao("Matar", "ban", async () => { await A.pedir("orq_terminal_matar", { id: s.id }); A.recarregar(); }, "perigo") : null,
        s.estado === "rodando" ? A.botao("Pausar", "pause", () => A.pedir("orq_terminal_pausar", { id: s.id })) : null,
        s.estado === "pausado" ? A.botao("Retomar", "play", () => A.pedir("orq_terminal_retomar", { id: s.id })) : null].filter(Boolean));
      alvo.replaceChildren(...s.saida.map((l) => h("div", { class: "lg " + l.fluxo, "data-n": String(l.n) }, l.texto)));
      alvo.scrollTop = alvo.scrollHeight;
    } catch (e) { alvo.replaceChildren(h("div", { class: "resposta erro", texto: e.message })); }
  }
  async function instalar(c) {
    try {
      const r = await A.pedir("orq_cli_instalar", { id: c.id });
      if (r.precisa_confirmar && confirm(`Instalar o ${c.nome}?\n\n${r.comando}\n\n(${r.motivo})`)) {
        const s = await A.pedir("orq_cli_instalar", { id: c.id, confirmado: true });
        O.sessao = s.id; A.recarregar();
      }
    } catch (e) { A.erro(e); }
  }
  async function doctor(c) {
    A.abrirModal(h("h3", {}, ic("stethoscope"), `${c.nome}: doctor`), h("div", { class: "c-vazio" }, ic("loader-circle", "gira"), " Rodando…"));
    try {
      const r = await A.pedir("orq_cli_doctor", { id: c.id });
      A.abrirModal(h("h3", {}, ic("stethoscope"), `${c.nome}: doctor`), h("pre", { class: "console mono", texto: r.saida }), h("div", { class: "acoes-m" }, A.botao("Fechar", "x", A.fecharModal)));
    } catch (e) { A.abrirModal(h("h3", {}, "Doctor"), h("div", { class: "resposta erro", texto: e.message }), h("div", { class: "acoes-m" }, A.botao("Fechar", "x", A.fecharModal))); }
  }
  function projetosAutorizados(p) {
    const caminho = h("input", { class: "c-campo", placeholder: "caminho da pasta do projeto (ex.: D:\\Projetos\\Loja)", valor: p.vscode && !p.autorizados.some((x) => x.caminho === p.vscode) ? p.vscode : "" });
    return h("div", {},
      p.autorizados.length ? p.autorizados.map((x) => h("div", { class: "linha-c" }, ic("folder-check"), h("span", { texto: `${x.nome} · ${x.caminho}` }),
        h("span", { class: "quando", texto: (x.autorizado_em || "").slice(0, 10) }),
        h("button", { type: "button", class: "icone-btn", title: "Tirar a autorização", "aria-label": "Tirar a autorização de " + x.nome,
          onclick: async () => { if (!confirm(`Tirar a autorização de ${x.nome}?`)) return; await A.pedir("orq_desautorizar", { caminho: x.caminho }); A.recarregar(); } }, ic("trash-2"))))
        : h("div", { class: "c-vazio", texto: "Nenhum projeto autorizado ainda: os agentes só trabalham em pastas autorizadas." }),
      h("form", { class: "linha-form", onsubmit: async (e) => { e.preventDefault();
        if (!caminho.value.trim() || !confirm(`Autorizar os agentes de IA a trabalhar em ${caminho.value}?`)) return;
        try { await A.pedir("orq_autorizar", { caminho: caminho.value }); A.recarregar(); } catch (err) { A.erro(err); } } },
        caminho, h("button", { type: "submit", class: "btn mini" }, ic("folder-plus"), " Autorizar")));
  }
  function servidoresMcp(m) {
    const nome = h("input", { class: "c-campo", placeholder: "nome (ex.: arquivos)" });
    const comando = h("input", { class: "c-campo mono", placeholder: "comando (ex.: npx)" });
    const args = h("input", { class: "c-campo mono", placeholder: "argumentos (ex.: -y @modelcontextprotocol/server-filesystem D:\\Docs)" });
    return h("div", {},
      m.servidores.length ? m.servidores.map((s) => h("div", { class: "integ mcp" },
        h("div", { class: "topo-i" }, h("span", { class: "logo-i" }, ic("server", "g")),
          h("div", {}, h("h4", { texto: s.nome }), h("span", { class: "auth mono", texto: [s.comando, ...s.args].join(" ") }))),
        h("div", { class: "rodape-i" }, h("span", { class: "estado " + (s.conectado ? "conectado" : "desconectado"), texto: s.conectado ? `conectado · ${s.ferramentas.length} ferramentas` : "desconectado" })),
        s.ferramentas.length ? h("div", { class: "ferr" }, s.ferramentas.map((f) => h("span", { class: "pil-f " + f.nivel, title: f.descricao, texto: `${f.nome} · ${f.nivel}` }))) : null,
        h("div", { class: "rodape-i" }, A.botao(s.conectado ? "Atualizar" : "Conectar", "link", async () => { try { await A.pedir("orq_mcp_conectar", { nome: s.nome }); A.recarregar(); } catch (e) { A.erro(e); } }),
          A.botao("Remover", "trash-2", async () => { if (!confirm(`Remover o servidor ${s.nome}?`)) return; await A.pedir("orq_mcp_remover", { nome: s.nome }); A.recarregar(); }, "perigo"))))
        : h("div", { class: "c-vazio", texto: "Nenhum servidor MCP. Adicione abaixo (mesmo formato do Claude Desktop) e o Jarvis passa a usar as ferramentas dele, com permissão por ferramenta." }),
      h("form", { class: "linha-form", onsubmit: async (e) => { e.preventDefault();
        try { await A.pedir("orq_mcp_salvar", { nome: nome.value, comando: comando.value, args: args.value }); A.recarregar(); } catch (err) { A.erro(err); } } },
        nome, comando, args, h("button", { type: "submit", class: "btn mini" }, ic("plus"), " Adicionar")),
      h("p", { class: "c-dica", texto: `Configuração em ${m.arquivo}. Permissões por ferramenta: livre 🟢 · registrado 🟡 · confirmar 🔴 (padrão; dica "só leitura" do servidor vira 🟡).` }));
  }

  /* ================================================================ eventos ao vivo */
  function evento(ev) {
    if (!A.aberta()) return;
    const sec = A.secaoAtual();
    if (ev.acao === "logs" && sec === "orquestracao" && O.exe) {
      const cons = A.$c("orq-console");
      const novas = (ev.linhas || []).filter((l) => l.execucao === O.exe && l.id > O.ultimoLog && (!O.filtro || l.passo === O.filtro));
      if (cons && novas.length) {
        const noFim = cons.scrollHeight - cons.scrollTop - cons.clientHeight < 40;
        for (const l of novas) { cons.append(linhaLog({ ...l, quando: "0000-00-00T" + l.quando })); O.ultimoLog = Math.max(O.ultimoLog, l.id); }
        if (noFim) cons.scrollTop = cons.scrollHeight;
      }
    } else if (ev.acao === "estado") {
      if (sec === "orquestracao") {
        agendar("lista", carregarLista, 900);
        if (O.exe && ev.execucao === O.exe) agendar("det", carregarDetalhe, 700);
      }
      else if (sec === "agentes") agendar("ag", () => A.recarregar(), 900);
    } else if (ev.acao === "terminal" && sec === "terminal" && ev.sessao === O.sessao) {
      const alvo = A.$c("term-saida");
      if (alvo) {
        const ultimo = alvo.lastElementChild ? Number(alvo.lastElementChild.dataset.n) : 0;
        for (const l of ev.linhas || []) if (l.n > ultimo) alvo.append(h("div", { class: "lg " + l.fluxo, "data-n": String(l.n) }, l.texto));
        alvo.scrollTop = alvo.scrollHeight;
      }
    } else if (ev.acao === "terminal_estado" && sec === "terminal") {
      agendar("term", () => A.recarregar(), 600);
    }
  }

  Central.registrarAba("agentes", "Agentes IA", "bot", abaAgentes);
  Central.registrarAba("orquestracao", "Orquestração", "network", abaOrquestracao);
  Central.registrarAba("terminal", "Terminal", "square-terminal", abaTerminal);
  return { evento, abrirExecucao };
})();
