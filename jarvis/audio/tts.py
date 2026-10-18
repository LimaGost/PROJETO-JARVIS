import os
import queue
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import piper  # importado aqui, na thread principal (precarregar): carrega o onnxruntime antes de existir outra thread
import sounddevice as sd

from jarvis.config import caminho
from jarvis.hud.bus import bus


TAXA_CLONE = 24000
ESPERA_VOZ_S = 60   # numa queda da voz clonada, quanto esperar ela voltar antes de pular a frase


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


def _de_memoria(audio: np.ndarray):
    yield audio


class Voz:
    """Fala em segundo plano: frases entram na fila e tocam em ordem.

    Com a voz clonada, cada frase é pedida ao processo da voz NO MOMENTO em que chega (não quando a anterior acaba):
    enquanto uma toca, a seguinte já está sendo gerada, e a fala sai emendada, sem pausas entre as frases.
    A voz do Jarvis é uma só: o Piper só fala se `tts.motor: piper` ou `tts.reserva_piper: true`.
    """

    def __init__(self, cfg: dict):
        t = cfg["tts"]
        self.cfg_tts = t
        self.tom = t.get("tom", 1.0)
        self.saida = cfg["audio"]["saida"]
        self.clone = None
        self.reserva_piper = t.get("motor", "piper") != "clone" or t.get("reserva_piper", False)
        self._piper = None
        if t.get("motor", "piper") == "clone":
            try:
                from jarvis.audio.clone import VozClonada
                self.clone = VozClonada(t)
            except Exception as e:
                print(f"[tts] a voz do Jarvis não carregou ({e}).")
                bus.emitir("sistema", texto="A voz do Jarvis não carregou; vou responder só por texto até ela voltar.")
        self.fila: queue.Queue = queue.Queue()
        self._cond = threading.Condition()
        self._pendentes = 0  # frases na fila + a que está tocando
        self._geracao = 0    # calar() invalida tudo que foi enfileirado antes dele
        self._tocando = None  # (áudio, taxa, instante de início) do trecho em reprodução
        self._pausa = threading.Event()   # limpo = fala segurada (você falou por cima e ainda não sei se foi eco)
        self._pausa.set()
        self._avisou_sem_voz = False
        # frases curtas que se repetem ("Pois não?", "Já cuido disso"): a 1ª vez é gerada, depois sai na hora
        self.memorizaveis: set[str] = set()
        self._cache: dict[str, np.ndarray] = {}
        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------ Piper (só se pedido)
    @property
    def voz(self):
        if self._piper is None:
            from piper import PiperVoice
            self._piper = PiperVoice.load(caminho(self.cfg_tts["voz"]), espeak_data_dir=_dados_espeak())
        return self._piper

    def _sintese_piper(self):
        from piper import SynthesisConfig
        t = self.cfg_tts
        # "tom" < 1 aprofunda a voz (toca mais devagar, então a síntese é acelerada na mesma proporção)
        return SynthesisConfig(length_scale=t["velocidade"] * self.tom, noise_scale=t.get("variacao", 0.667),
                               noise_w_scale=t.get("ritmo", 0.8))

    # ------------------------------------------------------------ interface
    def esperar_pronta(self, limite_s: float = 120) -> bool:
        """Na partida: só começa a falar com a voz certa."""
        return self.clone is None or self.clone.esperar_pronta(limite_s)

    def falar(self, texto: str):
        texto = texto.strip()
        if not texto:
            return
        fonte = None
        if texto in self._cache:
            fonte = _de_memoria(self._cache[texto])
        elif self.clone is not None and self.clone.disponivel():
            try:
                fonte = self.clone.pedir(texto)   # começa a gerar já, enquanto a frase anterior ainda toca
            except Exception:
                fonte = None
        with self._cond:
            if self._pendentes == 0:
                bus.emitir("voz", falando=True)
            self._pendentes += 1
            self.fila.put((self._geracao, texto, fonte))

    def esta_falando(self) -> bool:
        return self._pendentes > 0

    def nivel(self) -> float:
        """Volume (0 a 1) do trecho que está tocando agora, para a HUD animar o reator."""
        atual = self._tocando
        if atual is None:
            return 0.0
        audio, taxa, inicio = atual
        pos = int((time.monotonic() - inicio) * taxa)
        janela = audio[max(0, pos - 400): pos + 400]
        if len(janela) == 0:
            return 0.0
        return min(1.0, float(np.sqrt(np.mean(janela.astype(np.float32) ** 2))) / 32768.0 * 5)

    def esperar(self):
        """Espera terminar de falar + uma guarda para a reverberação não virar 'fala do usuário'."""
        with self._cond:
            self._cond.wait_for(lambda: self._pendentes == 0)
        time.sleep(0.2)

    def calar(self):
        """Interrompe a fala atual e descarta o que estava na fila. Falas novas continuam valendo."""
        with self._cond:
            self._geracao += 1
        if self.clone is not None:
            self.clone.cancelar()        # para de gerar a frase atual e as adiantadas
        self._pausa.set()
        sd.stop()

    def pausar(self):
        """Segura a fala (você começou a falar por cima). `continuar()` retoma do ponto exato; `calar()` descarta."""
        self._pausa.clear()

    def continuar(self):
        self._pausa.set()

    # ------------------------------------------------------------ reprodução
    def _loop(self):
        while True:
            geracao, texto, fonte = self.fila.get()
            try:
                if geracao == self._geracao:
                    self._tocar(texto, geracao, fonte)
                elif fonte is not None:
                    fonte.close()
            except Exception as e:  # um erro de áudio não pode matar a thread
                print(f"[tts] erro: {e}")
            finally:
                self._tocando = None
                with self._cond:
                    self._pendentes -= 1
                    self._cond.notify_all()
                    if self._pendentes == 0:
                        bus.emitir("voz", falando=False)

    def _escrever(self, saida, audio: np.ndarray, taxa: int, geracao: int) -> bool:
        """Toca em fatias de 0,1 s: calar() e pausar() valem quase na hora. False = calaram no meio."""
        passo = max(1, taxa // 10)
        for i in range(0, len(audio), passo):
            while not self._pausa.is_set():
                self._tocando = None
                if geracao != self._geracao:
                    return False
                self._pausa.wait(0.05)
            if geracao != self._geracao:
                return False
            fatia = audio[i:i + passo]
            self._tocando = (fatia, taxa, time.monotonic())
            saida.write(fatia.reshape(-1, 1))
        return True

    def _tocar_clonada(self, fonte, geracao: int, texto: str = "") -> bool:
        """Toca enquanto o processo da voz gera. False = nada saiu (a voz caiu antes de começar)."""
        tocou = False
        pedacos = []
        try:
            with sd.OutputStream(samplerate=TAXA_CLONE, channels=1, dtype="int16", device=self.saida) as saida:
                try:
                    for pedaco in fonte:
                        tocou = True
                        pedacos.append(pedaco)
                        if not self._escrever(saida, pedaco, TAXA_CLONE, geracao):
                            saida.abort()
                            return True
                    saida.stop()                     # deixa o fim do áudio sair antes de fechar
                    if texto in self.memorizaveis and pedacos and texto not in self._cache:
                        self._cache[texto] = np.concatenate(pedacos)
                finally:
                    fonte.close()
        except Exception as e:
            print(f"[tts] voz clonada falhou ({e}).")
        return tocou

    def _tocar(self, texto: str, geracao: int, fonte=None):
        if self.clone is None:
            if self.reserva_piper:
                self._tocar_piper(texto, geracao)
            else:
                self._sem_voz()
            return
        for _ in range(2):          # se a voz cair no meio, espera ela voltar e tenta a frase mais uma vez
            if fonte is None:
                pronta = self.clone.esperar_pronta(ESPERA_VOZ_S, continuar=lambda: geracao == self._geracao)
                if geracao != self._geracao:
                    return
                if not pronta:
                    break
                fonte = self.clone.pedir(texto)
            if self._tocar_clonada(fonte, geracao, texto):
                self._avisou_sem_voz = False
                return
            if geracao != self._geracao:
                return
            fonte = None
        if self.reserva_piper:
            self._tocar_piper(texto, geracao)
        else:
            self._sem_voz()

    def _sem_voz(self):
        if not self._avisou_sem_voz:
            self._avisou_sem_voz = True
            print("[tts] a voz do Jarvis está fora do ar; as respostas aparecem só na HUD.")
            bus.emitir("sistema", texto="A voz do Jarvis está fora do ar; respondo por texto até ela voltar.")

    def _tocar_piper(self, texto: str, geracao: int):
        for chunk in self.voz.synthesize(texto, self._sintese_piper()):
            if geracao != self._geracao:
                return
            audio = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
            taxa = int(chunk.sample_rate * self.tom)
            with sd.OutputStream(samplerate=taxa, channels=1, dtype="int16", device=self.saida) as saida:
                if not self._escrever(saida, audio, taxa, geracao):
                    saida.abort()
                    return
                saida.stop()
