"""Jarvis Intelligent Router: qual agente faz cada passo.

Nota de cada agente para um passo (pesos em orquestra.roteador.pesos):
    capacidade declarada na categoria (0,40) + semelhança SEMÂNTICA entre o passo e o que o agente faz bem (0,25)
    + histórico real (acertos/tentativas nesta categoria, com suavização) (0,20) + custo (0,10) + latência (0,05)
A semelhança usa os embeddings do Jarvis (embeddinggemma, os mesmos da memória); sem o Ollama, cai para uma
semelhança léxica com raízes de palavras. Palavras-chave são só um dos sinais da classificação, nunca o único.
Elegibilidade: disponível agora, sabe a categoria, edita arquivos se o passo escreve, enxerga o projeto se o passo
precisa, cabe no orçamento, não falhou neste passo ainda. O ranking inteiro fica guardado: é a fila de recuperação.
"""
import re
import threading
import unicodedata
from dataclasses import dataclass, field

import numpy as np

from jarvis.orquestra.agentes.base import CATEGORIAS, PRECISA_ARQUIVOS

PESOS = {"capacidade": 0.40, "semantica": 0.25, "historico": 0.20, "custo": 0.10, "latencia": 0.05}
CUSTO = {"gratis": 1.0, "assinatura": 0.85, "variavel": 0.7, "por_token": 0.5}
LATENCIA = {"baixa": 1.0, "media": 0.7, "alta": 0.4}
PALAVRAS = {   # sinais de categoria (raízes, sem acento); somam com a semelhança semântica
    "analise": ["analis", "entend", "arquitet", "estrutur", "problem", "avali", "diagnost", "inspecion", "investig"],
    "implementacao": ["implement", "cri", "adicion", "desenvolv", "funcionalidad", "featur", "constru", "nov"],
    "depuracao": ["corrig", "corrij", "erro", "bug", "falh", "quebr", "excec", "traceback", "consert", "resolv", "arrum"],
    "refatoracao": ["refator", "otimiz", "desempenh", "performanc", "lent", "limp", "melhor", "organiz", "rapid"],
    "testes": ["test", "valid", "verific", "pytest"],
    "documentacao": ["document", "readm", "coment", "guia", "manual", "docstring"],
    "planejamento": ["planej", "plan", "estrateg", "etap", "roadmap", "decid", "passo a passo"],
    "consulta": ["o que e", "como funcion", "expliq", "pergunt", "duvid", "pesquis", "resum", "qual a diferenc"],
    "revisao": ["revis", "review", "confir", "audit"],
    "julgamento": ["compar", "discut", "debat", "escolh", "propost", "melhor opcao", "alternativ"],
}
APELIDOS = {"claude_code": ["claude code", "claude"], "codex": ["codex"], "gemini": ["gemini"],
            "openai": ["gpt", "chatgpt", "openai"], "openrouter": ["openrouter", "open router"],
            "ollama": ["ollama", "qwen", "modelo local"], "testes": ["executor de testes"]}
_PARADAS = set("a o e de da do das dos em no na nos nas um uma para por com que se os as ao aos meu minha seu sua "
               "isso esse essa este esta the and of to in".split())
_SUFIXOS = ("amento", "imento", "acoes", "icoes", "mente", "acao", "icao", "ando", "endo", "indo", "ados", "idos",
            "ador", "ar", "er", "ir", "es", "os", "as", "s")


def sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if not unicodedata.combining(c))


def raizes(texto: str) -> set[str]:
    saida = set()
    for p in re.findall(r"[a-z0-9]+", sem_acento(texto)):
        if p in _PARADAS or len(p) < 3:
            continue
        for s in _SUFIXOS:
            if len(p) - len(s) >= 4 and p.endswith(s):
                p = p[: -len(s)]
                break
        saida.add(p)
    return saida


class Similaridade:
    """Semelhança 0..1 entre um texto e vários alvos: embeddings (se houver) ou léxica."""

    def __init__(self, embedder=None):
        self.embedder = embedder
        self._cache: dict[str, np.ndarray] = {}
        self._lock = threading.Lock()
        self.modo = "semantica" if embedder is not None else "lexica"

    def _vetor(self, texto: str, tipo: str) -> np.ndarray:
        chave = tipo + "|" + texto
        with self._lock:
            if chave in self._cache:
                return self._cache[chave]
        v = self.embedder.vetores([texto], tipo)[0]
        with self._lock:
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[chave] = v
        return v

    def __call__(self, texto: str, alvos: list[str]) -> list[float]:
        if self.embedder is not None:
            try:
                q = self._vetor(texto, "consulta")
                return [float(max(0.0, min(1.0, np.dot(q, self._vetor(a, "documento"))))) for a in alvos]
            except Exception:
                self.modo = "lexica"          # Ollama fechado: segue sem parar
        r = raizes(texto)
        saida = []
        for a in alvos:
            ra = raizes(a)
            saida.append(len(r & ra) / max(1, min(len(r), len(ra))) if r and ra else 0.0)
        return saida


def classificar(texto: str, similaridade: Similaridade) -> list[tuple[str, float]]:
    """[(categoria, nota)] da mais provável para a menos: semântica (0,6) + sinais de palavras (0,4)."""
    cats = list(CATEGORIAS)
    sem = similaridade(texto, [CATEGORIAS[c] for c in cats])
    base = sem_acento(texto)
    notas = []
    for c, s in zip(cats, sem):
        sinais = sum(1 for p in PALAVRAS[c] if p in base)
        notas.append((c, round(0.6 * s + 0.4 * min(1.0, sinais / 2), 4)))
    return sorted(notas, key=lambda x: -x[1])


def mencoes(texto: str) -> list[tuple[str, int, int]]:
    """Agentes citados pelo nome no pedido: [(agente, início, fim)] em ordem."""
    base = sem_acento(texto)
    achados = []
    for agente, nomes in APELIDOS.items():
        for n in sorted(nomes, key=len, reverse=True):
            for m in re.finditer(rf"\b{re.escape(n)}\b", base):
                if not any(a <= m.start() < b for _, a, b in achados):
                    achados.append((agente, m.start(), m.end()))
    return sorted(achados, key=lambda x: x[1])


MIN_HISTORICO = 3         # execuções numa categoria a partir das quais o histórico corrige a capacidade declarada


@dataclass
class Escolha:
    agente: str
    nota: float
    partes: dict = field(default_factory=dict)
    motivo: str = ""


class Roteador:
    def __init__(self, agentes: dict, banco, custos, similaridade: Similaridade | None = None, cfg: dict | None = None):
        self.agentes, self.banco, self.custos = agentes, banco, custos
        self.sim = similaridade or Similaridade()
        r = ((cfg or {}).get("orquestra", {}) or {}).get("roteador") or {}
        self.pesos = {**PESOS, **(r.get("pesos") or {})}
        self.preferidos = r.get("preferidos") or {}          # {categoria: agente}

    def elegivel(self, agente, passo: dict, com_projeto: bool) -> str:
        """'' se pode; senão o motivo."""
        cat = passo["categoria"]
        if not agente.ativo:
            return "desligado"
        if agente.capacidade(cat) <= 0:
            return f"não faz {cat}"
        if passo.get("escrita") and not agente.pode_editar:
            return "não edita arquivos"
        if com_projeto and cat in PRECISA_ARQUIVOS and not agente.pode_ler_projeto:
            return "não enxerga os arquivos do projeto"
        if cat == "testes" and agente.id != "testes" and not passo.get("escrita"):
            return "rodar testes é com o executor de testes"
        st = agente.disponibilidade()
        if st.estado != "disponivel":
            return f"{st.estado}: {st.detalhe}"
        return ""

    def historico(self, agente_id: str, categoria: str, dados: dict) -> tuple[float, int, int]:
        d = dados.get((agente_id, categoria))
        if not d:
            return 0.5, 0, 0
        n, s = int(d["total"]), int(d["sucessos"] or 0)
        return (s + 1) / (n + 2), s, n

    def ranquear(self, passo: dict, com_projeto: bool, excluir: set | None = None) -> tuple[list[Escolha], dict]:
        """(elegíveis da melhor para a pior, {agente: motivo de não ser elegível})."""
        excluir = excluir or set()
        cat = passo["categoria"]
        candidatos, recusados = [], {}
        for a in self.agentes.values():
            if a.id in excluir:
                recusados[a.id] = "já falhou neste passo"
                continue
            motivo = self.elegivel(a, passo, com_projeto)
            if motivo:
                recusados[a.id] = motivo
            else:
                candidatos.append(a)
        if not candidatos:
            return [], recusados
        texto = f"{passo['titulo']}. {passo.get('instrucoes', '')}"[:1500]
        semelhancas = self.sim(texto, [a.descricao_semantica or a.descricao for a in candidatos])
        dados = self.banco.desempenho()
        escolhas = []
        for a, sem in zip(candidatos, semelhancas):
            hist, s, n = self.historico(a.id, cat, dados)
            capacidade = a.capacidade(cat)
            if n >= MIN_HISTORICO:            # com dados suficientes, o que ele FEZ pesa sobre o que ele DECLARA
                capacidade = min(1.0, capacidade * (0.5 + hist))
            partes = {"capacidade": round(capacidade, 3), "semantica": round(sem, 3), "historico": round(hist, 3),
                      "custo": CUSTO.get(a.custo_modo, 0.5), "latencia": LATENCIA.get(a.latencia, 0.7)}
            nota = sum(self.pesos[k] * v for k, v in partes.items())
            if self.preferidos.get(cat) == a.id:
                nota += 0.15
            if a.rodando >= a.max_simultaneos:
                nota -= 0.2                       # ocupado: só se não houver outro
            motivo = (f"capacidade {partes['capacidade']:.2f} em {cat}, semelhança {partes['semantica']:.2f} "
                      f"({self.sim.modo}), histórico {s}/{n}, custo {a.custo_modo}")
            escolhas.append(Escolha(a.id, round(nota, 4), partes, motivo))
        return sorted(escolhas, key=lambda e: -e.nota), recusados

    def escolher(self, passo: dict, com_projeto: bool, excluir: set | None = None) -> Escolha | None:
        fixo = passo.get("agente") if passo.get("agente_fixo") else None
        if fixo:
            a = self.agentes.get(fixo)
            motivo = "não existe" if a is None else self.elegivel(a, passo, com_projeto)
            if a is not None and not motivo and fixo not in (excluir or set()):
                return Escolha(fixo, 1.0, {}, "definido no plano (pedido seu ou regra do passo)")
            ranking, _ = self.ranquear(passo, com_projeto, excluir)
            if ranking:
                ranking[0].motivo = f"{fixo} indisponível ({motivo or 'falhou'}); no lugar: {ranking[0].motivo}"
                return ranking[0]
            return None
        ranking, _ = self.ranquear(passo, com_projeto, excluir)
        return ranking[0] if ranking else None
