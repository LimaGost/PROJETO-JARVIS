"use strict";
/* HUD do Jarvis. Todo texto que vem do servidor (fala do modelo, resultado de ferramenta, trecho de site ou
   arquivo) é conteúdo não confiável: entra na página SEMPRE por textContent, nunca por innerHTML. */

const $ = (id) => document.getElementById(id);
function el(tag, classe, texto) {
  const e = document.createElement(tag);
  if (classe) e.className = classe;
  if (texto !== undefined) e.textContent = texto;
  return e;
}

const NOMES_ESTADO = {
  iniciando: "INICIANDO", aguardando: "AGUARDANDO", ouvindo: "OUVINDO", transcrevendo: "TRANSCREVENDO",
  pensando: "PENSANDO", falando: "FALANDO", confirmando: "CONFIRMAÇÃO", mudo: "MUDO",
};
const DICAS = {
  aguardando: "Diga “Hey Jarvis” ou digite abaixo", ouvindo: "Pode falar…", transcrevendo: "Entendendo o que você disse…",
  pensando: "Processando seu pedido…", falando: "", confirmando: "Responda por voz ou clique na janela", iniciando: "Carregando módulos…",
  mudo: "Microfone desligado: só texto",
};
const MAX_MSGS = 80;

const S = {
  ws: null, tentativas: 0, conectado: false, estado: "iniciando", falando: false, mudo: false,
  nivel: { mic: 0, voz: 0 }, historicoNiveis: new Array(72).fill(0),
  msgAtual: null, fatos: [], pendente: null, stats: { cpu: [], gpu: [] }, cerebro: null,
};
const token = document.querySelector('meta[name="jarvis-token"]').content;

/* ============================================================ mentalidade (Qwen / Claude) */
function renderCerebros() {
  const c = S.cerebro, caixa = $("cerebros");
  caixa.hidden = !(c && c.opcoes && c.opcoes.length > 1);
  if (caixa.hidden) return;
  caixa.replaceChildren();
  for (const o of c.opcoes) {
    const b = el("button", "btn cerebro", o.nome);
    b.type = "button";
    b.setAttribute("aria-pressed", String(!!o.ativo));
    b.title = o.disponivel ? o.descricao : `Indisponível: ${o.motivo}`;
    b.disabled = !o.disponivel || !S.conectado || !!c.trocando;
    if (c.trocando === o.id) b.classList.add("trocando");
    if (!o.ativo) b.addEventListener("click", () => {
      S.cerebro = { ...c, trocando: o.id }; renderCerebros();
      enviar({ tipo: "trocar_cerebro", id: o.id });
    });
    caixa.appendChild(b);
  }
}

/* ============================================================ estado visível */
function estadoVisivel() {
  if (S.falando) return "falando";
  if (S.mudo && (S.estado === "aguardando" || S.estado === "ouvindo")) return "mudo";
  return S.estado;
}
function atualizarEstado() {
  const e = estadoVisivel(), rot = $("estado");
  if (document.body.dataset.estado !== e) { // pequena falha de holograma a cada troca
    rot.classList.remove("troca"); void rot.offsetWidth; rot.classList.add("troca");
  }
  document.body.dataset.estado = e;
  rot.textContent = NOMES_ESTADO[e] || e.toUpperCase();
  $("dica").textContent = (e === "iniciando" && S.detalhe) ? S.detalhe : (DICAS[e] ?? "");
}

/* ============================================================ conversa */
function rolarFim() {
  const c = $("conversa");
  const perto = c.scrollHeight - c.scrollTop - c.clientHeight < 90;
  if (perto) requestAnimationFrame(() => { c.scrollTop = c.scrollHeight; });
}
function podar(lista, max) { while (lista.children.length > max) lista.removeChild(lista.firstChild); }
function novaMsg(tipo, quem, texto) {
  const m = el("div", "msg " + tipo);
  if (quem) m.appendChild(el("span", "quem", quem));
  const corpo = el("span", "corpo", texto);
  m.appendChild(corpo);
  $("conversa").appendChild(m);
  podar($("conversa"), MAX_MSGS);
  rolarFim();
  return corpo;
}
function addUsuario(texto, origem) {
  S.msgAtual = null;
  novaMsg("usuario", origem === "hud" ? "Você (texto)" : "Você", texto);
}
function addJarvis(texto) {
  if (S.msgAtual && S.msgAtual.isConnected) {
    S.msgAtual.textContent += " " + texto;
    rolarFim();
  } else {
    S.msgAtual = novaMsg("jarvis", "Jarvis", texto);
  }
}
function addSistema(texto) { S.msgAtual = null; novaMsg("sistema", "", texto); }
function aviso(texto) {
  const a = el("div", "aviso", texto);
  $("avisos").appendChild(a);
  setTimeout(() => a.remove(), 4500);
}

/* ============================================================ memória */
let timerApagar = null;
function renderMemoria() {
  const filtro = $("busca-memoria").value.trim().toLowerCase();
  const lista = $("lista-memoria");
  lista.replaceChildren();
  const fatos = S.fatos.filter((f) => !filtro || f.texto.toLowerCase().includes(filtro) || f.categoria.includes(filtro));
  for (const f of fatos) {
    const li = el("li", "fato");
    const btn = el("button", "", "×");
    btn.type = "button";
    btn.setAttribute("aria-label", "Esquecer: " + f.texto);
    btn.title = "Esquecer isto";
    btn.addEventListener("click", () => {
      if (btn.classList.contains("sim")) { enviar({ tipo: "apagar_fato", id: f.id }); return; }
      clearTimeout(timerApagar);
      document.querySelectorAll(".fato button.sim").forEach((b) => { b.className = ""; b.textContent = "×"; });
      btn.className = "sim"; btn.textContent = "Esquecer?";
      timerApagar = setTimeout(() => { btn.className = ""; btn.textContent = "×"; }, 3000);
    });
    li.append(el("span", "cat", f.categoria), el("span", "txt", f.texto), btn);
    lista.appendChild(li);
  }
  $("vazio-memoria").hidden = fatos.length > 0;
  $("vazio-memoria").textContent = S.fatos.length ? "Nada com esse filtro." : "Ainda não aprendi nada sobre você.";
  $("n-fatos").textContent = S.fatos.length ? `(${S.fatos.length})` : "";
}

/* ============================================================ confirmação */
let timerConf = null, liberaConf = null;
function abrirConfirmacao(ev) {
  S.pendente = ev.id;
  $("conf-resumo").textContent = "Senhor, posso " + ev.resumo + "?";
  $("confirmacao").hidden = false;
  const sim = $("conf-sim");
  sim.disabled = true;
  clearTimeout(liberaConf);
  liberaConf = setTimeout(() => { sim.disabled = false; }, 700); // evita confirmar por um clique que já estava a caminho
  $("conf-nao").focus();
  const barra = $("conf-tempo");
  const total = (ev.expira_s || 30) * 1000;
  barra.style.transition = "none";
  barra.style.transform = "scaleX(1)";
  void barra.offsetWidth;
  barra.style.transition = `transform ${total}ms linear`;
  barra.style.transform = "scaleX(0)";
  clearTimeout(timerConf);
  timerConf = setTimeout(() => fecharConfirmacao(), total + 500);
}
function fecharConfirmacao() {
  S.pendente = null;
  $("confirmacao").hidden = true;
  clearTimeout(timerConf); clearTimeout(liberaConf);
  $("entrada").focus({ preventScroll: true });
}
function responderConfirmacao(sim) {
  if (S.pendente == null) return;
  enviar({ tipo: "confirmar", id: S.pendente, sim });
  fecharConfirmacao();
}

/* ============================================================ eventos do servidor */
function aplicar(ev) {
  switch (ev.tipo) {
    case "estado": S.estado = ev.estado; S.detalhe = ev.detalhe || ""; atualizarEstado(); break;
    case "pronto": $("btn-mudo").hidden = !ev.tem_mic; break;
    case "voz": S.falando = !!ev.falando; atualizarEstado(); break;
    case "mudo": S.mudo = !!ev.valor; $("btn-mudo").setAttribute("aria-pressed", String(S.mudo)); atualizarEstado(); break;
    case "usuario": addUsuario(ev.texto, ev.origem); break;
    case "jarvis": addJarvis(ev.texto); break;
    case "fim_turno": S.msgAtual = null; break;
    case "sistema": addSistema(ev.texto); aviso(ev.texto); break;
    case "confirmacao": abrirConfirmacao(ev); break;
    case "confirmacao_resolvida":
      if (S.pendente === ev.id) fecharConfirmacao();
      aviso((ev.sim ? "Confirmado" : "Negado") + ({ voz: " por voz", hud: "", tempo: " (tempo esgotado)", parar: " (interrompido)" }[ev.por] || ""));
      break;
    case "memoria": S.fatos = ev.fatos || []; renderMemoria(); break;
    case "nivel": S.nivel = { mic: ev.mic || 0, voz: ev.voz || 0 }; break;
    case "stats": aplicarStats(ev); break;
    case "cerebro": S.cerebro = ev; renderCerebros(); break;
  }
}

function hello(ev) {
  S.rotulo = (ev.nome || "Jarvis").split("").join(".").toUpperCase() + ".";
  decodificar($("nome"), S.rotulo);
  S.estado = ev.estado; S.falando = !!ev.falando; S.mudo = !!ev.mudo; S.detalhe = "";
  $("btn-mudo").setAttribute("aria-pressed", String(S.mudo));
  $("btn-mudo").hidden = !ev.tem_mic;
  S.cerebro = ev.cerebro || null; renderCerebros();
  $("conversa").replaceChildren(); S.msgAtual = null;
  for (const h of ev.historico || []) aplicar(h);
  atualizarEstado();
  if (ev.pendente) aviso("Há uma confirmação pendente.");
  requestAnimationFrame(() => { $("conversa").scrollTop = $("conversa").scrollHeight; });
}

// o nome "se decifra" letra a letra, como um sinal sendo sintonizado
let timerDecod = null;
function decodificar(alvo, texto) {
  clearInterval(timerDecod);
  if (REDUZIR) { alvo.textContent = texto; return; }
  const glifos = "▓▒░<>/\\|#%&*+=?01ΔΣΩΛΞ";
  let passo = 0;
  timerDecod = setInterval(() => {
    passo++;
    alvo.textContent = [...texto].map((ch, i) =>
      ch === "." || i < passo / 2 ? ch : glifos[Math.floor(Math.random() * glifos.length)]).join("");
    if (passo / 2 >= texto.length) { clearInterval(timerDecod); alvo.textContent = texto; }
  }, 35);
}

/* ============================================================ conexão */
function enviar(obj) {
  if (S.ws && S.ws.readyState === WebSocket.OPEN) S.ws.send(JSON.stringify(obj));
}
function conectar() {
  const ws = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(token)}`);
  S.ws = ws;
  ws.onopen = () => {
    S.tentativas = 0; S.conectado = true;
    $("chip-conexao").textContent = "conectado"; $("chip-conexao").className = "chip on";
    $("offline").hidden = true;
    $("entrada").disabled = false; $("enviar").disabled = false;
  };
  ws.onmessage = (m) => {
    let ev; try { ev = JSON.parse(m.data); } catch { return; }
    if (ev.tipo === "hello") hello(ev); else aplicar(ev);
  };
  ws.onclose = () => {
    S.conectado = false;
    $("chip-conexao").textContent = "desconectado"; $("chip-conexao").className = "chip off";
    $("entrada").disabled = true; $("enviar").disabled = true;
    $("offline").hidden = false;
    renderCerebros();
    fecharConfirmacao();
    S.tentativas++;
    setTimeout(conectar, Math.min(5000, 400 * S.tentativas));
  };
  ws.onerror = () => ws.close();
}

/* ============================================================ telemetria: mostradores circulares */
const dials = { cpu: { v: 0, alvo: 0 }, ram: { v: 0, alvo: 0 }, gpu: { v: 0, alvo: 0 }, vram: { v: 0, alvo: 0 } };
function corPct(p) { return p >= 90 ? [255, 77, 77] : p >= 75 ? [255, 178, 36] : [25, 211, 255]; }

function desenharDial(chave, dt, t) {
  const cv = $("d-" + chave), d = dials[chave];
  if (!cv) return;
  d.v += (d.alvo - d.v) * Math.min(1, dt * 4);
  const c = cv.getContext("2d"), W = cv.width, cx = W / 2, cy = W / 2, R = W / 2 - 7;
  c.clearRect(0, 0, W, W);
  const [r, g, b] = corPct(d.v), rgba = (a) => `rgba(${r},${g},${b},${a})`;
  c.globalCompositeOperation = "lighter";
  // graduação externa, girando devagar
  c.save(); c.translate(cx, cy); c.rotate(REDUZIR ? 0 : t * 0.1);
  c.strokeStyle = rgba(0.3); c.lineWidth = 1;
  c.beginPath(); c.arc(0, 0, R, 0, 7); c.stroke();
  for (let i = 0; i < 72; i++) {
    const a = (i / 72) * Math.PI * 2, longo = i % 6 === 0, l = longo ? 8 : 4;
    c.strokeStyle = rgba(longo ? 0.75 : 0.35); c.lineWidth = longo ? 1.6 : 1;
    c.beginPath(); c.moveTo(Math.cos(a) * R, Math.sin(a) * R); c.lineTo(Math.cos(a) * (R - l), Math.sin(a) * (R - l)); c.stroke();
  }
  c.restore();
  // arco de valor (270°) com trilho
  const a0 = Math.PI * 0.75, total = Math.PI * 1.5, rr = R - 17;
  c.lineWidth = 10; c.strokeStyle = rgba(0.14);
  c.beginPath(); c.arc(cx, cy, rr, a0, a0 + total); c.stroke();
  if (d.v > 0.4) {
    c.shadowColor = rgba(1); c.shadowBlur = 14; c.strokeStyle = rgba(0.95);
    c.beginPath(); c.arc(cx, cy, rr, a0, a0 + total * Math.min(1, d.v / 100)); c.stroke();
    c.shadowBlur = 0;
  }
  // anel interno fino com divisões
  c.strokeStyle = rgba(0.4); c.lineWidth = 1;
  c.beginPath(); c.arc(cx, cy, rr - 9, 0, 7); c.stroke();
  c.setLineDash([2, 5]); c.strokeStyle = rgba(0.5);
  c.beginPath(); c.arc(cx, cy, rr - 15, 0, 7); c.stroke(); c.setLineDash([]);
  c.globalCompositeOperation = "source-over";
}

function desenharOsc(t) {
  const cv = $("osc");
  if (!cv) return;
  const c = cv.getContext("2d"), W = cv.width, H = cv.height;
  c.clearRect(0, 0, W, H);
  c.strokeStyle = "rgba(25,211,255,.16)"; c.lineWidth = 1;
  for (let i = 1; i < 4; i++) { c.beginPath(); c.moveTo(0, (H / 4) * i); c.lineTo(W, (H / 4) * i); c.stroke(); }
  for (let i = 1; i < 12; i++) { c.beginPath(); c.moveTo((W / 12) * i, 0); c.lineTo((W / 12) * i, H); c.stroke(); }
  const n = S.historicoNiveis.length;
  c.shadowColor = "rgba(25,211,255,1)"; c.shadowBlur = 8; c.strokeStyle = "rgba(143,240,255,.95)"; c.lineWidth = 1.6;
  c.beginPath();
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * W, amp = S.historicoNiveis[i] * (H * 0.42);
    const y = H / 2 + Math.sin(i * 1.3 + t * 9) * amp;
    i ? c.lineTo(x, y) : c.moveTo(x, y);
  }
  c.stroke(); c.shadowBlur = 0;
}

function aplicarStats(s) {
  const pct = (v) => Math.round(v) + "%";
  dials.cpu.alvo = s.cpu; $("v-cpu").textContent = pct(s.cpu);
  dials.ram.alvo = s.ram; $("v-ram").textContent = pct(s.ram);
  $("v-ram-gb").textContent = `${s.ram_usada_gb} / ${s.ram_total_gb} GB`;
  if (s.gpu !== undefined) {
    dials.gpu.alvo = s.gpu; $("v-gpu").textContent = pct(s.gpu);
    const pv = (s.vram_usada_mb / s.vram_total_mb) * 100;
    dials.vram.alvo = pv; $("v-vram").textContent = pct(pv);
    $("v-vram-gb").textContent = `${(s.vram_usada_mb / 1024).toFixed(1)} / ${(s.vram_total_mb / 1024).toFixed(1)} GB`;
    $("v-temp").textContent = s.gpu_temp.toFixed(0) + " °C";
  }
  const h = Math.floor(s.ligado_s / 3600), m = Math.floor((s.ligado_s % 3600) / 60);
  $("v-uptime").textContent = h ? `${h} h ${m} min` : `${m} min`;
}

/* ============================================================ núcleo holográfico */
// Esfera de filamentos de luz, no estilo da "mente" do J.A.R.V.I.S. nos filmes, só em ciano e azul.
// O vermelho fica reservado ao pedido de confirmação, que é um alerta.
const CORES = {
  iniciando: [25, 211, 255], aguardando: [25, 211, 255], ouvindo: [70, 230, 255], transcrevendo: [90, 200, 255],
  pensando: [64, 150, 255], falando: [140, 245, 255], confirmando: [255, 77, 77], mudo: [100, 125, 140],
};
const AZUL_PROFUNDO = [16, 72, 190];
const REDUZIR = matchMedia("(prefers-reduced-motion: reduce)").matches;
const TAU = Math.PI * 2;
const reator = { t: 0, nivel: 0, cor: [25, 211, 255], giro: 0, giro2: 0, fluxo: 0, onda: 0 };

// geometria fixa, gerada uma vez com semente para o desenho ser o mesmo a cada recarga
function gerador(s) {
  return () => {
    s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
// eixo w e dois vetores u, v perpendiculares a ele
function baseDe(w) {
  const ref = Math.abs(w[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
  let u = [ref[1] * w[2] - ref[2] * w[1], ref[2] * w[0] - ref[0] * w[2], ref[0] * w[1] - ref[1] * w[0]];
  const lu = Math.hypot(...u);
  u = u.map((x) => x / lu);
  const v = [w[1] * u[2] - w[2] * u[1], w[2] * u[0] - w[0] * u[2], w[0] * u[1] - w[1] * u[0]];
  return [w, u, v];
}
function baseAleatoria(rnd) {
  const z = rnd() * 2 - 1, a = rnd() * TAU, s = Math.sqrt(1 - z * z);
  return baseDe([Math.cos(a) * s, Math.sin(a) * s, z]);
}
// arco sobre a esfera de raio r: alfa = π/2 dá um círculo máximo, menor dá um "paralelo"
function arco(rnd, r, alfa, th0, len, ruido, [w, u, v]) {
  const n = Math.max(2, Math.ceil(len * 14) + 1), pts = new Float32Array(n * 3);
  const ca = Math.cos(alfa), sa = Math.sin(alfa);
  for (let i = 0; i < n; i++) {
    const th = th0 + (len * i) / (n - 1), rr = r * (1 + (rnd() - 0.5) * ruido), c = Math.cos(th), s = Math.sin(th);
    for (let k = 0; k < 3; k++) pts[i * 3 + k] = rr * (w[k] * ca + sa * (u[k] * c + v[k] * s));
  }
  return pts;
}
// grupos: 0 casca externa, 1 camadas internas, 2 núcleo, 3 anel largo; cada um gira no seu ritmo
const GEO = (() => {
  const rnd = gerador(1977), frags = [];
  const add = (grupo, pts, r, peso) => frags.push({ grupo, pts, r, peso, f: rnd() * TAU });
  // casca externa: arcos quebrados, alguns em feixes paralelos
  for (let i = 0; i < 380; i++) {
    const b = baseAleatoria(rnd), r = [1, 0.96, 1.05][Math.floor(rnd() * 3)];
    const alfa = rnd() < 0.6 ? Math.PI / 2 : 0.3 + rnd() * 1.2, th0 = rnd() * TAU, len = 0.08 + rnd() ** 2 * 0.9, peso = 0.4 + rnd() * 0.6;
    add(0, arco(rnd, r, alfa, th0, len, 0.03, b), r, peso);
    const irmaos = rnd() < 0.3 ? 1 + Math.floor(rnd() * 2) : 0;
    for (let k = 1; k <= irmaos; k++) add(0, arco(rnd, r * (1 - 0.015 * k), alfa, th0 + 0.02 * k, len * (0.6 + rnd() * 0.4), 0.03, b), r, peso * 0.8);
  }
  // estilhaços: traços curtos tangentes à superfície
  for (let i = 0; i < 380; i++) {
    const r = 0.9 + rnd() * 0.22;
    add(0, arco(rnd, r, Math.PI / 2, rnd() * TAU, (0.02 + rnd() * 0.07) / r, 0, baseAleatoria(rnd)), r, 0.3 + rnd() * 0.7);
  }
  // camadas internas e raios que partem do centro
  for (let i = 0; i < 220; i++) {
    const r = 0.68 + rnd() * 0.16;
    add(1, arco(rnd, r, rnd() < 0.5 ? Math.PI / 2 : 0.4 + rnd(), rnd() * TAU, 0.1 + rnd() * 0.6, 0.04, baseAleatoria(rnd)), r, 0.3 + rnd() * 0.5);
  }
  for (let i = 0; i < 18; i++) {
    const [w] = baseAleatoria(rnd), a = 0.12, z = 0.95 + rnd() * 0.1;
    add(1, new Float32Array([w[0] * a, w[1] * a, w[2] * a, w[0] * z, w[1] * z, w[2] * z]), z, 0.25 + rnd() * 0.3);
  }
  // núcleo: laços ondulados entrelaçados e filamentos curtos
  for (let i = 0; i < 6; i++) {
    const [w, u, v] = baseAleatoria(rnd), rc = 0.16 + rnd() * 0.14, h = 0.04 + rnd() * 0.05, m = 2 + Math.floor(rnd() * 2), fase = rnd() * TAU;
    const pts = new Float32Array(65 * 3);
    for (let j = 0; j <= 64; j++) {
      const s = (j / 64) * TAU, c = Math.cos(s) * rc, sn = Math.sin(s) * rc, o = Math.sin(m * s + fase) * h;
      for (let k = 0; k < 3; k++) pts[j * 3 + k] = u[k] * c + v[k] * sn + w[k] * o;
    }
    add(2, pts, rc + h, 0.8 + rnd() * 0.2);
  }
  for (let i = 0; i < 80; i++) {
    const r = 0.3 + rnd() * 0.18;
    add(2, arco(rnd, r, Math.PI / 2, rnd() * TAU, 0.2 + rnd() * 0.9, 0.05, baseAleatoria(rnd)), r, 0.4 + rnd() * 0.5);
  }
  // anel largo e achatado em volta da esfera, todo picotado
  const eixo = [0.15, 1, 0.3], le = Math.hypot(...eixo), bAnel = baseDe(eixo.map((x) => x / le));
  for (const [r, peso] of [[1.3, 0.85], [1.36, 0.5], [1.24, 0.4]]) {
    let th = rnd() * TAU;
    const fim = th + TAU;
    while (th < fim) {
      const len = Math.min(0.06 + rnd() * 0.45, fim - th);
      add(3, arco(rnd, r, Math.PI / 2, th, len, 0.008, bAnel), r, peso * (0.6 + rnd() * 0.4));
      th += len + 0.03 + rnd() * 0.14;
    }
  }
  return { frags };
})();
const fragmentos = Array.from({ length: 160 }, () => novoFragmento(true));
function novoFragmento(espalhar) {
  return { a: Math.random() * TAU, r: espalhar ? 0.72 + Math.random() * 0.36 : 0.72 + Math.random() * 0.06, v: 0.03 + Math.random() * 0.08, rastro: Math.random() < 0.5 };
}

// sprite de brilho por cor
const sprites = new Map();
function brilho(r, g, b) {
  const chave = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3);
  let s = sprites.get(chave);
  if (!s) {
    if (sprites.size > 48) sprites.clear();
    s = document.createElement("canvas"); s.width = s.height = 64;
    const c = s.getContext("2d"), gr = c.createRadialGradient(32, 32, 0, 32, 32, 32);
    gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(0.15, `rgba(${r},${g},${b},0.95)`);
    gr.addColorStop(0.45, `rgba(${r},${g},${b},0.25)`); gr.addColorStop(1, `rgba(${r},${g},${b},0)`);
    c.fillStyle = gr; c.fillRect(0, 0, 64, 64);
    sprites.set(chave, s);
  }
  return s;
}

// gira em Y, inclina em X e aplica uma perspectiva leve; o resultado fica em P
const P = { x: 0, y: 0, z: 0 };
function projetar(x, y, z, cg, sg, ci, si) {
  const x1 = x * cg + z * sg, z1 = z * cg - x * sg;
  P.z = y * si + z1 * ci;
  const k = 1 - P.z * 0.13;
  P.x = x1 * k; P.y = (y * ci - z1 * si) * k;
}

// os filamentos são agrupados por intensidade: um traço por faixa em vez de um por filamento
const FAIXAS = 10;
const centelhas = [];
function desenharFilamentos(cc, { cx, cy, ESF, INCL, n, t, corV, corF, corC, sprC }) {
  const ci = Math.cos(INCL), si = Math.sin(INCL), g = reator.giro;
  centelhas.length = 0;
  const giros = [g * 0.35, -g * 0.5, g * 1.4, g * 0.12], cgs = giros.map(Math.cos), sgs = giros.map(Math.sin);
  const esc = [ESF * (1 + n * 0.07), ESF * (1 + n * 0.04), ESF * (1 + n * 0.3), ESF * (1 + n * 0.03)];
  const caminhos = Array.from({ length: FAIXAS }, () => new Path2D());
  const onda = reator.onda, cintila = t * (2 + n * 10);
  for (const fr of GEO.frags) {
    const gr = fr.grupo, cg = cgs[gr], sg = sgs[gr], e = esc[gr], p = fr.pts, np = p.length / 3, mi = (np >> 1) * 3;
    projetar(p[mi], p[mi + 1], p[mi + 2], cg, sg, ci, si);
    const zr = Math.max(-1, Math.min(1, P.z / fr.r)), frente = (1 - zr) / 2, borda = 1 - Math.abs(zr);
    // uma onda de energia varre a esfera; a borda e a frente brilham, o fundo fica apagado
    const luz = 0.5 + 0.5 * Math.sin(P.x * 3.2 + P.y * 2.4 - onda + fr.f * 0.25);
    const pisca = 0.8 + 0.2 * Math.sin(fr.f + cintila);
    const v = fr.peso * pisca * (gr === 2
      ? (0.55 + 0.45 * luz) * (0.8 + n)
      : (0.1 + 0.5 * frente + 0.45 * borda * borda) * (0.45 + 0.55 * luz) * (1 + n * 0.9));
    const cam = caminhos[Math.min(FAIXAS - 1, Math.floor(v * FAIXAS))];
    // pontos de luz brancos sobre os filamentos mais fortes, como no holograma do filme
    if (v > 0.5 && fr.f < 2.2) centelhas.push(cx + P.x * e, cy + P.y * e, v, fr.f);
    for (let i = 0; i < np; i++) {
      projetar(p[i * 3], p[i * 3 + 1], p[i * 3 + 2], cg, sg, ci, si);
      const x = cx + P.x * e, y = cy + P.y * e;
      i ? cam.lineTo(x, y) : cam.moveTo(x, y);
    }
  }
  cc.lineCap = "round"; cc.lineJoin = "round";
  const grossura = Math.min(1.7, Math.max(1, ESF / 230)); // na tela cheia a esfera é grande: traço um pouco mais grosso
  for (let k = 0; k < FAIXAS; k++) {
    const q = (k + 1) / FAIXAS;
    cc.strokeStyle = k < 3 ? corF : k < 8 ? corV : corC;
    cc.globalAlpha = 0.2 + 0.8 * q;
    cc.lineWidth = (0.7 + q * 1.3) * grossura;
    cc.stroke(caminhos[k]);
  }
  for (let i = 0; i < centelhas.length; i += 4) {
    const v = centelhas[i + 2], tw = 0.5 + 0.5 * Math.sin(t * 3 + centelhas[i + 3] * 9), s = (3 + v * 6) * grossura;
    cc.globalAlpha = Math.min(1, v * tw);
    cc.drawImage(sprC, centelhas[i] - s / 2, centelhas[i + 1] - s / 2, s, s);
  }
  cc.globalAlpha = 1;
}

function desenharCentro(cc, { cx, cy, ESF, n, rgba, spr }) {
  let g = cc.createRadialGradient(cx, cy, 0, cx, cy, ESF * 0.8);
  g.addColorStop(0, rgba(0.16 + n * 0.22)); g.addColorStop(0.3, rgba(0.04 + n * 0.1)); g.addColorStop(1, rgba(0));
  cc.fillStyle = g; cc.beginPath(); cc.arc(cx, cy, ESF * 0.8, 0, TAU); cc.fill();
  const rn = ESF * (0.06 + n * 0.08);
  g = cc.createRadialGradient(cx, cy, 0, cx, cy, rn);
  g.addColorStop(0, "rgba(255,255,255,0.95)"); g.addColorStop(0.35, rgba(0.85)); g.addColorStop(1, rgba(0));
  cc.fillStyle = g; cc.beginPath(); cc.arc(cx, cy, rn, 0, TAU); cc.fill();
  cc.drawImage(spr, cx - rn, cy - rn, rn * 2, rn * 2);
}

function desenharFragmentos(cc, { cx, cy, R, n, dt, corV }) {
  // estilhaços se soltando da esfera
  cc.fillStyle = corV; cc.strokeStyle = corV; cc.lineWidth = 1;
  const acel = REDUZIR ? 0.2 : 1 + n * 4;
  for (let i = 0; i < fragmentos.length; i++) {
    const f = fragmentos[i];
    f.r += f.v * dt * acel;
    if (f.r > 1.08) { fragmentos[i] = novoFragmento(false); continue; }
    cc.globalAlpha = Math.max(0, 1 - (f.r - 0.72) / 0.36) * 0.75;
    const ca = Math.cos(f.a), sa = Math.sin(f.a);
    if (f.rastro) {
      cc.beginPath(); cc.moveTo(cx + ca * (f.r - 0.03) * R, cy + sa * (f.r - 0.03) * R); cc.lineTo(cx + ca * f.r * R, cy + sa * f.r * R); cc.stroke();
    } else {
      cc.fillRect(cx + ca * f.r * R - 0.8, cy + sa * f.r * R - 0.8, 1.6, 1.6);
    }
  }
  cc.globalAlpha = 1;
}

// anéis de HUD em volta da esfera, grandes o bastante para ocupar a tela e passar por baixo dos painéis
function desenharAneis({ c, cx, cy, ESF, n, rgba, fundo }) {
  const halo = ESF * 1.9;
  const g = c.createRadialGradient(cx, cy, ESF * 0.3, cx, cy, halo);
  g.addColorStop(0, `rgba(${fundo},0.2)`); g.addColorStop(0.55, `rgba(${fundo},0.08)`); g.addColorStop(1, `rgba(${fundo},0)`);
  c.fillStyle = g; c.beginPath(); c.arc(cx, cy, halo, 0, TAU); c.fill();

  const pulso = 1 + n * 0.04, g1 = reator.giro, g2 = reator.giro2;
  c.save(); c.translate(cx, cy);
  // anel graduado, girando devagar
  let r = ESF * 1.2 * pulso;
  c.lineWidth = 1; c.strokeStyle = rgba(0.22);
  c.beginPath(); c.arc(0, 0, r, 0, TAU); c.stroke();
  c.beginPath();
  for (let i = 0; i < 180; i++) {
    const a = (i / 180) * TAU + g2 * 0.15, l = i % 15 === 0 ? 12 : i % 5 === 0 ? 7 : 3;
    c.moveTo(Math.cos(a) * r, Math.sin(a) * r); c.lineTo(Math.cos(a) * (r + l), Math.sin(a) * (r + l));
  }
  c.strokeStyle = rgba(0.3); c.stroke();
  // arcos grossos segmentados, no sentido contrário
  r = ESF * 1.3 * pulso;
  c.lineWidth = 4;
  for (const [a0, len, al] of [[0, 0.9, 0.4], [1.3, 0.35, 0.25], [2.2, 1.4, 0.35], [4.1, 0.6, 0.3], [5, 0.8, 0.2]]) {
    c.strokeStyle = rgba(al); c.beginPath(); c.arc(0, 0, r, a0 + g1 * 0.25, a0 + len + g1 * 0.25); c.stroke();
  }
  // anel tracejado, com quatro marcadores apontando para o centro
  r = ESF * 1.48;
  c.lineWidth = 1.5; c.setLineDash([2, 9]); c.lineDashOffset = g2 * 20; c.strokeStyle = rgba(0.28);
  c.beginPath(); c.arc(0, 0, r, 0, TAU); c.stroke(); c.setLineDash([]);
  c.fillStyle = rgba(0.55);
  for (let i = 0; i < 4; i++) {
    c.save(); c.rotate((i * Math.PI) / 2 + g1 * 0.05);
    c.beginPath(); c.moveTo(r + 6, 0); c.lineTo(r + 16, -5); c.lineTo(r + 16, 5); c.closePath(); c.fill();
    c.restore();
  }
  // arcos largos dos dois lados; o nível da voz acende de baixo para cima
  r = ESF * 1.7;
  const esq = [Math.PI * 0.62, Math.PI * 1.38], dir = [-Math.PI * 0.38, Math.PI * 0.38];
  c.lineWidth = ESF * 0.06; c.strokeStyle = `rgba(${fundo},0.22)`;
  for (const [a0, a1] of [esq, dir]) { c.beginPath(); c.arc(0, 0, r, a0, a1); c.stroke(); }
  c.lineWidth = 1; c.strokeStyle = rgba(0.3);
  for (const [a0, a1] of [esq, dir]) { c.beginPath(); c.arc(0, 0, r + ESF * 0.04, a0, a1); c.stroke(); }
  const ext = Math.PI * 0.76 * Math.min(1, 0.04 + n * 1.3);
  c.lineWidth = 3; c.strokeStyle = rgba(0.7);
  c.beginPath(); c.arc(0, 0, r, dir[1] - ext, dir[1]); c.stroke();
  c.beginPath(); c.arc(0, 0, r, esq[0], esq[0] + ext); c.stroke();
  // um círculo enorme e quase apagado, que sai da tela
  c.lineWidth = 1; c.strokeStyle = rgba(0.1);
  c.beginPath(); c.arc(0, 0, ESF * 2.15, 0, TAU); c.stroke();
  c.restore();
}

// emissor na "mesa" sob a esfera: a esfera é projetada por um feixe de luz, como os hologramas do laboratório
const faiscas = Array.from({ length: 28 }, (_, i) => ({ f: i / 28, x: Math.random() * 2 - 1, v: 0.18 + Math.random() * 0.25 }));
function desenharProjetor(cc, { H, cx, cy, ESF, n, dt, rgba, spr }) {
  const ey = Math.min(cy + ESF * 1.12, H - 70), rx = ESF * 0.78, ry = ESF * 0.1, topo = cy - ESF * 0.35;
  // feixe que se abre do emissor até a esfera
  const g = cc.createLinearGradient(0, ey, 0, topo);
  g.addColorStop(0, rgba(0.09 + n * 0.1)); g.addColorStop(0.45, rgba(0.025 + n * 0.03)); g.addColorStop(1, rgba(0));
  cc.fillStyle = g;
  cc.beginPath();
  cc.moveTo(cx - rx * 0.9, ey); cc.lineTo(cx - ESF * 1.05, topo); cc.lineTo(cx + ESF * 1.05, topo); cc.lineTo(cx + rx * 0.9, ey);
  cc.closePath(); cc.fill();
  // brilho do emissor
  cc.save(); cc.translate(cx, ey); cc.scale(1, ry / rx);
  const gg = cc.createRadialGradient(0, 0, 0, 0, 0, rx);
  gg.addColorStop(0, rgba(0.4 + n * 0.25)); gg.addColorStop(0.4, rgba(0.1)); gg.addColorStop(1, rgba(0));
  cc.fillStyle = gg; cc.beginPath(); cc.arc(0, 0, rx, 0, TAU); cc.fill();
  cc.restore();
  // anéis do emissor: os de dentro firmes, o de fora tracejado e girando
  cc.lineWidth = 1.2;
  for (const [k, a] of [[1, 0.65], [0.7, 0.45], [0.42, 0.85]]) {
    cc.strokeStyle = rgba(a); cc.beginPath(); cc.ellipse(cx, ey, rx * k, ry * k, 0, 0, TAU); cc.stroke();
  }
  cc.setLineDash([5, 9]); cc.lineDashOffset = reator.giro2 * 30; cc.lineWidth = 2; cc.strokeStyle = rgba(0.55);
  cc.beginPath(); cc.ellipse(cx, ey, rx * 1.22, ry * 1.22, 0, 0, TAU); cc.stroke();
  cc.setLineDash([]);
  // partículas subindo pelo feixe
  const subida = REDUZIR ? 0.05 : 1 + n * 3;
  for (const p of faiscas) {
    p.f = (p.f + dt * p.v * subida * 0.3) % 1;
    const f = p.f, y = ey - f * (ey - topo), larg = rx * 0.9 + (ESF * 1.05 - rx * 0.9) * f;
    const s = 3 + (1 - f) * 3;
    cc.globalAlpha = Math.sin(f * Math.PI) * 0.8;
    cc.drawImage(spr, cx + p.x * larg * 0.85 - s / 2, y - s / 2, s, s);
  }
  cc.globalAlpha = 1;
}

// órbitas achatadas em volta da esfera, cada uma com um satélite deixando rastro
function desenharOrbitas(cc, { cx, cy, ESF, n, rgba, spr }) {
  for (const [k, inc, vel, fase] of [[1.42, -0.17, 0.55, 0], [1.56, 0.11, -0.35, 2.1]]) {
    const rx = ESF * k, ry = rx * 0.2, a = reator.giro * vel + fase;
    cc.save(); cc.translate(cx, cy); cc.rotate(inc);
    cc.lineWidth = 1; cc.strokeStyle = rgba(0.26); cc.setLineDash([3, 7]);
    cc.beginPath(); cc.ellipse(0, 0, rx, ry, 0, 0, TAU); cc.stroke(); cc.setLineDash([]);
    cc.strokeStyle = rgba(0.8); cc.lineWidth = 1.6;
    cc.beginPath();
    if (vel > 0) cc.ellipse(0, 0, rx, ry, 0, a - 0.9, a); else cc.ellipse(0, 0, rx, ry, 0, a, a + 0.9);
    cc.stroke();
    const s = 12 + n * 12;
    cc.drawImage(spr, Math.cos(a) * rx - s / 2, Math.sin(a) * ry - s / 2, s, s);
    cc.restore();
  }
}

// a esfera é desenhada numa camada à parte; uma cópia reduzida e desfocada dela vira o brilho (bloom)
const cena = document.createElement("canvas"), borrao = document.createElement("canvas");
function desenharReator(dt) {
  const canvas = $("reator");
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5); // tela cheia: acima disso só gasta GPU
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !H) return;
  const pw = Math.round(W * dpr), ph = Math.round(H * dpr);
  if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
  if (cena.width !== pw || cena.height !== ph) { cena.width = pw; cena.height = ph; }
  const c = canvas.getContext("2d"), cc = cena.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
  cc.setTransform(dpr, 0, 0, dpr, 0, 0); cc.clearRect(0, 0, W, H);

  const e = estadoVisivel();
  // nível alvo: a voz do Jarvis quando fala, o microfone quando ouve; um "respirar" suave nos demais
  const respira = 0.06 + 0.04 * Math.sin(reator.t * 1.6);
  let alvo = respira;
  if (e === "falando") alvo = Math.max(respira, S.nivel.voz);
  else if (e === "ouvindo" || e === "aguardando") alvo = Math.max(respira, S.nivel.mic * 0.9);
  else if (e === "pensando" || e === "transcrevendo") alvo = 0.14 + 0.08 * Math.sin(reator.t * 5);
  else if (e === "confirmando") alvo = 0.12 + 0.1 * Math.sin(reator.t * 4);
  reator.nivel += (alvo - reator.nivel) * Math.min(1, dt * 14);
  S.historicoNiveis.push(reator.nivel); S.historicoNiveis.shift();

  const alvoCor = CORES[e] || CORES.aguardando;
  for (let i = 0; i < 3; i++) reator.cor[i] += (alvoCor[i] - reator.cor[i]) * Math.min(1, dt * 5);
  const cor = reator.cor.map(Math.round), [r, g, b] = cor;
  const rgba = (a) => `rgba(${r},${g},${b},${a})`;
  const fundo = AZUL_PROFUNDO.map((v, i) => Math.round(v * 0.65 + cor[i] * 0.35)).join(",");

  const n = reator.nivel;
  const vel = REDUZIR ? 0.08 : (e === "pensando" ? 2.2 : e === "falando" ? 0.9 : e === "mudo" ? 0.08 : 0.42);
  reator.giro += dt * vel; reator.giro2 -= dt * vel * 0.75;
  reator.onda += dt * (REDUZIR ? 0.3 : (e === "pensando" || e === "transcrevendo" ? 6 : 2.2) + n * 6);
  reator.fluxo += dt * (REDUZIR ? 0.15 : (1 + n * 3) * (e === "pensando" ? 2 : 1));

  // a esfera ocupa a tela: centrada um pouco acima do meio, deixando a conversa embaixo
  const cx = W / 2, cy = H * 0.45, ESF = Math.min(W * 0.33, H * 0.4);
  if (ESF < 40) return;
  const X = {
    c, W, H, cx, cy, R: ESF / 0.7, ESF, n, dt, t: reator.t, rgba, fundo,
    corV: `rgb(${r},${g},${b})`, corF: `rgb(${fundo})`, corC: `rgb(${cor.map((v) => Math.round(v + (255 - v) * 0.6)).join(",")})`,
    INCL: 0.38 + 0.05 * Math.sin(reator.t * 0.25), spr: brilho(r, g, b), sprC: brilho(225, 250, 255),
  };

  desenharAneis(X);

  cc.globalCompositeOperation = "lighter";
  desenharProjetor(cc, X);
  desenharCentro(cc, X);
  desenharFilamentos(cc, X);
  desenharFragmentos(cc, X);
  desenharOrbitas(cc, X);
  cc.globalCompositeOperation = "source-over";

  const bw = Math.max(1, Math.round(W / 3)), bh = Math.max(1, Math.round(H / 3));
  if (borrao.width !== bw || borrao.height !== bh) { borrao.width = bw; borrao.height = bh; }
  const bc = borrao.getContext("2d");
  bc.clearRect(0, 0, bw, bh);
  bc.filter = "blur(4px)"; bc.drawImage(cena, 0, 0, bw, bh); bc.filter = "none";

  c.globalCompositeOperation = "lighter";
  c.globalAlpha = Math.min(1, 0.55 + n * 0.45);
  c.drawImage(borrao, 0, 0, W, H); c.drawImage(borrao, 0, 0, W, H);
  c.globalAlpha = 1;
  c.drawImage(cena, 0, 0, W, H);
  c.globalCompositeOperation = "source-over";
}

/* ============================================================ espaço ao fundo */
// Nebulosa e estrelas distantes são pintadas uma vez numa camada fixa; por cima, a cada quadro,
// estrelas que cintilam e derivam devagar, poeira de luz subindo e a mesa holográfica em perspectiva.
const cosmos = { base: document.createElement("canvas"), W: 0, H: 0, estrelas: [], poeira: [], cadente: null, proxCadente: 6, grade: 0 };
function novaPoeira(espalhar) {
  const { W, H } = cosmos;
  return { x: Math.random() * W, y: espalhar ? Math.random() * H : H + 10, v: 6 + Math.random() * 16, d: (Math.random() - 0.5) * 6,
    s: 0.8 + Math.random() * 1.6, f: Math.random() * TAU, ouro: Math.random() < 0.18 };
}
function prepararCosmos() {
  const cv = $("cosmos"), W = innerWidth, H = innerHeight;
  if (!W || !H) return;
  cosmos.W = W; cosmos.H = H;
  cv.width = cosmos.base.width = W; cv.height = cosmos.base.height = H;
  const rnd = gerador(4242), c = cosmos.base.getContext("2d");
  let g = c.createRadialGradient(W * 0.5, H * 0.4, 0, W * 0.5, H * 0.4, Math.hypot(W, H) * 0.62);
  g.addColorStop(0, "#062038"); g.addColorStop(0.45, "#030d1c"); g.addColorStop(1, "#010308");
  c.fillStyle = g; c.fillRect(0, 0, W, H);
  // nebulosas: bolhas de luz sobrepostas (ciano, azul profundo e um toque âmbar, como o globo dourado)
  c.globalCompositeOperation = "lighter";
  const nuvens = [[0.16, 0.28, [20, 130, 180], 0.2], [0.84, 0.22, [40, 70, 190], 0.2], [0.5, 0.42, [30, 190, 255], 0.09],
    [0.78, 0.78, [255, 160, 60], 0.07], [0.24, 0.82, [20, 150, 175], 0.12], [0.62, 0.1, [90, 60, 190], 0.08]];
  const M = Math.max(W, H);
  for (const [x, y, [r, gg, b], a] of nuvens) {
    for (let i = 0; i < 12; i++) {
      const px = (x + (rnd() - 0.5) * 0.3) * W, py = (y + (rnd() - 0.5) * 0.25) * H, pr = M * (0.06 + rnd() * 0.16);
      const rg = c.createRadialGradient(px, py, 0, px, py, pr);
      rg.addColorStop(0, `rgba(${r},${gg},${b},${a * (0.3 + rnd() * 0.6)})`); rg.addColorStop(1, `rgba(${r},${gg},${b},0)`);
      c.fillStyle = rg; c.fillRect(px - pr, py - pr, pr * 2, pr * 2);
    }
  }
  // poeira estelar fixa
  for (let i = 0, n = Math.round((W * H) / 1500); i < n; i++) {
    const a = 0.08 + rnd() * rnd() * 0.75, s = rnd() < 0.96 ? 1 : 1.7;
    c.fillStyle = `rgba(${(190 + rnd() * 65) | 0},${(215 + rnd() * 40) | 0},255,${a})`;
    c.fillRect(rnd() * W, rnd() * H, s, s);
  }
  c.globalCompositeOperation = "source-over";
  const cores = [[230, 245, 255], [150, 230, 255], [255, 205, 140]];
  cosmos.estrelas = Array.from({ length: Math.round((W * H) / 8000) }, () => ({
    x: rnd() * W, y: rnd() * H, z: 0.2 + rnd() * 0.8, f: rnd() * TAU, cor: cores[rnd() < 0.6 ? 0 : rnd() < 0.7 ? 1 : 2] }));
  cosmos.poeira = Array.from({ length: Math.round(W / 22) }, () => novaPoeira(true));
}
let timerCosmos = null;
addEventListener("resize", () => { clearTimeout(timerCosmos); timerCosmos = setTimeout(prepararCosmos, 150); });

function desenharCosmos(dt, t) {
  const { W, H } = cosmos;
  if (!W) return;
  const c = $("cosmos").getContext("2d");
  c.globalCompositeOperation = "source-over"; c.globalAlpha = 1;
  c.drawImage(cosmos.base, 0, 0);
  c.globalCompositeOperation = "lighter";
  const lento = REDUZIR ? 0.1 : 1;

  // estrelas vivas: as mais próximas derivam mais rápido (paralaxe) e cintilam
  for (const s of cosmos.estrelas) {
    s.x -= s.z * 2.4 * dt * lento;
    if (s.x < -6) s.x += W + 12;
    const a = (0.35 + 0.65 * s.z) * (0.55 + 0.45 * Math.sin(t * (0.8 + s.z * 2) + s.f)), tam = 2 + s.z * 5;
    c.globalAlpha = a;
    c.drawImage(brilho(...s.cor), s.x - tam / 2, s.y - tam / 2, tam, tam);
  }

  // mesa holográfica: grade em perspectiva que corre devagar na direção de quem olha
  const y0 = H * 0.66, fundoMesa = H + 4, vx = W / 2;
  cosmos.grade = (cosmos.grade + dt * 0.18 * lento) % 1;
  const gv = c.createLinearGradient(0, y0, 0, fundoMesa);
  gv.addColorStop(0, "rgba(34,216,255,0)"); gv.addColorStop(1, "rgba(34,216,255,0.16)");
  c.globalAlpha = 1; c.strokeStyle = gv; c.lineWidth = 1;
  c.beginPath();
  for (let k = -18; k <= 18; k++) { c.moveTo(vx + k * W * 0.012, y0); c.lineTo(vx + k * W * 0.09, fundoMesa); }
  c.stroke();
  for (let i = 0; i < 14; i++) {
    const d = i + 1 - cosmos.grade, y = y0 + (fundoMesa - y0) / (1 + d * 0.55), q = (y - y0) / (fundoMesa - y0);
    c.globalAlpha = q * q * 0.5;
    c.strokeStyle = "rgba(34,216,255,0.32)";
    c.beginPath(); c.moveTo(0, y); c.lineTo(W, y); c.stroke();
  }
  // brilho onde a mesa encontra o horizonte
  const gh = c.createLinearGradient(0, y0 - 30, 0, y0 + 30);
  gh.addColorStop(0, "rgba(34,216,255,0)"); gh.addColorStop(0.5, "rgba(34,216,255,0.06)"); gh.addColorStop(1, "rgba(34,216,255,0)");
  c.globalAlpha = 1; c.fillStyle = gh; c.fillRect(0, y0 - 30, W, 60);

  // poeira de luz subindo, como as partículas soltas dos hologramas
  for (let i = 0; i < cosmos.poeira.length; i++) {
    const p = cosmos.poeira[i];
    p.y -= p.v * dt * lento; p.x += p.d * dt * lento;
    if (p.y < -10) { cosmos.poeira[i] = novaPoeira(false); continue; }
    c.globalAlpha = Math.min(1, (H - p.y) / (H * 0.3)) * (0.35 + 0.35 * Math.sin(t * 2 + p.f));
    c.fillStyle = p.ouro ? "rgb(255,190,100)" : "rgb(120,230,255)";
    c.fillRect(p.x, p.y, p.s, p.s);
  }

  // estrela cadente de vez em quando
  if (!REDUZIR) {
    cosmos.proxCadente -= dt;
    if (!cosmos.cadente && cosmos.proxCadente <= 0) {
      cosmos.cadente = { x: W * (0.2 + Math.random() * 0.7), y: H * Math.random() * 0.3, vida: 0, dx: -(0.5 + Math.random() * 0.4), dy: 0.35 };
      cosmos.proxCadente = 14 + Math.random() * 20;
    }
    const k = cosmos.cadente;
    if (k) {
      k.vida += dt;
      const v = 900, x = k.x + k.dx * v * k.vida, y = k.y + k.dy * v * k.vida, cauda = 120;
      const gc = c.createLinearGradient(x, y, x - k.dx * cauda, y - k.dy * cauda);
      gc.addColorStop(0, "rgba(220,250,255,0.9)"); gc.addColorStop(1, "rgba(120,220,255,0)");
      c.globalAlpha = Math.max(0, 1 - k.vida / 0.9); c.strokeStyle = gc; c.lineWidth = 1.5;
      c.beginPath(); c.moveTo(x, y); c.lineTo(x - k.dx * cauda, y - k.dy * cauda); c.stroke();
      if (k.vida > 0.9) cosmos.cadente = null;
    }
  }
  c.globalAlpha = 1; c.globalCompositeOperation = "source-over";
}

/* ============================================================ quadros */
let ultimo = performance.now(), acumLento = 0;
function quadro(agora) {
  const dt = Math.min(0.1, (agora - ultimo) / 1000);
  ultimo = agora;
  reator.t += dt;
  if (!document.hidden) {
    desenharReator(dt);
    for (const k of Object.keys(dials)) desenharDial(k, dt, reator.t);
    desenharOsc(reator.t);
    // o fundo é decoração: 30 quadros por segundo bastam e poupam a máquina (Whisper e a voz usam a GPU)
    acumLento += dt;
    if (acumLento >= 1 / 31) {
      desenharCosmos(acumLento, reator.t);
      acumLento = 0;
    }
  }
  requestAnimationFrame(quadro);
}

/* ============================================================ controles */
const DIAS = ["DOM", "SEG", "TER", "QUA", "QUI", "SEX", "SÁB"];
const MESES = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"];
function relogio() {
  const d = new Date(), dois = (v) => String(v).padStart(2, "0");
  $("relogio").textContent = d.toLocaleTimeString("pt-BR", { hour12: false });
  const doAno = Math.floor((d - new Date(d.getFullYear(), 0, 0)) / 86400000);
  $("data").textContent = `${DIAS[d.getDay()]} · ${dois(d.getDate())} ${MESES[d.getMonth()]} ${d.getFullYear()} · DIA ${String(doAno).padStart(3, "0")}`;
}
$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = $("entrada").value.trim();
  if (!t || !S.conectado) return;
  enviar({ tipo: "texto", texto: t });
  $("entrada").value = "";
});
$("btn-parar").addEventListener("click", () => enviar({ tipo: "parar" }));
$("btn-mudo").addEventListener("click", () => enviar({ tipo: "mudo", valor: !S.mudo }));
$("conf-sim").addEventListener("click", () => responderConfirmacao(true));
$("conf-nao").addEventListener("click", () => responderConfirmacao(false));
$("busca-memoria").addEventListener("input", renderMemoria);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (S.pendente != null) responderConfirmacao(false); else enviar({ tipo: "parar" });
  } else if (e.key === "/" && document.activeElement !== $("entrada") && document.activeElement !== $("busca-memoria")) {
    e.preventDefault(); $("entrada").focus();
  }
});

prepararCosmos();
relogio(); setInterval(relogio, 1000);
atualizarEstado();
requestAnimationFrame(quadro);
conectar();

