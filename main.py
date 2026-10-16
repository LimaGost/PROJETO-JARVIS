import argparse
import sys
import threading
import time
import traceback

from jarvis.brain.comandos import identificar, sim_ou_nao
from jarvis.brain.llm import Cerebro
from jarvis.config import caminho, carregar
from jarvis.memoria import criar as criar_memoria
from jarvis.tools import apps
from jarvis.tools.executor import Executor
from jarvis.tools.todas import REGISTRO  # noqa: F401  (registra as ferramentas)


def aquecer_ou_sair(cerebro, cfg):
    try:
        cerebro.aquecer()
    except Exception as e:
        print(f"\nERRO: não consegui falar com o Ollama em {cfg['llm']['host']} ({type(e).__name__}).")
        print(f"Confirme que o Ollama está aberto e que o modelo existe: ollama pull {cfg['llm']['modelo']}")
        sys.exit(1)


def responder(cerebro, texto, voz=None, t0=None):
    t0 = t0 or time.time()
    primeira = True
    print("Jarvis: ", end="", flush=True)
    for frase in cerebro.responder(texto):
        if primeira:
            print(f"[1ª frase em {time.time() - t0:.2f}s] ", end="")
            primeira = False
        print(frase, end=" ", flush=True)
        if voz:
            voz.falar(frase)
    print()
    if voz:
        voz.esperar()


# ------------------------------------------------------------------ modo voz
def conversar(cfg, mic, ouvido, cerebro, voz, ocupado: threading.Event) -> bool:
    """Uma sessão: do 'Hey Jarvis' até o silêncio. Retorna False se o usuário mandou desligar."""
    print("\n[aguardando 'Hey Jarvis']")
    mic.esperar_wakeword()
    ocupado.set()  # a reflexão em segundo plano espera a conversa acabar
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
            voz.falar(f"Até logo, {cfg['tratamento']}.")
            voz.esperar()
            return False
        if comando == "esquecer":
            cerebro.esquecer()
            voz.falar("Conversa reiniciada.")
            voz.esperar()
        else:
            responder(cerebro, texto, voz, t1)

        espera = cfg["conversa"]["janela_followup_s"]


def modo_voz(cfg):
    from jarvis.audio.mic import Microfone
    from jarvis.audio.stt import Ouvido
    from jarvis.audio.tts import Voz

    tratamento = cfg["tratamento"]
    print("Carregando módulos...")
    t = time.time()
    voz = Voz(cfg)
    ouvido = Ouvido(cfg)
    mic = Microfone(cfg)

    def confirmar(resumo: str) -> bool:
        voz.esperar()  # termina o que estava falando antes de perguntar
        voz.falar(f"Atenção, {tratamento}: vou {resumo}. Confirma?")
        voz.esperar()
        audio = mic.gravar_fala(cfg["vad"]["espera_inicio_s"])
        resposta = ouvido.transcrever(audio) if audio is not None else ""
        print(f"\n  Você (confirmação): {resposta or '(silêncio)'}")
        return sim_ou_nao(resposta)

    ocupado = threading.Event()
    memoria, reflexao = criar_memoria(cfg, ocupado)
    if reflexao:
        reflexao.iniciar_zelador()
    cerebro = Cerebro(cfg, Executor(confirmar, caminho("data/logs")), memoria)
    aquecer_ou_sair(cerebro, cfg)
    ouvido.aquecer()
    print(f"Pronto em {time.time() - t:.1f}s. Diga 'Hey Jarvis'. Ctrl+C para sair.")

    mic.iniciar()
    voz.falar(f"Sistemas online. Às suas ordens, {tratamento}.")
    voz.esperar()

    falhas = 0
    try:
        while True:
            if reflexao:
                reflexao.em_segundo_plano()  # aprende com os dias anteriores enquanto está ocioso
            try:
                if not conversar(cfg, mic, ouvido, cerebro, voz, ocupado):
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
            finally:
                ocupado.clear()
    except KeyboardInterrupt:
        pass
    finally:
        voz.calar()
        mic.parar()
        print("\nEncerrado.")


# ------------------------------------------------------------------ modo texto
def modo_texto(cfg, falar: bool):
    """Digitar em vez de falar: útil por acesso remoto ou em lugar silencioso."""
    voz = None
    if falar:
        from jarvis.audio.tts import Voz
        voz = Voz(cfg)

    def confirmar(resumo: str) -> bool:
        if voz:
            voz.esperar()
        resposta = input(f"\n  🔴 Vou {resumo}. Confirma? (s/n) ").strip()
        return resposta.lower() in ("s", "y") or sim_ou_nao(resposta)

    ocupado = threading.Event()
    memoria, reflexao = criar_memoria(cfg, ocupado)
    if reflexao:
        reflexao.iniciar_zelador()
    cerebro = Cerebro(cfg, Executor(confirmar, caminho("data/logs")), memoria)
    aquecer_ou_sair(cerebro, cfg)
    print("Modo texto. Digite 'tchau jarvis' ou Ctrl+C para sair, 'esqueça a conversa' para recomeçar.")
    try:
        while True:
            if reflexao:
                reflexao.em_segundo_plano()
            texto = input("\nVocê: ").strip()
            if not texto:
                continue
            comando = identificar(texto)
            if comando == "desligar":
                print(f"Jarvis: Até logo, {cfg['tratamento']}.")
                break
            if comando == "esquecer":
                cerebro.esquecer()
                print("Jarvis: Conversa reiniciada.")
                continue
            ocupado.set()
            try:
                responder(cerebro, texto, voz)
            except Exception:
                traceback.print_exc()
            finally:
                ocupado.clear()
    except (KeyboardInterrupt, EOFError):
        print("\nEncerrado.")


def main():
    parser = argparse.ArgumentParser(description="Jarvis, assistente local")
    parser.add_argument("--texto", action="store_true", help="digitar em vez de falar")
    parser.add_argument("--falar", action="store_true", help="no modo texto, responder também em voz")
    args = parser.parse_args()

    cfg = carregar()
    apps.carregar_em_segundo_plano()  # lista de apps do menu Iniciar (~1 s)
    if args.texto:
        modo_texto(cfg, args.falar)
    else:
        modo_voz(cfg)


if __name__ == "__main__":
    main()
