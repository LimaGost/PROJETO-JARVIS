import ctypes
import time

from pycaw.pycaw import AudioUtilities

from jarvis.tools.base import ErroFerramenta, ferramenta


def _endpoint():
    return AudioUtilities.GetSpeakers().EndpointVolume


@ferramenta(
    "volume",
    "Consulta ou altera o volume do som do computador.",
    {"acao": {"type": "string", "enum": ["consultar", "definir", "aumentar", "diminuir", "mudo", "tirar_mudo"]},
     "valor": {"type": "integer", "description": "Porcentagem (para definir) ou quanto aumentar/diminuir. Padrão: 10"}},
    obrigatorios=("acao",),
)
def volume(acao: str, valor: int | None = None) -> str:
    ev = _endpoint()
    atual = round(ev.GetMasterVolumeLevelScalar() * 100)
    if acao == "consultar":
        return f"Volume em {atual}%" + (" (mudo)" if ev.GetMute() else "")
    if acao == "mudo":
        ev.SetMute(1, None)
        return "Som no mudo."
    if acao == "tirar_mudo":
        ev.SetMute(0, None)
        return f"Som ligado, volume em {atual}%."
    if acao == "definir":
        if valor is None:
            raise ErroFerramenta("faltou o valor do volume")
        novo = valor
    else:
        passo = valor if valor is not None else 10
        novo = atual + passo if acao == "aumentar" else atual - passo
    novo = max(0, min(100, novo))
    ev.SetMasterVolumeLevelScalar(novo / 100, None)
    if novo > 0:
        ev.SetMute(0, None)
    return f"Volume em {novo}%."


_TECLAS = {"pausar_ou_continuar": 0xB3, "pular_para_proxima": 0xB0, "voltar_para_anterior": 0xB1,
           "encerrar_reproducao": 0xB2}


def _titulo_spotify() -> str | None:
    """Título da janela do Spotify: 'Artista - Música' se estiver tocando, 'Spotify...' se pausado.
    None se o app não estiver aberto."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    achados: list[str] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _janela(hwnd, _):
        tamanho = user32.GetWindowTextLengthW(hwnd)
        classe = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, classe, 64)
        if not tamanho or classe.value != "Chrome_WidgetWin_1":  # janela principal do Spotify (Electron)
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED
        if not proc:
            return True
        try:
            buf, n = ctypes.create_unicode_buffer(520), ctypes.c_ulong(520)
            if kernel32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(n)) and \
                    buf.value.lower().endswith("\\spotify.exe"):
                texto = ctypes.create_unicode_buffer(tamanho + 1)
                user32.GetWindowTextW(hwnd, texto, tamanho + 1)
                achados.append(texto.value)
        finally:
            kernel32.CloseHandle(proc)
        return True

    user32.EnumWindows(_janela, 0)
    return max(achados, key=len) if achados else None


def _tocando_agora() -> str:
    titulo = _titulo_spotify()
    if titulo is None:
        return "Não consigo ver o que está tocando (só leio o Spotify, e ele não está aberto)."
    if titulo.lower().startswith("spotify"):
        return "Spotify aberto, mas nada tocando agora (pausado)."
    return f"Tocando agora no Spotify: {titulo}."


@ferramenta(
    "controle_midia",
    "Controla a reprodução (play/pausa, próxima, anterior, parar) e diz o que está tocando no Spotify. "
    "NÃO busca nem escolhe músicas: para uma música específica use tocar_musica.",
    {"acao": {"type": "string", "enum": [*_TECLAS, "o_que_esta_tocando"],
              "description": "pausar_ou_continuar: pausar ou voltar a tocar (pausa a música, continua, dá play); "
                             "pular_para_proxima: próxima faixa (pula essa música); "
                             "voltar_para_anterior: faixa anterior; "
                             "encerrar_reproducao: só quando pedirem para parar de vez; "
                             "o_que_esta_tocando: informa a música atual"}},
    obrigatorios=("acao",),
)
def controle_midia(acao: str) -> str:
    if acao == "o_que_esta_tocando":
        return _tocando_agora()
    tecla = _TECLAS[acao]
    ctypes.windll.user32.keybd_event(tecla, 0, 0, 0)
    ctypes.windll.user32.keybd_event(tecla, 0, 2, 0)  # 2 = KEYEVENTF_KEYUP
    time.sleep(0.8)  # dá tempo de o Spotify atualizar o título da janela
    feito = {"pausar_ou_continuar": "Tecla de pausar/tocar enviada.", "pular_para_proxima": "Tecla de próxima faixa enviada.",
             "voltar_para_anterior": "Tecla de faixa anterior enviada.",
             "encerrar_reproducao": "Tecla de parar enviada."}[acao]
    return f"{feito} {_tocando_agora()}"
