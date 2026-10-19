"""Claude Code (Anthropic) como agente: `claude -p --output-format stream-json`, com o login da sua assinatura.

Conferido na CLI 2.1.303:
  - só leitura: --tools Read,Glob,Grep + --permission-mode dontAsk (o evento init lista exatamente essas três);
  - escrita: --permission-mode acceptEdits, sem git push/reset/commit/checkout, sem rede (WebFetch/WebSearch);
  - --restricted (quando a versão tem): ferramentas de arquivo presas à pasta, ignora settings de terceiros e não
    deixa escrever em arquivos do git e de configuração;
  - --max-budget-usd: o próprio Claude para ao chegar no limite de custo do passo;
  - o evento init é conferido (fail-closed): ferramenta fora da lista esperada = processo encerrado.
"""
import json
import time

from jarvis.orquestra.agentes.base import Controle, Disponibilidade, Resultado, Trabalho, agente
from jarvis.orquestra.agentes.cli import AgenteCLI
from jarvis.orquestra import gateway
from jarvis.orquestra.seguranca import ambiente_limpo

LEITURA = ("Read", "Glob", "Grep")
ESCRITA = ("Read", "Edit", "Write", "MultiEdit", "Glob", "Grep", "Bash", "PowerShell", "NotebookEdit", "TodoWrite")
PROIBIDAS = ("Bash(git push:*)", "Bash(git reset:*)", "Bash(git commit:*)", "Bash(git checkout:*)", "Bash(git switch:*)",
             "Bash(git rebase:*)", "Bash(git clean:*)", "Bash(git stash:*)", "Bash(git restore:*)", "Bash(rm -rf:*)",
             "Bash(rm -r:*)", "Bash(shutdown:*)", "Bash(format:*)", "Bash(curl:*)", "Bash(wget:*)",
             "PowerShell(git push:*)", "PowerShell(git reset:*)", "PowerShell(git commit:*)", "PowerShell(Remove-Item:*)",
             "PowerShell(Invoke-WebRequest:*)", "PowerShell(Invoke-RestMethod:*)", "WebFetch", "WebSearch")
EDITORES = ("Edit", "Write", "MultiEdit", "NotebookEdit")
REGRAS = ("Você é um agente de programação coordenado pelo Jarvis, o assistente do Iury. Trabalhe só dentro da pasta "
          "atual. Não faça commit nem push, não reescreva o histórico do git, não instale nada fora do projeto. Siga o "
          "padrão de código que já existe. Termine com um resumo curto do que fez e do que encontrou.")
MAX_TURNOS = {"leitura": 25, "escrita": 60}


@agente
class ClaudeCode(AgenteCLI):
    id = "claude_code"
    nome = "Claude Code"
    provedor = "Anthropic"
    icone = "brain"
    descricao = "Analisa projetos inteiros, arquitetura, revisão e implementação cuidadosa. Usa a sua assinatura."
    descricao_semantica = ("analisar a arquitetura de um projeto de software, entender código grande, revisar com "
                           "cuidado, encontrar problemas, planejar e implementar mudanças com qualidade, documentar")
    capacidades = {"analise": 0.95, "revisao": 0.95, "planejamento": 0.9, "implementacao": 0.9, "depuracao": 0.9,
                   "refatoracao": 0.9, "documentacao": 0.85, "julgamento": 0.85, "consulta": 0.8, "testes": 0.5}
    pode_ler_projeto = pode_editar = suporta_retomar = True
    custo_modo = "assinatura"
    latencia = "media"
    comando_base = "claude"
    versao_minima = "2.0.0"
    ler_flags = True
    docs = "https://docs.claude.com/en/docs/claude-code/cli-reference"
    instalar = "PowerShell: irm https://claude.ai/install.ps1 | iex   (ou: npm install -g @anthropic-ai/claude-code)"
    login = "claude auth login (abre o navegador; use a conta da sua assinatura)"

    @property
    def login_cmd(self) -> list[str]:
        cmd = self.comando()
        return [*cmd, "auth", "login"] if cmd else []

    def modelo_padrao(self) -> str:
        return str(self.cfg.get("modelo") or (self.cfg_geral.get("claude_cli", {}) or {}).get("modelo") or "sonnet")

    def modelos(self) -> list[str]:
        return list((self.cfg_geral.get("claude_cli", {}) or {}).get("modelos") or ["opus", "sonnet", "haiku"])

    def _autenticacao(self) -> Disponibilidade:
        d = self.detectar()
        codigo, saida, _ = gateway.rodar([*d.comando, "auth", "status"], env=ambiente_limpo(), limite_s=30)
        try:
            info = json.loads(saida)
        except ValueError:
            return Disponibilidade("disponivel", "não consegui ler o login (versão sem 'auth status'); vou tentar usar",
                                   None, "assinatura")
        if not info.get("loggedIn"):
            return Disponibilidade("desconectado", "sem login: clique em Login (claude auth login)", False)
        chave = "api" in str(info.get("authMethod", "")).lower() and "oauth" not in str(info.get("authMethod", "")).lower()
        return Disponibilidade("disponivel", "logado com " + ("chave de API" if chave else "a sua assinatura"), True,
                               "chave" if chave else "assinatura")

    # ------------------------------------------------------------ execução
    def argumentos(self, trabalho: Trabalho, flags: set) -> list[str]:
        modelo = trabalho.modelo or self.modelo_padrao()
        a = ["-p", "--output-format", "stream-json", "--verbose", "--model", modelo, "--strict-mcp-config",
             "--max-turns", str(MAX_TURNOS["escrita" if trabalho.escrita else "leitura"])]
        if "--restricted" in flags:
            a.append("--restricted")
        if trabalho.escrita:
            a += ["--tools", ",".join(ESCRITA), "--permission-mode", "acceptEdits",
                  "--allowedTools", ",".join(ESCRITA), "--disallowedTools", ",".join(PROIBIDAS)]
        else:
            a += ["--tools", ",".join(LEITURA), "--permission-mode", "dontAsk", "--allowedTools", ",".join(LEITURA)]
        if trabalho.limite_usd and "--max-budget-usd" in flags:
            a += ["--max-budget-usd", f"{max(0.05, trabalho.limite_usd):.2f}"]
        if "--disable-slash-commands" in flags:
            a.append("--disable-slash-commands")
        a += ["--append-system-prompt", REGRAS + ("\n\n" + trabalho.sistema if trabalho.sistema else "")]
        if trabalho.retomar and gateway.caminho_em_args_ok(trabalho.retomar):
            a += ["--resume", trabalho.retomar]
        return a

    def executar(self, trabalho: Trabalho, controle: Controle) -> Resultado:
        d = self.detectar()
        if not d.instalado:
            return Resultado(False, erro=d.detalhe)
        metodo = self.disponibilidade().metodo
        r = Resultado(False, cobrado=(metodo == "chave"), modelo=trabalho.modelo or self.modelo_padrao())
        permitidas = set(ESCRITA if trabalho.escrita else LEITURA)
        textos: list[str] = []
        estado = {"resultado": False, "bloqueado": False}

        def ao_json(ev: dict):
            tipo = ev.get("type")
            if tipo == "system" and ev.get("subtype") == "init":
                r.sessao = ev.get("session_id") or r.sessao
                r.modelo = ev.get("model") or r.modelo
                ferramentas = set(ev.get("tools") or [])
                estranhas = sorted(ferramentas - permitidas)
                if estranhas:     # fail-closed: carregou algo que não pedimos (MCP, rede...): não deixa trabalhar
                    r.erro = f"o Claude carregou ferramentas não autorizadas ({', '.join(estranhas)[:200]}); parei"
                    estado["bloqueado"] = True
                    controle.evento("erro", r.erro)
                    if controle.processo is not None:
                        controle.processo.cancelar(r.erro)
                    return
                controle.evento("sistema", f"sessão {r.sessao} · modelo {r.modelo} · ferramentas {', '.join(sorted(ferramentas))}",
                                sessao=r.sessao)
            elif tipo == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "text" and b.get("text", "").strip():
                        textos.append(b["text"])
                        controle.evento("texto", b["text"][:1500])
                    elif b.get("type") == "tool_use":
                        nome, entrada = b.get("name", ""), b.get("input") or {}
                        if nome in EDITORES:
                            caminho = entrada.get("file_path") or entrada.get("notebook_path") or ""
                            if caminho and caminho not in r.arquivos:
                                r.arquivos.append(caminho)
                            controle.evento("arquivo", f"{nome}: {caminho}")
                        elif nome in ("Bash", "PowerShell"):
                            comando = str(entrada.get("command", ""))
                            r.comandos.append(comando)
                            controle.evento("comando", comando[:1000])
                        else:
                            alvo = entrada.get("file_path") or entrada.get("pattern") or entrada.get("path") or ""
                            controle.evento("ferramenta", f"{nome} {alvo}"[:300])
            elif tipo == "user":
                for b in (ev.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        conteudo = b.get("content")
                        texto = conteudo if isinstance(conteudo, str) else json.dumps(conteudo, ensure_ascii=False)
                        controle.evento("aviso", f"ferramenta recusou/falhou: {texto[:400]}")
            elif tipo == "result":
                estado["resultado"] = True
                u = ev.get("usage") or {}
                r.tokens_entrada = int((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                                       + (u.get("cache_creation_input_tokens") or 0))
                r.tokens_saida = int(u.get("output_tokens") or 0)
                r.custo_usd = round(float(ev.get("total_cost_usd") or 0), 5)
                r.sessao = ev.get("session_id") or r.sessao
                r.texto = str(ev.get("result") or "").strip() or "\n".join(textos[-2:])
                sub = ev.get("subtype")
                if ev.get("is_error") or sub not in (None, "success"):
                    r.erro = {"error_max_turns": "a tarefa ficou grande demais para os turnos permitidos",
                              "error_max_budget_usd": "chegou no limite de custo do passo"}.get(
                        sub, r.texto[:300] or str(sub))
                    r.ok = False
                else:
                    r.ok = True
                controle.evento("uso", f"{r.tokens_entrada} tokens de entrada, {r.tokens_saida} de saída, "
                                       f"US$ {r.custo_usd:.4f}", custo=r.custo_usd)

        inicio = time.time()
        p, outras = self.rodar_cli([*d.comando, *self.argumentos(trabalho, d.flags)], trabalho, controle, ao_json)
        if not estado["resultado"] and not r.erro:
            r.erro = "o Claude Code terminou sem resultado: " + " | ".join(outras[-3:])[:400]
            r.texto = "\n".join(textos[-2:])
        r = self.finalizar(r, p, outras, inicio)
        if estado["bloqueado"]:
            r.cancelado = False          # foi a trava de segurança, não você
        return r
