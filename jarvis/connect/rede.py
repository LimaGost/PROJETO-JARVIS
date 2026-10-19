"""Chamadas HTTP dos conectores: repetição com espera crescente, limite de ritmo por serviço e registro de consumo.

  - 429 e erros 5xx/rede: tenta de novo (até 3x), respeitando o Retry-After quando o serviço manda;
  - 4xx (pedido errado, sem permissão): não repete, vira erro explicado;
  - cada serviço tem um balde de fichas (ex.: 1 chamada/s com rajada de 5) para nunca martelar uma API;
  - toda chamada conta no painel (consumo por serviço/dia).
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from jarvis.tools.base import ErroFerramenta

TIMEOUT_S = 15
TENTATIVAS = 3


class Balde:
    """Limite de ritmo: `por_s` fichas por segundo, até `rajada` acumuladas."""

    def __init__(self, por_s: float = 1.0, rajada: int = 5, relogio=time.monotonic, dormir=time.sleep):
        self.por_s, self.rajada, self.relogio, self.dormir = por_s, rajada, relogio, dormir
        self._fichas = float(rajada)
        self._ultimo = relogio()
        self._lock = threading.Lock()

    def pegar(self):
        with self._lock:
            agora = self.relogio()
            self._fichas = min(self.rajada, self._fichas + (agora - self._ultimo) * self.por_s)
            self._ultimo = agora
            espera = 0.0 if self._fichas >= 1 else (1 - self._fichas) / self.por_s
            self._fichas -= 1
        if espera > 0:
            self.dormir(espera)


_baldes: dict[str, Balde] = {}
_banco = None          # jarvis.connect.banco.Banco, ligado pelo motor


def ligar_banco(banco):
    global _banco
    _banco = banco


def balde(conector: str) -> Balde:
    if conector not in _baldes:
        _baldes[conector] = Balde()
    return _baldes[conector]


def _contar(conector: str, erro: bool):
    if _banco is not None:
        try:
            _banco.contar_chamada(conector, erro)
        except Exception:
            pass


def requisitar(conector: str, metodo: str, url: str, corpo=None, cabecalhos: dict | None = None,
               params: dict | None = None, abrir=urllib.request.urlopen, dormir=time.sleep,
               tentativas: int = TENTATIVAS, timeout: float = TIMEOUT_S):
    """JSON de resposta (ou None). Erros viram ErroFerramenta com texto em português."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    dados = json.dumps(corpo).encode() if corpo is not None else None
    h = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Jarvis-Connect/1.0",
         **(cabecalhos or {})}
    ultimo = None
    for tentativa in range(1, tentativas + 1):
        balde(conector).pegar()
        req = urllib.request.Request(url, dados, h, method=metodo)
        try:
            with abrir(req, timeout=timeout) as r:
                texto = r.read()
            _contar(conector, False)
            if not texto:
                return None
            try:
                return json.loads(texto)
            except ValueError:
                return texto.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            _contar(conector, True)
            if e.code == 429 or e.code >= 500:
                ultimo = e
                espera = _retry_after(e) or 2 ** (tentativa - 1)
                if tentativa < tentativas:
                    dormir(min(espera, 30))
                    continue
            raise ErroFerramenta(_explicar(conector, e))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            _contar(conector, True)
            ultimo = e
            if tentativa < tentativas:
                dormir(2 ** (tentativa - 1))
                continue
    motivo = getattr(ultimo, "reason", None) or ultimo
    raise ErroFerramenta(f"não consegui falar com {conector} depois de {tentativas} tentativa(s) ({motivo})")


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    try:
        return float(e.headers.get("Retry-After", ""))
    except (TypeError, ValueError, AttributeError):
        return None


def _explicar(conector: str, e: urllib.error.HTTPError) -> str:
    detalhe = ""
    try:
        corpo = json.loads(e.read() or b"{}")
        detalhe = str(corpo.get("message") or corpo.get("error") or corpo.get("response", {}).get("message") or "")
    except Exception:
        pass
    dica = {401: "credencial inválida ou vencida", 403: "sem permissão para isso", 404: "não encontrado",
            429: "limite de requisições estourou"}.get(e.code, "")
    return f"{conector} recusou o pedido (erro {e.code}{': ' + dica if dica else ''}){' ' + detalhe[:160] if detalhe else ''}"
