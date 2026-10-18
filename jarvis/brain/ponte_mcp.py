"""Ponte MCP (stdio): o `claude` a executa como um servidor de ferramentas e ela repassa tudo ao JARVIS.

Roda como SCRIPT separado, só com a biblioteca padrão (sobe rápido e não importa nada do projeto).
Não executa nada por conta própria: cada chamada vai por HTTP local (com token) ao Executor do JARVIS,
que decide o nível 🟢🟡🔴, pede confirmação e registra. Variáveis: JARVIS_PONTE_URL, JARVIS_PONTE_TOKEN.

Protocolo MCP sobre stdio = uma mensagem JSON-RPC por linha. stdout é SÓ protocolo; diagnóstico vai para stderr.
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("JARVIS_PONTE_URL", "")
TOKEN = os.environ.get("JARVIS_PONTE_TOKEN", "")
TIMEOUT_EXECUTAR_S = float(os.environ.get("JARVIS_PONTE_TIMEOUT", "300"))  # inclui esperar a sua confirmação
VERSAO_PADRAO = "2025-06-18"


def log(*a):
    print("[ponte]", *a, file=sys.stderr, flush=True)


def chamar(caminho: str, corpo: dict | None = None, timeout: float = 15.0) -> dict:
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(URL + caminho, data=dados, headers={
        "X-Jarvis-Token": TOKEN, "Content-Type": "application/json"}, method="POST" if dados is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def responder(id_, resultado=None, erro=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if erro is not None:
        msg["error"] = erro
    else:
        msg["result"] = resultado
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def tratar(msg: dict):
    metodo, id_, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if id_ is None:
        return  # notificação (notifications/initialized etc.): não se responde
    if metodo == "initialize":
        responder(id_, {"protocolVersion": params.get("protocolVersion", VERSAO_PADRAO),
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "jarvis", "version": "1.0"}})
    elif metodo == "ping":
        responder(id_, {})
    elif metodo == "tools/list":
        try:
            responder(id_, {"tools": chamar("/ferramentas")["ferramentas"]})
        except Exception as e:
            log("falha ao listar ferramentas:", e)
            responder(id_, {"tools": []})
    elif metodo == "tools/call":
        nome, args = params.get("name", ""), params.get("arguments") or {}
        try:
            r = chamar("/executar", {"nome": nome, "args": args}, timeout=TIMEOUT_EXECUTAR_S)
            conteudo = [{"type": "text", "text": r["texto"]}]
            if r.get("imagem"):  # ver_tela / mouse
                conteudo.append({"type": "image", "data": r["imagem"]["dados"], "mimeType": r["imagem"]["tipo"]})
            responder(id_, {"content": conteudo, "isError": bool(r.get("erro"))})
        except urllib.error.HTTPError as e:
            responder(id_, {"content": [{"type": "text", "text": f"Erro: o JARVIS recusou a chamada (HTTP {e.code})."}],
                            "isError": True})
        except Exception as e:
            responder(id_, {"content": [{"type": "text", "text": f"Erro: não consegui falar com o JARVIS ({type(e).__name__})."}],
                            "isError": True})
    elif metodo in ("resources/list", "prompts/list", "resources/templates/list"):
        responder(id_, {metodo.split("/")[0]: []})
    else:
        responder(id_, erro={"code": -32601, "message": f"método desconhecido: {metodo}"})


def main():
    if not URL or not TOKEN:
        log("JARVIS_PONTE_URL/JARVIS_PONTE_TOKEN ausentes")
        return 1
    for linha in sys.stdin:
        linha = linha.strip()
        if not linha:
            continue
        try:
            tratar(json.loads(linha))
        except Exception as e:  # uma mensagem ruim não derruba a ponte
            log("erro:", type(e).__name__, e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
