"""Executor de testes: o agente que NÃO é IA. Roda os testes do projeto de verdade e conta o que passou e o que falhou.

Descobre o comando sozinho:
  - Python (pasta tests/ ou arquivos test_*.py / *_test.py): `<python do .venv do projeto> -m pytest -q`;
  - Node (package.json com script "test" de verdade): `npm test`;
  - ou o que você definir em orquestra.testes.<nome do projeto>: ["comando", "args"...].
Rodar os testes antes e depois das mudanças é o que deixa o Jarvis separar falha que já existia de falha nova.
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

from jarvis.orquestra import gateway
from jarvis.orquestra.agentes.base import Agente, Controle, Deteccao, Disponibilidade, Resultado, Trabalho, agente
from jarvis.orquestra.seguranca import ambiente_limpo

_RESUMO_PYTEST = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)")
_FALHA_PYTEST = re.compile(r"^(?:FAILED|ERROR) (\S+)")
IGNORAR = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".tox", ".mypy_cache"}


def python_do_projeto(raiz: Path) -> str | None:
    for pasta in (".venv", "venv", "env"):
        for rel in ("Scripts/python.exe", "bin/python"):
            exe = raiz / pasta / rel
            if exe.exists():
                return str(exe)
    return shutil.which("python") or shutil.which("python3") or sys.executable


def tem_testes_python(raiz: Path) -> bool:
    if (raiz / "tests").is_dir() or (raiz / "test").is_dir():
        return True
    vistos = 0
    for p in raiz.rglob("*.py"):
        if IGNORAR & set(p.relative_to(raiz).parts):
            continue
        vistos += 1
        if p.name.startswith("test_") or p.name.endswith("_test.py"):
            return True
        if vistos > 3000:
            break
    return False


def comando_de_testes(raiz: Path, cfg: dict | None = None) -> tuple[list[str] | None, str]:
    """(argv, descrição) ou (None, por que não achou)."""
    raiz = Path(raiz)
    proprios = ((cfg or {}).get("orquestra", {}) or {}).get("testes") or {}
    proprio = proprios.get(raiz.name) or proprios.get(str(raiz))
    if proprio:
        cmd = gateway.resolver_executavel(proprio[0]) if isinstance(proprio, list) else None
        return ([*cmd, *proprio[1:]], "comando definido no config") if cmd else (None, f"não achei '{proprio[0]}'")
    pacote = raiz / "package.json"
    if pacote.exists():
        try:
            script = (json.loads(pacote.read_text(encoding="utf-8")).get("scripts") or {}).get("test", "")
        except (OSError, ValueError):
            script = ""
        if script and "no test specified" not in script:
            npm = gateway.resolver_executavel("npm")
            if npm:
                return [*npm, "test", "--silent"], "npm test"
    if tem_testes_python(raiz):
        py = python_do_projeto(raiz)
        return [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--color=no"], f"pytest ({Path(py).name})"
    return None, "não achei testes neste projeto (nem tests/ de Python nem script test do npm)"


def ler_pytest(saida: str) -> dict:
    contagem = {"passou": 0, "falhou": 0, "erros": 0, "pulados": 0}
    linhas = saida.strip().splitlines()
    resumo = next((l for l in reversed(linhas) if _RESUMO_PYTEST.search(l)), "")
    for n, tipo in _RESUMO_PYTEST.findall(resumo):
        chave = {"passed": "passou", "failed": "falhou", "error": "erros", "errors": "erros",
                 "skipped": "pulados"}.get(tipo)
        if chave:
            contagem[chave] += int(n)
    falhas = [m.group(1) for l in linhas if (m := _FALHA_PYTEST.match(l))]
    return {**contagem, "falhas": falhas[:60], "resumo": resumo.strip(" =")}


@agente
class ExecutorDeTestes(Agente):
    id = "testes"
    nome = "Executor de testes"
    provedor = "Jarvis"
    tipo = "local"
    icone = "flask-conical"
    descricao = "Roda os testes do projeto (pytest, npm test) antes e depois das mudanças. Não é IA: é a prova."
    descricao_semantica = "rodar os testes automatizados do projeto e conferir se as alterações funcionam"
    capacidades = {"testes": 1.0}
    pode_ler_projeto = True
    custo_modo = "gratis"
    latencia = "baixa"
    suporta_pausar = True
    max_simultaneos = 2

    def _detectar(self) -> Deteccao:
        return Deteccao(True, "local", detalhe="pytest / npm test")

    def _autenticacao(self) -> Disponibilidade:
        return Disponibilidade("disponivel", "pronto", True, "local")

    def executar(self, trabalho: Trabalho, controle: Controle) -> Resultado:
        inicio = time.time()
        if trabalho.cwd is None:
            return Resultado(False, erro="o executor de testes precisa da pasta do projeto")
        argv, descricao = comando_de_testes(trabalho.cwd, self.cfg_geral)
        if argv is None:
            return Resultado(False, erro=descricao, dados={"sem_testes": True})
        controle.evento("comando", " ".join(argv[-6:]) + f"   ({descricao})")
        linhas: list[str] = []

        def ao_linha(fluxo, linha):
            linhas.append(linha)
            if len(linhas) <= 400:
                controle.evento("saida", linha[:400])
        p = gateway.Processo(argv, trabalho.cwd, ambiente_limpo(), None, min(trabalho.limite_s, 1800),
                             trabalho.limite_mem_mb, ao_linha, nome="testes")
        controle.registrar(p)
        p.iniciar()
        p.esperar(trabalho.limite_s + 60)
        saida = "\n".join(linhas)
        r = Resultado(False, duracao_s=round(time.time() - inicio, 1), comandos=[" ".join(argv)])
        if p.estado == "cancelado":
            r.cancelado, r.erro = True, "cancelado"
            return r
        if p.estado in ("estourou_tempo", "estourou_memoria", "falhou_iniciar"):
            r.erro = p.motivo_fim
            return r
        e_pytest = "pytest" in descricao or "pytest" in argv
        dados = ler_pytest(saida) if e_pytest else {"passou": 0, "falhou": 0, "erros": 0, "falhas": []}
        dados["verde"] = p.codigo == 0
        dados["codigo"] = p.codigo
        dados["comando"] = descricao
        if e_pytest and p.codigo == 5:          # pytest: 5 = nenhum teste coletado
            r.erro, dados["sem_testes"] = "o pytest não achou testes", True
            r.dados = dados
            return r
        r.ok = True                                           # os testes RODARAM (passando ou não)
        r.dados = dados
        cauda = "\n".join(linhas[-25:])
        r.texto = (f"Testes ({descricao}): " + ("TODOS PASSARAM" if dados["verde"] else "HÁ FALHAS") +
                   (f" · {dados.get('resumo')}" if dados.get("resumo") else f" · código {p.codigo}") +
                   ("\nFalhas: " + ", ".join(dados["falhas"][:15]) if dados.get("falhas") else "") +
                   f"\n--- final da saída ---\n{cauda}")
        controle.evento("uso", dados.get("resumo") or f"código {p.codigo}")
        return r
