"""Context Manager: o que cada agente recebe, e só isso.

- Perfil do projeto (linguagens, estrutura, README, git, comando de testes), com cache curto.
- Quadro da execução: o que cada passo concluiu fica guardado no banco; um passo recebe o resultado SÓ das
  dependências dele (não da conversa inteira), cortado para caber no orçamento.
- Histórico do projeto: o resumo das últimas execuções no mesmo projeto (decisões já tomadas).
- Privacidade: nada da memória pessoal do Iury, nenhuma chave (o Redator passa em tudo), e o agente de texto (API)
  só recebe trechos que o próprio Jarvis montou.
"""
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path

from jarvis.orquestra import seguranca
from jarvis.orquestra.agentes.base import CATEGORIAS, ESCREVE

IGNORAR = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "dist", "build", ".tox", ".mypy_cache",
           ".pytest_cache", ".idea", ".vscode", "data", ".next", "target", "coverage"}
LINGUAGENS = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript/React", ".jsx": "React",
              ".java": "Java", ".cs": "C#", ".go": "Go", ".rs": "Rust", ".php": "PHP", ".rb": "Ruby", ".sql": "SQL",
              ".html": "HTML", ".css": "CSS", ".vue": "Vue", ".kt": "Kotlin", ".swift": "Swift", ".cpp": "C++",
              ".c": "C", ".ps1": "PowerShell", ".sh": "Shell", ".dart": "Dart"}
MAX_PADRAO = 24000
_cache: dict[str, tuple[float, dict]] = {}

PAPEIS = {
    "analise": ("Você é o ANALISTA. Leia o projeto e responda ao objetivo. Não altere nenhum arquivo. Liste os "
                "problemas encontrados com arquivo e linha, gravidade (alta/média/baixa) e a correção sugerida."),
    "implementacao": ("Você é o IMPLEMENTADOR. Faça as mudanças pedidas direto nos arquivos, no padrão do código que "
                      "já existe. Não faça commit. Se puder, rode os testes para conferir. No fim, liste os arquivos "
                      "alterados e o que mudou em cada um."),
    "depuracao": ("Você é o DEPURADOR. Ache a causa raiz antes de mudar qualquer coisa (leia o erro, reproduza). "
                  "Corrija na origem, sem mascarar o sintoma. Não faça commit. Diga a causa, a correção e como conferiu."),
    "refatoracao": ("Você é o OTIMIZADOR. Melhore o código pedido (desempenho, clareza) sem mudar o comportamento. "
                    "Não faça commit. Diga o que mudou e por que é mais rápido ou mais claro."),
    "revisao": ("Você é o REVISOR. Revise as alterações (o diff vem abaixo) procurando bugs, regressões e riscos. Não "
                "altere arquivos. Para cada problema: arquivo, linha, gravidade e como corrigir. Se estiver bom, diga."),
    "documentacao": "Você é o DOCUMENTADOR. Escreva a documentação pedida, clara e direta, em português.",
    "planejamento": ("Você é o PLANEJADOR. Proponha um plano em etapas curtas e objetivas, com riscos e a ordem certa. "
                     "Não altere arquivos."),
    "consulta": "Responda à pergunta de forma direta e correta, em português.",
    "julgamento": ("Você é o JUIZ. Compare as propostas abaixo pelo resultado, risco e esforço. Escolha a melhor (ou "
                   "combine o melhor de cada) e justifique em poucas linhas. Comece com 'ESCOLHA: <nome>'."),
    "testes": "Rode os testes do projeto.",
}
FORMATO = "\n\nTermine com uma seção 'RESUMO:' de 3 a 6 linhas (o que fez/encontrou e o que falta)."


def _git_info(raiz: Path) -> dict:
    if not seguranca.eh_git(raiz):
        return {"git": False}
    try:
        branch = seguranca._rodar_git(raiz, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except seguranca.ErroGit:
        branch = "(sem commits)"
    try:
        mudados = len([l for l in seguranca._rodar_git(raiz, "status", "--porcelain").splitlines() if l.strip()])
    except seguranca.ErroGit:
        mudados = 0
    return {"git": True, "branch": branch, "alterados": mudados}


def perfil_projeto(raiz: Path | str, cfg: dict | None = None, cache_s: float = 120) -> dict:
    raiz = Path(raiz).resolve()
    chave = str(raiz)
    if chave in _cache and time.time() - _cache[chave][0] < cache_s:
        return _cache[chave][1]
    contagem: Counter = Counter()
    arquivos = 0
    for atual, pastas, nomes in os.walk(raiz):
        pastas[:] = [p for p in pastas if p not in IGNORAR and not p.startswith(".")]
        for n in nomes:
            arquivos += 1
            lang = LINGUAGENS.get(Path(n).suffix.lower())
            if lang:
                contagem[lang] += 1
        if arquivos > 20000:
            break
    topo = []
    for item in sorted(raiz.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:60]:
        if item.name in IGNORAR or item.name.startswith(".") and item.name not in (".github",):
            continue
        topo.append(item.name + ("/" if item.is_dir() else ""))
    readme = ""
    for nome in ("README.md", "readme.md", "README.txt", "README"):
        if (raiz / nome).exists():
            readme = (raiz / nome).read_text(encoding="utf-8", errors="replace")[:1500]
            break
    instrucoes = [n for n in ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md") if (raiz / n).exists()]
    from jarvis.orquestra.agentes.testes import comando_de_testes
    cmd, desc = comando_de_testes(raiz, cfg)
    perfil = {"nome": raiz.name, "caminho": str(raiz), "arquivos": arquivos,
              "linguagens": [f"{l} ({n})" for l, n in contagem.most_common(6)], "estrutura": topo,
              "readme": readme, "instrucoes_de_agentes": instrucoes, "testes": desc if cmd else "sem testes detectados",
              **_git_info(raiz)}
    _cache[chave] = (time.time(), perfil)
    return perfil


def texto_perfil(p: dict, completo: bool = False) -> str:
    linhas = [f"Projeto: {p['nome']} ({p.get('arquivos', 0)} arquivos) · linguagens: {', '.join(p['linguagens']) or '?'}",
              f"Git: {'branch ' + p.get('branch', '?') + ', ' + str(p.get('alterados', 0)) + ' arquivos alterados' if p.get('git') else 'não usa git'}",
              f"Testes: {p.get('testes')}", "Estrutura: " + ", ".join(p["estrutura"][:40])]
    if p.get("instrucoes_de_agentes"):
        linhas.append("Instruções para agentes no projeto: " + ", ".join(p["instrucoes_de_agentes"]))
    if completo and p.get("readme"):
        linhas.append("README (começo):\n" + p["readme"])
    return "\n".join(linhas)


def resumo_de(texto: str, limite: int = 700) -> str:
    """A seção RESUMO: do agente (ou o fim da resposta)."""
    m = re.search(r"(?is)\bRESUMO\s*:\s*(.+)$", texto or "")
    base = (m.group(1) if m else texto or "").strip()
    return base[:limite] if m else base[-limite:]


def _cortar(texto: str, limite: int) -> str:
    if len(texto) <= limite:
        return texto
    metade = max(200, limite // 2 - 40)
    return texto[:metade] + "\n[... cortado para caber no contexto ...]\n" + texto[-metade:]


class GerenteDeContexto:
    def __init__(self, banco, cfg: dict | None = None, redator=None):
        self.banco = banco
        self.cfg = cfg or {}
        self.redator = redator or seguranca.Redator()
        self.max = int(((self.cfg.get("orquestra", {}) or {}).get("contexto_max_caracteres")) or MAX_PADRAO)
        self.preferencias = str(((self.cfg.get("orquestra", {}) or {}).get("preferencias")) or "").strip()

    def historico_do_projeto(self, projeto: str | None, menos: int | None = None, quantos: int = 2) -> str:
        if not projeto:
            return ""
        linhas = []
        for e in self.banco.execucoes(20):
            if e["projeto"] != projeto or e["id"] == menos or e["status"] not in ("concluida", "falhou"):
                continue
            linhas.append(f"- #{e['id']} ({e['criada'][:10]}, {e['status']}): {e['pedido'][:120]} → "
                          f"{resumo_de(e['relatorio'], 300)}")
            if len(linhas) >= quantos:
                break
        return ("Execuções anteriores neste projeto (decisões já tomadas):\n" + "\n".join(linhas)) if linhas else ""

    def montar(self, execucao: dict, passo: dict, agente, dependencias: list[dict], extra: str = "") -> tuple[str, str]:
        """(prompt, instruções de papel) para um passo, dentro do orçamento e sem segredos."""
        plano = execucao.get("plano") or {}
        categoria = passo["categoria"]
        partes = [f"PEDIDO DO USUÁRIO: {execucao['pedido']}"]
        if plano.get("objetivo"):
            partes.append(f"OBJETIVO GERAL: {plano['objetivo']}")
        partes.append(f"SUA TAREFA ({passo['titulo']}): {passo.get('instrucoes') or passo['titulo']}")
        restricoes = []
        if passo.get("escrita") and categoria in ESCREVE:
            restricoes.append("Você PODE alterar arquivos deste projeto (só dentro da pasta). Não faça commit nem push.")
            if passo.get("escopo"):
                restricoes.append("Mexa só em: " + ", ".join(passo["escopo"]))
        else:
            restricoes.append("NÃO altere nenhum arquivo: este passo é só de leitura.")
        partes.append("REGRAS: " + " ".join(restricoes))
        if self.preferencias:
            partes.append("PREFERÊNCIAS DO USUÁRIO: " + self.preferencias)
        if execucao.get("projeto"):
            p = perfil_projeto(execucao["projeto"], self.cfg)
            partes.append("CONTEXTO DO PROJETO:\n" + texto_perfil(p, completo=not agente.pode_ler_projeto))
            hist = self.historico_do_projeto(execucao["projeto"], execucao["id"])
            if hist:
                partes.append(hist)
        fixo = "\n\n".join(partes)
        sobra = max(2000, self.max - len(fixo) - len(extra) - 600)
        blocos = []
        if dependencias:
            cota = max(800, sobra // len(dependencias))
            for d in dependencias:
                quem = d.get("agente") or "?"
                corpo = d.get("resultado") or d.get("erro") or "(sem resultado)"
                blocos.append(f"--- Resultado do passo '{d['titulo']}' ({quem}, {d['status']}) ---\n{_cortar(corpo, cota)}")
        prompt = fixo + ("\n\nO QUE OS OUTROS AGENTES JÁ FIZERAM:\n" + "\n\n".join(blocos) if blocos else "")
        if extra:
            prompt += "\n\n" + extra
        prompt += FORMATO
        papel = PAPEIS.get(categoria, PAPEIS["consulta"])
        return self.redator(prompt), papel

    def diff_para_revisao(self, raiz: str, checkpoint: str | None, max_caracteres: int = 10000) -> str:
        if not checkpoint or not seguranca.eh_git(Path(raiz)):
            return ""
        try:
            atual = seguranca.arvore_atual(Path(raiz))
            diff = seguranca.patch(Path(raiz), checkpoint, atual, max_caracteres)
        except (seguranca.ErroGit, OSError, subprocess.SubprocessError):
            return ""
        return ("DIFF DAS ALTERAÇÕES FEITAS NESTA EXECUÇÃO:\n" + diff) if diff else "Nenhum arquivo foi alterado ainda."


def categorias_texto() -> str:
    return "; ".join(f"{k} = {v}" for k, v in CATEGORIAS.items())
