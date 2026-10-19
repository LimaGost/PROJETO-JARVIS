"""Motor de voz Qwen3-TTS: escolha pelo config, protocolo do processo (com o modelo simulado) e a comparação."""
import json
import os
import struct
import subprocess
import sys
import textwrap
import types

import numpy as np
import pytest

from jarvis.audio import clone

QWEN3_CFG = {"clone": {"modelo": "qwen3", "referencia": __file__,
                       "qwen3": {"python": sys.executable, "texto_referencia": "Olá, eu sou o Jarvis.",
                                 "modelo": "Qwen/Qwen3-TTS-12Hz-0.6B-Base", "pedaco": 12}}}


@pytest.fixture
def sem_processo(monkeypatch):
    monkeypatch.setattr(clone.VozClonada, "_subir", lambda self: None)


def test_config_qwen3_escolhe_o_processo_e_passa_a_transcricao(sem_processo):
    v = clone.VozClonada(QWEN3_CFG)
    assert v.worker.name == "qwen3_worker.py" and str(v.python) == sys.executable
    extra = json.loads(v.args[-1])
    assert extra["texto_referencia"] == "Olá, eu sou o Jarvis." and extra["pedaco"] == 12
    assert extra["modelo"].endswith("0.6B-Base") and extra["idioma"] == "Portuguese"


def test_padrao_continua_sendo_o_xtts(sem_processo):
    v = clone.VozClonada({"clone": {"python": sys.executable, "referencia": __file__}})
    assert v.modelo == "xtts" and v.worker.name == "clone_worker.py"


def test_modelo_desconhecido_e_ambiente_ausente_sao_avisados(sem_processo):
    with pytest.raises(clone.ErroClone, match="desconhecido"):
        clone.VozClonada({"clone": {"modelo": "bark", "referencia": __file__}})
    with pytest.raises(clone.ErroClone, match="qwen3"):
        clone.VozClonada({"clone": {"modelo": "qwen3", "referencia": __file__,
                                    "qwen3": {"python": "/nao/existe/python.exe"}}})


FALSOS = {
    "torch.py": "class cuda:\n    @staticmethod\n    def is_available(): return True\n",
    "faster_qwen3_tts.py": textwrap.dedent('''
        import numpy as np
        class _Interno:
            def create_voice_clone_prompt(self, ref_audio, ref_text, x_vector_only_mode):
                return [{"ref_text": ref_text, "xvec": x_vector_only_mode}]
        class FasterQwen3TTS:
            chamadas = []
            @classmethod
            def from_pretrained(cls, modelo_id):
                m = cls(); m.model = _Interno(); return m
            def warmup(self, prefill_len=100): pass
            def generate_voice_clone_streaming(self, text, chunk_size, language, ref_audio, ref_text, xvec_only,
                                               voice_clone_prompt=None):
                assert language == "Portuguese" and voice_clone_prompt and not xvec_only
                for _ in range(3):   # 0,5 s cada, a 12 kHz: o processo precisa converter para 24 kHz
                    yield np.full(6000, 0.5, dtype=np.float32), 12000, {}
    '''),
}


def _ler_quadro(saida):
    cab = saida.read(8)
    id_, n = struct.unpack("<II", cab)
    return id_, saida.read(n)


def test_processo_qwen3_fala_o_mesmo_protocolo_do_xtts(tmp_path):
    for nome, codigo in FALSOS.items():
        (tmp_path / nome).write_text(codigo, encoding="utf-8")
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")
    extra = json.dumps({"texto_referencia": "Olá.", "pedaco": 8})
    proc = subprocess.Popen([sys.executable, str(clone._WORKER_QWEN3), str(ref), "1.0", "0.65", "cuda", extra],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, "PYTHONPATH": str(tmp_path)})
    try:
        aquecimento = []
        while True:                                   # o aquecimento manda áudio com id 0 e depois "pronto"
            id_, dados = _ler_quadro(proc.stdout)
            if dados == b"":
                break
            aquecimento.append(dados)
        assert id_ == 0 and aquecimento
        proc.stdin.write(b'{"id": 7, "texto": "Bom dia, senhor."}\n')
        proc.stdin.flush()
        pedacos = []
        while True:
            id_, dados = _ler_quadro(proc.stdout)
            assert id_ == 7
            if not dados:
                break
            pedacos.append(np.frombuffer(dados, dtype=np.int16))
        audio = np.concatenate(pedacos)
        assert len(pedacos) == 3 and len(audio) == 3 * 12000          # 0,5 s a 24 kHz por pedaço
        assert abs(int(audio.mean()) - 16383) < 50                      # 0.5 em float vira ~16383 em int16
        proc.stdin.write(b'{"sair": true}\n')
        proc.stdin.flush()
        assert proc.wait(10) == 0
        assert "molde da voz" in proc.stderr.read().decode("utf-8")
    finally:
        if proc.poll() is None:
            proc.kill()


def test_comparacao_recomenda_a_fluida_e_mais_fiel(tmp_path, monkeypatch):
    from jarvis import diagnostico as d

    medidas = {"xtts": dict(engasgos=0, silencio=0.0, primeiro_som=0.4, velocidade=3.5, nitidez=82, partida=30),
               "qwen3": dict(engasgos=0, silencio=0.0, primeiro_som=0.7, velocidade=1.3, nitidez=96, partida=45)}
    monkeypatch.setattr(d, "testar_voz", lambda cfg, r, ouvir, modelo: {"modelo": modelo, **medidas[modelo]})
    r = d.Relatorio()
    d.comparar_vozes({"tts": {"clone": {"modelo": "xtts"}}}, r, ouvir=False)
    rec = next(i for i in r.itens if i[0] == "voz recomendada")
    assert "qwen3" in rec[2] and "tts.clone.modelo: qwen3" in rec[3]

    medidas["qwen3"].update(engasgos=4, silencio=2.5)                   # fiel, mas trava: não serve
    r = d.Relatorio()
    d.comparar_vozes({"tts": {"clone": {"modelo": "xtts"}}}, r, ouvir=False)
    rec = next(i for i in r.itens if i[0] == "voz recomendada")
    assert rec[2].startswith("xtts") and rec[1] == "✅"
