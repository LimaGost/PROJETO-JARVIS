"""Terminal integrado e gerenciador de CLIs (Central > Terminal).

Terminal: você digita, o BACKEND executa (o navegador nunca roda comando). Só em pasta autorizada (ou na do Jarvis),
com ambiente limpo, limite de tempo e memória, saída ao vivo na HUD, matar/pausar e histórico (auditoria). Comando
destrutivo ou que sai para fora (rm -rf, git push --force, format, curl | sh...) só roda depois de você confirmar.

CLIs: detecta Claude Code, Codex e Gemini (instalado? versão? compatível? logado?), mostra o comando OFICIAL de
instalação (instalar é sempre confirmado), abre o login numa janela própria e roda o `doctor` de cada uma.
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

from jarvis.config import RAIZ
from jarvis.orquestra import gateway, seguranca
from jarvis.orquestra.seguranca import ambiente_limpo, classificar_comando

MAX_LINHAS = 5000
LIMITE_S = 30 * 60
INSTALACAO = {      # comandos oficiais (rodam no terminal integrado, sempre com confirmação)
    "claude_code": "npm install -g @anthropic-ai/claude-code",
    "codex": "npm install -g @openai/codex",
    "gemini": "npm install -g @google/gemini-cli",
}


def _argv_shell(comando: str) -> list[str]:
    if sys.platform == "win32":
        exe = gateway.resolver_executavel("pwsh") or gateway.resolver_executavel("powershell")
        prefixo = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
        return [*(exe or ["powershell"]), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prefixo + comando]
    return [gateway.resolver_executavel("bash")[0] if gateway.resolver_executavel("bash") else "/bin/sh", "-c", comando]


class Sessao:
    def __init__(self, id_: int, pasta: str, comando: str):
        self.id, self.pasta, self.comando = id_, pasta, comando
        self.linhas: list[tuple[int, str, str]] = []      # (n, fluxo, texto)
        self.n = 0
        self.processo: gateway.Processo | None = None
        self.inicio = time.time()

    def resumo(self) -> dict:
        p = self.processo
        return {"id": self.id, "pasta": self.pasta, "comando": self.comando, "linhas": self.n,
                **({"estado": p.estado, "codigo": p.codigo, "tempo_s": round(p.tempo_ativo(), 1), "pid": p.pid,
                    "memoria_mb": round(p.memoria_mb(), 1) if p.rodando else 0} if p else {"estado": "criado"})}


class Terminal:
    def __init__(self, nucleo):
        self.n = nucleo
        self.sessoes: dict[int, Sessao] = {}
        self._sujas: set[int] = set()
        self._lock = threading.Lock()
        self._acordar = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ pastas
    def pastas(self) -> list[dict]:
        saida = [{"caminho": str(RAIZ), "nome": f"{RAIZ.name} (o Jarvis)"}]
        saida += [{"caminho": p["caminho"], "nome": p["nome"]} for p in self.n.banco.projetos()]
        return saida

    def _pasta_ok(self, pasta: str) -> Path:
        p = seguranca.validar_pasta(pasta)
        if p == RAIZ.resolve() or self.n.autorizado(p):
            return p
        raise ValueError(f"a pasta {p.name} não está autorizada; autorize o projeto antes (Central > Orquestração)")

    # ------------------------------------------------------------ executar
    def executar(self, pasta: str, comando: str, confirmado: bool = False) -> dict:
        comando = (comando or "").strip()
        if not comando:
            raise ValueError("digite um comando")
        if len(comando) > 4000:
            raise ValueError("comando grande demais")
        raiz = self._pasta_ok(pasta)
        risco, motivo = classificar_comando(comando)
        if risco != "normal" and not confirmado:
            return {"precisa_confirmar": True, "risco": risco, "motivo": motivo, "comando": comando}
        id_ = self.n.banco.registrar_comando(str(raiz), comando, risco, confirmado)
        self.n.monitor.log(None, None, "terminal", "comando", f"[{raiz.name}] {comando}" +
                           (f"  (confirmado: {motivo})" if risco != "normal" else ""))
        s = Sessao(id_, str(raiz), comando)
        s.processo = gateway.Processo(_argv_shell(comando), raiz, ambiente_limpo(), None, LIMITE_S,
                                      self.n.memoria_max_mb, lambda f, l, s=s: self._linha(s, f, l), nome="terminal",
                                      prioridade_baixa=False)
        with self._lock:
            self.sessoes[id_] = s
            for velho in sorted(self.sessoes)[:-30]:           # guarda as 30 últimas
                if not (self.sessoes[velho].processo and self.sessoes[velho].processo.rodando):
                    self.sessoes.pop(velho, None)
        s.processo.iniciar()
        threading.Thread(target=self._esperar, args=(s,), daemon=True, name=f"term-{id_}").start()
        self._emitir_estado()
        return {"id": id_, "pid": s.processo.pid, "risco": risco}

    def _linha(self, s: Sessao, fluxo: str, linha: str):
        linha = self.n.redator(linha)
        with self._lock:
            s.n += 1
            s.linhas.append((s.n, fluxo, linha[:2000]))
            if len(s.linhas) > MAX_LINHAS:
                s.linhas = s.linhas[-MAX_LINHAS:]
            self._sujas.add(s.id)
        self._garantir_thread()
        self._acordar.set()

    def _esperar(self, s: Sessao):
        s.processo.esperar(LIMITE_S + 60)
        cauda = "\n".join(t for _, _, t in s.linhas[-80:])
        self.n.banco.fechar_comando(s.id, s.processo.codigo, round(s.processo.tempo_ativo(), 1), cauda)
        self._emitir_estado()

    def _garantir_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._despachar, daemon=True, name="term-saida")
            self._thread.start()

    def _despachar(self):
        enviados: dict[int, int] = {}
        while True:
            self._acordar.wait(5)
            self._acordar.clear()
            time.sleep(0.3)
            with self._lock:
                sujas, self._sujas = self._sujas, set()
                lotes = {i: [l for l in self.sessoes[i].linhas if l[0] > enviados.get(i, 0)][:50]
                         for i in sujas if i in self.sessoes}
            for i, lote in lotes.items():
                if lote:
                    enviados[i] = lote[-1][0]
                    self.n.monitor.emitir("orq", acao="terminal", sessao=i,
                                          linhas=[{"n": n, "fluxo": f, "texto": t[:600]} for n, f, t in lote])
                    if len(lote) == 50:           # o bus corta listas em 50: manda o resto no próximo lote
                        with self._lock:
                            self._sujas.add(i)
                        self._acordar.set()

    def _emitir_estado(self):
        self.n.monitor.emitir("orq", acao="terminal_estado")

    def saida(self, id_: int, depois_de: int = 0) -> dict:
        s = self.sessoes.get(int(id_))
        if s is None:
            raise ValueError("essa sessão do terminal não existe mais")
        with self._lock:
            linhas = [{"n": n, "fluxo": f, "texto": t} for n, f, t in s.linhas if n > depois_de][-1000:]
        return {**s.resumo(), "saida": linhas}

    def matar(self, id_: int) -> bool:
        s = self.sessoes.get(int(id_))
        if s is None or s.processo is None or not s.processo.rodando:
            return False
        s.processo.cancelar("encerrado por você")
        return True

    def pausar(self, id_: int) -> bool:
        s = self.sessoes.get(int(id_))
        return bool(s and s.processo and s.processo.pausar())

    def retomar(self, id_: int) -> bool:
        s = self.sessoes.get(int(id_))
        return bool(s and s.processo and s.processo.retomar())

    def estado(self) -> dict:
        processos = [p.resumo() for p in gateway.processos_ativos()]
        return {"sessoes": [s.resumo() for s in sorted(self.sessoes.values(), key=lambda s: -s.id)],
                "processos": processos, "pastas": self.pastas(),
                "historico": self.n.banco.historico_terminal(30)}

    # ------------------------------------------------------------ gerenciador de CLIs
    def clis(self, forcar: bool = False) -> list[dict]:
        saida = []
        for a in self.n.agentes.values():
            if a.tipo != "cli":
                continue
            if forcar:
                a.esquecer_cache()
            c = a.cartao()
            c["instalar_cmd"] = INSTALACAO.get(a.id, "")
            c["tem_doctor"] = a.id in ("claude_code", "codex")
            saida.append(c)
        return saida

    def login(self, agente_id: str) -> str:
        a = self.n.agentes.get(agente_id)
        if a is None or not a.login_cmd:
            raise ValueError("essa CLI não está instalada (ou não tem login)")
        if sys.platform != "win32":
            return "Rode no seu terminal: " + " ".join(a.login_cmd)
        subprocess.Popen(a.login_cmd, creationflags=0x00000010, cwd=str(RAIZ), env=ambiente_limpo())  # janela própria
        a.esquecer_cache()
        return f"Abri o login do {a.nome} numa janela. Termine por lá e clique em Verificar."

    def instalar(self, agente_id: str, confirmado: bool) -> dict:
        cmd = INSTALACAO.get(agente_id)
        if not cmd:
            raise ValueError("não sei instalar essa CLI por aqui")
        if not confirmado:
            return {"precisa_confirmar": True, "risco": "externo", "comando": cmd,
                    "motivo": "instala um programa no computador (npm, global)"}
        if gateway.resolver_executavel("npm") is None:
            raise ValueError("falta o Node.js (npm). Instale em https://nodejs.org e tente de novo")
        r = self.executar(str(RAIZ), cmd, confirmado=True)
        self.n.agentes[agente_id].esquecer_cache()
        return r

    def doctor(self, agente_id: str) -> str:
        a = self.n.agentes.get(agente_id)
        cmd = a.comando() if a is not None and hasattr(a, "comando") else None
        if not cmd:
            raise ValueError("essa CLI não está instalada")
        codigo, saida, erros = gateway.rodar([*cmd, "doctor"], cwd=RAIZ, env=ambiente_limpo(), limite_s=90)
        return self.n.redator((saida + ("\n" + erros if erros else "")).strip()[-6000:] or f"(sem saída, código {codigo})")
