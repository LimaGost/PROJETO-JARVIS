"""Modo jogo: o Jarvis libera GPU/RAM do Ollama quando outro programa usa a GPU pesado, e volta depois."""
import types

import pytest

from jarvis import recursos
from jarvis.brain.llm import Cerebro
from jarvis.memoria.embeddings import Embedder

CFG_LLM = {"nome": "Jarvis", "tratamento": "senhor",
           "llm": {"modelo": "qwen3:8b", "host": "http://127.0.0.1:1", "keep_alive": -1, "temperatura": 0.3,
                   "max_turnos": 10}}


@pytest.fixture(autouse=True)
def limpo():
    recursos._em_jogo.clear()
    yield
    recursos._em_jogo.clear()


class Relogio:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class OllamaFalso:
    """ps() com os modelos do Jarvis e um de outro programa; registra o que foi descarregado."""
    def __init__(self, embed_sem_generate=True):
        self.descarregados = []
        self.embed_sem_generate = embed_sem_generate

    def ps(self):
        modelos = [types.SimpleNamespace(model="qwen3:8b", size_vram=5_500 * 2**20, size=6_000 * 2**20),
                   types.SimpleNamespace(model="embeddinggemma:latest", size_vram=0, size=900 * 2**20),
                   types.SimpleNamespace(model="llama3.2:3b", size_vram=2_000 * 2**20, size=2_000 * 2**20)]
        return types.SimpleNamespace(models=modelos)

    def generate(self, model, prompt, keep_alive):
        if "embeddinggemma" in model and self.embed_sem_generate:
            raise RuntimeError("does not support generate")
        assert keep_alive == 0
        self.descarregados.append(model)

    def embed(self, model, input, keep_alive):
        assert keep_alive == 0
        self.descarregados.append(model)


def guardiao(uso, ocupado=lambda: False, **cfg):
    relogio, avisos = Relogio(), []
    ollama = OllamaFalso()
    g = recursos.Guardiao({"recursos": cfg}, lambda: {"gpu": uso[0]} if uso[0] is not None else {}, ocupado,
                          ollama, ["qwen3:8b", "embeddinggemma"], avisos.append, relogio)
    return g, relogio, ollama, avisos


def test_jogo_sustentado_entra_e_libera_so_os_modelos_do_jarvis():
    uso = [85]
    g, relogio, ollama, avisos = guardiao(uso)
    assert g.verificar() is None                  # pico de 0 s ainda não é jogo
    relogio.t = 10
    assert g.verificar() is None
    relogio.t = 21
    assert g.verificar() is True and recursos.em_jogo()
    assert sorted(ollama.descarregados) == ["embeddinggemma:latest", "qwen3:8b"]   # o llama3.2 de outro app fica
    assert "Modo jogo" in avisos[0] and "Hey Jarvis" in avisos[0]


def test_pico_curto_nao_e_jogo():
    uso = [90]
    g, relogio, _, _ = guardiao(uso)
    g.verificar()
    relogio.t = 15
    uso[0] = 10                                   # abriu um vídeo e fechou
    g.verificar()
    relogio.t = 40
    uso[0] = 90
    g.verificar()
    relogio.t = 50
    assert g.verificar() is None and not recursos.em_jogo()   # o contador recomeçou do zero


def test_gpu_usada_pelo_proprio_jarvis_nao_conta():
    uso, falando = [95], [True]
    g, relogio, _, _ = guardiao(uso, ocupado=lambda: falando[0])
    for t in range(0, 60, 5):
        relogio.t = t
        assert g.verificar() is None
    assert not recursos.em_jogo()


def test_sai_do_modo_jogo_quando_a_gpu_fica_livre():
    uso = [80]
    g, relogio, _, avisos = guardiao(uso, segundos_para_entrar=0)
    assert g.verificar() is True
    uso[0] = 5
    relogio.t = 30
    assert g.verificar() is None and recursos.em_jogo()        # 30 s livre ainda não basta
    relogio.t = 125
    assert g.verificar() is False and not recursos.em_jogo()
    assert "modo normal" in avisos[-1]


def test_sem_nvidia_smi_nao_mexe_em_nada():
    g, relogio, ollama, _ = guardiao([None], segundos_para_entrar=0)
    relogio.t = 100
    assert g.verificar() is None and ollama.descarregados == [] and not recursos.em_jogo()


def test_desligado_pelo_config():
    g, relogio, _, _ = guardiao([99], modo_jogo_automatico=False, segundos_para_entrar=0)
    assert g.verificar() is None and not recursos.em_jogo()


def test_em_jogo_o_ollama_solta_logo_o_que_carregar():
    qwen = Cerebro(CFG_LLM)
    assert qwen.keep_alive == -1                                # cérebro: fica na GPU (sem jogo)
    recursos._em_jogo.set()
    assert qwen.keep_alive == recursos.KEEP_ALIVE_EM_JOGO       # jogando: sai 30 s depois de usar


def test_embeddings_nao_ficam_mais_para_sempre(monkeypatch):
    chamadas = []

    class Cliente:
        def __init__(self, host):
            pass

        def embed(self, model, input, keep_alive, options):
            chamadas.append(keep_alive)
            return types.SimpleNamespace(embeddings=[[1.0, 0.0]] * len(input))
    monkeypatch.setattr("jarvis.memoria.embeddings.ollama.Client", Cliente)
    e = Embedder("http://127.0.0.1:1", "embeddinggemma", "30m")
    e.vetores(["oi"])
    recursos._em_jogo.set()
    e.vetores(["oi"])
    assert chamadas == ["30m", recursos.KEEP_ALIVE_EM_JOGO]


def test_reflexao_nao_roda_jogando():
    import threading

    from jarvis.memoria.reflexao import Reflexao
    r = Reflexao.__new__(Reflexao)
    r.ocupado = threading.Event()
    assert r._livre()
    recursos._em_jogo.set()
    assert not r._livre()


def test_inicio_com_windows_usa_o_powershell_que_existir(monkeypatch):
    from jarvis.hud import inicio
    usados = []
    monkeypatch.setattr(inicio.shutil, "which", lambda n: r"C:\Windows\powershell.exe" if n == "powershell" else None)
    monkeypatch.setattr(inicio.subprocess, "run",
                        lambda cmd, **k: usados.append(cmd[0]) or types.SimpleNamespace(returncode=0, stderr=""))
    inicio._powershell("echo oi")
    assert usados == [r"C:\Windows\powershell.exe"]           # sem PowerShell 7 instalado, não quebra mais
