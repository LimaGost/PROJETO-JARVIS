"""A cabeça do Jarvis Core: planejar, julgar propostas e escrever o relatório final.

Roda isolada, como a IA das automações: `claude -p --tools ""` numa pasta vazia, sem MCP, saída JSON (com custo e
tokens). Sem o claude, usa o Qwen local (Ollama). Ela só lê texto e devolve texto: não executa nada.
"""
import json
import re
import time

from jarvis import recursos
from jarvis.config import RAIZ
from jarvis.orquestra import gateway
from jarvis.orquestra.seguranca import ambiente_limpo

PASTA = RAIZ / "data" / "orquestra" / "nucleo"
MODELO = "sonnet"
LIMITE_S = 180
PAPEL = ("Você é o núcleo do Jarvis, que coordena outros agentes de IA. Siga as instruções da mensagem e responda "
         "exatamente no formato pedido.")       # vai como argumento: texto fixo, sem caracteres especiais


class RespostaIA:
    def __init__(self, texto: str, custo: float = 0.0, entrada: int = 0, saida: int = 0, motor: str = ""):
        self.texto, self.custo, self.entrada, self.saida, self.motor = texto, custo, entrada, saida, motor


def _claude(prompt: str, sistema: str, modelo: str, limite_s: float) -> RespostaIA | None:
    cmd = gateway.resolver_executavel("claude")
    if cmd is None:
        return None
    PASTA.mkdir(parents=True, exist_ok=True)
    argv = [*cmd, "-p", "--output-format", "json", "--strict-mcp-config", "--tools", "", "--model", modelo,
            "--max-turns", "1", "--no-session-persistence", "--append-system-prompt", PAPEL]
    codigo, saida, _ = gateway.rodar(argv, cwd=PASTA, env=ambiente_limpo(), entrada=f"{sistema}\n\n{prompt}",
                                     limite_s=limite_s)
    try:
        r = json.loads(saida)
    except ValueError:
        return None
    if codigo != 0 or r.get("is_error"):
        return None
    u = r.get("usage") or {}
    return RespostaIA(str(r.get("result") or "").strip(), float(r.get("total_cost_usd") or 0),
                      int((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)),
                      int(u.get("output_tokens") or 0), f"claude {modelo}")


def _ollama(prompt: str, sistema: str, cfg: dict, limite_s: float) -> RespostaIA:
    import urllib.request
    llm = cfg.get("llm", {}) or {}
    host = str(llm.get("host", "http://127.0.0.1:11434")).replace("localhost", "127.0.0.1").rstrip("/")
    corpo = {"model": llm.get("modelo", "qwen3:8b"), "system": sistema, "prompt": prompt[:16000] + " /no_think",
             "stream": False, "options": {"num_ctx": 6144},
             "keep_alive": recursos.keep_alive(llm.get("keep_alive_reserva", "2m"))}
    req = urllib.request.Request(f"{host}/api/generate", json.dumps(corpo).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=limite_s) as r:
        dados = json.load(r)
    texto = re.sub(r"<think>.*?</think>", "", dados.get("response", ""), flags=re.S).strip()
    return RespostaIA(texto, 0.0, int(dados.get("prompt_eval_count") or 0), int(dados.get("eval_count") or 0),
                      f"ollama {corpo['model']}")


def pensar(prompt: str, sistema: str, cfg: dict | None = None, modelo: str | None = None,
           limite_s: float = LIMITE_S) -> RespostaIA:
    cfg = cfg or {}
    modelo = modelo or ((cfg.get("orquestra", {}) or {}).get("modelo_nucleo")) or MODELO
    inicio = time.time()
    try:
        r = _claude(prompt, sistema, modelo, limite_s)
    except Exception:
        r = None
    if r is not None and r.texto:
        return r
    return _ollama(prompt, sistema, cfg, max(30.0, limite_s - (time.time() - inicio)))


def extrair_json(texto: str):
    """Primeiro objeto JSON da resposta (às vezes vem dentro de ```json)."""
    m = re.search(r"\{.*\}", texto or "", flags=re.S)
    if not m:
        raise ValueError("a resposta não tem JSON")
    return json.loads(m.group(0))
