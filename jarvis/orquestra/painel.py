"""Operações do AI Command Center pedidas pela HUD (Central > Agentes IA · Orquestração · Terminal).

Chegam pelo WebSocket da HUD (127.0.0.1 + token) como {tipo: "central", op: "orq_...", ...}. Tudo é validado aqui;
chaves de API entram no cofre e nunca voltam (a HUD só vê '••••••1234').
"""
from pathlib import Path

from jarvis.orquestra.mcp import gateway_mcp, texto_do_resultado
from jarvis.orquestra.planejador import ordem_topologica
from jarvis.orquestra.terminal import Terminal

PROVEDORES_CHAVE = {"openrouter": "openrouter", "openai": "openai", "gemini": "gemini"}


def _int(v) -> int:
    if isinstance(v, bool) or not str(v).isdigit():
        raise ValueError("id inválido")
    return int(v)


def _txt(v, limite=4000) -> str:
    return str(v or "")[:limite]


class PainelOrquestra:
    def __init__(self, nucleo):
        self.n = nucleo
        self.terminal = Terminal(nucleo)

    def tratar(self, pedido: dict) -> dict:
        op = str(pedido.get("op", ""))
        resposta = {"tipo": "central", "op": op, "pedido": pedido.get("pedido")}
        metodo = getattr(self, op, None) if op.startswith("orq_") and op.isidentifier() else None
        if metodo is None:
            return {**resposta, "ok": False, "erro": "operação desconhecida"}
        try:
            return {**resposta, "ok": True, "dados": metodo(pedido)}
        except (ValueError, KeyError) as e:
            return {**resposta, "ok": False, "erro": str(e).strip("'")[:300]}
        except Exception as e:
            return {**resposta, "ok": False, "erro": f"{type(e).__name__}: {e}"[:300]}

    # ------------------------------------------------------------ agentes
    def orq_estado(self, p):
        e = self.n.estado_agentes()
        ativas = self.n.banco.execucoes(10, ("executando", "aguardando_aprovacao", "interrompida", "planejada"))
        e["ativas"] = [self._resumo_exe(x) for x in ativas]
        e["processos"] = len(self.terminal.estado()["processos"])
        return e

    def orq_agente_verificar(self, p):
        a = self.n.agentes.get(_txt(p.get("id"), 40))
        if a is None:
            raise ValueError("agente desconhecido")
        a.esquecer_cache()
        return a.cartao()

    def orq_chave(self, p):
        provedor = PROVEDORES_CHAVE.get(_txt(p.get("provedor"), 30))
        if provedor is None:
            raise ValueError("provedor sem chave por aqui")
        chave = _txt(p.get("apikey"), 400).strip()
        if chave.startswith("••••••"):
            raise ValueError("cole a chave inteira")
        if chave:
            self.n.cofre.guardar(provedor, {"apikey": chave})
            self.n.redator.segredos = sorted(set(self.n.redator.segredos) | {chave}, key=len, reverse=True)
        else:
            self.n.cofre.apagar(provedor)
        for a in self.n.agentes.values():
            a.esquecer_cache()
        return {"provedor": provedor, "guardada": bool(chave), "mascara": self.n.cofre.mascarar(chave)}

    def orq_modo(self, p):
        return {"modo": self.n.definir_modo(_txt(p.get("modo"), 20))}

    def orq_custos(self, p):
        self.n.custos.salvar_limites(**{k: p.get(k) for k in ("limite_tarefa_usd", "limite_dia_usd", "limite_mes_usd",
                                                               "assinatura_conta_no_limite") if k in p})
        return self.n.custos.situacao()

    # ------------------------------------------------------------ projetos
    def orq_projetos(self, p):
        aberto = None
        try:
            aberto = str(self.n.resolver_projeto(""))
        except Exception:
            pass
        return {"autorizados": self.n.banco.projetos(), "vscode": aberto}

    def orq_autorizar(self, p):
        return self.n.autorizar_projeto(_txt(p.get("caminho"), 600), "hud")

    def orq_desautorizar(self, p):
        return {"removido": self.n.banco.desautorizar(_txt(p.get("caminho"), 600))}

    # ------------------------------------------------------------ execuções
    def _resumo_exe(self, e: dict) -> dict:
        passos = self.n.banco.passos(e["id"])
        return {"id": e["id"], "pedido": e["pedido"][:200], "status": e["status"], "modo": e["modo"],
                "projeto": Path(e["projeto"]).name if e["projeto"] else "", "criada": e["criada"], "fim": e["fim"],
                "custo_usd": round(e["custo_usd"], 4), "cobrado_usd": round(e["cobrado_usd"], 4),
                "passos": len(passos), "concluidos": sum(1 for x in passos if x["status"] == "concluido"),
                "agentes": sorted({x["agente"] for x in passos if x["agente"]}), "rodando": self.n.tarefas.rodando(e["id"]),
                "motivo_espera": e["erro"] if e["status"] == "aguardando_aprovacao" else ""}

    def orq_execucoes(self, p):
        return {"itens": [self._resumo_exe(e) for e in self.n.banco.execucoes(int(p.get("limite") or 30))]}

    def orq_execucao(self, p):
        d = self.n.detalhe(_int(p.get("id")))
        try:
            d["niveis"] = ordem_topologica([{"id": x["chave"], "depende_de": x["depende"]} for x in d["passos"]])
        except ValueError:
            d["niveis"] = [[x["chave"] for x in d["passos"]]]
        d["logs"] = self.n.banco.ultimos_logs(d["id"], 300)
        d["nomes"] = {a.id: a.nome for a in self.n.agentes.values()}
        d["projeto_nome"] = Path(d["projeto"]).name if d["projeto"] else ""
        d["autorizado"] = self.n.autorizado(d["projeto"]) if d["projeto"] else True
        d.pop("plano", None)
        return d

    def orq_logs(self, p):
        return {"itens": self.n.banco.logs(_int(p.get("id")), int(p.get("depois_de") or 0), 500)}

    def orq_criar(self, p):
        fixos = p.get("fixos") if isinstance(p.get("fixos"), dict) else None
        projeto = _txt(p.get("projeto"), 600).strip() or None
        exe = self.n.criar(_txt(p.get("texto")), projeto,      # "pedido" é o id da requisição na Central _txt(p.get("modo"), 20) or None, None,
                           {str(k)[:40]: str(v)[:40] for k, v in (fixos or {}).items()}, origem="hud",
                           prioridade=_txt(p.get("prioridade"), 20) or None)
        return {"id": exe["id"], "status": exe["status"], "motivo_espera": exe["motivo_espera"]}

    def orq_trocar_agente(self, p):
        self.n.trocar_agente(_int(p.get("id")), _txt(p.get("passo"), 40), _txt(p.get("agente"), 40) or None)
        return self.orq_execucao({"id": p.get("id")})

    def orq_aprovar(self, p):
        exe = self.n.aprovar(_int(p.get("id")), bool(p.get("autorizar")))
        return {"id": exe["id"], "status": exe["status"]}

    def orq_cancelar(self, p):
        return {"status": self.n.cancelar(_int(p.get("id")))["status"]}

    def orq_pausar(self, p):
        return {"ok": self.n.pausar(_int(p.get("id")), _txt(p.get("passo"), 40) or None)}

    def orq_retomar(self, p):
        return {"ok": self.n.retomar(_int(p.get("id")), _txt(p.get("passo"), 40) or None)}

    def orq_cancelar_passo(self, p):
        return {"ok": self.n.cancelar_passo(_int(p.get("id")), _txt(p.get("passo"), 40))}

    def orq_a_desfazer(self, p):
        return self.n.a_desfazer(_int(p.get("id")))

    def orq_desfazer(self, p):
        if not p.get("confirmado"):
            raise ValueError("confirme para desfazer")
        return self.n.desfazer(_int(p.get("id")))

    def orq_relatorio(self, p):
        exe = self.n.detalhe(_int(p.get("id")))
        return {"relatorio": exe["relatorio"] or self.n.relatorio(exe["id"])[0]}

    # ------------------------------------------------------------ terminal e CLIs
    def orq_terminal_estado(self, p):
        return self.terminal.estado()

    def orq_terminal_exec(self, p):
        return self.terminal.executar(_txt(p.get("pasta"), 600), _txt(p.get("comando")), bool(p.get("confirmado")))

    def orq_terminal_saida(self, p):
        return self.terminal.saida(_int(p.get("id")), int(p.get("depois_de") or 0))

    def orq_terminal_matar(self, p):
        return {"ok": self.terminal.matar(_int(p.get("id")))}

    def orq_terminal_pausar(self, p):
        return {"ok": self.terminal.pausar(_int(p.get("id")))}

    def orq_terminal_retomar(self, p):
        return {"ok": self.terminal.retomar(_int(p.get("id")))}

    def orq_clis(self, p):
        return {"itens": self.terminal.clis(bool(p.get("forcar")))}

    def orq_cli_login(self, p):
        return {"mensagem": self.terminal.login(_txt(p.get("id"), 40))}

    def orq_cli_instalar(self, p):
        return self.terminal.instalar(_txt(p.get("id"), 40), bool(p.get("confirmado")))

    def orq_cli_doctor(self, p):
        return {"saida": self.terminal.doctor(_txt(p.get("id"), 40))}

    # ------------------------------------------------------------ MCP
    def orq_mcp_estado(self, p):
        return {"servidores": gateway_mcp().estado(), "arquivo": str(gateway_mcp().arquivo)}

    def orq_mcp_conectar(self, p):
        nome = _txt(p.get("nome"), 60)
        g = gateway_mcp()
        ferramentas = g.ferramentas(nome, forcar=True)
        self.n.monitor.log(None, None, "mcp", "sistema", f"MCP {nome}: {len(ferramentas)} ferramentas")
        return {"servidores": g.estado()}

    def orq_mcp_salvar(self, p):
        args = p.get("args") if isinstance(p.get("args"), list) else str(p.get("args") or "").split()
        gateway_mcp().salvar_servidor(_txt(p.get("nome"), 60), _txt(p.get("comando"), 300),
                                      [str(a)[:300] for a in args][:30])
        return {"servidores": gateway_mcp().estado()}

    def orq_mcp_remover(self, p):
        gateway_mcp().remover_servidor(_txt(p.get("nome"), 60))
        return {"servidores": gateway_mcp().estado()}

    def orq_mcp_testar(self, p):
        """Chama uma ferramenta MCP de LEITURA (🟢/🟡) pela Central; as 🔴 só pela conversa, com confirmação."""
        servidor, ferramenta = _txt(p.get("servidor"), 60), _txt(p.get("ferramenta"), 120)
        g = gateway_mcp()
        if g.nivel(servidor, ferramenta) == "confirmar":
            raise ValueError("essa ferramenta pode alterar coisas: use pela conversa, que pede confirmação")
        r = g.chamar(servidor, ferramenta, p.get("argumentos") if isinstance(p.get("argumentos"), dict) else {})
        return {"texto": self.n.redator(texto_do_resultado(r)), "erro": bool(r.get("isError"))}
