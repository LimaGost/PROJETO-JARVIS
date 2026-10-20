"""Ponto central do Jarvis em execução: junta voz, HUD e texto.

- Um pedido por vez (`turno_lock`): se você fala enquanto digita na HUD, um espera o outro.
- Confirmação 🔴 por voz OU por clique na HUD, o que vier primeiro.
- Parar tudo: cancela a geração, calar a voz e nega qualquer confirmação pendente.
"""
import itertools
import random
import threading
import time
import traceback

import psutil

from jarvis.brain.comandos import sim_ou_nao
from jarvis.hud.bus import bus

TEMPO_CONFIRMACAO_S = 30   # tempo para clicar na HUD; depois disso conta como "não"
MAX_TEXTO_HUD = 1500


class Controle:
    def __init__(self, cfg: dict, cerebro, voz=None, mic=None, ouvido=None, memoria=None,
                 ocupado: threading.Event | None = None, confirmar_consola=None):
        self.cfg = cfg
        self.confirmar_consola = confirmar_consola  # modo texto: pergunta no terminal (s/n)
        self.tratamento = cfg["tratamento"]
        self.cerebro = cerebro
        self.voz, self.mic, self.ouvido, self.memoria = voz, mic, ouvido, memoria
        self.ocupado = ocupado or threading.Event()
        self.turno_lock = threading.Lock()
        self.encerrar = threading.Event()
        self._origem = threading.local()
        self._origem_turno: str | None = None  # origem do pedido em andamento (para ferramentas chamadas de outra thread)
        self._ids = itertools.count(1)
        self._pendente: dict | None = None
        self._lock = threading.Lock()
        self._inicio = time.time()
        self._cpu_inicializada = False
        self._gpu: tuple[float, dict] = (0.0, {})
        self._gerando = False         # o cérebro ainda está produzindo a resposta
        self._interrompido = False    # você falou por cima: o resto da resposta não é falado
        self._reconheceu = False      # já disse "um momento" neste pedido
        self.falado_turno: list[str] = []

    # ------------------------------------------------------------ confirmação 🔴
    def confirmar(self, resumo: str, nome: str = "", args: dict | None = None) -> bool:
        """Chamado pelo Executor numa ação 🔴. Só retorna True com um 'sim' claro."""
        # a origem do pedido vem da thread que o iniciou; quando a ferramenta chega de outra thread (a ponte do
        # claude-cli), vale a origem do pedido em andamento. Pedido digitado na HUD nunca confirma pelo microfone.
        origem = getattr(self._origem, "valor", None) or self._origem_turno or "voz"
        if origem == "consola" and self.confirmar_consola:
            if self.voz is not None:
                self.voz.esperar()
            return bool(self.confirmar_consola(resumo))
        cid = next(self._ids)
        evento = threading.Event()
        with self._lock:
            self._pendente = {"id": cid, "evento": evento, "sim": False, "por": None}
        bus.emitir("estado", estado="confirmando")
        bus.emitir("confirmacao", id=cid, resumo=resumo, nome=nome, args=args or {}, expira_s=TEMPO_CONFIRMACAO_S)

        voz_ouve = (self.voz is not None and self.mic is not None and self.ouvido is not None
                    and not self.mic.silenciado and origem != "hud")
        if self.voz is not None:
            self.voz.esperar()  # termina o que estava falando antes de perguntar
            self.voz.falar(f"Atenção, {self.tratamento}: vou {resumo}. Confirma?")
            self.voz.esperar()

        if voz_ouve and not evento.is_set():
            audio = self.mic.gravar_fala(self.cfg["vad"]["espera_inicio_s"], cancelar=evento)
            if audio is not None and not evento.is_set():
                resposta = self.ouvido.transcrever(audio)
                print(f"\n  Você (confirmação): {resposta or '(silêncio)'}")
                bus.emitir("sistema", texto=f"Confirmação por voz: “{resposta}”")
                self.responder_confirmacao(cid, sim_ou_nao(resposta), "voz")

        if not evento.is_set():
            if bus.conexoes > 0:  # alguém está olhando a HUD: dá tempo para clicar
                evento.wait(TEMPO_CONFIRMACAO_S)
            if not evento.is_set():
                self.responder_confirmacao(cid, False, "tempo")

        with self._lock:
            sim = bool(self._pendente and self._pendente["id"] == cid and self._pendente["sim"])
            por = self._pendente["por"] if self._pendente else None
            self._pendente = None
        bus.emitir("confirmacao_resolvida", id=cid, sim=sim, por=por)
        bus.emitir("estado", estado="pensando")
        return sim

    def responder_confirmacao(self, cid: int, sim: bool, por: str = "hud") -> bool:
        """A primeira resposta vence. Ids antigos ou repetidos são ignorados."""
        with self._lock:
            p = self._pendente
            if p is None or p["id"] != cid or p["evento"].is_set():
                return False
            p["sim"], p["por"] = bool(sim), por
            p["evento"].set()
            return True

    # ------------------------------------------------------------ pedidos
    def processar(self, texto: str, origem: str = "voz", t0: float | None = None):
        """Um pedido completo: pensa, age e fala. Mostra tudo na HUD e no console."""
        t0 = t0 or time.time()
        with self.turno_lock:
            self._origem_turno = "consola" if origem == "texto" else origem
            self._interrompido = False
            self._reconheceu = False
            self.falado_turno = []
            bus.emitir("usuario", texto=texto, origem=origem)
            bus.emitir("estado", estado="pensando")
            primeira = True
            print("Jarvis: ", end="", flush=True)
            try:
                self._gerando = True
                for frase in self.cerebro.responder(texto):
                    if primeira:
                        print(f"[1ª frase em {time.time() - t0:.2f}s] ", end="")
                        primeira = False
                    print(frase, end=" ", flush=True)
                    self.falado_turno.append(frase)
                    bus.emitir("jarvis", texto=frase)
                    if self.voz and not self._interrompido:
                        self.voz.falar(frase)
                self._gerando = False
                print()
                if self.voz:
                    self.voz.esperar()
            finally:
                self._gerando = False
                self._origem_turno = None
                bus.emitir("fim_turno")
                self.enviar_memoria()

    def texto_do_hud(self, texto: str):
        """Pedido digitado na HUD. Roda em thread própria; a confirmação aqui é só por clique."""
        texto = (texto or "").strip()[:MAX_TEXTO_HUD]
        if not texto:
            return
        self._origem.valor = "hud"
        from jarvis.brain.comandos import identificar
        comando = identificar(texto)
        if comando == "desligar":
            bus.emitir("usuario", texto=texto, origem="hud")
            bus.emitir("jarvis", texto=f"Até logo, {self.tratamento}.")
            bus.emitir("fim_turno")
            self.encerrar.set()
            if self.mic:
                self.mic.encerrar.set()
            return
        if comando == "esquecer":
            self.esquecer_conversa()
            return
        if comando == "calar":
            self.calar()
            return
        if comando and comando.startswith("trocar:"):
            bus.emitir("usuario", texto=texto, origem="hud")
            self.trocar_por_alvo(comando.split(":", 1)[1])
            return
        self.ocupado.set()
        try:
            self.processar(texto, "hud")
        except Exception:
            traceback.print_exc()
            bus.emitir("sistema", texto="Tive um problema ao processar esse pedido.")
        finally:
            self.ocupado.clear()
            if not bus.falando:
                bus.emitir("estado", estado="aguardando")

    # ------------------------------------------------------------ fluidez
    def reconhecer_acao(self, nome: str, nivel: str):
        """Resposta instantânea: uma ação vai começar e ele ainda não disse nada neste pedido. Em vez de silêncio
        enquanto abre o programa, pesquisa ou programa, ele já responde ("Já cuido disso, senhor")."""
        if (self.voz is None or not self._gerando or self.falado_turno or self._interrompido
                or getattr(self, "_reconheceu", True) or nivel == "CONFIRMAR" or nome in self.ACOES_INSTANTANEAS):
            return
        self._reconheceu = True
        frase = random.choice(self.RECONHECIMENTOS).format(t=self.tratamento)
        self.falado_turno.append(frase)
        bus.emitir("jarvis", texto=frase)
        self.voz.falar(frase)

    RECONHECIMENTOS = ("Um momento, {t}.", "Já cuido disso.", "Certamente, {t}.", "Deixa comigo.", "É pra já, {t}.",
                       "Agora mesmo.")
    ACOES_INSTANTANEAS = {"hora_data", "calcular", "volume", "controle_midia", "lembrar", "memorizar", "esquecer_fato",
                          "uso_claude", "tarefa_programacao", "vscode", "listar_janelas", "focar_janela"}

    def anunciar(self, frase: str):
        """Fala por iniciativa própria (uma tarefa longa terminou), sem atropelar a conversa: espera ela acabar."""
        def falar():
            while self.ocupado.is_set() and not self.encerrar.is_set():
                time.sleep(0.5)
            with self.turno_lock:
                bus.emitir("jarvis", texto=frase)
                bus.emitir("fim_turno")
                if self.memoria is not None:
                    try:
                        self.memoria.registrar_turno("avisos", "(o Jarvis avisou por conta própria)", frase, [])
                    except Exception:
                        pass
                if self.voz:
                    self.voz.falar(frase)
                    self.voz.esperar()
        threading.Thread(target=falar, daemon=True, name="anuncio").start()

    # ------------------------------------------------------------ controles
    def parar(self):
        """Botão 'parar tudo'."""
        self.cerebro.cancelar()
        if self.voz:
            self.voz.calar()
        with self._lock:
            p = self._pendente
        if p:
            self.responder_confirmacao(p["id"], False, "parar")
        bus.emitir("sistema", texto="Interrompido pelo usuário.")

    def interromper(self):
        """Você falou por cima: ele cala na hora e cancela o que ainda estava pensando/fazendo."""
        self._interrompido = True
        if self.voz:
            self.voz.calar()
        if self._gerando:
            self.cerebro.cancelar()
        bus.emitir("estado", estado="ouvindo")

    def calar(self):
        """'Fica quieto': para de falar e de fazer, sem responder nada."""
        self._interrompido = True
        if self._gerando:
            self.cerebro.cancelar()
        if self.voz:
            self.voz.calar()
        bus.emitir("sistema", texto="Em silêncio.")
        bus.emitir("estado", estado="aguardando")

    def esquecer_conversa(self):
        self.cerebro.esquecer()
        bus.emitir("sistema", texto="Conversa reiniciada.")
        if self.voz:
            self.voz.falar("Conversa reiniciada.")

    # ------------------------------------------------------------ mentalidade (Qwen / Claude)
    NOMES_FALADOS = {"qwen": "Qwen local", "claude-cli": "Claude", "claude-api": "Claude por API",
                     "codex-cli": "ChatGPT", "gemini-cli": "Gemini"}

    def enviar_cerebro(self, trocando: str | None = None):
        if hasattr(self.cerebro, "estado"):
            bus.emitir("cerebro", trocando=trocando, **self.cerebro.estado())

    NOMES_MODELOS = {"opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku"}

    def trocar_modelo_claude(self, modelo: str):
        """Opus / Sonnet / Haiku, esperando o pedido atual acabar. Se o novo não subir, o atual continua."""
        sel = self.cerebro
        if not hasattr(sel, "trocar_modelo_claude"):
            bus.emitir("sistema", texto="Este JARVIS não troca o modelo do Claude.")
            return
        if not self.turno_lock.acquire(timeout=90):
            self.enviar_cerebro()
            bus.emitir("sistema", texto="Estou no meio de um pedido; tente trocar de novo em instantes.")
            return
        nome = self.NOMES_MODELOS.get(modelo, modelo)
        try:
            self.enviar_cerebro(trocando=f"modelo:{modelo}")
            bus.emitir("estado", estado="iniciando", detalhe=f"Trocando para o {nome}…")
            resultado = sel.trocar_modelo_claude(modelo)
        finally:
            self.turno_lock.release()
        self.enviar_cerebro()
        bus.emitir("estado", estado="aguardando")
        if resultado["ok"]:
            bus.emitir("sistema", texto=f"Claude agora no {nome}. A conversa continua de onde parou.")
            if self.voz:
                self.voz.falar(f"Agora estou pensando com o {nome}, {self.tratamento}.")
        else:
            bus.emitir("sistema", texto=f"Não consegui trocar para o {nome}: {resultado['erro']}. Continuo como estava.")
            if self.voz:
                self.voz.falar(f"Não consegui trocar para o {nome}, {self.tratamento}. Continuo com o modelo atual.")

    def trocar_por_alvo(self, alvo: str):
        """Comando de voz/texto ('usa o claude'): 'claude' vira a melhor opção Claude disponível;
        'opus' / 'sonnet' / 'haiku' trocam o modelo do Claude (e passam para o Claude se estiver no Qwen)."""
        sel = self.cerebro
        if alvo in getattr(sel, "modelos_claude", ()):
            self.trocar_modelo_claude(alvo)
            return
        id_ = sel.resolver(alvo) if hasattr(sel, "resolver") else None
        if id_ is None:
            nome = {"chatgpt": "ChatGPT", "gemini": "Gemini"}.get(alvo, "Claude")
            bus.emitir("sistema", texto=f"O {nome} não está disponível agora (não está instalado neste PC).")
            if self.voz:
                self.voz.falar(f"O {nome} não está disponível agora, {self.tratamento}.")
            return
        self.trocar_cerebro(id_)

    def trocar_cerebro(self, id_: str):
        """Troca a mentalidade esperando o pedido atual acabar. Se a nova não subir, a anterior continua em uso."""
        sel = self.cerebro
        if not hasattr(sel, "trocar"):
            bus.emitir("sistema", texto="Este JARVIS não tem seletor de mentalidade.")
            return
        if not self.turno_lock.acquire(timeout=90):
            self.enviar_cerebro()  # a HUD já marcou "trocando": devolve o estado real para ela liberar os botões
            bus.emitir("sistema", texto="Estou no meio de um pedido; tente trocar de novo em instantes.")
            return
        try:
            nome = self.NOMES_FALADOS.get(id_, id_)
            self.enviar_cerebro(trocando=id_)
            bus.emitir("estado", estado="iniciando", detalhe=f"Trocando para {nome}…")
            resultado = sel.trocar(id_)
        finally:
            self.turno_lock.release()
        self.enviar_cerebro()
        bus.emitir("estado", estado="aguardando")
        if resultado["ok"]:
            bus.emitir("sistema", texto=f"Mentalidade: {sel.rotulo}. A conversa continua de onde parou.")
            if self.voz:
                self.voz.falar(f"Mentalidade {nome} ativada.")
        elif resultado.get("precisa_login"):
            self._login_e_trocar(id_, nome)
        else:
            bus.emitir("sistema", texto=f"Não consegui trocar para {nome}: {resultado['erro']}. Continuo com {sel.rotulo}.")
            if self.voz:
                self.voz.falar(f"Não consegui trocar para {nome}, senhor. Continuo com a mentalidade atual.")

    def _login_e_trocar(self, id_: str, nome: str):
        """Cérebro de CLI sem login: abre o login oficial e, quando você terminar de entrar, troca sozinho."""
        from jarvis.brain import logins
        from jarvis.brain.seletor import CLI_DO_CEREBRO
        cli = CLI_DO_CEREBRO.get(id_)
        if cli is None:
            return

        def terminou(ok: bool, detalhe: str):
            self.enviar_cerebro()
            if ok:
                bus.emitir("sistema", texto=f"Login do {nome} feito. Trocando…")
                self.trocar_cerebro(id_)
            else:
                bus.emitir("sistema", texto=f"O login do {nome} não terminou ({detalhe}). Continuo com {self.cerebro.rotulo}.")
        try:
            frase = logins.login_automatico(cli, terminou)
        except Exception as e:
            bus.emitir("sistema", texto=f"Não consegui abrir o login do {nome}: {e}")
            return
        self.enviar_cerebro()
        bus.emitir("sistema", texto=frase)
        if self.voz:
            self.voz.falar(f"O {nome} precisa de login, {self.tratamento}. Abri a janela; entre com a sua conta que eu troco sozinho.")

    def silenciar(self, valor: bool):
        if self.mic:
            self.mic.silenciado = bool(valor)
        bus.emitir("mudo", valor=bool(valor))

    # ------------------------------------------------------------ memória
    def enviar_memoria(self):
        if self.memoria is None:
            return
        try:
            bus.emitir("memoria", fatos=[{"id": f.id, "texto": f.texto, "categoria": f.categoria,
                                          "atualizado": f.atualizado[:10]} for f in self.memoria.fatos()])
        except Exception:
            pass

    def apagar_fato(self, id_: int):
        if self.memoria and self.memoria.apagar_fato(id_):
            bus.emitir("sistema", texto="Fato esquecido.")
        self.enviar_memoria()

    # ------------------------------------------------------------ telemetria para a HUD
    def niveis(self) -> dict:
        return {"mic": round(min(1.0, getattr(self.mic, "nivel", 0.0)), 3) if self.mic else 0.0,
                "voz": round(self.voz.nivel(), 3) if self.voz else 0.0}

    def estatisticas(self) -> dict:
        if not self._cpu_inicializada:
            psutil.cpu_percent(None)
            self._cpu_inicializada = True
        mem = psutil.virtual_memory()
        return {"cpu": psutil.cpu_percent(None), "ram": mem.percent,
                "ram_usada_gb": round(mem.used / 1024**3, 1), "ram_total_gb": round(mem.total / 1024**3, 1),
                "ligado_s": int(time.time() - self._inicio), **self._gpu_info()}

    def _gpu_info(self) -> dict:
        import subprocess
        agora = time.time()
        if agora - self._gpu[0] < 2.5:
            return self._gpu[1]
        info: dict = {}
        try:
            saida = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout.strip()
            uso, usada, total, temp = [float(x) for x in saida.split(",")]
            info = {"gpu": uso, "vram_usada_mb": usada, "vram_total_mb": total, "gpu_temp": temp}
        except Exception:
            pass
        self._gpu = (agora, info)
        return info

    def pronto(self):
        """Módulos carregados: a HUD aprende o que está disponível (microfone, voz) e libera o botão de mudo."""
        bus.emitir("pronto", tem_voz=self.voz is not None, tem_mic=self.mic is not None)

    def hello(self) -> dict:
        modelo = getattr(self.cerebro, "rotulo", None) or self.cfg["llm"]["modelo"]
        cerebro = self.cerebro.estado() if hasattr(self.cerebro, "estado") else None
        return {"nome": self.cfg["nome"], "tratamento": self.tratamento, "modelo": modelo, "cerebro": cerebro,
                "estado": bus.estado, "falando": bus.falando, "mudo": bus.mudo,
                "pendente": ({"id": self._pendente["id"]} if self._pendente else None),
                "tem_voz": self.voz is not None, "tem_mic": self.mic is not None,
                "tem_memoria": self.memoria is not None}
