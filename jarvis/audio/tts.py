import os
import queue
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import piper
import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

from jarvis.config import caminho


def _dados_espeak() -> str:
    """O espeak-ng no Windows não lê caminhos com acento (ex.: PROGRAMAÇÃO).
    Nesse caso usamos uma cópia dos dados em um caminho ASCII."""
    original = Path(piper.__file__).parent / "espeak-ng-data"
    if str(original).isascii():
        return str(original)
    copia = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "jarvis_espeak"
    if not copia.exists():
        shutil.copytree(original, copia)
    return str(copia)


class Voz:
    """Fala em segundo plano: frases entram na fila e tocam em ordem."""

    def __init__(self, cfg: dict):
        self.voz = PiperVoice.load(caminho(cfg["tts"]["voz"]), espeak_data_dir=_dados_espeak())
        self.sintese = SynthesisConfig(length_scale=cfg["tts"]["velocidade"])
        self.saida = cfg["audio"]["saida"]
        self.fila: queue.Queue = queue.Queue()
        self._cond = threading.Condition()
        self._pendentes = 0  # frases na fila + a que está tocando
        self._geracao = 0    # calar() invalida tudo que foi enfileirado antes dele
        threading.Thread(target=self._loop, daemon=True).start()

    def falar(self, texto: str):
        texto = texto.strip()
        if not texto:
            return
        with self._cond:
            self._pendentes += 1
            self.fila.put((self._geracao, texto))

    def esta_falando(self) -> bool:
        return self._pendentes > 0

    def esperar(self):
        """Espera terminar de falar + uma guarda para a reverberação não virar 'fala do usuário'."""
        with self._cond:
            self._cond.wait_for(lambda: self._pendentes == 0)
        time.sleep(0.2)

    def calar(self):
        """Interrompe a fala atual e descarta o que estava na fila. Falas novas continuam valendo."""
        with self._cond:
            self._geracao += 1
        sd.stop()

    def _loop(self):
        while True:
            geracao, texto = self.fila.get()
            try:
                if geracao == self._geracao:
                    self._tocar(texto, geracao)
            except Exception as e:  # um erro de áudio não pode matar a thread
                print(f"[tts] erro: {e}")
            finally:
                with self._cond:
                    self._pendentes -= 1
                    self._cond.notify_all()

    def _tocar(self, texto: str, geracao: int):
        for chunk in self.voz.synthesize(texto, self.sintese):
            if geracao != self._geracao:
                return
            audio = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
            sd.play(audio, chunk.sample_rate, device=self.saida)
            sd.wait()
