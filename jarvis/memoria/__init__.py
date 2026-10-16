"""Memória de longo prazo do Jarvis. Uso: python -m jarvis.memoria (listar | buscar | apagar | dias | refletir)."""
import os
import threading

import ollama

from jarvis.config import caminho


def criar(cfg: dict, ocupado: threading.Event | None = None):
    """Monta memória + reflexão e liga as ferramentas de memória. Retorna (memoria, reflexao) ou (None, None).
    JARVIS_MEMORIA=<arquivo.db> usa outro banco (testes não sujam a memória real)."""
    from jarvis.memoria.banco import Memoria
    from jarvis.memoria.embeddings import Embedder
    from jarvis.memoria.reflexao import Consolidador, Reflexao
    from jarvis.tools import memoria_tools

    m = cfg.get("memoria", {})
    if not m.get("ativa", True):
        return None, None
    llm = cfg["llm"]
    host = llm["host"].replace("localhost", "127.0.0.1")
    embedder = Embedder(host, m.get("embeddings", "embeddinggemma"))
    memoria = Memoria(os.environ.get("JARVIS_MEMORIA") or caminho(m.get("banco", "data/memoria/jarvis.db")), embedder)
    consolidador = Consolidador(memoria, ollama.Client(host=host), llm["modelo"],
                                {"num_ctx": llm.get("contexto", 4096)}, llm["keep_alive"])
    memoria_tools.ativar(memoria, consolidador)
    reflexao = Reflexao(memoria, consolidador, caminho("data/logs"), ocupado)
    threading.Thread(target=_aquecer, args=(embedder,), daemon=True).start()
    return memoria, reflexao


def _aquecer(embedder):
    try:
        embedder.aquecer()
    except Exception:
        pass  # sem o modelo de embeddings a memória ainda funciona com o perfil
