"""Voz do Jarvis: uma só (nunca troca para o Piper), frases emendadas (a próxima já é gerada enquanto a atual toca),
continuidade da conversa e o registro de uso da assinatura."""
import json
import threading
import time
from datetime import datetime, timedelta

import numpy as np
import pytest

from jarvis.audio import tts


class SaidaFalsa:
    tocado: list = []

    def __init__(self, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def write(self, dados):
        SaidaFalsa.tocado.append(len(dados))

    def stop(self):
        pass

    def abort(self):
        pass


class CloneFalso:
    def __init__(self, pronto=True):
        self.pronto, self.pedidos, self.cancelados = pronto, [], 0

    def disponivel(self):
        return self.pronto

    def esperar_pronta(self, limite_s, continuar=lambda: True):
        fim = time.monotonic() + limite_s
        while not self.pronto and time.monotonic() < fim and continuar():
            time.sleep(0.02)
        return self.pronto

    def pedir(self, texto):
        self.pedidos.append((time.monotonic(), texto))

        def gerar():
            time.sleep(0.05)
            yield np.zeros(2400, dtype=np.int16)
        return gerar()

    def cancelar(self):
        self.cancelados += 1


@pytest.fixture
def voz(monkeypatch):
    monkeypatch.setattr(tts.sd, "OutputStream", SaidaFalsa)
    monkeypatch.setattr(tts.sd, "stop", lambda: None)
    SaidaFalsa.tocado = []
    v = tts.Voz({"tts": {"motor": "piper", "voz": "x", "velocidade": 1.0}, "audio": {"saida": None}})
    v.reserva_piper = False
    v._tocar_piper = lambda *a: pytest.fail("a voz antiga (Piper) não pode falar")
    return v


def test_cada_frase_e_pedida_na_hora_em_que_chega(voz):
    voz.clone = CloneFalso()
    t0 = time.monotonic()
    for f in ("Primeira frase.", "Segunda frase.", "Terceira frase."):
        voz.falar(f)
    assert [p[1] for p in voz.clone.pedidos] == ["Primeira frase.", "Segunda frase.", "Terceira frase."]
    assert voz.clone.pedidos[-1][0] - t0 < 0.05        # a 3ª já está sendo gerada antes da 1ª terminar de tocar
    voz.esperar()
    assert len(SaidaFalsa.tocado) == 3


def test_partida_espera_a_voz_dele_em_vez_de_usar_outra(voz):
    voz.clone = CloneFalso(pronto=False)
    voz.falar("Sistemas online.")
    time.sleep(0.2)
    assert voz.clone.pedidos == [] and voz.esta_falando()   # esperando a voz certa, sem falar com outra
    voz.clone.pronto = True
    voz.esperar()
    assert [p[1] for p in voz.clone.pedidos] == ["Sistemas online."] and SaidaFalsa.tocado


def test_sem_a_voz_dele_responde_so_em_texto(voz, monkeypatch):
    monkeypatch.setattr(tts, "ESPERA_VOZ_S", 0.2)
    voz.clone = CloneFalso(pronto=False)
    voz.falar("Olá.")
    voz.esperar()
    assert SaidaFalsa.tocado == [] and voz._avisou_sem_voz


def test_calar_cancela_tambem_as_frases_adiantadas(voz):
    voz.clone = CloneFalso()
    voz.falar("Uma.")
    voz.calar()
    assert voz.clone.cancelados == 1


def test_frases_repetidas_saem_da_memoria_na_segunda_vez(voz):
    voz.clone = CloneFalso()
    voz.memorizaveis = {"Pois não?"}
    voz.falar("Pois não?")
    voz.esperar()
    voz.falar("Pois não?")
    voz.falar("Outra frase.")
    voz.esperar()
    assert [p[1] for p in voz.clone.pedidos] == ["Pois não?", "Outra frase."]   # a 2ª vez não precisou gerar
    assert len(SaidaFalsa.tocado) == 3


def test_partida_espera_a_voz_ficar_pronta(voz):
    voz.clone = CloneFalso(pronto=False)
    threading.Timer(0.1, lambda: setattr(voz.clone, "pronto", True)).start()
    assert voz.esperar_pronta(2)


# ------------------------------------------------------------------ continuidade (um Jarvis só)
def test_banco_devolve_a_conversa_recente_e_respeita_o_esquecer(tmp_path):
    from jarvis.memoria.banco import Memoria
    m = Memoria(str(tmp_path / "m.db"), embedder=None)
    m.registrar_turno("s1", "meu número da sorte é 47", "Anotado.", [])
    m.registrar_turno("s2", "e o seu?", "Não tenho um, senhor.", [])
    assert [t[1] for t in m.turnos_recentes(8)] == ["meu número da sorte é 47", "e o seu?"]
    assert m.turnos_recentes(8, desde=datetime.now() + timedelta(seconds=1)) == []


def test_qwen_recem_iniciado_recebe_a_conversa_recente():
    from jarvis.brain.llm import Cerebro

    class Mem:
        marco_conversa = None

        def turnos_recentes(self, n, horas=12, desde=None):
            return [("2026-10-17T20:00:00", "meu número da sorte é 47", "Anotado.")]

        def contexto(self, texto):
            return ""
    c = Cerebro({"nome": "Jarvis", "tratamento": "senhor", "llm": {"modelo": "x", "host": "http://127.0.0.1:1",
                                                                   "keep_alive": -1, "temperatura": 0.3, "max_turnos": 10}},
                None, Mem())
    assert "47" in c._contexto("qual é o meu número?", sessao_nova=True)
    assert "Conversa recente" not in c._contexto("qual é o meu número?", sessao_nova=False)
    c.limite_continuidade = 10
    assert c._continuidade() == ""                                  # não estoura o contexto pequeno do Qwen


# ------------------------------------------------------------------ uso da assinatura
def test_resumo_de_uso(tmp_path):
    from jarvis.tools.uso import resumo_uso
    agora = datetime(2026, 10, 17, 21, 0)
    linhas = [{"quando": "2026-10-17T20:00:00", "tipo": "partida", "custo_api_usd": 0.003, "cache_lido": 9000, "saida": 5},
              {"quando": "2026-10-17T20:01:00", "tipo": "pedido", "custo_api_usd": 0.005, "cache_lido": 18000,
               "saida": 50, "chamadas": 2},
              {"quando": "2026-10-10T20:01:00", "tipo": "pedido", "custo_api_usd": 9.0, "chamadas": 1}]
    (tmp_path / "uso_claude_cli.jsonl").write_text("\n".join(json.dumps(l) for l in linhas), encoding="utf-8")
    r = resumo_uso("hoje", tmp_path, agora)
    assert r.startswith("Hoje: 1 pedidos") and "US$ 0,01" in r and "27.000" in r
    assert "nenhum pedido" in resumo_uso("hoje", tmp_path / "vazio", agora)
