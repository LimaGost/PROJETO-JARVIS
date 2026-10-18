"""Processo da voz clonada (XTTS-v2). Roda no ambiente .venv-voz (PyTorch), separado do ambiente do Jarvis.

Protocolo (stdin/stdout, binário):
  entrada : uma linha JSON por pedido  {"id": n, "texto": "..."}  |  {"cancelar": true}  |  {"sair": true}
  saída   : quadros  [id uint32][tamanho uint32][PCM int16 mono 24 kHz]; tamanho 0 = fim do pedido (id 0 = pronto).
Tudo que as bibliotecas imprimirem vai para stderr, para não corromper o stdout binário.
"""
import json
import os
import queue
import struct
import sys
import threading
import time

T0 = time.time()

SAIDA = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
sys.stdout = sys.stderr
os.environ["COQUI_TOS_AGREED"] = "1"      # licença CPML (uso não comercial) aceita pelo dono do projeto

import numpy as np                      # noqa: E402
import soundfile as sf                  # noqa: E402
import torch                            # noqa: E402
import torchaudio                       # noqa: E402


def _carregar(caminho, *a, **k):
    """torchaudio.load exige torchcodec+ffmpeg (DLLs) no Windows; o soundfile basta."""
    d, sr = sf.read(caminho, dtype="float32", always_2d=True)
    return torch.from_numpy(d.T.copy()), sr


torchaudio.load = _carregar
from TTS.api import TTS                 # noqa: E402


def etapa(nome: str):
    """Tempo desde o início do processo, no voz_clonada.log: mostra onde a partida da voz demora."""
    print(f"[voz clonada] {time.strftime('%H:%M:%S')} {nome}: {time.time() - T0:.1f}s", file=sys.stderr, flush=True)


def latentes(modelo, referencia: str, dispositivo: str):
    """O "molde" da voz sai do áudio de referência. Calcular custa alguns segundos a cada partida, então fica
    salvo ao lado do áudio e só é refeito se o arquivo de referência mudar."""
    info = os.stat(referencia)
    chave = f"{info.st_size}-{int(info.st_mtime)}"
    cache = referencia + ".latentes.pt"
    try:
        dados = torch.load(cache, map_location=dispositivo, weights_only=True)
        if dados.get("chave") == chave:
            return dados["latente"], dados["embedding"]
    except Exception:
        pass   # sem cache ou cache de outra versão: calcula de novo
    latente, embedding = modelo.get_conditioning_latents(audio_path=[referencia])
    try:
        torch.save({"chave": chave, "latente": latente.cpu(), "embedding": embedding.cpu()}, cache)
    except Exception:
        pass
    return latente, embedding


def enviar(id_: int, dados: bytes = b""):
    SAIDA.write(struct.pack("<II", id_, len(dados)) + dados)


def main():
    referencia, velocidade, temperatura, dispositivo = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
    etapa("bibliotecas carregadas")
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(dispositivo)
    modelo = tts.synthesizer.tts_model
    etapa("modelo XTTS na GPU")
    latente, embedding = latentes(modelo, referencia, dispositivo)
    latente, embedding = latente.to(dispositivo), embedding.to(dispositivo)
    etapa("molde da voz")
    cancelar = threading.Event()
    pedidos: queue.Queue = queue.Queue()

    def ler():
        for linha in sys.stdin:
            try:
                msg = json.loads(linha)
            except ValueError:
                continue
            if msg.get("cancelar"):
                cancelar.set()
                while True:                       # descarta o que ainda não começou
                    try:
                        pedidos.get_nowait()
                    except queue.Empty:
                        break
            elif msg.get("sair"):
                break
            else:
                pedidos.put(msg)
        pedidos.put(None)
    threading.Thread(target=ler, daemon=True).start()

    def sintetizar(texto: str, id_: int):
        for trecho in modelo.inference_stream(texto, "pt", latente, embedding, stream_chunk_size=20, speed=velocidade,
                                              temperature=temperatura, enable_text_splitting=True):
            if cancelar.is_set():
                return
            pcm = (trecho.clamp(-1, 1).cpu().numpy() * 32767).astype(np.int16)
            enviar(id_, pcm.tobytes())

    sintetizar("Sistemas prontos, senhor.", 0)    # esquenta os kernels da GPU (a 1ª frase leva ~1 minuto sem isso)
    etapa("aquecimento (pronta)")
    cancelar.clear()
    enviar(0)                                     # id 0, tamanho 0 = pronto
    while True:
        msg = pedidos.get()
        if msg is None:
            return
        cancelar.clear()
        try:
            sintetizar(str(msg["texto"]), int(msg["id"]))
        except Exception as e:                    # uma frase ruim não derruba o processo
            print(f"[voz clonada] erro: {e}", file=sys.stderr)
        enviar(int(msg["id"]))


if __name__ == "__main__":
    main()
