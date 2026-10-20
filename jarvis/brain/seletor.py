"""Seletor de mentalidade: escolher em tempo real o cérebro do Jarvis: Qwen local, Claude (assinatura/CLI ou API),
ChatGPT (Codex CLI, conta do ChatGPT) e Gemini (Gemini CLI, conta Google).

Cérebro de CLI sem login: escolher abre o login oficial sozinho (jarvis/brain/logins.py) e troca quando terminar.

Tem a mesma interface de um cérebro (responder, cancelar, esquecer, aquecer, rotulo), então o resto do JARVIS não
sabe qual está em uso. Cada cérebro é criado só quando é escolhido pela primeira vez (o `claude` só sobe se você
pedir) e a escolha fica salva em data/preferencias.json.

Ao trocar, a conversa em andamento recomeça (cada cérebro tem a sua), mas a memória de longo prazo é a mesma.

Dentro do Claude também se escolhe o MODELO (Opus, Sonnet, Haiku): trocar reinicia o processo do `claude` com o
novo `--model` (ou recria o cliente da API). O novo é testado antes de largar o atual, e recebe a conversa recente
do banco: para o usuário, o Jarvis continua o mesmo, só pensando com outro modelo.
"""
import json
import threading
from typing import Callable, Iterator

from jarvis.config import RAIZ

ARQUIVO_PREFERENCIAS = RAIZ / "data" / "preferencias.json"
MOTOR_PARA_ID = {"ollama": "qwen", "claude-cli": "claude-cli", "claude": "claude-api", "codex-cli": "codex-cli",
                 "gemini-cli": "gemini-cli"}
CLI_DO_CEREBRO = {"claude-cli": "claude", "codex-cli": "codex", "gemini-cli": "gemini"}   # para o login automático
ALVOS = {"chatgpt": "codex-cli", "codex": "codex-cli", "gpt": "codex-cli", "gemini": "gemini-cli"}
MODELOS_CLAUDE = ("opus", "sonnet", "haiku")       # apelidos do `claude --model`
MODELOS_API = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
IDS_CLAUDE = ("claude-cli", "claude-api")
MOTIVO_LOGIN = "sem login: escolha para entrar (abro o login sozinho)"


def _preferencias() -> dict:
    try:
        dados = json.loads(ARQUIVO_PREFERENCIAS.read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def carregar_preferencia(chave: str = "cerebro") -> str | None:
    return _preferencias().get(chave)


def salvar_preferencia(id_: str, **outras):
    try:
        ARQUIVO_PREFERENCIAS.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_PREFERENCIAS.write_text(json.dumps({**_preferencias(), "cerebro": id_, **outras}), encoding="utf-8")
    except OSError:
        pass  # perder a preferência não pode atrapalhar a troca


class Seletor:
    def __init__(self, cfg: dict, executor=None, memoria=None, fabricas: dict[str, Callable] | None = None,
                 disponibilidade: Callable[[str], str | None] | None = None):
        """`fabricas` e `disponibilidade` existem para teste; em produção vêm das implementações reais."""
        from jarvis.brain.llm import Cerebro
        # cópia própria dos blocos do Claude: trocar o modelo não mexe no config de quem chamou
        cfg = {**cfg, "claude_cli": dict(cfg.get("claude_cli", {}) or {}), "claude": dict(cfg.get("claude", {}) or {})}
        self.cfg, self.executor, self.memoria = cfg, executor, memoria
        self.modelos_claude = list(cfg["claude_cli"].get("modelos", MODELOS_CLAUDE))
        self.modelos_api = {**MODELOS_API, **(cfg["claude"].get("modelos") or {})}
        salvo = carregar_preferencia("modelo_claude")
        inicial = salvo if salvo in self.modelos_claude else cfg["claude_cli"].get("modelo", "sonnet")
        self._aplicar_modelo(inicial)
        self.local = Cerebro(cfg, executor, memoria)               # sempre existe: é o Qwen e a reserva dos outros
        self._objetos: dict[str, object] = {"qwen": self.local}
        self._lock = threading.RLock()
        self._fabricas = fabricas or {"claude-cli": self._criar_cli, "claude-api": self._criar_api,
                                      "codex-cli": self._criar_codex, "gemini-cli": self._criar_gemini}
        self._disponibilidade = disponibilidade or self._motivo_indisponivel
        self.atual = self._escolha_inicial()

    # ------------------------------------------------------------ catálogo
    def _meta(self) -> dict[str, dict]:
        cli = self.cfg.get("claude_cli", {}).get("modelo", "sonnet")
        api = self.cfg.get("claude", {}).get("modelo", "claude-opus-5-5")
        return {
            "qwen": {"nome": "Qwen local", "descricao": f"{self.cfg['llm']['modelo']} no seu PC: privado, sem custo, mais fraco"},
            "claude-cli": {"nome": "Claude · assinatura", "descricao": f"Claude {cli} pelo seu login: muito mais capaz, gasta a sua cota"},
            "claude-api": {"nome": "Claude · API", "descricao": f"{api} por chave de API: muito mais capaz, cobrança por uso"},
            "codex-cli": {"nome": "ChatGPT · CLI", "descricao": "ChatGPT pelo Codex CLI, com o login da sua conta do ChatGPT"},
            "gemini-cli": {"nome": "Gemini · CLI", "descricao": "Gemini pelo Gemini CLI, com a sua conta Google"},
        }

    def _motivo_indisponivel(self, id_: str) -> str | None:
        """None se dá para usar; senão o motivo, em português, para a HUD mostrar."""
        if id_ == "qwen":
            return None
        if id_ in CLI_DO_CEREBRO:
            from jarvis.brain import logins
            cli = CLI_DO_CEREBRO[id_]
            configurado = self.cfg.get({"claude-cli": "claude_cli", "codex-cli": "codex_cli",
                                        "gemini-cli": "gemini_cli"}[id_], {}) or {}
            exe = configurado.get("executavel") or configurado.get("comando")
            if not (exe or logins.instalado(cli)):
                return f"o {logins.CLIS[cli]['nome']} não está instalado"
            estado, _ = logins.verificar(cli, exe if isinstance(exe, list) else None)
            if estado is False:
                return MOTIVO_LOGIN
            return None
        if id_ == "claude-api":
            from jarvis.brain.claude import carregar_chave
            return None if carregar_chave() else "falta a chave em data/anthropic.key"
        return "opção desconhecida"

    def ids(self) -> list[str]:
        return list(self._meta())

    def opcoes(self) -> list[dict]:
        from jarvis.brain import logins
        meta = self._meta()
        saida = []
        for id_, m in meta.items():
            motivo = self._disponibilidade(id_)
            saida.append({"id": id_, "nome": m["nome"], "descricao": m["descricao"], "disponivel": motivo is None,
                          "motivo": motivo, "ativo": id_ == self.atual, "precisa_login": motivo == MOTIVO_LOGIN,
                          "entrando": id_ in CLI_DO_CEREBRO and logins.esperando(CLI_DO_CEREBRO[id_])})
        return saida

    def estado(self) -> dict:
        return {"atual": self.atual, "rotulo": self.rotulo, "opcoes": self.opcoes(),
                "modelos_claude": self.modelos_claude, "modelo_claude": self.modelo_claude}

    # ------------------------------------------------------------ modelo do Claude (Opus / Sonnet / Haiku)
    def _aplicar_modelo(self, modelo: str):
        self.modelo_claude = modelo
        self.cfg["claude_cli"]["modelo"] = modelo
        if modelo in self.modelos_api:
            self.cfg["claude"]["modelo"] = self.modelos_api[modelo]

    def trocar_modelo_claude(self, modelo: str) -> dict:
        """{'ok', 'atual', 'modelo', 'erro'}. Se o Qwen estiver ativo, já passa para o Claude com esse modelo.
        O novo processo é testado antes de largar o atual: se falhar (cota, login), tudo continua como estava."""
        with self._lock:
            if modelo not in self.modelos_claude:
                return {"ok": False, "atual": self.atual, "modelo": self.modelo_claude,
                        "erro": f"modelo desconhecido (use {', '.join(self.modelos_claude)})"}
            alvo = self.atual if self.atual in IDS_CLAUDE else self.resolver("claude")
            if alvo is None:
                return {"ok": False, "atual": self.atual, "modelo": self.modelo_claude, "erro": "o Claude não está disponível"}
            if modelo == self.modelo_claude and alvo == self.atual and alvo in self._objetos:
                return {"ok": True, "atual": self.atual, "modelo": modelo, "erro": None}
            anterior_modelo, anterior_atual = self.modelo_claude, self.atual
            self._aplicar_modelo(modelo)
            novo = None
            try:
                novo = self._fabricas[alvo]()
                novo.aquecer()
                if getattr(novo, "usando_reserva", False):
                    raise RuntimeError(getattr(novo, "ultimo_erro", None) or "não consegui iniciar")
            except Exception as e:
                self._aplicar_modelo(anterior_modelo)
                self._encerrar_objeto(novo)
                return {"ok": False, "atual": self.atual, "modelo": anterior_modelo,
                        "erro": f"{type(e).__name__}: {str(e)[:140]}"}
            for id_ in IDS_CLAUDE:              # os outros cérebros Claude estão no modelo velho: recriados na hora
                velho = self._objetos.pop(id_, None)
                if velho is not None and velho is not novo:
                    self._encerrar_objeto(velho)
            self._objetos[alvo] = novo
            self.atual = alvo
            getattr(novo, "nova_sessao", novo.esquecer)()
            if anterior_atual == "qwen":
                self._liberar("qwen")
            salvar_preferencia(alvo, modelo_claude=modelo)
            return {"ok": True, "atual": alvo, "modelo": modelo, "erro": None}

    @staticmethod
    def _encerrar_objeto(obj):
        principal = getattr(obj, "principal", obj)
        if obj is not None and hasattr(principal, "encerrar"):
            try:
                principal.encerrar()
            except Exception:
                pass

    def resolver(self, alvo: str) -> str | None:
        """'qwen' -> 'qwen'; 'claude' -> a melhor opção Claude disponível (assinatura antes da API)."""
        if alvo == "qwen":
            return "qwen"
        if alvo in ALVOS:
            return ALVOS[alvo] if self._disponibilidade(ALVOS[alvo]) in (None, MOTIVO_LOGIN) else None
        for id_ in ("claude-cli", "claude-api"):
            if self._disponibilidade(id_) is None:
                return id_
        return None

    def _escolha_inicial(self) -> str:
        candidatos = [carregar_preferencia(), MOTOR_PARA_ID.get(self.cfg.get("cerebro", {}).get("motor", "ollama"))]
        for id_ in candidatos:
            if id_ in self._meta() and self._disponibilidade(id_) is None:
                return id_
        return "qwen"

    # ------------------------------------------------------------ fábricas reais
    def _criar_cli(self):
        from jarvis.brain.claude import CerebroComReserva
        from jarvis.brain.claude_cli import CerebroClaudeCLI
        principal = CerebroClaudeCLI(self.cfg, self.executor, self.memoria)
        return CerebroComReserva(principal, self.local) if self.cfg.get("cerebro", {}).get("reserva_local", True) else principal

    def _criar_codex(self):
        from jarvis.brain.claude import CerebroComReserva
        from jarvis.brain.outros_cli import CerebroCodexCLI
        principal = CerebroCodexCLI(self.cfg, self.executor, self.memoria)
        return CerebroComReserva(principal, self.local) if self.cfg.get("cerebro", {}).get("reserva_local", True) else principal

    def _criar_gemini(self):
        from jarvis.brain.claude import CerebroComReserva
        from jarvis.brain.outros_cli import CerebroGeminiCLI
        principal = CerebroGeminiCLI(self.cfg, self.executor, self.memoria)
        return CerebroComReserva(principal, self.local) if self.cfg.get("cerebro", {}).get("reserva_local", True) else principal

    def _criar_api(self):
        from jarvis.brain.claude import CerebroClaude, CerebroComReserva
        principal = CerebroClaude(self.cfg, self.executor, self.memoria)
        return CerebroComReserva(principal, self.local) if self.cfg.get("cerebro", {}).get("reserva_local", True) else principal

    # ------------------------------------------------------------ cérebro ativo
    @property
    def atual(self) -> str:
        return self._atual

    @atual.setter
    def atual(self, id_: str):
        """Com o Claude ativo, o Qwen vira só reserva e não fica morando na GPU."""
        self._atual = id_
        self.local.so_reserva = id_ != "qwen"

    def _ativo(self):
        return self._objetos[self.atual]

    @property
    def rotulo(self) -> str:
        obj = self._objetos.get(self.atual)   # antes do aquecer() o Claude escolhido ainda não existe (a HUD já pergunta)
        if obj is None:
            return self._meta()[self.atual]["nome"]
        return getattr(obj, "rotulo", self.atual)

    @property
    def historico(self):
        return self._ativo().historico

    @property
    def sessao(self):
        return self._ativo().sessao

    def aquecer(self):
        """Prepara o cérebro ativo. Se o Claude escolhido na partida não subir, cai no Qwen em vez de travar."""
        with self._lock:
            if self.atual not in self._objetos:
                self._objetos[self.atual] = self._fabricas[self.atual]()
            try:
                self._ativo().aquecer()
            except Exception as e:
                if self.atual == "qwen":
                    raise
                print(f"[cérebro] {self.atual} não subiu ({type(e).__name__}: {str(e)[:100]}); usando o Qwen local.")
                self.atual = "qwen"
                self._ativo().aquecer()

    def responder(self, texto: str) -> Iterator[str]:
        return self._ativo().responder(texto)

    def cancelar(self):
        for obj in list(self._objetos.values()):
            obj.cancelar()

    def esquecer(self):
        for obj in list(self._objetos.values()):
            obj.esquecer()

    # ------------------------------------------------------------ troca
    def trocar(self, id_: str) -> dict:
        """{'ok': bool, 'atual': id, 'erro': str|None}. Se falhar, o cérebro anterior continua em uso."""
        with self._lock:
            if id_ not in self._meta():
                return {"ok": False, "atual": self.atual, "erro": "opção desconhecida"}
            motivo = self._disponibilidade(id_)
            if motivo:
                return {"ok": False, "atual": self.atual, "erro": motivo, "precisa_login": motivo == MOTIVO_LOGIN}
            if id_ == self.atual:
                return {"ok": True, "atual": self.atual, "erro": None}
            anterior = self.atual
            novo = None
            try:
                if id_ not in self._objetos:
                    self._objetos[id_] = self._fabricas[id_]()
                novo = self._objetos[id_]
                novo.aquecer()                      # valida login/conexão/isolamento ANTES de largar o anterior
                if getattr(novo, "usando_reserva", False):
                    # a reserva assumiu em silêncio: numa troca MANUAL isso é uma falha que o usuário precisa ver
                    raise RuntimeError(getattr(novo, "ultimo_erro", None) or "não consegui iniciar")
            except Exception as e:
                if id_ != "qwen":
                    self._liberar(id_)
                    self._objetos.pop(id_, None)    # a próxima tentativa começa limpa
                return {"ok": False, "atual": anterior, "erro": f"{type(e).__name__}: {str(e)[:140]}"}
            self.atual = id_
            # o escolhido larga o histórico velho dele e recebe a conversa recente do banco: o Jarvis continua um só
            getattr(novo, "nova_sessao", novo.esquecer)()
            self._liberar(anterior)
            salvar_preferencia(id_)
            return {"ok": True, "atual": id_, "erro": None}

    def _liberar(self, id_: str):
        """Quem sai libera recursos: o processo `claude` não fica ligado à toa."""
        obj = self._objetos.get(id_)
        if id_ == "qwen" and hasattr(obj, "descarregar"):
            obj.descarregar()                       # libera a VRAM para a voz clonada e o Whisper
            return
        principal = getattr(obj, "principal", obj)
        if id_ != "qwen" and hasattr(principal, "encerrar"):
            try:
                principal.encerrar()
            except Exception:
                pass
