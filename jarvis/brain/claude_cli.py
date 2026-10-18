"""Cérebro Claude pelo CLI `claude` (usa a SUA assinatura, sem chave de API).

Como funciona:
  JARVIS ──(stream-json)──► processo `claude` persistente ──(MCP stdio)──► ponte_mcp.py ──(HTTP local + token)──► JARVIS
O `claude` só decide o que pedir. Quem executa, classifica 🟢🟡🔴, pede confirmação e registra é o Executor do JARVIS,
chamado pela ponte. Por isso as ferramentas do próprio `claude` (Bash, Edit...) têm de estar DESLIGADAS.

Isolamento (fail-closed): o processo sobe com --restricted --strict-mcp-config --tools "" --disable-slash-commands e
o evento `init` é conferido: se aparecer qualquer ferramenta que não seja `mcp__jarvis__*`, um servidor MCP seu
(Gmail, Supabase...), skill ou plugin externo, o JARVIS RECUSA usar este cérebro e cai no modelo local.

Aviso: usar a assinatura assim é uma zona cinza da política da Anthropic (veja o README). Uso pessoal, programa
`claude` original, sem revender nem intermediar credenciais.
"""
import atexit
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

from jarvis.brain.claude import ErroCerebro
from jarvis.brain.llm import _FIM_FRASE, _LIMPAR, _TRAVESSAO, PROMPT_SISTEMA, PROMPT_VISAO, Cerebro
from jarvis.config import RAIZ

PREFIXO = "mcp__jarvis__"
ANEXO_SISTEMA = (
    "\n\nComo você roda: suas ferramentas aparecem com o prefixo mcp__jarvis__ (ex.: mcp__jarvis__hora_data); chame-as "
    "por esse nome completo. Você não tem acesso a nenhuma outra ferramenta nem ao terminal por outro caminho. "
    "Se uma ação for perigosa, a própria ferramenta pede a confirmação do usuário; não pergunte antes."
)
VARIAVEIS_REMOVIDAS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")  # a ideia aqui é usar a assinatura, não uma chave


# ---------------------------------------------------------------- isolamento
def validar_init(ev: dict) -> str | None:
    """None se o `claude` está confinado às ferramentas do JARVIS; senão, o motivo (e o JARVIS recusa rodar)."""
    ferramentas = ev.get("tools") or []
    fora = [t for t in ferramentas if not str(t).startswith(PREFIXO)]
    if fora:
        return f"ferramentas fora do JARVIS ligadas: {fora[:5]}"
    servidores = [s.get("name") if isinstance(s, dict) else str(s) for s in (ev.get("mcp_servers") or [])]
    if any(s != "jarvis" for s in servidores):
        return f"servidores MCP de terceiros carregados: {servidores}"
    if ev.get("skills"):
        return f"skills carregadas: {ev.get('skills')}"
    externos = [p for p in (ev.get("plugins") or []) if isinstance(p, dict) and p.get("path") != "builtin"]
    if externos:
        return f"plugins externos carregados: {[p.get('name') for p in externos]}"
    return None


def dividir_frases(texto: str) -> list[str]:
    """Mesmo corte usado no streaming, para quando o texto chega inteiro (sem pedaços parciais)."""
    texto, saida = _TRAVESSAO.sub(", ", texto), []
    while (m := _FIM_FRASE.search(texto)):
        frase, texto = texto[: m.end()], texto[m.end():]
        frase = _LIMPAR.sub("", frase).strip()
        if len(frase) > 1:
            saida.append(frase)
    resto = _LIMPAR.sub("", texto).strip()
    return saida + ([resto] if resto else [])


def schema_para_mcp(schemas_ollama: list[dict]) -> list[dict]:
    saida = []
    for s in schemas_ollama:
        f = s["function"]
        entrada = dict(f.get("parameters") or {})
        entrada.setdefault("type", "object")
        entrada.setdefault("properties", {})
        saida.append({"name": f["name"], "description": f["description"], "inputSchema": entrada})
    return saida


# ---------------------------------------------------------------- ponte HTTP (lado do JARVIS)
class PonteHTTP:
    """Recebe as chamadas de ferramenta vindas da ponte MCP e as entrega ao Executor. Só 127.0.0.1, com token."""

    def __init__(self, executor):
        self.executor = executor
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()  # o Executor é de um pedido por vez; chamadas paralelas esperam a vez
        ponte = self

        class Handler(BaseHTTPRequestHandler):
            def _autorizado(self) -> bool:
                host = self.headers.get("Host", "")
                token = self.headers.get("X-Jarvis-Token", "")
                return host == f"127.0.0.1:{ponte.porta}" and secrets.compare_digest(token, ponte.token)

            def _json(self, codigo: int, corpo: dict):
                dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)

            def do_GET(self):
                if not self._autorizado():
                    return self._json(403, {"erro": "não autorizado"})
                if self.path == "/ferramentas":
                    return self._json(200, {"ferramentas": ponte.ferramentas()})
                self._json(404, {"erro": "não encontrado"})

            def do_POST(self):
                if not self._autorizado():
                    return self._json(403, {"erro": "não autorizado"})
                if self.path != "/executar":
                    return self._json(404, {"erro": "não encontrado"})
                try:
                    tam = int(self.headers.get("Content-Length", "0"))
                    if tam > 200_000:
                        return self._json(413, {"erro": "grande demais"})
                    pedido = json.loads(self.rfile.read(tam))
                    nome, args = pedido["nome"], pedido.get("args") or {}
                    if not isinstance(nome, str) or not isinstance(args, dict):
                        raise ValueError("formato")
                except Exception:
                    return self._json(400, {"erro": "pedido inválido"})
                texto = ponte.executar(nome, args)
                corpo = {"texto": str(texto), "erro": texto.startswith(("Erro", "Ação perigosa bloqueada",
                                                                         "O usuário NÃO autorizou"))}
                if getattr(texto, "imagem", None):      # ver_tela / mouse: o print vai junto para o Claude enxergar
                    corpo["imagem"] = {"dados": texto.imagem, "tipo": texto.tipo}
                self._json(200, corpo)

            def log_message(self, *a):
                pass

        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.servidor.daemon_threads = True
        self.porta = self.servidor.server_address[1]
        threading.Thread(target=self.servidor.serve_forever, daemon=True, name="ponte-http").start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.porta}"

    def ferramentas(self) -> list[dict]:
        return schema_para_mcp(self.executor.schemas(visao=True)) if self.executor else []

    def executar(self, nome: str, args: dict) -> str:
        with self._lock:
            return self.executor.executar(nome, args)

    def parar(self):
        self.servidor.shutdown()
        self.servidor.server_close()


# ---------------------------------------------------------------- cérebro
class CerebroClaudeCLI(Cerebro):
    usa_conhecimento = True
    def __init__(self, cfg: dict, executor=None, memoria=None, executavel: str | None = None):
        super().__init__(cfg, executor, memoria)
        c = cfg.get("claude_cli", {})
        self.modelo_cli = c.get("modelo", "sonnet")           # apelidos do CLI: sonnet | opus | haiku | fable
        self.esforco = c.get("esforco", "low")
        self.timeout_s = c.get("timeout_s", 120)
        self.max_turnos_cli = c.get("max_turnos_por_pedido", 12)
        self.exe = executavel or c.get("executavel") or shutil.which("claude")
        if not self.exe:
            raise RuntimeError("não achei o programa 'claude' (instale o Claude Code e faça login)")
        if executor is None:
            raise RuntimeError("o cérebro claude-cli precisa do Executor (as ferramentas passam por ele)")
        self.rotulo = f"claude · {self.modelo_cli}"
        self.pasta = RAIZ / "data" / "claude_cli"
        (self.pasta / "cwd").mkdir(parents=True, exist_ok=True)   # pasta vazia: nenhum CLAUDE.md de projeto
        (self.pasta / "sistema.txt").write_text(self.sistema + PROMPT_VISAO + ANEXO_SISTEMA, encoding="utf-8")
        self.ponte = PonteHTTP(executor)
        self.limite_continuidade = 5000
        self._sessao_nova = True       # processo recém-aberto: o 1º pedido leva a conversa recente do banco
        self._drenar = False           # um pedido foi interrompido: descartar o resto dele antes do próximo
        self._custo_processo = 0.0     # o 'total_cost_usd' do claude é acumulado por processo
        pasta_logs = Path(executor.log).parent if hasattr(executor, "log") else RAIZ / "data" / "logs"
        self.arquivo_uso = pasta_logs / "uso_claude_cli.jsonl"
        self.proc: subprocess.Popen | None = None
        self.fila: queue.Queue = queue.Queue()
        self.stderr_recente: deque = deque(maxlen=20)
        self.init: dict | None = None
        atexit.register(self.encerrar)

    # ------------------------------------------------------------ processo
    def _comando(self) -> list[str]:
        ponte_py = str(RAIZ / "jarvis" / "brain" / "ponte_mcp.py")
        mcp = {"mcpServers": {"jarvis": {
            "command": sys.executable, "args": ["-X", "utf8", ponte_py],
            "env": {"JARVIS_PONTE_URL": self.ponte.url, "JARVIS_PONTE_TOKEN": self.ponte.token, "PYTHONUTF8": "1"}}}}
        cmd = [self.exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages",
               "--restricted", "--strict-mcp-config", "--mcp-config", json.dumps(mcp), "--tools", "",
               "--disable-slash-commands", "--no-session-persistence",
               "--permission-mode", "dontAsk", "--allowedTools", "mcp__jarvis",
               "--system-prompt-file", str(self.pasta / "sistema.txt"),
               "--model", self.modelo_cli, "--max-turns", str(self.max_turnos_cli)]
        if self.esforco:
            cmd += ["--effort", self.esforco]
        return cmd

    def _iniciar(self):
        self.encerrar()
        env = {k: v for k, v in os.environ.items() if k not in VARIAVEIS_REMOVIDAS}
        env.update({"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1",
                    "MCP_TOOL_TIMEOUT": "300000", "MCP_TIMEOUT": "30000", "PYTHONUTF8": "1"})
        self.proc = subprocess.Popen(
            self._comando(), cwd=str(self.pasta / "cwd"), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.fila = queue.Queue()
        self.init = None
        self._sessao_nova, self._drenar, self._custo_processo = True, False, 0.0
        proc, fila = self.proc, self.fila

        def ler_saida():
            for linha in proc.stdout:
                try:
                    fila.put(json.loads(linha))
                except ValueError:
                    continue
            fila.put(None)  # fim: o processo morreu

        def ler_erro():
            for linha in proc.stderr:
                self.stderr_recente.append(linha.rstrip())

        threading.Thread(target=ler_saida, daemon=True, name="claude-saida").start()
        threading.Thread(target=ler_erro, daemon=True, name="claude-erro").start()

    def encerrar(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.stdin.close()
            except Exception:
                pass
            p.kill()
            try:
                p.wait(timeout=5)
            except Exception:
                pass

    def _vivo(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _enviar(self, texto: str):
        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": texto}]}}
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _proximo(self, limite_s: float) -> dict | None:
        """Próximo evento, ou None se o processo morreu. Confere o isolamento no `init`. Estoura em ErroCerebro."""
        fim = time.time() + limite_s
        while True:
            if self._cancelado.is_set():
                raise _Cancelado()
            try:
                ev = self.fila.get(timeout=0.2)
            except queue.Empty:
                if time.time() > fim:
                    self.encerrar()
                    raise ErroCerebro(f"o claude ficou {limite_s:.0f}s sem responder")
                continue
            if ev is None:
                return None
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                self.init = ev
                motivo = validar_init(ev)
                if motivo:
                    self.encerrar()
                    raise ErroCerebro(f"isolamento do claude falhou ({motivo}); recusando usá-lo")
            return ev

    def _falha(self, motivo: str) -> ErroCerebro:
        detalhe = " | ".join(list(self.stderr_recente)[-3:])
        return ErroCerebro(f"{motivo}" + (f" ({detalhe[:200]})" if detalhe else ""))

    # ------------------------------------------------------------ ciclo de vida
    def aquecer(self):
        """Sobe o processo, confere o isolamento e faz uma chamada mínima (valida o login e prepara o cache)."""
        self._cancelado.clear()
        self._iniciar()
        self._enviar("Responda somente: ok.")
        while True:
            try:
                ev = self._proximo(self.timeout_s + 30)
            except _Cancelado:
                raise ErroCerebro("cancelado durante a partida")
            if ev is None:
                raise self._falha("o claude encerrou na partida (login feito? rode 'claude auth status')")
            if ev.get("type") == "result":
                self._registrar_uso(ev, "partida")
                if ev.get("is_error"):
                    self.encerrar()
                    raise self._falha(f"o claude recusou o pedido: {str(ev.get('result'))[:150]}")
                return

    def nova_sessao(self):
        self.encerrar()  # o próximo pedido sobe outro processo, que recebe a conversa recente do banco
        self.historico.clear()

    # ------------------------------------------------------------ interrupção sem perder a conversa
    def _interromper(self):
        """Você falou por cima ou mandou parar: interrompe o pedido em andamento SEM matar o processo.
        A conversa continua no mesmo `claude` (antes ele era encerrado e tudo recomeçava do zero)."""
        try:
            self.proc.stdin.write(json.dumps({"type": "control_request", "request_id": f"int-{secrets.token_hex(4)}",
                                              "request": {"subtype": "interrupt"}}) + "\n")
            self.proc.stdin.flush()
            self._drenar = True
        except Exception:
            self.encerrar()

    def _drenar_interrompido(self, limite_s: float = 15):
        """Descarta o resto do pedido interrompido (até o 'result') antes de mandar o próximo."""
        self._drenar = False
        fim = time.time() + limite_s
        while time.time() < fim:
            try:
                ev = self.fila.get(timeout=0.2)
            except queue.Empty:
                continue
            if ev is None:
                break
            if ev.get("type") == "result":
                self._registrar_uso(ev, "interrompido")
                return
        self.encerrar()   # não terminou a tempo: recomeça o processo (a continuidade vem do banco)

    # ------------------------------------------------------------ uso da assinatura
    def _registrar_uso(self, ev: dict, tipo: str = "pedido"):
        """Cada pedido em data/logs/uso_claude_cli.jsonl: tokens e o custo equivalente em preço de API.
        Na assinatura não há cobrança por pedido, mas é isso que consome o limite de uso."""
        u = ev.get("usage") or {}
        total = float(ev.get("total_cost_usd") or 0.0)
        custo, self._custo_processo = max(0.0, total - self._custo_processo), max(total, self._custo_processo)
        registro = {"quando": datetime.now().isoformat(timespec="seconds"), "modelo": self.modelo_cli, "tipo": tipo,
                    "entrada": u.get("input_tokens", 0) or 0, "cache_lido": u.get("cache_read_input_tokens", 0) or 0,
                    "cache_escrito": u.get("cache_creation_input_tokens", 0) or 0, "saida": u.get("output_tokens", 0) or 0,
                    "chamadas": ev.get("num_turns", 1), "custo_api_usd": round(custo, 5),
                    "duracao_ms": ev.get("duration_ms", 0)}
        try:
            self.arquivo_uso.parent.mkdir(parents=True, exist_ok=True)
            with open(self.arquivo_uso, "a", encoding="utf-8") as f:
                f.write(json.dumps(registro) + "\n")
        except OSError:
            pass  # telemetria de uso nunca atrapalha a conversa

    # ------------------------------------------------------------ pedido
    def responder(self, texto: str) -> Iterator[str]:
        self._cancelado.clear()
        if self._drenar and self._vivo():
            self._drenar_interrompido()
        if not self._vivo() or self.init is None:
            self.aquecer()                       # primeira vez, depois de esquecer ou de uma queda
        if self.executor:
            self.executor.novo_turno()
        sessao_nova, self._sessao_nova = self._sessao_nova, False
        contexto = self._contexto(texto, sessao_nova)
        self._enviar(f"{contexto}\n\n{texto}")
        falado: list[str] = []
        usadas: list[str] = []
        vistos: set[str] = set()
        buffer = ""
        streamou = False
        try:
            while True:
                ev = self._proximo(self.timeout_s)
                if ev is None:
                    self.encerrar()
                    raise self._falha("o claude encerrou no meio do pedido")
                tipo = ev.get("type")
                if tipo == "stream_event":
                    d = (ev.get("event") or {}).get("delta") or {}
                    if d.get("type") == "text_delta":
                        streamou = True
                        buffer = _TRAVESSAO.sub(", ", buffer + d.get("text", ""))
                        while (m := _FIM_FRASE.search(buffer)):
                            frase, buffer = buffer[: m.end()], buffer[m.end():]
                            frase = _LIMPAR.sub("", frase).strip()
                            if len(frase) > 1:
                                falado.append(frase)
                                yield frase
                elif tipo == "assistant":
                    for b in (ev.get("message") or {}).get("content") or []:
                        if b.get("type") == "tool_use" and b.get("id") not in vistos:
                            vistos.add(b.get("id"))
                            usadas.append(str(b.get("name", "")).removeprefix(PREFIXO))
                elif tipo == "result":
                    self._registrar_uso(ev)
                    resto = _LIMPAR.sub("", buffer).strip()
                    if resto:
                        falado.append(resto)
                        yield resto
                    if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                        sub = ev.get("subtype")
                        if sub == "error_max_turns":
                            frase = "Não consegui concluir isso, senhor."
                            falado.append(frase)
                            yield frase
                        else:
                            raise self._falha(f"erro do claude: {str(ev.get('result') or sub)[:150]}")
                    elif not streamou and not falado and ev.get("result"):
                        for frase in dividir_frases(str(ev["result"])):
                            falado.append(frase)
                            yield frase
                    break
        except _Cancelado:
            self._interromper()                  # abandona só este pedido; a conversa continua no mesmo processo
            return
        self._registrar(texto, falado, usadas)


class _Cancelado(Exception):
    pass
