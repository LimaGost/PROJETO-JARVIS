"""Modo jogo: o Jarvis fica ligado o tempo todo sem pesar no PC enquanto você joga ou usa algo pesado na GPU.

Quem pesa no Ollama:
  - o Qwen (~5,5 GB de VRAM): quando é o cérebro ele mora na GPU, e a reflexão da memória o sobe quando o PC
    fica ocioso (jogando você não fala com o Jarvis, então a reflexão rodava NO MEIO do jogo);
  - o embeddinggemma (~1 GB de RAM), que ficava carregado para sempre.

O Guardião olha a GPU a cada poucos segundos. Uso alto e sustentado que NÃO é do Jarvis (ele não está ouvindo,
pensando nem falando) = jogo. Aí ele: tira o Qwen e o embeddinggemma da memória do Ollama, segura a reflexão e faz
qualquer carga nova do Ollama sair logo depois de usar. Quando a GPU fica livre por um tempo, volta ao normal (os
modelos voltam sob demanda). A voz e o ouvido continuam: o Jarvis segue atendendo "Hey Jarvis" durante o jogo.
"""
import threading
import time
from typing import Callable

_em_jogo = threading.Event()
KEEP_ALIVE_EM_JOGO = "30s"     # o que o Ollama carregar durante o jogo sai meio minuto depois de usar


def em_jogo() -> bool:
    return _em_jogo.is_set()


def keep_alive(normal):
    """keep_alive para mandar ao Ollama agora: o normal, ou curto durante o jogo."""
    return KEEP_ALIVE_EM_JOGO if _em_jogo.is_set() else normal


def ollama_carregados(cliente) -> list[tuple[str, int, int]]:
    """[(modelo, MB de VRAM, MB totais)] do que está na memória do Ollama agora."""
    try:
        r = cliente.ps()
    except Exception:
        return []
    saida = []
    for m in getattr(r, "models", None) or []:
        nome = getattr(m, "model", None) or getattr(m, "name", "")
        saida.append((nome, int((getattr(m, "size_vram", 0) or 0) / 2**20), int((getattr(m, "size", 0) or 0) / 2**20)))
    return saida


def descarregar_ollama(cliente, nomes: list[str] | None = None) -> list[str]:
    """Tira da memória do Ollama os modelos carregados (todos, ou só os de `nomes`). Devolve os que saíram."""
    saiu = []
    for nome, _, _ in ollama_carregados(cliente):
        if nomes and not any(nome == n or nome.split(":")[0] == n.split(":")[0] for n in nomes):
            continue
        for tentar in (lambda: cliente.generate(model=nome, prompt="", keep_alive=0),
                       lambda: cliente.embed(model=nome, input=["."], keep_alive=0)):   # modelo só de embeddings
            try:
                tentar()
                saiu.append(nome)
                break
            except Exception:
                continue
    return saiu


class Guardiao:
    def __init__(self, cfg: dict, ler_gpu: Callable[[], dict], jarvis_ocupado: Callable[[], bool],
                 cliente_ollama=None, modelos: list[str] | None = None, avisar: Callable[[str], None] = print,
                 relogio: Callable[[], float] = time.monotonic):
        r = cfg.get("recursos", {}) or {}
        self.ativo = bool(r.get("modo_jogo_automatico", True))
        self.limiar = float(r.get("gpu_jogo_pct", 50))           # % de uso da GPU que conta como jogo
        self.para_entrar = float(r.get("segundos_para_entrar", 20))
        self.para_sair = float(r.get("segundos_para_sair", 90))
        self.intervalo = float(r.get("intervalo_s", 5))
        self.ler_gpu, self.jarvis_ocupado = ler_gpu, jarvis_ocupado
        self.cliente, self.modelos, self.avisar, self.relogio = cliente_ollama, modelos, avisar, relogio
        self._alto_desde: float | None = None
        self._baixo_desde: float | None = None
        self._parar = threading.Event()

    # ------------------------------------------------------------ decisão (testável sem thread)
    def verificar(self) -> bool | None:
        """Uma leitura. Devolve True ao entrar no modo jogo, False ao sair, None se nada mudou."""
        if not self.ativo:
            return None
        agora = self.relogio()
        if self.jarvis_ocupado():               # o próprio Jarvis usando a GPU (voz, Whisper) não é jogo
            self._alto_desde = None
            return None
        uso = (self.ler_gpu() or {}).get("gpu")
        if uso is None:                          # sem nvidia-smi: não dá para saber, não mexe em nada
            return None
        if uso >= self.limiar:
            self._baixo_desde = None
            self._alto_desde = self._alto_desde if self._alto_desde is not None else agora
            if not em_jogo() and agora - self._alto_desde >= self.para_entrar:
                self.entrar()
                return True
        else:
            self._alto_desde = None
            self._baixo_desde = self._baixo_desde if self._baixo_desde is not None else agora
            if em_jogo() and agora - self._baixo_desde >= self.para_sair:
                self.sair()
                return False
        return None

    def entrar(self):
        _em_jogo.set()
        saiu = descarregar_ollama(self.cliente, self.modelos) if self.cliente is not None else []
        self.avisar("Modo jogo: liberei a GPU e a memória" + (f" ({', '.join(saiu)})" if saiu else "")
                    + ". Continuo ouvindo o 'Hey Jarvis'.")

    def sair(self):
        _em_jogo.clear()
        self.avisar("GPU livre de novo: modo normal.")

    # ------------------------------------------------------------ vigia
    def iniciar(self):
        if not self.ativo:
            return

        def vigiar():
            while not self._parar.wait(self.intervalo):
                try:
                    self.verificar()
                except Exception:
                    pass                          # uma leitura que falha não pode derrubar o vigia
        threading.Thread(target=vigiar, daemon=True, name="guardiao").start()

    def parar(self):
        self._parar.set()
