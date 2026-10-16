# JARVIS (Fase 1: loop de voz)

Assistente de voz 100% local: **"Hey Jarvis"** → ouve → pensa → responde falando.

```
microfone → openWakeWord → webrtcvad → faster-whisper (GPU) → Qwen3 8B (Ollama) → Piper TTS → alto-falante
```

## Como usar
Dois cliques em `iniciar.bat` (ou `.venv\Scripts\python.exe main.py`). O Ollama precisa estar aberto.

- Diga **"Hey Jarvis"**, espere o "Pois não?" e fale.
- Depois da resposta há 6 s para continuar a conversa sem repetir o chamado.
- **"Tchau Jarvis"**, **"Desligar Jarvis"** ou **"Pode dormir"** encerram.
- **"Esqueça a conversa"** zera a memória da conversa atual.
- Ctrl+C também encerra.

## Ajustes (`config.yaml`)
| Sintoma | Ajuste |
|---|---|
| Não reage ao "Hey Jarvis" | `wakeword.limiar` para 0.3 |
| Corta sua fala no meio / não te ouve | `vad.agressividade` para 2, ou aumente o ganho do microfone no Windows |
| Demora a perceber que você terminou | `vad.silencio_ms` para 700 |
| Erra nomes de apps | acrescente em `stt.vocabulario` |
| Quer mais precisão e aceita +450 MB de VRAM | `stt.modelo: large-v3-turbo`, `stt.tipo: int8_float16` |

## Armadilhas já resolvidas (não desfazer)
- **`llm.host` precisa ser `127.0.0.1`**, nunca `localhost`: no Windows isso custa +2 s por pergunta (tenta IPv6 primeiro).
- **Caminho com acento** (`PROGRAMAÇÃO`) quebra o espeak do Piper; `tts.py` copia os dados para `C:\ProgramData\jarvis_espeak`.
- **O Ollama deve ficar com o modelo fixo** (`keep_alive: -1`) e o prompt de sistema aquecido (`Cerebro.aquecer`), senão a 1ª pergunta paga ~2,4 s.
- Whisper e Qwen juntos usam ~7,3 GB dos 8 GB da GPU; feche apps pesados de GPU antes de usar.

## Medidas (RTX 3050 8 GB, testadas com áudio sintético)
Transcrição ~0,4 s · 1ª frase do LLM ~0,3 s · LLM 37 tokens/s · wake word 6/6 sem falso positivo em 60 s de ruído.
Latência estimada do fim da sua fala até ele começar a falar: ~1,9 s (0,9 s é o silêncio do VAD).

## Próximas fases
2. Ferramentas (PowerShell, arquivos, apps, navegador) com permissões 🟢 livre / 🟡 registrado / 🔴 confirma
3. Memória de longo prazo + reflexão noturna
4. Interface HUD web
5. Visão de tela + proatividade
