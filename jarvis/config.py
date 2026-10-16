from pathlib import Path
import yaml

RAIZ = Path(__file__).resolve().parent.parent


def carregar() -> dict:
    with open(RAIZ / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def caminho(rel: str) -> str:
    return str(RAIZ / rel)
