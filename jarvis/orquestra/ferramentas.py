"""O que o cérebro do Jarvis usa do AI Command Center (você fala; ele delega).

"use as IAs para desenvolver X", "peça ao Claude Code para revisar e ao Codex para implementar", "faça os agentes
discutirem e escolha a melhor", "quais agentes estão trabalhando e quanto estão gastando?"...
Níveis (decididos pelo CÓDIGO): planejar é 🟡; executar alterando arquivos num projeto ainda não autorizado é 🔴
(uma vez por projeto); aprovar um plano é 🔴 (mostra os passos e os agentes); desfazer é 🔴; mudar para o modo
automático é 🔴. Depois de ler conteúdo externo, os 🟡 viram 🔴 (regra geral do Executor).
"""
from jarvis.orquestra.agentes.base import ESCREVE, PRECISA_ARQUIVOS
from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, conteudo_externo, ferramenta

_n = None


def ativar(nucleo):
    global _n
    _n = nucleo


def _nucleo():
    if _n is None:
        raise ErroFerramenta("o AI Command Center está desligado (orquestra.ativo no config.yaml)")
    return _n


def _precisa_projeto(pedido: str, passos: list | None) -> tuple[bool, bool]:
    """(usa arquivos de projeto, pode alterar arquivos)."""
    if passos:
        cats = {str(p.get("categoria")) for p in passos if isinstance(p, dict)}
        escreve = any(isinstance(p, dict) and p.get("escrita") for p in passos)
        return bool(cats & (PRECISA_ARQUIVOS | ESCREVE | {"testes"})) or escreve, escreve
    # o mesmo modelo de plano que o Jarvis usaria sem IA, supondo que haja projeto: diz se mexe em arquivos
    from jarvis.orquestra.planejador import plano_por_modelo
    previsto = plano_por_modelo(pedido, True, _nucleo().sim, False)["passos"]
    cats = {p["categoria"] for p in previsto}
    escreve = any(p["escrita"] for p in previsto)
    return bool(cats & (PRECISA_ARQUIVOS | ESCREVE | {"testes"})) or escreve, escreve


def _projeto(args: dict, precisa: bool):
    texto = str(args.get("projeto") or "").strip()
    if texto or precisa:
        return _nucleo().resolver_projeto(texto)
    return None


def _avaliar_orquestrar(args: dict) -> Avaliacao:
    n = _nucleo()
    pedido = str(args.get("pedido") or "").strip()
    if not pedido:
        raise ErroFerramenta("diga o que é para fazer")
    precisa, escreve = _precisa_projeto(pedido, args.get("passos"))
    raiz = _projeto(args, precisa)
    modo = args.get("modo") if args.get("modo") in ("automatico", "supervisionado", "manual") else n.modo
    onde = f" no projeto {raiz.name}" if raiz else ""
    if modo == "automatico" and raiz is not None and escreve and not n.autorizado(raiz):
        return Avaliacao(Nivel.CONFIRMAR, f"autorizar os agentes de IA a trabalhar{onde} (alterando arquivos, com "
                                          f"checkpoint para desfazer) e executar: {pedido[:120]}")
    return Avaliacao(Nivel.REGISTRADO, f"planejar com os agentes de IA{onde}: {pedido[:120]}")


@ferramenta(
    "orquestrar",
    "Distribui um pedido entre os agentes de IA (Claude Code, Codex, Gemini, GPT/OpenRouter, Ollama, executor de "
    "testes): planeja, escolhe o melhor agente para cada passo, roda em paralelo quando dá, valida com os testes e "
    "consolida um relatório. Use para: 'use as IAs para...', 'peça ao Claude Code / ao Codex...', 'analise e corrija "
    "meu projeto', 'faça os agentes discutirem e escolha a melhor', ou qualquer tarefa de código grande. Passe "
    "'passos' (você já planeja bem): [{id, titulo, categoria, instrucoes, depende_de, escrita, agente, opcional}] com "
    "categoria em analise|implementacao|depuracao|refatoracao|testes|documentacao|planejamento|consulta|revisao|"
    "julgamento; agente = claude_code|codex|gemini|openai|openrouter|ollama|testes ou omita (o roteador escolhe). "
    "Passos sem dependência rodam juntos; um passo 'testes' (agente testes) antes e outro depois das mudanças. "
    "Responda só que começou ou que o plano espera aprovação: o aviso de conclusão chega sozinho.",
    {"pedido": {"type": "string", "description": "O pedido completo do usuário"},
     "projeto": {"type": "string", "description": "Nome ou caminho do projeto (padrão: o aberto no VS Code; vazio "
                                                  "para perguntas sem projeto)"},
     "modo": {"type": "string", "enum": ["automatico", "supervisionado", "manual"]},
     "passos": {"type": "array", "items": {"type": "object"}},
     "agentes": {"type": "object", "description": "Agente fixo por passo: {\"p1\": \"codex\"}"}},
    obrigatorios=("pedido",), avaliar=_avaliar_orquestrar, so_claude=True,
)
def orquestrar(pedido: str, projeto: str = "", modo: str = "", passos: list | None = None,
               agentes: dict | None = None) -> str:
    n = _nucleo()
    precisa, escreve = _precisa_projeto(pedido, passos)
    raiz = _projeto({"projeto": projeto}, precisa)
    modo = modo if modo in ("automatico", "supervisionado", "manual") else n.modo
    if modo == "automatico" and raiz is not None and escreve and not n.autorizado(raiz):
        n.autorizar_projeto(raiz, "voz")         # chegou aqui = você confirmou o 🔴 acima
    exe = n.criar(pedido, raiz, modo, passos or None, agentes if isinstance(agentes, dict) else None, origem="voz")
    passos_txt = "; ".join(f"{p['titulo']} ({n.agentes[p['agente']].nome if p['agente'] in n.agentes else 'roteador'})"
                           for p in exe["passos"])
    if exe["status"] == "aguardando_aprovacao":
        return (f"Plano #{exe['id']} pronto, esperando aprovação ({exe['motivo_espera']}): {passos_txt}. Para executar: "
                f"aprovar_plano com id {exe['id']} (ou o botão Aprovar na Central).")
    return f"Comecei a orquestração #{exe['id']}: {passos_txt}. Aviso quando terminar."


def _avaliar_aprovar(args: dict) -> Avaliacao:
    n = _nucleo()
    exe = n.detalhe(int(args["id"]))
    if exe["status"] not in ("aguardando_aprovacao", "interrompida", "planejada"):
        raise ErroFerramenta(f"o plano #{exe['id']} está {exe['status']}; não há o que aprovar")
    extra = ""
    if exe["projeto"] and any(p["escrita"] for p in exe["passos"]) and not n.autorizado(exe["projeto"]):
        extra = " (e autorizar os agentes neste projeto)"
    return Avaliacao(Nivel.CONFIRMAR, n.resumo_plano(exe["id"]) + extra)


@ferramenta(
    "aprovar_plano",
    "Aprova e executa um plano de orquestração que está esperando aprovação (pede confirmação mostrando os passos).",
    {"id": {"type": "integer"}}, obrigatorios=("id",), avaliar=_avaliar_aprovar, so_claude=True,
)
def aprovar_plano(id: int) -> str:
    n = _nucleo()
    exe = n.detalhe(int(id))
    autorizar = bool(exe["projeto"]) and any(p["escrita"] for p in exe["passos"])
    n.aprovar(int(id), autorizar_projeto=autorizar)
    return f"Plano #{id} aprovado: os agentes começaram. Aviso quando terminar."


@ferramenta(
    "status_orquestra",
    "Mostra quais agentes de IA estão trabalhando agora, em quê, há quanto tempo, quanto gastaram hoje, os planos "
    "esperando aprovação e o modo (automático/supervisionado/manual).",
    so_claude=True,
)
def status_orquestra() -> str:
    n = _nucleo()
    e = n.estado_agentes()
    linhas = [f"Modo: {e['modo']}. Gasto hoje: US$ {e['custos']['hoje']['custo']:.2f} estimado, "
              f"US$ {e['custos']['hoje']['cobrado']:.2f} cobrado."]
    for c in e["agentes"]:
        trabalhos = "; ".join(f"#{t['execucao']} {t['titulo']} há {t['tempo_s']:.0f}s" for t in c["trabalhos"])
        linhas.append(f"- {c['nome']}: {c['estado']}" + (f" ({trabalhos})" if trabalhos else f" ({c['detalhe']})")
                      + (f" · hoje US$ {c['hoje']['custo']:.3f}, {c['hoje']['entrada'] + c['hoje']['saida']} tokens"
                         if c["hoje"]["chamadas"] else ""))
    esperando = n.banco.execucoes(10, ("aguardando_aprovacao", "interrompida"))
    if esperando:
        linhas.append("Esperando: " + "; ".join(f"#{x['id']} {x['pedido'][:60]} ({x['status']})" for x in esperando))
    return "\n".join(linhas)


@ferramenta(
    "relatorio_orquestracao",
    "Lê o relatório (ou o andamento) de uma orquestração pelo id.",
    {"id": {"type": "integer"}}, obrigatorios=("id",), externo=True, so_claude=True,
)
def relatorio_orquestracao(id: int) -> str:
    n = _nucleo()
    exe = n.detalhe(int(id))
    if exe["relatorio"]:
        return conteudo_externo("relatório da orquestração", exe["relatorio"])
    andamento = "; ".join(f"{p['titulo']}: {p['status']}" for p in exe["passos"])
    return f"#{id} está {exe['status']}: {andamento}"


@ferramenta(
    "controlar_orquestracao",
    "Pausa, retoma ou cancela uma orquestração (ou um passo dela).",
    {"id": {"type": "integer"}, "acao": {"type": "string", "enum": ["pausar", "retomar", "cancelar"]},
     "passo": {"type": "string"}},
    obrigatorios=("id", "acao"), nivel=Nivel.REGISTRADO, so_claude=True,
)
def controlar_orquestracao(id: int, acao: str, passo: str = "") -> str:
    n = _nucleo()
    if acao == "cancelar":
        if passo:
            return "Cancelei o passo." if n.cancelar_passo(int(id), passo) else "Esse passo não está rodando."
        return f"Orquestração #{id}: {n.cancelar(int(id))['status']}."
    ok = n.pausar(int(id), passo or None) if acao == "pausar" else n.retomar(int(id), passo or None)
    return f"{'Pausei' if acao == 'pausar' else 'Retomei'} a #{id}." if ok else "Não havia o que " + acao + "."


def _avaliar_desfazer(args: dict) -> Avaliacao:
    previa = _nucleo().a_desfazer(int(args["id"]))
    return Avaliacao(Nivel.CONFIRMAR, f"desfazer a orquestração #{args['id']}: {len(previa['voltam'])} arquivo(s) voltam "
                                      f"ao que eram e {len(previa['somem'])} arquivo(s) novos são apagados"
                                      + (f" ({', '.join(previa['somem'][:5])})" if previa["somem"] else ""))


@ferramenta(
    "desfazer_orquestracao",
    "Desfaz tudo o que os agentes mudaram numa orquestração (volta ao checkpoint git de antes).",
    {"id": {"type": "integer"}}, obrigatorios=("id",), avaliar=_avaliar_desfazer, so_claude=True,
)
def desfazer_orquestracao(id: int) -> str:
    r = _nucleo().desfazer(int(id))
    return f"Desfeito: {len(r['restaurados'])} arquivo(s) restaurados e {len(r['apagados'])} apagados."


def _avaliar_modo(args: dict) -> Avaliacao:
    modo = args.get("modo")
    if modo == "automatico":
        return Avaliacao(Nivel.CONFIRMAR, "passar o AI Command Center para o modo AUTOMÁTICO (os agentes executam sem "
                                          "esperar aprovação nos projetos já autorizados)")
    return Avaliacao(Nivel.REGISTRADO, f"passar o AI Command Center para o modo {modo}")


@ferramenta(
    "modo_orquestra",
    "Troca o modo do AI Command Center: automatico, supervisionado (mostra o plano e espera) ou manual.",
    {"modo": {"type": "string", "enum": ["automatico", "supervisionado", "manual"]}},
    obrigatorios=("modo",), avaliar=_avaliar_modo, so_claude=True,
)
def modo_orquestra(modo: str) -> str:
    return f"Modo {_nucleo().definir_modo(modo)} ativado."


def _avaliar_autorizar(args: dict) -> Avaliacao:
    raiz = _nucleo().resolver_projeto(str(args.get("projeto") or ""))
    return Avaliacao(Nivel.CONFIRMAR, f"autorizar os agentes de IA a trabalhar no projeto {raiz.name} ({raiz})")


@ferramenta(
    "autorizar_projeto",
    "Autoriza uma pasta de projeto para os agentes de IA trabalharem (uma vez por projeto).",
    {"projeto": {"type": "string", "description": "Nome ou caminho (padrão: o aberto no VS Code)"}},
    avaliar=_avaliar_autorizar, so_claude=True,
)
def autorizar_projeto(projeto: str = "") -> str:
    n = _nucleo()
    r = n.autorizar_projeto(n.resolver_projeto(projeto))
    return f"Projeto {r['nome']} autorizado para os agentes."


@ferramenta(
    "consultar_agente",
    "Faz uma pergunta direta a um agente específico (ex.: 'pergunte ao Gemini...', 'o que o GPT acha de...') e "
    "devolve a resposta. Sem alterar arquivos.",
    {"agente": {"type": "string", "enum": ["claude_code", "codex", "gemini", "openai", "openrouter", "ollama"]},
     "pergunta": {"type": "string"}, "projeto": {"type": "string", "description": "Opcional: para perguntas sobre um projeto"}},
    obrigatorios=("agente", "pergunta"), nivel=Nivel.REGISTRADO, externo=True, so_claude=True,
)
def consultar_agente(agente: str, pergunta: str, projeto: str = "") -> str:
    n = _nucleo()
    raiz = n.resolver_projeto(projeto) if projeto else None
    exe = n.consultar(agente, pergunta, raiz)
    p = exe["passos"][0]
    if exe["rodando"]:
        return f"{n.agentes[agente].nome} ainda está pensando (#{exe['id']}); aviso quando responder."
    if p["status"] != "concluido":
        return f"{n.agentes[agente].nome} não conseguiu: {p['erro']}"
    return conteudo_externo(f"resposta do {n.agentes[agente].nome}", p["resultado"][:6000])


# ------------------------------------------------------------------ MCP
@ferramenta(
    "mcp_ferramentas",
    "Lista os servidores MCP configurados e as ferramentas de cada um (com o nível de permissão).",
    {"servidor": {"type": "string", "description": "Opcional: só um servidor"}},
    so_claude=True,
)
def mcp_ferramentas(servidor: str = "") -> str:
    from jarvis.orquestra.mcp import ErroMCP, gateway_mcp
    g = gateway_mcp()
    nomes = [servidor] if servidor else list(g.config())
    if not nomes:
        return "Nenhum servidor MCP configurado (Central > Terminal > MCP, ou data/orquestra/mcp.json)."
    linhas = []
    for nome in nomes:
        try:
            fs = g.ferramentas(nome)
            linhas.append(f"{nome}: " + "; ".join(f"{f.get('name')} [{g.nivel(nome, f.get('name'))}] "
                                                  f"{(f.get('description') or '')[:80]}" for f in fs[:40]))
        except ErroMCP as e:
            linhas.append(f"{nome}: não conectou ({e})")
    return "\n".join(linhas)


def _avaliar_mcp(args: dict) -> Avaliacao:
    from jarvis.orquestra.mcp import gateway_mcp
    g = gateway_mcp()
    servidor, nome = str(args.get("servidor") or ""), str(args.get("ferramenta") or "")
    g.ferramentas(servidor)
    nivel = {"livre": Nivel.LIVRE, "registrado": Nivel.REGISTRADO}.get(g.nivel(servidor, nome), Nivel.CONFIRMAR)
    resumo = f"usar a ferramenta MCP {servidor}/{nome} com {str(args.get('argumentos') or {})[:160]}"
    return Avaliacao(nivel, resumo)


@ferramenta(
    "mcp_chamar",
    "Chama uma ferramenta de um servidor MCP conectado ao Jarvis (veja mcp_ferramentas).",
    {"servidor": {"type": "string"}, "ferramenta": {"type": "string"}, "argumentos": {"type": "object"}},
    obrigatorios=("servidor", "ferramenta"), avaliar=_avaliar_mcp, externo=True, so_claude=True,
)
def mcp_chamar(servidor: str, ferramenta: str, argumentos: dict | None = None) -> str:
    from jarvis.orquestra.mcp import ErroMCP, gateway_mcp, texto_do_resultado
    try:
        r = gateway_mcp().chamar(servidor, ferramenta, argumentos or {})
    except ErroMCP as e:
        raise ErroFerramenta(str(e))
    texto = texto_do_resultado(r)
    if _n is not None:
        texto = _n.redator(texto)
    return conteudo_externo(f"ferramenta MCP {servidor}/{ferramenta}", texto) + (" [a ferramenta indicou erro]"
                                                                               if r.get("isError") else "")
