"""MCP Gateway: o Jarvis como CLIENTE de servidores MCP (Model Context Protocol), por stdio.

Protocolo (spec 2025-06-18): JSON-RPC 2.0, uma mensagem por linha no stdin/stdout do servidor.
    -> initialize {protocolVersion, capabilities, clientInfo}   <- {protocolVersion, capabilities, serverInfo}
    -> notifications/initialized
    -> tools/list {cursor?}                                      <- {tools: [{name, description, inputSchema, annotations?}], nextCursor?}
    -> tools/call {name, arguments}                              <- {content: [...], isError, structuredContent?}
Pedidos do servidor (ping, roots/list...) são respondidos; o resto recebe "método não suportado".

Servidores em data/orquestra/mcp.json, no mesmo formato do Claude Desktop/Claude Code:
    {"mcpServers": {"nome": {"command": "npx", "args": ["-y", "pacote"], "env": {}, "permissoes": {"ferramenta": "livre"}}}}
Permissão por ferramenta: "livre" 🟢 | "registrado" 🟡 | "confirmar" 🔴. Padrão: 🔴; dica readOnlyHint do servidor = 🟡.
O ambiente do servidor sai limpo (sem chaves de outros serviços): só o "env" que você declarou para ele.
"""
import json
import queue
import threading
from pathlib import Path

from jarvis.config import RAIZ
from jarvis.orquestra import gateway
from jarvis.orquestra.seguranca import ambiente_limpo

ARQUIVO = RAIZ / "data" / "orquestra" / "mcp.json"
VERSAO = "2025-06-18"
TIMEOUT_S = 60
NIVEIS = ("livre", "registrado", "confirmar")


class ErroMCP(Exception):
    pass


class ClienteMCP:
    """Uma conexão stdio com um servidor MCP."""

    def __init__(self, nome: str, comando: list[str], env: dict | None = None, cwd: str | None = None):
        self.nome, self.comando, self.env, self.cwd = nome, comando, env or {}, cwd
        self.processo: gateway.Processo | None = None
        self._id = 0
        self._pendentes: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self.info: dict = {}
        self.stderr: list[str] = []

    def conectar(self, timeout: float = 30) -> dict:
        exe = gateway.resolver_executavel(self.comando[0])
        if exe is None:
            raise ErroMCP(f"não achei '{self.comando[0]}' para iniciar o servidor MCP {self.nome}")
        self.processo = gateway.Processo([*exe, *self.comando[1:]], self.cwd, ambiente_limpo(self.env), None,
                                         limite_s=10 ** 9, limite_mem_mb=2048, ao_linha=self._linha,
                                         nome=f"mcp-{self.nome}", prioridade_baixa=False, interativo=True).iniciar()
        if self.processo.estado == "falhou_iniciar":
            raise ErroMCP(self.processo.motivo_fim)
        r = self.pedir("initialize", {"protocolVersion": VERSAO, "capabilities": {},
                                      "clientInfo": {"name": "jarvis", "version": "1.0"}}, timeout)
        self.info = r
        self.notificar("notifications/initialized")
        return r

    def _linha(self, fluxo: str, linha: str):
        if fluxo == "stderr":
            self.stderr = (self.stderr + [linha])[-50:]
            return
        try:
            msg = json.loads(linha)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if "id" in msg and ("result" in msg or "error" in msg):
            fila = self._pendentes.pop(msg["id"], None)
            if fila is not None:
                fila.put(msg)
        elif "method" in msg and "id" in msg:       # pedido do servidor para nós
            if msg["method"] == "ping":
                self._enviar({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
            elif msg["method"] == "roots/list":
                self._enviar({"jsonrpc": "2.0", "id": msg["id"], "result": {"roots": []}})
            else:
                self._enviar({"jsonrpc": "2.0", "id": msg["id"],
                              "error": {"code": -32601, "message": f"o Jarvis não suporta {msg['method']}"}})

    def _enviar(self, msg: dict):
        try:
            self.processo.escrever(json.dumps(msg, ensure_ascii=False) + "\n")
        except (OSError, AttributeError, ValueError):
            raise ErroMCP(f"o servidor MCP {self.nome} não está rodando" +
                          (f": {self.stderr[-1]}" if self.stderr else ""))

    def pedir(self, metodo: str, params: dict | None = None, timeout: float = TIMEOUT_S) -> dict:
        with self._lock:
            self._id += 1
            id_ = self._id
        fila: queue.Queue = queue.Queue()
        self._pendentes[id_] = fila
        self._enviar({"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params or {}})
        try:
            msg = fila.get(timeout=timeout)
        except queue.Empty:
            self._pendentes.pop(id_, None)
            raise ErroMCP(f"o servidor MCP {self.nome} não respondeu a {metodo} em {timeout:.0f}s")
        if "error" in msg:
            e = msg["error"] or {}
            raise ErroMCP(f"{self.nome}: {e.get('message', 'erro')} (código {e.get('code')})")
        return msg.get("result") or {}

    def notificar(self, metodo: str, params: dict | None = None):
        self._enviar({"jsonrpc": "2.0", "method": metodo, "params": params or {}})

    def ferramentas(self) -> list[dict]:
        todas, cursor = [], None
        for _ in range(20):                       # paginação (nextCursor)
            r = self.pedir("tools/list", {"cursor": cursor} if cursor else {})
            todas += r.get("tools") or []
            cursor = r.get("nextCursor")
            if not cursor:
                break
        return todas

    def chamar(self, ferramenta: str, argumentos: dict | None = None, timeout: float = 120) -> dict:
        return self.pedir("tools/call", {"name": ferramenta, "arguments": argumentos or {}}, timeout)

    def fechar(self):
        if self.processo is not None:
            self.processo.fechar_entrada()
            if self.processo.esperar(3) is None:
                self.processo.cancelar("fechado pelo Jarvis")


def texto_do_resultado(r: dict, limite: int = 8000) -> str:
    partes = []
    for c in r.get("content") or []:
        if c.get("type") == "text":
            partes.append(str(c.get("text", "")))
        elif c.get("type") == "resource":
            partes.append(str((c.get("resource") or {}).get("text", ""))[:2000])
        else:
            partes.append(f"[{c.get('type')}]")
    if not partes and r.get("structuredContent") is not None:
        partes.append(json.dumps(r["structuredContent"], ensure_ascii=False))
    texto = "\n".join(partes)
    return texto[:limite]


class GatewayMCP:
    def __init__(self, arquivo: Path | None = None):
        self.arquivo = Path(arquivo or ARQUIVO)
        self.clientes: dict[str, ClienteMCP] = {}
        self.cache_ferramentas: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def config(self) -> dict:
        try:
            return (json.loads(self.arquivo.read_text(encoding="utf-8")).get("mcpServers") or {})
        except (OSError, ValueError):
            return {}

    def salvar_servidor(self, nome: str, comando: str, args: list[str] | None = None, env: dict | None = None):
        if not nome.replace("-", "").replace("_", "").isalnum():
            raise ValueError("nome do servidor: só letras, números, - e _")
        dados = {"mcpServers": self.config()}
        dados["mcpServers"][nome] = {"command": comando, "args": list(args or []), "env": dict(env or {}),
                                     "permissoes": (dados["mcpServers"].get(nome) or {}).get("permissoes", {})}
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        self.arquivo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        self.desconectar(nome)

    def remover_servidor(self, nome: str) -> bool:
        dados = {"mcpServers": self.config()}
        existia = dados["mcpServers"].pop(nome, None) is not None
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        self.arquivo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        self.desconectar(nome)
        return existia

    def cliente(self, nome: str) -> ClienteMCP:
        with self._lock:
            c = self.clientes.get(nome)
            if c is not None and c.processo is not None and c.processo.rodando:
                return c
            cfg = self.config().get(nome)
            if cfg is None:
                raise ErroMCP(f"não há servidor MCP '{nome}' (configure em data/orquestra/mcp.json)")
            c = ClienteMCP(nome, [cfg["command"], *(cfg.get("args") or [])], cfg.get("env") or {}, cfg.get("cwd"))
            c.conectar()
            self.clientes[nome] = c
            return c

    def ferramentas(self, nome: str, forcar: bool = False) -> list[dict]:
        if forcar or nome not in self.cache_ferramentas:
            self.cache_ferramentas[nome] = self.cliente(nome).ferramentas()
        return self.cache_ferramentas[nome]

    def nivel(self, servidor: str, ferramenta: str) -> str:
        cfg = self.config().get(servidor) or {}
        definido = (cfg.get("permissoes") or {}).get(ferramenta)
        if definido in NIVEIS:
            return definido
        for f in self.cache_ferramentas.get(servidor, []):
            if f.get("name") == ferramenta and (f.get("annotations") or {}).get("readOnlyHint") is True:
                return "registrado"
        return "confirmar"

    def chamar(self, servidor: str, ferramenta: str, argumentos: dict | None = None) -> dict:
        nomes = {f.get("name") for f in self.ferramentas(servidor)}
        if ferramenta not in nomes:
            raise ErroMCP(f"o servidor {servidor} não tem a ferramenta '{ferramenta}'")
        return self.cliente(servidor).chamar(ferramenta, argumentos)

    def estado(self) -> list[dict]:
        saida = []
        for nome, cfg in self.config().items():
            c = self.clientes.get(nome)
            ligado = bool(c and c.processo and c.processo.rodando)
            saida.append({"nome": nome, "comando": cfg.get("command"), "args": cfg.get("args") or [],
                          "conectado": ligado, "servidor": (c.info.get("serverInfo") if ligado else None),
                          "ferramentas": [{"nome": f.get("name"), "descricao": (f.get("description") or "")[:200],
                                           "nivel": self.nivel(nome, f.get("name"))}
                                          for f in self.cache_ferramentas.get(nome, [])]})
        return saida

    def desconectar(self, nome: str | None = None):
        with self._lock:
            for n in ([nome] if nome else list(self.clientes)):
                c = self.clientes.pop(n, None)
                self.cache_ferramentas.pop(n, None)
                if c is not None:
                    c.fechar()


_gateway: GatewayMCP | None = None


def gateway_mcp() -> GatewayMCP:
    global _gateway
    if _gateway is None:
        _gateway = GatewayMCP()
    return _gateway
