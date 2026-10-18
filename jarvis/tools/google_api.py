"""Login do Google (OAuth para app de computador, com PKCE) e chamadas às APIs do Gmail, Agenda e Contatos.
Só biblioteca padrão, como o spotify.py.

Configurar uma vez:
  1. No Google Cloud Console, crie um "ID do cliente OAuth" do tipo "App para computador" e baixe o JSON
     para data/google/cliente.json.
  2. Rode:  python -m jarvis.tools.google_api
O token fica em data/google/token.json (fora do git).
"""
import base64
import hashlib
import json
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from jarvis.tools.base import ErroFerramenta

PASTA = Path(__file__).resolve().parents[2] / "data" / "google"
CLIENTE = PASTA / "cliente.json"
TOKEN = PASTA / "token.json"
AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
TROCAR = "https://oauth2.googleapis.com/token"
# o mínimo para o que o Jarvis faz: ler e enviar e-mail (não apaga), eventos da agenda e ler contatos
ESCOPOS = ("https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send "
           "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/contacts.readonly")
COMANDO = "python -m jarvis.tools.google_api"


def _cliente() -> dict:
    try:
        dados = json.loads(CLIENTE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ErroFerramenta(f"a conta Google não está configurada: falta {CLIENTE.name} em data/google (veja o README)")
    return dados.get("installed") or dados.get("web") or dados


def _post_token(dados: dict) -> dict:
    c = _cliente()
    dados = {**dados, "client_id": c["client_id"], "client_secret": c.get("client_secret", "")}
    req = urllib.request.Request(TROCAR, urllib.parse.urlencode(dados).encode(),
                                 {"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _salvar(tok: dict, antigo: dict | None = None):
    tok["expira_em"] = time.time() + tok.get("expires_in", 3600) - 60
    tok.setdefault("refresh_token", (antigo or {}).get("refresh_token"))  # o refresh não vem de novo na renovação
    PASTA.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(json.dumps(tok), encoding="utf-8")


def autorizar():
    """Abre o navegador para o login e guarda o token. Roda uma vez."""
    c = _cliente()
    with socket.socket() as s:            # o Google aceita qualquer porta no 127.0.0.1 para app de computador
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    redirect = f"http://127.0.0.1:{porta}"
    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    estado = secrets.token_urlsafe(16)
    url = AUTORIZAR + "?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "response_type": "code", "redirect_uri": redirect, "scope": ESCOPOS,
        "code_challenge_method": "S256", "code_challenge": desafio, "state": estado,
        "access_type": "offline", "prompt": "consent"})
    recebido: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            recebido.update({k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Jarvis autorizado no Google. Pode fechar esta aba.</h2>".encode())

        def log_message(self, *a):
            pass

    servidor = HTTPServer(("127.0.0.1", porta), Handler)
    print("Abrindo o navegador para autorizar o Google (Gmail, Agenda e Contatos)...")
    webbrowser.open(url)
    servidor.timeout = 300
    servidor.handle_request()
    servidor.server_close()
    if recebido.get("state") != estado or "code" not in recebido:
        raise SystemExit(f"Autorização falhou: {recebido.get('error', 'sem resposta')}")
    _salvar(_post_token({"grant_type": "authorization_code", "code": recebido["code"], "redirect_uri": redirect,
                         "code_verifier": verificador}))
    print("Pronto. Token salvo em", TOKEN)


def _token() -> str:
    if not TOKEN.exists():
        raise ErroFerramenta(f"a conta Google ainda não foi autorizada. O usuário precisa rodar '{COMANDO}' uma vez.")
    tok = json.loads(TOKEN.read_text(encoding="utf-8"))
    if time.time() >= tok.get("expira_em", 0):
        try:
            novo = _post_token({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        except urllib.error.HTTPError:
            # app do Google Cloud em modo "Teste" vence o login em 7 dias (veja o README)
            raise ErroFerramenta(f"o login do Google venceu; o usuário precisa rodar '{COMANDO}' de novo")
        except (urllib.error.URLError, TimeoutError) as e:
            raise ErroFerramenta(f"sem conexão com o Google ({getattr(e, 'reason', e)})")
        _salvar(novo, tok)
        tok = novo
    return tok["access_token"]


def api(metodo: str, url: str, params: dict | None = None, corpo: dict | None = None):
    """Chamada autenticada. Erros viram ErroFerramenta (texto para o modelo explicar ao usuário)."""
    url = url + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    req = urllib.request.Request(url, json.dumps(corpo).encode() if corpo is not None else None, method=metodo,
                                 headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
    try:
        for tentativa in (1, 2):  # a rede às vezes engasga na 1ª chamada; uma repetição resolve
            try:
                with urllib.request.urlopen(req, timeout=12) as r:
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
            detalhe = json.load(e).get("error", {}).get("message", "")
        except Exception:
            pass
        if e.code in (401, 403) and "insufficient" in detalhe.lower():
            raise ErroFerramenta(f"falta permissão na conta Google; o usuário precisa rodar '{COMANDO}' de novo")
        raise ErroFerramenta(f"o Google recusou o pedido (erro {e.code}: {detalhe[:120]})")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ErroFerramenta(f"sem conexão com o Google ({getattr(e, 'reason', e)})")


# ------------------------------------------------------------------ contatos
PESSOAS = "https://people.googleapis.com/v1"
_aquecido = False


def buscar_contatos(nome: str) -> list[dict]:
    """[{'nome', 'emails', 'telefones'}] dos contatos do Google que batem com o nome."""
    global _aquecido
    campos = {"readMask": "names,emailAddresses,phoneNumbers"}
    if not _aquecido:  # a API de busca pede uma chamada vazia antes, para preparar o cache dela
        api("GET", f"{PESSOAS}/people:searchContacts", {**campos, "query": ""})
        _aquecido = True
    r = api("GET", f"{PESSOAS}/people:searchContacts", {**campos, "query": nome, "pageSize": 10}) or {}
    saida = []
    for item in r.get("results", []):
        p = item.get("person", {})
        nomes = [n.get("displayName", "") for n in p.get("names", [])]
        saida.append({"nome": next((n for n in nomes if n), nome),
                      "emails": [e["value"] for e in p.get("emailAddresses", []) if e.get("value")],
                      "telefones": [t.get("canonicalForm") or t["value"] for t in p.get("phoneNumbers", []) if t.get("value")]})
    return saida


def um_contato(nome: str, campo: str) -> tuple[str, str]:
    """(nome, valor) de quem tem o campo ('emails' ou 'telefones'). Ambíguo ou ausente = erro com as opções."""
    achados = [c for c in buscar_contatos(nome) if c[campo]]
    if not achados:
        tipo = "e-mail" if campo == "emails" else "telefone"
        raise ErroFerramenta(f"não achei '{nome}' com {tipo} nos contatos do Google; peça o {tipo} ao usuário")
    exatos = [c for c in achados if c["nome"].lower() == nome.strip().lower()]
    if len(exatos) == 1:
        achados = exatos
    valores = {v for c in achados for v in c[campo]}
    if len(achados) > 1 or len(valores) > 1:
        opcoes = "; ".join(f"{c['nome']}: {', '.join(c[campo])}" for c in achados[:5])
        raise ErroFerramenta(f"há mais de uma opção para '{nome}' ({opcoes}). Pergunte ao usuário qual é")
    return achados[0]["nome"], achados[0][campo][0]


if __name__ == "__main__":
    autorizar()
