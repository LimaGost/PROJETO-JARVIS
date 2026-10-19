"""Servidor MCP mínimo (stdio, JSON-RPC 2.0) para os testes do MCP Gateway: duas páginas de ferramentas (nextCursor),
uma ferramenta só-leitura (readOnlyHint) e uma que erra. Também manda um ping ao cliente, como servidores reais fazem."""
import json
import sys

FERRAMENTAS = [
    [{"name": "eco", "description": "devolve o texto", "inputSchema": {"type": "object"},
      "annotations": {"readOnlyHint": True}}],
    [{"name": "falha", "description": "sempre erra", "inputSchema": {"type": "object"}}],
]


def responder(id_, resultado=None, erro=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg.update({"error": erro} if erro else {"result": resultado})
    print(json.dumps(msg), flush=True)


for linha in sys.stdin:
    msg = json.loads(linha)
    metodo, id_ = msg.get("method"), msg.get("id")
    if id_ is None:
        if metodo == "notifications/initialized":
            print(json.dumps({"jsonrpc": "2.0", "id": "s1", "method": "ping"}), flush=True)
        continue
    if metodo == "initialize":
        responder(id_, {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                        "serverInfo": {"name": "teste", "version": "1.0"}})
    elif metodo == "tools/list":
        pagina = int((msg.get("params") or {}).get("cursor") or 0)
        r = {"tools": FERRAMENTAS[pagina]}
        if pagina + 1 < len(FERRAMENTAS):
            r["nextCursor"] = str(pagina + 1)
        responder(id_, r)
    elif metodo == "tools/call":
        p = msg["params"]
        if p["name"] == "eco":
            responder(id_, {"content": [{"type": "text", "text": "eco: " + str(p["arguments"].get("texto"))}]})
        else:
            responder(id_, {"content": [{"type": "text", "text": "deu errado"}], "isError": True})
    else:
        responder(id_, erro={"code": -32601, "message": "método desconhecido"})
