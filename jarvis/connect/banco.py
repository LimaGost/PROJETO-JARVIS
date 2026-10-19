"""Banco local do Connect (SQLite): tarefas, automações, execuções, linha do tempo (auditoria) e consumo de API.

Uma conexão compartilhada com trava: o motor, os webhooks e a HUD escrevem de threads diferentes.
"""
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from jarvis.config import RAIZ

ARQUIVO = RAIZ / "data" / "connect" / "connect.db"
PRIORIDADES = ("baixa", "normal", "alta", "urgente")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS tarefas (
    id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, descricao TEXT DEFAULT '', prioridade TEXT DEFAULT 'normal',
    quando TEXT, status TEXT DEFAULT 'pendente', origem TEXT DEFAULT 'voce', automacao_id INTEGER,
    criada TEXT NOT NULL, concluida TEXT, lembrada INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS automacoes (
    id INTEGER PRIMARY KEY, nome TEXT NOT NULL, descricao TEXT DEFAULT '', ativa INTEGER DEFAULT 1,
    fluxo TEXT NOT NULL, aprovacao TEXT, criada TEXT NOT NULL, proxima TEXT, ultima TEXT,
    execucoes INTEGER DEFAULT 0, falhas INTEGER DEFAULT 0, ultimo_status TEXT
);
CREATE TABLE IF NOT EXISTS execucoes (
    id INTEGER PRIMARY KEY, automacao_id INTEGER, chave TEXT UNIQUE, inicio TEXT NOT NULL, fim TEXT,
    status TEXT DEFAULT 'rodando', passos TEXT DEFAULT '[]', resultado TEXT DEFAULT '', teste INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY, quando TEXT NOT NULL, tipo TEXT NOT NULL, conector TEXT DEFAULT '',
    resumo TEXT NOT NULL, nivel TEXT DEFAULT 'info'
);
CREATE TABLE IF NOT EXISTS consumo (
    conector TEXT NOT NULL, dia TEXT NOT NULL, chamadas INTEGER DEFAULT 0, erros INTEGER DEFAULT 0,
    PRIMARY KEY (conector, dia)
);
CREATE TABLE IF NOT EXISTS sincronizacao (conector TEXT PRIMARY KEY, quando TEXT NOT NULL, ok INTEGER, detalhe TEXT);
CREATE INDEX IF NOT EXISTS ix_eventos_quando ON eventos(quando);
CREATE INDEX IF NOT EXISTS ix_exec_auto ON execucoes(automacao_id, inicio);
"""


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Banco:
    def __init__(self, arquivo: Path | str | None = None):
        self.arquivo = Path(arquivo or ARQUIVO)
        if str(self.arquivo) != ":memory:":
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(self.arquivo), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.executescript(ESQUEMA)
            if str(self.arquivo) != ":memory:":
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

    # ------------------------------------------------------------ linha do tempo / auditoria
    def evento(self, tipo: str, resumo: str, conector: str = "", nivel: str = "info") -> int:
        cur = self._exec("INSERT INTO eventos (quando, tipo, conector, resumo, nivel) VALUES (?,?,?,?,?)",
                         (agora(), tipo, conector, resumo[:500], nivel))
        return cur.lastrowid

    def eventos(self, limite: int = 50, conector: str = "", nivel: str = "") -> list[dict]:
        sql, p = "SELECT * FROM eventos WHERE 1=1", []
        if conector:
            sql, p = sql + " AND conector=?", p + [conector]
        if nivel:
            sql, p = sql + " AND nivel=?", p + [nivel]
        return self._todos(sql + " ORDER BY id DESC LIMIT ?", p + [limite])

    # ------------------------------------------------------------ consumo de API
    def contar_chamada(self, conector: str, erro: bool = False):
        dia = time.strftime("%Y-%m-%d")
        self._exec("INSERT INTO consumo (conector, dia, chamadas, erros) VALUES (?,?,1,?) "
                   "ON CONFLICT(conector, dia) DO UPDATE SET chamadas=chamadas+1, erros=erros+?",
                   (conector, dia, int(erro), int(erro)))

    def consumo(self, dias: int = 7) -> list[dict]:
        return self._todos("SELECT conector, SUM(chamadas) AS chamadas, SUM(erros) AS erros FROM consumo "
                           "WHERE dia >= date('now', ?) GROUP BY conector ORDER BY chamadas DESC", (f"-{dias} day",))

    def sincronizou(self, conector: str, ok: bool, detalhe: str = ""):
        self._exec("INSERT OR REPLACE INTO sincronizacao VALUES (?,?,?,?)", (conector, agora(), int(ok), detalhe[:300]))

    def ultima_sincronizacao(self, conector: str) -> dict | None:
        return self._um("SELECT * FROM sincronizacao WHERE conector=?", (conector,))

    # ------------------------------------------------------------ tarefas
    def criar_tarefa(self, titulo: str, descricao: str = "", prioridade: str = "normal", quando: str | None = None,
                     origem: str = "voce", automacao_id: int | None = None) -> dict:
        prioridade = prioridade if prioridade in PRIORIDADES else "normal"
        cur = self._exec("INSERT INTO tarefas (titulo, descricao, prioridade, quando, origem, automacao_id, criada) "
                         "VALUES (?,?,?,?,?,?,?)", (titulo.strip()[:200], descricao.strip()[:2000], prioridade,
                                                    quando, origem, automacao_id, agora()))
        return self.tarefa(cur.lastrowid)

    def tarefa(self, id_: int) -> dict | None:
        return self._um("SELECT * FROM tarefas WHERE id=?", (id_,))

    def tarefas(self, status: str = "pendente", limite: int = 100) -> list[dict]:
        ordem = ("CASE prioridade WHEN 'urgente' THEN 0 WHEN 'alta' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, "
                 "COALESCE(quando, '9999'), id")
        if status == "todas":
            return self._todos(f"SELECT * FROM tarefas ORDER BY status DESC, {ordem} LIMIT ?", (limite,))
        return self._todos(f"SELECT * FROM tarefas WHERE status=? ORDER BY {ordem} LIMIT ?", (status, limite))

    def mudar_tarefa(self, id_: int, **campos) -> dict | None:
        validos = {k: v for k, v in campos.items() if k in ("titulo", "descricao", "prioridade", "quando", "status")}
        if "quando" in validos:
            validos["lembrada"] = 0
        if "status" in validos:
            validos["concluida"] = agora() if validos["status"] == "concluida" else None
        if validos:
            sets = ", ".join(f"{k}=?" for k in validos)
            self._exec(f"UPDATE tarefas SET {sets} WHERE id=?", (*validos.values(), id_))
        return self.tarefa(id_)

    def tarefas_vencidas(self, ate: str | None = None) -> list[dict]:
        """Pendentes cujo horário chegou (até `ate`, ISO) e que ainda não foram lembradas."""
        return self._todos("SELECT * FROM tarefas WHERE status='pendente' AND lembrada=0 AND quando IS NOT NULL "
                           "AND quando <= ?", (ate or agora(),))

    def marcar_lembrada(self, id_: int):
        self._exec("UPDATE tarefas SET lembrada=1 WHERE id=?", (id_,))

    def apagar_tarefa(self, id_: int) -> bool:
        return self._exec("DELETE FROM tarefas WHERE id=?", (id_,)).rowcount > 0

    # ------------------------------------------------------------ automações
    def criar_automacao(self, nome: str, fluxo: dict, descricao: str = "", aprovacao: dict | None = None,
                        ativa: bool = True, proxima: str | None = None) -> dict:
        cur = self._exec("INSERT INTO automacoes (nome, descricao, ativa, fluxo, aprovacao, criada, proxima) "
                         "VALUES (?,?,?,?,?,?,?)", (nome.strip()[:120], descricao[:500], int(ativa),
                                                    json.dumps(fluxo, ensure_ascii=False),
                                                    json.dumps(aprovacao) if aprovacao else None, agora(), proxima))
        return self.automacao(cur.lastrowid)

    @staticmethod
    def _auto(r: dict | None) -> dict | None:
        if r is None:
            return None
        r["fluxo"] = json.loads(r["fluxo"])
        r["aprovacao"] = json.loads(r["aprovacao"]) if r["aprovacao"] else None
        r["ativa"] = bool(r["ativa"])
        return r

    def automacao(self, id_: int) -> dict | None:
        return self._auto(self._um("SELECT * FROM automacoes WHERE id=?", (id_,)))

    def automacoes(self, so_ativas: bool = False) -> list[dict]:
        sql = "SELECT * FROM automacoes" + (" WHERE ativa=1" if so_ativas else "") + " ORDER BY id"
        return [self._auto(r) for r in self._todos(sql)]

    def mudar_automacao(self, id_: int, **campos) -> dict | None:
        validos = {}
        for k, v in campos.items():
            if k in ("fluxo", "aprovacao"):
                validos[k] = json.dumps(v, ensure_ascii=False) if v is not None else None
            elif k == "ativa":
                validos[k] = int(bool(v))
            elif k in ("nome", "descricao", "proxima", "ultima", "ultimo_status"):
                validos[k] = v
        if validos:
            sets = ", ".join(f"{k}=?" for k in validos)
            self._exec(f"UPDATE automacoes SET {sets} WHERE id=?", (*validos.values(), id_))
        return self.automacao(id_)

    def apagar_automacao(self, id_: int) -> bool:
        return self._exec("DELETE FROM automacoes WHERE id=?", (id_,)).rowcount > 0

    # ------------------------------------------------------------ execuções
    def abrir_execucao(self, automacao_id: int, chave: str, teste: bool = False) -> int | None:
        """Registra o início. None = essa mesma execução (mesma chave) já rodou: proteção contra duplicar."""
        cur = self._exec("INSERT OR IGNORE INTO execucoes (automacao_id, chave, inicio, teste) VALUES (?,?,?,?)",
                         (automacao_id, chave, agora(), int(teste)))
        return cur.lastrowid if cur.rowcount else None

    def fechar_execucao(self, id_: int, status: str, passos: list, resultado: str):
        self._exec("UPDATE execucoes SET fim=?, status=?, passos=?, resultado=? WHERE id=?",
                   (agora(), status, json.dumps(passos, ensure_ascii=False)[:20000], resultado[:2000], id_))
        r = self._um("SELECT automacao_id, teste FROM execucoes WHERE id=?", (id_,))
        if r and not r["teste"]:
            self._exec("UPDATE automacoes SET execucoes=execucoes+1, falhas=falhas+?, ultima=?, ultimo_status=? "
                       "WHERE id=?", (int(status != "ok"), agora(), status, r["automacao_id"]))

    def execucoes(self, automacao_id: int | None = None, limite: int = 30) -> list[dict]:
        if automacao_id is None:
            linhas = self._todos("SELECT * FROM execucoes ORDER BY id DESC LIMIT ?", (limite,))
        else:
            linhas = self._todos("SELECT * FROM execucoes WHERE automacao_id=? ORDER BY id DESC LIMIT ?",
                                 (automacao_id, limite))
        for r in linhas:
            r["passos"] = json.loads(r["passos"] or "[]")
        return linhas

    # ------------------------------------------------------------ painel
    def resumo(self) -> dict:
        um = lambda sql, p=(): (self._um(sql, p) or {}).get("n", 0)
        hoje = time.strftime("%Y-%m-%d")
        return {
            "tarefas_pendentes": um("SELECT COUNT(*) n FROM tarefas WHERE status='pendente'"),
            "tarefas_concluidas": um("SELECT COUNT(*) n FROM tarefas WHERE status='concluida'"),
            "automacoes_ativas": um("SELECT COUNT(*) n FROM automacoes WHERE ativa=1"),
            "automacoes_total": um("SELECT COUNT(*) n FROM automacoes"),
            "rodando": um("SELECT COUNT(*) n FROM execucoes WHERE status='rodando'"),
            "falhas_hoje": um("SELECT COUNT(*) n FROM execucoes WHERE status NOT IN ('ok','rodando','ignorada') "
                              "AND inicio >= ?", (hoje,)),
            "execucoes_hoje": um("SELECT COUNT(*) n FROM execucoes WHERE teste=0 AND inicio >= ?", (hoje,)),
        }

    def proximas(self, limite: int = 8) -> list[dict]:
        autos = self._todos("SELECT id, nome, proxima FROM automacoes WHERE ativa=1 AND proxima IS NOT NULL "
                            "ORDER BY proxima LIMIT ?", (limite,))
        tarefas = self._todos("SELECT id, titulo AS nome, quando AS proxima FROM tarefas WHERE status='pendente' "
                              "AND quando IS NOT NULL ORDER BY quando LIMIT ?", (limite,))
        juntos = [{**a, "tipo": "automacao"} for a in autos] + [{**t, "tipo": "tarefa"} for t in tarefas]
        return sorted(juntos, key=lambda x: x["proxima"])[:limite]

    def fechar(self):
        with self._lock:
            self._con.close()
