"""Jarvis Core, parte 2: validar e consolidar. O relatório final de uma execução, em português.

Parte fixa (sempre, sem IA): quem fez cada passo e por quê, tempo, custo, testes antes × depois (falhas novas e
corrigidas), arquivos alterados (diff do checkpoint até agora), conflitos, alertas da auditoria e como desfazer.
Parte escrita pela IA do núcleo (quando disponível): o resumo para você e a frase que ele fala em voz alta.
"""
import re
from datetime import datetime
from pathlib import Path

from jarvis.orquestra import ia, seguranca
from jarvis.orquestra.contexto import resumo_de

SISTEMA = ("Você escreve o relatório final de uma orquestração de agentes de IA para o Iury, em português do Brasil, "
           "direto e honesto (diga o que NÃO deu certo). Até 10 linhas: o que foi feito, problemas achados e corrigidos, "
           "o que ficou pendente ou arriscado, e a recomendação. Depois, numa linha começando com 'FALA:', 1 ou 2 "
           "frases curtas para ler em voz alta (sem markdown, sem caminhos de arquivo).")


def _duracao(ini: str | None, fim: str | None) -> str:
    try:
        s = (datetime.fromisoformat(fim) - datetime.fromisoformat(ini)).total_seconds()
    except (TypeError, ValueError):
        return "-"
    return f"{s / 60:.1f} min" if s >= 90 else f"{s:.0f} s"


def comparar_testes(passos: list[dict]) -> dict | None:
    testes = [p for p in passos if p["categoria"] == "testes" and p["status"] == "concluido" and p["dados"]
              and not p["dados"].get("sem_testes")]
    if not testes:
        return None
    por_chave = {p["chave"]: p for p in passos}

    def depois_de_escrita(p, vistos=None) -> bool:      # algum passo que escreve vem antes deste?
        vistos = vistos if vistos is not None else set()
        for d in p.get("depende") or []:
            if d in vistos or d not in por_chave:
                continue
            vistos.add(d)
            if por_chave[d]["escrita"] or depois_de_escrita(por_chave[d], vistos):
                return True
        return False
    # a linha de base: "testes_antes" dos modelos prontos, ou (plano feito pela IA) o 1º teste antes de qualquer escrita
    antes = next((p for p in testes if p["chave"] == "testes_antes"), None) or \
        (next((p for p in testes if not depois_de_escrita(p)), None) if any(p["escrita"] for p in passos) else None)
    depois = testes[-1] if testes[-1] is not antes else None
    saida = {"antes": antes["dados"] if antes else None, "depois": depois["dados"] if depois else None}
    if antes and depois:
        fa, fd = set(antes["dados"].get("falhas") or []), set(depois["dados"].get("falhas") or [])
        saida["novas_falhas"] = sorted(fd - fa)
        saida["corrigidas"] = sorted(fa - fd)
    return saida


def _linha_testes(d: dict | None) -> str:
    if not d:
        return "-"
    return ("verde" if d.get("verde") else "com falhas") + f" ({d.get('resumo') or 'código ' + str(d.get('codigo'))})"


def gerar(nucleo, execucao_id: int, com_ia: bool | None = None) -> tuple[str, str]:
    banco = nucleo.banco
    exe = banco.execucao(execucao_id)
    passos = banco.passos(execucao_id)
    plano = exe["plano"] or {}
    linhas = [f"# Orquestração #{exe['id']}: {exe['status']}",
              f"**Pedido:** {exe['pedido']}"]
    if plano.get("objetivo") and plano["objetivo"] != exe["pedido"]:
        linhas.append(f"**Objetivo:** {plano['objetivo']}")
    linhas.append(f"**Modo:** {exe['modo']} · **Projeto:** {Path(exe['projeto']).name if exe['projeto'] else '-'} · "
                  f"**Duração:** {_duracao(exe['inicio'], exe['fim'])} · **Custo estimado:** US$ {exe['custo_usd']:.4f} "
                  f"(cobrado US$ {exe['cobrado_usd']:.4f}) · **Tokens:** {exe['tokens_entrada']:,} entrada / "
                  f"{exe['tokens_saida']:,} saída".replace(",", "."))
    linhas += ["", "## Quem fez o quê", "| Passo | Agente | Status | Tempo | Custo | Por que esse agente |",
               "|---|---|---|---|---|---|"]
    for p in passos:
        nome = nucleo.agentes[p["agente"]].nome if p["agente"] in nucleo.agentes else (p["agente"] or "-")
        linhas.append(f"| {p['titulo'][:60]} | {nome} | {p['status']}{' (' + str(p['tentativas']) + ' tentativas)' if p['tentativas'] > 1 else ''} "
                      f"| {_duracao(p['inicio'], p['fim'])} | US$ {p['custo_usd']:.4f} | {(p['motivo'] or p['erro'] or '-')[:110]} |")
    t = comparar_testes(passos)
    if t:
        linhas += ["", "## Testes", f"- Antes das mudanças: {_linha_testes(t['antes'])}",
                   f"- Depois: {_linha_testes(t['depois'])}"]
        if t.get("corrigidas"):
            linhas.append(f"- Corrigidas: {', '.join(t['corrigidas'][:15])}")
        if t.get("novas_falhas"):
            linhas.append(f"- **Falhas novas (regressão):** {', '.join(t['novas_falhas'][:15])}")
    if exe["projeto"] and exe["checkpoint"]:
        raiz = Path(exe["projeto"])
        try:
            diff = seguranca.resumo_diff(raiz, exe["checkpoint"], seguranca.arvore_atual(raiz))
        except seguranca.ErroGit:
            diff = ""
        linhas += ["", "## Arquivos alterados", f"```\n{diff}\n```" if diff else "Nenhum arquivo foi alterado."]
    elif any(p["arquivos"] for p in passos):
        linhas += ["", "## Arquivos alterados"] + [f"- {a}" for p in passos for a in p["arquivos"][:30]]
    alertas = [l["conteudo"] for l in banco.logs(execucao_id, limite=5000) if l["tipo"] == "alerta"]
    conflitos = [(p["chave"], c) for p in passos for c in p["conflitos"]]
    if alertas or conflitos:
        linhas += ["", "## Conflitos e alertas"] + [f"- {a[:300]}" for a in alertas[:10]] + \
                  [f"- conflito entre '{k}' e '{c['com']}' em {', '.join(c['arquivos'][:5])}" for k, c in conflitos[:10]]
    julg = next((p for p in passos if p["categoria"] == "julgamento" and p["resultado"]), None)
    if julg:
        m = re.search(r"(?i)ESCOLHA\s*:\s*(.+)", julg["resultado"])
        linhas += ["", "## Escolha entre as propostas", (m.group(1).strip() if m else resumo_de(julg["resultado"], 500))]
    linhas += ["", "## O que cada agente relatou"]
    for p in passos:
        if p["resultado"] and p["categoria"] != "testes":
            linhas.append(f"**{p['titulo']}** ({p['agente']}): {resumo_de(p['resultado'], 600)}")
        elif p["status"] in ("falhou", "bloqueado") and p["erro"]:
            linhas.append(f"**{p['titulo']}**: não deu certo: {p['erro'][:300]}")
    fala = f"A orquestração {exe['id']} terminou como {exe['status']}."
    usar_ia = nucleo.relatorio_com_ia if com_ia is None else com_ia
    if usar_ia and any(p["status"] == "concluido" for p in passos):
        corpo = nucleo.redator("\n".join(linhas))[:14000]
        try:
            r = nucleo.pensar(corpo, SISTEMA, nucleo.cfg)
            nucleo.custos.registrar("nucleo", execucao_id, r.custo, False, r.entrada, r.saida)
            m = re.search(r"(?im)^\s*FALA\s*:\s*(.+)$", r.texto)
            resumo = re.sub(r"(?im)^\s*FALA\s*:.*$", "", r.texto).strip()
            if m:
                fala = m.group(1).strip()
            if resumo:
                linhas[2:2] = ["", "## Resumo do Jarvis", resumo]
        except Exception:
            pass
    if exe["checkpoint"]:
        linhas += ["", f"_Para desfazer tudo: Central > Orquestração > Desfazer (ou \"desfaz a orquestração {exe['id']}\"). "
                       f"Checkpoint git {exe['checkpoint'][:10]}._"]
    return "\n".join(linhas), fala
