import time

T_PROCESSO = time.time()  # antes dos imports pesados: a partida mede também o tempo de carregar as bibliotecas

import argparse  # noqa: E402
import queue  # noqa: E402
import random  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import traceback  # noqa: E402

from jarvis.brain import avisos
from jarvis.brain.comandos import identificar, sim_ou_nao
from jarvis.brain.seletor import Seletor
from jarvis.config import RAIZ, caminho, carregar
from jarvis.hud import bandeja, inicio, janela
from jarvis.hud.bus import bus
from jarvis.hud.controle import Controle
from jarvis.memoria import criar as criar_memoria
from jarvis.tools import apps
from jarvis.tools.executor import Executor
from jarvis.tools.todas import REGISTRO  # noqa: F401  (registra as ferramentas)

class Cronometro:
    """Tempo de cada etapa da partida, no console e em data/logs/partida.log, para saber onde ela demora."""

    def __init__(self, inicio: float):
        self.inicio = self.ultimo = inicio
        self.etapas: list[tuple[str, float]] = []

    def marca(self, nome: str):
        agora = time.time()
        self.etapas.append((nome, agora - self.ultimo))
        self.ultimo = agora

    def resumo(self) -> str:
        partes = " · ".join(f"{n} {s:.1f}s" for n, s in self.etapas)
        return f"total {self.ultimo - self.inicio:.1f}s: {partes}"

    def gravar(self):
        print(f"[partida] {self.resumo()}")
        try:
            pasta = RAIZ / "data" / "logs"
            pasta.mkdir(parents=True, exist_ok=True)
            with open(pasta / "partida.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {self.resumo()}\n")
        except OSError:
            pass


CRONOMETRO = Cronometro(T_PROCESSO)
ESPERA_OLLAMA_S = 120  # no boot do Windows o Ollama pode demorar a subir
CHAMADOS = ("Pois não?", "Sim, {t}?", "Às ordens.", "Pois não, {t}?", "Estou aqui.")   # variar soa menos robótico


def aquecer_ou_sair(cerebro, cfg):
    limite = time.time() + ESPERA_OLLAMA_S
    avisou = False
    while True:
        try:
            cerebro.aquecer()
            return
        except Exception as e:
            if time.time() > limite:
                print(f"\nERRO: não consegui falar com o Ollama em {cfg['llm']['host']} ({type(e).__name__}).")
                print(f"Confirme que o Ollama está aberto e que o modelo existe: ollama pull {cfg['llm']['modelo']}")
                bus.emitir("sistema", texto="Não consegui falar com o Ollama. Abra-o e reinicie o Jarvis.")
                sys.exit(1)
            if not avisou:
                print("Aguardando o Ollama ficar pronto...")
                bus.emitir("estado", estado="iniciando", detalhe="Aguardando o Ollama ficar pronto…")
                avisou = True
            time.sleep(3)


def iniciar_hud(cfg, ctl, silencioso: bool):
    """Sobe o servidor da HUD, a janela e a bandeja. Retorna (servidor, ícone) ou (None, None)."""
    h = cfg.get("hud", {})
    if not h.get("ativa", True):
        return None, None
    try:
        from jarvis.hud.servidor import ServidorHUD
        servidor = ServidorHUD(ctl, h.get("host", "127.0.0.1"), h.get("porta", 8765))
        servidor.iniciar()
    except Exception as e:  # a HUD é um extra: sem ela o Jarvis continua funcionando
        print(f"[hud] não consegui iniciar: {type(e).__name__}: {e}")
        return None, None
    print(f"HUD em {servidor.url}")
    icone = bandeja.iniciar(servidor.url, ctl) if h.get("bandeja", True) else None
    if h.get("abrir_ao_iniciar", True) and not silencioso:
        janela.abrir(servidor.url)
    return servidor, icone


# ------------------------------------------------------------------ modo voz
def processar_ouvindo(cfg, ctl, texto: str, t1: float) -> str | None:
    """Processa o pedido escutando ao mesmo tempo: você pode interromper falando por cima ou com "Hey Jarvis".
    Retorna o que você disse por cima (None = ninguém interrompeu)."""
    if not cfg.get("interrupcao", {}).get("ativa", True):
        ctl.processar(texto, "voz", t1)
        return None
    from jarvis.audio.interrupcao import Escuta
    escuta = Escuta(cfg, ctl.mic, ctl.ouvido, ctl.voz, ao_interromper=ctl.interromper,
                    falado=lambda: " ".join(ctl.falado_turno), pausado=lambda: ctl._pendente is not None)
    with escuta:
        ctl.processar(texto, "voz", t1)
    if escuta.resultado is not None:
        bus.emitir("sistema", texto="Interrompido por você.")
    return escuta.resultado


def conversar(cfg, ctl, ocupado: threading.Event) -> bool:
    """Uma sessão: do 'Hey Jarvis' até o silêncio. Retorna False se mandaram desligar."""
    mic, ouvido, voz = ctl.mic, ctl.ouvido, ctl.voz
    ctl._origem.valor = "voz"
    bus.emitir("estado", estado="aguardando")
    print("\n[aguardando 'Hey Jarvis']")
    if not mic.esperar_wakeword():
        return False
    ocupado.set()  # a reflexão em segundo plano espera a conversa acabar
    bus.emitir("estado", estado="ouvindo")
    voz.falar(random.choice(CHAMADOS).format(t=cfg["tratamento"]))
    voz.esperar()

    espera = cfg["vad"]["espera_inicio_s"]
    por_cima: str | None = None   # o que você disse interrompendo a resposta anterior
    while not ctl.encerrar.is_set():  # janela de conversa: continua sem precisar do wake word
        if por_cima:
            texto, t1 = por_cima, time.time()
            por_cima = None
            print(f"Você (interrompendo): {texto}")
        else:
            bus.emitir("estado", estado="ouvindo")
            audio = mic.gravar_fala(espera)
            if audio is None:
                return True

            bus.emitir("estado", estado="transcrevendo")
            t0 = time.time()
            texto = ouvido.transcrever(audio)
            t1 = time.time()
            if len(texto) < 2:
                return True
            print(f"Você: {texto}  ({t1 - t0:.2f}s de transcrição)")

        comando = identificar(texto)
        if comando == "calar":                       # "fica quieto": cala e não responde nada
            bus.emitir("usuario", texto=texto, origem="voz")
            ctl.calar()
            return True                              # volta a esperar o "Hey Jarvis"
        if comando == "desligar":
            bus.emitir("usuario", texto=texto, origem="voz")
            adeus = f"Até logo, {cfg['tratamento']}."
            bus.emitir("jarvis", texto=adeus)
            bus.emitir("fim_turno")
            voz.falar(adeus)
            voz.esperar()
            ctl.encerrar.set()
            return False
        if comando == "esquecer":
            bus.emitir("usuario", texto=texto, origem="voz")
            ctl.esquecer_conversa()
            voz.esperar()
        elif comando and comando.startswith("trocar:"):
            bus.emitir("usuario", texto=texto, origem="voz")
            ctl.trocar_por_alvo(comando.split(":", 1)[1])
            voz.esperar()
        else:
            por_cima = processar_ouvindo(cfg, ctl, texto, t1)
            if por_cima == "":                       # interrompeu com "Hey Jarvis" e não disse nada: escuta de novo
                por_cima = None
                espera = cfg["vad"]["espera_inicio_s"]
                continue

        espera = cfg["conversa"]["janela_followup_s"]
        if ctl.falado_turno and ctl.falado_turno[-1].rstrip().endswith("?"):
            espera = max(espera, cfg["conversa"].get("janela_pergunta_s", 15))   # ele perguntou: dá tempo de pensar
    return False


def modo_voz(cfg, silencioso: bool):
    from jarvis.audio.mic import Microfone
    from jarvis.audio.stt import Ouvido
    from jarvis.audio.tts import Voz

    tratamento = cfg["tratamento"]
    ocupado = threading.Event()
    ctl = Controle(cfg, None, ocupado=ocupado)
    bus.emitir("estado", estado="iniciando", detalhe="Carregando voz, audição e memória…")
    servidor, icone = iniciar_hud(cfg, ctl, silencioso)
    cron = CRONOMETRO
    cron.marca("HUD")

    print("Carregando módulos...")
    t = time.time()
    ctl.voz = Voz(cfg)                       # a voz clonada sobe em outro processo, em paralelo com o resto
    t_voz = time.time()
    ctl.ouvido = Ouvido(cfg)
    cron.marca("Whisper")
    ctl.mic = mic = Microfone(cfg)
    cron.marca("microfone")
    memoria, reflexao = criar_memoria(cfg, ocupado)
    ctl.memoria = memoria
    cron.marca("memória")
    if reflexao:
        reflexao.iniciar_zelador(aviso=lambda txt: bus.emitir("sistema", texto=txt.strip()),
                                 exigir_ocio=lambda: getattr(ctl.cerebro, "atual", "qwen") != "qwen")
    executor = Executor(ctl.confirmar, caminho("data/logs"))
    executor.ao_comecar = ctl.reconhecer_acao      # resposta instantânea quando uma ação vai demorar
    avisos.ouvir(ctl.anunciar)                     # tarefas em segundo plano avisam por voz quando terminam
    ctl.cerebro = cerebro = Seletor(cfg, executor, memoria)
    if reflexao:
        reflexao.consolidador.keep_alive = lambda: cerebro.local.keep_alive
    bus.emitir("estado", estado="iniciando", detalhe="Carregando o modelo de linguagem…")
    aquecer_ou_sair(cerebro, cfg)
    ctl.enviar_cerebro()
    cron.marca(f"cérebro ({cerebro.atual})")
    bus.emitir("estado", estado="iniciando", detalhe="Preparando o reconhecimento de voz…")
    ctl.ouvido.aquecer()
    cron.marca("aquecer Whisper")
    bus.emitir("estado", estado="iniciando", detalhe="Preparando a voz…")
    if not ctl.voz.esperar_pronta(150):      # nunca fala com outra voz: espera a dele (carrega em paralelo, ~30 s)
        print("[tts] a voz ainda não ficou pronta; as primeiras respostas podem sair só em texto.")
    cron.marca("esperando a voz")
    print(f"[partida] a voz clonada levou {time.time() - t_voz:.1f}s para ficar pronta (em paralelo; detalhes em "
          "data/logs/voz_clonada.log)")
    cron.gravar()
    ctl.voz.memorizaveis = {f.format(t=tratamento) for f in CHAMADOS + Controle.RECONHECIMENTOS}
    print(f"Pronto em {time.time() - t:.1f}s. Diga 'Hey Jarvis'. Ctrl+C para sair.")

    mic.iniciar()
    ctl.pronto()
    ctl.enviar_memoria()
    boas_vindas = f"Sistemas online. Às suas ordens, {tratamento}."
    bus.emitir("jarvis", texto=boas_vindas)
    bus.emitir("fim_turno")
    ctl.voz.falar(boas_vindas)
    ctl.voz.esperar()

    falhas = 0
    try:
        while not ctl.encerrar.is_set():
            if reflexao and cerebro.atual == "qwen":   # com o Claude, quem decide é o zelador (espera o PC ocioso)
                reflexao.em_segundo_plano(aviso=lambda txt: bus.emitir("sistema", texto=txt.strip()))
            try:
                if not conversar(cfg, ctl, ocupado):
                    break
                falhas = 0
            except KeyboardInterrupt:
                raise
            except Exception:
                # um erro em qualquer etapa não pode derrubar o assistente
                falhas += 1
                traceback.print_exc()
                ctl.voz.calar()
                if falhas >= 5:
                    print("Falhas repetidas, encerrando.")
                    bus.emitir("sistema", texto="Falhas repetidas: encerrando.")
                    break
                bus.emitir("sistema", texto="Tive um problema técnico; voltando a escutar.")
                ctl.voz.falar(f"Tive um problema técnico, {tratamento}. Já estou voltando a escutar.")
                ctl.voz.esperar()
                time.sleep(1)
            finally:
                ocupado.clear()
    except KeyboardInterrupt:
        pass
    finally:
        bus.emitir("estado", estado="iniciando", detalhe="Encerrando…")
        ctl.voz.calar()
        mic.parar()
        if icone:
            icone.stop()
        if servidor:
            time.sleep(0.2)
            servidor.parar()
        print("\nEncerrado.")


# ------------------------------------------------------------------ modo texto
class Teclado:
    """Lê o terminal numa thread, para o laço principal poder encerrar mesmo sem ninguém digitando
    (ex.: 'tchau jarvis' digitado na HUD)."""

    def __init__(self, encerrar: threading.Event):
        self.fila: queue.Queue = queue.Queue()
        self.encerrar = encerrar
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self):
        while True:
            linha = sys.stdin.readline()
            self.fila.put(None if linha == "" else linha.rstrip("\r\n"))
            if linha == "":
                return

    def ler(self, prompt: str) -> str:
        print(prompt, end="", flush=True)
        while not self.encerrar.is_set():
            try:
                linha = self.fila.get(timeout=0.3)
            except queue.Empty:
                continue
            if linha is None:
                raise EOFError
            return linha
        raise EOFError


def modo_texto(cfg, falar: bool, silencioso: bool):
    """Digitar em vez de falar: útil por acesso remoto ou em lugar silencioso. A HUD também funciona."""
    voz = None
    if falar:
        from jarvis.audio.tts import Voz
        voz = Voz(cfg)

    teclado: Teclado | None = None

    def perguntar(resumo: str) -> bool:
        resposta = teclado.ler(f"\n  🔴 Vou {resumo}. Confirma? (s/n) ").strip()
        return resposta.lower() in ("s", "y") or sim_ou_nao(resposta)

    ocupado = threading.Event()
    ctl = Controle(cfg, None, voz=voz, ocupado=ocupado, confirmar_consola=perguntar)
    teclado = Teclado(ctl.encerrar)
    servidor, icone = iniciar_hud(cfg, ctl, silencioso)
    memoria, reflexao = criar_memoria(cfg, ocupado)
    ctl.memoria = memoria
    if reflexao:
        reflexao.iniciar_zelador(aviso=lambda txt: bus.emitir("sistema", texto=txt.strip()),
                                 exigir_ocio=lambda: getattr(ctl.cerebro, "atual", "qwen") != "qwen")
    executor = Executor(ctl.confirmar, caminho("data/logs"))
    executor.ao_comecar = ctl.reconhecer_acao      # resposta instantânea quando uma ação vai demorar
    avisos.ouvir(ctl.anunciar)                     # tarefas em segundo plano avisam por voz quando terminam
    ctl.cerebro = cerebro = Seletor(cfg, executor, memoria)
    if reflexao:
        reflexao.consolidador.keep_alive = lambda: cerebro.local.keep_alive
    bus.emitir("estado", estado="iniciando", detalhe="Carregando o modelo de linguagem…")
    aquecer_ou_sair(cerebro, cfg)
    ctl.enviar_cerebro()
    if voz is not None:
        bus.emitir("estado", estado="iniciando", detalhe="Preparando a voz…")
        voz.esperar_pronta(150)
    ctl.pronto()
    ctl.enviar_memoria()
    bus.emitir("estado", estado="aguardando")
    print("Modo texto. Digite 'tchau jarvis' ou Ctrl+C para sair, 'esqueça a conversa' para recomeçar.")
    ctl._origem.valor = "consola"
    try:
        while not ctl.encerrar.is_set():
            texto = teclado.ler("\nVocê: ").strip()
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
            if comando and comando.startswith("trocar:"):
                ctl.trocar_por_alvo(comando.split(":", 1)[1])
                print(f"Jarvis: mentalidade em uso: {cerebro.rotulo}")
                continue
            ocupado.set()
            try:
                ctl.processar(texto, "texto")
            except Exception:
                traceback.print_exc()
            finally:
                ocupado.clear()
                if not bus.falando:
                    bus.emitir("estado", estado="aguardando")
    except (KeyboardInterrupt, EOFError):
        print("\nEncerrado.")
    finally:
        if icone:
            icone.stop()
        if servidor:
            servidor.parar()


def precarregar(voz: bool, falar: bool):
    """Carrega as bibliotecas nativas (numpy, onnxruntime, ctranslate2...) na thread principal, ANTES de
    qualquer outra thread existir. No Windows, criar uma thread enquanto outra carrega uma DLL pode travar
    o processo inteiro: a HUD (que cria threads o tempo todo) pendurava o Jarvis no `import numpy`."""
    import numpy  # noqa: F401
    import jarvis.memoria.banco, jarvis.memoria.embeddings, jarvis.memoria.reflexao  # noqa: F401,E401
    if voz:
        import jarvis.audio.mic, jarvis.audio.stt, jarvis.audio.tts  # noqa: F401,E401
    elif falar:
        import jarvis.audio.tts  # noqa: F401


def main():
    parser = argparse.ArgumentParser(description="Jarvis, assistente local")
    parser.add_argument("--texto", action="store_true", help="digitar em vez de falar")
    parser.add_argument("--falar", action="store_true", help="no modo texto, responder também em voz")
    parser.add_argument("--silencioso", action="store_true", help="não abre a janela da HUD (só o ícone da bandeja)")
    parser.add_argument("--instalar-inicio", action="store_true", help="iniciar o Jarvis junto com o Windows")
    parser.add_argument("--remover-inicio", action="store_true", help="desfaz --instalar-inicio")
    args = parser.parse_args()

    if args.instalar_inicio:
        print(f"Atalho criado: {inicio.instalar()}\nO Jarvis vai subir sozinho no próximo login (só o ícone da bandeja).")
        return
    if args.remover_inicio:
        print("Início automático removido." if inicio.remover() else "O início automático não estava instalado.")
        return

    if sys.stdout is None:  # pythonw (início automático): sem console, então tudo vai para um arquivo
        pasta = RAIZ / "data" / "logs"
        pasta.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(pasta / "jarvis.log", "a", encoding="utf-8", buffering=1)
        print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====")

    cfg = carregar()
    CRONOMETRO.marca("imports")
    precarregar(voz=not args.texto, falar=args.falar)
    CRONOMETRO.marca("bibliotecas nativas")
    apps.carregar_em_segundo_plano()  # lista de apps do menu Iniciar (~1 s)
    if args.texto:
        modo_texto(cfg, args.falar, args.silencioso)
    else:
        modo_voz(cfg, args.silencioso)


if __name__ == "__main__":
    main()
