"""Jarvis Core, parte 1: entender o pedido e montar o plano.

Plano = {"objetivo", "resultado_esperado", "categoria", "prioridade", "complexidade", "recursos", "multiagente",
         "passos": [{"id", "titulo", "categoria", "instrucoes", "depende_de", "escrita", "escopo", "agente", "opcional"}]}
Três caminhos, nesta ordem:
  1. o próprio cérebro do Jarvis (Claude, na conversa) já manda os passos (ferramenta `orquestrar`);
  2. a IA isolada do núcleo monta o plano em JSON, conhecendo os agentes disponíveis agora;
  3. sem IA: modelos prontos (corrigir/melhorar, analisar, comparar propostas, "peça ao X e ao Y", pergunta).
Todo plano passa por `validar_plano`: ids únicos, dependências que existem e sem ciclo, no máximo 8 passos, escrita só
em categorias que alteram arquivos e só com projeto, escopo sem "..".
"""
import re

from jarvis.orquestra import ia
from jarvis.orquestra.agentes.base import CATEGORIAS, ESCREVE
from jarvis.orquestra.contexto import categorias_texto, perfil_projeto, texto_perfil
from jarvis.orquestra.roteador import Similaridade, classificar, mencoes, sem_acento

MAX_PASSOS = 8
PRIORIDADES = ("baixa", "normal", "alta", "urgente")
COMPLEXIDADES = ("baixa", "media", "alta")
_ID = re.compile(r"[^a-z0-9_]")


def validar_plano(plano: dict, agentes: dict, com_projeto: bool) -> dict:
    if not isinstance(plano, dict):
        raise ValueError("o plano precisa ser um objeto")
    passos_brutos = plano.get("passos") or []
    if not isinstance(passos_brutos, list) or not passos_brutos:
        raise ValueError("o plano precisa de pelo menos um passo")
    if len(passos_brutos) > MAX_PASSOS:
        raise ValueError(f"no máximo {MAX_PASSOS} passos")
    passos, ids = [], set()
    for i, p in enumerate(passos_brutos):
        if not isinstance(p, dict):
            raise ValueError(f"passo {i + 1} inválido")
        pid = _ID.sub("_", str(p.get("id") or f"p{i + 1}").lower())[:20] or f"p{i + 1}"
        if pid in ids:
            raise ValueError(f"id repetido: {pid}")
        ids.add(pid)
        cat = str(p.get("categoria") or "").strip().lower()
        if cat not in CATEGORIAS:
            raise ValueError(f"passo {pid}: categoria desconhecida '{cat}' (opções: {', '.join(CATEGORIAS)})")
        escrita = bool(p.get("escrita")) and cat in ESCREVE
        if escrita and not com_projeto:
            raise ValueError(f"passo {pid}: só dá para alterar arquivos com um projeto escolhido")
        agente = p.get("agente") or None
        if agente is not None and agente not in agentes:
            agente = None                         # nome desconhecido: o roteador escolhe
        escopo = [str(e).strip().replace("\\", "/") for e in (p.get("escopo") or []) if str(e).strip()]
        if any(".." in e.split("/") or e.startswith("/") or re.match(r"^[a-zA-Z]:", e) for e in escopo):
            raise ValueError(f"passo {pid}: o escopo precisa ser caminhos relativos dentro do projeto")
        passos.append({"id": pid, "titulo": str(p.get("titulo") or cat)[:150], "categoria": cat,
                       "instrucoes": str(p.get("instrucoes") or "")[:4000],
                       "depende_de": [_ID.sub("_", str(d).lower()) for d in (p.get("depende_de") or [])],
                       "escrita": escrita, "escopo": escopo[:20], "agente": agente,
                       "opcional": bool(p.get("opcional"))})
    for p in passos:
        faltam = [d for d in p["depende_de"] if d not in ids]
        if faltam:
            raise ValueError(f"passo {p['id']} depende de passo que não existe: {', '.join(faltam)}")
        if p["id"] in p["depende_de"]:
            raise ValueError(f"passo {p['id']} depende de si mesmo")
    ordem_topologica(passos)                     # levanta erro se houver ciclo
    cat = plano.get("categoria") if plano.get("categoria") in CATEGORIAS else passos[0]["categoria"]
    return {"objetivo": str(plano.get("objetivo") or "")[:600],
            "resultado_esperado": str(plano.get("resultado_esperado") or "")[:600],
            "categoria": cat,
            "prioridade": plano.get("prioridade") if plano.get("prioridade") in PRIORIDADES else "normal",
            "complexidade": plano.get("complexidade") if plano.get("complexidade") in COMPLEXIDADES else
            ("alta" if len(passos) >= 4 else "media" if len(passos) >= 2 else "baixa"),
            "recursos": [str(r)[:60] for r in (plano.get("recursos") or [])][:8] or recursos_de(passos, com_projeto),
            "multiagente": len({p["agente"] or p["categoria"] for p in passos}) > 1,
            "passos": passos}


def ordem_topologica(passos: list[dict]) -> list[list[str]]:
    """Níveis do DAG (cada nível pode rodar em paralelo). ValueError se houver ciclo."""
    deps = {p["id"]: set(p["depende_de"]) for p in passos}
    niveis, feitos = [], set()
    while deps:
        prontos = sorted(i for i, d in deps.items() if d <= feitos)
        if not prontos:
            raise ValueError("o plano tem dependência circular")
        niveis.append(prontos)
        feitos |= set(prontos)
        for i in prontos:
            deps.pop(i)
    return niveis


def recursos_de(passos: list[dict], com_projeto: bool) -> list[str]:
    r = []
    if com_projeto:
        r.append("arquivos do projeto")
    if any(p["escrita"] for p in passos):
        r.append("alterar arquivos")
    if any(p["categoria"] == "testes" for p in passos):
        r.append("rodar testes")
    if len(passos) > 1:
        r.append("vários agentes")
    return r


# ------------------------------------------------------------------ modelos prontos (sem IA)
def _quer(texto: str, *raizes_: str) -> bool:
    base = sem_acento(texto)
    return any(r in base for r in raizes_)


def _limpar_trecho(texto: str) -> str:
    """'  para revisar a arquitetura e ao ' -> 'revisar a arquitetura'."""
    t = texto.strip(" ,.;:")
    t = re.sub(r"(?i)^(?:para|pra|que|a|o)\s+", "", t)
    t = re.sub(r"(?i)\s+(?:e|depois)(?:\s+(?:ao|a|o|pro|pra|para o|para a))?$", "", t)
    return t.strip(" ,.;:")


def _passo(pid, titulo, cat, instrucoes, deps=(), escrita=False, agente=None, opcional=False):
    return {"id": pid, "titulo": titulo, "categoria": cat, "instrucoes": instrucoes, "depende_de": list(deps),
            "escrita": escrita, "agente": agente, "opcional": opcional}


def plano_por_modelo(pedido: str, com_projeto: bool, similaridade: Similaridade, tem_testes: bool,
                     disponiveis: list[str] | None = None) -> dict:
    """Plano sem IA, a partir do tipo do pedido (semântica + sinais) e dos agentes citados pelo nome."""
    disponiveis = disponiveis or []
    cats = classificar(pedido, similaridade)
    principal = cats[0][0]
    citados = mencoes(pedido)
    quer_mudar = com_projeto and principal != "documentacao" and (
        principal in ESCREVE or _quer(pedido, "corrig", "corrij", "implement", "melhor", "otimiz", "refator", "adicion",
                                      "cri", "consert", "resolv", "arrum", "desenvolv"))
    objetivo = pedido.strip()[:300]
    # 1) "peça ao Claude Code para X e ao Codex para Y": um passo por agente citado, na ordem
    if len({a for a, _, _ in citados}) >= 2 or (citados and _quer(pedido, "peca", "pede", "mande", "manda", "use o",
                                                                      "use a", "utilize")):
        passos, anterior = [], None
        for i, (agente, _, fim_nome) in enumerate(citados):
            fim = citados[i + 1][1] if i + 1 < len(citados) else len(pedido)
            trecho = _limpar_trecho(pedido[fim_nome:fim])
            cat = classificar(trecho, similaridade)[0][0]
            if cat == "testes" and agente != "testes":
                cat = "implementacao" if com_projeto else "consulta"
            escrita = com_projeto and cat in ESCREVE
            if not com_projeto and cat in ("analise", "revisao", "implementacao", "depuracao", "refatoracao"):
                cat = "consulta"
            pid = f"p{len(passos) + 1}"
            passos.append(_passo(pid, trecho[:120] or cat, cat, trecho, [anterior] if anterior else [], escrita, agente))
            anterior = pid
        if com_projeto and tem_testes and any(p["escrita"] for p in passos):
            passos.append(_passo(f"p{len(passos) + 1}", "Rodar os testes depois das mudanças", "testes",
                                 "Rode os testes do projeto.", [anterior], agente="testes"))
        return {"objetivo": objetivo, "categoria": passos[0]["categoria"], "passos": passos}
    # 2) comparar/discutir propostas
    if principal == "julgamento" or _quer(pedido, "compar", "discut", "debat", "propost"):
        candidatos = [a for a in ("claude_code", "codex", "gemini", "openai", "openrouter", "ollama")
                      if a in disponiveis][:3] or [None, None]
        cat_prop = "analise" if com_projeto else "planejamento"
        passos = [_passo(f"proposta_{i + 1}", f"Proposta {i + 1}", cat_prop,
                         f"Proponha a sua solução para: {pedido}. Seja concreto (o que mudar, onde, riscos).",
                         agente=a) for i, a in enumerate(candidatos)]
        ids = [p["id"] for p in passos]
        passos.append(_passo("julgamento", "Comparar as propostas e escolher a melhor", "julgamento",
                             "Compare as propostas e escolha a melhor, justificando.", ids))
        if quer_mudar and _quer(pedido, "implement", "aplic", "faca", "corrij", "corrig", "arrum"):
            passos.append(_passo("implementar", "Implementar a proposta escolhida", "implementacao",
                                 "Implemente a proposta escolhida no julgamento.", ["julgamento"], escrita=True))
            if tem_testes:
                passos.append(_passo("testes_depois", "Rodar os testes", "testes", "Rode os testes.", ["implementar"],
                                     agente="testes"))
        return {"objetivo": objetivo, "categoria": "julgamento", "passos": passos}
    # 3) analisar + corrigir/implementar/melhorar + testar + revisar
    if quer_mudar:
        cat_mudar = principal if principal in ESCREVE and principal != "documentacao" else (
            "depuracao" if _quer(pedido, "corrig", "corrij", "erro", "bug", "falh", "consert", "arrum") else
            "refatoracao" if _quer(pedido, "otimiz", "desempenh", "refator", "melhor") else "implementacao")
        passos = [_passo("analise", "Analisar o projeto e achar os problemas", "analise",
                         f"Analise o projeto com foco em: {pedido}. Liste os problemas e onde corrigir.")]
        deps = ["analise"]
        if tem_testes:
            passos.append(_passo("testes_antes", "Rodar os testes antes das mudanças", "testes",
                                 "Rode os testes para saber o que já falhava.", agente="testes"))
            deps.append("testes_antes")
        passos.append(_passo("mudar", {"depuracao": "Corrigir os problemas", "refatoracao": "Melhorar o código"}.get(
            cat_mudar, "Implementar"), cat_mudar,
            f"Com base na análise (e nos testes que já falhavam), faça: {pedido}", deps, escrita=True))
        if tem_testes:
            passos.append(_passo("testes_depois", "Rodar os testes depois das mudanças", "testes",
                                 "Rode os testes de novo para conferir as mudanças.", ["mudar"], agente="testes"))
        passos.append(_passo("revisao", "Revisar as alterações", "revisao",
                             "Revise o diff das alterações procurando bugs e regressões.",
                             ["mudar"] + (["testes_depois"] if tem_testes else []), opcional=True))
        return {"objetivo": objetivo, "categoria": cat_mudar, "complexidade": "alta", "passos": passos}
    # 4) um passo só (analisar, revisar, documentar, planejar, perguntar)
    cat = principal
    if not com_projeto and cat in ("analise", "revisao", "testes", "implementacao", "depuracao", "refatoracao"):
        cat = "consulta"
    if cat == "testes":
        return {"objetivo": objetivo, "categoria": "testes",
                "passos": [_passo("testes", "Rodar os testes", "testes", "Rode os testes.", agente="testes")]}
    escrita = com_projeto and cat == "documentacao" and _quer(pedido, "escrev", "cri", "atualiz", "gere", "faca")
    return {"objetivo": objetivo, "categoria": cat,
            "passos": [_passo("p1", pedido.strip()[:120], cat, pedido.strip(), escrita=escrita)]}


# ------------------------------------------------------------------ com IA
SISTEMA = """Você é o planejador do Jarvis: recebe um pedido e divide em passos para agentes de IA.
Responda SÓ com um objeto JSON neste formato:
{"objetivo": "...", "resultado_esperado": "...", "categoria": "<categoria principal>", "prioridade": "baixa|normal|alta|urgente",
 "complexidade": "baixa|media|alta", "recursos": ["..."],
 "passos": [{"id": "p1", "titulo": "...", "categoria": "<categoria>", "instrucoes": "o que exatamente fazer",
             "depende_de": [], "escrita": false, "escopo": [], "agente": null, "opcional": false}]}
Regras:
- Categorias: {categorias}.
- Use o MENOR número de passos que resolve bem (1 passo para pedidos simples; no máximo 8).
- Passos sem dependência entre si rodam em paralelo; passos que ALTERAM arquivos (escrita: true) rodam um de cada vez.
- "escrita": true só em implementacao, depuracao, refatoracao ou documentacao, e só se há projeto.
- Se o projeto tem testes e o pedido muda código: um passo "testes" antes (paralelo à análise) e outro depois da mudança.
- "agente": deixe null (o roteador escolhe) A NÃO SER que o usuário tenha pedido um agente pelo nome.
- Agentes disponíveis agora: {agentes}.
- Para "comparar/discutir propostas": um passo por agente (em paralelo), depois um passo "julgamento" que depende de todos."""


class Planejador:
    def __init__(self, agentes: dict, similaridade: Similaridade, cfg: dict | None = None, pensar=ia.pensar):
        self.agentes, self.sim, self.cfg, self.pensar = agentes, similaridade, cfg or {}, pensar
        self.usar_ia = ((self.cfg.get("orquestra", {}) or {}).get("planejar_com_ia", True)) is not False

    def _disponiveis(self) -> list[str]:
        return [a.id for a in self.agentes.values() if a.disponibilidade().estado == "disponivel"]

    def planejar(self, pedido: str, projeto: str | None, passos: list | None = None) -> tuple[dict, str, float]:
        """(plano validado, origem: 'cerebro' | 'ia' | 'modelo', custo do planejamento)."""
        com_projeto = bool(projeto)
        if passos:
            return validar_plano({"objetivo": pedido, "passos": passos}, self.agentes, com_projeto), "cerebro", 0.0
        perfil = perfil_projeto(projeto, self.cfg) if projeto else None
        tem_testes = bool(perfil and not perfil.get("testes", "").startswith("sem testes"))
        disponiveis = self._disponiveis()
        custo = 0.0
        if self.usar_ia:
            agentes_txt = "; ".join(
                f"{a.id} ({a.nome}: {', '.join(f'{c} {v:.1f}' for c, v in a.capacidades.items() if v >= 0.7)}"
                f"{', edita arquivos' if a.pode_editar else ', só texto'})" for a in self.agentes.values()
                if a.id in disponiveis) or "nenhum"
            prompt = (f"PEDIDO: {pedido}\n\n" + (f"PROJETO:\n{texto_perfil(perfil)}" if perfil else "SEM PROJETO "
                      "(só perguntas/planos/texto, nada de escrita).") + f"\nO projeto {'TEM' if tem_testes else 'NÃO tem'} testes.")
            try:
                r = self.pensar(prompt, SISTEMA.replace("{categorias}", categorias_texto()).replace("{agentes}", agentes_txt),
                                self.cfg)
                custo = r.custo
                plano = validar_plano(ia.extrair_json(r.texto), self.agentes, com_projeto)
                return plano, "ia", custo
            except Exception:
                pass                                  # IA fora do ar ou JSON ruim: cai nos modelos prontos
        bruto = plano_por_modelo(pedido, com_projeto, self.sim, tem_testes, disponiveis)
        return validar_plano(bruto, self.agentes, com_projeto), "modelo", custo
