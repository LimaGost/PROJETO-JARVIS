"""Prepara o áudio de referência da voz clonada a partir de um vídeo ou áudio (mp4, m4a, mp3, wav...).

    .venv\\Scripts\\python.exe -m jarvis.audio.preparar_voz "VOZ JARVIS UCM]\\Gravando 2026-10-17 131614.mp4"

O que faz, e por quê (o XTTS copia TUDO da referência, inclusive defeito):
  - mono, 24 kHz;
  - corta o silêncio do começo e do fim e encurta pausas longas para 0,35 s: pausa morta na referência vira
    pausa morta na fala dele;
  - normaliza o volume da fala (-20 dBFS RMS, pico em -1 dBFS): referência baixa ou estourada deixa a voz
    abafada ou chiada;
  - guarda a referência antiga como referencia.anterior.wav antes de trocar.
Usa o PyAV (já vem com o faster-whisper), então não precisa do ffmpeg instalado.
"""
import argparse
import shutil
import sys
import wave
from pathlib import Path

import numpy as np

from jarvis.config import RAIZ

TAXA = 24000
JANELA = int(0.02 * TAXA)        # 20 ms
LIMIAR_DB = -45.0                # abaixo disso (em relação ao pico) é silêncio
MAX_PAUSA_S = 0.35
MARGEM_S = 0.08                  # respiro mantido antes e depois da fala
ALVO_RMS_DB = -20.0
PICO_DB = -1.0
DESTINO = RAIZ / "data" / "voices" / "jarvis_ucm" / "referencia.wav"


def ler_audio(caminho: Path) -> np.ndarray:
    """Qualquer formato que o ffmpeg do PyAV entende -> float32 mono 24 kHz."""
    import av
    pedacos = []
    with av.open(str(caminho)) as arquivo:
        if not arquivo.streams.audio:
            raise SystemExit(f"{caminho} não tem áudio")
        conversor = av.AudioResampler(format="flt", layout="mono", rate=TAXA)
        for quadro in arquivo.decode(audio=0):
            for q in conversor.resample(quadro):
                pedacos.append(q.to_ndarray().reshape(-1))
        for q in conversor.resample(None):     # esvazia o que ficou no conversor
            pedacos.append(q.to_ndarray().reshape(-1))
    if not pedacos:
        raise SystemExit(f"{caminho}: não consegui ler o áudio")
    return np.concatenate(pedacos).astype(np.float32)


def _db(x: np.ndarray) -> np.ndarray:
    return 20 * np.log10(np.maximum(x, 1e-9))


def limpar(audio: np.ndarray) -> np.ndarray:
    audio = audio - float(np.mean(audio))                  # tira nível DC
    n = len(audio) // JANELA
    if n == 0:
        raise SystemExit("áudio curto demais")
    blocos = audio[: n * JANELA].reshape(n, JANELA)
    rms = np.sqrt(np.mean(blocos ** 2, axis=1))
    fala = _db(rms) > (_db(np.array([rms.max()]))[0] + LIMIAR_DB)
    if not fala.any():
        raise SystemExit("não achei fala no áudio")
    margem = int(MARGEM_S * TAXA / JANELA)
    primeiro = max(0, int(np.argmax(fala)) - margem)
    ultimo = min(n, n - int(np.argmax(fala[::-1])) + margem)

    max_pausa = int(MAX_PAUSA_S * TAXA / JANELA)
    saida, pausa = [], 0
    for i in range(primeiro, ultimo):
        if fala[i]:
            pausa = 0
        else:
            pausa += 1
            if pausa > max_pausa:
                continue                                    # pausa longa: o excesso sai
        saida.append(blocos[i])
    limpo = np.concatenate(saida)

    trechos_fala = blocos[primeiro:ultimo][fala[primeiro:ultimo]]
    rms_fala = float(np.sqrt(np.mean(trechos_fala ** 2)))
    ganho = 10 ** (ALVO_RMS_DB / 20) / max(rms_fala, 1e-9)
    pico = float(np.max(np.abs(limpo))) * ganho
    teto = 10 ** (PICO_DB / 20)
    if pico > teto:
        ganho *= teto / pico
    return np.clip(limpo * ganho, -1.0, 1.0)


def gravar(audio: np.ndarray, destino: Path):
    destino.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAXA)
        w.writeframes((audio * 32767).astype("<i2").tobytes())


def main(argv=None):
    p = argparse.ArgumentParser(description="Prepara o áudio de referência da voz do Jarvis")
    p.add_argument("origem", type=Path, help="vídeo ou áudio com a voz (mp4, m4a, mp3, wav...)")
    p.add_argument("--destino", type=Path, default=DESTINO)
    args = p.parse_args(argv)
    if not args.origem.exists():
        raise SystemExit(f"não achei {args.origem}")
    bruto = ler_audio(args.origem)
    limpo = limpar(bruto)
    duracao = len(limpo) / TAXA
    if args.destino.exists():
        anterior = args.destino.with_name(args.destino.stem + ".anterior.wav")
        shutil.copy2(args.destino, anterior)
        print(f"Referência antiga guardada em {anterior}")
    gravar(limpo, args.destino)
    print(f"Pronto: {args.destino} ({len(bruto) / TAXA:.1f}s originais -> {duracao:.1f}s de fala limpa)")
    if duracao < 6:
        print("Aviso: menos de 6 s de fala. O ideal para o XTTS é de 10 a 30 s.", file=sys.stderr)
    elif duracao > 30:
        print("Aviso: o XTTS usa só os primeiros 30 s; o resto é ignorado.", file=sys.stderr)
    print("Reinicie o Jarvis: o molde da voz é recalculado sozinho na próxima partida.")


if __name__ == "__main__":
    main()
