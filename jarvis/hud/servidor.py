"""Servidor local da HUD (FastAPI + WebSocket), dentro do processo do Jarvis.

Segurança. Qualquer página aberta no seu navegador consegue tentar conectar em 127.0.0.1, e a HUD
pode confirmar ações 🔴 (apagar, desligar). Por isso:
  - só escuta em 127.0.0.1;
  - confere o cabeçalho Host (contra DNS rebinding) e o Origin do WebSocket;
  - exige um token aleatório, entregue só na página da própria HUD (outro site não consegue lê-la);
  - CSP sem scripts inline e página proibida de ficar dentro de iframe.
"""
import asyncio
import json
import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from jarvis.hud.bus import bus

PASTA_WEB = Path(__file__).with_name("web")
TEMAS = ("dourado", "ciano")      # hud.tema: dourado = J.A.R.V.I.S. do MCU (padrão) | ciano = visual frio anterior
MAX_MENSAGEM = 4000
INTERVALO_NIVEL_S = 0.05
INTERVALO_STATS_S = 2.0


def porta_livre(host: str, preferida: int) -> int:
    for porta in range(preferida, preferida + 20):
        with socket.socket() as s:
            try:
                s.bind((host, porta))
                return porta
            except OSError:
                continue
    raise RuntimeError(f"nenhuma porta livre a partir de {preferida}")


class ServidorHUD:
    def __init__(self, controle, host: str = "127.0.0.1", porta: int = 8765):
        self.controle = controle
        self.host = host
        self.porta = porta_livre(host, porta)
        self.token = secrets.token_urlsafe(24)
        tema = ((getattr(controle, "cfg", None) or {}).get("hud", {}) or {}).get("tema", "dourado")
        self.tema = tema if tema in TEMAS else "dourado"     # entra no HTML: só valores conhecidos
        self.hosts_ok = {f"127.0.0.1:{self.porta}", f"localhost:{self.porta}"}
        self.origens_ok = {f"http://{h}" for h in self.hosts_ok}
        self._conexoes: set[asyncio.Queue] = set()
        # threads de trabalho criadas já na partida: criar thread no meio do carregamento de uma DLL pode travar o Windows
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hud")
        for _ in [self._pool.submit(time.sleep, 0.05) for _ in range(4)]:
            pass
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: uvicorn.Server | None = None
        self.app = self._criar_app()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.porta}/"

    # ------------------------------------------------------------ app
    def _criar_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.middleware("http")
        async def proteger(request: Request, call_next):
            if request.headers.get("host", "") not in self.hosts_ok:
                return JSONResponse({"erro": "host inválido"}, status_code=403)
            resposta = await call_next(request)
            resposta.headers["Cache-Control"] = "no-store"
            resposta.headers["X-Frame-Options"] = "DENY"
            resposta.headers["X-Content-Type-Options"] = "nosniff"
            resposta.headers["Referrer-Policy"] = "no-referrer"
            resposta.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                f"connect-src 'self' ws://127.0.0.1:{self.porta} ws://localhost:{self.porta}; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            return resposta

        @app.get("/", response_class=HTMLResponse)
        async def pagina():
            html = (PASTA_WEB / "index.html").read_text(encoding="utf-8")
            return html.replace("__TOKEN__", self.token).replace("__TEMA__", self.tema)

        @app.get("/web/{arquivo}")
        async def estatico(arquivo: str):
            caminho = (PASTA_WEB / arquivo).resolve()
            if caminho.parent != PASTA_WEB.resolve() or not caminho.is_file() or arquivo == "index.html":
                return JSONResponse({"erro": "não encontrado"}, status_code=404)
            return FileResponse(caminho)

        @app.websocket("/ws")
        async def ws(websocket: WebSocket):
            if (websocket.headers.get("host", "") not in self.hosts_ok
                    or websocket.headers.get("origin", "") not in self.origens_ok
                    or not secrets.compare_digest(websocket.query_params.get("token", ""), self.token)):
                await websocket.close(code=4403)
                return
            await websocket.accept()
            await self._atender(websocket)

        return app

    # ------------------------------------------------------------ websocket
    async def _atender(self, websocket: WebSocket):
        fila: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._conexoes.add(fila)
        bus.conexoes = len(self._conexoes)
        try:
            await websocket.send_json({"tipo": "hello", **self.controle.hello(), "historico": bus.historico()})
            self.controle.enviar_memoria()
            enviar = asyncio.create_task(self._enviar(websocket, fila))
            try:
                while True:
                    texto = await websocket.receive_text()
                    if len(texto) > MAX_MENSAGEM:
                        continue
                    try:
                        mensagem = json.loads(texto)
                    except ValueError:
                        continue
                    if isinstance(mensagem, dict):
                        await asyncio.get_running_loop().run_in_executor(self._pool, self.tratar, mensagem)
            finally:
                enviar.cancel()
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            self._conexoes.discard(fila)
            bus.conexoes = len(self._conexoes)

    async def _enviar(self, websocket: WebSocket, fila: asyncio.Queue):
        while True:
            await websocket.send_json(await fila.get())

    def tratar(self, m: dict):
        """Mensagens da HUD para o Jarvis. Valida tudo: o que chega aqui é entrada não confiável."""
        c = self.controle
        tipo = m.get("tipo")
        if tipo == "texto" and isinstance(m.get("texto"), str):
            threading.Thread(target=c.texto_do_hud, args=(m["texto"],), daemon=True).start()
        elif tipo == "confirmar" and isinstance(m.get("id"), int) and isinstance(m.get("sim"), bool):
            c.responder_confirmacao(m["id"], m["sim"], "hud")
        elif tipo == "parar":
            c.parar()
        elif tipo == "esquecer":
            c.esquecer_conversa()
        elif tipo == "mudo" and isinstance(m.get("valor"), bool):
            c.silenciar(m["valor"])
        elif tipo == "trocar_cerebro" and isinstance(m.get("id"), str) and len(m["id"]) < 30:
            threading.Thread(target=c.trocar_cerebro, args=(m["id"],), daemon=True).start()  # pode levar segundos
        elif tipo == "trocar_modelo_claude" and isinstance(m.get("modelo"), str) and len(m["modelo"]) < 20:
            threading.Thread(target=c.trocar_modelo_claude, args=(m["modelo"],), daemon=True).start()
        elif tipo == "pedir_memoria":
            c.enviar_memoria()
        elif tipo == "apagar_fato" and isinstance(m.get("id"), int):
            c.apagar_fato(m["id"])

    # ------------------------------------------------------------ difusão
    def _difundir(self, evento: dict):
        loop = self._loop
        if loop is None or not self._conexoes:
            return
        for fila in list(self._conexoes):
            loop.call_soon_threadsafe(self._colocar, fila, evento)

    @staticmethod
    def _colocar(fila: asyncio.Queue, evento: dict):
        try:
            fila.put_nowait(evento)
        except asyncio.QueueFull:
            pass  # cliente travado: perde eventos em vez de acumular memória

    async def _telemetria(self):
        ultimo_stats = 0.0
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(INTERVALO_NIVEL_S)
            if not self._conexoes:
                continue
            self._difundir({"tipo": "nivel", **self.controle.niveis()})
            agora = time.time()
            if agora - ultimo_stats >= INTERVALO_STATS_S:
                ultimo_stats = agora
                stats = await loop.run_in_executor(self._pool, self.controle.estatisticas)
                self._difundir({"tipo": "stats", **stats})

    # ------------------------------------------------------------ ciclo de vida
    def iniciar(self, esperar_s: float = 8.0):
        config = uvicorn.Config(self.app, host=self.host, port=self.porta, log_level="warning",
                                access_log=False, ws_max_size=65536, lifespan="off")
        self._server = uvicorn.Server(config)
        pronto = threading.Event()

        async def principal():
            self._loop = asyncio.get_running_loop()
            bus.assinar(self._difundir)
            tarefa = asyncio.create_task(self._telemetria())
            pronto.set()
            try:
                await self._server.serve()
            finally:
                tarefa.cancel()
                bus.cancelar(self._difundir)

        threading.Thread(target=lambda: asyncio.run(principal()), daemon=True, name="hud-servidor").start()
        pronto.wait(esperar_s)
        limite = time.time() + esperar_s
        while not self._server.started and time.time() < limite:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("o servidor da HUD não iniciou")

    def parar(self):
        if self._server:
            self._server.should_exit = True
