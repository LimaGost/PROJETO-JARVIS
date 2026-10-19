# JARVIS AI: orquestrador de agentes e CLIs (AI Command Center)

O Jarvis coordena outras IAs como uma equipe. Você pede uma vez ("analise meu projeto, encontre problemas, corrija os
erros e melhore o desempenho") e ele faz o resto:
1. entende o pedido;
2. monta um plano em passos;
3. escolhe o melhor agente para cada passo e roda em paralelo o que dá;
4. confere com os testes de verdade, corrige se quebrou;
5. entrega um relatório.

Tudo fica auditado, com limite de custo e com um checkpoint git que desfaz tudo num clique.

- **Por voz** (com o Claude como cérebro): "usa as IAs para corrigir o login", "peça ao Claude Code para revisar e ao
  Codex para implementar", "faça os agentes discutirem e escolha a melhor", "como está a orquestração?",
  "desfaz a orquestração 3", "passa para o modo automático".
- **Pela Central** (botão **Central** no topo da HUD), três abas novas:
  - **Agentes IA:** um cartão por agente; modo; custos e limites.
  - **Orquestração:** novo pedido, fluxo ao vivo, aprovar/pausar/cancelar/desfazer e o relatório.
  - **Terminal:** CLIs, terminal integrado, projetos autorizados e servidores MCP.

## O que é real e o que não foi verificado (2026-10-19)

| Situação | Agente / peça | Como foi conferido |
|---|---|---|
| ✅ Verificado de verdade | **Claude Code** (`claude -p --output-format stream-json`) | CLI 2.1.303 com login. A demonstração real: análise, correção e otimização num projeto git, testes de 1 falha → 5 passando, relatório, US$ 0,018 com o haiku. |
| ✅ Verificado de verdade | **Executor de testes** (pytest / npm test) | Roda os testes antes e depois; conta o que passou e o que falhou. |
| ✅ Verificado de verdade | **MCP Gateway** (cliente stdio, spec 2025-06-18) | Servidor oficial `@modelcontextprotocol/server-everything` (13 ferramentas, `echo` funcionando) e servidor de teste com paginação. |
| ✅ Verificado de verdade | Checkpoint/desfazer, terminal, travas, custos, painel | Testes automáticos (`tests/test_orquestra.py`, 49 testes) e a demonstração. |
| ⚠️ Pronto, sem execução real aqui | **Codex** (`codex exec --json`) | Lido no código do Codex (`exec_events.rs`). Na CLI 0.163 foram conferidos `--help` e `login status`. Sem login nem rede para a OpenAI aqui. O formato de eventos é testado com o simulador. Primeiro uso: `codex login` e a demonstração real. |
| ⚠️ Pronto, sem execução real aqui | **Gemini CLI** (`gemini -p -o json`) | Detecção conferida (0.64). Sem login. |
| ⚠️ Pronto, sem execução real aqui | **Ollama**, **OpenRouter**, **GPT (Responses API)** | Feitos pela documentação oficial. Sem Ollama aberto e sem chaves aqui. |
| 🧪 Simulado (rotulado) | `jarvis/orquestra/simuladores/cli_simulado.py` | CLIs falsas de Claude Code e Codex no formato REAL de eventos, para a demonstração `--simulado` e os testes. Toda resposta diz **[SIMULADO]**, e o cartão na Central mostra o selo **SIMULADO**. |

## Como rodar a demonstração

```bat
.venv\Scripts\python.exe -m jarvis.orquestra.demo --simulado             :: sem login, sem custo (CLIs simuladas)
.venv\Scripts\python.exe -m jarvis.orquestra.demo --modelo haiku         :: REAL: as CLIs instaladas, gastando pouco
.venv\Scripts\python.exe -m jarvis.orquestra.demo --agente implementacao=codex   :: força o Codex a implementar
```

A demonstração faz isto:
1. Cria `data/orquestra/demo/loja-exemplo`, um repositório git com um bug de desconto (10% vira -900) e uma busca
   O(n²).
2. Pede "analise meu projeto, encontre problemas, corrija os erros e melhore o desempenho".
3. Mostra o log ao vivo, quem fez o quê e por quê, o relatório e uma conferência independente (`git diff --stat` +
   `pytest`).

O banco da demonstração é separado (`data/orquestra/demo/*.db`).

## Arquitetura

```
voz/texto ─► cérebro Claude ─► ferramentas.py (orquestrar, aprovar_plano, status, controlar, desfazer, modo, MCP...)
HUD ─WebSocket─► hud/servidor.py ─► connect/central.py ─► orquestra/painel.py (ops "orq_*")
                                                              │
                                              nucleo.py  (Jarvis Core: modos, projetos, aprovar, desfazer)
        ┌───────────────┬──────────────┬──────────────┼──────────────┬──────────────┬──────────────┐
  planejador.py    roteador.py    execucao.py     contexto.py     custos.py      monitor.py
  (plano: IA       (Intelligent   (Task           (Context        (Cost          (Execution
   isolada ou       Router)        Orchestrator:   Manager)        Manager)       Monitor: logs,
   modelos)                        DAG, travas,                                   HUD ao vivo)
                                   reparo)
        └──────────────── agentes/ (Agent Registry: um arquivo por agente, @agente) ────────────────┘
          claude_code · codex · gemini (CLIs) │ ollama · openrouter · openai (APIs) │ testes (local)
                         │
                    gateway.py (CLI Gateway: processo isolado, limites de tempo/memória, pausar, matar)
                    seguranca.py (Security Manager: pastas, segredos, comandos, checkpoint git)
                    mcp.py (MCP Gateway) · terminal.py (terminal + gerenciador de CLIs) · banco.py (SQLite)
```

### Adicionar uma IA nova sem mexer no núcleo
Crie `jarvis/orquestra/agentes/<nome>.py` com uma classe `@agente` que herda de `Agente` (ou `AgenteCLI`). Ela
declara:
- `id`, `nome`, `provedor`;
- `capacidades` (`{categoria: 0..1}`) e `descricao_semantica`;
- `custo_modo`, `pode_editar` e `pode_ler_projeto`;
- os métodos `_detectar()`, `_autenticacao()` e `executar(trabalho, controle) -> Resultado`.

O registro carrega a classe sozinho. O roteador, a Central, os custos e a auditoria passam a tratar o agente como
qualquer outro. As capacidades podem ser ajustadas no `config.yaml` (`orquestra.agentes.<id>.capacidades`).

## Roteamento (não é só palavra-chave)
Para cada passo, cada agente elegível ganha uma nota:

| Peso | Critério |
|---|---|
| 0,40 | **capacidade** declarada na categoria. Com 3+ execuções, ela é corrigida pela taxa real de acerto. |
| 0,25 | **semelhança semântica** entre o passo e o que o agente faz bem. Usa os embeddings do Jarvis (embeddinggemma, na CPU); sem o Ollama, cai para semelhança léxica. |
| 0,20 | **histórico** de sucesso naquela categoria (bayesiano: (acertos+1)/(execuções+2)). |
| 0,10 | **custo**: grátis > assinatura > por token. |
| 0,05 | **latência**. |

Ficam de fora os agentes:
- desligados, desconectados ou sem login;
- que não editam (em passo de escrita);
- que não enxergam os arquivos (em análise de projeto).

Agente citado pelo nome ("peça ao Codex...") é fixo naquele passo. O motivo da escolha vai para o log e o relatório.

## Execução multiagente
- **Dependências:** o plano é um grafo. Passos sem dependência pendente rodam juntos, até
  `orquestra.max_passos_paralelos`.
- **Sem edição simultânea:** um escritor por projeto de cada vez, inclusive entre execuções diferentes. Dois passos de
  escrita só rodam juntos se declararam escopos que não se cruzam. Mesmo assim, se mexerem no mesmo arquivo, o
  conflito vai para o relatório.
- **Falhas:**
  - erro passageiro (rede, limite de taxa): tenta de novo com espera;
  - erro fixo: passa para o próximo agente do ranking;
  - a tentativa que falhou é desfeita antes da próxima.
- **Passo só de leitura que alterou arquivo:** desfeito na hora, com alerta na auditoria.
- **Testes:** rodam antes (linha de base) e depois. Falha nova = o mesmo agente recebe as falhas e corrige (retomando a
  sessão dele quando a CLI permite), e os testes rodam de novo.
- **Pausar, retomar, cancelar** cada passo ou a execução inteira. O processo do agente é congelado ou encerrado com a
  árvore inteira.
- **Fechou o Jarvis no meio?** A execução vira "interrompida" e dá para retomar.

## Modos
- **Automático:** planeja e executa sozinho nos projetos já autorizados. Projeto novo = confirmação uma vez.
  Continuam valendo os limites de custo, o checkpoint, as pastas autorizadas e os comandos bloqueados.
- **Supervisionado** (padrão): mostra o plano e espera você aprovar.
- **Manual:** você escolhe o agente de cada passo e aprova.

## Segurança
- **Só pastas autorizadas.** Nunca a raiz do disco, a pasta do usuário, pastas do sistema ou o E:.
- **Credenciais:**
  - Chaves das APIs ficam no cofre DPAPI (`data/connect/cofre.json`), nunca no `config.yaml`. A HUD só vê
    `••••••1234`.
  - Os agentes CLI usam o login da própria CLI: OAuth oficial (`claude auth login`, `codex login`, login Google do
    Gemini).
- **Ambiente limpo em todo processo de agente:** sem variáveis com chave, token ou senha, sem as de outros provedores
  e sem as de uma sessão do Claude Code que tenha aberto o Jarvis (`CLAUDE_CODE_SESSION_ID` etc.).
- **Nada de segredo nos logs nem no contexto:** um redator troca as chaves do cofre e os padrões de chave (`sk-...`,
  `Bearer ...`) por `[segredo]` antes de gravar, mostrar ou mandar para outro modelo.
- **Claude Code:**
  - só leitura = `Read, Glob, Grep` com `dontAsk`;
  - escrita = `acceptEdits` sem `git push/reset/commit/checkout`, `rm -rf` e rede;
  - `--restricted` e `--max-budget-usd` quando a versão tem;
  - o evento `init` é conferido: ferramenta a mais = processo encerrado.
- **Codex:** sandbox do próprio Codex (`read-only` / `workspace-write`, sem rede) e `approval_policy=never`.
- **Terminal:** roda no backend (o navegador nunca executa nada), só em pasta autorizada. Comando destrutivo
  (`rm -rf`, `git push --force`, `format`, `curl | sh`...) ou externo (`git push`, publicar pacote, `npm i -g`,
  `winget install`) só roda depois de você confirmar.
- **Auditoria:** cada comando, arquivo, ferramenta e custo vai para `data/orquestra/orquestra.db`.
- **Desfazer:** checkpoint git num índice temporário (`refs/jarvis/checkpoints/*`). Desfazer volta os arquivos e
  apaga os criados depois. O seu índice, a sua branch e o seu histórico não mudam.
- **MCP:** permissão por ferramenta. O padrão é 🔴; a dica "só leitura" do servidor vira 🟡. As 🔴 só pela conversa,
  com confirmação.

## Custos
- **Limite por tarefa** (custo estimado, também na assinatura: trava um agente em loop).
- **Limites por dia e por mês** (o que é cobrado de verdade, nas chaves de API). Ajuste em
  `orquestra.custos` ou em **Central > Agentes IA > Limites**.
- O custo vem da própria ferramenta:
  - Claude Code: `total_cost_usd`;
  - OpenRouter: `usage.cost`;
  - Codex e GPT: tokens × `orquestra.precos_usd_por_milhao`.

## Fontes oficiais usadas
- Claude Code CLI: https://docs.claude.com/en/docs/claude-code/cli-reference e o modo headless
  (https://docs.claude.com/en/docs/claude-code/headless).
- Codex não interativo: https://developers.openai.com/codex/noninteractive. Eventos em
  `codex-rs/exec/src/exec_events.rs` (github.com/openai/codex).
- Gemini CLI headless: https://github.com/google-gemini/gemini-cli (docs/cli/headless.md).
- MCP: https://modelcontextprotocol.io/specification/2025-06-18.
- OpenRouter: https://openrouter.ai/docs/api-reference/chat-completion e
  https://openrouter.ai/docs/use-cases/usage-accounting.
- OpenAI Responses API: https://platform.openai.com/docs/api-reference/responses.
- Ollama: https://github.com/ollama/ollama/blob/main/docs/api.md.
