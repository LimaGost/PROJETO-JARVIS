"""Cost Manager: quanto cada agente custou e os limites (por tarefa, por dia, por mês).

Dois números, porque assinatura não é dinheiro saindo:
  - custo estimado: o que a chamada custaria na API (o Claude Code informa; Codex/GPT pela tabela de preços);
  - custo cobrado: o que sai do seu bolso (chaves de API: OpenRouter, OpenAI, Claude/Codex com chave).
O limite por TAREFA vale para o estimado (trava contra um agente que entra em loop, inclusive na assinatura); os
limites por DIA e MÊS valem para o cobrado (orquestra.custos.assinatura_conta_no_limite: true conta os dois).
"""
from dataclasses import dataclass

from jarvis.orquestra.banco import Banco, inicio_do_dia, inicio_do_mes

PADRAO = {"limite_tarefa_usd": 3.0, "limite_dia_usd": 5.0, "limite_mes_usd": 30.0, "assinatura_conta_no_limite": False}


@dataclass
class Limites:
    tarefa: float
    dia: float
    mes: float
    assinatura_conta: bool


class GerenteDeCustos:
    def __init__(self, banco: Banco, cfg: dict | None = None):
        self.banco = banco
        self.cfg = cfg or {}

    def limites(self) -> Limites:
        c = {**PADRAO, **(((self.cfg.get("orquestra", {}) or {}).get("custos")) or {}),
             **(self.banco.config("custos", {}) or {})}
        return Limites(float(c["limite_tarefa_usd"]), float(c["limite_dia_usd"]), float(c["limite_mes_usd"]),
                       bool(c["assinatura_conta_no_limite"]))

    def salvar_limites(self, **novos):
        atuais = self.banco.config("custos", {}) or {}
        for k in ("limite_tarefa_usd", "limite_dia_usd", "limite_mes_usd"):
            if k in novos and novos[k] is not None:
                valor = float(novos[k])
                if not 0 <= valor <= 10_000:
                    raise ValueError(f"{k} fora do intervalo (0 a 10.000)")
                atuais[k] = valor
        if "assinatura_conta_no_limite" in novos:
            atuais["assinatura_conta_no_limite"] = bool(novos["assinatura_conta_no_limite"])
        self.banco.salvar_config("custos", atuais)

    def _no_limite(self, g: dict, lim: Limites) -> float:
        return float(g["custo"] if lim.assinatura_conta else g["cobrado"])

    def situacao(self) -> dict:
        lim = self.limites()
        dia, mes = self.banco.gasto_desde(inicio_do_dia()), self.banco.gasto_desde(inicio_do_mes())
        return {"limites": lim.__dict__, "hoje": dia, "mes": mes,
                "hoje_no_limite": round(self._no_limite(dia, lim), 4), "mes_no_limite": round(self._no_limite(mes, lim), 4),
                "por_agente_hoje": self.banco.gasto_por_agente(inicio_do_dia()),
                "por_agente_mes": self.banco.gasto_por_agente(inicio_do_mes())}

    def pode_gastar(self, execucao: dict | None, cobra: bool) -> tuple[bool, str, float]:
        """(pode, motivo, quanto resta para esta tarefa)."""
        lim = self.limites()
        gasto_tarefa = float((execucao or {}).get("custo_usd") or 0)
        resta = lim.tarefa - gasto_tarefa
        if resta <= 0:
            return False, f"a tarefa já chegou no limite de US$ {lim.tarefa:.2f}", 0.0
        if cobra or lim.assinatura_conta:
            hoje = self._no_limite(self.banco.gasto_desde(inicio_do_dia()), lim)
            mes = self._no_limite(self.banco.gasto_desde(inicio_do_mes()), lim)
            if hoje >= lim.dia:
                return False, f"o limite do dia (US$ {lim.dia:.2f}) foi atingido", 0.0
            if mes >= lim.mes:
                return False, f"o limite do mês (US$ {lim.mes:.2f}) foi atingido", 0.0
            resta = min(resta, lim.dia - hoje, lim.mes - mes)
        return True, "", round(resta, 4)

    def registrar(self, agente: str, execucao_id: int | None, custo: float, cobrado: bool, entrada: int, saida: int):
        self.banco.registrar_gasto(agente, execucao_id, custo, custo if cobrado else 0.0, entrada, saida)
        if execucao_id is not None:
            self.banco.somar_na_execucao(execucao_id, custo, custo if cobrado else 0.0, entrada, saida)


def estimar_tokens(texto: str) -> int:
    """~4 caracteres por token (português/código): bom o bastante para orçamento de contexto."""
    return max(1, len(texto or "") // 4)
