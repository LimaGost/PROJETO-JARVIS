"""Barramento de eventos: o Jarvis publica o que acontece, a HUD (e qualquer outro cliente) assina.

Sem assinantes, emitir custa quase nada, então o resto do código pode emitir sempre.
"""
import threading
import time
from collections import deque
from typing import Callable

# eventos que uma HUD recém-aberta precisa para reconstruir a conversa
TIPOS_NO_HISTORICO = {"usuario", "jarvis", "acao", "sistema", "fim_turno"}
MAX_HISTORICO = 120
MAX_TEXTO = 4000  # um resultado de ferramenta gigante não deve inchar a HUD


def _cortar(valor, limite=MAX_TEXTO):
    if isinstance(valor, str) and len(valor) > limite:
        return valor[:limite] + "…"
    if isinstance(valor, dict):
        return {k: _cortar(v, limite) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_cortar(v, limite) for v in valor[:50]]
    return valor


class Bus:
    def __init__(self):
        self._lock = threading.Lock()
        self._assinantes: list[Callable[[dict], None]] = []
        self._historico: deque = deque(maxlen=MAX_HISTORICO)
        self.estado = "iniciando"
        self.falando = False
        self.mudo = False
        self.conexoes = 0  # clientes da HUD conectados agora

    def assinar(self, callback: Callable[[dict], None]):
        with self._lock:
            self._assinantes.append(callback)

    def cancelar(self, callback):
        with self._lock:
            if callback in self._assinantes:
                self._assinantes.remove(callback)

    def emitir(self, tipo: str, **dados) -> dict:
        evento = {"tipo": tipo, "t": round(time.time(), 3), **_cortar(dados)}
        if tipo == "estado":
            self.estado = dados.get("estado", self.estado)
        elif tipo == "voz":
            self.falando = bool(dados.get("falando"))
        elif tipo == "mudo":
            self.mudo = bool(dados.get("valor"))
        with self._lock:
            if tipo in TIPOS_NO_HISTORICO:
                self._historico.append(evento)
            assinantes = list(self._assinantes)
        for cb in assinantes:
            try:
                cb(evento)
            except Exception:
                pass  # um cliente com problema não pode derrubar o Jarvis
        return evento

    def historico(self) -> list[dict]:
        with self._lock:
            return list(self._historico)

    def limpar(self):
        """Para testes."""
        with self._lock:
            self._assinantes.clear()
            self._historico.clear()
        self.estado, self.falando, self.mudo, self.conexoes = "iniciando", False, False, 0


bus = Bus()
