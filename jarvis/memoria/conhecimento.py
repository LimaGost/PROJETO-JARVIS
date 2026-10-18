"""Base de conhecimento local: o que o Jarvis ESTUDOU (documentos, páginas, manuais). Separada da memória sobre o
usuário (jarvis.db): aqui só entra conteúdo de estudo, em data/conhecimento/conhecimento.db.

Como funciona: o texto é dividido em trechos de ~1.000 caracteres (com sobreposição, para uma ideia não ficar
cortada ao meio) e cada trecho ganha um vetor de significado (embeddinggemma, na CPU). Numa pergunta, os trechos
com significado mais próximo voltam para o Claude ler antes de responder, como consultar um livro aberto.
Estudar de novo a mesma fonte substitui a versão antiga.
"""
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

TAMANHO_TRECHO = 1000
SOBREPOSICAO = 150
MAX_TRECHOS = 600          # ~600 mil caracteres por documento (um livro técnico médio)
LOTE_EMBEDDINGS = 24

ESQUEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, fonte TEXT NOT NULL UNIQUE, estudado TEXT NOT NULL,
    caracteres INTEGER NOT NULL, trechos INTEGER NOT NULL, cortado INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS trechos (
    id INTEGER PRIMARY KEY, documento INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    ordem INTEGER NOT NULL, texto TEXT NOT NULL, vetor BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS trechos_documento ON trechos(documento, ordem);
"""


@dataclass
class Documento:
    id: int
    titulo: str
    fonte: str
    estudado: str
    caracteres: int
    trechos: int
    cortado: bool


@dataclass
class Achado:
    documento: int
    titulo: str
    fonte: str
    ordem: int
    texto: str
    nota: float


def dividir(texto: str, tamanho: int = TAMANHO_TRECHO, sobra: int = SOBREPOSICAO) -> list[str]:
    """Trechos de até `tamanho` caracteres, quebrando em parágrafo ou frase, com `sobra` repetida do anterior."""
    texto = texto.strip()
    if not texto:
        return []
    unidades: list[str] = []
    for paragrafo in re.split(r"\n\s*\n", texto):
        paragrafo = paragrafo.strip()
        if len(paragrafo) <= tamanho:
            if paragrafo:
                unidades.append(paragrafo)
            continue
        for frase in re.split(r"(?<=[.!?;:])\s+", paragrafo):
            while len(frase) > tamanho:            # frase gigante (tabela, código): corta no espaço
                corte = frase.rfind(" ", 0, tamanho)
                corte = corte if corte > tamanho // 2 else tamanho
                unidades.append(frase[:corte])
                frase = frase[corte:].strip()
            if frase:
                unidades.append(frase)
    trechos, atual = [], ""
    for u in unidades:
        if atual and len(atual) + 2 + len(u) > tamanho:
            trechos.append(atual)
            cauda = atual[-sobra:]
            espaco = cauda.find(" ")
            atual = (cauda[espaco + 1:] if espaco >= 0 else cauda) + " " + u
        else:
            atual = f"{atual}\n\n{u}" if atual else u
    if atual:
        trechos.append(atual)
    return trechos


class Conhecimento:
    def __init__(self, caminho: str, embedder):
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._db = sqlite3.connect(caminho, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(ESQUEMA)
        self._lock = threading.RLock()
        self._indice: tuple[np.ndarray, list[tuple]] | None = None

    # ------------------------------------------------------------ escrita
    def estudar(self, titulo: str, fonte: str, texto: str) -> tuple[Documento, bool]:
        """Guarda (ou substitui) um documento. Devolve (documento, substituiu_versão_anterior)."""
        trechos = dividir(texto)
        if not trechos:
            raise ValueError("documento sem texto")
        cortado = len(trechos) > MAX_TRECHOS
        trechos = trechos[:MAX_TRECHOS]
        vetores = []
        for i in range(0, len(trechos), LOTE_EMBEDDINGS):      # calcula ANTES de mexer no banco: falhou, nada muda
            lote = trechos[i:i + LOTE_EMBEDDINGS]
            vetores.append(self.embedder.vetores([f"{titulo}\n{t}" for t in lote], "documento"))
        matriz = np.concatenate(vetores).astype(np.float32)
        with self._lock, self._db:
            antigo = self._db.execute("SELECT id FROM documentos WHERE fonte = ?", (fonte,)).fetchone()
            if antigo:
                self._db.execute("DELETE FROM documentos WHERE id = ?", (antigo[0],))
            cur = self._db.execute(
                "INSERT INTO documentos (titulo, fonte, estudado, caracteres, trechos, cortado) VALUES (?,?,?,?,?,?)",
                (titulo, fonte, datetime.now().isoformat(timespec="seconds"), len(texto), len(trechos), int(cortado)))
            doc_id = cur.lastrowid
            self._db.executemany("INSERT INTO trechos (documento, ordem, texto, vetor) VALUES (?,?,?,?)",
                                 [(doc_id, i, t, matriz[i].tobytes()) for i, t in enumerate(trechos)])
            self._indice = None
        return self.documento(doc_id), bool(antigo)

    def apagar(self, doc_id: int) -> Documento | None:
        doc = self.documento(doc_id)
        if doc is None:
            return None
        with self._lock, self._db:
            self._db.execute("DELETE FROM documentos WHERE id = ?", (doc_id,))
            self._indice = None
        return doc

    # ------------------------------------------------------------ leitura
    def documentos(self) -> list[Documento]:
        with self._lock:
            linhas = self._db.execute("SELECT id, titulo, fonte, estudado, caracteres, trechos, cortado "
                                      "FROM documentos ORDER BY estudado DESC").fetchall()
        return [Documento(*l[:6], bool(l[6])) for l in linhas]

    def documento(self, doc_id: int) -> Documento | None:
        with self._lock:
            l = self._db.execute("SELECT id, titulo, fonte, estudado, caracteres, trechos, cortado "
                                 "FROM documentos WHERE id = ?", (doc_id,)).fetchone()
        return Documento(*l[:6], bool(l[6])) if l else None

    def _carregar(self):
        with self._lock:
            if self._indice is None:
                linhas = self._db.execute(
                    "SELECT t.documento, d.titulo, d.fonte, t.ordem, t.texto, t.vetor "
                    "FROM trechos t JOIN documentos d ON d.id = t.documento").fetchall()
                matriz = (np.stack([np.frombuffer(l[5], dtype=np.float32) for l in linhas]) if linhas
                          else np.zeros((0, 1), dtype=np.float32))
                self._indice = (matriz, [l[:5] for l in linhas])
            return self._indice

    def buscar(self, pergunta: str, k: int = 5, minimo: float = 0.25, documento: int | None = None) -> list[Achado]:
        matriz, dados = self._carregar()
        if not dados:
            return []
        notas = matriz @ self.embedder.consulta(pergunta)
        if documento is not None:
            notas = np.where(np.array([d[0] == documento for d in dados]), notas, -1.0)
        ordem = np.argsort(-notas)[:k]
        return [Achado(*dados[i], float(notas[i])) for i in ordem if notas[i] >= minimo]

    def vizinhos(self, achado: Achado) -> str:
        """Trecho anterior + o achado + o seguinte: a resposta raramente cabe num pedaço só."""
        with self._lock:
            linhas = self._db.execute("SELECT texto FROM trechos WHERE documento = ? AND ordem BETWEEN ? AND ? "
                                      "ORDER BY ordem", (achado.documento, achado.ordem - 1, achado.ordem + 1)).fetchall()
        return "\n[...]\n".join(l[0] for l in linhas)
