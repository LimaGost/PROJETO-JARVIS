"""Motor de automações: Gatilho → Condições → Ações → Resultado.

Fluxo (JSON guardado no banco):
    {"gatilho":   {"tipo": "horario", "repetir": "nunca|diario|semanal|mensal|intervalo", "quando": "2026-10-20T09:00",
                   "hora": "08:00", "dias": [0..6 (0=segunda)], "dia": 1, "a_cada_min": 30}
                | {"tipo": "evento", "evento": "whatsapp.mensagem|gmail.novo|agenda.proxima|webhook", "minutos_antes": 15}
                | {"tipo": "manual"},
     "condicoes": [{"campo": "mensagem.texto", "op": "contem|nao_contem|igual|comeca|regex|de|ia", "valor": "..."}],
     "modo_condicoes": "todas|qualquer",
     "acoes": [{"tipo": "avisar|criar_tarefa|ler_emails|ver_agenda|ia|whatsapp|email|criar_evento",
                "params": {...}, "salvar_como": "resumo", "paralelo": false, "tentativas": 1,
                "se_falhar": "parar|continuar"}]}

Textos aceitam variáveis: {data} {hora} {amanha} {mensagem.texto} {mensagem.nome} {evento.titulo} {resultado.resumo}...

Envio (WhatsApp/e-mail) — escolha do Iury, "aprovar ao criar":
  - o fluxo é aprovado uma vez (o hash dele fica guardado). Mudou qualquer coisa, o envio fica bloqueado até aprovar de
    novo;
  - "eu" (o próprio Iury) recebe qualquer conteúdo, inclusive resumos da IA;
  - terceiros: só o número/e-mail aprovado, ou responder quem mandou a mensagem ({mensagem.de}), e só com texto FIXO
    (variáveis seguras: data, hora, dados do evento da agenda). Nada gerado por IA nem copiado do que chegou: um
    e-mail malicioso não consegue usar o Jarvis para mandar mensagem a ninguém.
"""
import hashlib
import json
import re
import threading
import time
import traceback
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from jarvis.connect.banco import Banco

TICK_S = 15
POLL_S = 120
MAX_ATRASO = timedelta(hours=12)      # horário perdido (PC desligado) há mais que isso: não roda, só reagenda
EVENTOS = ("whatsapp.mensagem", "gmail.novo", "agenda.proxima", "webhook")
REPETICOES = ("nunca", "diario", "semanal", "mensal", "intervalo")
OPERADORES = ("contem", "nao_contem", "igual", "comeca", "regex", "de", "ia")
VARIAVEIS_SEGURAS = {"data", "hora", "amanha", "evento.titulo", "evento.inicio", "evento.hora", "evento.local",
                     "evento.minutos"}
DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
_VAR = re.compile(r"\{([a-z_][\w.]*)\}")


# ------------------------------------------------------------------ horários
def _hora(texto: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2})(?:(?::|h)(\d{2})?)?\s*", str(texto or "08:00"))   # 08:00, 8h, 8h30, 8
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        raise ValueError(f"hora inválida: {texto!r} (use 08:00)")
    return int(m.group(1)), int(m.group(2) or 0)


def proxima(gatilho: dict, depois: datetime) -> datetime | None:
    """Próximo disparo estritamente depois de `depois`. None = não dispara mais."""
    rep = gatilho.get("repetir", "nunca")
    if rep == "nunca":
        quando = datetime.fromisoformat(str(gatilho["quando"]))
        return quando if quando > depois else None
    if rep == "intervalo":
        return depois + timedelta(minutes=max(5, int(gatilho.get("a_cada_min") or 60)))
    h, m = _hora(gatilho.get("hora"))
    cand = depois.replace(hour=h, minute=m, second=0, microsecond=0)
    if cand <= depois:
        cand += timedelta(days=1)
    if rep == "diario":
        return cand
    if rep == "semanal":
        dias = {int(d) for d in (gatilho.get("dias") or [0])}
        for _ in range(8):
            if cand.weekday() in dias:
                return cand
            cand += timedelta(days=1)
        raise ValueError("dias da semana inválidos")
    if rep == "mensal":
        dia = min(max(int(gatilho.get("dia") or 1), 1), 28)
        cand = cand.replace(day=dia)
        while cand <= depois:
            cand = (cand.replace(day=1) + timedelta(days=32)).replace(day=dia)
        return cand
    raise ValueError(f"repetição desconhecida: {rep}")


def descrever_gatilho(g: dict) -> str:
    t = g.get("tipo")
    if t == "manual":
        return "quando você mandar rodar"
    if t == "evento":
        ev = g.get("evento")
        return {"whatsapp.mensagem": "quando chegar mensagem no WhatsApp", "gmail.novo": "quando chegar e-mail novo",
                "agenda.proxima": f"{g.get('minutos_antes', 15)} min antes de cada compromisso",
                "webhook": "quando chegar um webhook"}.get(ev, str(ev))
    rep = g.get("repetir", "nunca")
    if rep == "nunca":
        return f"uma vez, em {datetime.fromisoformat(str(g['quando'])):%d/%m às %H:%M}"
    if rep == "intervalo":
        return f"a cada {g.get('a_cada_min', 60)} min"
    hora = "%02d:%02d" % _hora(g.get("hora"))
    if rep == "diario":
        return f"todo dia às {hora}"
    if rep == "semanal":
        return "toda " + ", ".join(DIAS[int(d)] for d in sorted(g.get("dias") or [0])) + f" às {hora}"
    return f"todo dia {g.get('dia', 1)} do mês às {hora}"


# ------------------------------------------------------------------ variáveis e condições
def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t).lower()) if not unicodedata.combining(c))


def pegar(ctx: dict, caminho: str):
    atual = ctx
    for parte in caminho.split("."):
        if isinstance(atual, dict) and parte in atual:
            atual = atual[parte]
        else:
            return None
    return atual


def render(valor, ctx: dict):
    if isinstance(valor, str):
        def trocar(m):
            v = pegar(ctx, m.group(1))
            return m.group(0) if v is None else (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
        return _VAR.sub(trocar, valor)
    if isinstance(valor, dict):
        return {k: render(v, ctx) for k, v in valor.items()}
    if isinstance(valor, list):
        return [render(v, ctx) for v in valor]
    return valor


def variaveis(texto) -> set[str]:
    return set(_VAR.findall(texto)) if isinstance(texto, str) else set()


def contexto_base(agora: datetime | None = None) -> dict:
    agora = agora or datetime.now()
    return {"data": agora.strftime("%d/%m/%Y"), "hora": agora.strftime("%H:%M"),
            "amanha": (agora + timedelta(days=1)).strftime("%d/%m/%Y"), "resultado": {}}


def condicao_ok(c: dict, ctx: dict, pensar: Callable | None = None) -> bool:
    op, alvo = c.get("op", "contem"), str(c.get("valor", ""))
    valor = pegar(ctx, c.get("campo", "")) if c.get("campo") else None
    texto = "" if valor is None else (json.dumps(valor, ensure_ascii=False) if isinstance(valor, (dict, list)) else str(valor))
    if op in ("contem", "nao_contem"):
        palavras = [_sem_acento(p.strip()) for p in alvo.split(",") if p.strip()]
        achou = any(p in _sem_acento(texto) for p in palavras)
        return achou if op == "contem" else not achou
    if op == "igual":
        return _sem_acento(texto.strip()) == _sem_acento(alvo.strip())
    if op == "comeca":
        return _sem_acento(texto).startswith(_sem_acento(alvo))
    if op == "regex":
        return bool(re.search(alvo[:200], texto[:5000], flags=re.I))
    if op == "de":
        quem = _sem_acento(str(pegar(ctx, "mensagem.de") or "") + " " + str(pegar(ctx, "mensagem.nome") or "")
                           + " " + str(pegar(ctx, "email.de") or ""))
        return any(_sem_acento(p.strip()) in quem for p in alvo.split(",") if p.strip())
    if op == "ia":
        if pensar is None:
            return False
        resposta = pensar(f"Responda só SIM ou NÃO. {alvo}", texto or json.dumps(ctx, ensure_ascii=False)[:8000])
        return _sem_acento(resposta).strip().startswith("sim")
    raise ValueError(f"operador desconhecido: {op}")


def _baratas_primeiro(conds: list[dict]) -> list[dict]:
    return sorted(conds, key=lambda c: c.get("op") == "ia")


def condicoes_ok(fluxo: dict, ctx: dict, pensar=None, so_baratas: bool = False) -> bool:
    conds = [c for c in fluxo.get("condicoes") or [] if not (so_baratas and c.get("op") == "ia")]
    if not conds:
        return True
    if (fluxo.get("modo_condicoes") or "todas") == "qualquer":
        if so_baratas and len(conds) < len(fluxo.get("condicoes") or []):
            return True          # há uma condição de IA que pode aprovar: decide na execução
        return any(condicao_ok(c, ctx, pensar) for c in _baratas_primeiro(conds))
    return all(condicao_ok(c, ctx, pensar) for c in _baratas_primeiro(conds))


# ------------------------------------------------------------------ ações
@dataclass
class TipoAcao:
    nome: str
    descricao: str
    envia: str = ""            # "whatsapp" | "email": manda algo para alguém
    efeito: bool = False       # muda algo (no teste, só descreve)
    campos: tuple = ()


TIPOS_ACAO = {
    "avisar": TipoAcao("Avisar você", "fala e mostra na HUD", efeito=True, campos=("texto",)),
    "criar_tarefa": TipoAcao("Criar tarefa", "anota no motor de tarefas", efeito=True,
                             campos=("titulo", "descricao", "prioridade")),
    "ler_emails": TipoAcao("Ler e-mails", "busca no Gmail (só leitura)", campos=("busca", "quantos")),
    "ver_agenda": TipoAcao("Ver agenda", "compromissos dos próximos dias", campos=("dias",)),
    "ia": TipoAcao("IA analisa", "resume, classifica ou extrai dados", campos=("pedido", "contexto", "json")),
    "whatsapp": TipoAcao("Enviar WhatsApp", "manda pela Evolution", envia="whatsapp", efeito=True,
                         campos=("para", "texto")),
    "email": TipoAcao("Enviar e-mail", "manda pelo Gmail", envia="email", efeito=True,
                      campos=("para", "assunto", "texto")),
    "criar_evento": TipoAcao("Criar evento", "na sua Google Agenda", efeito=True,
                             campos=("titulo", "inicio", "duracao_min")),
}


def hash_fluxo(fluxo: dict) -> str:
    return hashlib.sha256(json.dumps(fluxo, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _eu(para) -> bool:
    return _sem_acento(str(para or "")).strip() in ("eu", "mim", "para mim", "me")


def analisar_envios(fluxo: dict) -> list[dict]:
    """Os envios do fluxo, já conferidos pela política. Erro = fluxo recusado (com o motivo)."""
    envios = []
    gat = fluxo.get("gatilho") or {}
    for i, a in enumerate(fluxo.get("acoes") or []):
        tipo = TIPOS_ACAO.get(a.get("tipo"))
        if not tipo or not tipo.envia:
            continue
        p = a.get("params") or {}
        para, texto = str(p.get("para") or "").strip(), str(p.get("texto") or "")
        if not para or not texto.strip():
            raise ValueError(f"ação {i + 1}: falta o destinatário ou o texto")
        if _eu(para):
            envios.append({"acao": i, "canal": tipo.envia, "para": "eu", "texto": texto, "terceiro": False})
            continue
        if para == "{mensagem.de}":
            if not (gat.get("tipo") == "evento" and gat.get("evento") == "whatsapp.mensagem" and tipo.envia == "whatsapp"):
                raise ValueError(f"ação {i + 1}: responder quem mandou só vale para mensagem de WhatsApp")
        elif variaveis(para):
            raise ValueError(f"ação {i + 1}: o destinatário precisa ser fixo (um contato aprovado), não uma variável")
        inseguras = variaveis(texto) - VARIAVEIS_SEGURAS
        if inseguras or variaveis(p.get("assunto")) - VARIAVEIS_SEGURAS:
            raise ValueError(f"ação {i + 1}: para outra pessoa o texto precisa ser fixo; tire "
                             + ", ".join("{" + v + "}" for v in sorted(inseguras or variaveis(p.get('assunto'))))
                             + " (conteúdo de IA ou de mensagem recebida só pode ir para você)")
        envios.append({"acao": i, "canal": tipo.envia, "para": para, "texto": texto, "terceiro": True})
    return envios


def validar_fluxo(fluxo: dict) -> dict:
    """Confere a estrutura e devolve o fluxo normalizado. ValueError com o motivo em português."""
    if not isinstance(fluxo, dict):
        raise ValueError("o fluxo precisa ser um objeto")
    g = dict(fluxo.get("gatilho") or {})
    t = g.get("tipo")
    if t == "horario":
        g.setdefault("repetir", "nunca")
        if g["repetir"] not in REPETICOES:
            raise ValueError(f"repetição inválida: {g['repetir']}")
        if g["repetir"] == "nunca":
            try:
                datetime.fromisoformat(str(g.get("quando")))
            except ValueError:
                raise ValueError("diga quando (data e hora, ex.: 2026-10-20T09:00)")
        elif g["repetir"] != "intervalo":
            _hora(g.get("hora"))
        if g["repetir"] == "semanal" and not all(str(d).isdigit() and 0 <= int(d) <= 6 for d in g.get("dias") or [0]):
            raise ValueError("dias da semana vão de 0 (segunda) a 6 (domingo)")
    elif t == "evento":
        if g.get("evento") not in EVENTOS:
            raise ValueError(f"evento desconhecido: {g.get('evento')} (opções: {', '.join(EVENTOS)})")
    elif t != "manual":
        raise ValueError("gatilho precisa ser horario, evento ou manual")
    conds = []
    for c in fluxo.get("condicoes") or []:
        if c.get("op") not in OPERADORES:
            raise ValueError(f"operador inválido: {c.get('op')} (opções: {', '.join(OPERADORES)})")
        if c.get("op") == "regex":
            try:
                re.compile(str(c.get("valor", "")))
            except re.error:
                raise ValueError("a expressão regular da condição está inválida")
        conds.append({"campo": str(c.get("campo", "")), "op": c["op"], "valor": str(c.get("valor", ""))})
    acoes = []
    for i, a in enumerate(fluxo.get("acoes") or []):
        if a.get("tipo") not in TIPOS_ACAO:
            raise ValueError(f"ação {i + 1}: tipo desconhecido {a.get('tipo')!r} (opções: {', '.join(TIPOS_ACAO)})")
        acoes.append({"tipo": a["tipo"], "params": dict(a.get("params") or {}),
                      "salvar_como": re.sub(r"\W", "", str(a.get("salvar_como") or ""))[:30],
                      "paralelo": bool(a.get("paralelo")), "tentativas": min(max(int(a.get("tentativas") or 1), 1), 5),
                      "se_falhar": "continuar" if a.get("se_falhar") == "continuar" else "parar"})
    if not acoes:
        raise ValueError("a automação precisa de pelo menos uma ação")
    novo = {"gatilho": g, "condicoes": conds, "modo_condicoes": "qualquer" if fluxo.get("modo_condicoes") == "qualquer"
            else "todas", "acoes": acoes}
    analisar_envios(novo)
    return novo


def resumo_aprovacao(nome: str, fluxo: dict) -> str:
    """O que a pessoa aprova, em português: quando roda e para quem manda o quê."""
    partes = [f"criar a automação '{nome}' ({descrever_gatilho(fluxo['gatilho'])})"]
    for e in analisar_envios(fluxo):
        canal = "WhatsApp" if e["canal"] == "whatsapp" else "e-mail"
        quem = "você mesmo" if e["para"] == "eu" else ("quem mandou a mensagem" if e["para"] == "{mensagem.de}"
                                                        else e["para"])
        texto = e["texto"] if len(e["texto"]) <= 140 else e["texto"][:140] + "…"
        partes.append(f"que manda {canal} para {quem}: \"{texto}\"")
    return ", ".join(partes)


# ------------------------------------------------------------------ o motor
class Motor:
    def __init__(self, banco: Banco, servicos: dict | None = None, cfg: dict | None = None,
                 relogio: Callable[[], datetime] = datetime.now, emitir: Callable | None = None):
        """servicos: funções que falam com o mundo (trocáveis nos testes). Faltando uma, a ação dá erro explicado."""
        self.banco, self.cfg, self.relogio = banco, cfg or {}, relogio
        self.s = servicos or {}
        self.emitir = emitir or (lambda **k: None)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="connect")
        for f in [self._pool.submit(time.sleep, 0.02) for _ in range(2)]:   # threads já na partida (DLLs no Windows)
            f.result()
        self._parar = threading.Event()
        self._vistos_email: set[str] | None = None
        self._ultimo_poll = 0.0

    # ------------------------------------------------------------ criar / aprovar
    def criar(self, nome: str, fluxo: dict, descricao: str = "", aprovado: bool = False, ativa: bool = True) -> dict:
        fluxo = validar_fluxo(fluxo)
        aprovacao = {"hash": hash_fluxo(fluxo), "quando": self.relogio().isoformat(timespec="seconds")} if aprovado else None
        auto = self.banco.criar_automacao(nome, fluxo, descricao, aprovacao, ativa, self._proxima(fluxo))
        self.banco.evento("automacao", f"automação criada: {nome}" + (" (aprovada)" if aprovado else ""))
        self._avisar_hud()
        return auto

    def atualizar(self, id_: int, nome: str, fluxo: dict, descricao: str = "") -> dict:
        fluxo = validar_fluxo(fluxo)
        atual = self.banco.automacao(id_)
        if atual is None:
            raise ValueError("automação não encontrada")
        aprov = atual["aprovacao"] if atual["aprovacao"] and atual["aprovacao"].get("hash") == hash_fluxo(fluxo) else None
        auto = self.banco.mudar_automacao(id_, nome=nome, descricao=descricao, fluxo=fluxo, aprovacao=aprov,
                                          proxima=self._proxima(fluxo))
        self.banco.evento("automacao", f"automação editada: {nome}" + ("" if aprov or not analisar_envios(fluxo)
                                                                         else " (envio precisa de nova aprovação)"))
        self._avisar_hud()
        return auto

    def aprovar(self, id_: int, hash_visto: str) -> dict:
        """A pessoa viu o resumo e aprovou: só vale se o fluxo é o mesmo que ela viu."""
        auto = self.banco.automacao(id_)
        if auto is None or hash_fluxo(auto["fluxo"]) != hash_visto:
            raise ValueError("a automação mudou depois que você viu; confira de novo")
        auto = self.banco.mudar_automacao(id_, aprovacao={"hash": hash_visto,
                                                          "quando": self.relogio().isoformat(timespec="seconds")})
        self.banco.evento("aprovacao", f"envios aprovados: {auto['nome']}")
        self._avisar_hud()
        return auto

    def precisa_aprovar(self, auto: dict) -> bool:
        return bool(analisar_envios(auto["fluxo"])) and not self._aprovada(auto)

    @staticmethod
    def _aprovada(auto: dict) -> bool:
        return bool(auto.get("aprovacao")) and auto["aprovacao"].get("hash") == hash_fluxo(auto["fluxo"])

    def ativar(self, id_: int, ativa: bool) -> dict:
        auto = self.banco.automacao(id_)
        if auto is None:
            raise ValueError("automação não encontrada")
        auto = self.banco.mudar_automacao(id_, ativa=ativa, proxima=self._proxima(auto["fluxo"]) if ativa else None)
        self.banco.evento("automacao", f"automação {'ligada' if ativa else 'pausada'}: {auto['nome']}")
        self._avisar_hud()
        return auto

    def _proxima(self, fluxo: dict) -> str | None:
        g = fluxo["gatilho"]
        if g.get("tipo") != "horario":
            return None
        p = proxima(g, self.relogio() - timedelta(seconds=1)) if g.get("repetir") == "nunca" else proxima(g, self.relogio())
        return p.isoformat(timespec="seconds") if p else None

    # ------------------------------------------------------------ disparos
    def tick(self):
        """Uma volta do agendador: automações de horário vencidas e lembretes de tarefas."""
        agora = self.relogio()
        for auto in self.banco.automacoes(so_ativas=True):
            if auto["fluxo"]["gatilho"].get("tipo") != "horario" or not auto["proxima"]:
                continue
            quando = datetime.fromisoformat(auto["proxima"])
            if quando > agora:
                continue
            g = auto["fluxo"]["gatilho"]
            seguinte = proxima(g, agora) if g.get("repetir") != "nunca" else None
            self.banco.mudar_automacao(auto["id"], proxima=seguinte.isoformat(timespec="seconds") if seguinte else None,
                                       **({} if seguinte or g.get("repetir") != "nunca" else {"ativa": False}))
            if agora - quando > MAX_ATRASO:
                self.banco.evento("automacao", f"horário perdido (Jarvis desligado): {auto['nome']}", nivel="alerta")
                continue
            self.enfileirar(auto, contexto_base(agora), f"{auto['id']}:horario:{auto['proxima']}")
        for t in self.banco.tarefas_vencidas(agora.isoformat(timespec="seconds")):
            self.banco.marcar_lembrada(t["id"])
            self._anunciar(f"Lembrete: {t['titulo']}" + (f". {t['descricao']}" if t["descricao"] else ""),
                           f"Lembrete, senhor: {t['titulo']}.")
            self.banco.evento("tarefa", f"lembrete: {t['titulo']}")

    def publicar(self, evento: str, dados: dict, id_evento: str):
        """Um acontecimento externo (mensagem, e-mail, reunião chegando). Dispara as automações que combinam."""
        chave_ctx = {"whatsapp.mensagem": "mensagem", "gmail.novo": "email", "agenda.proxima": "evento"}.get(evento,
                                                                                                           "webhook")
        ctx = {**contexto_base(self.relogio()), chave_ctx: dados}
        disparou = 0
        for auto in self.banco.automacoes(so_ativas=True):
            g = auto["fluxo"]["gatilho"]
            if g.get("tipo") != "evento" or g.get("evento") != evento:
                continue
            if evento == "agenda.proxima" and int(dados.get("minutos", 0)) > int(g.get("minutos_antes") or 15):
                continue
            try:
                if not condicoes_ok(auto["fluxo"], ctx, so_baratas=True):
                    continue
            except Exception:
                continue
            self.enfileirar(auto, ctx, f"{auto['id']}:{evento}:{id_evento}")
            disparou += 1
        return disparou

    def rodar_agora(self, id_: int) -> int | None:
        auto = self.banco.automacao(id_)
        if auto is None:
            raise ValueError("automação não encontrada")
        return self.executar(auto, contexto_base(self.relogio()), f"{id_}:manual:{time.time()}")

    def enfileirar(self, auto: dict, ctx: dict, chave: str):
        self._pool.submit(self._executar_seguro, auto, ctx, chave)

    def _executar_seguro(self, auto, ctx, chave):
        try:
            self.executar(auto, ctx, chave)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------ execução
    def executar(self, auto: dict, ctx: dict, chave: str, teste: bool = False) -> int | None:
        """Roda a automação. Devolve o id da execução (None se essa mesma já tinha rodado: não duplica)."""
        exec_id = self.banco.abrir_execucao(auto["id"], chave, teste)
        if exec_id is None:
            return None
        self._avisar_hud()
        passos, status, resultado = [], "ok", ""
        ctx = {**ctx, "resultado": dict(ctx.get("resultado") or {})}
        try:
            if not condicoes_ok(auto["fluxo"], ctx, self._pensar):
                self.banco.fechar_execucao(exec_id, "ignorada", [{"passo": "condições", "status": "não atendidas"}],
                                           "condições não atendidas")
                self._avisar_hud()
                return exec_id
            passos.append({"passo": "condições", "status": "ok"})
            for grupo in self._grupos(auto["fluxo"]["acoes"]):
                if len(grupo) == 1:
                    feitos = [self._rodar_acao(auto, grupo[0], ctx, teste)]
                else:
                    feitos = list(self._pool_paralelo(auto, grupo, ctx, teste))
                for (i, acao), (ok, saida) in zip(grupo, feitos):
                    passos.append({"passo": TIPOS_ACAO[acao["tipo"]].nome, "acao": i, "status": "ok" if ok else "erro",
                                   "saida": str(saida)[:1500]})
                    if ok and acao.get("salvar_como"):
                        ctx["resultado"][acao["salvar_como"]] = saida
                    resultado = str(saida)[:500] if ok else resultado
                    if not ok and acao.get("se_falhar") != "continuar":
                        status = "erro"
                if status == "erro":
                    break
        except Exception as e:
            status = "erro"
            passos.append({"passo": "motor", "status": "erro", "saida": f"{type(e).__name__}: {e}"[:500]})
        self.banco.fechar_execucao(exec_id, status, passos, resultado if status == "ok" else passos[-1].get("saida", ""))
        if not teste:
            self.banco.evento("execucao", f"{auto['nome']}: {'concluída' if status == 'ok' else 'falhou'}",
                              nivel="info" if status == "ok" else "erro")
            if status != "ok":
                self._anunciar(f"A automação '{auto['nome']}' falhou: {passos[-1].get('saida', '')[:200]}",
                               f"Senhor, a automação {auto['nome']} falhou.")
        self._avisar_hud()
        return exec_id

    @staticmethod
    def _grupos(acoes: list[dict]) -> list[list[tuple[int, dict]]]:
        grupos: list[list[tuple[int, dict]]] = []
        for i, a in enumerate(acoes):
            if a.get("paralelo") and grupos:
                grupos[-1].append((i, a))
            else:
                grupos.append([(i, a)])
        return grupos

    def _pool_paralelo(self, auto, grupo, ctx, teste):
        with ThreadPoolExecutor(max_workers=len(grupo)) as pool:
            futuros = [pool.submit(self._rodar_acao, auto, (i, a), ctx, teste) for i, a in grupo]
            return [f.result() for f in futuros]

    def _rodar_acao(self, auto: dict, item: tuple[int, dict], ctx: dict, teste: bool) -> tuple[bool, str]:
        i, acao = item
        tipo = TIPOS_ACAO[acao["tipo"]]
        params = render(acao.get("params") or {}, ctx)
        if tipo.envia:
            bloqueio = self._conferir_envio(auto, i, acao, params, ctx)
            if bloqueio and teste:          # no teste mostra o que mandaria e avisa o que falta para valer
                return True, f"(teste) {self._descrever(acao['tipo'], params)} — atenção, {bloqueio}"
            if bloqueio:
                return False, bloqueio
        if teste and tipo.efeito:
            return True, "(teste) " + self._descrever(acao["tipo"], params)
        erro = ""
        for tentativa in range(1, acao.get("tentativas", 1) + 1):
            try:
                return True, self._fazer(acao["tipo"], params, auto)
            except Exception as e:
                erro = str(e)[:500] or type(e).__name__
                if tentativa < acao.get("tentativas", 1):
                    time.sleep(min(2 ** tentativa, 20))
        return False, erro

    def _conferir_envio(self, auto, i, acao, params, ctx) -> str:
        """Política na hora de mandar (a da criação pode ter sido burlada editando o banco à mão)."""
        if not self._aprovada(auto):
            return "envio bloqueado: a automação ainda não foi aprovada (ou mudou depois da aprovação)"
        bruto = str((acao.get("params") or {}).get("para") or "")
        if _eu(bruto):
            return ""
        if bruto == "{mensagem.de}":
            if params.get("para") != str(pegar(ctx, "mensagem.de") or "") or not params.get("para"):
                return "envio bloqueado: não sei quem mandou a mensagem"
        elif variaveis(bruto):
            return "envio bloqueado: destinatário variável"
        if variaveis(str((acao.get("params") or {}).get("texto") or "")) - VARIAVEIS_SEGURAS:
            return "envio bloqueado: texto variável para outra pessoa"
        return ""

    def _descrever(self, tipo: str, p: dict) -> str:
        if tipo == "whatsapp":
            return f"enviaria WhatsApp para {p.get('para')}: {str(p.get('texto'))[:200]}"
        if tipo == "email":
            return f"enviaria e-mail para {p.get('para')} ({p.get('assunto', '')}): {str(p.get('texto'))[:200]}"
        if tipo == "criar_tarefa":
            return f"criaria a tarefa '{p.get('titulo')}'"
        if tipo == "avisar":
            return f"avisaria: {str(p.get('texto'))[:300]}"
        if tipo == "criar_evento":
            return f"criaria o evento '{p.get('titulo')}' em {p.get('inicio')}"
        return tipo

    def _servico(self, nome: str):
        f = self.s.get(nome)
        if f is None:
            raise RuntimeError(f"o serviço '{nome}' não está disponível")
        return f

    def _pensar(self, pedido: str, contexto: str = "") -> str:
        return self._servico("pensar")(pedido, contexto)

    def _anunciar(self, texto: str, falado: str = ""):
        f = self.s.get("anunciar")
        if f:
            try:
                f(texto, falado)
            except Exception:
                pass

    def _fazer(self, tipo: str, p: dict, auto: dict) -> str:
        if tipo == "avisar":
            texto = str(p.get("texto") or "").strip()
            self._anunciar(f"[automação {auto['nome']}] {texto}", texto)
            return texto
        if tipo == "criar_tarefa":
            t = self.banco.criar_tarefa(str(p.get("titulo") or "Tarefa"), str(p.get("descricao") or ""),
                                        str(p.get("prioridade") or "normal"), p.get("quando") or None,
                                        origem="automacao", automacao_id=auto["id"])
            self._avisar_hud()
            return f"tarefa {t['id']} criada: {t['titulo']}"
        if tipo == "ler_emails":
            return self._servico("ler_emails")(str(p.get("busca") or "is:unread newer_than:1d"), int(p.get("quantos") or 10))
        if tipo == "ver_agenda":
            return self._servico("ver_agenda")(int(p.get("dias") or 1))
        if tipo == "ia":
            resposta = self._pensar(str(p.get("pedido") or "Resuma."), str(p.get("contexto") or ""))
            if str(p.get("json", "")).lower() in ("1", "true", "sim"):
                from jarvis.connect.ia import extrair_json
                return extrair_json(resposta)
            return resposta
        if tipo == "whatsapp":
            para = self.meu("whatsapp") if _eu(p.get("para")) else str(p.get("para"))
            return self._servico("enviar_whatsapp")(para, str(p.get("texto")))
        if tipo == "email":
            para = self.meu("email") if _eu(p.get("para")) else str(p.get("para"))
            return self._servico("enviar_email")(para, str(p.get("assunto") or "Jarvis"), str(p.get("texto")))
        if tipo == "criar_evento":
            return self._servico("criar_evento")(str(p.get("titulo")), str(p.get("inicio")), int(p.get("duracao_min") or 60))
        raise ValueError(tipo)

    def meu(self, canal: str) -> str:
        c = self.cfg.get("connect", {}) or {}
        valor = c.get("meu_whatsapp" if canal == "whatsapp" else "meu_email")
        if not valor:
            raise RuntimeError(f"configure connect.{'meu_whatsapp' if canal == 'whatsapp' else 'meu_email'} no config.yaml")
        return str(valor)

    # ------------------------------------------------------------ teste antes de ativar
    def testar(self, id_: int, exemplo: dict | None = None) -> dict:
        """Roda de mentira: lê de verdade (e-mails, agenda, IA), mas envio/criação só são descritos."""
        auto = self.banco.automacao(id_)
        if auto is None:
            raise ValueError("automação não encontrada")
        ctx = {**contexto_base(self.relogio()), **(exemplo or self.exemplo(auto["fluxo"]))}
        exec_id = self.executar(auto, ctx, f"{id_}:teste:{time.time()}", teste=True)
        return self.banco.execucoes(id_, 1)[0] if exec_id else {}

    @staticmethod
    def exemplo(fluxo: dict) -> dict:
        ev = (fluxo.get("gatilho") or {}).get("evento")
        if ev == "whatsapp.mensagem":
            return {"mensagem": {"de": "5511999990000", "nome": "Cliente teste", "texto": "Olá! Pode me mandar um "
                                 "orçamento? Podemos conversar amanhã às 15h?", "grupo": False}}
        if ev == "gmail.novo":
            return {"email": {"de": "cliente@exemplo.com", "assunto": "Orçamento", "trecho": "Bom dia, segue o pedido."}}
        if ev == "agenda.proxima":
            return {"evento": {"titulo": "Reunião de implantação", "inicio": "hoje 15:00", "hora": "15:00",
                               "local": "Meet", "minutos": 15}}
        return {}

    # ------------------------------------------------------------ vigias (Gmail e Agenda)
    def precisa(self, evento: str) -> bool:
        return any(a["fluxo"]["gatilho"].get("evento") == evento for a in self.banco.automacoes(so_ativas=True))

    def vigiar(self):
        """Gmail e Agenda não têm webhook simples: consulta a cada 2 min, só se alguma automação precisa."""
        if self.precisa("gmail.novo") and "emails_novos" in self.s:
            try:
                novos = self.s["emails_novos"]()
                ids = {e["id"] for e in novos}
                if self._vistos_email is None:          # 1ª consulta: só marca o que já existia, não dispara
                    self._vistos_email = ids
                else:
                    for e in novos:
                        if e["id"] not in self._vistos_email:
                            self.publicar("gmail.novo", e, e["id"])
                    self._vistos_email |= ids
                self.banco.sincronizou("gmail", True)
            except Exception as e:
                self.banco.sincronizou("gmail", False, str(e))
        if self.precisa("agenda.proxima") and "eventos_proximos" in self.s:
            try:
                for ev in self.s["eventos_proximos"](60):
                    self.publicar("agenda.proxima", ev, f"{ev['id']}:{ev['inicio']}")
                self.banco.sincronizou("agenda", True)
            except Exception as e:
                self.banco.sincronizou("agenda", False, str(e))

    # ------------------------------------------------------------ ciclo de vida
    def iniciar(self):
        def laco():
            while not self._parar.wait(TICK_S):
                try:
                    self.tick()
                    if time.monotonic() - self._ultimo_poll >= POLL_S:
                        self._ultimo_poll = time.monotonic()
                        self.vigiar()
                except Exception:
                    traceback.print_exc()
        threading.Thread(target=laco, daemon=True, name="connect-motor").start()
        global _motor
        _motor = self

    def parar(self):
        self._parar.set()

    def _avisar_hud(self):
        try:
            self.emitir(tipo="connect")
        except Exception:
            pass


_motor: Motor | None = None


def atual() -> Motor | None:
    return _motor


def ativo() -> bool:
    return _motor is not None and not _motor._parar.is_set()
