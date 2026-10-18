"""Escuta enquanto o Jarvis pensa ou fala, para você poder interrompê-lo.

Dois gatilhos:
- "Hey Jarvis" no meio da fala: sempre interrompe e escuta o que vem depois.
- Falar por cima (barge-in): o microfone também ouve o alto-falante, então a sua voz só conta quando fica
  bem acima do "eco" esperado. O eco é estimado em tempo real (quanto do volume da fala dele volta pelo
  microfone) e o limiar acompanha. Com fone de ouvido quase não há eco e funciona ainda melhor.

Quando dispara durante a fala, ele PAUSA na hora e grava o resto da sua frase. Depois de transcrever:
- se o texto é a própria fala dele (eco que passou) ou ruído, foi alarme falso: retoma do ponto exato;
- senão, o pedido em andamento é interrompido e o texto vira o próximo pedido (ou um "fica quieto").
"""
import queue
import threading
import time
from collections import deque
from statistics import median

import numpy as np
import webrtcvad

from jarvis.brain.comandos import normalizar

BLOCO = 1280            # 80 ms a 16 kHz (o mesmo do microfone)
DUR_BLOCO = BLOCO / 16000


def rms(bloco: np.ndarray) -> float:
    return float(np.sqrt(np.mean(bloco.astype(np.float32) ** 2))) / 32768.0


class Detector:
    """Decide, bloco a bloco, se é a SUA voz (não o eco dele). Lógica pura, testável sem áudio."""

    def __init__(self, fator_eco: float = 3.0, min_blocos: int = 4, min_blocos_quieto: int = 5):
        self.fator_eco = fator_eco                 # quantas vezes acima do eco esperado a sua voz precisa estar
        self.min_blocos = min_blocos               # voz contínua mínima enquanto ele fala (4 x 80 ms = 320 ms)
        self.min_blocos_quieto = min_blocos_quieto  # enquanto ele só pensa (sem eco), um pouco mais rígido
        self.razoes: deque = deque(maxlen=60)      # microfone / saída, últimos ~5 s de fala dele
        self.saidas: deque = deque(maxlen=5)       # volume de saída recente (o eco chega atrasado)
        self.ruido = 0.003                         # ruído ambiente estimado
        self.seguidos = 0

    def acoplamento(self) -> float:
        """Quanto do volume do alto-falante volta pelo microfone (mediana: ignora quando você falou por cima)."""
        return median(self.razoes) if len(self.razoes) >= 10 else 1.0   # sem dados: bem conservador

    def alimentar(self, nivel_mic: float, nivel_saida: float, tem_voz: bool) -> bool:
        self.saidas.append(nivel_saida)
        saida = max(self.saidas)
        if saida > 0.005:
            self.razoes.append(nivel_mic / saida)
            limiar = max(self.ruido * 4, self.fator_eco * self.acoplamento() * saida)
            precisa = self.min_blocos
        else:
            if not tem_voz:
                self.ruido = 0.95 * self.ruido + 0.05 * nivel_mic
            limiar = max(0.006, self.ruido * 4)
            precisa = self.min_blocos_quieto
        if tem_voz and nivel_mic > limiar:
            self.seguidos += 1
        else:
            self.seguidos = 0
        return self.seguidos >= precisa


def e_eco(transcrito: str, falado: str) -> bool:
    """O que o microfone 'ouviu' é a fala do próprio Jarvis? (≥ 70% das palavras saíram da boca dele)"""
    palavras = normalizar(transcrito).split()
    if not palavras:
        return True
    dele = set(normalizar(falado).split())
    if not dele:
        return False
    return sum(p in dele for p in palavras) / len(palavras) >= 0.7 and len(palavras) >= 2


class Escuta:
    """Roda numa thread durante um pedido. `resultado` depois de `parar()`:
    None = ninguém interrompeu; "" = interrompeu mas não disse nada; texto = o que você disse por cima."""

    def __init__(self, cfg: dict, mic, ouvido, voz, ao_interromper, falado, pausado):
        c = cfg.get("interrupcao", {})
        self.por_voz = c.get("por_voz", True)
        self.det = Detector(c.get("fator_eco", 3.0), max(1, int(c.get("min_ms", 320) / 80)))
        self.mic, self.ouvido, self.voz = mic, ouvido, voz
        self.ao_interromper = ao_interromper   # () -> cala e cancela o que ainda estava sendo feito
        self.falado = falado                   # () -> texto que ele já falou neste pedido (para reconhecer eco)
        self.pausado = pausado                 # () -> True durante uma confirmação 🔴 (lá quem escuta é ela)
        self.limiar_wake = cfg["wakeword"]["limiar"]
        self.silencio_ms = cfg["vad"]["silencio_ms"]
        self.max_fala_s = cfg["vad"]["max_fala_s"]
        self.espera_s = cfg["vad"]["espera_inicio_s"]
        self.vad = webrtcvad.Vad(cfg["vad"]["agressividade"])
        self.resultado: str | None = None
        self.interrompeu = threading.Event()
        self._parar = threading.Event()
        self._fila = None
        self._thread = None

    def __enter__(self):
        self._fila = self.mic.assinar()
        self.mic.wake.reset()
        self._thread = threading.Thread(target=self._rodar, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.parar()

    def parar(self):
        """Fim do pedido. Se você estava no meio de uma frase por cima, espera ela acabar e ser transcrita."""
        if self.interrompeu.is_set():
            self._thread.join(self.max_fala_s + 15)
        self._parar.set()
        self._thread.join(2)
        self.mic.desassinar(self._fila)

    # ------------------------------------------------------------ thread
    def _tem_voz(self, bloco) -> bool:
        return sum(self.vad.is_speech(bloco[i:i + 320].tobytes(), 16000) for i in range(0, BLOCO, 320)) >= 2

    def _rodar(self):
        pre: deque = deque(maxlen=8)          # ~640 ms antes do gatilho: não corta o começo da sua frase
        while not self._parar.is_set():
            try:
                bloco = self._fila.get(timeout=0.1)
            except queue.Empty:
                continue
            if self.pausado():
                pre.clear()
                self.det.seguidos = 0
                continue
            pre.append(bloco)
            if max(self.mic.wake.predict(bloco).values()) >= self.limiar_wake:
                self._disparou(wake=True, pre=[])
                return
            nivel_saida = (self.voz.nivel() / 5.0) if self.voz is not None else 0.0
            if self.por_voz and self.det.alimentar(rms(bloco), nivel_saida, self._tem_voz(bloco)):
                if self._disparou(wake=False, pre=list(pre)):
                    return
                pre.clear()                    # alarme falso: volta a vigiar o resto da resposta
                self.det.seguidos = 0
                self.mic.wake.reset()

    def _disparou(self, wake: bool, pre: list) -> bool:
        """True = interrupção de verdade; False = alarme falso (eco ou ruído)."""
        self.interrompeu.set()
        if wake:
            self.ao_interromper()                    # "Hey Jarvis" é intenção clara: cala e cancela já
        elif self.voz is not None:
            self.voz.pausar()                        # segura a fala até saber se era você ou o eco dele
        try:
            audio = self._gravar(pre, espera=self.espera_s if wake else 1.0)
            texto = (self.ouvido.transcrever(audio) if audio is not None else "") or ""
        except Exception as e:
            print(f"\n[interrupção] erro: {e}")
            texto = ""
        texto = texto.strip()
        if not wake and (len(texto) < 2 or e_eco(texto, self.falado())):
            print(f"\n[interrupção ignorada: {'eco' if texto else 'ruído'}{f' “{texto}”' if texto else ''}]")
            self.interrompeu.clear()
            if self.voz is not None:
                self.voz.continuar()                 # retoma exatamente de onde parou
            return False
        if not wake:
            self.ao_interromper()
        self.resultado = texto
        return True

    def _gravar(self, pre: list, espera: float):
        """Continua gravando até você parar de falar."""
        blocos = list(pre)
        falando = bool(pre)
        silencio = 0.0
        inicio = time.monotonic()
        while True:
            try:
                bloco = self._fila.get(timeout=0.2)
            except queue.Empty:
                if not falando and time.monotonic() - inicio > espera:
                    return None
                if time.monotonic() - inicio > espera + self.max_fala_s:
                    break
                continue
            voz = self._tem_voz(bloco)
            if not falando:
                if voz:
                    falando = True
                    blocos.append(bloco)
                elif time.monotonic() - inicio > espera:
                    return None
                continue
            blocos.append(bloco)
            silencio = 0.0 if voz else silencio + DUR_BLOCO
            if silencio * 1000 >= self.silencio_ms or len(blocos) * DUR_BLOCO >= self.max_fala_s:
                break
        return np.concatenate(blocos) if blocos else None
