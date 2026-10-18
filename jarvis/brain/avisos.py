"""Novidades que aconteceram fora de um pedido (ex.: uma tarefa de programação terminou em segundo plano).

Quem publica: as ferramentas de trabalho longo. Quem consome: o cérebro, no próximo pedido, para saber do que aconteceu
(e o Controle, para falar na hora). Assim o Jarvis "lembra" do que fez sozinho, como um só.
"""
import threading
from typing import Callable

_lock = threading.Lock()
_pendentes: list[str] = []
_ao_publicar: list[Callable[[str], None]] = []


def publicar(texto: str, falado: str = ""):
    """`texto` vai para o cérebro no próximo pedido; `falado` (curto, para a voz) vai para quem anuncia."""
    with _lock:
        _pendentes.append(texto)
        ouvintes = list(_ao_publicar)
    for ouvir in ouvintes:
        try:
            ouvir(falado or texto)
        except Exception:
            pass   # quem anuncia (voz/HUD) nunca derruba quem publicou


def consumir() -> list[str]:
    with _lock:
        saida = list(_pendentes)
        _pendentes.clear()
    return saida


def ouvir(funcao: Callable[[str], None]):
    with _lock:
        _ao_publicar.append(funcao)


def limpar():
    with _lock:
        _pendentes.clear()
        _ao_publicar.clear()
