"""Task Orchestrator: executa o plano como um grafo de dependências.

- Passos sem dependência pendente rodam em paralelo (até orquestra.max_passos_paralelos).
- Escrita: um escritor por projeto de cada vez (vale entre execuções diferentes também). Dois passos de escrita só
  rodam juntos se ambos declararam escopos que não se cruzam; mesmo assim, se mexerem no mesmo arquivo, o conflito
  vai para o relatório.
- Antes de escrever: checkpoint git da execução (desfazer tudo) e a árvore de antes de cada passo (desfazer só a
  tentativa que falhou, antes de tentar de novo ou com outro agente).
- Passo só-leitura que alterou arquivo: as alterações são desfeitas na hora e o fato vai para a auditoria.
- Falha: erro passageiro (rede, limite de taxa) tenta de novo o mesmo agente com espera; senão, o próximo do ranking.
- Testes que falham depois da mudança: o Jarvis pede ao mesmo agente que corrija (retomando a sessão dele quando a
  CLI permite) e roda os testes de novo (orquestra.reparos_de_testes, padrão 1 rodada).
- Limite de custo: antes de cada passo; estourou, a execução para e diz por quê.
"""
import threading
import time
import traceback
from pathlib import Path

from jarvis.orquestra import seguranca
from jarvis.orquestra.agentes.base import Controle, Resultado, Trabalho
from jarvis.orquestra.banco import agora

MAX_TENTATIVAS = 3
ESPERAS_TRANSITORIO_S = (5, 20)
FINAIS_PASSO = ("concluido", "falhou", "cancelado", "pulado", "bloqueado")


def _prefixo(escopo: str) -> str:
    corte = min([i for i in (escopo.find(c) for c in "*?[") if i >= 0] or [len(escopo)])
    return escopo[:corte].strip("/").lower()


def escopos_conflitam(a: list[str], b: list[str]) -> bool:
    if not a or not b:
        return True                                   # sem escopo = projeto inteiro
    for x in a:
        for y in b:
            px, py = _prefixo(x), _prefixo(y)
            if not px or not py or px.startswith(py) or py.startswith(px):
                return True
    return False


class TravasDeEscrita:
    """Um escritor por projeto (ou escopos que não se cruzam). Vale para todas as execuções."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ativas: dict[str, list[tuple[tuple, list[str]]]] = {}

    def tentar(self, projeto: str, dono: tuple, escopo: list[str]) -> bool:
        with self._lock:
            for outro, esc in self._ativas.get(projeto, []):
                if outro != dono and escopos_conflitam(esc, escopo):
                    return False
            self._ativas.setdefault(projeto, []).append((dono, list(escopo)))
            return True

    def soltar(self, projeto: str, dono: tuple):
        with self._lock:
            self._ativas[projeto] = [(d, e) for d, e in self._ativas.get(projeto, []) if d != dono]

    def ocupado(self, projeto: str) -> bool:
        with self._lock:
            return bool(self._ativas.get(projeto))


class EstadoExecucao:
    def __init__(self):
        self.cancelado = threading.Event()
        self.pausado = threading.Event()
        self.evento = threading.Event()
        self.controles: dict[str, Controle] = {}
        self.escritas: dict[str, tuple[float, float, set]] = {}
        self.parada = ""                          # por que parou de iniciar passos (custo...)
        self.git: bool | None = None


class OrquestradorDeTarefas:
    def __init__(self, nucleo):
        self.n = nucleo
        self.travas = TravasDeEscrita()
        self.estados: dict[int, EstadoExecucao] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ controle externo
    def iniciar(self, execucao_id: int) -> bool:
        with self._lock:
            if execucao_id in self.estados:
                return False
            st = self.estados[execucao_id] = EstadoExecucao()
        threading.Thread(target=self._rodar_seguro, args=(execucao_id, st), daemon=True,
                         name=f"orq-exec-{execucao_id}").start()
        return True

    def rodando(self, execucao_id: int) -> bool:
        return execucao_id in self.estados

    def cancelar(self, execucao_id: int) -> bool:
        st = self.estados.get(execucao_id)
        if st is None:
            return False
        st.cancelado.set()
        for c in list(st.controles.values()):
            c.cancelar()
        st.evento.set()
        return True

    def cancelar_passo(self, execucao_id: int, chave: str) -> bool:
        st = self.estados.get(execucao_id)
        c = st.controles.get(chave) if st else None
        if c is None:
            return False
        c.cancelar()
        return True

    def pausar(self, execucao_id: int, chave: str | None = None) -> bool:
        st = self.estados.get(execucao_id)
        if st is None:
            return False
        if chave is None:
            st.pausado.set()                      # não começa passos novos
        alvo = [st.controles[chave]] if chave and chave in st.controles else list(st.controles.values())
        ok = any([c.pausar() for c in alvo]) or chave is None
        for (e, p), v in list(self.n.monitor.ao_vivo.items()):
            if e == execucao_id and (chave is None or p == chave):
                v["pausado"] = True
        self.n.monitor.mudou(execucao_id, chave)
        return ok

    def retomar(self, execucao_id: int, chave: str | None = None) -> bool:
        st = self.estados.get(execucao_id)
        if st is None:
            return False
        if chave is None:
            st.pausado.clear()
        alvo = [st.controles[chave]] if chave and chave in st.controles else list(st.controles.values())
        for c in alvo:
            c.retomar()
        for (e, p), v in list(self.n.monitor.ao_vivo.items()):
            if e == execucao_id and (chave is None or p == chave):
                v["pausado"] = False
        st.evento.set()
        self.n.monitor.mudou(execucao_id, chave)
        return True

    # ------------------------------------------------------------ o laço da execução
    def _rodar_seguro(self, execucao_id: int, st: EstadoExecucao):
        try:
            self._rodar(execucao_id, st)
        except Exception as e:
            traceback.print_exc()
            self.n.banco.mudar_execucao(execucao_id, status="falhou", fim=agora(), erro=f"{type(e).__name__}: {e}")
            self.n.monitor.log(execucao_id, None, None, "erro", f"o orquestrador falhou: {type(e).__name__}: {e}")
        finally:
            with self._lock:
                self.estados.pop(execucao_id, None)
            self.n.monitor.mudou(execucao_id)

    def _log(self, execucao_id, chave, agente, tipo, texto):
        self.n.monitor.log(execucao_id, chave, agente, tipo, texto)

    def _rodar(self, execucao_id: int, st: EstadoExecucao):
        n, banco = self.n, self.n.banco
        exe = banco.execucao(execucao_id)
        raiz = Path(exe["projeto"]) if exe["projeto"] else None
        banco.mudar_execucao(execucao_id, status="executando", inicio=exe["inicio"] or agora(), erro="")
        self._log(execucao_id, None, None, "sistema", f"execução #{execucao_id} começou ({exe['modo']})")
        n.monitor.mudou(execucao_id)
        passos = banco.passos(execucao_id)
        if raiz is not None:
            st.git = seguranca.eh_git(raiz)
            if any(p["escrita"] for p in passos) and not exe["checkpoint"]:
                if st.git:
                    cp = seguranca.checkpoint(raiz, f"execucao-{execucao_id}")
                    banco.mudar_execucao(execucao_id, checkpoint=cp)
                    self._log(execucao_id, None, None, "sistema", f"checkpoint git {cp[:10]} (para desfazer tudo depois)")
                else:
                    self._log(execucao_id, None, None, "aviso", "o projeto não usa git: não vai dar para desfazer "
                                                                "automaticamente o que os agentes mudarem")
        ativos: dict[str, threading.Thread] = {}
        while True:
            passos = banco.passos(execucao_id)
            por_chave = {p["chave"]: p for p in passos}
            for p in passos:
                if p["status"] != "pendente":
                    continue
                ruins = [d for d in p["depende"] if por_chave[d]["status"] in ("falhou", "cancelado", "pulado", "bloqueado")
                         and not por_chave[d]["opcional"]]
                if ruins:
                    banco.mudar_passo(execucao_id, p["chave"], status="pulado", fim=agora(),
                                      erro=f"não rodou porque '{por_chave[ruins[0]]['titulo']}' não deu certo")
                    self._log(execucao_id, p["chave"], None, "aviso", f"pulado: depende de '{ruins[0]}'")
            passos = banco.passos(execucao_id)
            por_chave = {p["chave"]: p for p in passos}
            ativos = {k: t for k, t in ativos.items() if t.is_alive()}
            if st.cancelado.is_set():
                break
            prontos = [p for p in passos if p["status"] == "pendente" and p["chave"] not in ativos
                       and all(por_chave[d]["status"] == "concluido" or
                               (por_chave[d]["opcional"] and por_chave[d]["status"] in FINAIS_PASSO) for d in p["depende"])]
            if not st.pausado.is_set() and not st.parada:
                for p in prontos:
                    if len(ativos) >= n.max_paralelos:
                        break
                    if p["escrita"] and raiz is not None and not self.travas.tentar(str(raiz), (execucao_id, p["chave"]),
                                                                                   p["escopo"]):
                        continue
                    t = threading.Thread(target=self._rodar_passo, args=(execucao_id, p, raiz, st), daemon=True,
                                         name=f"orq-{execucao_id}-{p['chave']}")
                    ativos[p["chave"]] = t
                    banco.mudar_passo(execucao_id, p["chave"], status="executando")
                    t.start()
            pendentes = [p for p in passos if p["status"] in ("pendente", "executando")]
            if not ativos and (not pendentes or st.parada or not prontos and not any(
                    p["status"] == "executando" for p in pendentes)):
                break
            st.evento.wait(0.5)
            st.evento.clear()
        for t in ativos.values():
            t.join(timeout=60)
        self._finalizar(execucao_id, st, raiz)

    def _finalizar(self, execucao_id: int, st: EstadoExecucao, raiz: Path | None):
        n, banco = self.n, self.n.banco
        for p in banco.passos(execucao_id):
            if p["status"] in ("pendente", "executando"):
                banco.mudar_passo(execucao_id, p["chave"], status="cancelado" if st.cancelado.is_set() else "pulado",
                                  fim=agora(), erro=st.parada or ("cancelado" if st.cancelado.is_set() else "não rodou"))
        passos = banco.passos(execucao_id)
        if st.cancelado.is_set():
            status = "cancelada"
        elif all(p["status"] == "concluido" or p["opcional"] for p in passos):
            status = "concluida"
        else:
            status = "falhou"
        banco.mudar_execucao(execucao_id, status=status, fim=agora(), erro=st.parada or "")
        self._log(execucao_id, None, None, "sistema", f"execução {status}")
        try:
            texto, fala = n.relatorio(execucao_id)
        except Exception as e:
            texto, fala = f"(não consegui montar o relatório: {type(e).__name__}: {e})", f"A execução {execucao_id} {status}."
        banco.mudar_execucao(execucao_id, relatorio=texto)
        n.avisar(f"Orquestração #{execucao_id} ({status}): {texto[:1500]}", fala)
        n.monitor.mudou(execucao_id)

    # ------------------------------------------------------------ um passo
    def _rodar_passo(self, execucao_id: int, passo: dict, raiz: Path | None, st: EstadoExecucao):
        n, banco, chave = self.n, self.n.banco, passo["chave"]
        excluir: set[str] = set()
        ultimo = Resultado(False, erro="não rodou")
        tentativa = 0
        try:
            while tentativa < MAX_TENTATIVAS and not st.cancelado.is_set():
                tentativa += 1
                passo = banco.passo(execucao_id, chave)
                ranking, recusados = n.roteador.ranquear(passo, raiz is not None, excluir)
                escolha = n.roteador.escolher(passo, raiz is not None, excluir)
                if tentativa == 1:
                    banco.mudar_passo(execucao_id, chave, ranking=[{"agente": e.agente, "nota": e.nota, "motivo": e.motivo}
                                                                   for e in ranking[:6]] +
                                      [{"agente": a, "nota": None, "motivo": m} for a, m in list(recusados.items())[:6]])
                if escolha is None:
                    motivos = "; ".join(f"{a}: {m}" for a, m in list(recusados.items())[:6])
                    ultimo = Resultado(False, erro=f"nenhum agente disponível para {passo['categoria']} ({motivos})")
                    break
                agente = n.agentes[escolha.agente]
                exe = banco.execucao(execucao_id)
                cobra = agente.custo_modo == "por_token" or agente.disponibilidade().metodo == "chave"
                pode, motivo, resta = n.custos.pode_gastar(exe, cobra)
                if not pode:
                    st.parada = f"limite de custo: {motivo}"
                    banco.mudar_passo(execucao_id, chave, status="bloqueado", fim=agora(), erro=st.parada)
                    self._log(execucao_id, chave, agente.id, "erro", st.parada)
                    return
                deps = [banco.passo(execucao_id, d) for d in passo["depende"]]
                extra = ""
                if passo["categoria"] == "revisao" and raiz is not None:
                    extra = n.contexto.diff_para_revisao(str(raiz), exe["checkpoint"])
                prompt, papel = n.contexto.montar(exe, passo, agente, deps, extra)
                antes = seguranca.arvore_atual(raiz) if raiz is not None and st.git else None
                retomar = None
                if passo.get("retomar_de") and agente.suporta_retomar:
                    origem = banco.passo(execucao_id, passo["retomar_de"])
                    if origem and origem.get("agente") == agente.id and origem.get("sessao"):
                        retomar = origem["sessao"]
                trabalho = Trabalho(prompt, cwd=raiz, escrita=passo["escrita"], categoria=passo["categoria"],
                                    retomar=retomar, limite_s=n.limite_passo_s, limite_usd=resta, sistema=papel,
                                    limite_mem_mb=n.memoria_max_mb)
                controle = Controle(lambda tipo, texto, dados, a=agente.id: self._log(execucao_id, chave, a, tipo, texto))
                st.controles[chave] = controle
                banco.mudar_passo(execucao_id, chave, status="executando", agente=agente.id, motivo=escolha.motivo,
                                  tentativas=tentativa, inicio=agora(), erro="", arvore_antes=antes)
                self._log(execucao_id, chave, agente.id, "sistema",
                          f"tentativa {tentativa} com {agente.nome}: {escolha.motivo}" + (" · retomando a sessão" if retomar else ""))
                n.monitor.comecou(execucao_id, chave, agente.id, passo["titulo"], controle)
                agente.rodando += 1
                try:
                    res = agente.executar(trabalho, controle)
                except Exception as e:
                    res = Resultado(False, erro=f"{type(e).__name__}: {e}")
                finally:
                    agente.rodando -= 1
                    st.controles.pop(chave, None)
                    n.monitor.terminou(execucao_id, chave)
                ultimo = res
                mudou = self._mudancas(raiz, antes)
                if mudou and not passo["escrita"]:
                    seguranca.restaurar(raiz, antes)
                    self._log(execucao_id, chave, agente.id, "alerta", "este passo era só de leitura e mexeu em "
                              f"{len(mudou)} arquivo(s): desfiz ({', '.join(p for _, p in mudou[:5])})")
                    mudou = []
                n.custos.registrar(agente.id, execucao_id, res.custo_usd, res.cobrado, res.tokens_entrada, res.tokens_saida)
                if not res.cancelado:
                    banco.registrar_desempenho(agente.id, passo["categoria"], res.ok, res.duracao_s, res.custo_usd)
                campos = dict(sessao=res.sessao, custo_usd=res.custo_usd, cobrado_usd=res.custo_usd if res.cobrado else 0,
                              tokens_entrada=res.tokens_entrada, tokens_saida=res.tokens_saida, dados=res.dados)
                if res.ok:
                    arquivos = [p for _, p in mudou] or [self._relativo(a, raiz) for a in res.arquivos]
                    banco.mudar_passo(execucao_id, chave, status="concluido", fim=agora(), erro="", arquivos=arquivos,
                                      resultado=n.redator(res.texto)[:20000], **campos)
                    if passo["escrita"]:
                        self._conferir_conflitos(execucao_id, chave, st, set(arquivos))
                    self._log(execucao_id, chave, agente.id, "sistema", f"concluído em {res.duracao_s:.0f}s"
                              + (f" · {len(arquivos)} arquivo(s) alterado(s)" if arquivos else ""))
                    if passo["categoria"] == "testes":
                        self._talvez_reparar(execucao_id, chave, res)
                    return
                if (res.dados or {}).get("sem_testes"):
                    banco.mudar_passo(execucao_id, chave, status="concluido", fim=agora(), erro="",
                                      resultado=f"Sem testes para rodar: {res.erro}", **campos)
                    self._log(execucao_id, chave, agente.id, "aviso", f"sem testes: {res.erro}")
                    return
                banco.mudar_passo(execucao_id, chave, **campos)
                if res.cancelado or st.cancelado.is_set():
                    banco.mudar_passo(execucao_id, chave, status="cancelado", fim=agora(), erro=res.erro or "cancelado")
                    return
                self._log(execucao_id, chave, agente.id, "erro", f"{agente.nome} falhou: {res.erro}")
                if mudou and passo["escrita"] and antes:
                    seguranca.restaurar(raiz, antes)
                    self._log(execucao_id, chave, agente.id, "sistema", "desfiz as mudanças parciais desta tentativa")
                if res.transitorio and tentativa < MAX_TENTATIVAS:
                    espera = ESPERAS_TRANSITORIO_S[min(tentativa - 1, len(ESPERAS_TRANSITORIO_S) - 1)]
                    self._log(execucao_id, chave, agente.id, "sistema", f"erro passageiro: tento de novo em {espera}s")
                    if st.cancelado.wait(espera):
                        break
                    continue
                excluir.add(agente.id)
                if tentativa < MAX_TENTATIVAS:
                    self._log(execucao_id, chave, agente.id, "sistema", "vou tentar com outro agente")
            estado = "cancelado" if st.cancelado.is_set() else "falhou"
            banco.mudar_passo(execucao_id, chave, status=estado, fim=agora(), erro=ultimo.erro or estado)
        except Exception as e:
            traceback.print_exc()
            banco.mudar_passo(execucao_id, chave, status="falhou", fim=agora(), erro=f"{type(e).__name__}: {e}")
        finally:
            if passo.get("escrita") and raiz is not None:
                self.travas.soltar(str(raiz), (execucao_id, chave))
            st.evento.set()
            n.monitor.mudou(execucao_id, chave)

    @staticmethod
    def _relativo(caminho: str, raiz: Path | None) -> str:
        if raiz is None:
            return caminho
        try:
            return str(Path(caminho).resolve().relative_to(raiz.resolve())).replace("\\", "/")
        except (ValueError, OSError):
            return caminho

    @staticmethod
    def _mudancas(raiz: Path | None, antes: str | None) -> list[tuple[str, str]]:
        if raiz is None or not antes:
            return []
        try:
            return seguranca.mudancas(raiz, antes, seguranca.arvore_atual(raiz))
        except seguranca.ErroGit:
            return []

    def _conferir_conflitos(self, execucao_id: int, chave: str, st: EstadoExecucao, arquivos: set):
        """Dois passos de escrita que rodaram ao mesmo tempo e mexeram no mesmo arquivo = conflito no relatório."""
        p = self.n.banco.passo(execucao_id, chave)
        inicio, fim = time.time() - 1e9, time.time()
        try:
            from datetime import datetime
            inicio = datetime.fromisoformat(p["inicio"]).timestamp()
        except (TypeError, ValueError):
            pass
        conflitos = []
        for outro, (ini2, fim2, arqs2) in st.escritas.items():
            if ini2 < fim and inicio < fim2 and arquivos & arqs2:
                conflitos.append({"com": outro, "arquivos": sorted(arquivos & arqs2)[:20]})
                anteriores = self.n.banco.passo(execucao_id, outro)["conflitos"]
                self.n.banco.mudar_passo(execucao_id, outro, conflitos=anteriores + [
                    {"com": chave, "arquivos": sorted(arquivos & arqs2)[:20]}])
        st.escritas[chave] = (inicio, fim, arquivos)
        if conflitos:
            self.n.banco.mudar_passo(execucao_id, chave, conflitos=conflitos)
            self._log(execucao_id, chave, None, "alerta", f"conflito: o mesmo arquivo foi alterado por passos paralelos "
                                                            f"({conflitos})")

    # ------------------------------------------------------------ recuperação pelos testes
    def _talvez_reparar(self, execucao_id: int, chave: str, res: Resultado):
        n, banco = self.n, self.n.banco
        dados = res.dados or {}
        if dados.get("verde") or dados.get("sem_testes"):
            return
        passos = banco.passos(execucao_id)
        por_chave = {p["chave"]: p for p in passos}
        feitos = sum(1 for p in passos if p["chave"].startswith("reparo_"))
        if feitos >= n.reparos_de_testes:
            return
        escritor = self._escritor_ancestral(por_chave, chave)
        if escritor is None:
            return                                   # testes de antes das mudanças: só informam
        k = feitos + 1
        falhas = ", ".join(dados.get("falhas", [])[:15]) or dados.get("resumo") or "veja a saída"
        reparo = {"id": f"reparo_{k}", "titulo": f"Corrigir os testes que falharam ({k}ª tentativa)",
                  "categoria": escritor["categoria"], "escrita": True, "depende_de": [chave], "agente": escritor["agente"],
                  "retomar_de": escritor["chave"],
                  "instrucoes": ("Depois das suas mudanças, estes testes falham: " + falhas + ". Ache a causa raiz e "
                                 "corrija (não mude o teste para passar, a menos que o teste esteja errado). Saída:\n"
                                 + res.texto[-3000:])}
        banco.criar_passo(execucao_id, reparo)
        banco.criar_passo(execucao_id, {"id": f"testes_reparo_{k}", "titulo": f"Rodar os testes de novo ({k}ª correção)",
                                        "categoria": "testes", "depende_de": [reparo["id"]], "agente": "testes",
                                        "instrucoes": "Rode os testes."})
        for p in passos:
            if p["status"] == "pendente" and chave in p["depende"]:
                banco.mudar_passo(execucao_id, p["chave"], depende=p["depende"] + [f"testes_reparo_{k}"])
        self._log(execucao_id, chave, None, "sistema", f"recuperação: os testes falharam ({falhas[:200]}); pedi a "
                                                       f"{escritor['agente']} para corrigir e vou rodar de novo")

    @staticmethod
    def _escritor_ancestral(por_chave: dict, chave: str) -> dict | None:
        fila, vistos = list(por_chave[chave]["depende"]), set()
        while fila:
            c = fila.pop(0)
            if c in vistos or c not in por_chave:
                continue
            vistos.add(c)
            p = por_chave[c]
            if p["escrita"] and p["status"] == "concluido":
                return p
            fila += p["depende"]
        return None
