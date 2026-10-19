"""Jarvis Core: a fachada do AI Command Center. Você (voz, texto ou HUD) fala só com isto.

Modos (orquestra.modo, troca na Central ou por voz):
  automatico     o Jarvis planeja e já executa. Pede confirmação só quando precisa: projeto ainda não autorizado
                 (uma vez por projeto). Continua valendo tudo: limites de custo, checkpoint, comandos bloqueados.
  supervisionado o Jarvis mostra o plano e espera você aprovar antes de executar (padrão).
  manual         você escolhe os agentes de cada passo (na Central ou "peça ao Codex para..."), depois aprova.
"""
import threading
import time
from pathlib import Path

from jarvis.orquestra import ia, relatorio, seguranca
from jarvis.orquestra.agentes import instancias
from jarvis.orquestra.banco import Banco, agora
from jarvis.orquestra.contexto import GerenteDeContexto
from jarvis.orquestra.custos import GerenteDeCustos
from jarvis.orquestra.execucao import OrquestradorDeTarefas
from jarvis.orquestra.monitor import Monitor
from jarvis.orquestra.planejador import Planejador, ordem_topologica
from jarvis.orquestra.roteador import Roteador, Similaridade
from jarvis.tools.base import ErroFerramenta

MODOS = ("automatico", "supervisionado", "manual")
ESPERANDO = ("aguardando_aprovacao", "interrompida")


class Orquestra:
    def __init__(self, cfg: dict | None = None, banco: Banco | None = None, cofre=None, embedder=None, emitir=None,
                 avisar=None, pensar=None, agentes: dict | None = None):
        self.cfg = cfg or {}
        o = self.cfg.get("orquestra", {}) or {}
        self.banco = banco or Banco()
        if cofre is None:
            from jarvis.connect.cofre import cofre as cofre_global
            cofre = cofre_global()
        self.cofre = cofre
        if emitir is None:
            from jarvis.hud.bus import bus
            emitir = bus.emitir
        if avisar is None:
            from jarvis.brain import avisos
            avisar = avisos.publicar
        self.avisar = avisar
        self.pensar = pensar or ia.pensar
        self.redator = seguranca.redator_do_cofre(cofre)
        self.agentes = agentes if agentes is not None else instancias(self.cfg, cofre)
        self.sim = Similaridade(embedder)
        self.custos = GerenteDeCustos(self.banco, self.cfg)
        self.contexto = GerenteDeContexto(self.banco, self.cfg, self.redator)
        self.roteador = Roteador(self.agentes, self.banco, self.custos, self.sim, self.cfg)
        self.planejador = Planejador(self.agentes, self.sim, self.cfg, self.pensar)
        self.monitor = Monitor(self.banco, self.redator, emitir)
        self.tarefas = OrquestradorDeTarefas(self)
        self.max_paralelos = int(o.get("max_passos_paralelos", 3))
        self.limite_passo_s = float(o.get("limite_passo_s", 1200))
        self.memoria_max_mb = float(o.get("memoria_max_mb", 4096))
        self.reparos_de_testes = int(o.get("reparos_de_testes", 1))
        self.relatorio_com_ia = o.get("relatorio_com_ia", True) is not False
        self._modo_cfg = o.get("modo", "supervisionado")

    # ------------------------------------------------------------ modo e projetos
    @property
    def modo(self) -> str:
        m = self.banco.config("modo") or self._modo_cfg
        return m if m in MODOS else "supervisionado"

    def definir_modo(self, modo: str) -> str:
        if modo not in MODOS:
            raise ValueError(f"modo inválido (use: {', '.join(MODOS)})")
        self.banco.salvar_config("modo", modo)
        self.monitor.mudou(None, modo=modo)
        return modo

    def resolver_projeto(self, texto: str = "") -> Path:
        """'' = o projeto aberto no VS Code; nome = um autorizado ou conhecido do VS Code; ou um caminho."""
        texto = (texto or "").strip().strip('"')
        if texto:
            p = Path(texto)
            if not p.is_absolute():
                autorizados = [Path(x["caminho"]) for x in self.banco.projetos() if x["nome"].lower() == texto.lower()]
                if autorizados:
                    p = autorizados[0]
                else:
                    from jarvis.tools.programador import pastas_do_vscode
                    casados = [q for q in pastas_do_vscode() if q.name.lower() == texto.lower()]
                    if not casados:
                        raise ErroFerramenta(f"não achei o projeto '{texto}'; diga o caminho ou abra no VS Code")
                    p = casados[0]
            return seguranca.validar_pasta(p)
        from jarvis.tools.programador import projeto_aberto
        p, _ = projeto_aberto()
        if p is None:
            raise ErroFerramenta("não há projeto aberto no VS Code; diga qual projeto")
        return seguranca.validar_pasta(p)

    def autorizar_projeto(self, pasta: str | Path, origem: str = "voce") -> dict:
        p = seguranca.validar_pasta(pasta)
        self.banco.autorizar(str(p), p.name, origem)
        self.monitor.log(None, None, None, "sistema", f"projeto autorizado: {p}")
        return {"caminho": str(p), "nome": p.name}

    def autorizado(self, pasta: str | Path | None) -> bool:
        return bool(pasta) and self.banco.autorizado(str(Path(pasta).resolve()))

    # ------------------------------------------------------------ criar, aprovar, executar
    def criar(self, pedido: str, projeto: str | Path | None = None, modo: str | None = None, passos: list | None = None,
              fixos: dict | None = None, origem: str = "hud", prioridade: str | None = None, iniciar: bool = True) -> dict:
        pedido = (pedido or "").strip()
        if not pedido:
            raise ValueError("diga o que é para fazer")
        modo = modo if modo in MODOS else self.modo
        raiz = seguranca.validar_pasta(projeto) if projeto else None
        plano, origem_plano, custo = self.planejador.planejar(pedido, str(raiz) if raiz else None, passos)
        for p in plano["passos"]:
            agente = (fixos or {}).get(p["id"]) or (fixos or {}).get(f"categoria:{p['categoria']}")
            if agente in self.agentes:
                p["agente"] = agente
        plano["origem"] = origem_plano
        plano["niveis"] = ordem_topologica(plano["passos"])
        escreve = any(p["escrita"] for p in plano["passos"])
        exe = self.banco.criar_execucao(pedido, str(raiz) if raiz else None, modo, plano, "planejada", origem,
                                        prioridade or plano.get("prioridade", "normal"))
        for p in plano["passos"]:
            self.banco.criar_passo(exe["id"], p)
        if custo:
            self.custos.registrar("nucleo", exe["id"], custo, False, 0, 0)
        self.monitor.log(exe["id"], None, None, "sistema",
                         f"plano criado ({origem_plano}, {len(plano['passos'])} passos, modo {modo})")
        if modo == "automatico" and (not escreve or self.autorizado(raiz)):
            espera = ""
        elif modo == "automatico":
            espera = f"o projeto {raiz.name} ainda não foi autorizado: aprove uma vez para os agentes trabalharem nele"
        elif modo == "manual":
            espera = "modo manual: confira os agentes de cada passo e aprove"
        else:
            espera = "modo supervisionado: confira o plano e aprove"
        if espera or not iniciar:
            self.banco.mudar_execucao(exe["id"], status="aguardando_aprovacao", erro=espera)
        else:
            self.banco.mudar_execucao(exe["id"], aprovada_em=agora())
            self.tarefas.iniciar(exe["id"])
        self.monitor.mudou(exe["id"])
        return self.detalhe(exe["id"])

    def trocar_agente(self, execucao_id: int, chave: str, agente: str | None) -> dict:
        exe = self._exigir(execucao_id)
        if exe["status"] not in ESPERANDO + ("planejada",):
            raise ValueError("só dá para trocar o agente antes de executar")
        if agente and agente not in self.agentes:
            raise ValueError(f"agente desconhecido: {agente}")
        self.banco.mudar_passo(execucao_id, chave, agente=agente or None, agente_fixo=bool(agente))
        self.monitor.mudou(execucao_id, chave)
        return self.detalhe(execucao_id)

    def aprovar(self, execucao_id: int, autorizar_projeto: bool = False) -> dict:
        exe = self._exigir(execucao_id)
        if exe["status"] not in ESPERANDO + ("planejada",):
            raise ValueError(f"a execução #{execucao_id} está {exe['status']}; não há o que aprovar")
        if exe["projeto"] and (autorizar_projeto or exe["modo"] == "automatico"):
            self.autorizar_projeto(exe["projeto"], "aprovacao")
        self.banco.mudar_execucao(execucao_id, aprovada_em=agora(), erro="")
        self.monitor.log(execucao_id, None, None, "sistema", "plano aprovado por você")
        self.tarefas.iniciar(execucao_id)
        return self.detalhe(execucao_id)

    def cancelar(self, execucao_id: int) -> dict:
        exe = self._exigir(execucao_id)
        if not self.tarefas.cancelar(execucao_id) and exe["status"] in ESPERANDO + ("planejada",):
            self.banco.mudar_execucao(execucao_id, status="cancelada", fim=agora())
            for p in self.banco.passos(execucao_id):
                if p["status"] == "pendente":
                    self.banco.mudar_passo(execucao_id, p["chave"], status="cancelado")
        self.monitor.log(execucao_id, None, None, "sistema", "cancelada por você")
        self.monitor.mudou(execucao_id)
        return self.detalhe(execucao_id)

    def pausar(self, execucao_id: int, passo: str | None = None) -> bool:
        return self.tarefas.pausar(execucao_id, passo)

    def retomar(self, execucao_id: int, passo: str | None = None) -> bool:
        exe = self._exigir(execucao_id)
        if self.tarefas.rodando(execucao_id):
            return self.tarefas.retomar(execucao_id, passo)
        if exe["status"] == "interrompida":
            self.aprovar(execucao_id)
            return True
        return False

    def cancelar_passo(self, execucao_id: int, passo: str) -> bool:
        return self.tarefas.cancelar_passo(execucao_id, passo)

    # ------------------------------------------------------------ desfazer (rollback)
    def a_desfazer(self, execucao_id: int) -> dict:
        exe = self._exigir(execucao_id)
        if not exe["checkpoint"] or not exe["projeto"]:
            raise ValueError("esta execução não tem checkpoint (não alterou arquivos, ou o projeto não usa git)")
        return seguranca.a_desfazer(Path(exe["projeto"]), exe["checkpoint"])

    def desfazer(self, execucao_id: int) -> dict:
        exe = self._exigir(execucao_id)
        if self.tarefas.rodando(execucao_id):
            raise ValueError("cancele a execução antes de desfazer")
        if exe["status"] == "desfeita":
            raise ValueError("essa execução já foi desfeita")
        if not exe["checkpoint"] or not exe["projeto"]:
            raise ValueError("esta execução não tem checkpoint para desfazer")
        r = seguranca.restaurar(Path(exe["projeto"]), exe["checkpoint"])
        self.banco.mudar_execucao(execucao_id, status="desfeita", desfeita_em=agora())
        self.monitor.log(execucao_id, None, None, "sistema", f"desfeita: {len(r['restaurados'])} arquivo(s) voltaram, "
                                                              f"{len(r['apagados'])} apagado(s)")
        self.monitor.mudou(execucao_id)
        return r

    # ------------------------------------------------------------ consultar e acompanhar
    def consultar(self, agente: str, pergunta: str, projeto: str | Path | None = None, esperar_s: float = 90) -> dict:
        if agente not in self.agentes:
            raise ValueError(f"agente desconhecido: {agente}")
        passos = [{"id": "p1", "titulo": pergunta[:120], "categoria": "consulta" if not projeto else "analise",
                   "instrucoes": pergunta, "agente": agente}]
        exe = self.criar(pergunta, projeto, "automatico", passos, origem="consulta")
        limite = time.time() + esperar_s
        while time.time() < limite and self.tarefas.rodando(exe["id"]):
            time.sleep(0.5)
        return self.detalhe(exe["id"])

    def relatorio(self, execucao_id: int) -> tuple[str, str]:
        return relatorio.gerar(self, execucao_id)

    def _exigir(self, execucao_id: int) -> dict:
        exe = self.banco.execucao(int(execucao_id))
        if exe is None:
            raise ValueError(f"não há execução #{execucao_id}")
        return exe

    def detalhe(self, execucao_id: int) -> dict:
        exe = self._exigir(execucao_id)
        exe["passos"] = self.banco.passos(execucao_id)
        exe["rodando"] = self.tarefas.rodando(execucao_id)
        exe["aguardando"] = exe["status"] in ESPERANDO
        exe["motivo_espera"] = exe["erro"] if exe["status"] == "aguardando_aprovacao" else ""
        return exe

    def resumo_plano(self, execucao_id: int) -> str:
        exe = self.detalhe(execucao_id)
        linhas = []
        for p in exe["passos"]:
            quem = self.agentes[p["agente"]].nome if p["agente"] in self.agentes else "o roteador escolhe"
            linhas.append(f"{p['titulo']} ({p['categoria']}, {quem}{', ALTERA ARQUIVOS' if p['escrita'] else ''})")
        onde = f" no projeto {Path(exe['projeto']).name}" if exe["projeto"] else ""
        return f"executar o plano #{exe['id']}{onde}: " + "; ".join(linhas)

    def estado_agentes(self) -> dict:
        from jarvis.orquestra.banco import inicio_do_dia
        vivos = self.monitor.por_agente()
        hoje = {g["agente"]: g for g in self.banco.gasto_por_agente(inicio_do_dia())}
        desempenho = self.banco.desempenho()
        cartoes = []
        for a in self.agentes.values():
            c = a.cartao()
            if vivos.get(a.id):
                c["estado"] = "executando"
            c["trabalhos"] = vivos.get(a.id, [])
            c["hoje"] = hoje.get(a.id) or {"custo": 0, "cobrado": 0, "entrada": 0, "saida": 0, "chamadas": 0}
            stats = [v for (ag, _), v in desempenho.items() if ag == a.id]
            c["historico"] = {"execucoes": sum(int(s["total"]) for s in stats),
                              "sucessos": sum(int(s["sucessos"] or 0) for s in stats)}
            cartoes.append(c)
        return {"modo": self.modo, "agentes": cartoes, "custos": self.custos.situacao(),
                "semantica": self.sim.modo}

    # ------------------------------------------------------------ partida
    def recuperar(self) -> int:
        """Execuções que estavam rodando quando o Jarvis fechou viram 'interrompida' (dá para retomar)."""
        n = 0
        for exe in self.banco.execucoes(50, ("executando", "planejada")):
            if self.tarefas.rodando(exe["id"]):
                continue
            for p in self.banco.passos(exe["id"]):
                if p["status"] == "executando":
                    self.banco.mudar_passo(exe["id"], p["chave"], status="pendente",
                                           retomar_de=p["chave"] if p.get("sessao") else p.get("retomar_de"))
            self.banco.mudar_execucao(exe["id"], status="interrompida", erro="o Jarvis foi fechado no meio; dá para retomar")
            n += 1
        return n

    def aquecer(self):
        """Detecta as CLIs em segundo plano (a primeira detecção do Gemini leva alguns segundos)."""
        def rodar():
            for a in list(self.agentes.values()):
                try:
                    a.disponibilidade()
                except Exception:
                    pass
        threading.Thread(target=rodar, daemon=True, name="orq-aquecer").start()
