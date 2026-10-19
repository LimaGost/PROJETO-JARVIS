"""OpenAI Codex CLI como agente: `codex exec --json` (modo não interativo, eventos JSONL).

Conferido no código do Codex (codex-rs/exec/src/exec_events.rs) e na CLI 0.163:
  thread.started{thread_id} · turn.started · item.started/updated/completed{item:{id, type, ...}} ·
  turn.completed{usage:{input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens}} ·
  turn.failed{error:{message}} · error{message} (também usado para avisos não fatais, ex. "Reconnecting... 2/5")
  itens: agent_message{text} · reasoning{text} · command_execution{command, aggregated_output, exit_code, status} ·
         file_change{changes:[{path, kind}], status} · mcp_tool_call · web_search · todo_list · error
Segurança: sandbox do próprio Codex (read-only para análise, workspace-write para escrever: só a pasta do projeto,
sem rede) e approval_policy=never (o que precisaria de aprovação falha em vez de esperar). O pedido vai pelo stdin
("-"): sem isso o `codex exec` fica esperando a entrada quando não há terminal.
Retomar: `codex exec resume <thread_id>` (a sandbox vai por -c sandbox_mode=..., que o resume aceita).
Os valores do -c vão sem aspas (o Codex usa o texto literal quando não é TOML): nada de aspas na linha de comando.
"""
import time

from jarvis.orquestra import gateway
from jarvis.orquestra.agentes.base import Controle, Disponibilidade, Resultado, Trabalho, agente
from jarvis.orquestra.agentes.cli import AgenteCLI
from jarvis.orquestra.seguranca import Redator, ambiente_limpo


@agente
class Codex(AgenteCLI):
    id = "codex"
    nome = "Codex"
    provedor = "OpenAI"
    icone = "code"
    descricao = "Implementa, corrige e refatora código direto no projeto, com sandbox própria. Usa o seu login."
    descricao_semantica = ("implementar código, gerar funções, corrigir bugs, refatorar, otimizar desempenho, "
                           "escrever testes, executar tarefas de desenvolvimento no repositório")
    capacidades = {"implementacao": 0.95, "depuracao": 0.9, "refatoracao": 0.9, "revisao": 0.85, "analise": 0.8,
                   "planejamento": 0.75, "documentacao": 0.7, "consulta": 0.7, "julgamento": 0.7, "testes": 0.6}
    pode_ler_projeto = pode_editar = suporta_retomar = True
    custo_modo = "assinatura"
    latencia = "media"
    comando_base = "codex"
    versao_minima = "0.40.0"
    docs = "https://developers.openai.com/codex/noninteractive"
    instalar = "npm install -g @openai/codex"
    login = "codex login (abre o navegador; entre com a conta do ChatGPT ou use uma chave de API)"

    @property
    def login_cmd(self) -> list[str]:
        cmd = self.comando()
        return [*cmd, "login"] if cmd else []

    def _autenticacao(self) -> Disponibilidade:
        d = self.detectar()
        _, saida, erros = gateway.rodar([*d.comando, "login", "status"], env=ambiente_limpo(), limite_s=30)
        texto = Redator()((saida + " " + erros).strip())
        baixo = texto.lower()
        if "not logged in" in baixo or "não" in baixo and "login" in baixo:
            return Disponibilidade("desconectado", "sem login: clique em Login (codex login)", False)
        if "logged in" in baixo:
            chave = "api key" in baixo
            return Disponibilidade("disponivel", "logado com " + ("chave de API" if chave else "a conta do ChatGPT"),
                                   True, "chave" if chave else "assinatura")
        return Disponibilidade("disponivel", f"login não confirmado ({texto[:80]})", None, "assinatura")

    def argumentos(self, trabalho: Trabalho) -> list[str]:
        sandbox = "workspace-write" if trabalho.escrita else "read-only"
        modelo = trabalho.modelo or self.modelo_padrao()
        if trabalho.retomar and gateway.caminho_em_args_ok(trabalho.retomar):
            a = ["exec", "resume", trabalho.retomar, "--json", "--skip-git-repo-check", "-c", f"sandbox_mode={sandbox}"]
        else:
            a = ["exec", "--json", "--skip-git-repo-check", "-s", sandbox]
            if trabalho.cwd:
                a += ["-C", str(trabalho.cwd)]
        a += ["-c", "approval_policy=never"]     # sem aspas: o Codex usa o texto literal se não for TOML
        if modelo and gateway.caminho_em_args_ok(modelo):
            a += ["-m", modelo]
        return a + ["-"]

    def preco(self, modelo: str) -> tuple[float, float] | None:
        tabela = (self.cfg_geral.get("orquestra", {}) or {}).get("precos_usd_por_milhao") or {}
        par = tabela.get(modelo) if modelo else None
        return (float(par[0]), float(par[1])) if par and len(par) == 2 else None

    def executar(self, trabalho: Trabalho, controle: Controle) -> Resultado:
        d = self.detectar()
        if not d.instalado:
            return Resultado(False, erro=d.detalhe)
        metodo = self.disponibilidade().metodo
        modelo = trabalho.modelo or self.modelo_padrao()
        r = Resultado(False, cobrado=(metodo == "chave"), modelo=modelo or "padrão do Codex")
        mensagens: list[str] = []
        estado = {"turno_ok": False, "falha": "", "ultimo_erro": ""}

        def ao_json(ev: dict):
            tipo = ev.get("type")
            item = ev.get("item") or {}
            it = item.get("type")
            if tipo == "thread.started":
                r.sessao = ev.get("thread_id")
                controle.evento("sistema", f"sessão {r.sessao} · sandbox "
                                           f"{'workspace-write' if trabalho.escrita else 'read-only'}", sessao=r.sessao)
            elif tipo == "item.started" and it == "command_execution":
                controle.evento("comando", str(item.get("command", ""))[:1000])
            elif tipo == "item.completed":
                if it == "agent_message":
                    mensagens.append(str(item.get("text", "")))
                    controle.evento("texto", str(item.get("text", ""))[:1500])
                elif it == "reasoning":
                    controle.evento("pensamento", str(item.get("text", ""))[:600])
                elif it == "command_execution":
                    r.comandos.append(str(item.get("command", "")))
                    saida = str(item.get("aggregated_output", ""))[-600:]
                    controle.evento("comando_fim", f"{item.get('command', '')} → código {item.get('exit_code')} "
                                                   f"({item.get('status')})\n{saida}"[:1500])
                elif it == "file_change":
                    for ch in item.get("changes") or []:
                        caminho = str(ch.get("path", ""))
                        if caminho and caminho not in r.arquivos:
                            r.arquivos.append(caminho)
                        controle.evento("arquivo", f"{ch.get('kind')}: {caminho} ({item.get('status')})")
                elif it == "mcp_tool_call":
                    controle.evento("ferramenta", f"MCP {item.get('server')}/{item.get('tool')} ({item.get('status')})")
                elif it == "web_search":
                    controle.evento("ferramenta", f"busca na web: {item.get('query', '')}"[:300])
                elif it == "todo_list":
                    passos = "; ".join(("✓ " if t.get("completed") else "· ") + str(t.get("text", ""))
                                       for t in item.get("items") or [])
                    controle.evento("plano", passos[:1000])
                elif it == "error":
                    controle.evento("aviso", str(item.get("message", ""))[:600])
            elif tipo == "turn.completed":
                u = ev.get("usage") or {}
                r.tokens_entrada += int(u.get("input_tokens") or 0)
                r.tokens_saida += int((u.get("output_tokens") or 0) + (u.get("reasoning_output_tokens") or 0))
                estado["turno_ok"] = True
                controle.evento("uso", f"{r.tokens_entrada} tokens de entrada "
                                       f"({u.get('cached_input_tokens', 0)} em cache), {r.tokens_saida} de saída")
            elif tipo == "turn.failed":
                estado["falha"] = str((ev.get("error") or {}).get("message", "o turno falhou"))
                controle.evento("erro", estado["falha"][:600])
            elif tipo == "error":
                # o Codex também manda aqui avisos que não são fatais ("Reconnecting... 2/5"): quem decide é o turno
                estado["ultimo_erro"] = str(ev.get("message", "erro"))
                controle.evento("aviso", estado["ultimo_erro"][:600])

        inicio = time.time()
        entrada = (trabalho.sistema + "\n\n" if trabalho.sistema else "") + trabalho.prompt   # o exec não tem system prompt
        p, outras = self.rodar_cli([*d.comando, *self.argumentos(trabalho)], trabalho, controle, ao_json, entrada=entrada)
        r.texto = (mensagens[-1] if mensagens else "").strip()
        if estado["turno_ok"] and not estado["falha"]:
            r.ok = True
        else:
            r.erro = (estado["falha"] or estado["ultimo_erro"]
                      or "o Codex terminou sem concluir o turno: " + " | ".join(outras[-3:])[:400])
        preco = self.preco(modelo)
        if preco:
            r.custo_usd = round((r.tokens_entrada * preco[0] + r.tokens_saida * preco[1]) / 1e6, 5)
        r.dados["custo_conhecido"] = bool(preco)
        return self.finalizar(r, p, outras, inicio)
