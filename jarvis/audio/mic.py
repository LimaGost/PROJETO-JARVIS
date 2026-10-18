import queue
import threading
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
        self._ouvintes: list[queue.Queue] = []
        self.nivel = 0.0
        self.silenciado = False            # botão "mudo" da HUD: ignora o "Hey Jarvis"
        self.encerrar = threading.Event()  # tira o programa do laço de espera (bandeja > Sair)

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
        bloco = dados[:, 0].copy()
        # volume suavizado (0 a 1) para a HUD animar o reator enquanto você fala
        rms = float(np.sqrt(np.mean(bloco.astype(np.float32) ** 2))) / 32768.0
        self.nivel = max(rms * 6, self.nivel * 0.6)
        self.fila.put(bloco)
        for ouvinte in self._ouvintes:
            ouvinte.put(bloco)

    def assinar(self) -> queue.Queue:
        """Uma cópia do áudio do microfone, para quem escuta em paralelo (ex.: interrupção enquanto ele fala)."""
        q: queue.Queue = queue.Queue()
        self._ouvintes = self._ouvintes + [q]
        return q

    def desassinar(self, q: queue.Queue):
        self._ouvintes = [o for o in self._ouvintes if o is not q]

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

    def esperar_wakeword(self) -> bool:
        """Bloqueia até ouvir o chamado. Retorna False se mandaram encerrar."""
        self.limpar()
        self.wake.reset()
        while not self.encerrar.is_set():
            try:
                bloco = self.fila.get(timeout=0.2)
            except queue.Empty:
                continue
            if self.silenciado:
                continue
            scores = self.wake.predict(bloco)
            if max(scores.values()) >= self.limiar:
                return True
        return False

    def gravar_fala(self, espera_inicio_s: float, cancelar: threading.Event | None = None) -> np.ndarray | None:
        """Grava até o silêncio. Retorna None se ninguém falou a tempo ou se `cancelar` foi acionado."""
        self.limpar()
        blocos: list[np.ndarray] = []
        pre: list[np.ndarray] = []  # um pouco antes da voz, para não cortar a primeira sílaba
        falando = False
        silencio = 0.0
        inicio = time.monotonic()
        dur_bloco = BLOCO / self.taxa

        while True:
            try:
                bloco = self.fila.get(timeout=0.1)
            except queue.Empty:
                if cancelar is not None and cancelar.is_set():
                    return None
                continue
            if cancelar is not None and cancelar.is_set():
                return None
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
