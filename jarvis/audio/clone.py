"""Cliente da voz clonada: sobe o processo do XTTS-v2 (ambiente .venv-voz) e recebe o áudio em pedaços.

A voz do Jarvis é SÓ esta (decisão do Iury, 2026-10-17): na partida ele espera ela ficar pronta (~30 s) antes de
falar, e se o processo morrer ele sobe de novo (no máximo a cada 10 s) e espera, em vez de trocar de voz.
"""
import json
import queue
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterator

import numpy as np

from jarvis.config import RAIZ, caminho

TAXA = 24000
LOG = RAIZ / "data" / "logs" / "voz_clonada.log"
_WORKER = Path(__file__).with_name("clone_worker.py")


class ErroClone(RuntimeError):
    pass


class VozClonada:
    def __init__(self, cfg_tts: dict):
        c = cfg_tts["clone"]
        self.python = Path(caminho(c.get("python", ".venv-voz/Scripts/python.exe")))
        self.referencia = Path(caminho(c["referencia"]))
        if not self.python.exists():
            raise ErroClone(f"ambiente da voz não encontrado: {self.python}")
        if not self.referencia.exists():
            raise ErroClone(f"áudio de referência não encontrado: {self.referencia}")
        self.args = [str(self.referencia), str(c.get("velocidade", 1.0)), str(c.get("temperatura", 0.65)), c.get("dispositivo", "cuda")]
        self.pronto = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._tentou_em = 0.0
        self._lock = threading.Lock()
        self._saidas: dict[int, queue.Queue] = {}
        self._subir()

    # ------------------------------------------------------------ processo
    def _subir(self):
        self._tentou_em = time.monotonic()
        self.pronto.clear()
        try:   # os erros do processo da voz vão para um arquivo: antes sumiam e não dava para saber por que caiu
            LOG.parent.mkdir(parents=True, exist_ok=True)
            erros = open(LOG, "wb" if LOG.exists() and LOG.stat().st_size > 2_000_000 else "ab")
        except OSError:
            erros = subprocess.DEVNULL
        self._proc = subprocess.Popen(
            [str(self.python), str(_WORKER), *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=erros,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if erros is not subprocess.DEVNULL:
            erros.close()   # o processo filho ficou com a própria cópia
        threading.Thread(target=self._ler, args=(self._proc,), daemon=True).start()

    def _ler(self, proc):
        try:
            while True:
                cab = proc.stdout.read(8)
                if len(cab) < 8:
                    break
                id_, n = struct.unpack("<II", cab)
                dados = proc.stdout.read(n) if n else b""
                if id_ == 0:
                    if n == 0:
                        self.pronto.set()
                    continue
                fila = self._saidas.get(id_)
                if fila is not None:
                    fila.put(dados if n else None)
        except (OSError, ValueError):
            pass
        finally:
            if proc is self._proc:
                self.pronto.clear()
            for fila in list(self._saidas.values()):     # quem esperava não fica pendurado
                fila.put(None)

    def _vivo(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def disponivel(self) -> bool:
        """Pronto para falar agora? Se o processo morreu, tenta ressuscitar (sem bloquear)."""
        if self._vivo():
            return self.pronto.is_set()
        if time.monotonic() - self._tentou_em > 10:
            try:
                self._subir()
            except OSError:
                pass
        return False

    def esperar_pronta(self, limite_s: float, continuar=lambda: True) -> bool:
        """Espera a voz ficar pronta (partida ~30 s; ressurreição depois de uma queda). False = não deu."""
        fim = time.monotonic() + limite_s
        while time.monotonic() < fim and continuar():
            if self.disponivel():
                return True
            self.pronto.wait(0.25)
        return self.disponivel()

    # ------------------------------------------------------------ síntese
    def pedir(self, texto: str) -> Iterator[np.ndarray]:
        """Manda sintetizar AGORA e devolve os pedaços (int16, 24 kHz) conforme ficam prontos. Pedir a próxima frase
        enquanto a atual toca acaba com o silêncio entre frases: o processo gera mais rápido do que o áudio toca."""
        with self._lock:
            self._id += 1
            id_ = self._id
            fila: queue.Queue = queue.Queue()
            self._saidas[id_] = fila
        try:
            self._enviar({"id": id_, "texto": texto})
        except ErroClone:
            self._saidas.pop(id_, None)
            raise
        return self._receber(id_, fila)

    sintetizar = pedir

    def _receber(self, id_: int, fila: queue.Queue) -> Iterator[np.ndarray]:
        terminou = False
        try:
            while True:
                try:
                    dados = fila.get(timeout=30)
                except queue.Empty:
                    raise ErroClone("a voz clonada não respondeu em 30 s")
                if dados is None:
                    terminou = True
                    return
                yield np.frombuffer(dados, dtype=np.int16)
        finally:
            self._saidas.pop(id_, None)
            if not terminou:
                self.cancelar()

    def cancelar(self):
        """Para tudo o que foi pedido: a frase que está saindo e as adiantadas."""
        with self._lock:
            filas = list(self._saidas.values())
            self._saidas.clear()
        for fila in filas:
            fila.put(None)
        try:
            self._enviar({"cancelar": True})
        except ErroClone:
            pass

    def _enviar(self, msg: dict):
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except (OSError, ValueError, AttributeError) as e:
            raise ErroClone(f"processo da voz indisponível: {e}")

    def encerrar(self):
        try:
            self._enviar({"sair": True})
            self._proc.wait(3)
        except Exception:
            if self._vivo():
                self._proc.kill()
