"""O que o cérebro usa do Connect: tarefas e automações por linguagem natural, integrações e conversas do WhatsApp.

"Todos os dias às 8h, resuma meus e-mails importantes" -> o Claude monta o fluxo (gatilho/condições/ações) e chama
criar_automacao. O CÓDIGO confere o fluxo e decide o risco: automação que envia algo é 🔴 e a confirmação mostra
destinatário e texto exatos; aprovada, ela roda sozinha (escolha do Iury: "aprovar ao criar").
"""
import json

from jarvis.connect import motor as m
from jarvis.hud.bus import bus
from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, conteudo_externo, ferramenta


def _motor() -> m.Motor:
    atual = m.atual()
    if atual is None:
        raise ErroFerramenta("o JARVIS Connect não está ligado (connect.ativo no config.yaml)")
    return atual


def _quando(texto: str) -> str | None:
    if not texto:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(texto.strip()).isoformat(timespec="minutes")
    except ValueError:
        raise ErroFerramenta("o horário da tarefa precisa vir como data ISO (ex.: 2026-10-20T09:00)")


# ------------------------------------------------------------------ tarefas
@ferramenta(
    "criar_tarefa",
    "Anota uma tarefa/lembrete no motor de tarefas do Jarvis. Com 'quando' (ISO), ele avisa na hora.",
    {"titulo": {"type": "string"}, "descricao": {"type": "string"},
     "prioridade": {"type": "string", "enum": ["baixa", "normal", "alta", "urgente"]},
     "quando": {"type": "string", "description": "Data e hora ISO, ex.: 2026-10-20T09:00 (opcional)"}},
    obrigatorios=("titulo",), nivel=Nivel.REGISTRADO,
)
def criar_tarefa(titulo: str, descricao: str = "", prioridade: str = "normal", quando: str = "") -> str:
    t = _motor().banco.criar_tarefa(titulo, descricao, prioridade or "normal", _quando(quando))
    _motor()._avisar_hud()
    return f"Tarefa {t['id']} anotada: {t['titulo']}" + (f" (lembro em {t['quando']})" if t["quando"] else "")


@ferramenta(
    "listar_tarefas",
    "Lista as tarefas do motor de tarefas (pendentes por padrão).",
    {"status": {"type": "string", "enum": ["pendente", "concluida", "todas"]}},
)
def listar_tarefas(status: str = "pendente") -> str:
    ts = _motor().banco.tarefas(status or "pendente", 40)
    if not ts:
        return "Nenhuma tarefa " + ("pendente." if status in ("", "pendente", None) else "aqui.")
    return "\n".join(f"- [{t['id']}] {t['titulo']} ({t['prioridade']}"
                     + (f", {t['quando'].replace('T', ' ')}" if t["quando"] else "") + f", {t['status']})"
                     for t in ts)


@ferramenta(
    "concluir_tarefa",
    "Marca uma tarefa como concluída (pelo id de listar_tarefas).",
    {"id": {"type": "integer"}}, obrigatorios=("id",), nivel=Nivel.REGISTRADO,
)
def concluir_tarefa(id: int) -> str:
    t = _motor().banco.mudar_tarefa(int(id), status="concluida")
    if not t:
        raise ErroFerramenta(f"não há tarefa {id}")
    _motor().banco.evento("tarefa", f"tarefa concluída: {t['titulo']}")
    _motor()._avisar_hud()
    return f"Concluída: {t['titulo']}."


# ------------------------------------------------------------------ automações
_ESQUEMA_FLUXO = (
    "gatilho: {tipo:'horario', repetir:'nunca'|'diario'|'semanal'|'mensal'|'intervalo', quando:'2026-10-20T09:00' "
    "(só 'nunca'), hora:'08:00', dias:[0..6, 0=segunda], dia:1-28 (mensal), a_cada_min} | {tipo:'evento', "
    "evento:'whatsapp.mensagem'|'gmail.novo'|'agenda.proxima'|'webhook', minutos_antes:15} | {tipo:'manual'}. "
    "condicoes: [{campo:'mensagem.texto'|'mensagem.nome'|'email.assunto'|'email.de'..., op:'contem'|'nao_contem'|"
    "'igual'|'comeca'|'regex'|'de'|'ia', valor}] ('contem' aceita palavras separadas por vírgula; 'ia' é uma pergunta "
    "de sim/não sobre o conteúdo). acoes: [{tipo, params, salvar_como, paralelo, tentativas, se_falhar}] com tipo: "
    "avisar{texto} | criar_tarefa{titulo,descricao,prioridade} | ler_emails{busca,quantos} | ver_agenda{dias} | "
    "ia{pedido,contexto,json} | whatsapp{para,texto} | email{para,assunto,texto} | criar_evento{titulo,inicio,"
    "duracao_min}. Variáveis nos textos: {data} {hora} {amanha} {mensagem.texto} {mensagem.nome} {email.assunto} "
    "{evento.titulo} {evento.hora} {resultado.NOME} (o que uma ação salvou com salvar_como). ENVIO: para='eu' "
    "(o usuário) aceita qualquer texto, inclusive {resultado.x}; para outra pessoa (nome do contato, telefone ou "
    "e-mail, ou '{mensagem.de}' para responder quem mandou no WhatsApp) o texto precisa ser FIXO.")

_preparados: dict[str, tuple[str, dict]] = {}


def _preparar(args: dict) -> tuple[str, dict]:
    """Valida e troca nomes de contato pelo número/e-mail, para a confirmação mostrar exatamente quem recebe."""
    chave = json.dumps({"nome": args.get("nome"), "gatilho": args.get("gatilho"), "acoes": args.get("acoes"),
                        "condicoes": args.get("condicoes") or [], "modo": args.get("modo_condicoes") or "todas"},
                       sort_keys=True, ensure_ascii=False, default=str)
    if chave in _preparados:
        return _preparados[chave]
    nome = str(args.get("nome") or "").strip() or "Automação"
    fluxo = {"gatilho": args.get("gatilho") or {}, "condicoes": args.get("condicoes") or [],
             "modo_condicoes": args.get("modo_condicoes") or "todas", "acoes": args.get("acoes") or []}
    for k in ("gatilho", "condicoes", "acoes"):
        try:
            fluxo[k] = json.loads(fluxo[k]) if isinstance(fluxo[k], str) else json.loads(json.dumps(fluxo[k]))
        except (ValueError, TypeError):
            raise ErroFerramenta(f"'{k}' precisa ser JSON")
    for a in fluxo["acoes"] if isinstance(fluxo["acoes"], list) else []:
        p = a.get("params") or {}
        para = str(p.get("para") or "").strip()
        if not para or m._eu(para) or "{" in para:
            continue
        if a.get("tipo") == "whatsapp":
            from jarvis.tools.whatsapp import resolver
            p["para"] = resolver(para)[1]
        elif a.get("tipo") == "email":
            from jarvis.tools.gmail import _destinatario
            p["para"] = _destinatario(para)[1]
    try:
        fluxo = m.validar_fluxo(fluxo)
    except ValueError as e:
        raise ErroFerramenta(str(e))
    _preparados[chave] = (nome, fluxo)
    if len(_preparados) > 20:
        _preparados.pop(next(iter(_preparados)))
    return nome, fluxo


def _avaliar_criar(args: dict) -> Avaliacao:
    nome, fluxo = _preparar(args)
    nivel = Nivel.CONFIRMAR if m.analisar_envios(fluxo) else Nivel.REGISTRADO
    return Avaliacao(nivel, m.resumo_aprovacao(nome, fluxo))


@ferramenta(
    "criar_automacao",
    "Cria uma automação (Gatilho → Condições → Ações) a partir do pedido em linguagem natural, ex.: 'todo dia às 8h "
    "resuma meus e-mails', 'quando chegar mensagem com orçamento crie uma tarefa', 'amanhã às 9h mande pro João "
    "lembrando da reunião'. Formato: " + _ESQUEMA_FLUXO,
    {"nome": {"type": "string"}, "descricao": {"type": "string"},
     "gatilho": {"type": "object"}, "condicoes": {"type": "array", "items": {"type": "object"}},
     "modo_condicoes": {"type": "string", "enum": ["todas", "qualquer"]},
     "acoes": {"type": "array", "items": {"type": "object"}}},
    obrigatorios=("nome", "gatilho", "acoes"),
    avaliar=_avaliar_criar, so_claude=True,
)
def criar_automacao(nome: str, gatilho: dict, acoes: list, descricao: str = "", condicoes: list | None = None,
                    modo_condicoes: str = "todas") -> str:
    nome, fluxo = _preparar({"nome": nome, "gatilho": gatilho, "condicoes": condicoes,
                             "modo_condicoes": modo_condicoes, "acoes": acoes})   # o mesmo que foi confirmado
    # chegou aqui = o Executor já pediu o "sim" (se envia) ou registrou (se não envia): fica aprovada
    auto = _motor().criar(nome, fluxo, descricao, aprovado=True)
    quando = f" Próxima vez: {auto['proxima'].replace('T', ' ')}." if auto["proxima"] else ""
    return f"Automação {auto['id']} '{auto['nome']}' criada e ligada ({m.descrever_gatilho(fluxo['gatilho'])}).{quando}"


@ferramenta("listar_automacoes", "Lista as automações (id, nome, quando roda, ligada ou não, última execução).",
            so_claude=True)
def listar_automacoes() -> str:
    autos = _motor().banco.automacoes()
    if not autos:
        return "Nenhuma automação criada ainda."
    linhas = []
    for a in autos:
        estado = "ligada" if a["ativa"] else "pausada"
        if _motor().precisa_aprovar(a):
            estado += ", envio aguardando aprovação"
        ultima = f", última: {a['ultima']} ({a['ultimo_status']})" if a["ultima"] else ""
        linhas.append(f"- [{a['id']}] {a['nome']}: {m.descrever_gatilho(a['fluxo']['gatilho'])}, {estado}{ultima}")
    return "\n".join(linhas)


def _achar(id_) -> dict:
    a = _motor().banco.automacao(int(id_))
    if a is None:
        raise ErroFerramenta(f"não há automação {id_}")
    return a


@ferramenta(
    "ligar_automacao", "Liga ou pausa uma automação.",
    {"id": {"type": "integer"}, "ligar": {"type": "boolean"}}, obrigatorios=("id", "ligar"),
    avaliar=lambda a: Avaliacao(Nivel.REGISTRADO, f"{'ligar' if a.get('ligar') else 'pausar'} a automação "
                                                  f"'{_achar(a['id'])['nome']}'"),
    so_claude=True,
)
def ligar_automacao(id: int, ligar: bool) -> str:
    a = _motor().ativar(int(id), bool(ligar))
    return f"Automação '{a['nome']}' {'ligada' if a['ativa'] else 'pausada'}."


@ferramenta(
    "testar_automacao",
    "Testa uma automação sem efeitos: lê de verdade (e-mails, agenda, IA) mas só DESCREVE o que enviaria ou criaria.",
    {"id": {"type": "integer"}}, obrigatorios=("id",), externo=True, so_claude=True,
)
def testar_automacao(id: int) -> str:
    _achar(id)
    r = _motor().testar(int(id))
    passos = "\n".join(f"- {p['passo']}: {p['status']}" + (f" → {p.get('saida', '')[:300]}" if p.get("saida") else "")
                       for p in r.get("passos", []))
    return conteudo_externo("teste da automação", f"Resultado: {r.get('status')}\n{passos}")


@ferramenta(
    "rodar_automacao", "Roda uma automação agora (fora do horário).",
    {"id": {"type": "integer"}}, obrigatorios=("id",),
    avaliar=lambda a: Avaliacao(Nivel.REGISTRADO, f"rodar agora a automação '{_achar(a['id'])['nome']}'"),
    so_claude=True,
)
def rodar_automacao(id: int) -> str:
    _motor().rodar_agora(int(id))
    ex = _motor().banco.execucoes(int(id), 1)
    return f"Rodei: {ex[0]['status']}. {ex[0]['resultado'][:300]}" if ex else "Rodei."


@ferramenta(
    "apagar_automacao", "Apaga uma automação.", {"id": {"type": "integer"}}, obrigatorios=("id",),
    avaliar=lambda a: Avaliacao(Nivel.REGISTRADO, f"apagar a automação '{_achar(a['id'])['nome']}'"),
    so_claude=True,
)
def apagar_automacao(id: int) -> str:
    a = _achar(id)
    _motor().banco.apagar_automacao(int(id))
    _motor().banco.evento("automacao", f"automação apagada: {a['nome']}")
    _motor()._avisar_hud()
    return f"Apaguei a automação '{a['nome']}'."


# ------------------------------------------------------------------ integrações e WhatsApp
@ferramenta(
    "ver_integracoes",
    "Mostra quais integrações estão conectadas (Gmail, Agenda, WhatsApp/Evolution...) antes de planejar um pedido "
    "que usa várias. Também diz o que ainda não está implementado.",
    so_claude=True,
)
def ver_integracoes() -> str:
    from jarvis.connect.conectores import catalogo
    linhas = []
    for c in catalogo(_motor().banco):
        if c["situacao"] == "planejado":
            continue
        linhas.append(f"- {c['nome']}: {c['estado']}" + (f" ({c['detalhe']})" if c["detalhe"] else ""))
    planejados = [c["nome"] for c in catalogo() if c["situacao"] == "planejado"]
    return "\n".join(linhas) + "\nAinda não implementados: " + ", ".join(planejados)


@ferramenta(
    "whatsapp_conversas",
    "Lê as mensagens recentes do WhatsApp (pela Evolution): sem contato, lista as conversas; com contato (nome ou "
    "número), traz as últimas mensagens para resumir.",
    {"contato": {"type": "string"}, "quantas": {"type": "integer", "description": "Padrão 20"}},
    externo=True, so_claude=True,
)
def whatsapp_conversas(contato: str = "", quantas: int = 20) -> str:
    from jarvis.connect import evolution as evo
    from jarvis.connect.conectores import instancia
    c = instancia("evolution")
    if not c.configurado():
        raise ErroFerramenta("ler conversas precisa da Evolution API ligada (Central > Integrações > WhatsApp)")
    cli = c.cliente()
    if not contato:
        chats = cli.conversas()[:20]
        linhas = [f"- {ch.get('pushName') or ch.get('name') or ''} ({str(ch.get('remoteJid', '')).split('@')[0]})"
                  for ch in chats]
        return conteudo_externo("conversas do WhatsApp", "\n".join(linhas) or "nenhuma conversa")
    from jarvis.tools.whatsapp import resolver
    try:
        nome, numero = resolver(contato)
    except ErroFerramenta:
        achados = cli.contatos(contato)
        if not achados:
            raise
        nome, numero = contato, str(achados[0].get("remoteJid", "")).split("@")[0]
    msgs = cli.mensagens(f"{numero}@s.whatsapp.net", min(max(int(quantas or 20), 1), 50))
    linhas = []
    for msg in reversed(msgs):
        quem = "eu" if (msg.get("key") or {}).get("fromMe") else (msg.get("pushName") or nome)
        texto = evo.texto_da_mensagem(msg)
        if texto:
            linhas.append(f"{quem}: {texto}")
    return conteudo_externo(f"conversa com {nome}", "\n".join(linhas) or "sem mensagens de texto")


@ferramenta("abrir_central", "Abre a Central do Jarvis (integrações, tarefas, automações e painel) na HUD.",
            {"secao": {"type": "string", "enum": ["painel", "integracoes", "tarefas", "automacoes"]}})
def abrir_central(secao: str = "painel") -> str:
    bus.emitir("central_abrir", secao=secao or "painel")
    return "Central aberta na HUD."
