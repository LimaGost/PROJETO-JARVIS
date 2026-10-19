"""Demonstração do AI Command Center: uma tarefa de verdade distribuída entre IAs diferentes.

    .venv\\Scripts\\python.exe -m jarvis.orquestra.demo               # com as CLIs REAIS (Claude Code, Codex...)
    .venv\\Scripts\\python.exe -m jarvis.orquestra.demo --simulado    # sem login: CLIs simuladas (formato real de eventos)
    ... --agente implementacao=codex --agente analise=claude_code   # fixa agentes por categoria (o resto o roteador escolhe)

Cria um projeto de exemplo (data/orquestra/demo/loja-exemplo, um repositório git com um bug de desconto e uma busca
O(n²)) e pede: "analise meu projeto, encontre problemas, corrija os erros e melhore o desempenho". O Jarvis planeja,
o roteador escolhe um agente por passo (análise → implementação → testes de verdade → revisão), tudo com checkpoint
git, auditoria e limite de custo, e no fim mostra o relatório e confere os testes por conta própria.
No modo real os agentes gastam da sua assinatura/chaves (limite por tarefa: orquestra.custos.limite_tarefa_usd).
"""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from jarvis.config import RAIZ, carregar
from jarvis.orquestra import seguranca
from jarvis.orquestra.banco import Banco

PASTA = RAIZ / "data" / "orquestra" / "demo"
PEDIDO = "analise meu projeto, encontre problemas, corrija os erros e melhore o desempenho"
SIMULADOR = Path(__file__).parent / "simuladores" / "cli_simulado.py"

ARQUIVOS = {
    "loja/__init__.py": "",
    "loja/precos.py": '''"""Preços da loja."""


def aplicar_desconto(preco: float, percentual: float) -> float:
    """Preço com desconto. `percentual` vai de 0 a 100 (10 = 10%)."""
    return round(preco - preco * percentual, 2)


def total_pedido(itens: list[dict]) -> float:
    return round(sum(i["preco"] * i["qtd"] for i in itens), 2)
''',
    "loja/estoque.py": '''"""Estoque da loja."""


def produtos_repetidos(codigos: list[str]) -> list[str]:
    """Códigos que aparecem mais de uma vez, na ordem em que aparecem."""
    repetidos = []
    for i, a in enumerate(codigos):
        for j, b in enumerate(codigos):
            if i != j and a == b and a not in repetidos:
                repetidos.append(a)
    return repetidos
''',
    "tests/__init__.py": "",
    "tests/test_precos.py": '''from loja.precos import aplicar_desconto, total_pedido


def test_desconto_de_10_por_cento():
    assert aplicar_desconto(100.0, 10) == 90.0


def test_sem_desconto():
    assert aplicar_desconto(59.9, 0) == 59.9


def test_total_pedido():
    assert total_pedido([{"preco": 10.0, "qtd": 2}, {"preco": 5.5, "qtd": 1}]) == 25.5
''',
    "tests/test_estoque.py": '''from loja.estoque import produtos_repetidos


def test_repetidos_na_ordem():
    assert produtos_repetidos(["A1", "B2", "A1", "C3", "B2", "A1"]) == ["A1", "B2"]


def test_sem_repetidos():
    assert produtos_repetidos(["X", "Y"]) == []
''',
    "conftest.py": "",
    "README.md": "# Loja exemplo\n\nProjeto de exemplo da demonstração do JARVIS AI (orquestrador de agentes).\n",
}


def criar_projeto(pasta: Path) -> Path:
    if pasta.exists():
        shutil.rmtree(pasta)
    pasta.mkdir(parents=True)
    for rel, texto in ARQUIVOS.items():
        (pasta / rel).parent.mkdir(parents=True, exist_ok=True)
        (pasta / rel).write_text(texto, encoding="utf-8")
    git = ["git", "-C", str(pasta), "-c", "user.name=Jarvis demo", "-c", "user.email=jarvis@localhost"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "loja exemplo"]):
        subprocess.run([*git, *args], check=True, capture_output=True)
    return pasta


def configurar(simulado: bool, atraso: float, fixos: dict) -> dict:
    cfg = carregar()
    o = cfg.setdefault("orquestra", {})
    o["testes"] = {**(o.get("testes") or {}), "loja-exemplo": [sys.executable, "-m", "pytest", "-q", "-p",
                                                               "no:cacheprovider", "--color=no"]}
    if simulado:
        o["planejar_com_ia"] = o["relatorio_com_ia"] = False          # o núcleo também seria uma IA de verdade
        ag = o["agentes"] = {k: dict(v or {}) for k, v in (o.get("agentes") or {}).items()}
        for id_ in ("claude_code", "codex"):
            ag.setdefault(id_, {})["comando"] = [sys.executable, str(SIMULADOR), "claude" if id_ == "claude_code" else "codex"]
            ag[id_].pop("modelo", None)
        for id_ in ("gemini", "ollama", "openrouter", "openai"):
            ag.setdefault(id_, {})["ativo"] = False
        import os
        os.environ["JARVIS_SIM_ATRASO"] = str(atraso)
    return cfg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--simulado", action="store_true", help="usa as CLIs simuladas (sem login, sem custo)")
    ap.add_argument("--atraso", type=float, default=0.5, help="segundos entre eventos do simulador")
    ap.add_argument("--agente", action="append", default=[], metavar="CATEGORIA=AGENTE",
                    help="fixa o agente de uma categoria (ex.: implementacao=codex)")
    ap.add_argument("--pedido", default=PEDIDO)
    ap.add_argument("--modelo", default="", help="modelo do Claude Code e do núcleo (ex.: haiku, para gastar menos)")
    a = ap.parse_args(argv)
    fixos = {}
    for par in a.agente:
        cat, _, ag = par.partition("=")
        fixos[f"categoria:{cat.strip()}"] = ag.strip()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from jarvis.connect.cofre import Cofre, cofre
    from jarvis.orquestra.nucleo import Orquestra
    cfg = configurar(a.simulado, a.atraso, fixos)
    if a.modelo:
        cfg["orquestra"]["modelo_nucleo"] = a.modelo
        cfg["orquestra"].setdefault("agentes", {}).setdefault("claude_code", {})["modelo"] = a.modelo
    PASTA.mkdir(parents=True, exist_ok=True)
    projeto = criar_projeto(PASTA / "loja-exemplo")
    banco_arq = PASTA / ("demo-simulado.db" if a.simulado else "demo.db")
    banco_arq.unlink(missing_ok=True)
    o = Orquestra(cfg, Banco(banco_arq), Cofre(PASTA / "cofre-simulado.json") if a.simulado else cofre(),
                  emitir=lambda tipo, **k: None, avisar=lambda texto, fala="": None)

    titulo = "SIMULADO (CLIs falsas no formato real)" if a.simulado else "REAL (CLIs instaladas no seu PC)"
    print(f"\n=== JARVIS AI · demonstração {titulo} ===\nprojeto: {projeto}\npedido:  {a.pedido}\n")
    print("Agentes:")
    for c in o.estado_agentes()["agentes"]:
        print(f"  {'●' if c['estado'] == 'disponivel' else '○'} {c['nome']:<18} {c['estado']:<13} "
              f"{c['versao'] or '-':<10} {c['detalhe'][:70]}")

    o.autorizar_projeto(projeto, "demo")
    exe = o.criar(a.pedido, projeto, "automatico", fixos=fixos, origem="demo")
    print(f"\nPlano #{exe['id']} ({exe['plano']['origem']}):")
    for p in exe["passos"]:
        print(f"  {p['chave']:<16} {p['categoria']:<14} depende de {', '.join(p['depende']) or '-':<22} "
              f"{'ALTERA ARQUIVOS' if p['escrita'] else ''}")
    print("\nAo vivo:")
    visto, inicio = 0, time.time()
    try:
        while True:
            for l in o.banco.logs(exe["id"], visto, 500):
                visto = l["id"]
                quem = f"{l['passo'] or 'jarvis'}/{l['agente']}" if l["agente"] else (l["passo"] or "jarvis")
                print(f"  {l['quando'][11:19]} [{quem}] {l['tipo']}: {l['conteudo'][:220].replace(chr(10), ' ⏎ ')}")
            if not o.tarefas.rodando(exe["id"]) and o.detalhe(exe["id"])["status"] not in ("executando", "planejada"):
                break
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\nCancelando…")
        o.cancelar(exe["id"])
        time.sleep(2)
    exe = o.detalhe(exe["id"])
    print(f"\nResultado: {exe['status']} em {time.time() - inicio:.0f} s · custo estimado US$ {exe['custo_usd']:.4f} "
          f"(cobrado US$ {exe['cobrado_usd']:.4f}) · {exe['tokens_entrada'] + exe['tokens_saida']} tokens")
    print("Quem fez o quê:")
    for p in exe["passos"]:
        nome = o.agentes[p["agente"]].nome if p["agente"] in o.agentes else "-"
        print(f"  {p['chave']:<16} {p['status']:<10} {nome:<16} {p['motivo'][:90]}")
    print("\n" + (exe["relatorio"] or "(sem relatório)"))

    print("Conferência independente (fora do Jarvis):")
    diff = subprocess.run(["git", "-C", str(projeto), "diff", "--stat"], capture_output=True, text=True).stdout.strip()
    print("  git diff --stat:", diff.replace("\n", " | ") or "(nada mudou)")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--color=no"], cwd=projeto,
                       capture_output=True, text=True, env=seguranca.ambiente_limpo())
    final = (r.stdout.strip().splitlines() or ["?"])[-1]
    print(f"  pytest: {final}")
    print(f"  desfazer tudo: na Central > Orquestração > #{exe['id']} > Desfazer (ou banco em {banco_arq.name})\n")
    return 0 if exe["status"] == "concluida" and r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
