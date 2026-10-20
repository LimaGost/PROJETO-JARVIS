"""Login automático das CLIs de IA (Claude Code, Codex/ChatGPT, Gemini).

Ninguém precisa abrir terminal: quando você escolhe um cérebro (ou um agente) que está sem login, o Jarvis
  1. abre o login oficial da própria CLI numa janela (ela abre o navegador para você entrar na sua conta),
  2. confere sozinho a cada poucos segundos se o login terminou,
  3. avisa quando deu certo (e quem pediu continua: o seletor troca de cérebro, a Central atualiza o cartão).
As credenciais ficam onde cada CLI guarda (pasta do usuário); o Jarvis nunca lê nem copia token nenhum.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

CRIAR_CONSOLE = 0x00000010            # CREATE_NEW_CONSOLE: a janela do login é dela, não do Jarvis
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CACHE_S = 20
ESPERA_MAX_S = 300
INTERVALO_S = 3

CLIS = {
    "claude": {"nome": "Claude", "programa": "claude", "login": ["auth", "login"]},
    "codex": {"nome": "ChatGPT (Codex)", "programa": "codex", "login": ["login"]},
    # o Gemini não tem "login" separado: aberto sem pedido, ele pergunta como entrar (escolha "Login with Google")
    "gemini": {"nome": "Gemini", "programa": "gemini", "login": []},
}

_cache: dict[str, tuple[float, tuple[bool | None, str]]] = {}
_esperando: set[str] = set()
_lock = threading.Lock()


def _comando(cli: str, configurado=None) -> list[str] | None:
    from jarvis.orquestra import gateway
    return gateway.resolver_executavel(CLIS[cli]["programa"], configurado)


def _ambiente() -> dict:
    from jarvis.orquestra.seguranca import ambiente_limpo
    return ambiente_limpo()


def instalado(cli: str, configurado=None) -> bool:
    return _comando(cli, configurado) is not None


def verificar(cli: str, configurado=None, usar_cache: bool = True) -> tuple[bool | None, str]:
    """(True logado | False sem login | None não instalado/sem resposta, detalhe em português)."""
    agora = time.time()
    if usar_cache and cli in _cache and agora - _cache[cli][0] < CACHE_S:
        return _cache[cli][1]
    cmd = _comando(cli, configurado)
    if cmd is None:
        r: tuple[bool | None, str] = (None, f"o {CLIS[cli]['nome']} não está instalado")
    elif cli == "gemini":
        casa = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"
        r = (True, "conta Google") if (casa / "oauth_creds.json").exists() else (False, "sem login")
    else:
        try:
            p = subprocess.run([*cmd, *(["auth", "status"] if cli == "claude" else ["login", "status"])],
                               capture_output=True, text=True, timeout=30, env=_ambiente(), creationflags=SEM_JANELA)
            texto = (p.stdout + " " + p.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            texto, p = f"erro {type(e).__name__}", None
        if cli == "claude":
            try:
                info = json.loads(p.stdout) if p else {}
                r = (True, "assinatura") if info.get("loggedIn") else (False, "sem login")
            except ValueError:
                r = (None, "não consegui conferir o login")
        else:
            baixo = texto.lower()
            r = (False, "sem login") if "not logged in" in baixo else \
                (True, "conta do ChatGPT" if "chatgpt" in baixo else "chave de API") if "logged in" in baixo else \
                (None, "não consegui conferir o login")
    _cache[cli] = (agora, r)
    return r


def esquecer_cache(cli: str | None = None):
    if cli:
        _cache.pop(cli, None)
    else:
        _cache.clear()


def esperando(cli: str) -> bool:
    return cli in _esperando


def abrir_login(cli: str, configurado=None) -> str:
    """Abre o login oficial numa janela própria. Devolve a frase para mostrar/falar."""
    cmd = _comando(cli, configurado)
    if cmd is None:
        raise RuntimeError(f"o {CLIS[cli]['nome']} não está instalado")
    argv = [*cmd, *CLIS[cli]["login"]]
    if sys.platform == "win32":
        subprocess.Popen(argv, creationflags=CRIAR_CONSOLE, env=_ambiente())
    else:                                   # fora do Windows não há "nova janela": tenta um terminal gráfico
        for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["xterm", "-e"]):
            try:
                subprocess.Popen([*term, *argv], env=_ambiente())
                break
            except OSError:
                continue
        else:
            return f"Rode no terminal: {' '.join(argv)}"
    return f"Abri o login do {CLIS[cli]['nome']}. Entre com a sua conta no navegador; eu confiro sozinho."


def login_automatico(cli: str, ao_terminar: Callable[[bool, str], None] | None = None, configurado=None,
                     limite_s: float = ESPERA_MAX_S) -> str:
    """Abre o login e fica conferindo em segundo plano. `ao_terminar(ok, detalhe)` é chamado uma vez."""
    with _lock:
        if cli in _esperando:
            return f"Já estou esperando o login do {CLIS[cli]['nome']}."
        _esperando.add(cli)
    try:
        frase = abrir_login(cli, configurado)
    except Exception:
        _esperando.discard(cli)
        raise

    def vigiar():
        fim = time.time() + limite_s
        ok, detalhe = False, "o login não terminou a tempo"
        try:
            while time.time() < fim:
                time.sleep(INTERVALO_S)
                estado, det = verificar(cli, configurado, usar_cache=False)
                if estado:
                    ok, detalhe = True, det
                    break
        finally:
            _esperando.discard(cli)
            if ao_terminar is not None:
                try:
                    ao_terminar(ok, detalhe)
                except Exception:
                    pass

    threading.Thread(target=vigiar, daemon=True, name=f"login-{cli}").start()
    return frase
