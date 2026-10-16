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


def identificar(texto: str) -> str | None:
    t = normalizar(texto)
    if not t or len(t.split()) > MAX_PALAVRAS:
        return None
    for nome, frases in COMANDOS.items():
        for frase in frases:
            if frase in t or SequenceMatcher(None, t, frase).ratio() >= SIMILARIDADE:
                return nome
    return None
