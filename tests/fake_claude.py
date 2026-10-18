"""`claude` de mentira para os testes: lê mensagens stream-json no stdin e emite eventos no formato real.
Comportamento por variável de ambiente FAKE_INIT (ok | ruim_bash | ruim_mcp | ruim_skill) e pelo texto recebido."""
import json
import os
import sys
import time

modo = os.environ.get("FAKE_INIT", "ok")
INIT = {"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-sonnet-5-5", "tools": ["mcp__jarvis__hora_data"],
        "mcp_servers": [{"name": "jarvis", "status": "connected"}], "skills": [], "slash_commands": [],
        "plugins": [{"name": "cc-plugin-agents-md", "path": "builtin"}], "permissionMode": "dontAsk",
        "env_chave": bool(os.environ.get("ANTHROPIC_API_KEY")), "argv": sys.argv[1:]}
if modo == "ruim_bash":
    INIT["tools"] = ["Bash", "mcp__jarvis__hora_data"]
elif modo == "ruim_mcp":
    INIT["mcp_servers"].append({"name": "claude.ai Gmail", "status": "connected"})
elif modo == "ruim_skill":
    INIT["skills"] = ["deploy"]


def emitir(ev):
    sys.stdout.write(json.dumps(ev, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def texto(t):
    emitir({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": "text_delta", "text": t}}})


def resultado(t="", erro=False, sub="success"):
    emitir({"type": "result", "subtype": sub, "is_error": erro, "result": t, "total_cost_usd": 0.0, "num_turns": 1})


primeira = True
for linha in sys.stdin:
    if not linha.strip():
        continue
    msg = json.loads(linha)
    if msg.get("type") == "control_request":     # interromper: o pedido acaba e o processo continua vivo
        emitir({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"]}})
        continue
    recebido = msg["message"]["content"][0]["text"]
    if primeira:
        emitir(INIT)
        primeira = False
    if recebido.startswith("Responda somente"):
        resultado("ok")
    elif "MORRER" in recebido:
        sys.exit(3)
    elif "DEMORA" in recebido:
        time.sleep(30)
    elif "ERRO_API" in recebido:
        resultado("API Error: 401 authentication_error", erro=True, sub="error_during_execution")
    elif "MAXTURNOS" in recebido:
        resultado("", erro=True, sub="error_max_turns")
    elif "SEM_STREAM" in recebido:
        resultado("Sem pedaços parciais. Chegou inteira, senhor.")
    elif "LENTO" in recebido:
        for p in ["Primeira frase. ", "Segunda ", "frase continua. "]:
            texto(p)
            time.sleep(0.4)
        resultado("Primeira frase. Segunda frase continua.")
    else:
        emitir({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__jarvis__hora_data", "input": {}}]}})
        texto("Olá, senhor. ")
        texto("Tudo bem")
        texto(" por aqui. ECO" + recebido.replace("\n", " ")[:600])
        resultado("Olá, senhor. Tudo bem por aqui.")
