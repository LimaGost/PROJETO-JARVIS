"""Iniciar o Jarvis junto com o Windows: um atalho na pasta Inicializar do usuário (sem admin, fácil de desfazer).

  python main.py --instalar-inicio     cria o atalho (sobe em segundo plano, só o ícone da bandeja)
  python main.py --remover-inicio      remove
"""
import base64
import os
import shutil
import subprocess
from pathlib import Path

from jarvis.config import RAIZ

NOME = "Jarvis.lnk"


def pasta_inicio() -> Path:
    """JARVIS_PASTA_INICIO permite testar sem tocar na pasta de verdade."""
    if os.environ.get("JARVIS_PASTA_INICIO"):
        return Path(os.environ["JARVIS_PASTA_INICIO"])
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"


def atalho() -> Path:
    return pasta_inicio() / NOME


def instalado() -> bool:
    return atalho().exists()


def _powershell(script: str):
    codificado = base64.b64encode(script.encode("utf-16-le")).decode()
    # pwsh (PowerShell 7) nem sempre está instalado; o powershell.exe vem com todo Windows
    exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", codificado],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "o PowerShell falhou")


def instalar() -> Path:
    pythonw = RAIZ / ".venv" / "Scripts" / "pythonw.exe"  # sem janela de console
    if not pythonw.exists():
        raise RuntimeError(f"não achei {pythonw}")
    destino = atalho()
    destino.parent.mkdir(parents=True, exist_ok=True)
    esc = lambda p: str(p).replace("'", "''")
    _powershell(
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; $s.Arguments = '\"%s\" --silencioso'; $s.WorkingDirectory = '%s'; "
        "$s.WindowStyle = 7; $s.Description = 'Jarvis, assistente local'; $s.Save()"
        % (esc(destino), esc(pythonw), esc(RAIZ / "main.py"), esc(RAIZ)))
    return destino


def remover() -> bool:
    if instalado():
        atalho().unlink()
        return True
    return False
