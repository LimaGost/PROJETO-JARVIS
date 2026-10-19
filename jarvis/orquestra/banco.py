"""Banco do AI Command Center (SQLite, data/orquestra/orquestra.db): execuções, passos, logs (auditoria de comandos),
desempenho dos agentes (para o roteador aprender), gastos, projetos autorizados, terminal e preferências."""
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from jarvis.config import RAIZ

ARQUIVO = RAIZ / "data" / "orquestra" / "orquestra.db"
MAX_LOG = 4000
STATUS_FINAIS = ("concluida", "falhou", "cancelada", "desfeita", "interrompida")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS execucoes (
    id INTEGER PRIMARY KEY, pedido TEXT NOT NULL, projeto TEXT, modo TEXT NOT NULL, prioridade TEXT DEFAULT 'normal',
    status TEXT NOT NULL, plano TEXT NOT NULL, criada TEXT NOT NULL, inicio TEXT, fim TEXT, origem TEXT DEFAULT 'hud',
    custo_usd REAL DEFAULT 0, cobrado_usd REAL DEFAULT 0, tokens_entrada INTEGER DEFAULT 0,
    tokens_saida INTEGER DEFAULT 0, checkpoint TEXT, relatorio TEXT DEFAULT '', erro TEXT DEFAULT '',
    aprovada_em TEXT, desfeita_em TEXT
);
CREATE TABLE IF NOT EXISTS passos (
    id INTEGER PRIMARY KEY, execucao_id INTEGER NOT NULL, chave TEXT NOT NULL, titulo TEXT NOT NULL,
    categoria TEXT NOT NULL, instrucoes TEXT DEFAULT '', depende TEXT DEFAULT '[]', escrita INTEGER DEFAULT 0,
    escopo TEXT DEFAULT '[]', agente TEXT, agente_fixo INTEGER DEFAULT 0, motivo TEXT DEFAULT '',
    ranking TEXT DEFAULT '[]', status TEXT DEFAULT 'pendente', tentativas INTEGER DEFAULT 0, inicio TEXT, fim TEXT,
    sessao TEXT, custo_usd REAL DEFAULT 0, cobrado_usd REAL DEFAULT 0, tokens_entrada INTEGER DEFAULT 0,
    tokens_saida INTEGER DEFAULT 0, resultado TEXT DEFAULT '', erro TEXT DEFAULT '', arquivos TEXT DEFAULT '[]',
    conflitos TEXT DEFAULT '[]', opcional INTEGER DEFAULT 0, arvore_antes TEXT, dados TEXT DEFAULT '{}',
    retomar_de TEXT, UNIQUE (execucao_id, chave)
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY, execucao_id INTEGER, passo TEXT, agente TEXT, quando TEXT NOT NULL,
    tipo TEXT NOT NULL, conteudo TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS desempenho (
    id INTEGER PRIMARY KEY, agente TEXT NOT NULL, categoria TEXT NOT NULL, sucesso INTEGER NOT NULL,
    duracao_s REAL DEFAULT 0, custo_usd REAL DEFAULT 0, quando TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gastos (
    id INTEGER PRIMARY KEY, quando TEXT NOT NULL, agente TEXT NOT NULL, execucao_id INTEGER,
    custo_usd REAL DEFAULT 0, cobrado_usd REAL DEFAULT 0, tokens_entrada INTEGER DEFAULT 0, tokens_saida INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS projetos (
    caminho TEXT PRIMARY KEY, nome TEXT NOT NULL, autorizado_em TEXT NOT NULL, origem TEXT DEFAULT 'voce'
);
CREATE TABLE IF NOT EXISTS terminal (
    id INTEGER PRIMARY KEY, quando TEXT NOT NULL, pasta TEXT NOT NULL, comando TEXT NOT NULL, risco TEXT,
    confirmado INTEGER DEFAULT 0, codigo INTEGER, duracao_s REAL, saida TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS config (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_logs_exec ON logs(execucao_id, id);
CREATE INDEX IF NOT EXISTS ix_passos_exec ON passos(execucao_id);
CREATE INDEX IF NOT EXISTS ix_desemp ON desempenho(agente, categoria);
CREATE INDEX IF NOT EXISTS ix_gastos ON gastos(quando);
"""

_JSON_PASSO = ("depende", "escopo", "ranking", "arquivos", "conflitos")


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Banco:
    def __init__(self, arquivo: Path | str | None = None):
        self.arquivo = Path(arquivo or ARQUIVO)
        memoria = str(self.arquivo) == ":memory:"
        if not memoria:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(self.arquivo), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.executescript(ESQUEMA)
            if not memoria:
                self._con.execute("PRAGMA journal_mode=WAL")

    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._con.execute(sql, params)
            self._con.commit()
            return cur

    def _todos(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._con.execute(sql, params).fetchall()]

    def _um(self, sql: str, params=()) -> dict | None:
        r = self._todos(sql, params)
        return r[0] if r else None

    # ------------------------------------------------------------ execuções
    def criar_execucao(self, pedido: str, projeto: str | None, modo: str, plano: dict, status: str,
                       origem: str = "hud", prioridade: str = "normal") -> dict:
        cur = self._exec("INSERT INTO execucoes (pedido, projeto, modo, prioridade, status, plano, criada, origem) "
                         "VALUES (?,?,?,?,?,?,?,?)", (pedido[:4000], projeto, modo, prioridade, status,
                                                      json.dumps(plano, ensure_ascii=False), agora(), origem))
        return self.execucao(cur.lastrowid)

    @staticmethod
    def _exe(r: dict | None) -> dict | None:
        if r is not None:
            r["plano"] = json.loads(r["plano"] or "{}")
        return r

    def execucao(self, id_: int) -> dict | None:
        return self._exe(self._um("SELECT * FROM execucoes WHERE id=?", (id_,)))

    def execucoes(self, limite: int = 30, status: tuple[str, ...] | None = None) -> list[dict]:
        if status:
            marcas = ",".join("?" * len(status))
            linhas = self._todos(f"SELECT * FROM execucoes WHERE status IN ({marcas}) ORDER BY id DESC LIMIT ?",
                                 (*status, limite))
        else:
            linhas = self._todos("SELECT * FROM execucoes ORDER BY id DESC LIMIT ?", (limite,))
        return [self._exe(r) for r in linhas]

    def mudar_execucao(self, id_: int, **campos) -> dict | None:
        validos = {}
        for k, v in campos.items():
            if k == "plano":
                validos[k] = json.dumps(v, ensure_ascii=False)
            elif k in ("status", "inicio", "fim", "custo_usd", "cobrado_usd", "tokens_entrada", "tokens_saida",
                       "checkpoint", "relatorio", "erro", "aprovada_em", "desfeita_em", "modo", "prioridade"):
                validos[k] = v
        if validos:
            self._exec(f"UPDATE execucoes SET {', '.join(f'{k}=?' for k in validos)} WHERE id=?",
                       (*validos.values(), id_))
        return self.execucao(id_)

    def somar_na_execucao(self, id_: int, custo: float, cobrado: float, entrada: int, saida: int):
        self._exec("UPDATE execucoes SET custo_usd=custo_usd+?, cobrado_usd=cobrado_usd+?, "
                   "tokens_entrada=tokens_entrada+?, tokens_saida=tokens_saida+? WHERE id=?",
                   (custo, cobrado, entrada, saida, id_))

    # ------------------------------------------------------------ passos
    def criar_passo(self, execucao_id: int, p: dict) -> dict:
        self._exec("INSERT INTO passos (execucao_id, chave, titulo, categoria, instrucoes, depende, escrita, escopo, "
                   "agente, agente_fixo, opcional, retomar_de) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (execucao_id, p["id"], p["titulo"][:200], p["categoria"], p.get("instrucoes", "")[:8000],
                    json.dumps(p.get("depende_de") or []), int(bool(p.get("escrita"))),
                    json.dumps(p.get("escopo") or []), p.get("agente"), int(bool(p.get("agente"))),
                    int(bool(p.get("opcional"))), p.get("retomar_de")))
        return self.passo(execucao_id, p["id"])

    @staticmethod
    def _pas(r: dict | None) -> dict | None:
        if r is not None:
            for k in _JSON_PASSO:
                r[k] = json.loads(r[k] or "[]")
            r["dados"] = json.loads(r.get("dados") or "{}")
            r["escrita"], r["agente_fixo"], r["opcional"] = bool(r["escrita"]), bool(r["agente_fixo"]), bool(r["opcional"])
        return r

    def passo(self, execucao_id: int, chave: str) -> dict | None:
        return self._pas(self._um("SELECT * FROM passos WHERE execucao_id=? AND chave=?", (execucao_id, chave)))

    def passos(self, execucao_id: int) -> list[dict]:
        return [self._pas(r) for r in self._todos("SELECT * FROM passos WHERE execucao_id=? ORDER BY id",
                                                  (execucao_id,))]

    def mudar_passo(self, execucao_id: int, chave: str, **campos) -> dict | None:
        validos = {}
        for k, v in campos.items():
            if k in _JSON_PASSO or k == "dados":
                validos[k] = json.dumps(v, ensure_ascii=False)
            elif k in ("titulo", "categoria", "instrucoes", "escrita", "agente", "agente_fixo", "motivo", "status",
                       "tentativas", "inicio", "fim", "sessao", "custo_usd", "cobrado_usd", "tokens_entrada",
                       "tokens_saida", "resultado", "erro", "opcional", "arvore_antes", "retomar_de"):
                validos[k] = int(v) if isinstance(v, bool) else v
        if validos:
            self._exec(f"UPDATE passos SET {', '.join(f'{k}=?' for k in validos)} WHERE execucao_id=? AND chave=?",
                       (*validos.values(), execucao_id, chave))
        return self.passo(execucao_id, chave)

    def apagar_passos(self, execucao_id: int):
        self._exec("DELETE FROM passos WHERE execucao_id=?", (execucao_id,))

    # ------------------------------------------------------------ logs (auditoria)
    def log(self, execucao_id: int | None, passo: str | None, agente: str | None, tipo: str, conteudo: str) -> int:
        cur = self._exec("INSERT INTO logs (execucao_id, passo, agente, quando, tipo, conteudo) VALUES (?,?,?,?,?,?)",
                         (execucao_id, passo, agente, agora(), tipo, conteudo[:MAX_LOG]))
        return cur.lastrowid

    def logs(self, execucao_id: int, depois_de: int = 0, limite: int = 400, passo: str | None = None) -> list[dict]:
        if passo:
            return self._todos("SELECT * FROM logs WHERE execucao_id=? AND id>? AND passo=? ORDER BY id LIMIT ?",
                               (execucao_id, depois_de, passo, limite))
        return self._todos("SELECT * FROM logs WHERE execucao_id=? AND id>? ORDER BY id LIMIT ?",
                           (execucao_id, depois_de, limite))

    def ultimos_logs(self, execucao_id: int, limite: int = 200) -> list[dict]:
        return list(reversed(self._todos("SELECT * FROM logs WHERE execucao_id=? ORDER BY id DESC LIMIT ?",
                                         (execucao_id, limite))))

    def comandos_auditados(self, limite: int = 100) -> list[dict]:
        return self._todos("SELECT * FROM logs WHERE tipo='comando' ORDER BY id DESC LIMIT ?", (limite,))

    # ------------------------------------------------------------ desempenho e gastos
    def registrar_desempenho(self, agente: str, categoria: str, sucesso: bool, duracao_s: float, custo: float):
        self._exec("INSERT INTO desempenho (agente, categoria, sucesso, duracao_s, custo_usd, quando) "
                   "VALUES (?,?,?,?,?,?)", (agente, categoria, int(sucesso), duracao_s, custo, agora()))

    def desempenho(self, agente: str | None = None) -> dict[tuple[str, str], dict]:
        """{(agente, categoria): {'total', 'sucessos', 'duracao_media', 'custo_medio'}} (últimas 200 por par)."""
        sql = ("SELECT agente, categoria, COUNT(*) total, SUM(sucesso) sucessos, AVG(duracao_s) duracao_media, "
               "AVG(custo_usd) custo_medio FROM (SELECT * FROM desempenho {f} ORDER BY id DESC LIMIT 5000) "
               "GROUP BY agente, categoria")
        linhas = self._todos(sql.format(f="WHERE agente=?" if agente else ""), (agente,) if agente else ())
        return {(r["agente"], r["categoria"]): r for r in linhas}

    def registrar_gasto(self, agente: str, execucao_id: int | None, custo: float, cobrado: float, entrada: int,
                        saida: int):
        self._exec("INSERT INTO gastos (quando, agente, execucao_id, custo_usd, cobrado_usd, tokens_entrada, "
                   "tokens_saida) VALUES (?,?,?,?,?,?,?)", (agora(), agente, execucao_id, custo, cobrado, entrada, saida))

    def gasto_desde(self, desde_iso: str) -> dict:
        r = self._um("SELECT COALESCE(SUM(custo_usd),0) custo, COALESCE(SUM(cobrado_usd),0) cobrado, "
                     "COALESCE(SUM(tokens_entrada),0) entrada, COALESCE(SUM(tokens_saida),0) saida "
                     "FROM gastos WHERE quando >= ?", (desde_iso,))
        return r or {"custo": 0, "cobrado": 0, "entrada": 0, "saida": 0}

    def gasto_por_agente(self, desde_iso: str) -> list[dict]:
        return self._todos("SELECT agente, SUM(custo_usd) custo, SUM(cobrado_usd) cobrado, SUM(tokens_entrada) "
                           "entrada, SUM(tokens_saida) saida, COUNT(*) chamadas FROM gastos WHERE quando >= ? "
                           "GROUP BY agente ORDER BY custo DESC", (desde_iso,))

    # ------------------------------------------------------------ projetos autorizados
    def autorizar(self, caminho: str, nome: str, origem: str = "voce"):
        self._exec("INSERT OR REPLACE INTO projetos (caminho, nome, autorizado_em, origem) VALUES (?,?,?,?)",
                   (caminho, nome, agora(), origem))

    def desautorizar(self, caminho: str) -> bool:
        return self._exec("DELETE FROM projetos WHERE caminho=?", (caminho,)).rowcount > 0

    def projetos(self) -> list[dict]:
        return self._todos("SELECT * FROM projetos ORDER BY nome")

    def autorizado(self, caminho: str) -> bool:
        return self._um("SELECT 1 x FROM projetos WHERE caminho=?", (caminho,)) is not None

    # ------------------------------------------------------------ terminal
    def registrar_comando(self, pasta: str, comando: str, risco: str, confirmado: bool) -> int:
        return self._exec("INSERT INTO terminal (quando, pasta, comando, risco, confirmado) VALUES (?,?,?,?,?)",
                          (agora(), pasta, comando[:2000], risco, int(confirmado))).lastrowid

    def fechar_comando(self, id_: int, codigo: int | None, duracao_s: float, saida: str):
        self._exec("UPDATE terminal SET codigo=?, duracao_s=?, saida=? WHERE id=?", (codigo, duracao_s,
                                                                                    saida[-MAX_LOG:], id_))

    def historico_terminal(self, limite: int = 50) -> list[dict]:
        return self._todos("SELECT * FROM terminal ORDER BY id DESC LIMIT ?", (limite,))

    # ------------------------------------------------------------ preferências
    def config(self, chave: str, padrao=None):
        r = self._um("SELECT valor FROM config WHERE chave=?", (chave,))
        return json.loads(r["valor"]) if r else padrao

    def salvar_config(self, chave: str, valor):
        self._exec("INSERT OR REPLACE INTO config (chave, valor) VALUES (?,?)", (chave, json.dumps(valor)))

    def fechar(self):
        with self._lock:
            self._con.close()


def inicio_do_dia() -> str:
    return time.strftime("%Y-%m-%dT00:00:00")


def inicio_do_mes() -> str:
    return time.strftime("%Y-%m-01T00:00:00")
