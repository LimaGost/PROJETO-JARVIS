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


def identificar(texto: str) -> str | None:
    t = normalizar(texto)
    if not t or len(t.split()) > MAX_PALAVRAS:
        return None
    for nome, frases in COMANDOS.items():
        for frase in frases:
            if frase in t or SequenceMatcher(None, t, frase).ratio() >= SIMILARIDADE:
                return nome
    return None
