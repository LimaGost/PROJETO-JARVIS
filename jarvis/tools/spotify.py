"""Tocar música pelo nome com a Web API do Spotify (OAuth PKCE, só biblioteca padrão).

Autorizar uma vez:  python -m jarvis.tools.spotify
O token fica em data/spotify/token.json (fora do git). Controlar a reprodução exige Spotify Premium.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from jarvis.tools.base import ErroFerramenta, ferramenta

CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "4ebc30c24eb64e4a848eb45595b3757b")  # público, não é segredo
REDIRECT = "http://127.0.0.1:8888/callback"
ESCOPOS = "user-modify-playback-state user-read-playback-state user-read-currently-playing"
TOKEN = Path(__file__).resolve().parents[2] / "data" / "spotify" / "token.json"
API = "https://api.spotify.com/v1"
CONTA = "https://accounts.spotify.com"


def _post_token(dados: dict) -> dict:
    req = urllib.request.Request(f"{CONTA}/api/token", urllib.parse.urlencode(dados).encode(),
                                 {"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _salvar(tok: dict, antigo: dict | None = None):
    tok["expira_em"] = time.time() + tok.get("expires_in", 3600) - 60
    tok.setdefault("refresh_token", (antigo or {}).get("refresh_token"))  # o refresh pode não vir de novo
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(json.dumps(tok), encoding="utf-8")


def autorizar():
    """Abre o navegador para o login e guarda o token. Roda uma vez."""
    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    estado = secrets.token_urlsafe(16)
    url = f"{CONTA}/authorize?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID, "response_type": "code", "redirect_uri": REDIRECT, "scope": ESCOPOS,
        "code_challenge_method": "S256", "code_challenge": desafio, "state": estado})
    recebido: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            recebido.update({k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Jarvis autorizado. Pode fechar esta aba.</h2>".encode())

        def log_message(self, *a):
            pass

    servidor = HTTPServer(("127.0.0.1", 8888), Handler)
    print("Abrindo o navegador para autorizar o Spotify...")
    webbrowser.open(url)
    servidor.timeout = 180
    servidor.handle_request()
    servidor.server_close()
    if recebido.get("state") != estado or "code" not in recebido:
        raise SystemExit(f"Autorização falhou: {recebido.get('error', 'sem resposta')}")
    _salvar(_post_token({"client_id": CLIENT_ID, "grant_type": "authorization_code", "code": recebido["code"],
                         "redirect_uri": REDIRECT, "code_verifier": verificador}))
    print("Pronto. Token salvo em", TOKEN)


def _token() -> str:
    if not TOKEN.exists():
        raise ErroFerramenta("Spotify ainda não autorizado. O usuário precisa rodar 'python -m jarvis.tools.spotify' uma vez.")
    tok = json.loads(TOKEN.read_text(encoding="utf-8"))
    if time.time() >= tok["expira_em"]:
        try:
            novo = _post_token({"client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        except urllib.error.URLError as e:
            raise ErroFerramenta(f"não consegui renovar o login do Spotify ({e}); rode 'python -m jarvis.tools.spotify'")
        _salvar(novo, tok)
        tok = novo
    return tok["access_token"]


def _api(metodo: str, caminho: str, params: dict | None = None, corpo: dict | None = None):
    url = API + caminho + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, json.dumps(corpo).encode() if corpo is not None else None, method=metodo,
                                 headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
    try:
        for tentativa in (1, 2):  # a rede às vezes engasga na 1ª chamada (DNS/IPv6); uma repetição resolve
            try:
                with urllib.request.urlopen(req, timeout=8) as r:
                    texto = r.read()
                    return json.loads(texto) if texto else None
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, TimeoutError):
                if tentativa == 2:
                    raise
    except urllib.error.HTTPError as e:
        detalhe = ""
        try:
            detalhe = json.load(e).get("error", {}).get("reason", "")
        except Exception:
            pass
        if e.code == 403 and detalhe == "PREMIUM_REQUIRED":
            raise ErroFerramenta("o Spotify só deixa controlar a reprodução por API com conta Premium")
        if e.code == 404 and detalhe == "NO_ACTIVE_DEVICE":
            raise ErroFerramenta("NO_ACTIVE_DEVICE")
        raise ErroFerramenta(f"o Spotify recusou o pedido (erro {e.code} {detalhe})")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ErroFerramenta(f"sem conexão com o Spotify ({getattr(e, 'reason', e)})")


def _tocar(corpo: dict, dispositivo: str | None = None):
    _api("PUT", "/me/player/play", {"device_id": dispositivo} if dispositivo else None, corpo)


def _nome(faixa: dict) -> str:
    return f"{faixa['name']}, de {', '.join(a['name'] for a in faixa['artists'])}"


def _norm(texto: str) -> str:
    sem = unicodedata.normalize("NFD", texto.lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in sem if not unicodedata.combining(c)))


def _ordenar(consulta: str, faixas: list[dict]) -> list[dict]:
    """A busca do Spotify mistura por relevância ("1993 Matuê" devolvia "333"). Reordena: o título tem que
    bater com as palavras do pedido que não são o nome do artista; popularidade só desempata."""
    palavras = _norm(consulta).split()

    def nota(f: dict) -> float:
        artistas = _norm(" ".join(a["name"] for a in f["artists"])).split()
        resto = [p for p in palavras if p not in artistas]
        titulo = _norm(f["name"]).split()
        pontos = len(palavras) - len(resto)  # palavras que são o artista
        if resto and titulo == resto:
            pontos += 10
        elif resto and all(p in titulo for p in resto):
            pontos += 5
        elif resto:
            pontos += sum(p in titulo for p in resto)
        return pontos + f.get("popularity", 0) / 1000

    return sorted(faixas, key=nota, reverse=True)


@ferramenta(
    "tocar_musica",
    "Busca no catálogo inteiro do Spotify (não só a biblioteca do usuário) e toca a música pedida. "
    "Use para qualquer pedido de música, com nome e artista. Se o usuário disser que não é essa, chame de novo "
    "com 'opcao' 2, 3... ou com uma busca mais específica. O Spotify precisa estar aberto.",
    {"consulta": {"type": "string", "description": "Nome da música e artista, ex.: '1993 Matuê'"},
     "opcao": {"type": "integer", "description": "Qual resultado tocar (1 = melhor). Padrão: 1"}},
    obrigatorios=("consulta",),
)
def tocar_musica(consulta: str, opcao: int = 1) -> str:
    achados = _ordenar(consulta, _api("GET", "/search", {"q": consulta, "type": "track", "limit": 10})["tracks"]["items"])
    if not achados:
        return f"O Spotify não encontrou nenhuma música para '{consulta}'."
    faixa = achados[min(max(opcao, 1), len(achados)) - 1]
    outras = "; ".join(_nome(f) for f in achados[:5] if f is not faixa)
    corpo = {"uris": [faixa["uri"]]}
    try:
        _tocar(corpo)
    except ErroFerramenta as e:
        if str(e) != "NO_ACTIVE_DEVICE":
            raise
        # app aberto mas sem sessão ativa: escolhe um dispositivo e toca nele
        dispositivos = _api("GET", "/me/player/devices")["devices"]
        if not dispositivos:
            raise ErroFerramenta("o Spotify não está aberto em nenhum dispositivo; abra o app primeiro")
        pc = next((d for d in dispositivos if d["type"] == "Computer"), dispositivos[0])
        _tocar(corpo, pc["id"])
    for _ in range(6):  # confirma pelo que o Spotify diz que está tocando de fato
        time.sleep(0.7)
        atual = _api("GET", "/me/player/currently-playing")
        if atual and atual.get("is_playing") and atual.get("item"):
            tocando = atual["item"]
            if tocando["uri"] == faixa["uri"]:
                return f"Tocando agora: {_nome(tocando)}. Outras opções da busca: {outras}."
            return (f"Pedi '{_nome(faixa)}', mas o Spotify está tocando '{_nome(tocando)}'. "
                    "Não foi possível confirmar a música pedida.")
    return f"Enviei '{_nome(faixa)}' ao Spotify, mas não consegui confirmar que começou a tocar."


if __name__ == "__main__":
    autorizar()
