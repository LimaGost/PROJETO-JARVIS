"""Memória de longo prazo: fatos sobre o usuário, resumos de cada dia e o histórico das conversas.

SQLite + numpy. Para alguns milhares de fatos a busca por força bruta leva microssegundos,
então não vale a dependência de um banco vetorial.
"""
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np

CATEGORIAS = ["perfil", "preferencia", "pessoa", "trabalho", "projeto", "rotina", "procedimento", "correcao", "outro"]
SEMPRE_INCLUIR = ("perfil", "correcao")  # entram em todo pedido, relevantes ou não
MAX_SEMPRE = 8

# Nunca guardar segredos, mesmo que o usuário fale em voz alta.
_SEGREDO = re.compile(
    r"senha|password|\bpin\b|cvv|c[oó]digo de seguran[cç]a|cart[aã]o de cr[eé]dito|n[uú]mero do cart[aã]o|"
    r"\btoken\b|chave de api|api[ _-]?key|\bcpf\b|\brg\b|conta banc[aá]ria|ag[eê]ncia e conta|"
    r"\b\d{4}[ .-]?\d{4}[ .-]?\d{4}[ .-]?\d{4}\b|\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
    re.IGNORECASE)

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS fatos (
    id INTEGER PRIMARY KEY, texto TEXT NOT NULL, categoria TEXT NOT NULL, vetor BLOB NOT NULL,
    criado TEXT NOT NULL, atualizado TEXT NOT NULL, origem TEXT NOT NULL, usos INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS episodios (
    dia TEXT PRIMARY KEY, resumo TEXT NOT NULL, vetor BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS turnos (
    id INTEGER PRIMARY KEY, sessao TEXT NOT NULL, quando TEXT NOT NULL, usuario TEXT NOT NULL,
    jarvis TEXT NOT NULL, ferramentas TEXT NOT NULL, refletido INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS turnos_pendentes ON turnos(refletido, quando);
"""


def contem_segredo(texto: str) -> bool:
    return bool(_SEGREDO.search(texto))


@dataclass
class Fato:
    id: int
    texto: str
    categoria: str
    atualizado: str


class Memoria:
    def __init__(self, caminho: str, embedder):
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._lock = threading.RLock()  # a reflexão roda em outra thread
        self._db = sqlite3.connect(caminho, check_same_thread=False)
        self._db.executescript(_ESQUEMA)
        self._indice = None  # (ids, matriz) dos fatos, recarregado após escrita

    # ------------------------------------------------------------ fatos
    def adicionar_fato(self, texto: str, categoria: str = "outro", origem: str = "conversa") -> int | None:
        texto = texto.strip()
        if not texto or contem_segredo(texto):
            return None
        categoria = categoria if categoria in CATEGORIAS else "outro"
        vetor = self.embedder.vetores([texto], "documento")[0]
        agora = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO fatos (texto, categoria, vetor, criado, atualizado, origem) VALUES (?,?,?,?,?,?)",
                (texto, categoria, vetor.tobytes(), agora, agora, origem))
            self._db.commit()
            self._indice = None
            return cur.lastrowid

    def atualizar_fato(self, id_: int, texto: str, categoria: str | None = None) -> bool:
        texto = texto.strip()
        if not texto or contem_segredo(texto):
            return False
        vetor = self.embedder.vetores([texto], "documento")[0]
        with self._lock:
            sets, valores = ["texto=?", "vetor=?", "atualizado=?"], [texto, vetor.tobytes(), datetime.now().isoformat(timespec="seconds")]
            if categoria in CATEGORIAS:
                sets.append("categoria=?")
                valores.append(categoria)
            cur = self._db.execute(f"UPDATE fatos SET {', '.join(sets)} WHERE id=?", (*valores, id_))
            self._db.commit()
            self._indice = None
            return cur.rowcount > 0

    def apagar_fato(self, id_: int) -> bool:
        """Esquecer é esquecer de verdade: apaga a linha, não só marca."""
        with self._lock:
            cur = self._db.execute("DELETE FROM fatos WHERE id=?", (id_,))
            self._db.commit()
            self._indice = None
            return cur.rowcount > 0

    def fatos(self, categoria: str | None = None) -> list[Fato]:
        with self._lock:
            sql = "SELECT id, texto, categoria, atualizado FROM fatos"
            linhas = self._db.execute(sql + (" WHERE categoria=?" if categoria else "") + " ORDER BY id",
                                      (categoria,) if categoria else ()).fetchall()
        return [Fato(*l) for l in linhas]

    def fato(self, id_: int) -> Fato | None:
        with self._lock:
            l = self._db.execute("SELECT id, texto, categoria, atualizado FROM fatos WHERE id=?", (id_,)).fetchone()
        return Fato(*l) if l else None

    def _carregar_indice(self):
        with self._lock:
            if self._indice is None:
                linhas = self._db.execute("SELECT id, vetor FROM fatos ORDER BY id").fetchall()
                ids = np.array([l[0] for l in linhas], dtype=np.int64)
                matriz = np.stack([np.frombuffer(l[1], dtype=np.float32) for l in linhas]) if linhas else None
                self._indice = (ids, matriz)
            return self._indice

    def _ranquear(self, vetor: np.ndarray, k: int, minimo: float) -> list[tuple[Fato, float]]:
        ids, matriz = self._carregar_indice()
        if matriz is None:
            return []
        sims = matriz @ vetor
        resultado = []
        for i in np.argsort(-sims)[:k]:
            if sims[i] < minimo:
                break
            f = self.fato(int(ids[i]))
            if f:
                resultado.append((f, float(sims[i])))
        return resultado

    def buscar(self, consulta: str, k: int = 5, minimo: float = 0.30) -> list[tuple[Fato, float]]:
        """Fatos relevantes para um pedido do usuário."""
        return self._ranquear(self.embedder.consulta(consulta), k, minimo)

    def semelhantes(self, texto: str, k: int = 3, minimo: float = 0.0) -> list[tuple[Fato, float]]:
        """Fatos parecidos com outro fato (para achar duplicados e versões antigas)."""
        return self._ranquear(self.embedder.vetores([texto], "documento")[0], k, minimo)

    def marcar_uso(self, ids: list[int]):
        if not ids:
            return
        with self._lock:
            self._db.executemany("UPDATE fatos SET usos = usos + 1 WHERE id=?", [(i,) for i in ids])
            self._db.commit()

    # ------------------------------------------------------------ episódios (resumo de cada dia)
    def salvar_episodio(self, dia: str, resumo: str):
        vetor = self.embedder.vetores([resumo], "documento")[0]
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO episodios (dia, resumo, vetor) VALUES (?,?,?)",
                             (dia, resumo, vetor.tobytes()))
            self._db.commit()

    def episodio(self, dia: str) -> str | None:
        with self._lock:
            l = self._db.execute("SELECT resumo FROM episodios WHERE dia=?", (dia,)).fetchone()
        return l[0] if l else None

    def episodios(self) -> list[tuple[str, str]]:
        with self._lock:
            return self._db.execute("SELECT dia, resumo FROM episodios ORDER BY dia").fetchall()

    def buscar_episodios(self, consulta: str, k: int = 2, minimo: float = 0.30) -> list[tuple[str, str, float]]:
        with self._lock:
            linhas = self._db.execute("SELECT dia, resumo, vetor FROM episodios").fetchall()
        if not linhas:
            return []
        v = self.embedder.consulta(consulta)
        pontuados = [(dia, resumo, float(np.frombuffer(vet, dtype=np.float32) @ v)) for dia, resumo, vet in linhas]
        pontuados.sort(key=lambda x: -x[2])
        return [p for p in pontuados[:k] if p[2] >= minimo]

    # ------------------------------------------------------------ histórico das conversas
    def registrar_turno(self, sessao: str, usuario: str, jarvis: str, ferramentas: list[str]):
        with self._lock:
            self._db.execute(
                "INSERT INTO turnos (sessao, quando, usuario, jarvis, ferramentas) VALUES (?,?,?,?,?)",
                (sessao, datetime.now().isoformat(timespec="seconds"), usuario, jarvis, ",".join(ferramentas)))
            self._db.commit()

    def dias_pendentes(self, incluir_hoje: bool = False) -> list[str]:
        """Dias com conversas ainda não refletidas. Por padrão só dias já encerrados."""
        hoje = date.today().isoformat()
        with self._lock:
            dias = [l[0] for l in self._db.execute(
                "SELECT DISTINCT substr(quando, 1, 10) FROM turnos WHERE refletido=0 ORDER BY 1").fetchall()]
        return [d for d in dias if incluir_hoje or d < hoje]

    def turnos_do_dia(self, dia: str, so_pendentes: bool = True) -> list[tuple[int, str, str, str, str]]:
        with self._lock:
            return self._db.execute(
                "SELECT id, quando, usuario, jarvis, ferramentas FROM turnos WHERE substr(quando, 1, 10)=?"
                + (" AND refletido=0" if so_pendentes else "") + " ORDER BY id", (dia,)).fetchall()

    def marcar_refletido(self, dia: str, ate_id: int | None = None):
        with self._lock:
            if ate_id is None:
                self._db.execute("UPDATE turnos SET refletido=1 WHERE substr(quando, 1, 10)=?", (dia,))
            else:
                self._db.execute("UPDATE turnos SET refletido=1 WHERE substr(quando, 1, 10)=? AND id<=?", (dia, ate_id))
            self._db.commit()

    def ultimo_turno(self) -> datetime | None:
        with self._lock:
            l = self._db.execute("SELECT max(quando) FROM turnos").fetchone()
        return datetime.fromisoformat(l[0]) if l and l[0] else None

    # ------------------------------------------------------------ contexto para o modelo
    def contexto(self, pedido: str, k: int = 5, minimo: float = 0.30) -> str:
        """Bloco que vai junto do pedido atual: perfil + fatos relevantes + conversas passadas relevantes."""
        sempre = [f for c in SEMPRE_INCLUIR for f in self.fatos(c)][:MAX_SEMPRE]
        try:
            relevantes = [f for f, _ in self.buscar(pedido, k, minimo)]
            episodios = self.buscar_episodios(pedido, 1, 0.45)
        except Exception:  # sem embeddings ainda funciona com o perfil
            relevantes, episodios = [], []
        vistos, fatos = set(), []
        for f in sempre + relevantes:
            if f.id not in vistos:
                vistos.add(f.id)
                fatos.append(f)
        self.marcar_uso([f.id for f in relevantes])
        if not fatos and not episodios:
            return ""
        linhas = ["[Memória sobre o usuário (use só o que for relevante para este pedido):"]
        linhas += [f"- {f.texto}" for f in fatos]
        for dia, resumo, _ in episodios:
            linhas.append(f"- Conversa de {dia[8:10]}/{dia[5:7]}: {resumo}")
        return "\n".join(linhas) + "]"
