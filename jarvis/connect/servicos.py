"""As funções reais que o motor usa para falar com o mundo (Gmail, Agenda, Evolution, IA, voz).
Separadas do motor para os testes trocarem por versões falsas."""
from datetime import datetime, timedelta

from jarvis.brain import avisos


def _evolution():
    from jarvis.connect.conectores import instancia
    c = instancia("evolution")
    if not c.configurado():
        raise RuntimeError("a Evolution API não está configurada (Central > Integrações > WhatsApp)")
    return c.cliente()


def enviar_whatsapp(numero: str, texto: str) -> str:
    from jarvis.tools.whatsapp import normalizar_numero
    alvo = normalizar_numero(numero) or numero
    _evolution().enviar_texto(alvo, texto)
    return f"WhatsApp enviado para +{alvo}"


def enviar_email(para: str, assunto: str, texto: str) -> str:
    from jarvis.tools import gmail
    return gmail.enviar_email(corpo=texto, para=para, assunto=assunto)


def ler_emails(busca: str, quantos: int) -> str:
    from jarvis.tools import gmail
    return gmail.ler_emails(busca=busca, quantos=quantos)


def ver_agenda(dias: int) -> str:
    from jarvis.tools import agenda
    return agenda.ver_agenda(dias=dias)


def criar_evento(titulo: str, inicio: str, duracao_min: int) -> str:
    from jarvis.tools import agenda
    return agenda.criar_evento(titulo=titulo, inicio=inicio, duracao_min=duracao_min)


def emails_novos() -> list[dict]:
    from jarvis.tools import gmail, google_api
    r = google_api.api("GET", f"{gmail.GMAIL}/messages", {"q": "in:inbox newer_than:1d", "maxResults": 15}) or {}
    saida = []
    for m in r.get("messages", []):
        msg = google_api.api("GET", f"{gmail.GMAIL}/messages/{m['id']}",
                             {"format": "metadata", "metadataHeaders": ["From", "Subject"]}) or {}
        cab = {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers", [])}
        saida.append({"id": m["id"], "de": cab.get("from", ""), "assunto": cab.get("subject", ""),
                      "trecho": msg.get("snippet", "")})
    return saida


def eventos_proximos(minutos: int) -> list[dict]:
    from jarvis.tools import agenda, google_api
    agora = datetime.now().astimezone()
    r = google_api.api("GET", agenda.EVENTOS, {"timeMin": agora.isoformat(),
                                               "timeMax": (agora + timedelta(minutes=minutos)).isoformat(),
                                               "singleEvents": "true", "orderBy": "startTime"}) or {}
    saida = []
    for ev in r.get("items", []):
        inicio = (ev.get("start") or {}).get("dateTime")
        if not inicio:
            continue                      # evento de dia inteiro não "chega" em minutos
        quando = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
        saida.append({"id": ev.get("id", ""), "titulo": ev.get("summary", "(sem título)"), "inicio": inicio,
                      "hora": quando.astimezone().strftime("%H:%M"), "local": ev.get("location", ""),
                      "minutos": max(0, int((quando - agora).total_seconds() // 60))})
    return saida


def anunciar(texto: str, falado: str = ""):
    avisos.publicar(texto, falado)


def todos(cfg: dict) -> dict:
    from jarvis.connect import ia
    return {"enviar_whatsapp": enviar_whatsapp, "enviar_email": enviar_email, "ler_emails": ler_emails,
            "ver_agenda": ver_agenda, "criar_evento": criar_evento, "emails_novos": emails_novos,
            "eventos_proximos": eventos_proximos, "anunciar": anunciar,
            "pensar": lambda pedido, contexto="": ia.pensar(pedido, contexto, cfg)}
