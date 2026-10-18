"""WhatsApp: mandar mensagem pelo app do WhatsApp para Windows.

O WhatsApp não tem API oficial para conta pessoal. O caminho seguro é o link `whatsapp://send?phone=...&text=...`:
o próprio app abre a conversa DAQUELE número com o texto já escrito, e o Jarvis só aperta Enter. Nada de procurar
o contato clicando pela tela (um clique errado manda para a pessoa errada).

Sempre 🔴: a tela mostra nome, número e a mensagem antes de você dizer sim. Nome vira número pelos contatos do Google.
Para LER conversas, o Claude usa ver_janela no WhatsApp aberto (marcado como texto de terceiros).
"""
import os
import re
import time
import urllib.parse

from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, ferramenta
from jarvis.tools.google_api import um_contato

DDI_PADRAO = "55"  # Brasil: número sem DDI ganha o 55
ESPERA_JANELA_S = 10
ESPERA_CONVERSA_S = 1.5


def normalizar_numero(texto: str) -> str | None:
    """'(11) 98765-4321' -> '5511987654321'. None se não parece telefone."""
    texto = (texto or "").strip()
    digitos = re.sub(r"\D", "", texto)
    com_ddi = texto.startswith(("+", "00"))
    if texto.startswith("00"):
        digitos = digitos[2:]
    if not com_ddi:
        if len(digitos) not in (10, 11):        # DDD + número; menos que isso falta o DDD
            return None
        digitos = DDI_PADRAO + digitos
    return digitos if 10 <= len(digitos) <= 15 else None


def resolver(contato: str) -> tuple[str, str]:
    """(nome para mostrar, número só com dígitos e DDI)."""
    numero = normalizar_numero(contato)
    if numero:
        return contato.strip(), numero
    if sum(c.isdigit() for c in contato) >= 6:
        raise ErroFerramenta(f"'{contato}' não parece um telefone completo (com DDD)")
    nome, telefone = um_contato(contato, "telefones")
    numero = normalizar_numero(telefone)
    if not numero:
        raise ErroFerramenta(f"o telefone de {nome} nos contatos ({telefone}) está incompleto")
    return nome, numero


def _avaliar(args: dict) -> Avaliacao:
    mensagem = (args.get("mensagem") or "").strip()
    if not mensagem:
        raise ErroFerramenta("a mensagem está vazia")
    nome, numero = resolver(args.get("contato", ""))
    trecho = mensagem if len(mensagem) <= 120 else mensagem[:120] + "…"
    return Avaliacao(Nivel.CONFIRMAR, f"mandar no WhatsApp para {nome} (+{numero}): \"{trecho}\"")


def _janela_do_whatsapp():
    from jarvis.tools import interface
    interface._exigir()
    auto = interface.auto
    limite = time.time() + ESPERA_JANELA_S
    while time.time() < limite:
        try:
            topo = auto.GetForegroundControl().GetTopLevelControl()
            if topo is not None and "whatsapp" in interface._processo(topo.ProcessId).lower():
                return auto, topo
        except Exception:
            pass
        time.sleep(0.3)
    return auto, None


@ferramenta(
    "whatsapp_enviar",
    "Manda uma mensagem no WhatsApp do usuário (app do Windows). 'contato' = nome de um contato do Google ou "
    "telefone com DDD. Sempre pede confirmação.",
    {"contato": {"type": "string"}, "mensagem": {"type": "string"}},
    obrigatorios=("contato", "mensagem"),
    avaliar=_avaliar, so_claude=True,
)
def whatsapp_enviar(contato: str, mensagem: str) -> str:
    if not hasattr(os, "startfile"):
        raise ErroFerramenta("o envio pelo WhatsApp só funciona no Windows")
    nome, numero = resolver(contato)
    url = "whatsapp://send?" + urllib.parse.urlencode({"phone": numero, "text": mensagem.strip()},
                                                      quote_via=urllib.parse.quote)
    try:
        os.startfile(url)
    except OSError:
        raise ErroFerramenta("não consegui abrir o WhatsApp; ele está instalado neste computador?")
    auto, janela = _janela_do_whatsapp()
    if janela is None:
        raise ErroFerramenta("abri o WhatsApp mas a janela dele não veio para a frente; a mensagem NÃO foi enviada")
    time.sleep(ESPERA_CONVERSA_S)  # a conversa e o texto pré-preenchido levam um instante para aparecer
    _, janela = _janela_do_whatsapp()
    if janela is None:
        raise ErroFerramenta("o WhatsApp saiu da frente antes do envio; a mensagem NÃO foi enviada")
    auto.SendKeys("{Enter}", waitTime=0.3)
    return (f"Mensagem enviada no WhatsApp para {nome} (+{numero}). Se a conversa não abriu com o texto, "
            "o Enter não manda nada: confira com ver_janela se precisar.")
