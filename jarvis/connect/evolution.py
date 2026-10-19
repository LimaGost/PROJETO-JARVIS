"""Cliente da Evolution API v2 (gratuita, código aberto): WhatsApp pelo SEU número, via QR code, rodando no seu PC
(Docker, pasta servicos/evolution). Conferido no código da v2.3.7.

    GET  /instance/connectionState/{inst}   estado: open | connecting | close
    GET  /instance/connect/{inst}           QR code (base64) para parear
    POST /instance/create                   cria a instância (chave global)
    POST /message/sendText/{inst}           {"number", "text"}
    POST /chat/findContacts|findChats|findMessages/{inst}
    POST /webhook/set/{inst}                {"webhook": {"enabled", "url", "headers", "events", ...}}
Cabeçalho de autenticação: apikey (a chave global do .env da Evolution, ou o token da instância).

Atenção (custo zero tem preço): a Evolution usa o protocolo do WhatsApp Web (Baileys), que não é oficial. Uso
pessoal e moderado é o normal; disparo em massa pode levar o WhatsApp a bloquear o número.
"""
from jarvis.connect import rede

PADRAO_URL = "http://127.0.0.1:8080"
PADRAO_INSTANCIA = "jarvis"
EVENTOS_WEBHOOK = ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]


class Evolution:
    def __init__(self, url: str = PADRAO_URL, apikey: str = "", instancia: str = PADRAO_INSTANCIA,
                 requisitar=rede.requisitar):
        self.url = (url or PADRAO_URL).rstrip("/")
        self.apikey = apikey
        self.instancia = instancia or PADRAO_INSTANCIA
        self._req = requisitar

    def _chamar(self, metodo: str, caminho: str, corpo=None, **opcoes):
        return self._req("evolution", metodo, f"{self.url}{caminho}", corpo, {"apikey": self.apikey}, **opcoes)

    # ------------------------------------------------------------ instância
    def estado(self) -> str:
        r = self._chamar("GET", f"/instance/connectionState/{self.instancia}", tentativas=1, timeout=4) or {}
        return str((r.get("instance") or {}).get("state") or r.get("state") or "desconhecido")

    def criar_instancia(self) -> dict:
        return self._chamar("POST", "/instance/create", {"instanceName": self.instancia, "qrcode": True,
                                                         "integration": "WHATSAPP-BAILEYS"}) or {}

    def qrcode(self) -> dict:
        """{'base64': 'data:image/png;base64,...', 'pairingCode': ...} ou {} se já está conectado."""
        r = self._chamar("GET", f"/instance/connect/{self.instancia}")
        if not isinstance(r, dict):
            return {}
        if r.get("error"):
            raise ValueError(str(r.get("message"))[:200])
        return r.get("qrcode") or r

    def configurar_webhook(self, url: str, segredo: str) -> dict:
        return self._chamar("POST", f"/webhook/set/{self.instancia}", {"webhook": {
            "enabled": True, "url": url, "headers": {"x-jarvis-segredo": segredo}, "byEvents": False,
            "base64": False, "events": EVENTOS_WEBHOOK}}) or {}

    # ------------------------------------------------------------ mensagens
    def enviar_texto(self, numero: str, texto: str) -> dict:
        return self._chamar("POST", f"/message/sendText/{self.instancia}", {"number": numero, "text": texto}) or {}

    def contatos(self, nome: str = "") -> list[dict]:
        r = self._chamar("POST", f"/chat/findContacts/{self.instancia}", {"where": {"pushName": nome}} if nome else {})
        return r if isinstance(r, list) else []

    def conversas(self) -> list[dict]:
        r = self._chamar("POST", f"/chat/findChats/{self.instancia}", {})
        return r if isinstance(r, list) else []

    def mensagens(self, jid: str, quantas: int = 20) -> list[dict]:
        r = self._chamar("POST", f"/chat/findMessages/{self.instancia}",
                         {"where": {"key": {"remoteJid": jid}}, "offset": quantas, "page": 1, "limit": quantas}) or {}
        registros = (r.get("messages") or {}).get("records") if isinstance(r, dict) else r
        return registros if isinstance(registros, list) else []


def texto_da_mensagem(dados: dict) -> str:
    """Texto de uma mensagem da Evolution (conversa, texto estendido ou legenda de mídia)."""
    msg = dados.get("message") or {}
    return (msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text")
            or (msg.get("imageMessage") or {}).get("caption") or (msg.get("videoMessage") or {}).get("caption")
            or (msg.get("documentMessage") or {}).get("caption") or "").strip()


def mensagem_do_webhook(corpo: dict) -> dict | None:
    """Evento messages.upsert -> {'id', 'de', 'nome', 'texto', 'grupo', 'quando'}; None se não for mensagem recebida."""
    if str(corpo.get("event", "")).lower().replace("_", ".") != "messages.upsert":
        return None
    dados = corpo.get("data") or {}
    if isinstance(dados, list):
        dados = dados[0] if dados else {}
    chave = dados.get("key") or {}
    if chave.get("fromMe"):
        return None                     # o que eu mesmo mandei não dispara automação
    jid = str(chave.get("remoteJid") or "")
    return {"id": str(chave.get("id") or ""), "de": jid.split("@")[0], "jid": jid, "grupo": jid.endswith("@g.us"),
            "nome": str(dados.get("pushName") or ""), "texto": texto_da_mensagem(dados)[:4000],
            "quando": dados.get("messageTimestamp")}
