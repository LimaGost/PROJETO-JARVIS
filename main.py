import sys
import time
import traceback

from jarvis.audio.mic import Microfone
from jarvis.audio.stt import Ouvido
from jarvis.audio.tts import Voz
from jarvis.brain.comandos import identificar
from jarvis.brain.llm import Cerebro
from jarvis.config import carregar

def conversar(cfg, mic, ouvido, cerebro, voz) -> bool:
    """Uma sessão: do 'Hey Jarvis' até o silêncio. Retorna False se o usuário mandou desligar."""
    tratamento = cfg["tratamento"]
    print("\n[aguardando 'Hey Jarvis']")
    mic.esperar_wakeword()
    voz.falar("Pois não?")
    voz.esperar()

    espera = cfg["vad"]["espera_inicio_s"]
    while True:  # janela de conversa: continua sem precisar do wake word
        audio = mic.gravar_fala(espera)
        if audio is None:
            return True

        t0 = time.time()
        texto = ouvido.transcrever(audio)
        t1 = time.time()
        if len(texto) < 2:
            return True
        print(f"Você: {texto}  ({t1 - t0:.2f}s de transcrição)")

        comando = identificar(texto)
        if comando == "desligar":
            voz.falar(f"Até logo, {tratamento}.")
            voz.esperar()
            return False
        if comando == "esquecer":
            cerebro.esquecer()
            voz.falar("Conversa reiniciada.")
            voz.esperar()
        else:
            primeira = True
            print("Jarvis: ", end="", flush=True)
            for frase in cerebro.responder(texto):
                if primeira:
                    print(f"[1ª frase em {time.time() - t1:.2f}s] ", end="")
                    primeira = False
                print(frase, end=" ", flush=True)
                voz.falar(frase)
            print()
            voz.esperar()

        espera = cfg["conversa"]["janela_followup_s"]


def main():
    cfg = carregar()
    tratamento = cfg["tratamento"]

    print("Carregando módulos...")
    t = time.time()
    voz = Voz(cfg)
    ouvido = Ouvido(cfg)
    cerebro = Cerebro(cfg)
    mic = Microfone(cfg)
    try:
        cerebro.aquecer()
    except Exception as e:
        print(f"\nERRO: não consegui falar com o Ollama em {cfg['llm']['host']} ({type(e).__name__}).")
        print(f"Confirme que o Ollama está aberto e que o modelo existe: ollama pull {cfg['llm']['modelo']}")
        sys.exit(1)
    ouvido.aquecer()
    print(f"Pronto em {time.time() - t:.1f}s. Diga 'Hey Jarvis'. Ctrl+C para sair.")

    mic.iniciar()
    voz.falar(f"Sistemas online. Às suas ordens, {tratamento}.")
    voz.esperar()

    falhas = 0
    try:
        while True:
            try:
                if not conversar(cfg, mic, ouvido, cerebro, voz):
                    break
                falhas = 0
            except KeyboardInterrupt:
                raise
            except Exception:
                # um erro em qualquer etapa não pode derrubar o assistente
                falhas += 1
                traceback.print_exc()
                voz.calar()
                if falhas >= 5:
                    print("Falhas repetidas, encerrando.")
                    break
                voz.falar(f"Tive um problema técnico, {tratamento}. Já estou voltando a escutar.")
                voz.esperar()
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        voz.calar()
        mic.parar()
        print("\nEncerrado.")


if __name__ == "__main__":
    main()
