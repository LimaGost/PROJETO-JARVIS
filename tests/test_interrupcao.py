"""'Fica quieto' e interromper o Jarvis falando por cima (detector de eco, escuta em paralelo, Controle)."""
import queue
import threading
import time

import numpy as np
import pytest

from jarvis.audio import interrupcao
from jarvis.audio.interrupcao import Detector, Escuta, e_eco
from jarvis.brain.comandos import identificar, pedido_de_silencio
from jarvis.hud.bus import bus
from jarvis.hud.controle import Controle


# ------------------------------------------------------------------ "fica quieto"
@pytest.mark.parametrize("fala", [
    "Fica quieto.", "Fica quieto, Jarvis!", "fique quieto", "Silêncio.", "Silêncio, por favor.", "Cala a boca.",
    "Para de falar.", "Pare de falar, Jarvis.", "Para.", "Pare!", "Chega.", "Jarvis, chega.", "Pode parar.",
    "Shhh", "Psiu!", "Xiu.", "Agora não.", "Deixa pra lá.", "Esquece.", "Tá bom, chega.", "Ok, para.",
    "Fica calado", "para com isso", "Ei, para Jarvis",
])
def test_pedidos_de_silencio(fala):
    assert pedido_de_silencio(fala)
    assert identificar(fala) == "calar"


@pytest.mark.parametrize("fala", [
    "Para onde vai o voo de amanhã?", "Para quando é a reunião?", "pare o spotify",
    "Esquece a conversa", "chega de música, abre o youtube", "Qual a previsão do tempo?",
    "pode parar a música", "Fica quieto e abre o navegador pra mim agora mesmo",
    "silêncio no discord", "Tchau Jarvis",
])
def test_nao_e_pedido_de_silencio(fala):
    assert identificar(fala) != "calar"


def test_comandos_antigos_ainda_ganham():
    assert identificar("Esqueça a conversa.") == "esquecer"
    assert identificar("Tchau, Jarvis") == "desligar"
    assert identificar("usa o claude") == "trocar:claude"


# ------------------------------------------------------------------ detector de eco
def alimentar(det, n, mic, saida, voz=True):
    return [det.alimentar(mic, saida, voz) for _ in range(n)]


def test_eco_dele_nao_dispara():
    det = Detector(fator_eco=3.0, min_blocos=4)
    # o alto-falante em 0,10 volta pelo microfone em ~0,03 (acoplamento 0,3), e o VAD acha que é voz
    assert not any(alimentar(det, 80, mic=0.03, saida=0.10))
    assert det.acoplamento() == pytest.approx(0.3)


def test_sua_voz_por_cima_dispara_depois_de_calibrar():
    det = Detector(fator_eco=3.0, min_blocos=4)
    alimentar(det, 30, mic=0.03, saida=0.10)            # calibra com o eco
    r = alimentar(det, 4, mic=0.15, saida=0.10)         # você fala alto por cima: 0,15 > 3 x 0,3 x 0,10
    assert r == [False, False, False, True]


def test_pico_curto_nao_dispara():
    det = Detector(fator_eco=3.0, min_blocos=4)
    alimentar(det, 30, mic=0.03, saida=0.10)
    assert not any(alimentar(det, 3, mic=0.2, saida=0.10) + alimentar(det, 2, mic=0.03, saida=0.10))


def test_sem_calibrar_e_conservador():
    det = Detector(fator_eco=3.0, min_blocos=4)
    assert not any(alimentar(det, 5, mic=0.2, saida=0.10))   # acoplamento assumido = 1: precisaria de 0,30


def test_enquanto_ele_so_pensa_a_sua_voz_dispara_e_ruido_nao():
    det = Detector(min_blocos_quieto=5)
    assert not any(alimentar(det, 50, mic=0.004, saida=0.0, voz=False))      # ambiente
    assert not any(alimentar(det, 10, mic=0.005, saida=0.0, voz=True))       # VAD enganado por ruído baixo
    assert alimentar(det, 5, mic=0.05, saida=0.0, voz=True)[-1]


def test_eco_atrasado_ainda_conta_como_eco():
    det = Detector(fator_eco=3.0, min_blocos=4)
    alimentar(det, 30, mic=0.03, saida=0.10)
    # a fala dele acabou de parar, mas o eco ainda chega ~200 ms depois: não pode virar "sua voz"
    assert not any(alimentar(det, 3, mic=0.03, saida=0.0))


@pytest.mark.parametrize("ouvido,falado,eco", [
    ("abri o spotify e coloquei", "Abri o Spotify e coloquei o volume em trinta por cento.", True),
    ("não, abre o chrome", "Abri o Spotify e coloquei o volume em trinta por cento.", False),
    ("para", "Abri o Spotify.", False),
    ("", "qualquer", True),
])
def test_e_eco(ouvido, falado, eco):
    assert e_eco(ouvido, falado) is eco


# ------------------------------------------------------------------ escuta em paralelo (áudio falso)
class MicFalso:
    def __init__(self):
        self.ouvintes = []
        self.wake = type("W", (), {"reset": lambda s: None, "predict": lambda s, b: {"hey_jarvis": float(b[0] == 7)}})()

    def assinar(self):
        q = queue.Queue()
        self.ouvintes.append(q)
        return q

    def desassinar(self, q):
        self.ouvintes.remove(q)

    def tocar(self, amplitude, n, marca=0):
        for _ in range(n):
            b = np.full(1280, amplitude, dtype=np.int16)
            b[0] = marca
            for q in self.ouvintes:
                q.put(b)


class VozFalsa:
    def __init__(self, nivel=0.5):
        self._nivel, self.falando = nivel, True
        self.pausas = self.continuacoes = 0

    def nivel(self): return self._nivel
    def esta_falando(self): return self.falando
    def pausar(self): self.pausas += 1
    def continuar(self): self.continuacoes += 1


class OuvidoFalso:
    def __init__(self, texto): self.texto = texto
    def transcrever(self, audio): return self.texto


CFG = {"wakeword": {"limiar": 0.5}, "vad": {"silencio_ms": 400, "max_fala_s": 5, "espera_inicio_s": 1, "agressividade": 3},
       "interrupcao": {"fator_eco": 3.0, "min_ms": 320}}


@pytest.fixture
def voz_por_amplitude(monkeypatch):
    """No teste, 'tem voz' = amplitude acima de 500 (o webrtcvad precisaria de fala de verdade)."""
    monkeypatch.setattr(Escuta, "_tem_voz", lambda self, b: int(abs(b[1:]).mean()) > 500)


def escuta(texto, voz=None, falado="Abri o Spotify e coloquei o volume em trinta por cento."):
    mic, chamadas = MicFalso(), []
    voz = voz or VozFalsa()
    e = Escuta(CFG, mic, OuvidoFalso(texto), voz, ao_interromper=lambda: chamadas.append(1),
               falado=lambda: falado, pausado=lambda: False)
    return e, mic, voz, chamadas


def esperar(cond, t=3.0):
    fim = time.time() + t
    while time.time() < fim and not cond():
        time.sleep(0.02)
    return cond()


def test_voce_fala_por_cima_ele_pausa_e_vira_o_proximo_pedido(voz_por_amplitude):
    e, mic, voz, chamadas = escuta("não, abre o chrome")
    voz._nivel = 0.5                                   # saída 0,1 (nível/5)
    with e:
        mic.tocar(1000, 30)                            # eco: ~0,03 de RMS
        mic.tocar(6000, 6)                             # você: ~0,18 de RMS
        mic.tocar(0, 8)                                # silêncio: fim da sua frase
        assert esperar(lambda: e.resultado is not None)
    assert voz.pausas == 1 and chamadas == [1] and e.resultado == "não, abre o chrome"


def test_eco_que_passou_retoma_a_fala(voz_por_amplitude):
    e, mic, voz, chamadas = escuta("coloquei o volume em trinta")
    with e:
        mic.tocar(1000, 30)
        mic.tocar(6000, 6)
        mic.tocar(0, 8)
        assert esperar(lambda: voz.continuacoes == 1)
    assert chamadas == [] and e.resultado is None


def test_hey_jarvis_interrompe_na_hora(voz_por_amplitude):
    e, mic, voz, chamadas = escuta("que horas são")
    with e:
        mic.tocar(1000, 5)
        mic.tocar(1000, 1, marca=7)                    # o modelo do wake word "ouviu" Hey Jarvis
        assert esperar(lambda: chamadas == [1])
        mic.tocar(6000, 5)
        mic.tocar(0, 8)
        assert esperar(lambda: e.resultado is not None)
    assert e.resultado == "que horas são" and voz.pausas == 0


def test_hey_jarvis_sem_dizer_nada_devolve_vazio(voz_por_amplitude):
    e, mic, voz, chamadas = escuta("")
    with e:
        mic.tocar(1000, 1, marca=7)
        mic.tocar(0, 20)
        assert esperar(lambda: e.resultado is not None, 4)
    assert e.resultado == "" and chamadas == [1]


def test_durante_confirmacao_nao_escuta(voz_por_amplitude):
    mic, chamadas = MicFalso(), []
    e = Escuta(CFG, mic, OuvidoFalso("sim"), VozFalsa(), ao_interromper=lambda: chamadas.append(1),
               falado=lambda: "", pausado=lambda: True)
    with e:
        mic.tocar(9000, 20, marca=7)
        time.sleep(0.4)
    assert chamadas == [] and e.resultado is None


# ------------------------------------------------------------------ Controle
class Cerebro:
    rotulo = "x"

    def __init__(self, frases, atraso=0.0):
        self.frases, self.atraso, self.cancelado = frases, atraso, threading.Event()

    def responder(self, t):
        for f in self.frases:
            if self.cancelado.is_set():
                return
            time.sleep(self.atraso)
            yield f

    def cancelar(self):
        self.cancelado.set()


class Voz:
    def __init__(self):
        self.ditas, self.calou = [], 0

    def falar(self, t): self.ditas.append(t)
    def esperar(self): pass
    def calar(self): self.calou += 1


def ctl(cerebro, voz):
    bus.limpar()
    return Controle({"nome": "Jarvis", "tratamento": "senhor", "llm": {"modelo": "x"}, "vad": {"espera_inicio_s": 1}}, cerebro, voz=voz)


def test_interromper_no_meio_cancela_e_nao_fala_o_resto():
    voz, cer = Voz(), Cerebro(["Um.", "Dois.", "Três.", "Quatro."], atraso=0.15)
    c = ctl(cer, voz)
    threading.Timer(0.35, c.interromper).start()
    c.processar("conta até quatro")
    assert cer.cancelado.is_set() and voz.calou == 1
    assert voz.ditas[:2] == ["Um.", "Dois."] and "Quatro." not in voz.ditas


def test_interromper_depois_de_gerar_nao_cancela_o_cerebro():
    voz, cer = Voz(), Cerebro(["Pronto."])
    c = ctl(cer, voz)
    c.processar("oi")
    c.interromper()                                   # resposta já gerada: só cala a voz (o histórico fica)
    assert not cer.cancelado.is_set() and voz.calou == 1


def test_fica_quieto_digitado_na_hud_cala_sem_responder():
    voz, cer = Voz(), Cerebro(["Não devia falar."])
    c = ctl(cer, voz)
    sis = []
    bus.assinar(lambda e: sis.append(e) if e["tipo"] in ("jarvis", "usuario") else None)
    c.texto_do_hud("fica quieto")
    assert voz.calou == 1 and voz.ditas == [] and sis == []
