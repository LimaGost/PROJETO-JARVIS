"""Quanto o Jarvis usou do Claude: lê os registros que os cérebros Claude gravam a cada pedido.

- claude-cli (assinatura): data/logs/uso_claude_cli.jsonl. Não há cobrança por pedido; o número em dólar é o preço
  equivalente de API, que é a melhor medida de quanto do limite de uso da assinatura foi consumido.
- API: data/logs/uso_claude.jsonl (tokens; esse sim é cobrado por uso).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from jarvis.config import RAIZ
from jarvis.tools.base import ferramenta

PASTA = RAIZ / "data" / "logs"


def _registros(arquivo: Path, desde: datetime) -> list[dict]:
    saida = []
    try:
        with open(arquivo, encoding="utf-8") as f:
            for linha in f:
                try:
                    r = json.loads(linha)
                    if datetime.fromisoformat(r["quando"]) >= desde:
                        saida.append(r)
                except (ValueError, KeyError):
                    continue
    except OSError:
        pass
    return saida


def resumo_uso(periodo: str = "hoje", pasta: Path = PASTA, agora: datetime | None = None) -> str:
    agora = agora or datetime.now()
    inicio_dia = datetime.combine(agora.date(), datetime.min.time())
    desde = {"hoje": inicio_dia, "semana": inicio_dia - timedelta(days=agora.weekday()),
             "5h": agora - timedelta(hours=5)}.get(periodo, inicio_dia)
    nome = {"hoje": "Hoje", "semana": "Nesta semana", "5h": "Nas últimas 5 horas"}.get(periodo, "Hoje")
    cli = [r for r in _registros(pasta / "uso_claude_cli.jsonl", desde) if r.get("tipo", "pedido") != "partida"]
    partidas = [r for r in _registros(pasta / "uso_claude_cli.jsonl", desde) if r.get("tipo") == "partida"]
    if not cli and not partidas:
        return f"{nome}: nenhum pedido ao Claude pela assinatura."
    custo = sum(r.get("custo_api_usd", 0) for r in cli + partidas)
    lidos = sum(r.get("cache_lido", 0) + r.get("entrada", 0) + r.get("cache_escrito", 0) for r in cli + partidas)
    saida = sum(r.get("saida", 0) for r in cli + partidas)
    chamadas = sum(r.get("chamadas", 1) for r in cli)
    media = custo / len(cli) if cli else 0
    return (f"{nome}: {len(cli)} pedidos ao Claude pela assinatura ({chamadas} chamadas ao modelo, {len(partidas)} "
            f"partidas do processo). Tokens lidos: {_br(lidos)}; escritos: {_br(saida)}. Equivalente em preço de API: "
            f"US$ {_br(custo, 2)} (média de US$ {_br(media, 3)} por pedido). Na assinatura isso não é cobrado à parte; "
            f"conta para o limite de uso dela.")


def _br(n: float, casas: int = 0) -> str:
    """1234.5 -> '1.234,50' (como se lê em português)."""
    return f"{n:,.{casas}f}".replace(",", "§").replace(".", ",").replace("§", ".")


@ferramenta(
    "uso_claude",
    "Quanto o Jarvis já usou do Claude (da assinatura do usuário): pedidos, tokens e o equivalente em preço de API.",
    {"periodo": {"type": "string", "enum": ["hoje", "5h", "semana"], "description": "Padrão: hoje"}},
)
def uso_claude(periodo: str = "hoje") -> str:
    return resumo_uso(periodo)
