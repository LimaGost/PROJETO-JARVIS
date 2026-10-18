"""Abre a HUD como janela de aplicativo (sem barra de endereço), no Edge que já vem no Windows."""
import os
import subprocess
import webbrowser
from pathlib import Path

CAMINHOS_EDGE = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
]


def abrir(url: str) -> None:
    """JARVIS_SEM_JANELA=1 desliga (testes automáticos não devem abrir janelas na sua tela)."""
    if os.environ.get("JARVIS_SEM_JANELA"):
        return
    for edge in CAMINHOS_EDGE:
        if edge.exists():
            subprocess.Popen([str(edge), f"--app={url}", "--window-size=1500,920"],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            return
    webbrowser.open(url)
