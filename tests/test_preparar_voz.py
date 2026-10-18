"""Preparação do áudio de referência da voz clonada (sem o PyAV: o sinal é gerado aqui)."""
import wave

import numpy as np

from jarvis.audio import preparar_voz as pv


def _tom(segundos, volume=0.1):
    t = np.arange(int(segundos * pv.TAXA)) / pv.TAXA
    return (volume * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _silencio(segundos):
    return np.zeros(int(segundos * pv.TAXA), dtype=np.float32)


def test_corta_pontas_encurta_pausas_e_normaliza():
    bruto = np.concatenate([_silencio(2), _tom(1.0), _silencio(1.5), _tom(1.0, 0.02), _silencio(3)])
    limpo = pv.limpar(bruto)
    duracao = len(limpo) / pv.TAXA
    # 2 s de fala + no máximo 0,35 s de pausa + margens de 0,08 s nas pontas
    assert 2.0 <= duracao <= 2.0 + pv.MAX_PAUSA_S + 2 * pv.MARGEM_S + 0.05
    assert np.max(np.abs(limpo)) <= 10 ** (pv.PICO_DB / 20) + 1e-6       # nada estoura


def test_volume_baixo_sobe_para_o_alvo():
    limpo = pv.limpar(np.concatenate([_tom(2.0, 0.005), _silencio(0.5)]))
    rms_db = 20 * np.log10(np.sqrt(np.mean(limpo[: int(1.5 * pv.TAXA)] ** 2)))
    assert abs(rms_db - pv.ALVO_RMS_DB) < 1.0


def test_grava_wav_mono_24k(tmp_path):
    destino = tmp_path / "ref.wav"
    pv.gravar(pv.limpar(_tom(1.0)), destino)
    with wave.open(str(destino)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 24000, 2)
