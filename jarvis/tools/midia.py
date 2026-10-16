import ctypes

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


@ferramenta(
    "controle_midia",
    "Controla a música ou vídeo tocando (Spotify, YouTube...).",
    {"acao": {"type": "string", "enum": list(_TECLAS),
              "description": "pausar_ou_continuar: pausar ou voltar a tocar (pausa a música, continua, dá play); "
                             "pular_para_proxima: próxima faixa (pula essa música); "
                             "voltar_para_anterior: faixa anterior; "
                             "encerrar_reproducao: só quando pedirem para parar de vez"}},
    obrigatorios=("acao",),
)
def controle_midia(acao: str) -> str:
    tecla = _TECLAS[acao]
    ctypes.windll.user32.keybd_event(tecla, 0, 0, 0)
    ctypes.windll.user32.keybd_event(tecla, 0, 2, 0)  # 2 = KEYEVENTF_KEYUP
    return {"pausar_ou_continuar": "Alternei pausar/tocar.", "pular_para_proxima": "Próxima faixa.",
            "voltar_para_anterior": "Faixa anterior.", "encerrar_reproducao": "Reprodução encerrada."}[acao]
