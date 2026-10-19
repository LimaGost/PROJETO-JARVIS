"""CLI SIMULADA de Claude Code e Codex, para a demonstração (`python -m jarvis.orquestra.demo --simulado`) e os testes.

NÃO é IA: é um programa determinístico que fala EXATAMENTE o formato das CLIs reais, para o Jarvis exercitar o caminho
inteiro (gateway de processo, leitura de eventos, sandbox, checkpoint, testes de verdade, relatório) num PC sem login
no Codex/Claude. Toda resposta diz [SIMULADO]. Uso (o Jarvis chama assim, via `orquestra.agentes.<id>.comando`):

    python cli_simulado.py claude -p --output-format stream-json --verbose --tools Read,Glob,Grep ...   (prompt no stdin)
    python cli_simulado.py codex exec --json -s read-only -C <pasta> -c approval_policy=never -                (idem)

O que ele "sabe" fazer é o projeto de exemplo da demonstração (loja: desconto errado e busca de repetidos O(n²)):
lê os arquivos de verdade, aponta os problemas com arquivo e linha e, quando a sandbox deixa escrever, corrige os
arquivos de verdade. Em modo só leitura ele nunca escreve (igual à CLI real). Atraso entre eventos: JARVIS_SIM_ATRASO.
Para os testes de falha: JARVIS_SIM_FERRAMENTA_EXTRA (o init lista uma ferramenta a mais), JARVIS_SIM_RECONEXAO=1
(o Codex manda o aviso "Reconnecting..." no evento error, que não é fatal) e JARVIS_SIM_FALHAR=<mensagem> (turno falha).
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

ATRASO = float(os.environ.get("JARVIS_SIM_ATRASO", "0.5"))
FALHAR = os.environ.get("JARVIS_SIM_FALHAR", "")
VERSAO = {"claude": "2.1.303 (Claude Code) [SIMULADO]", "codex": "codex-cli 0.163.0 [SIMULADO]"}
AJUDA_CLAUDE = ("Usage: claude [options] [command] [prompt]  [SIMULADO]\n  -p, --print\n  --output-format <format>\n"
                "  --verbose\n  --model <model>\n  --tools <tools...>\n  --allowedTools <tools...>\n"
                "  --disallowedTools <tools...>\n  --permission-mode <mode>\n  --max-turns <n>\n  --max-budget-usd <amount>\n"
                "  --append-system-prompt <prompt>\n  --strict-mcp-config\n  --restricted\n  --resume <id>\n"
                "  --disable-slash-commands\n")

# problemas que o simulador reconhece: (arquivo, trecho com bug, correção, gravidade, explicação)
CORRECOES = [
    ("loja/precos.py", "return round(preco - preco * percentual, 2)", "return round(preco - preco * percentual / 100, 2)",
     "alta", "aplicar_desconto trata o percentual como fração: 10% de desconto vira 1000% (preço negativo)"),
    ("loja/estoque.py",
     "    repetidos = []\n    for i, a in enumerate(codigos):\n        for j, b in enumerate(codigos):\n"
     "            if i != j and a == b and a not in repetidos:\n                repetidos.append(a)\n    return repetidos",
     "    vistos, repetidos = set(), {}\n    for c in codigos:\n        if c in vistos:\n            repetidos[c] = None\n"
     "        vistos.add(c)\n    return list(repetidos)",
     "média", "produtos_repetidos compara todos com todos (O(n²)) e ainda faz `in` numa lista: trava com estoques grandes"),
]


def pausa(fator: float = 1.0):
    time.sleep(ATRASO * fator)


def emitir(ev: dict):
    print(json.dumps(ev, ensure_ascii=False), flush=True)


def arg(argv: list[str], nome: str, padrao: str = "") -> str:
    return argv[argv.index(nome) + 1] if nome in argv and argv.index(nome) + 1 < len(argv) else padrao


def achados(raiz: Path) -> list[dict]:
    saida = []
    for rel, bug, _, gravidade, explicacao in CORRECOES:
        arq = raiz / rel
        if arq.exists():
            texto = arq.read_text(encoding="utf-8")
            if bug in texto:
                linha = texto[: texto.index(bug)].count("\n") + 1
                saida.append({"arquivo": rel, "linha": linha, "gravidade": gravidade, "explicacao": explicacao})
    return saida


def lidos(raiz: Path) -> list[str]:
    return sorted(str(p.relative_to(raiz)).replace("\\", "/") for p in raiz.rglob("*.py")
                  if not {".git", ".venv", "__pycache__"} & set(p.relative_to(raiz).parts))[:12]


def corrigir(raiz: Path) -> list[str]:
    mudados = []
    for rel, bug, certo, _, _ in CORRECOES:
        arq = raiz / rel
        if arq.exists() and bug in (texto := arq.read_text(encoding="utf-8")):
            arq.write_text(texto.replace(bug, certo), encoding="utf-8")
            mudados.append(rel)
    return mudados


def papel(prompt: str) -> str:
    for chave, nome in (("REVISOR", "revisao"), ("IMPLEMENTADOR", "escrever"), ("DEPURADOR", "escrever"),
                        ("OTIMIZADOR", "escrever"), ("ANALISTA", "analise")):
        if chave in prompt:
            return nome
    return "consulta"


def responder(raiz: Path, prompt: str, pode_escrever: bool) -> tuple[str, list[str], list[str]]:
    """(texto final, arquivos lidos, arquivos alterados)."""
    p = papel(prompt)
    arquivos = lidos(raiz)
    if p == "escrever" and pode_escrever:
        antes = achados(raiz)
        mudados = corrigir(raiz)
        if not mudados:
            return ("[SIMULADO] Não achei nada que eu saiba corrigir neste projeto (o simulador só conhece o projeto de "
                    "exemplo).\nRESUMO: nada alterado [SIMULADO]"), arquivos, []
        linhas = [f"- {a['arquivo']}: {a['explicacao']} → corrigido" for a in antes]
        return ("[SIMULADO] Corrigi na origem:\n" + "\n".join(linhas) +
                "\n\nRESUMO: corrigi o desconto (percentual/100) e troquei a busca O(n²) por um set (O(n)) [SIMULADO]"), \
            arquivos, mudados
    if p == "revisao":
        diff = prompt.split("DIFF DAS ALTERAÇÕES", 1)[-1] if "DIFF DAS ALTERAÇÕES" in prompt else ""
        if "percentual / 100" in diff or "vistos" in diff:
            return ("[SIMULADO] Revisão: a correção do desconto está certa (percentual dividido por 100) e a nova "
                    "busca de repetidos mantém a ordem de aparição e é O(n). Sem regressões à vista.\n"
                    "RESUMO: mudanças aprovadas na revisão [SIMULADO]"), arquivos, []
        return "[SIMULADO] Revisão: não vi mudanças para revisar.\nRESUMO: nada a revisar [SIMULADO]", arquivos, []
    if p == "analise":
        lista = achados(raiz)
        if not lista:
            return ("[SIMULADO] Li " + ", ".join(arquivos[:6]) + " e não achei os problemas que o simulador conhece.\n"
                    "RESUMO: nenhum problema conhecido [SIMULADO]"), arquivos, []
        linhas = [f"- {a['arquivo']}:{a['linha']} · gravidade {a['gravidade']} · {a['explicacao']}" for a in lista]
        return ("[SIMULADO] Problemas encontrados:\n" + "\n".join(linhas) +
                f"\n\nRESUMO: {len(lista)} problema(s): desconto errado e busca de repetidos lenta [SIMULADO]"), arquivos, []
    return f"[SIMULADO] Recebi o pedido ({len(prompt)} caracteres).\nRESUMO: resposta simulada [SIMULADO]", arquivos, []


# ---------------------------------------------------------------- claude
def claude(argv: list[str]) -> int:
    if "--version" in argv:
        print(VERSAO["claude"])
        return 0
    if "--help" in argv:
        print(AJUDA_CLAUDE)
        return 0
    if argv[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "simulado (claude.ai)", "simulado": True}))
        return 0
    if argv[:1] == ["doctor"]:
        print("[SIMULADO] Claude Code doctor: tudo certo (simulador)")
        return 0
    prompt = sys.stdin.read()
    ferramentas = [f for f in arg(argv, "--tools").split(",") if f]
    pode_escrever = "Edit" in ferramentas and arg(argv, "--permission-mode") == "acceptEdits"
    sessao = arg(argv, "--resume") or "sim-" + uuid.uuid4().hex[:12]
    raiz = Path.cwd()
    extra = [f for f in os.environ.get("JARVIS_SIM_FERRAMENTA_EXTRA", "").split(",") if f]
    emitir({"type": "system", "subtype": "init", "session_id": sessao, "tools": ferramentas + extra, "cwd": str(raiz),
            "model": arg(argv, "--model", "sonnet"), "permissionMode": arg(argv, "--permission-mode", "default"),
            "apiKeySource": "none", "mcp_servers": []})
    if extra:                 # a CLI real seguiria trabalhando; o Jarvis tem de encerrar ao ver o init
        pausa(4)
    if FALHAR:
        emitir({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": FALHAR,
                "session_id": sessao, "total_cost_usd": 0.0001, "usage": {"input_tokens": 10, "output_tokens": 0}})
        return 1
    texto, arquivos, mudados = responder(raiz, arg(argv, "--append-system-prompt") + "\n" + prompt, pode_escrever)
    for n, rel in enumerate(arquivos[:5]):
        pausa()
        emitir({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"toolu_{n}", "name": "Read", "input": {"file_path": str(raiz / rel)}}]},
                "session_id": sessao})
        emitir({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": "(conteúdo do arquivo)"}]}, "session_id": sessao})
    for n, rel in enumerate(mudados):
        pausa()
        emitir({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"toolu_e{n}", "name": "Edit", "input": {"file_path": str(raiz / rel)}}]},
                "session_id": sessao})
    pausa()
    emitir({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": texto}]},
            "session_id": sessao})
    entrada, saida = len(prompt) // 4 + 900, len(texto) // 4 + 120
    emitir({"type": "result", "subtype": "success", "is_error": False, "result": texto, "session_id": sessao,
            "num_turns": len(arquivos[:5]) + len(mudados) + 1, "duration_ms": int(ATRASO * 1000 * 6),
            "total_cost_usd": round(entrada * 3e-6 + saida * 15e-6, 5),
            "usage": {"input_tokens": entrada, "output_tokens": saida, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}})
    return 0


# ---------------------------------------------------------------- codex
def codex(argv: list[str]) -> int:
    if "--version" in argv:
        print(VERSAO["codex"])
        return 0
    if argv[:2] == ["login", "status"]:
        print("Logged in using ChatGPT [SIMULADO]", file=sys.stderr)
        return 0
    if argv[:1] == ["doctor"]:
        print("[SIMULADO] codex doctor: tudo certo (simulador)")
        return 0
    if argv[:1] != ["exec"]:
        print("[SIMULADO] uso: codex exec --json ...", file=sys.stderr)
        return 2
    prompt = sys.stdin.read() if argv[-1:] == ["-"] else argv[-1]
    if argv[1:2] == ["resume"]:
        sessao = argv[2]
        sandbox = next((c.split("=", 1)[1] for c in argv if c.startswith("sandbox_mode=")), "read-only")
    else:
        sessao = str(uuid.uuid4())
        sandbox = arg(argv, "-s", "read-only")
    raiz = Path(arg(argv, "-C") or os.getcwd())
    emitir({"type": "thread.started", "thread_id": sessao})
    emitir({"type": "turn.started"})
    if os.environ.get("JARVIS_SIM_RECONEXAO"):
        emitir({"type": "error", "message": "Reconnecting... 1/5 [SIMULADO]"})
    if FALHAR:
        emitir({"type": "turn.failed", "error": {"message": FALHAR}})
        return 1
    pausa()
    emitir({"type": "item.completed", "item": {"id": "item_0", "type": "reasoning",
                                               "text": "**Lendo o projeto** [SIMULADO]"}})
    texto, arquivos, mudados = responder(raiz, prompt, sandbox == "workspace-write")
    comando = "rg --files" if sys.platform != "win32" else "Get-ChildItem -Recurse -Name"
    emitir({"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": comando,
                                             "aggregated_output": "", "exit_code": None, "status": "in_progress"}})
    pausa()
    emitir({"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": comando,
                                               "aggregated_output": "\n".join(arquivos), "exit_code": 0,
                                               "status": "completed"}})
    if mudados:
        pausa()
        emitir({"type": "item.completed", "item": {"id": "item_2", "type": "file_change", "status": "completed",
                                                   "changes": [{"path": str(raiz / m), "kind": "update"} for m in mudados]}})
    pausa()
    emitir({"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": texto}})
    emitir({"type": "turn.completed", "usage": {"input_tokens": len(prompt) // 4 + 2400, "cached_input_tokens": 1200,
                                                "output_tokens": len(texto) // 4 + 90, "reasoning_output_tokens": 64}})
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("claude", "codex"):
        print("uso: cli_simulado.py claude|codex ...", file=sys.stderr)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    return (claude if sys.argv[1] == "claude" else codex)(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
