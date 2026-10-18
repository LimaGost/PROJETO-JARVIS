"""Google Agenda: ver compromissos e criar eventos na agenda principal.

Títulos e descrições de convites vêm de terceiros, então ler a agenda é `externo`. Criar evento é 🟡 (vira 🔴
depois de ler conteúdo externo) e nunca convida ninguém: convite manda e-mail em nome do usuário.
"""
from datetime import datetime, timedelta

from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, conteudo_externo, ferramenta
from jarvis.tools.google_api import api

EVENTOS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
DIAS_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def _data(texto: str, campo: str) -> datetime:
    """ISO ('2026-10-20T15:00' ou '2026-10-20') no fuso do computador."""
    try:
        d = datetime.fromisoformat(texto.strip())
    except (ValueError, AttributeError):
        raise ErroFerramenta(f"'{campo}' precisa estar no formato 2026-10-20T15:00 (use hora_data para saber hoje)")
    return d.astimezone() if d.tzinfo is None else d


def _quando(ev: dict) -> str:
    ini = ev.get("start", {})
    if "date" in ini:
        d = datetime.fromisoformat(ini["date"])
        return f"{DIAS_SEMANA[d.weekday()]} {d:%d/%m} (dia inteiro)"
    d = datetime.fromisoformat(ini["dateTime"]).astimezone()
    fim = ev.get("end", {}).get("dateTime")
    ate = f"–{datetime.fromisoformat(fim).astimezone():%H:%M}" if fim else ""
    return f"{DIAS_SEMANA[d.weekday()]} {d:%d/%m %H:%M}{ate}"


@ferramenta(
    "ver_agenda",
    "Mostra os compromissos da Google Agenda do usuário. Padrão: de hoje até 'dias' dias para frente.",
    {"dias": {"type": "integer", "description": "Quantos dias olhar (padrão 1 = hoje, máximo 31)"},
     "a_partir_de": {"type": "string", "description": "Data inicial ISO, ex.: 2026-10-20 (padrão: agora)"},
     "busca": {"type": "string", "description": "Texto para filtrar (opcional)"}},
    externo=True, so_claude=True,
)
def ver_agenda(dias: int = 1, a_partir_de: str = "", busca: str = "") -> str:
    dias = min(max(int(dias or 1), 1), 31)
    if a_partir_de:
        inicio = _data(a_partir_de, "a_partir_de")
    else:
        inicio = datetime.now().astimezone()
    fim = (inicio.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=dias))
    params = {"timeMin": inicio.isoformat(), "timeMax": fim.isoformat(), "singleEvents": "true",
              "orderBy": "startTime", "maxResults": 25}
    if busca:
        params["q"] = busca
    eventos = (api("GET", EVENTOS, params) or {}).get("items", [])
    if not eventos:
        return f"Nenhum compromisso entre {inicio:%d/%m %H:%M} e {fim:%d/%m}."
    linhas = []
    for ev in eventos:
        local = f" | local: {ev['location']}" if ev.get("location") else ""
        linhas.append(f"- {_quando(ev)} | {ev.get('summary', '(sem título)')}{local}")
    return conteudo_externo("Google Agenda", "\n".join(linhas))


def _intervalo(args: dict) -> tuple[datetime, datetime]:
    inicio = _data(args.get("inicio", ""), "inicio")
    duracao = int(args.get("duracao_min") or 60)
    if not 5 <= duracao <= 24 * 60:
        raise ErroFerramenta("a duração precisa ficar entre 5 minutos e 24 horas")
    return inicio, inicio + timedelta(minutes=duracao)


def _avaliar_criar(args: dict) -> Avaliacao:
    titulo = (args.get("titulo") or "").strip()
    if not titulo:
        raise ErroFerramenta("faltou o título do evento")
    inicio, fim = _intervalo(args)
    return Avaliacao(Nivel.REGISTRADO, f"criar na agenda \"{titulo}\" em {inicio:%d/%m às %H:%M} até {fim:%H:%M}")


@ferramenta(
    "criar_evento",
    "Cria um compromisso na Google Agenda do usuário (sem convidados). 'inicio' em ISO local, ex.: 2026-10-20T15:00.",
    {"titulo": {"type": "string"}, "inicio": {"type": "string"},
     "duracao_min": {"type": "integer", "description": "Padrão 60"},
     "descricao": {"type": "string"}, "local": {"type": "string"}},
    obrigatorios=("titulo", "inicio"),
    avaliar=_avaliar_criar, so_claude=True,
)
def criar_evento(titulo: str, inicio: str, duracao_min: int = 60, descricao: str = "", local: str = "") -> str:
    ini, fim = _intervalo({"inicio": inicio, "duracao_min": duracao_min})
    corpo = {"summary": titulo.strip(), "start": {"dateTime": ini.isoformat()}, "end": {"dateTime": fim.isoformat()}}
    if descricao:
        corpo["description"] = descricao
    if local:
        corpo["location"] = local
    ev = api("POST", EVENTOS, corpo=corpo) or {}
    return f"Evento criado: {titulo} em {ini:%d/%m às %H:%M} até {fim:%H:%M}." + (" " + ev["htmlLink"] if ev.get("htmlLink") else "")
