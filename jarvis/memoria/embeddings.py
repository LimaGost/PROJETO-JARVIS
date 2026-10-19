"""Vetores de significado (embeddings) para a busca na memória.

Modelo: embeddinggemma, que em português acertou 15/15 num teste onde o nomic-embed-text
acertou 12/15. Roda na CPU (num_gpu=0, ~30 ms por consulta) porque a GPU está ocupada pelo
Qwen e pelo Whisper; se fosse para a GPU, o Ollama tiraria o Qwen da memória.
"""
from functools import lru_cache

import numpy as np
import ollama

from jarvis import recursos

# formatos de prompt recomendados para o embeddinggemma
_PREFIXOS = {
    "consulta": "task: search result | query: ",
    "documento": "title: none | text: ",
}


class Embedder:
    def __init__(self, host: str, modelo: str, keep_alive="30m"):
        self.cliente = ollama.Client(host=host.replace("localhost", "127.0.0.1"))
        self.modelo = modelo
        self.keep_alive = keep_alive   # antes -1 (para sempre): ~1 GB de RAM preso mesmo com o PC jogando
        self.dimensao: int | None = None

    def vetores(self, textos: list[str], tipo: str = "documento") -> np.ndarray:
        """Matriz (n, d) com linhas normalizadas: produto escalar = similaridade de cosseno."""
        if not textos:
            return np.zeros((0, self.dimensao or 0), dtype=np.float32)
        r = self.cliente.embed(
            model=self.modelo,
            input=[_PREFIXOS[tipo] + t for t in textos],
            keep_alive=recursos.keep_alive(self.keep_alive),
            options={"num_gpu": 0},
        )
        m = np.asarray(r.embeddings, dtype=np.float32)
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        self.dimensao = m.shape[1]
        return m

    @lru_cache(maxsize=256)
    def consulta(self, texto: str) -> np.ndarray:
        return self.vetores([texto], "consulta")[0]

    def aquecer(self):
        self.vetores(["aquecer"], "consulta")
