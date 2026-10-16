"""Resolução de caminhos ('área de trabalho/relatórios') e áreas protegidas do sistema.

Nesta máquina a Área de Trabalho e os Documentos ficam no OneDrive, por isso as pastas
conhecidas vêm da API do Windows (SHGetKnownFolderPath) e não de ~/Desktop.
"""
import ctypes
import os
import re
import unicodedata
import uuid
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path

from jarvis.config import RAIZ

_FOLDERIDS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documentos": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "imagens": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "musicas": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}

APELIDOS = {
    "area de trabalho": "desktop", "desktop": "desktop", "mesa": "desktop",
    "documentos": "documentos", "meus documentos": "documentos", "documents": "documentos",
    "downloads": "downloads", "download": "downloads", "baixados": "downloads",
    "imagens": "imagens", "fotos": "imagens", "pictures": "imagens",
    "musicas": "musicas", "music": "musicas",
    "videos": "videos",
}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


@lru_cache(maxsize=None)
def pasta_conhecida(nome: str) -> Path:
    u = uuid.UUID(_FOLDERIDS[nome])
    guid = _GUID.from_buffer_copy(u.bytes_le)
    ptr = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ptr)) != 0:
        return Path.home() / nome
    caminho = Path(ptr.value)
    ctypes.windll.ole32.CoTaskMemFree(ptr)
    return caminho


def sem_acento(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def resolver(texto: str) -> Path:
    """'área de trabalho/relatórios' -> C:/Users/.../OneDrive/Área de Trabalho/relatórios.
    Caminhos relativos partem da pasta do usuário."""
    texto = os.path.expandvars(texto.strip().strip('"').strip("'"))
    partes = re.split(r"[\\/]", texto, maxsplit=1)
    chave = sem_acento(partes[0]).strip()
    if chave in APELIDOS:
        base = pasta_conhecida(APELIDOS[chave])
        return (base / partes[1]).resolve() if len(partes) > 1 and partes[1] else base
    p = Path(texto).expanduser()
    if not p.is_absolute():
        p = Path.home() / p
    return p.resolve()


def _dentro(p: Path, base: Path) -> bool:
    try:
        p.relative_to(base)
        return True
    except ValueError:
        return False


@lru_cache(maxsize=1)
def _areas_protegidas() -> tuple[Path, ...]:
    env = os.environ
    areas = [
        env.get("SystemRoot", r"C:\Windows"),
        env.get("ProgramFiles", r"C:\Program Files"),
        env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        env.get("ProgramData", r"C:\ProgramData"),
        env.get("APPDATA", ""),          # inclui a pasta Inicializar (persistência)
        env.get("LOCALAPPDATA", ""),
        str(RAIZ),                       # o próprio Jarvis não se altera sozinho
    ]
    return tuple(Path(a).resolve() for a in areas if a)


def protegido(p: Path) -> bool:
    """Escrever aqui exige confirmação 🔴."""
    p = p.resolve()
    return any(_dentro(p, a) for a in _areas_protegidas())


def intocavel(p: Path) -> bool:
    """Nunca apagar/mover, nem com confirmação: raízes de disco, a pasta do usuário e as pastas conhecidas em si."""
    p = p.resolve()
    if p.parent == p:  # C:\, E:\
        return True
    fixas = [Path.home().resolve()] + [pasta_conhecida(n).resolve() for n in _FOLDERIDS] + list(_areas_protegidas())
    return p in fixas


def caminho_literal_seguro(valor: str) -> bool:
    """Para comandos PowerShell 🟡: o argumento precisa ser um texto fixo, fora de áreas protegidas,
    e não pode apontar para registro/variáveis (HKCU:, Env:, Registry::)."""
    v = valor.strip()
    if "$" in v or "`" in v or "(" in v or "@" in v:
        return False  # variável/subexpressão: só se sabe o valor ao executar
    v = v.strip("'\"")
    if re.match(r"^[A-Za-z]{2,}:", v) or "::" in v:
        return False  # HKCU:, HKLM:, Env:, Cert:, Registry::
    try:
        return not protegido(resolver(v))
    except (OSError, ValueError):
        return False
