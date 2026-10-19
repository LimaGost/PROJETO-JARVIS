"""Execution Monitor: tudo que os agentes fazem passa por aqui.

- Cada evento (texto, comando, arquivo, uso, erro) vai para o banco já sem segredos: é a auditoria completa.
- A HUD recebe em tempo real, em lotes (no máximo a cada 0,4 s), sem inundar o WebSocket.
- O estado ao vivo de cada agente (o que está fazendo, há quanto tempo, quanto gastou) alimenta os cartões.
"""
import threading
import time
from typing import Callable

INTERVALO_S = 0.4
MAX_LOTE = 50


class Monitor:
    def __init__(self, banco, redator, emitir: Callable | None = None):
        self.banco, self.redator = banco, redator
        self.emitir = emitir or (lambda tipo, **k: None)
        self._fila: list[dict] = []
        self._lock = threading.Lock()
        self._acordar = threading.Event()
        self._thread: threading.Thread | None = None
        self.ao_vivo: dict[tuple[int, str], dict] = {}

    # ------------------------------------------------------------ logs
    def log(self, execucao_id: int | None, passo: str | None, agente: str | None, tipo: str, texto: str):
        texto = self.redator(str(texto or ""))
        id_ = self.banco.log(execucao_id, passo, agente, tipo, texto)
        vivo = self.ao_vivo.get((execucao_id, passo))
        if vivo is not None:
            vivo["ultimo"] = f"{tipo}: {texto[:160]}"
            vivo["eventos"] += 1
        with self._lock:
            self._fila.append({"id": id_, "execucao": execucao_id, "passo": passo, "agente": agente, "tipo": tipo,
                               "conteudo": texto[:600], "quando": time.strftime("%H:%M:%S")})
        self._garantir_thread()
        self._acordar.set()

    def _garantir_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._despachar, daemon=True, name="orq-monitor")
            self._thread.start()

    def _despachar(self):
        while True:
            self._acordar.wait(5)
            self._acordar.clear()
            time.sleep(INTERVALO_S)
            with self._lock:
                lote, self._fila = self._fila[:MAX_LOTE], self._fila[MAX_LOTE:]
                sobra = bool(self._fila)
            if lote:
                try:
                    self.emitir("orq", acao="logs", linhas=lote)
                except Exception:
                    pass
            if sobra:
                self._acordar.set()

    def mudou(self, execucao_id: int | None = None, passo: str | None = None, **extra):
        try:
            self.emitir("orq", acao="estado", execucao=execucao_id, passo=passo, **extra)
        except Exception:
            pass

    # ------------------------------------------------------------ estado ao vivo
    def comecou(self, execucao_id: int, passo: str, agente: str, titulo: str, controle):
        self.ao_vivo[(execucao_id, passo)] = {"execucao": execucao_id, "passo": passo, "agente": agente,
                                              "titulo": titulo, "inicio": time.time(), "ultimo": "começando",
                                              "eventos": 0, "controle": controle, "custo": 0.0, "pausado": False}
        self.mudou(execucao_id, passo)

    def terminou(self, execucao_id: int, passo: str):
        self.ao_vivo.pop((execucao_id, passo), None)
        self.mudou(execucao_id, passo)

    def por_agente(self) -> dict[str, list[dict]]:
        saida: dict[str, list[dict]] = {}
        for v in list(self.ao_vivo.values()):
            processo = getattr(v["controle"], "processo", None)
            item = {k: v[k] for k in ("execucao", "passo", "titulo", "ultimo", "eventos", "custo", "pausado")}
            item["tempo_s"] = round(processo.tempo_ativo() if processo is not None else time.time() - v["inicio"], 1)
            item["memoria_mb"] = round(processo.memoria_mb(), 1) if processo is not None and processo.rodando else 0
            saida.setdefault(v["agente"], []).append(item)
        return saida
