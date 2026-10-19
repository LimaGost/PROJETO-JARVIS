"""Catálogo de conectores. Cada módulo desta pasta se registra com @conector ao ser importado."""
import importlib
import pkgutil

from jarvis.connect.conectores.base import CATEGORIAS, REGISTRO, Conector

_carregado = False


def carregar() -> dict[str, type[Conector]]:
    global _carregado
    if not _carregado:
        for m in pkgutil.iter_modules(__path__):
            if m.name != "base":
                importlib.import_module(f"{__name__}.{m.name}")
        _carregado = True
    return REGISTRO


def instancia(id_: str, cofre=None, cfg: dict | None = None) -> Conector:
    classes = carregar()
    if id_ not in classes:
        raise KeyError(id_)
    return classes[id_](cofre, cfg)


def catalogo(banco=None, cofre=None, cfg: dict | None = None) -> list[dict]:
    """Cartões de todos os conectores, na ordem das categorias e com os implementados primeiro."""
    ordem = {"implementado": 0, "em_desenvolvimento": 1, "planejado": 2}
    cartoes = [c(cofre, cfg).cartao(banco) for c in carregar().values()]
    return sorted(cartoes, key=lambda c: (CATEGORIAS.index(c["categoria"]), ordem[c["situacao"]], c["nome"]))
