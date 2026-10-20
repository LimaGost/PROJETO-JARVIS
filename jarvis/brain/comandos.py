"""Comandos de controle do próprio Jarvis (desligar, esquecer a conversa).

O Whisper nem sempre escreve exatamente o que foi dito ('Esquisa convés'), então a
comparação é por similaridade, e só vale para frases curtas: 'como desligar o
computador?' é uma pergunta normal, não um comando.
"""
import re
import unicodedata
from difflib import SequenceMatcher

MAX_PALAVRAS = 4
SIMILARIDADE = 0.72

COMANDOS = {
    "desligar": ["tchau jarvis", "desligar jarvis", "encerrar jarvis", "pode dormir", "ate logo jarvis"],
    "esquecer": ["esqueca a conversa", "esquece a conversa", "limpar conversa", "nova conversa"],
}


def normalizar(texto: str) -> str:
    """minúsculas, sem acentos nem pontuação: 'Tchau, Jarvis!' -> 'tchau jarvis'"""
    sem_acento = unicodedata.normalize("NFD", texto.lower())
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", sem_acento).split())


PALAVRAS_SIM = {"sim", "confirmo", "confirma", "confirmado", "pode", "autorizo", "autorizado", "claro",
                "isso", "positivo", "manda", "faz", "faca", "ok", "okay", "certo", "beleza", "prossiga"}
PALAVRAS_NAO = {"nao", "cancela", "cancelar", "cancelado", "negativo", "espera", "nunca", "nem", "pare", "jamais"}


def sim_ou_nao(texto: str) -> bool:
    """Resposta a um pedido de confirmação. Na dúvida é NÃO: só aceita um sim claro e sem negação."""
    palavras = normalizar(texto).split()
    if not palavras or palavras == ["para"]:  # 'para' só é negação sozinho ('pode apagar para mim' é sim)
        return False
    if any(p in PALAVRAS_NAO for p in palavras):
        return False
    return any(p in PALAVRAS_SIM for p in palavras)


# Trocar de mentalidade por voz. O Whisper escreve "Qwen" de muitos jeitos (quen, queen, kuen...); "Claude",
# "ChatGPT" e "Gemini" também.
MAX_PALAVRAS_TROCA = 7
VERBOS_TROCA = {"usa", "usar", "use", "muda", "mudar", "troca", "trocar", "volta", "voltar", "ativa", "ativar", "coloca",
                "colocar", "passa", "passar", "modo", "mentalidade", "cerebro", "selecionar", "seleciona", "escolhe"}
ALVO_QWEN = ("qwen", "quen", "queen", "kuen", "cuen", "quein")
ALVO_CLAUDE = ("claude", "claudio", "clode", "cloud", "clod")
ALVO_LOCAL = {"local", "offline"}
ALVO_CHATGPT = ("chatgpt", "gpt", "chat", "codex", "chatgepete", "openai", "xatgpt", "chatgp")
ALVO_GEMINI = ("gemini", "jemini", "gemine", "geminai", "gemaini", "jeminai")
# modelos do Claude, do jeito que o Whisper costuma escrever
ALVO_MODELOS = {"opus": ("opus", "opos", "opuz", "hopus"),
                "sonnet": ("sonnet", "sonet", "soneto", "sonete", "sonnete", "sonit"),
                "haiku": ("haiku", "haicai", "haicu", "haico", "raicu", "aiku", "haikai")}


def destino_cerebro(texto: str) -> str | None:
    """'usa o claude' -> 'claude'; 'muda para o qwen' -> 'qwen'; 'usa o opus' -> 'opus'; senão None. Só frases
    curtas COM verbo de troca: 'o que é o claude?' é uma pergunta, não um comando."""
    palavras = normalizar(texto).split()
    if not palavras or len(palavras) > MAX_PALAVRAS_TROCA or not any(p in VERBOS_TROCA for p in palavras):
        return None
    for p in palavras:                  # o modelo vem antes: "usa o claude opus" é o Opus
        if p in VERBOS_TROCA or len(p) < 4:
            continue
        for modelo, grafias in ALVO_MODELOS.items():
            if any(SequenceMatcher(None, p, a).ratio() >= 0.8 for a in grafias):
                return modelo
    for p in palavras:
        if p == "gpt":
            return "chatgpt"
        if p in VERBOS_TROCA or len(p) < 4:
            continue
        if any(SequenceMatcher(None, p, a).ratio() >= 0.72 for a in ALVO_QWEN) or p in ALVO_LOCAL:
            return "qwen"
        if any(SequenceMatcher(None, p, a).ratio() >= 0.72 for a in ALVO_CLAUDE):
            return "claude"
        if p in ALVO_CHATGPT or any(SequenceMatcher(None, p, a).ratio() >= 0.8 for a in ALVO_CHATGPT if len(a) > 4):
            return "chatgpt"
        if any(SequenceMatcher(None, p, a).ratio() >= 0.75 for a in ALVO_GEMINI):
            return "gemini"
    return None


# "Fica quieto": ele para de falar e NÃO responde nada (nem "ok"). Vale no meio da fala dele ou num pedido normal.
SILENCIO = {
    "quieto", "fica quieto", "fique quieto", "fica quietinho", "silencio", "fica em silencio", "fique em silencio",
    "faz silencio", "cala a boca", "cala boca", "cala se", "calado", "fica calado", "fique calado", "para de falar",
    "pare de falar", "para", "pare", "parar", "pode parar", "chega", "ja chega", "shh", "psiu", "xiu", "chiu",
    "nao fala nada", "agora nao", "deixa", "deixa pra la", "deixa quieto", "esquece", "esquece isso", "cancela",
    "nao precisa", "ta bom chega", "para tudo", "para com isso", "silencio por favor", "mudo",
}
_ENCHIMENTO = {"jarvis", "por", "favor", "ai", "ok", "okay", "ta", "bom", "ei", "hey", "e", "o", "agora", "senhor", "pfv", "porfavor"}
_ENCHIMENTO_MANTER = {"ta bom chega", "agora nao", "silencio por favor"}  # enchimentos que fazem parte da frase


def pedido_de_silencio(texto: str) -> bool:
    """'Fica quieto, Jarvis!' / 'Para de falar.' / 'Shhh' -> True. 'Para onde vai o voo?' -> False (frase longa/pedido)."""
    t = re.sub(r"\b(s+h+|x+i+u+|p+s+i+u+)\b", lambda m: "shh" if m.group(0)[0] == "s" else ("xiu" if m.group(0)[0] == "x" else "psiu"),
               normalizar(texto))
    if not t or len(t.split()) > 6:
        return False
    if t in SILENCIO or t in _ENCHIMENTO_MANTER:
        return True
    nucleo = " ".join(p for p in t.split() if p not in _ENCHIMENTO)
    if not nucleo:
        return False
    if nucleo in SILENCIO:
        return True
    return len(nucleo.split()) >= 2 and any(SequenceMatcher(None, nucleo, f).ratio() >= 0.85 for f in SILENCIO if " " in f)


def identificar(texto: str) -> str | None:
    """'desligar' | 'esquecer' | 'calar' | 'trocar:qwen' | 'trocar:claude' | 'trocar:chatgpt' | 'trocar:gemini' |
    'trocar:opus' (sonnet, haiku) | None"""
    destino = destino_cerebro(texto)
    if destino:
        return f"trocar:{destino}"
    t = normalizar(texto)
    if not t:
        return None
    if pedido_de_silencio(texto):      # antes do "desligar": "para, Jarvis" é calar, não encerrar o programa
        return "calar"
    if len(t.split()) <= MAX_PALAVRAS:
        for nome, frases in COMANDOS.items():
            for frase in frases:
                if frase in t or SequenceMatcher(None, t, frase).ratio() >= SIMILARIDADE:
                    return nome
    return None
