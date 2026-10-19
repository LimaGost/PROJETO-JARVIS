"""CLI Gateway: roda as ferramentas instaladas (claude, codex, gemini, testes, terminal) como processos CONTROLADOS.

Nada roda no navegador: a HUD só pede, quem executa é este módulo, no backend.
  - ambiente limpo (sem chaves de outros provedores) e pasta de trabalho conferida por quem chama;
  - entrada pelo stdin (o pedido não vira argumento de linha de comando: sem limite de tamanho e sem injeção);
  - limite de tempo (sem contar o tempo pausado) e de memória (soma da árvore de processos), prioridade baixa (a voz
    do Jarvis não engasga), sem janela no Windows;
  - cancelar e pausar/retomar a ÁRVORE inteira (o claude/codex abrem subprocessos);
  - no Windows, atalhos .cmd do npm viram `node arquivo.js` (sem passar pelo cmd.exe e seus problemas de aspas).
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import psutil

WINDOWS = sys.platform == "win32"
_FLAGS_WIN = (0x08000000 | 0x00000200 | 0x00004000) if WINDOWS else 0   # sem janela | grupo próprio | prioridade baixa
_META_CMD = re.compile(r'[&|<>^%!"\r\n]')

_ativos: dict[int, "Processo"] = {}
_lock_ativos = threading.Lock()


def resolver_executavel(nome: str, override: list[str] | str | None = None) -> list[str] | None:
    """Linha de comando base para uma CLI. `override` (config) permite ex.: ["wsl", "-e", "codex"]."""
    if override:
        partes = [override] if isinstance(override, str) else list(override)
        primeiro = shutil.which(partes[0]) or (partes[0] if Path(partes[0]).exists() else None)
        return [primeiro, *partes[1:]] if primeiro else None
    caminho = shutil.which(nome)
    if caminho is None:
        return None
    if WINDOWS and caminho.lower().endswith((".cmd", ".bat")):
        js = _js_do_atalho_npm(Path(caminho))
        if js is not None:
            node = Path(caminho).with_name("node.exe")
            node_exe = str(node) if node.exists() else shutil.which("node")
            if node_exe:
                return [node_exe, str(js)]
    return [caminho]


def _js_do_atalho_npm(atalho: Path) -> Path | None:
    """O .cmd que o npm cria chama `node "%dp0%\\node_modules\\pacote\\bin\\x.js" %*`: acha esse .js."""
    try:
        texto = atalho.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'%~?dp0%?\\?([^"%\r\n]+?\.(?:js|cjs|mjs))"', texto)
    if not m:
        return None
    js = (atalho.parent / m.group(1).lstrip("\\")).resolve()
    return js if js.exists() else None


class Processo:
    """Um processo de agente/terminal sob controle do Jarvis."""

    def __init__(self, argv: list[str], cwd: Path | str | None, env: dict | None = None, entrada: str | None = None,
                 limite_s: float = 1200, limite_mem_mb: float = 4096, ao_linha: Callable[[str, str], None] | None = None,
                 nome: str = "", prioridade_baixa: bool = True, interativo: bool = False):
        if not argv or not argv[0]:
            raise ValueError("comando vazio")
        if WINDOWS and argv[0].lower().endswith((".cmd", ".bat")):
            ruins = [a for a in argv[1:] if _META_CMD.search(a)]
            if ruins:   # via cmd.exe esses caracteres viram comandos: recusa em vez de arriscar
                raise ValueError("argumento com caractere especial para um atalho .cmd; configure o comando da CLI")
        self.argv, self.cwd, self.env, self.entrada = argv, str(cwd) if cwd else None, env, entrada
        self.limite_s, self.limite_mem_mb, self.ao_linha = limite_s, limite_mem_mb, ao_linha
        self.nome, self.prioridade_baixa = nome or Path(argv[0]).stem, prioridade_baixa
        self.interativo = interativo          # stdin fica aberto (servidores MCP): use escrever()
        self._lock_stdin = threading.Lock()
        self.estado = "criado"
        self.motivo_fim = ""
        self.codigo: int | None = None
        self.inicio = 0.0
        self.fim = 0.0
        self.pico_mem_mb = 0.0
        self._pausado_desde: float | None = None
        self._pausado_total = 0.0
        self._proc: subprocess.Popen | None = None
        self._threads: list[threading.Thread] = []
        self._terminou = threading.Event()

    # ------------------------------------------------------------ ciclo de vida
    def iniciar(self) -> "Processo":
        kwargs = dict(cwd=self.cwd, env=self.env,
                      stdin=subprocess.PIPE if self.entrada is not None or self.interativo else subprocess.DEVNULL,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                      bufsize=1)
        if WINDOWS:
            kwargs["creationflags"] = _FLAGS_WIN
        else:
            kwargs["start_new_session"] = True
        try:
            self._proc = subprocess.Popen(self.argv, **kwargs)
        except OSError as e:
            self.estado, self.motivo_fim, self.codigo = "falhou_iniciar", f"não consegui iniciar: {e}", -1
            self._terminou.set()
            return self
        self.inicio = time.time()
        self.estado = "rodando"
        if self.prioridade_baixa and not WINDOWS:
            try:
                psutil.Process(self._proc.pid).nice(10)
            except (psutil.Error, OSError):
                pass
        with _lock_ativos:
            _ativos[self._proc.pid] = self
        if self.entrada is not None and not self.interativo:
            self._thread(self._escrever_entrada)
        self._thread(self._ler, self._proc.stdout, "stdout")
        self._thread(self._ler, self._proc.stderr, "stderr")
        self._thread(self._vigiar)
        return self

    def _thread(self, alvo, *args):
        t = threading.Thread(target=alvo, args=args, daemon=True, name=f"gw-{self.nome}")
        t.start()
        self._threads.append(t)

    def _escrever_entrada(self):
        try:
            self._proc.stdin.write(self.entrada)
            self._proc.stdin.close()
        except (OSError, ValueError):
            pass

    def escrever(self, texto: str):
        """Modo interativo: manda texto para o stdin (uma mensagem por vez)."""
        if not self.interativo or self._proc is None or self._proc.poll() is not None:
            raise OSError("o processo não está aceitando entrada")
        with self._lock_stdin:
            self._proc.stdin.write(texto)
            self._proc.stdin.flush()

    def fechar_entrada(self):
        try:
            if self._proc is not None and self._proc.stdin:
                self._proc.stdin.close()
        except (OSError, ValueError):
            pass

    def _ler(self, fluxo, nome_fluxo: str):
        try:
            for linha in fluxo:
                if self.ao_linha is not None:
                    try:
                        self.ao_linha(nome_fluxo, linha.rstrip("\r\n"))
                    except Exception:
                        pass          # quem consome a saída nunca derruba o processo
        except (OSError, ValueError):
            pass

    def _vigiar(self):
        ultimo_mem = 0.0
        while True:
            try:
                self.codigo = self._proc.wait(timeout=0.4)
                break
            except subprocess.TimeoutExpired:
                pass
            if self._pausado_desde is not None:
                continue
            if self.tempo_ativo() > self.limite_s:
                limite = f"{self.limite_s / 60:.0f} min" if self.limite_s >= 120 else f"{self.limite_s:.0f} s"
                self._encerrar_arvore("estourou_tempo", f"passou do limite de {limite} e foi encerrado")
                continue
            if time.time() - ultimo_mem > 2:
                ultimo_mem = time.time()
                mem = self.memoria_mb()
                self.pico_mem_mb = max(self.pico_mem_mb, mem)
                if mem > self.limite_mem_mb:
                    self._encerrar_arvore("estourou_memoria", f"usou {mem:.0f} MB (limite {self.limite_mem_mb:.0f})")
        for t in self._threads:
            if t is not threading.current_thread() and t.name.startswith("gw-") and t.is_alive():
                t.join(timeout=5)   # termina de ler a saída antes de dizer que acabou
        self.fim = time.time()
        if self.estado in ("rodando", "pausado"):
            self.estado = "terminou"
        with _lock_ativos:
            _ativos.pop(self._proc.pid, None)
        self._terminou.set()

    def esperar(self, timeout: float | None = None) -> int | None:
        self._terminou.wait(timeout)
        return self.codigo

    @property
    def rodando(self) -> bool:
        return self.estado in ("rodando", "pausado") and not self._terminou.is_set()

    def tempo_ativo(self) -> float:
        if not self.inicio:
            return 0.0
        fim = self.fim or time.time()
        pausa = self._pausado_total + ((time.time() - self._pausado_desde) if self._pausado_desde else 0)
        return max(0.0, fim - self.inicio - pausa)

    # ------------------------------------------------------------ controle
    def _arvore(self) -> list[psutil.Process]:
        try:
            pai = psutil.Process(self._proc.pid)
            return [pai, *pai.children(recursive=True)]
        except (psutil.Error, AttributeError):
            return []

    def memoria_mb(self) -> float:
        total = 0
        for p in self._arvore():
            try:
                total += p.memory_info().rss
            except psutil.Error:
                continue
        return total / 2**20

    def _encerrar_arvore(self, estado: str, motivo: str):
        if self._terminou.is_set() or self._proc is None:
            return
        self.estado, self.motivo_fim = estado, motivo
        arvore = self._arvore()
        for p in reversed(arvore):          # filhos primeiro
            try:
                if self._pausado_desde is not None:
                    p.resume()
                p.kill()
            except psutil.Error:
                continue
        if WINDOWS and self._proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self._proc.pid)], capture_output=True,
                           creationflags=0x08000000)
        self._pausado_desde = None

    def cancelar(self, motivo: str = "cancelado a seu pedido"):
        self._encerrar_arvore("cancelado", motivo)

    def pausar(self) -> bool:
        if self.estado != "rodando":
            return False
        for p in self._arvore():
            try:
                p.suspend()
            except psutil.Error:
                continue
        self._pausado_desde = time.time()
        self.estado = "pausado"
        return True

    def retomar(self) -> bool:
        if self.estado != "pausado":
            return False
        for p in self._arvore():
            try:
                p.resume()
            except psutil.Error:
                continue
        self._pausado_total += time.time() - (self._pausado_desde or time.time())
        self._pausado_desde = None
        self.estado = "rodando"
        return True

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def resumo(self) -> dict:
        return {"pid": self.pid, "nome": self.nome, "estado": self.estado, "pasta": self.cwd,
                "tempo_s": round(self.tempo_ativo(), 1), "memoria_mb": round(self.memoria_mb(), 1) if self.rodando else 0,
                "pico_mem_mb": round(self.pico_mem_mb, 1), "codigo": self.codigo, "motivo": self.motivo_fim}


def processos_ativos() -> list[Processo]:
    with _lock_ativos:
        return list(_ativos.values())


def rodar(argv: list[str], cwd: Path | str | None = None, env: dict | None = None, entrada: str | None = None,
          limite_s: float = 30, limite_mem_mb: float = 2048) -> tuple[int | None, str, str]:
    """Roda e espera (para coisas rápidas: --version, auth status). (código, stdout, stderr)."""
    saida, erros = [], []

    def coletar(fluxo, linha):
        (saida if fluxo == "stdout" else erros).append(linha)
    p = Processo(argv, cwd, env, entrada, limite_s, limite_mem_mb, coletar, prioridade_baixa=False).iniciar()
    p.esperar(limite_s + 15)
    return p.codigo, "\n".join(saida), "\n".join(erros)


def ambiente_padrao(extra: dict | None = None) -> dict:
    from jarvis.orquestra.seguranca import ambiente_limpo
    return ambiente_limpo(extra)


def caminho_em_args_ok(valor: str) -> bool:
    """Valor que pode ir como argumento mesmo se a CLI for um .cmd (ids de sessão, nomes de modelo)."""
    return bool(valor) and not _META_CMD.search(valor) and len(valor) < 400
