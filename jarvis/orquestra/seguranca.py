"""Security Manager do AI Command Center.

- Pastas: agente só trabalha em projeto que você autorizou (uma vez). Nunca na raiz do disco, na pasta do usuário, em
  pastas do Windows nem no HD E: (mesmas travas do `programar`).
- Segredos: `Redator` tira chaves e tokens de tudo que vai para log, HUD, banco ou contexto de outro modelo; o ambiente
  dos processos sai limpo (sem *_API_KEY, *_TOKEN...): cada CLI usa o próprio login, e a chave de um provedor nunca
  chega a outro.
- Comandos: `classificar_comando` aponta o que é destrutivo (rm -rf, git push --force, format...) ou sai para fora
  (push, publish): o terminal integrado pede confirmação antes.
- Rollback: antes de um agente escrever, `checkpoint` grava um commit-fantasma com TODO o estado da pasta (inclusive
  arquivos novos) em refs/jarvis/checkpoints/*, usando um índice temporário: o seu índice, o seu branch e o seu
  histórico não mudam. `restaurar` volta os arquivos exatamente como estavam.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jarvis.tools.base import ErroFerramenta

PROIBIDAS_PASTAS = ("c:\\windows", "c:\\program files", "c:\\program files (x86)", "c:\\programdata", "/etc", "/usr",
                    "/bin", "/sbin", "/boot", "/sys", "/proc")
LIMITE_ARQUIVO_NOVO_MB = 50          # arquivo novo maior que isso fica fora do checkpoint (não incha o .git)
_SEM_JANELA = 0x08000000 if sys.platform == "win32" else 0


# ------------------------------------------------------------------ pastas
def validar_pasta(pasta: Path | str) -> Path:
    """Pasta aceitável para um agente trabalhar (não diz se está autorizada)."""
    try:
        pasta = Path(pasta).expanduser().resolve()
    except OSError:
        raise ErroFerramenta(f"não consegui abrir a pasta {pasta}")
    texto = str(pasta).lower().replace("/", "\\") if sys.platform == "win32" else str(pasta).lower()
    if not pasta.is_dir():
        raise ErroFerramenta(f"a pasta {pasta} não existe")
    if pasta.parent == pasta or pasta == Path.home().resolve() or texto.startswith(PROIBIDAS_PASTAS):
        raise ErroFerramenta("não trabalho na raiz do disco, na pasta do usuário nem em pastas do sistema")
    if texto.startswith("e:"):
        raise ErroFerramenta("esse projeto está no HD E:, que está falhando; copie o projeto para o C: ou o D: antes")
    return pasta


def dentro_de(caminho: Path | str, raiz: Path | str) -> bool:
    try:
        Path(caminho).resolve().relative_to(Path(raiz).resolve())
        return True
    except (ValueError, OSError):
        return False


# ------------------------------------------------------------------ segredos
PADROES_SEGREDO = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),                       # Anthropic
    re.compile(r"sk-(?:proj-|or-v1-|svcacct-)?[A-Za-z0-9_\-]{20,}"),  # OpenAI / OpenRouter
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),     # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),                      # Google
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),                # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                             # AWS
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{20,}"),
    re.compile(r"(?i)((?:api[_-]?key|apikey|secret|token|password|senha)\s*[=:]\s*)[\"']?[^\s\"']{8,}"),
]
_AMBIENTE_SEGREDO = re.compile(r"(?i)(API[_-]?KEY|TOKEN|SECRET|PASSW|CREDENTIAL|PRIVATE[_-]?KEY|_AUTH\b|^AUTH)")
_AMBIENTE_PROVEDOR = re.compile(r"(?i)^(ANTHROPIC_|OPENAI_|GEMINI_|GOOGLE_API|OPENROUTER_|AWS_|AZURE_OPENAI|GH_|"
                                r"GITHUB_TOKEN|HF_TOKEN|JARVIS_PONTE_|CLAUDE_CODE_OAUTH)")
# variáveis de uma sessão do Claude Code que abriu o Jarvis (id da sessão, socket, modo remoto...): o agente filho não
# pode herdar, senão ele se apresenta como a sessão do pai. Ficam só as de configuração que você mesmo define.
_AMBIENTE_SESSAO = re.compile(r"(?i)^(CLAUDE_|CCR_|SESSION_INGRESS|AI_AGENT$)")
_SESSAO_PERMITIDAS = {"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
                      "CLAUDE_CODE_USE_POWERSHELL_TOOL"}


class Redator:
    """Troca segredos por [segredo]. Além dos padrões, conhece os valores que estão no cofre do Jarvis."""

    def __init__(self, segredos: list[str] | None = None):
        self.segredos = sorted({s for s in (segredos or []) if s and len(s) >= 8}, key=len, reverse=True)

    def __call__(self, texto: str) -> str:
        if not texto:
            return texto
        for s in self.segredos:
            if s in texto:
                texto = texto.replace(s, "[segredo]")
        for p in PADROES_SEGREDO:
            if p.groups:
                texto = p.sub(lambda m: (m.group(1) + "[segredo]") if m.lastindex else "[segredo]", texto)
            else:
                texto = p.sub("[segredo]", texto)
        return texto


def redator_do_cofre(cofre) -> Redator:
    """Redator que também conhece cada valor guardado no cofre (chaves de API, tokens)."""
    valores = []
    try:
        for item in cofre._ler_tudo().values():
            valores += [str(v) for v in item.values() if isinstance(v, str)]
    except Exception:
        pass
    return Redator(valores)


def ambiente_limpo(extra: dict | None = None, base: dict | None = None) -> dict:
    """Cópia do ambiente sem chaves e tokens. `extra` entra por cima (ex.: a chave do próprio provedor)."""
    base = dict(os.environ if base is None else base)
    env = {k: v for k, v in base.items() if not (_AMBIENTE_SEGREDO.search(k) or _AMBIENTE_PROVEDOR.search(k) or
                                                 (_AMBIENTE_SESSAO.search(k) and k.upper() not in _SESSAO_PERMITIDAS))}
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1",
                "FORCE_COLOR": "0",
                "DISABLE_TELEMETRY": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    env.pop("CLAUDECODE", None)         # o Jarvis não é uma sessão do Claude Code: o filho não pode achar que é
    env.update(extra or {})
    return env


# ------------------------------------------------------------------ comandos
_DESTRUTIVOS = [
    (r"(?i)\brm\b[^\n|;&]*\s(?:-\w*r\w*|--recursive)\b", "apaga pastas inteiras"),
    (r"\b(?:del|erase)\b[^\n]*\s/[sSqQ]\b", "apaga arquivos em massa"),
    (r"\b(?:rd|rmdir)\b[^\n]*\s/[sS]\b", "apaga pastas inteiras"),
    (r"(?i)\b(?:remove-item|ri|rm|del)\b[^\n|;]*-(?:recurse|r\b|force)", "apaga pastas inteiras"),
    (r"(?i)\bformat(?:-volume)?\b\s+[a-z]:", "formata disco"),
    (r"(?i)\b(?:diskpart|mkfs\S*|fdisk|cipher\s+/w|vssadmin\s+delete|bcdedit)\b", "mexe no disco ou na inicialização"),
    (r"\bdd\s+if=", "grava direto em disco"),
    (r"(?i)\bgit\s+(?:push\b[^\n]*(?:--force|-f\b|--mirror|--delete)|reset\s+--hard|clean\s+-\S*[fdx]|"
     r"filter-branch|reflog\s+expire|gc\s+--prune|branch\s+-D|stash\s+(?:drop|clear)|checkout\s+--\s|restore\s)",
     "descarta trabalho do git"),
    (r"(?i)\b(?:shutdown|restart-computer|stop-computer|logoff)\b", "desliga ou reinicia o PC"),
    (r"(?i)\b(?:reg\s+delete|remove-itemproperty|set-executionpolicy)\b", "mexe no registro ou nas políticas"),
    (r"(?i)\b(?:taskkill|stop-process|kill(?:all)?)\b", "encerra processos"),
    (r"(?i)\b(?:drop\s+(?:table|database|schema)|truncate\s+table)\b", "apaga dados de banco"),
    (r"(?i)\b(?:chmod|chown)\s+-R\b", "muda permissões de pastas inteiras"),
    (r"(?i)(?:curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod)\b[^\n|]*\|\s*(?:sh|bash|iex|invoke-expression|pwsh|"
     r"powershell)\b", "baixa e executa código da internet"),
    (r"(?i)\bpip\s+uninstall\b|\bnpm\s+(?:uninstall|rm)\s+-g\b", "desinstala pacotes"),
]
_EXTERNOS = [
    (r"(?i)\bgit\s+push\b", "envia para o repositório remoto"),
    (r"(?i)\b(?:npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b", "publica um pacote"),
    (r"(?i)\b(?:scp|sftp|rsync)\b[^\n]*:", "copia para outra máquina"),
    (r"(?i)\b(?:curl|wget|invoke-webrequest|invoke-restmethod)\b[^\n]*(?:-X\s*(?:POST|PUT|DELETE|PATCH)|--data|-d\s|"
     r"-Method\s+(?:Post|Put|Delete|Patch))", "envia dados para a internet"),
    (r"(?i)\b(?:npm|pnpm|yarn)\s+(?:install|i|add)\b[^\n]*\s(?:-g|--global)\b|\byarn\s+global\s+add\b|"
     r"\b(?:winget|choco|scoop)\s+install\b|\binstall-module\b", "instala um programa no computador"),
]
_DESTRUTIVOS_RE = [(re.compile(p), m) for p, m in _DESTRUTIVOS]
_EXTERNOS_RE = [(re.compile(p), m) for p, m in _EXTERNOS]


def classificar_comando(comando: str) -> tuple[str, str]:
    """('destrutivo'|'externo'|'normal', motivo). Lista de bloqueio: ajuda a pessoa, não é uma caixa de areia."""
    c = comando.strip()
    for regex, motivo in _DESTRUTIVOS_RE:
        if regex.search(c):
            return "destrutivo", motivo
    for regex, motivo in _EXTERNOS_RE:
        if regex.search(c):
            return "externo", motivo
    return "normal", ""


# ------------------------------------------------------------------ git: checkpoint e rollback
class ErroGit(Exception):
    pass


def _git() -> str | None:
    return shutil.which("git")


def _rodar_git(raiz: Path, *args: str, env: dict | None = None, entrada: str | None = None, timeout: float = 120) -> str:
    exe = _git()
    if exe is None:
        raise ErroGit("git não está instalado")
    ambiente = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **(env or {})}
    r = subprocess.run([exe, "-C", str(raiz), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ambiente, input=entrada, timeout=timeout, creationflags=_SEM_JANELA)
    if r.returncode != 0:
        raise ErroGit((r.stderr or r.stdout).strip()[:400] or f"git {args[0]} falhou")
    return r.stdout


def eh_git(raiz: Path) -> bool:
    if _git() is None:
        return False
    try:
        dentro = _rodar_git(raiz, "rev-parse", "--is-inside-work-tree").strip() == "true"
        topo = Path(_rodar_git(raiz, "rev-parse", "--show-toplevel").strip()).resolve()
        return dentro and topo == Path(raiz).resolve()      # só a raiz do repositório (não uma subpasta)
    except (ErroGit, OSError, subprocess.SubprocessError):
        return False


def _excluir_grandes(raiz: Path) -> list[str]:
    """Pathspecs que deixam de fora arquivos novos (não rastreados) muito grandes."""
    saida = _rodar_git(raiz, "ls-files", "--others", "--exclude-standard", "-z")
    fora = []
    for rel in filter(None, saida.split("\0")):
        try:
            if (raiz / rel).stat().st_size > LIMITE_ARQUIVO_NOVO_MB * 2**20:
                fora.append(f":(exclude,literal){rel}")
        except OSError:
            continue
    return fora[:200]


def arvore_atual(raiz: Path) -> str:
    """Hash da árvore git com o estado ATUAL da pasta (rastreados + novos não ignorados), sem tocar no seu índice."""
    raiz = Path(raiz)
    indice_real = Path(_rodar_git(raiz, "rev-parse", "--git-path", "index").strip())
    if not indice_real.is_absolute():
        indice_real = raiz / indice_real
    with tempfile.TemporaryDirectory(prefix="jarvis_idx_") as tmp:
        indice = Path(tmp) / "index"
        if indice_real.exists():
            shutil.copyfile(indice_real, indice)      # começa do índice real: só os arquivos mudados são relidos
        env = {"GIT_INDEX_FILE": str(indice)}
        _rodar_git(raiz, "add", "-A", "--", ".", *_excluir_grandes(raiz), env=env, timeout=600)
        return _rodar_git(raiz, "write-tree", env=env).strip()


def checkpoint(raiz: Path, rotulo: str) -> str:
    """Commit-fantasma com o estado atual. Devolve o hash (guardado em refs/jarvis/checkpoints/<rotulo>)."""
    raiz = Path(raiz)
    arvore = arvore_atual(raiz)
    pais = []
    try:
        pais = ["-p", _rodar_git(raiz, "rev-parse", "--verify", "HEAD").strip()]
    except ErroGit:
        pass                                          # repositório sem nenhum commit ainda
    ident = {"GIT_AUTHOR_NAME": "Jarvis", "GIT_AUTHOR_EMAIL": "jarvis@localhost", "GIT_COMMITTER_NAME": "Jarvis",
             "GIT_COMMITTER_EMAIL": "jarvis@localhost"}
    commit = _rodar_git(raiz, "commit-tree", arvore, *pais, "-m", f"Jarvis: checkpoint antes de {rotulo}",
                        env=ident).strip()
    _rodar_git(raiz, "update-ref", f"refs/jarvis/checkpoints/{re.sub(r'[^A-Za-z0-9._-]', '-', rotulo)}", commit)
    return commit


_ARTEFATO = re.compile(r"(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.coverage|htmlcov|"
                      r"node_modules/\.cache)(/|$)|\.py[co]$")


def artefato(caminho: str) -> bool:
    """Arquivo gerado ao rodar (cache do Python, do pytest...): não conta como alteração de um agente."""
    return bool(_ARTEFATO.search(caminho.replace("\\", "/")))


def mudancas(raiz: Path, antes: str, depois: str, com_artefatos: bool = False) -> list[tuple[str, str]]:
    """[(A|M|D|R..., caminho)] entre duas árvores/commits (sem os caches gerados, a não ser que peça)."""
    if not antes or not depois or antes == depois:
        return []
    saida = _rodar_git(raiz, "diff-tree", "-r", "--no-renames", "--name-status", "-z", antes, depois)
    partes = [p for p in saida.split("\0") if p]
    pares = [(partes[i], partes[i + 1]) for i in range(0, len(partes) - 1, 2)]
    return pares if com_artefatos else [(s, c) for s, c in pares if not artefato(c)]


def resumo_diff(raiz: Path, antes: str, depois: str) -> str:
    if not antes or not depois or antes == depois:
        return ""
    return _rodar_git(raiz, "diff-tree", "-r", "--stat", "--no-renames", antes, depois).strip()[-3000:]


def a_desfazer(raiz: Path, commit: str) -> dict:
    """O que `restaurar` faria: {'voltam': [...], 'somem': [...]}."""
    agora_ = arvore_atual(raiz)
    m = mudancas(raiz, commit, agora_, com_artefatos=True)
    return {"voltam": [p for s, p in m if s in ("M", "D", "T")], "somem": [p for s, p in m if s == "A"]}


def restaurar(raiz: Path, commit: str) -> dict:
    """Volta a pasta ao estado do checkpoint: restaura o que mudou ou sumiu e apaga o que apareceu depois."""
    raiz = Path(raiz)
    plano = a_desfazer(raiz, commit)
    with tempfile.TemporaryDirectory(prefix="jarvis_idx_") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _rodar_git(raiz, "read-tree", commit, env=env)
        if plano["voltam"]:
            _rodar_git(raiz, "checkout-index", "-f", "-z", "--stdin", env=env, entrada="\0".join(plano["voltam"]))
    apagados = []
    for rel in plano["somem"]:
        alvo = (raiz / rel)
        if dentro_de(alvo, raiz) and alvo.is_file():
            alvo.unlink()
            apagados.append(rel)
    return {"restaurados": plano["voltam"], "apagados": apagados}


def patch(raiz: Path, antes: str, depois: str, max_caracteres: int = 12000) -> str:
    """O diff (unificado) entre duas árvores, cortado: o revisor vê o que mudou de verdade."""
    if not antes or not depois or antes == depois:
        return ""
    texto = _rodar_git(raiz, "diff-tree", "-r", "-p", "--no-renames", "--no-color", antes, depois)
    return texto if len(texto) <= max_caracteres else texto[:max_caracteres] + "\n[... diff cortado ...]"
