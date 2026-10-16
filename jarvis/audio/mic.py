import queue
import time

import numpy as np
import sounddevice as sd
import webrtcvad
from openwakeword.model import Model

from jarvis.config import caminho

BLOCO = 1280  # 80 ms a 16 kHz, tamanho que o openWakeWord espera


class Microfone:
    def __init__(self, cfg: dict):
        self.taxa = cfg["audio"]["taxa"]
        self.vad = webrtcvad.Vad(cfg["vad"]["agressividade"])
        self.silencio_ms = cfg["vad"]["silencio_ms"]
        self.max_fala_s = cfg["vad"]["max_fala_s"]
        self.limiar = cfg["wakeword"]["limiar"]
        self.fila: queue.Queue = queue.Queue()

        self.wake = Model(
            wakeword_models=[caminho(cfg["wakeword"]["modelo"])],
            melspec_model_path=caminho("data/wakeword/melspectrogram.onnx"),
            embedding_model_path=caminho("data/wakeword/embedding_model.onnx"),
            inference_framework="onnx",
        )
        self.stream = sd.InputStream(
            samplerate=self.taxa,
            channels=1,
            dtype="int16",
            blocksize=BLOCO,
            device=cfg["audio"]["entrada"],
            callback=self._callback,
        )

    def _callback(self, dados, frames, tempo, status):
        self.fila.put(dados[:, 0].copy())

    def iniciar(self):
        self.stream.start()

    def parar(self):
        self.stream.stop()
        self.stream.close()

    def limpar(self):
        """Descarta o áudio acumulado (ex.: o que o alto-falante soltou)."""
        while not self.fila.empty():
            try:
                self.fila.get_nowait()
            except queue.Empty:
                break

    def _tem_voz(self, bloco: np.ndarray) -> bool:
        # webrtcvad aceita quadros de 10/20/30 ms: 1280 amostras = 4 x 20 ms
        n = 0
        for i in range(0, BLOCO, 320):
            if self.vad.is_speech(bloco[i : i + 320].tobytes(), self.taxa):
                n += 1
        return n >= 2

    def esperar_wakeword(self) -> None:
        self.limpar()
        self.wake.reset()
        while True:
            bloco = self.fila.get()
            scores = self.wake.predict(bloco)
            if max(scores.values()) >= self.limiar:
                return

    def gravar_fala(self, espera_inicio_s: float) -> np.ndarray | None:
        """Grava até o silêncio. Retorna None se ninguém falou a tempo."""
        self.limpar()
        blocos: list[np.ndarray] = []
        pre: list[np.ndarray] = []  # um pouco antes da voz, para não cortar a primeira sílaba
        falando = False
        silencio = 0.0
        inicio = time.monotonic()
        dur_bloco = BLOCO / self.taxa

        while True:
            bloco = self.fila.get()
            voz = self._tem_voz(bloco)

            if not falando:
                pre.append(bloco)
                pre = pre[-4:]
                if voz:
                    falando = True
                    blocos = list(pre)
                elif time.monotonic() - inicio > espera_inicio_s:
                    return None
                continue

            blocos.append(bloco)
            silencio = 0.0 if voz else silencio + dur_bloco
            if silencio * 1000 >= self.silencio_ms:
                break
            if len(blocos) * dur_bloco >= self.max_fala_s:
                break

        return np.concatenate(blocos)
