"""Abrir, fechar e listar programas.

Abrir usa a lista do menu Iniciar (Get-StartApps), que inclui apps da Microsoft Store
(Spotify, WhatsApp, Calculadora). Fechar envia WM_CLOSE para as janelas, como clicar no X:
o programa pode perguntar se quer salvar. Nunca força o encerramento.
"""
import ctypes
import json
import os
import subprocess
import threading
import winreg
from ctypes import wintypes
from difflib import SequenceMatcher

import psutil

from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, ferramenta
from jarvis.tools.caminhos import sem_acento as _sem_acento

_apps: list[dict] = []
_carregado = threading.Event()
_carregando = threading.Lock()

APELIDOS = {
    "vscode": "visual studio code", "vs code": "visual studio code", "code": "visual studio code",
    "notepad": "bloco de notas", "explorador": "explorador de arquivos", "arquivos": "explorador de arquivos",
    "windows explorer": "explorador de arquivos", "chrome": "google chrome", "edge": "microsoft edge",
    "configuracoes": "configuracoes", "ajustes": "configuracoes", "calculadora": "calculadora",
    "gerenciador de tarefas": "gerenciador de tarefas", "zap": "whatsapp", "whats": "whatsapp",
}


def carregar_em_segundo_plano():
    threading.Thread(target=_carregar, daemon=True).start()


def _carregar():
    with _carregando:  # se já está carregando em segundo plano, espera em vez de repetir
        if _carregado.is_set():
            return
        _ler_menu_iniciar()


def _ler_menu_iniciar():
    try:
        out = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, timeout=20).stdout.decode("utf-8-sig")
        dados = json.loads(out)
        _apps[:] = [{"nome": a["Name"], "id": a["AppID"], "chave": _sem_acento(a["Name"])}
                    for a in (dados if isinstance(dados, list) else [dados])]
    finally:
        _carregado.set()


def _navegador_padrao() -> str:
    try:
        chave = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave) as k:
            progid = winreg.QueryValueEx(k, "ProgId")[0].lower()
    except OSError:
        return "microsoft edge"
    for marca, nome in (("chrome", "google chrome"), ("msedge", "microsoft edge"),
                        ("firefox", "firefox"), ("brave", "brave"), ("opera", "opera")):
        if marca in progid:
            return nome
    return "microsoft edge"


def encontrar_app(nome: str) -> dict | None:
    _carregar()
    alvo = _sem_acento(nome).strip()
    alvo = alvo.removeprefix("o ").removeprefix("a ")
    if alvo in ("navegador", "browser", "internet"):
        alvo = _navegador_padrao()
    alvo = APELIDOS.get(alvo, alvo)
    candidatos = [a for a in _apps if not a["id"].lower().startswith("http")]
    for a in candidatos:
        if a["chave"] == alvo:
            return a
    melhor, nota = None, 0.0
    for a in candidatos:
        n = SequenceMatcher(None, alvo, a["chave"]).ratio()
        if a["chave"].startswith(alvo) or f" {alvo}" in f" {a['chave']}":
            n = max(n, 0.8 + 0.1 * len(alvo) / max(len(a["chave"]), 1))
        if n > nota:
            melhor, nota = a, n
    return melhor if nota >= 0.62 else None


@ferramenta(
    "abrir_programa",
    "Abre um programa ou aplicativo instalado pelo nome (ex.: Spotify, Chrome, Calculadora, Visual Studio Code, WhatsApp, navegador).",
    {"nome": {"type": "string", "description": "Nome do programa"}},
    obrigatorios=("nome",),
)
def abrir_programa(nome: str) -> str:
    app = encontrar_app(nome)
    if app is None:
        parecidos = sorted(_apps, key=lambda a: -SequenceMatcher(None, _sem_acento(nome), a["chave"]).ratio())[:3]
        raise ErroFerramenta(f"não encontrei '{nome}'. Parecidos: {', '.join(a['nome'] for a in parecidos)}")
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['id']}"])
    return f"Abrindo {app['nome']}."


# ---------------------------------------------------------------- janelas
_user32 = ctypes.windll.user32
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WM_CLOSE = 0x0010
CLASSES_DO_SISTEMA = {"Shell_TrayWnd", "Progman", "WorkerW", "Shell_SecondaryTrayWnd"}
PROCESSOS_INTOCAVEIS = {"csrss.exe", "dwm.exe", "winlogon.exe", "lsass.exe", "services.exe", "svchost.exe",
                        "ollama.exe", "ollama app.exe", "llama-server.exe", "textinputhost.exe", "searchhost.exe",
                        "shellexperiencehost.exe", "startmenuexperiencehost.exe", "lockapp.exe"}


def _janelas() -> list[dict]:
    meu_pid = os.getpid()
    pais = {p.pid for p in psutil.Process(meu_pid).parents()}
    resultado = []

    def cada(hwnd, _):
        if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, 4):  # 4 = GW_OWNER (diálogos)
            return True
        tam = _user32.GetWindowTextLengthW(hwnd)
        if tam == 0:
            return True
        titulo = ctypes.create_unicode_buffer(tam + 1)
        _user32.GetWindowTextW(hwnd, titulo, tam + 1)
        classe = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, classe, 256)
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if classe.value in CLASSES_DO_SISTEMA or pid.value == meu_pid or pid.value in pais:
            return True
        try:
            processo = psutil.Process(pid.value).name()
        except psutil.Error:
            return True
        if processo.lower() in PROCESSOS_INTOCAVEIS:
            return True
        resultado.append({"hwnd": hwnd, "titulo": titulo.value, "processo": processo, "classe": classe.value})
        return True

    _user32.EnumWindows(_EnumProc(cada), 0)
    return resultado


def _janelas_do_app(nome: str) -> list[dict]:
    alvo = _sem_acento(nome).strip()
    alvo = APELIDOS.get(alvo, alvo)
    if alvo in ("navegador", "browser"):
        alvo = _navegador_padrao()
    app = encontrar_app(nome)
    termos = {alvo} | ({app["chave"]} if app else set())
    exe_alvo = {"google chrome": "chrome", "microsoft edge": "msedge", "visual studio code": "code",
                "explorador de arquivos": "explorer", "bloco de notas": "notepad"}
    termos |= {exe_alvo[t] for t in list(termos) if t in exe_alvo}
    achadas = []
    for j in _janelas():
        proc = _sem_acento(j["processo"].removesuffix(".exe"))
        titulo = _sem_acento(j["titulo"])
        if j["processo"].lower() == "explorer.exe" and j["classe"] != "CabinetWClass":
            continue  # do explorer só as janelas de pasta, nunca a barra de tarefas
        for t in termos:
            if proc == t.replace(" ", "") or proc == t or titulo == t or titulo.endswith(f"- {t}") \
                    or titulo.startswith(f"{t} -") or (len(t) > 3 and f" {t}" in f" {titulo} " and proc != "explorer"):
                achadas.append(j)
                break
    return achadas


def _avaliar_fechar(args: dict) -> Avaliacao:
    janelas = _janelas_do_app(args["nome"])
    if not janelas:
        raise ErroFerramenta(f"não há nenhuma janela aberta de '{args['nome']}'")
    return Avaliacao(Nivel.REGISTRADO, f"fechar {len(janelas)} janela(s) de {args['nome']}")


@ferramenta(
    "fechar_programa",
    "Fecha as janelas de um programa aberto, como clicar no X (o programa pode perguntar se quer salvar).",
    {"nome": {"type": "string", "description": "Nome do programa"}},
    obrigatorios=("nome",),
    avaliar=_avaliar_fechar,
)
def fechar_programa(nome: str) -> str:
    janelas = _janelas_do_app(nome)
    for j in janelas:
        _user32.PostMessageW(j["hwnd"], WM_CLOSE, 0, 0)
    return "Fechei: " + "; ".join(f"{j['processo'].removesuffix('.exe')} ({j['titulo'][:50]})" for j in janelas)


@ferramenta("listar_janelas", "Lista os programas e janelas abertos agora.")
def listar_janelas() -> str:
    janelas = _janelas()
    if not janelas:
        return "Nenhuma janela aberta."
    return "\n".join(f"{j['processo'].removesuffix('.exe')}: {j['titulo'][:70]}" for j in janelas)
