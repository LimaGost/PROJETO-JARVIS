import os
import sys
from pathlib import Path

import numpy as np


def _registrar_dlls_cuda():
    """No Windows o ctranslate2 precisa achar cublas/cudnn instalados via pip."""
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not base.exists():
        return
    for bin_dir in base.glob("*/bin"):
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]


_registrar_dlls_cuda()

from faster_whisper import WhisperModel  # noqa: E402


class Ouvido:
    def __init__(self, cfg: dict):
        c = cfg["stt"]
        self.idioma = c["idioma"]
        self.beam = c.get("beam", 1)
        self.vocabulario = c.get("vocabulario")
        self.modelo = WhisperModel(c["modelo"], device=c["dispositivo"], compute_type=c["tipo"])

    def aquecer(self):
        """A primeira inferência inicializa o CUDA/cuDNN e demora; paga isso na partida."""
        self.transcrever(np.zeros(16000, dtype=np.int16))

    def transcrever(self, audio_int16: np.ndarray) -> str:
        # um respiro de silêncio antes e depois evita que o Whisper engula a 1ª palavra de comandos curtos
        respiro = np.zeros(int(0.4 * 16000), dtype=np.int16)
        audio = np.concatenate([respiro, audio_int16, respiro]).astype(np.float32) / 32768.0
        segmentos, _ = self.modelo.transcribe(
            audio,
            language=self.idioma,
            beam_size=self.beam,
            initial_prompt=self.vocabulario,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segmentos).strip()
