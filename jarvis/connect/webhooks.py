"""Servidor SÓ de webhooks, separado da HUD (127.0.0.1:8766).

Por que separado: para receber eventos de serviços da internet é preciso um endereço público (Cloudflare Tunnel). O túnel
aponta só para cá; a HUD, que confirma ações perigosas, continua inalcançável de fora.

Cada rota exige prova de origem:
  /webhook/evolution         cabeçalho x-jarvis-segredo (a Evolution manda os cabeçalhos que configuramos)
  /webhook/geral/{segredo}   para serviços que só aceitam uma URL (o segredo vai no caminho)
  assinatura HMAC-SHA256 (X-Hub-Signature-256, padrão Meta/GitHub) pronta em `assinatura_ok` para os próximos
Corpo até 256 KB, eventos repetidos são descartados (o motor guarda a chave de cada execução).
"""
import asyncio
import hashlib
import hmac
import json
import secrets
import threading
import time

MAX_CORPO = 256 * 1024
PORTA = 8766

_servidor = None
_porta = PORTA


def assinatura_ok(segredo: str, corpo: bytes, cabecalho: str) -> bool:
    """X-Hub-Signature-256: 'sha256=<hex do HMAC do corpo>'."""
    if not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho[7:])


def criar_app(motor, cofre, banco):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    from jarvis.connect.evolution import mensagem_do_webhook

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    vistos: dict[str, float] = {}

    def repetido(chave: str) -> bool:
        agora = time.time()
        for k in [k for k, t in vistos.items() if agora - t > 3600]:
            vistos.pop(k, None)
        if chave in vistos:
            return True
        vistos[chave] = agora
        return False

    async def ler(request: Request) -> bytes | None:
        corpo = b""
        async for pedaco in request.stream():
            corpo += pedaco
            if len(corpo) > MAX_CORPO:
                return None
        return corpo

    @app.post("/webhook/evolution")
    async def evolution(request: Request):
        segredo = cofre.segredo("evolution")
        if not secrets.compare_digest(request.headers.get("x-jarvis-segredo", ""), segredo):
            banco.evento("webhook", "webhook da Evolution recusado: segredo errado", "evolution", "alerta")
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
        corpo = await ler(request)
        if corpo is None:
            return JSONResponse({"erro": "grande demais"}, status_code=413)
        try:
            dados = json.loads(corpo)
        except ValueError:
            return JSONResponse({"erro": "json inválido"}, status_code=400)
        msg = mensagem_do_webhook(dados) if isinstance(dados, dict) else None
        if msg and msg["texto"] and not msg["grupo"] and not repetido("evo:" + msg["id"]):
            banco.contar_chamada("evolution-webhook")
            banco.evento("whatsapp", f"mensagem de {msg['nome'] or msg['de']}", "evolution")
            await asyncio.get_running_loop().run_in_executor(None, motor.publicar, "whatsapp.mensagem", msg,
                                                             msg["id"] or str(time.time()))
        return {"ok": True}

    @app.post("/webhook/geral/{segredo}")
    async def geral(segredo: str, request: Request):
        if not secrets.compare_digest(segredo, cofre.segredo("webhooks")):
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
        corpo = await ler(request)
        if corpo is None:
            return JSONResponse({"erro": "grande demais"}, status_code=413)
        try:
            dados = json.loads(corpo or b"{}")
        except ValueError:
            dados = {"texto": corpo.decode("utf-8", "replace")[:4000]}
        id_ = str(request.headers.get("x-request-id") or hashlib.sha256(corpo).hexdigest()[:24])
        if repetido("geral:" + id_):
            return {"ok": True, "repetido": True}
        banco.evento("webhook", "webhook recebido", "webhooks")
        await asyncio.get_running_loop().run_in_executor(None, motor.publicar, "webhook",
                                                         dados if isinstance(dados, dict) else {"dados": dados}, id_)
        return {"ok": True}

    @app.get("/saude")
    async def saude():
        return {"ok": True}

    return app


def iniciar(motor, cofre, banco, host: str = "127.0.0.1", porta: int = PORTA):
    """Sobe em thread própria. Só 127.0.0.1: o túnel (cloudflared) roda nesta máquina e encaminha para cá."""
    global _servidor, _porta
    import uvicorn

    from jarvis.hud.servidor import porta_livre
    _porta = porta_livre(host, porta)
    config = uvicorn.Config(criar_app(motor, cofre, banco), host=host, port=_porta, log_level="warning",
                            access_log=False, lifespan="off")
    _servidor = uvicorn.Server(config)
    threading.Thread(target=_servidor.run, daemon=True, name="connect-webhooks").start()
    return _porta


def ativo() -> bool:
    return _servidor is not None and getattr(_servidor, "started", False) and not _servidor.should_exit


def endereco() -> str:
    return f"http://127.0.0.1:{_porta}"


def parar():
    if _servidor is not None:
        _servidor.should_exit = True
