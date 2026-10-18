"""Gmail: ler a caixa, abrir um e-mail e enviar/responder.

Segurança: e-mail é texto de terceiros (pode trazer "apague tal pasta"), então ler é `externo` e o que vem
depois passa a pedir confirmação. Enviar é sempre 🔴, com destinatário, assunto e começo do texto na tela.
Não há ferramenta de apagar: o login nem pede essa permissão.
"""
import base64
import html
import re
from email.message import EmailMessage
from email.utils import parseaddr

from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, conteudo_externo, ferramenta
from jarvis.tools.google_api import api, um_contato

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_CORPO = 3000


def _cabecalhos(msg: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _decodificar(dados: str) -> str:
    return base64.urlsafe_b64decode(dados + "=" * (-len(dados) % 4)).decode("utf-8", errors="replace")


def _sem_html(texto: str) -> str:
    texto = re.sub(r"(?is)<(script|style).*?</\1>", " ", texto)
    texto = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", texto)
    return html.unescape(re.sub(r"<[^>]+>", " ", texto))


def texto_do_email(payload: dict) -> str:
    """Prefere text/plain; senão o HTML sem as tags. Percorre partes aninhadas (multipart/alternative...)."""
    planos, htmls = [], []

    def visitar(parte: dict):
        tipo, dados = parte.get("mimeType", ""), parte.get("body", {}).get("data")
        if dados and tipo == "text/plain":
            planos.append(_decodificar(dados))
        elif dados and tipo == "text/html":
            htmls.append(_sem_html(_decodificar(dados)))
        for p in parte.get("parts", []) or []:
            visitar(p)

    visitar(payload)
    texto = "\n".join(planos) if planos else "\n".join(htmls)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", texto)).strip()


@ferramenta(
    "ler_emails",
    "Lista e-mails do Gmail do usuário (remetente, assunto, data, trecho e id). Use 'busca' com a sintaxe do Gmail: "
    "'is:unread', 'from:fulano', 'assunto', 'newer_than:2d'. Vazio = os mais recentes da caixa de entrada.",
    {"busca": {"type": "string"}, "quantos": {"type": "integer", "description": "Padrão 5, máximo 15"}},
    externo=True, so_claude=True,
)
def ler_emails(busca: str = "", quantos: int = 5) -> str:
    quantos = min(max(int(quantos or 5), 1), 15)
    r = api("GET", f"{GMAIL}/messages", {"q": busca or "in:inbox", "maxResults": quantos}) or {}
    ids = [m["id"] for m in r.get("messages", [])]
    if not ids:
        return f"Nenhum e-mail encontrado para '{busca or 'caixa de entrada'}'."
    linhas = []
    for id_ in ids:
        m = api("GET", f"{GMAIL}/messages/{id_}",
                {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        c = _cabecalhos(m)
        lido = "" if "UNREAD" in m.get("labelIds", []) else " (lido)"
        linhas.append(f"- id {id_}{lido} | de: {c.get('from', '?')} | assunto: {c.get('subject', '(sem assunto)')} | "
                      f"{c.get('date', '')} | {html.unescape(m.get('snippet', ''))[:160]}")
    return conteudo_externo("Gmail", "\n".join(linhas))


@ferramenta(
    "abrir_email",
    "Lê o texto completo de um e-mail pelo id que veio em ler_emails.",
    {"id": {"type": "string"}},
    obrigatorios=("id",),
    externo=True, so_claude=True,
)
def abrir_email(id: str) -> str:
    m = api("GET", f"{GMAIL}/messages/{id.strip()}", {"format": "full"})
    c = _cabecalhos(m)
    corpo = texto_do_email(m.get("payload", {}))
    if len(corpo) > MAX_CORPO:
        corpo = corpo[:MAX_CORPO] + "\n[...cortado]"
    return conteudo_externo("Gmail", f"De: {c.get('from', '?')}\nPara: {c.get('to', '?')}\nData: {c.get('date', '')}\n"
                                     f"Assunto: {c.get('subject', '')}\n\n{corpo}")


def _destinatario(para: str) -> tuple[str, str]:
    """(como mostrar, endereço). Aceita e-mail ou nome de um contato do Google."""
    para = para.strip()
    _, endereco = parseaddr(para)
    if re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", endereco or ""):
        return endereco, endereco
    if "@" in para:
        raise ErroFerramenta(f"'{para}' não parece um e-mail válido")
    nome, endereco = um_contato(para, "emails")
    return f"{nome} <{endereco}>", endereco


def _original(responder_a: str) -> dict:
    m = api("GET", f"{GMAIL}/messages/{responder_a.strip()}",
            {"format": "metadata", "metadataHeaders": ["From", "Reply-To", "Subject", "Message-ID", "References"]})
    return {"thread": m.get("threadId"), **_cabecalhos(m)}


def _avaliar_enviar(args: dict) -> Avaliacao:
    corpo = (args.get("corpo") or "").strip()
    if not corpo:
        raise ErroFerramenta("o e-mail está sem texto")
    if args.get("responder_a"):
        o = _original(args["responder_a"])
        mostrar = args.get("para") or o.get("reply-to") or o.get("from", "?")
        assunto = args.get("assunto") or o.get("subject", "")
        tipo = "responder por e-mail a"
    else:
        if not args.get("para"):
            raise ErroFerramenta("faltou o destinatário")
        mostrar, _ = _destinatario(args["para"])
        assunto = args.get("assunto", "")
        tipo = "enviar um e-mail para"
    trecho = corpo if len(corpo) <= 120 else corpo[:120] + "…"
    return Avaliacao(Nivel.CONFIRMAR, f"{tipo} {mostrar}, assunto \"{assunto or '(sem assunto)'}\": \"{trecho}\"")


@ferramenta(
    "enviar_email",
    "Envia um e-mail pelo Gmail do usuário. Sempre pede confirmação. Para responder um e-mail recebido, passe "
    "'responder_a' com o id dele (o destinatário e o assunto vêm do original). 'para' aceita e-mail ou nome de contato.",
    {"para": {"type": "string"}, "assunto": {"type": "string"}, "corpo": {"type": "string"},
     "responder_a": {"type": "string", "description": "id do e-mail a responder (opcional)"}},
    obrigatorios=("corpo",),
    avaliar=_avaliar_enviar, so_claude=True,
)
def enviar_email(corpo: str, para: str = "", assunto: str = "", responder_a: str = "") -> str:
    msg = EmailMessage()
    corpo_api: dict = {}
    if responder_a:
        o = _original(responder_a)
        endereco = _destinatario(para)[1] if para else parseaddr(o.get("reply-to") or o.get("from", ""))[1]
        assunto = assunto or o.get("subject", "")
        if not assunto.lower().startswith("re:"):
            assunto = "Re: " + assunto
        if o.get("message-id"):
            msg["In-Reply-To"] = o["message-id"]
            msg["References"] = (o.get("references", "") + " " + o["message-id"]).strip()
        if o.get("thread"):
            corpo_api["threadId"] = o["thread"]
    else:
        endereco = _destinatario(para)[1]
    if not endereco:
        raise ErroFerramenta("não consegui descobrir o destinatário")
    msg["To"] = endereco
    msg["Subject"] = assunto
    msg.set_content(corpo)
    corpo_api["raw"] = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    enviado = api("POST", f"{GMAIL}/messages/send", corpo=corpo_api) or {}
    return f"E-mail enviado para {endereco} (assunto: {assunto or 'sem assunto'}, id {enviado.get('id', '?')})."
