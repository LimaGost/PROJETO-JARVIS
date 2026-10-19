"""Processo da voz clonada com o Qwen3-TTS (faster-qwen3-tts, CUDA graphs). Roda no ambiente .venv-qwen3.

Mesmo protocolo do clone_worker.py (XTTS), então o resto do Jarvis não percebe a troca:
  entrada : uma linha JSON por pedido  {"id": n, "texto": "..."}  |  {"cancelar": true}  |  {"sair": true}
  saída   : quadros  [id uint32][tamanho uint32][PCM int16 mono 24 kHz]; tamanho 0 = fim do pedido (id 0 = pronto).

Por que Qwen3-TTS: num teste independente (RTX 4050, 1.620 falas) foi o mais fiel à voz de referência (0,85 contra
0,64 do XTTS), com 1,2% de palavras erradas contra 10,6% e nenhuma palavra inventada. Custa ~5 GB de VRAM e só fala
em tempo real com o faster-qwen3-tts. A clonagem usa a TRANSCRIÇÃO do áudio de referência (modo ICL): sem ela
(xvec_only) funciona, mas fica menos parecida.
"""
import json
import os
import queue
import struct
import sys
import threading
import time

SAIDA = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
sys.stdout = sys.stderr      # o que as bibliotecas imprimirem não pode corromper o stdout binário
T0 = time.time()
TAXA = 24000                 # o Jarvis toca a 24 kHz (TAXA_CLONE): se o modelo devolver outra taxa, é convertida

import numpy as np            # noqa: E402
import torch                  # noqa: E402
from faster_qwen3_tts import FasterQwen3TTS   # noqa: E402


def etapa(nome: str):
    print(f"[voz qwen3] {time.strftime('%H:%M:%S')} {nome}: {time.time() - T0:.1f}s", file=sys.stderr, flush=True)


def enviar(id_: int, dados: bytes = b""):
    SAIDA.write(struct.pack("<II", id_, len(dados)) + dados)


def para_pcm(trecho, taxa: int) -> bytes:
    """Tensor/array em float (-1..1), na taxa do modelo -> int16 a 24 kHz."""
    if hasattr(trecho, "detach"):
        trecho = trecho.detach().float().cpu().numpy()
    x = np.asarray(trecho, dtype=np.float32).reshape(-1)
    if taxa != TAXA and len(x):
        n = int(round(len(x) * TAXA / taxa))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def main():
    referencia, dispositivo = sys.argv[1], sys.argv[4]
    extra = json.loads(sys.argv[5]) if len(sys.argv) > 5 else {}
    modelo_id = extra.get("modelo", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
    texto_ref = (extra.get("texto_referencia") or "").strip()
    xvec = bool(extra.get("xvec_only")) or not texto_ref
    pedaco = int(extra.get("pedaco", 8))           # 8 = ~0,67 s de áudio por pedaço (o exemplo oficial)
    idioma = extra.get("idioma", "Portuguese")
    if dispositivo != "cuda" or not torch.cuda.is_available():
        etapa("aviso: sem CUDA; o Qwen3-TTS fica lento demais para conversar")
    etapa(f"bibliotecas carregadas ({modelo_id}, {'só timbre' if xvec else 'com transcrição'})")

    modelo = FasterQwen3TTS.from_pretrained(modelo_id)
    etapa("modelo na GPU")
    base = {"language": idioma, "ref_audio": referencia, "ref_text": texto_ref, "xvec_only": xvec}
    try:   # extrai a voz da referência UMA vez; sem isso cada frase reprocessa o áudio de referência
        prompt = modelo.model.create_voice_clone_prompt(ref_audio=referencia, ref_text=texto_ref or None,
                                                        x_vector_only_mode=xvec)
        base["voice_clone_prompt"] = prompt
        etapa("molde da voz")
    except Exception as e:
        etapa(f"aviso: sem molde reaproveitável ({type(e).__name__}: {e}); a referência vai em cada frase")
    if hasattr(modelo, "warmup"):
        try:
            modelo.warmup(prefill_len=100)
        except Exception as e:
            etapa(f"aviso: warmup falhou ({type(e).__name__})")

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
                while True:
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
        for trecho, taxa, _ in modelo.generate_voice_clone_streaming(text=texto, chunk_size=pedaco, **base):
            if cancelar.is_set():
                return
            enviar(id_, para_pcm(trecho, int(taxa)))

    sintetizar("Sistemas prontos, senhor.", 0)      # captura os CUDA graphs: a 1ª frase de verdade já sai rápida
    etapa("aquecimento (pronta)")
    cancelar.clear()
    enviar(0)
    while True:
        msg = pedidos.get()
        if msg is None:
            return
        cancelar.clear()
        try:
            sintetizar(str(msg["texto"]), int(msg["id"]))
        except Exception as e:
            print(f"[voz qwen3] erro: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        enviar(int(msg["id"]))


if __name__ == "__main__":
    main()
