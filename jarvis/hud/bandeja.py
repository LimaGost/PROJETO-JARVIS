"""Ícone do Jarvis na bandeja do Windows: abrir a HUD, silenciar o microfone, parar tudo e sair."""
import os

from jarvis.hud import janela


def _imagem():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), outline=(72, 214, 255, 255), width=5)
    d.ellipse((20, 20, 44, 44), fill=(154, 240, 255, 255))
    return img


def iniciar(url: str, ctl):
    """Retorna o ícone (para poder removê-lo ao encerrar) ou None se não for possível."""
    if os.environ.get("JARVIS_SEM_JANELA"):  # testes automáticos: nada de ícones na sua bandeja
        return None
    try:
        import pystray
    except ImportError:
        return None

    def sair(icone, item):
        ctl.encerrar.set()
        if ctl.mic:
            ctl.mic.encerrar.set()
        icone.stop()

    def alternar_mudo(icone, item):
        ctl.silenciar(not (ctl.mic and ctl.mic.silenciado))

    menu = pystray.Menu(
        pystray.MenuItem("Abrir HUD", lambda i, it: janela.abrir(url), default=True),
        pystray.MenuItem("Microfone desligado", alternar_mudo,
                         checked=lambda item: bool(ctl.mic and ctl.mic.silenciado),
                         visible=ctl.mic is not None),
        pystray.MenuItem("Parar tudo", lambda i, it: ctl.parar()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair do Jarvis", sair),
    )
    try:
        icone = pystray.Icon("jarvis", _imagem(), "Jarvis", menu)
        icone.run_detached()
        return icone
    except Exception:
        return None  # sem bandeja o Jarvis continua funcionando
