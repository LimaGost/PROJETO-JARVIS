"""O diagnóstico ao vivo funciona quando as peças existem (aqui simuladas) e acusa quando não funcionam."""
import re
import zlib

import numpy as np
import pytest

from jarvis import diagnostico as d


class EmbedderFalso:
    """Bolsa de palavras com sinônimos mínimos: faz o papel do modelo real que entende 'nome' ~ 'se chama'."""
    SINONIMOS = {"nome": "chama", "empresa": "trabalha", "time": "torce", "futebol": "torce", "nota": "nf",
                 "gerar": "emitir", "conto": "inventario", "pecas": "contagem", "site": "pedidos",
                 "manda": "envia", "devolver": "troca", "roupa": "peca"}

    def vetores(self, textos, tipo="documento"):
        m = np.zeros((len(textos), 512), dtype=np.float32)
        for i, t in enumerate(textos):
            for p in d._normalizar(t):
                if len(p) > 2:
                    m[i, zlib.crc32(self.SINONIMOS.get(p, p).encode()) % 512] += 1
        return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)

    def consulta(self, texto):
        return self.vetores([texto], "consulta")[0]

    def aquecer(self):
        pass


@pytest.fixture
def relatorio():
    return d.Relatorio()


def _situacao(r, parte):
    return next(i[1] for i in r.itens if i[0] == parte)


def test_memoria_e_aprendizado_com_embeddings_que_funcionam(relatorio, tmp_path, monkeypatch):
    monkeypatch.setattr(d, "_embedder", lambda cfg: EmbedderFalso())
    d.testar_memoria({}, relatorio, tmp_path)
    d.testar_aprendizado({}, relatorio, tmp_path)
    assert _situacao(relatorio, "proteção de segredos") == "✅"
    assert _situacao(relatorio, "contexto do pedido") == "✅"
    assert _situacao(relatorio, "estudar") == "✅"
    assert _situacao(relatorio, "lembrar pelo significado") == "✅"
    assert _situacao(relatorio, "responder pelo que estudou") == "✅"


def test_embeddings_quebrados_sao_acusados(relatorio, tmp_path, monkeypatch):
    class Quebrado(EmbedderFalso):
        def vetores(self, textos, tipo="documento"):     # tudo igual: não entende nada
            return np.ones((len(textos), 8), dtype=np.float32) / np.sqrt(8)
    monkeypatch.setattr(d, "_embedder", lambda cfg: Quebrado())
    d.testar_aprendizado({}, relatorio, tmp_path)
    assert _situacao(relatorio, "responder pelo que estudou") in ("❌", "⚠️")


def test_geracao_rapida_nao_engasga():
    _, _, n = d._linha_do_tempo([(0.3 * k, 24000) for k in range(1, 11)], 24000, reserva_s=1.2)   # 3x o tempo real
    assert n == 0


def test_frase_curta_lenta_a_reserva_segura():
    inicio, _, n = d._linha_do_tempo([(0.5, 12000), (1.5, 12000), (2.5, 12000)], 24000, reserva_s=1.2)
    assert n == 0 and inicio == 2.5            # sem engasgo, só demora mais para começar


def test_fala_longa_com_geracao_lenta_engasga():
    _, silencio, n = d._linha_do_tempo([(1.0 * k, 12000) for k in range(1, 11)], 24000, reserva_s=1.2)  # 0,5x
    assert n > 0 and silencio > 1.0


class VozFalsa:
    def __init__(self, cfg_tts):
        pass

    def esperar_pronta(self, limite):
        return True

    def pedir(self, texto):
        for _ in range(3):
            yield np.zeros(12000, dtype=np.int16)

    def encerrar(self):
        pass


class OuvidoFalso:
    def __init__(self, cfg):
        self.textos = iter(d.FRASES_VOZ[:2] + ["Senhor, encontrei três compromissos para amanhã, uma reunião"] * 5)

    def transcrever(self, audio):
        return next(self.textos)


def test_voz_gera_mede_grava_wav_e_confere_com_o_whisper(relatorio, tmp_path, monkeypatch):
    import sys
    import types

    import jarvis.audio.clone as clone
    monkeypatch.setattr(clone, "VozClonada", VozFalsa)
    monkeypatch.setitem(sys.modules, "jarvis.audio.stt", types.SimpleNamespace(Ouvido=OuvidoFalso))
    monkeypatch.setattr(d, "PASTA_LOGS", tmp_path)
    d.testar_voz({"tts": {"motor": "clone", "clone": {}}}, relatorio, ouvir=False)
    assert _situacao(relatorio, "partida da voz") == "✅" and _situacao(relatorio, "fluidez") == "✅"
    assert (tmp_path / "diagnostico_voz_xtts.wav").stat().st_size > 100_000
    assert any(i[0] == "nitidez da fala" for i in relatorio.itens)


def test_relatorio_grava_arquivo(relatorio, tmp_path, monkeypatch):
    monkeypatch.setattr(d, "PASTA_LOGS", tmp_path)
    relatorio.item("x", False, "quebrou", "conserte assim")
    relatorio.resumo()
    relatorio.gravar()
    texto = (tmp_path / "diagnostico.txt").read_text(encoding="utf-8")
    assert "conserte assim" in texto and "1 falha" in texto
