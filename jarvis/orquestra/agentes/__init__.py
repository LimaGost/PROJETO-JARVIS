"""Registro de agentes. Cada módulo desta pasta se registra com @agente ao ser importado."""
import importlib
import pkgutil

from jarvis.orquestra.agentes.base import CATEGORIAS, REGISTRO, Agente

_carregado = False


def carregar() -> dict[str, type[Agente]]:
    global _carregado
    if not _carregado:
        for m in pkgutil.iter_modules(__path__):
            if m.name not in ("base", "cli"):
                importlib.import_module(f"{__name__}.{m.name}")
        _carregado = True
    return REGISTRO


def instancias(cfg: dict | None = None, cofre=None) -> dict[str, Agente]:
    """Um objeto por agente (guardam o cache de detecção e quantos passos estão rodando)."""
    return {id_: classe(cfg, cofre) for id_, classe in carregar().items()}
