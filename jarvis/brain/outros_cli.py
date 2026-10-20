"""Cérebros pelo login de outras CLIs: ChatGPT (Codex CLI, conta do ChatGPT) e Gemini (Gemini CLI, conta Google).

Mesmo desenho do cérebro `claude-cli`:
  JARVIS ──► processo da CLI ──(MCP stdio)──► ponte_mcp.py ──(HTTP local + token)──► Executor do JARVIS
A CLI só decide o que pedir. Quem executa, classifica 🟢🟡🔴, pede confirmação e registra é o Executor.

Diferença: estas CLIs não têm um modo "processo persistente com entrada em stream" como o `claude`, então cada
pedido roda um processo novo. A continuidade vem da sessão da própria CLI (`codex exec resume <id>`) ou, no Gemini,
da conversa recente do banco que vai junto com o pedido.

Isolamento (fail-closed), porque as duas CLIs vêm com terminal, edição de arquivos, navegador e busca próprios:
  Codex   --ignore-user-config (nenhum servidor MCP seu), sandbox read-only, approval_policy=never, sem busca na web
          e com terminal/navegador/apps/plugins/agentes DESLIGADOS (--disable <recurso>);
  Gemini  pasta própria com .gemini/settings.json (só o servidor MCP "jarvis"), --allowed-mcp-server-names jarvis,
          política que nega toda ferramenta que não seja do servidor jarvis;
  e nos dois: qualquer evento de ferramenta que não seja do Jarvis encerra o processo e o pedido falha (a reserva
  local assume). Conferido nas opções das CLIs instaladas (codex 0.163, gemini 0.64); sem login aqui, a conversa
  real ainda não foi testada.
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator

from jarvis.brain.claude import ErroCerebro
from jarvis.brain.claude_cli import PonteHTTP, dividir_frases
from jarvis.brain.llm import PROMPT_VISAO, Cerebro
from jarvis.config import RAIZ

ANEXO = ("\n\nComo você roda: suas únicas ferramentas são as do servidor MCP 'jarvis' (hora, pesquisar_web, "
         "pesquisar_a_fundo, abrir programas, arquivos, mouse...). Use-as para agir; você não tem terminal nem outro "
         "acesso ao computador. Se uma ação for perigosa, a própria ferramenta pede a confirmação do usuário. "
         "Responda em português, curto, para ser falado em voz alta (sem markdown, sem listas longas).")
# recursos do Codex que dariam a ele um caminho próprio até o computador ou a internet
CODEX_DESLIGAR = ("shell_tool", "unified_exec", "unified_exec_tty", "shell_snapshot", "apps", "browser_use",
                  "browser_use_external", "browser_use_full_cdp_access", "in_app_browser", "computer_use",
                  "image_generation", "multi_agent", "plugins", "remote_plugin", "hooks", "goals", "skill_search",
                  "skill_mcp_dependency_install", "tool_suggest", "worktrees", "workspace_dependencies")
CODEX_ITENS_OK = {"agent_message", "reasoning", "todo_list", "error"}
VARIAVEIS_REMOVIDAS = ("OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
                       "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")


def _toml(valor: str) -> str:
    """String literal TOML (aspas simples: barras do Windows passam sem escapar)."""
    if "'" in valor or "\n" in valor:
        return json.dumps(valor)
    return f"'{valor}'"


def executavel(nome: str, configurado: str | list | None = None) -> list[str] | None:
    from jarvis.orquestra import gateway
    return gateway.resolver_executavel(nome, configurado)


class _Isolamento(Exception):
    pass


class _Cancelado(Exception):
    pass


class _CerebroPorPedido(Cerebro):
    """Base: um processo da CLI por pedido, eventos JSON por linha, ferramentas só pela ponte."""
    usa_conhecimento = True
    nome_cli = ""
    secao_cfg = ""

    def __init__(self, cfg: dict, executor=None, memoria=None, comando: list[str] | None = None):
        super().__init__(cfg, executor, memoria)
        self.c = dict(cfg.get(self.secao_cfg, {}) or {})
        self.modelo_cli = str(self.c.get("modelo") or "")
        self.timeout_s = float(self.c.get("timeout_s", 180))
        self.cmd = comando or executavel(self.nome_cli, self.c.get("comando"))
        if not self.cmd:
            raise RuntimeError(f"não achei o programa '{self.nome_cli}'")
        if executor is None:
            raise RuntimeError("este cérebro precisa do Executor (as ferramentas passam por ele)")
        self.pasta = RAIZ / "data" / f"{self.secao_cfg}_cerebro"
        (self.pasta / "cwd").mkdir(parents=True, exist_ok=True)       # pasta vazia: nenhum AGENTS.md/GEMINI.md
        self.ponte = PonteHTTP(executor)
        self.nomes_jarvis = {s["function"]["name"] for s in executor.schemas(visao=True)} if hasattr(executor, "schemas") else set()
        self.limite_continuidade = 5000
        self.sessao_cli: str | None = None
        self.proc: subprocess.Popen | None = None
        self.stderr: list[str] = []
        self.rotulo = self.nome_exibicao + (f" · {self.modelo_cli}" if self.modelo_cli else "")

    # ------------------------------------------------------------ peças de cada CLI
    nome_exibicao = ""

    def argumentos(self, retomar: str | None) -> list[str]:
        raise NotImplementedError

    def tratar(self, ev: dict, estado: dict):
        """Atualiza `estado` (texto, ferramentas, sessao, fim, erro). Levanta _Isolamento se fugir da regra."""
        raise NotImplementedError

    # ------------------------------------------------------------ comum
    def _ambiente(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in VARIAVEIS_REMOVIDAS and not k.startswith("CLAUDE_CODE_")}
        env.update({"PYTHONUTF8": "1", "NO_COLOR": "1"})
        return env

    def _rodar(self, prompt: str, retomar: str | None) -> Iterator[tuple[str, dict]]:
        """Roda um pedido; produz ('evento', ev) até o fim. Encerra o processo ao cancelar ou estourar o tempo."""
        argv = [*self.cmd, *self.argumentos(retomar)]
        self.stderr = []
        self.proc = proc = subprocess.Popen(
            argv, cwd=str(self.pasta / "cwd"), env=self._ambiente(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        fila: queue.Queue = queue.Queue()

        def ler():
            for linha in proc.stdout:
                linha = linha.strip()
                if linha.startswith("{"):
                    try:
                        fila.put(json.loads(linha))
                    except ValueError:
                        pass
            fila.put(None)

        def erros():
            for linha in proc.stderr:
                self.stderr = (self.stderr + [linha.rstrip()])[-20:]

        threading.Thread(target=ler, daemon=True).start()
        threading.Thread(target=erros, daemon=True).start()
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass
        ultimo = time.time()
        try:
            while True:
                if self._cancelado.is_set():
                    raise _Cancelado()
                try:
                    ev = fila.get(timeout=0.2)
                except queue.Empty:
                    if time.time() - ultimo > self.timeout_s:
                        raise ErroCerebro(f"o {self.nome_exibicao} ficou {self.timeout_s:.0f}s sem responder")
                    continue
                if ev is None:
                    return
                ultimo = time.time()
                yield ev
        finally:
            self.encerrar()

    def encerrar(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            try:
                p.kill()
                p.wait(timeout=5)
            except Exception:
                pass

    def cancelar(self):
        super().cancelar()
        self.encerrar()

    def nova_sessao(self):
        self.encerrar()
        self.sessao_cli = None
        self.historico.clear()

    def _falha(self, motivo: str) -> ErroCerebro:
        detalhe = " | ".join(self.stderr[-3:])
        return ErroCerebro(motivo + (f" ({detalhe[:200]})" if detalhe else ""))

    def logado(self) -> tuple[bool, str]:
        raise NotImplementedError

    def aquecer(self):
        """Confere o programa e o login (rápido, sem gastar a cota). A primeira conversa valida o resto."""
        ok, detalhe = self.logado()
        if not ok:
            raise ErroCerebro(detalhe)

    def nome_ferramenta(self, bruto: str) -> str | None:
        """O nome da ferramenta do Jarvis por trás do nome que a CLI usou; None = não é do Jarvis."""
        for prefixo in ("mcp__jarvis__", "mcp_jarvis_", "jarvis__", "jarvis."):
            if bruto.startswith(prefixo):
                bruto = bruto[len(prefixo):]
                break
        return bruto if bruto in self.nomes_jarvis else None

    def responder(self, texto: str) -> Iterator[str]:
        self._cancelado.clear()
        if self.executor:
            self.executor.novo_turno()
        retomar = self.sessao_cli if self.c.get("continuar_sessao", True) else None
        contexto = self._contexto(texto, sessao_nova=retomar is None)
        prompt = (f"{self.sistema}{PROMPT_VISAO}{ANEXO}\n\n" if retomar is None else "") + f"{contexto}\n\n{texto}"
        estado = {"texto": [], "ferramentas": [], "sessao": None, "fim": False, "erro": ""}
        falado: list[str] = []
        try:
            for ev in self._rodar(prompt, retomar):
                ja = len(estado["texto"])
                self.tratar(ev, estado)
                for pedaco in estado["texto"][ja:]:       # cada mensagem pronta vira frases faladas
                    for frase in dividir_frases(pedaco):
                        falado.append(frase)
                        yield frase
        except _Cancelado:
            return
        except _Isolamento as e:
            self.sessao_cli = None
            raise ErroCerebro(f"isolamento do {self.nome_exibicao} falhou ({e}); recusando usá-lo")
        if self._cancelado.is_set():                    # você mandou parar: o processo morreu por isso
            return
        if estado["sessao"]:
            self.sessao_cli = estado["sessao"]
        if estado["erro"] or not estado["fim"]:
            if retomar and not falado:                  # sessão velha que não abre mais: a próxima recomeça limpa
                self.sessao_cli = None
            if not falado:
                raise self._falha(estado["erro"] or f"o {self.nome_exibicao} terminou sem responder")
        self._registrar(texto, falado, estado["ferramentas"])


# ---------------------------------------------------------------- ChatGPT pelo Codex CLI
class CerebroCodexCLI(_CerebroPorPedido):
    nome_cli = "codex"
    secao_cfg = "codex_cli"
    nome_exibicao = "ChatGPT"

    def argumentos(self, retomar: str | None) -> list[str]:
        ponte = str(RAIZ / "jarvis" / "brain" / "ponte_mcp.py")
        env = (f"{{JARVIS_PONTE_URL={_toml(self.ponte.url)},JARVIS_PONTE_TOKEN={_toml(self.ponte.token)},"
               f"PYTHONUTF8='1'}}")
        config = [f"approval_policy='never'", "sandbox_mode='read-only'", "web_search='disabled'",
                  f"mcp_servers.jarvis.command={_toml(sys.executable)}",
                  f"mcp_servers.jarvis.args=['-X','utf8',{_toml(ponte)}]",
                  f"mcp_servers.jarvis.env={env}",
                  "mcp_servers.jarvis.startup_timeout_sec=30", "mcp_servers.jarvis.tool_timeout_sec=300"]
        a = ["exec"]
        if retomar:
            a += ["resume", retomar]
        a += ["--json", "--skip-git-repo-check", "--ignore-user-config"]
        if not retomar:
            a += ["-C", str(self.pasta / "cwd")]
        for c in config:
            a += ["-c", c]
        for recurso in self.c.get("desligar", CODEX_DESLIGAR):
            a += ["--disable", recurso]
        if self.modelo_cli:
            a += ["-m", self.modelo_cli]
        return a + ["-"]

    def tratar(self, ev: dict, estado: dict):
        tipo, item = ev.get("type"), ev.get("item") or {}
        if tipo == "thread.started":
            estado["sessao"] = ev.get("thread_id")
        elif tipo in ("item.started", "item.completed"):
            it = item.get("type")
            if it == "mcp_tool_call":
                nome = self.nome_ferramenta(str(item.get("tool", "")))
                if item.get("server") != "jarvis" or nome is None:
                    raise _Isolamento(f"ferramenta de fora: {item.get('server')}/{item.get('tool')}")
                if tipo == "item.started":
                    estado["ferramentas"].append(nome)
            elif it not in CODEX_ITENS_OK:
                raise _Isolamento(f"o Codex tentou usar '{it}'")
            elif tipo == "item.completed" and it == "agent_message" and str(item.get("text", "")).strip():
                estado["texto"].append(str(item["text"]))
        elif tipo == "turn.completed":
            estado["fim"] = True
        elif tipo == "turn.failed":
            estado["erro"] = str((ev.get("error") or {}).get("message", "o turno falhou"))
        # "error" sozinho também é aviso de reconexão: quem decide é o turno

    def logado(self) -> tuple[bool, str]:
        try:
            r = subprocess.run([*self.cmd, "login", "status"], capture_output=True, text=True, timeout=30,
                               env=self._ambiente(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"o Codex não respondeu ({type(e).__name__})"
        texto = (r.stdout + " " + r.stderr).lower()
        if "not logged in" in texto:
            return False, "o ChatGPT (Codex) está sem login"
        return True, "logado"


# ---------------------------------------------------------------- Gemini CLI
POLITICA_GEMINI = """# Jarvis: o Gemini só usa as ferramentas do servidor MCP "jarvis". Todo o resto é negado.
[[rule]]
toolName = "*"
decision = "deny"
priority = 900
denyMessage = "No Jarvis você só usa as ferramentas do servidor jarvis."

[[rule]]
mcpName = "jarvis"
toolName = "*"
decision = "allow"
priority = 950
"""


class CerebroGeminiCLI(_CerebroPorPedido):
    nome_cli = "gemini"
    secao_cfg = "gemini_cli"
    nome_exibicao = "Gemini"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._preparar_pasta()

    def _preparar_pasta(self):
        ponte = str(RAIZ / "jarvis" / "brain" / "ponte_mcp.py")
        conf = self.pasta / "cwd" / ".gemini"
        conf.mkdir(parents=True, exist_ok=True)
        (conf / "settings.json").write_text(json.dumps({
            "mcpServers": {"jarvis": {"command": sys.executable, "args": ["-X", "utf8", ponte], "trust": True,
                                      "timeout": 300000,
                                      "env": {"JARVIS_PONTE_URL": self.ponte.url, "JARVIS_PONTE_TOKEN": self.ponte.token,
                                              "PYTHONUTF8": "1"}}},
            "mcp": {"allowed": ["jarvis"]}}, indent=2), encoding="utf-8")
        (self.pasta / "politica.toml").write_text(POLITICA_GEMINI, encoding="utf-8")

    def argumentos(self, retomar: str | None) -> list[str]:
        a = ["-p", "", "-o", "stream-json", "--skip-trust", "--allowed-mcp-server-names", "jarvis",
             "--policy", str(self.pasta / "politica.toml"), "--approval-mode", "default", "-e", "none"]
        if self.modelo_cli:
            a += ["-m", self.modelo_cli]
        return a

    def tratar(self, ev: dict, estado: dict):
        tipo = ev.get("type")
        if tipo == "init":
            estado["sessao"] = ev.get("session_id")
        elif tipo == "message" and ev.get("role") == "assistant":
            estado.setdefault("buffer", "")
            estado["buffer"] += str(ev.get("content", ""))
            # o Gemini manda pedaços: solta só frases inteiras
            if any(estado["buffer"].rstrip().endswith(c) for c in ".!?:") and len(estado["buffer"]) > 40:
                estado["texto"].append(estado.pop("buffer"))
        elif tipo == "tool_use":
            nome = self.nome_ferramenta(str(ev.get("tool_name", "")))
            if nome is None:
                raise _Isolamento(f"ferramenta de fora: {ev.get('tool_name')}")
            estado["ferramentas"].append(nome)
        elif tipo == "result":
            if estado.get("buffer"):
                estado["texto"].append(estado.pop("buffer"))
            if ev.get("status") == "success":
                estado["fim"] = True
            else:
                estado["erro"] = str((ev.get("error") or {}).get("message", "o Gemini falhou"))
        elif tipo == "error" and ev.get("severity") == "error":
            estado["erro"] = str(ev.get("message", "erro"))

    def responder(self, texto: str) -> Iterator[str]:
        self.sessao_cli = None             # o Gemini recebe a conversa recente do banco a cada pedido
        return super().responder(texto)

    def logado(self) -> tuple[bool, str]:
        casa = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"
        if (casa / "oauth_creds.json").exists():
            return True, "logado com a conta Google"
        return False, "o Gemini está sem login (entre uma vez com a conta Google)"


def disponivel(nome: str, configurado=None) -> bool:
    return executavel(nome, configurado) is not None or shutil.which(nome) is not None


def novo_id() -> str:
    return str(uuid.uuid4())
