"""O Jarvis programa: entende qual projeto está aberto no VS Code e põe o Claude Code para trabalhar nele.

Como funciona:
  - o projeto vem do título da janela do VS Code ("arquivo - PASTA - Visual Studio Code") + a lista de pastas abertas
    que o próprio VS Code guarda; ou da pasta dita no pedido;
  - `programar` pede autorização 🔴 UMA vez por tarefa e devolve na hora ("comecei"): o trabalho roda em segundo plano,
    e quando acaba o Jarvis avisa por voz, abre no VS Code os arquivos que mudaram e o cérebro fica sabendo (avisos);
  - o programador é o `claude` (assinatura, modelo Sonnet) com ferramentas de arquivo e de comandos, preso à pasta do
    projeto, sem os seus servidores MCP (Gmail, Hostinger...) e sem `git push` (escolha do Iury em 2026-10-17);
  - pedidos seguidos no mesmo projeto continuam a mesma sessão de programação ("agora muda a cor do botão").
"""
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from jarvis.brain import avisos
from jarvis.config import RAIZ
from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, ferramenta

MODELO = "sonnet"
MAX_TURNOS = 60
LIMITE_S = 20 * 60                 # uma tarefa não roda para sempre
SESSAO_VALIDA_S = 2 * 3600         # pedido seguinte no mesmo projeto, dentro disso, continua a mesma sessão
FERRAMENTAS = "Read,Edit,Write,MultiEdit,Glob,Grep,NotebookEdit,TodoWrite,Bash,PowerShell"
PROIBIDAS = "Bash(git push:*),Bash(git reset --hard:*),Bash(rm -rf:*),Bash(shutdown:*),Bash(format:*),WebFetch,WebSearch"
INSTRUCOES = (
    "Você é o programador do Jarvis, o assistente de voz do Iury. O pedido chegou por voz e foi transcrito: interprete com "
    "bom senso. Trabalhe SÓ dentro da pasta atual do projeto. Nunca apague dados do usuário, nunca faça git push nem "
    "reescreva o histórico do git, não instale nada fora do projeto. Siga o padrão de código que já existe. Quando "
    "terminar, a sua ÚLTIMA mensagem vai ser LIDA EM VOZ ALTA: escreva 1 ou 2 frases em português, sem markdown, sem "
    "código e sem caminhos completos, dizendo o que fez (ou o que impediu)."
)
EDITORES = ("Edit", "Write", "MultiEdit", "NotebookEdit")
PROIBIDAS_PASTAS = ("c:\\windows", "c:\\program files", "c:\\program files (x86)", "c:\\programdata")

_ESTADO: dict = {"tarefa": None, "sessoes": {}}     # sessoes: pasta -> (session_id, quando)
_LOCK = threading.Lock()


# ---------------------------------------------------------------- qual projeto está aberto
def _uri_para_pasta(uri: str) -> Path | None:
    if not uri or not uri.startswith("file:///"):
        return None
    return Path(urllib.parse.unquote(uri[len("file:///"):]))


def pastas_do_vscode(arquivo: Path | None = None) -> list[Path]:
    """Pastas que o VS Code tem abertas (ou abriu por último), pela memória do próprio VS Code."""
    arquivo = arquivo or Path(os.environ.get("APPDATA", "")) / "Code" / "User" / "globalStorage" / "storage.json"
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    uris = [f.get("folderUri") for f in (dados.get("backupWorkspaces") or {}).get("folders") or []]
    estado = dados.get("windowsState") or {}
    uris += [w.get("folder") for w in [estado.get("lastActiveWindow") or {}] + list(estado.get("openedWindows") or [])]
    saida = []
    for u in uris:
        p = _uri_para_pasta(u or "")
        if p and p not in saida:
            saida.append(p)
    return saida


def titulos_do_vscode() -> list[str]:
    """Títulos das janelas do VS Code abertas, a ativa primeiro."""
    try:
        from jarvis.tools import interface
        interface._exigir()
        janelas = [j for j in interface._janelas_de_topo() if interface._processo(j.ProcessId).lower() == "code.exe"]
        ativa = interface.auto.GetForegroundControl().GetTopLevelControl()
        janelas.sort(key=lambda j: 0 if ativa is not None and j.NativeWindowHandle == ativa.NativeWindowHandle else 1)
        return [interface._limpo(j.Name) for j in janelas]
    except Exception:
        return []


def ler_titulo(titulo: str) -> tuple[str, str]:
    """'● main.py - PROJETO JARVIS - Visual Studio Code' -> ('main.py', 'PROJETO JARVIS')."""
    partes = [p.strip() for p in titulo.lstrip("● ").split(" - ")]
    if len(partes) >= 3 and partes[-1].startswith("Visual Studio Code"):
        return " - ".join(partes[:-2]), partes[-2]
    if len(partes) == 2 and partes[-1].startswith("Visual Studio Code"):
        return "", partes[0]
    return "", ""


def projeto_aberto(titulos: list[str] | None = None, pastas: list[Path] | None = None) -> tuple[Path | None, str]:
    """(pasta do projeto ativo no VS Code, arquivo em foco)."""
    titulos = titulos_do_vscode() if titulos is None else titulos
    pastas = pastas_do_vscode() if pastas is None else pastas
    for t in titulos:
        arquivo, raiz = ler_titulo(t)
        for p in pastas:
            if raiz and p.name.lower() == raiz.lower():
                return p, arquivo
    return (pastas[0] if pastas and not titulos else None), ""


def validar_pasta(pasta: Path) -> Path:
    try:
        pasta = pasta.resolve()
    except OSError:
        raise ErroFerramenta(f"não consegui abrir a pasta {pasta}")
    texto = str(pasta).lower()
    if not pasta.is_dir():
        raise ErroFerramenta(f"a pasta {pasta} não existe")
    if pasta.parent == pasta or pasta == Path.home() or texto.startswith(PROIBIDAS_PASTAS):
        raise ErroFerramenta("não programo na raiz do disco, na pasta do usuário nem em pastas do Windows")
    if texto.startswith("e:"):
        raise ErroFerramenta("esse projeto está no HD E:, que está falhando; copie o projeto para o C: ou o D: antes")
    return pasta


def _resolver_pasta(pasta: str) -> Path:
    if pasta.strip():
        p = Path(pasta.strip().strip('"'))
        if not p.is_absolute():   # nome de projeto: procura entre os que o VS Code conhece
            casados = [q for q in pastas_do_vscode() if q.name.lower() == pasta.strip().lower()]
            if not casados:
                raise ErroFerramenta(f"não achei o projeto '{pasta}'; abra-o no VS Code ou diga o caminho completo")
            p = casados[0]
        return validar_pasta(p)
    p, _ = projeto_aberto()
    if p is None:
        raise ErroFerramenta("não há projeto aberto no VS Code; abra a pasta do projeto nele ou diga qual é")
    return validar_pasta(p)


# ---------------------------------------------------------------- o programador
def comando(pedido: str, sessao: str | None) -> list[str]:
    exe = shutil.which("claude") or "claude"
    cmd = [exe, "-p", pedido, "--output-format", "stream-json", "--verbose", "--model", MODELO,
           "--permission-mode", "acceptEdits", "--allowedTools", FERRAMENTAS, "--disallowedTools", PROIBIDAS,
           "--strict-mcp-config", "--max-turns", str(MAX_TURNOS), "--append-system-prompt", INSTRUCOES]
    if sessao:
        cmd += ["--resume", sessao]
    return cmd


def _registrar_uso(ev: dict):
    u = ev.get("usage") or {}
    registro = {"quando": datetime.now().isoformat(timespec="seconds"), "modelo": MODELO, "tipo": "programacao",
                "entrada": u.get("input_tokens", 0) or 0, "cache_lido": u.get("cache_read_input_tokens", 0) or 0,
                "cache_escrito": u.get("cache_creation_input_tokens", 0) or 0, "saida": u.get("output_tokens", 0) or 0,
                "chamadas": ev.get("num_turns", 1), "custo_api_usd": round(float(ev.get("total_cost_usd") or 0), 5),
                "duracao_ms": ev.get("duration_ms", 0)}
    try:
        arquivo = RAIZ / "data" / "logs" / "uso_claude_cli.jsonl"
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        with open(arquivo, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro) + "\n")
    except OSError:
        pass


def _abrir_no_vscode(arquivos: list[str], pasta: Path):
    code = shutil.which("code")
    if not code or not arquivos:
        return
    try:
        subprocess.Popen([code, "--reuse-window", "-g", *arquivos[-5:]], cwd=str(pasta),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def executar_tarefa(tarefa: dict, rodar=subprocess.Popen):
    """Roda o `claude` na pasta e preenche `tarefa` (status, resumo, arquivos). Publica o aviso no fim."""
    pasta, pedido = tarefa["pasta"], tarefa["pedido"]
    anterior = _ESTADO["sessoes"].get(str(pasta))
    sessao = anterior[0] if anterior and time.time() - anterior[1] < SESSAO_VALIDA_S else None
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env.update({"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1", "PYTHONUTF8": "1"})
    arquivos: list[str] = []
    resultado, erro = "", None
    try:
        proc = rodar(comando(pedido, sessao), cwd=str(pasta), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        tarefa["proc"] = proc
        inicio = time.time()
        for linha in proc.stdout:
            if time.time() - inicio > LIMITE_S:
                proc.kill()
                erro = "passou de vinte minutos e eu parei"
                break
            try:
                ev = json.loads(linha)
            except ValueError:
                continue
            if ev.get("type") == "system" and ev.get("session_id"):
                _ESTADO["sessoes"][str(pasta)] = (ev["session_id"], time.time())
            elif ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use" and b.get("name") in EDITORES:
                        caminho = (b.get("input") or {}).get("file_path") or (b.get("input") or {}).get("notebook_path")
                        if caminho and caminho not in arquivos:
                            arquivos.append(caminho)
                        tarefa["progresso"] = f"mexendo em {Path(caminho).name}" if caminho else tarefa.get("progresso")
            elif ev.get("type") == "result":
                _registrar_uso(ev)
                resultado = str(ev.get("result") or "").strip()
                if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                    erro = {"error_max_turns": "a tarefa ficou grande demais para uma vez só"}.get(
                        ev.get("subtype"), resultado[:200] or str(ev.get("subtype")))
        proc.wait(timeout=10)
        if tarefa.get("cancelada"):
            erro = "cancelada a seu pedido"
    except Exception as e:
        erro = f"{type(e).__name__}: {e}"
    tarefa.update(fim=time.time(), arquivos=arquivos, status="erro" if erro else "concluida",
                  resumo=resultado if not erro else f"Não terminei a tarefa no {pasta.name}: {erro}.")
    if not erro:
        _abrir_no_vscode(arquivos, pasta)
    nomes = ", ".join(Path(a).name for a in arquivos[:6]) or "nenhum arquivo"
    falado = (f"Senhor, terminei a tarefa no {pasta.name}. {tarefa['resumo']}" if not erro else tarefa["resumo"])
    avisos.publicar(f"Tarefa de programação no projeto {pasta.name} ({'concluída' if not erro else 'com problema'}): "
                    f"{tarefa['resumo']} Arquivos alterados: {nomes}.", falado)


# ---------------------------------------------------------------- ferramentas
def _avaliar_programar(args: dict) -> Avaliacao:
    pasta = _resolver_pasta(args.get("pasta", ""))
    pedido = args.get("pedido", "").strip()
    if not pedido:
        raise ErroFerramenta("diga o que é para programar")
    extra = ", na pasta do próprio Jarvis" if pasta == RAIZ.resolve() else ""
    return Avaliacao(Nivel.CONFIRMAR, f"programar no projeto {pasta.name}{extra}: {pedido[:80]}")


@ferramenta(
    "programar",
    "Programa de verdade no projeto aberto no VS Code (ou na pasta indicada): cria e edita arquivos, roda testes e "
    "comandos. Use para qualquer pedido de código (criar, corrigir, explicar e mudar código). Roda em segundo plano: "
    "responda só que começou; o aviso de conclusão chega sozinho. Passe o pedido completo, com os detalhes que o "
    "usuário deu.",
    {"pedido": {"type": "string", "description": "O que programar, com todos os detalhes"},
     "pasta": {"type": "string", "description": "Nome ou caminho do projeto (opcional; padrão: o aberto no VS Code)"}},
    obrigatorios=("pedido",),
    avaliar=_avaliar_programar,
)
def programar(pedido: str, pasta: str = "") -> str:
    p = _resolver_pasta(pasta)
    with _LOCK:
        atual = _ESTADO["tarefa"]
        if atual and atual["status"] == "rodando":
            raise ErroFerramenta(f"ainda estou terminando a tarefa anterior no {atual['pasta'].name} "
                                 f"({atual.get('progresso') or 'trabalhando'}); peça de novo quando eu avisar")
        tarefa = {"pasta": p, "pedido": pedido, "status": "rodando", "inicio": time.time(), "progresso": "começando"}
        _ESTADO["tarefa"] = tarefa
    threading.Thread(target=executar_tarefa, args=(tarefa,), daemon=True, name="programador").start()
    return f"Comecei a tarefa no projeto {p.name}. Ela roda em segundo plano; aviso quando terminar."


@ferramenta(
    "tarefa_programacao",
    "Andamento da tarefa de programação em segundo plano (status), ou cancela (cancelar).",
    {"acao": {"type": "string", "enum": ["status", "cancelar"]}},
)
def tarefa_programacao(acao: str = "status") -> str:
    t = _ESTADO["tarefa"]
    if t is None:
        return "Nenhuma tarefa de programação foi pedida ainda."
    if acao == "cancelar":
        if t["status"] != "rodando":
            return "Não há tarefa rodando."
        t["cancelada"] = True
        proc = t.get("proc")
        if proc is not None and proc.poll() is None:
            proc.kill()
        return f"Cancelei a tarefa no {t['pasta'].name}."
    if t["status"] == "rodando":
        minutos = (time.time() - t["inicio"]) / 60
        return f"Ainda trabalhando no {t['pasta'].name} há {minutos:.0f} minutos: {t.get('progresso')}."
    return f"Última tarefa ({t['pasta'].name}): {t['resumo']}"


@ferramenta(
    "vscode",
    "Diz qual projeto e qual arquivo estão abertos no VS Code agora.",
)
def vscode() -> str:
    pasta, arquivo = projeto_aberto()
    if pasta is None:
        return "Não há projeto aberto no VS Code."
    return f"Projeto aberto no VS Code: {pasta.name} ({pasta})" + (f"; arquivo em foco: {arquivo}." if arquivo else ".")
