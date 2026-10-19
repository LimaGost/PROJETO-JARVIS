"""Cofre de credenciais do Connect (chaves de API, tokens, segredos de webhook).

No Windows usa a DPAPI (CryptProtectData): o arquivo só abre com o SEU usuário do Windows, neste PC. Copiar
data/connect/cofre.json para outro lugar não revela nada. Fora do Windows (testes), guarda sem criptografia e avisa.

Regras: o cofre nunca vai para a HUD (ela só vê "configurado / não configurado" e os 4 últimos caracteres), nem para
o config.yaml, nem para o log.
"""
import base64
import ctypes
import json
import os
import secrets
import sys
import threading
from pathlib import Path

from jarvis.config import RAIZ

ARQUIVO = RAIZ / "data" / "connect" / "cofre.json"
_ENTROPIA = b"jarvis-connect-v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(dados: bytes, proteger: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(dados, len(dados))
    ent = ctypes.create_string_buffer(_ENTROPIA, len(_ENTROPIA))
    entrada = _Blob(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    entropia = _Blob(len(_ENTROPIA), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
    saida = _Blob()
    sem_janela = 0x1                                    # CRYPTPROTECT_UI_FORBIDDEN
    if proteger:
        ok = crypt32.CryptProtectData(ctypes.byref(entrada), ctypes.c_wchar_p("jarvis"), ctypes.byref(entropia),
                                      None, None, sem_janela, ctypes.byref(saida))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(entrada), None, ctypes.byref(entropia),
                                        None, None, sem_janela, ctypes.byref(saida))
    if not ok:
        raise OSError("a DPAPI do Windows recusou a operação")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


class Protecao:
    """Como os bytes ficam no disco. Trocável nos testes."""
    nome = "nenhuma"

    def fechar(self, dados: bytes) -> bytes:
        return dados

    def abrir(self, dados: bytes) -> bytes:
        return dados


class ProtecaoWindows(Protecao):
    nome = "dpapi"

    def fechar(self, dados: bytes) -> bytes:
        return _dpapi(dados, True)

    def abrir(self, dados: bytes) -> bytes:
        return _dpapi(dados, False)


def protecao_padrao() -> Protecao:
    return ProtecaoWindows() if sys.platform == "win32" else Protecao()


class Cofre:
    def __init__(self, arquivo: Path | str | None = None, protecao: Protecao | None = None):
        self.arquivo = Path(arquivo or ARQUIVO)
        self.protecao = protecao or protecao_padrao()
        self._lock = threading.Lock()

    def _ler_tudo(self) -> dict:
        try:
            bruto = json.loads(self.arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        saida = {}
        for conector, caixa in (bruto.get("itens") or {}).items():
            try:
                saida[conector] = json.loads(self.protecao.abrir(base64.b64decode(caixa)).decode("utf-8"))
            except Exception:
                continue          # item de outro usuário/PC ou corrompido: ignora, não quebra o resto
        return saida

    def _gravar_tudo(self, itens: dict):
        caixas = {c: base64.b64encode(self.protecao.fechar(json.dumps(v).encode("utf-8"))).decode()
                  for c, v in itens.items()}
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        temp = self.arquivo.with_suffix(".tmp")
        temp.write_text(json.dumps({"protecao": self.protecao.nome, "itens": caixas}), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.arquivo)

    def ler(self, conector: str) -> dict:
        with self._lock:
            return dict(self._ler_tudo().get(conector) or {})

    def guardar(self, conector: str, valores: dict):
        """Mescla: guardar {'apikey': x} não apaga o 'segredo_webhook' que já estava lá."""
        with self._lock:
            itens = self._ler_tudo()
            atual = itens.get(conector) or {}
            atual.update({k: v for k, v in valores.items() if v not in (None, "")})
            itens[conector] = atual
            self._gravar_tudo(itens)

    def apagar(self, conector: str) -> bool:
        with self._lock:
            itens = self._ler_tudo()
            existia = itens.pop(conector, None) is not None
            self._gravar_tudo(itens)
            return existia

    def segredo(self, conector: str, campo: str = "segredo_webhook") -> str:
        """Segredo aleatório estável (cria na 1ª vez): usado para validar webhooks."""
        valor = self.ler(conector).get(campo)
        if not valor:
            valor = secrets.token_urlsafe(32)
            self.guardar(conector, {campo: valor})
        return valor

    @staticmethod
    def mascarar(valor: str) -> str:
        return ("•" * 6 + valor[-4:]) if valor and len(valor) > 8 else ("•" * 6 if valor else "")


_cofre: Cofre | None = None


def cofre() -> Cofre:
    global _cofre
    if _cofre is None:
        _cofre = Cofre()
    return _cofre


def usar(c: Cofre | None):
    """Para testes: troca o cofre global."""
    global _cofre
    _cofre = c
