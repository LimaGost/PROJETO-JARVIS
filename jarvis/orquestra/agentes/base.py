"""Agent Registry: o contrato de um agente e o registro (@agente). Agente novo = um arquivo nesta pasta.

Um agente declara o que sabe fazer (capacidades por categoria, 0 a 1), como cobra (assinatura, por token, grátis),
a latência típica e o que suporta (editar arquivos, retomar sessão, pausar). O roteador usa isso + o histórico real
(banco) para escolher; o orquestrador só chama `executar(trabalho, controle)`.
"""
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CATEGORIAS = {
    "analise": "analisar o projeto, entender a estrutura e a arquitetura, encontrar problemas e riscos no código",
    "implementacao": "implementar funcionalidade nova, escrever código, criar ou alterar arquivos do projeto",
    "depuracao": "corrigir erro, bug, exceção, teste falhando, achar a causa raiz de um traceback",
    "refatoracao": "refatorar, melhorar o desempenho, otimizar, limpar e reorganizar o código existente",
    "testes": "rodar os testes do projeto, verificar e validar as alterações",
    "documentacao": "documentar, escrever README, comentários, guias e explicações do código",
    "planejamento": "planejar, dividir em etapas, decidir arquitetura e estratégia, raciocinar sobre opções",
    "consulta": "responder dúvida, explicar conceito, pesquisar, resumir, sem mexer em arquivos",
    "revisao": "revisar alterações, code review, conferir qualidade, apontar bugs e riscos",
    "julgamento": "comparar propostas de soluções diferentes e escolher a melhor com justificativa",
}
PRECISA_ARQUIVOS = {"analise", "implementacao", "depuracao", "refatoracao", "revisao"}   # e "testes" (só o executor)
ESCREVE = {"implementacao", "depuracao", "refatoracao", "documentacao"}   # categorias que podem alterar arquivos
ESTADOS = ("disponivel", "executando", "aguardando", "desconectado", "erro")

REGISTRO: dict[str, type["Agente"]] = {}


@dataclass
class Deteccao:
    instalado: bool
    versao: str = ""
    comando: list[str] | None = None
    detalhe: str = ""
    flags: set = field(default_factory=set)
    compativel: bool = True


@dataclass
class Disponibilidade:
    estado: str                      # disponivel | aguardando | desconectado | erro
    detalhe: str = ""
    autenticado: bool | None = None
    metodo: str = ""                 # assinatura | chave | local | ...


@dataclass
class Trabalho:
    prompt: str
    cwd: Path | None = None
    escrita: bool = False
    categoria: str = "analise"
    modelo: str | None = None
    retomar: str | None = None       # id de sessão anterior (quando o agente permite)
    limite_s: float = 1200
    limite_usd: float | None = None
    limite_mem_mb: float = 4096
    sistema: str = ""                # instruções de papel (vão como system prompt quando o agente aceita)


@dataclass
class Resultado:
    ok: bool
    texto: str = ""
    erro: str = ""
    sessao: str | None = None
    custo_usd: float = 0.0           # custo estimado (equivalente de API)
    cobrado: bool = False            # True = sai do seu bolso (chave de API); False = assinatura/local
    tokens_entrada: int = 0
    tokens_saida: int = 0
    arquivos: list[str] = field(default_factory=list)
    comandos: list[str] = field(default_factory=list)
    cancelado: bool = False
    transitorio: bool = False        # rede/limite de taxa: vale tentar de novo
    duracao_s: float = 0.0
    modelo: str = ""
    dados: dict = field(default_factory=dict)


class Controle:
    """O que o orquestrador entrega ao agente: canal de eventos e o botão de cancelar/pausar."""

    def __init__(self, ao_evento: Callable[[str, str, dict], None] | None = None):
        self.ao_evento = ao_evento
        self.cancelado = threading.Event()
        self.processo = None             # gateway.Processo, quando o agente roda uma CLI

    def evento(self, tipo: str, texto: str, **dados):
        if self.ao_evento is not None:
            try:
                self.ao_evento(tipo, texto, dados)
            except Exception:
                pass

    def registrar(self, processo):
        self.processo = processo
        if self.cancelado.is_set():
            processo.cancelar()

    def cancelar(self):
        self.cancelado.set()
        if self.processo is not None:
            self.processo.cancelar()

    def pausar(self) -> bool:
        return bool(self.processo and self.processo.pausar())

    def retomar(self) -> bool:
        return bool(self.processo and self.processo.retomar())


def versao_tupla(texto: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", texto or "")
    return tuple(int(x) for x in m.groups() if x is not None) if m else ()


_TRANSITORIO = re.compile(r"(?i)(overloaded|rate.?limit|429|503|529|timed? ?out|timeout|ECONNRESET|ETIMEDOUT|"
                          r"network|temporarily|try again|connection (?:reset|refused|error)|stream disconnected)")


def parece_transitorio(texto: str) -> bool:
    return bool(_TRANSITORIO.search(texto or ""))


class Agente:
    id = ""
    nome = ""
    provedor = ""
    tipo = "cli"                       # cli | api | local
    icone = "bot"
    descricao = ""
    descricao_semantica = ""           # texto usado na similaridade semântica do roteador
    capacidades: dict = {}
    pode_ler_projeto = False           # enxerga os arquivos sozinho (CLIs de código)
    pode_editar = False
    suporta_retomar = False
    suporta_pausar = False
    custo_modo = "por_token"           # assinatura | por_token | gratis | variavel
    latencia = "media"                 # baixa | media | alta
    comando_base = ""                  # binário da CLI
    versao_minima = ""
    docs = ""
    instalar = ""                      # comando oficial de instalação
    login = ""                         # como fazer login (texto) · `login_cmd` abre no terminal
    login_cmd: list[str] = []
    max_simultaneos = 2
    CACHE_DETECCAO_S = 300
    CACHE_STATUS_S = 45

    def __init__(self, cfg: dict | None = None, cofre=None):
        self.cfg_geral = cfg or {}
        self.cfg = dict(((self.cfg_geral.get("orquestra", {}) or {}).get("agentes", {}) or {}).get(self.id, {}) or {})
        self._cofre = cofre
        self._deteccao: tuple[float, Deteccao] | None = None
        self._status: tuple[float, Disponibilidade] | None = None
        self._lock = threading.Lock()
        self.rodando = 0                 # quantos passos usando este agente agora

    # ------------------------------------------------------------ configuração
    @property
    def ativo(self) -> bool:
        return self.cfg.get("ativo", True) is not False

    @property
    def cofre(self):
        if self._cofre is None:
            from jarvis.connect.cofre import cofre
            self._cofre = cofre()
        return self._cofre

    def capacidade(self, categoria: str) -> float:
        extra = self.cfg.get("capacidades") or {}
        return float(extra.get(categoria, self.capacidades.get(categoria, 0.0)))

    def modelo_padrao(self) -> str:
        return str(self.cfg.get("modelo") or "")

    def modelos(self) -> list[str]:
        return [m for m in [self.modelo_padrao()] if m]

    # ------------------------------------------------------------ detecção (cache)
    def detectar(self, forcar: bool = False) -> Deteccao:
        with self._lock:
            if not forcar and self._deteccao and time.time() - self._deteccao[0] < self.CACHE_DETECCAO_S:
                return self._deteccao[1]
        try:
            d = self._detectar()
        except Exception as e:
            d = Deteccao(False, detalhe=f"falha ao detectar: {type(e).__name__}: {e}"[:200])
        if d.instalado and self.versao_minima and d.versao:
            d.compativel = versao_tupla(d.versao) >= versao_tupla(self.versao_minima)
        with self._lock:
            self._deteccao = (time.time(), d)
        return d

    def _detectar(self) -> Deteccao:
        return Deteccao(True, detalhe="sem instalação")

    def disponibilidade(self, forcar: bool = False) -> Disponibilidade:
        with self._lock:
            if not forcar and self._status and time.time() - self._status[0] < self.CACHE_STATUS_S:
                return self._status[1]
        if not self.ativo:
            st = Disponibilidade("desconectado", "desligado no config.yaml (orquestra.agentes)")
        else:
            d = self.detectar(forcar)
            if not d.instalado:
                st = Disponibilidade("desconectado", d.detalhe or "não instalado")
            elif not d.compativel:
                st = Disponibilidade("erro", f"versão {d.versao} é antiga; precisa de {self.versao_minima} ou mais nova")
            else:
                try:
                    st = self._autenticacao()
                except Exception as e:
                    st = Disponibilidade("erro", f"falha ao verificar o login: {type(e).__name__}: {e}"[:200])
        with self._lock:
            self._status = (time.time(), st)
        return st

    def _autenticacao(self) -> Disponibilidade:
        return Disponibilidade("disponivel", autenticado=True)

    def esquecer_cache(self):
        with self._lock:
            self._deteccao = self._status = None

    # ------------------------------------------------------------ execução
    def executar(self, trabalho: Trabalho, controle: Controle) -> Resultado:
        raise NotImplementedError

    # ------------------------------------------------------------ para a HUD
    def cartao(self) -> dict:
        d = self.detectar()
        st = self.disponibilidade()
        return {"id": self.id, "nome": self.nome, "provedor": self.provedor, "tipo": self.tipo, "icone": self.icone,
                "descricao": self.descricao, "estado": st.estado, "detalhe": st.detalhe, "metodo": st.metodo,
                "instalado": d.instalado, "versao": d.versao, "compativel": d.compativel, "versao_minima": self.versao_minima,
                "capacidades": {c: round(self.capacidade(c), 2) for c in CATEGORIAS if self.capacidade(c) > 0},
                "pode_editar": self.pode_editar, "pode_ler_projeto": self.pode_ler_projeto,
                "suporta_retomar": self.suporta_retomar, "suporta_pausar": self.suporta_pausar,
                "custo_modo": self.custo_modo, "latencia": self.latencia, "modelos": self.modelos(),
                "modelo": self.modelo_padrao(), "docs": self.docs, "instalar": self.instalar, "login": self.login,
                "pode_login": bool(self.login_cmd), "rodando": self.rodando, "max_simultaneos": self.max_simultaneos,
                "simulado": "[SIMULADO]" in (d.detalhe or "")}     # a CLI simulada da demonstração, não a real


def agente(classe):
    """Decorador: põe o agente no registro."""
    assert classe.id and classe.nome and all(c in CATEGORIAS for c in classe.capacidades), classe
    REGISTRO[classe.id] = classe
    return classe
