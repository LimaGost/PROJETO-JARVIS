# JARVIS (Fases 1 a 4: voz, controle do computador, memória e HUD)

Assistente 100% local: **"Hey Jarvis"** → ouve → lembra → pensa → age no computador → responde falando → aprende.

## Um Jarvis só (2026-10-17)
- **A conversa não recomeça.** Toda conversa fica gravada no banco; quando um cérebro começa do zero (Jarvis reaberto,
  processo do `claude` reiniciado, reserva local assumindo, troca de mentalidade), o primeiro pedido leva os últimos
  pedidos das últimas 12 h e ele continua de onde parou. Só "esqueça a conversa" corta essa continuidade.
- **Interromper não derruba a conversa:** falar por cima ou "parar tudo" interrompe só o pedido em andamento
  (`control_request interrupt` do `claude`); o processo e o contexto continuam. Antes o processo era morto.
- **A HUD mostra o Jarvis, não as engrenagens:** sem painel de ações, de mentalidade, de permissões, chip do modelo
  e botão "Nova conversa". Ficam o núcleo, a conversa, o sistema, a memória, Mudo e Parar. A autorização 🔴 continua.
- **Uma voz só:** a do Jarvis (XTTS). Na partida ele espera ela ficar pronta antes de falar; se ela cair, espera voltar
  (`tts.reserva_piper: false`). Erros do processo da voz ficam em `data/logs/voz_clonada.log`.
- **Fala emendada:** cada frase é pedida à voz no momento em que o cérebro a escreve, então a próxima já está pronta
  quando a atual acaba. Medido: 0 silêncios entre 4 frases (antes, 3 pausas somando 1,5 s).
- **Conversa corrida:** depois de responder ele escuta por 10 s sem precisar do "Hey Jarvis" (`conversa.janela_followup_s`).

## Programar pelo Jarvis (`programar`, `tarefa_programacao`, `vscode`)
"Jarvis, cria uma página de login", "corrige esse erro", "roda os testes": o pedido vai para o **Claude Code** (Sonnet,
pela assinatura), que trabalha na pasta do projeto aberto no VS Code (título da janela + pastas que o VS Code guarda em
`storage.json`) ou na pasta dita no pedido.
- Pede autorização 🔴 **uma vez por tarefa** e responde na hora; o trabalho roda em segundo plano. Quando acaba, ele
  **avisa por voz** (sem atropelar a conversa), abre no VS Code os arquivos alterados e o cérebro fica sabendo.
- Pode ler, criar e editar arquivos e rodar comandos (testes, npm, python, git), só na pasta do projeto. Sem `git push`,
  `git reset --hard`, `rm -rf`, sem os seus MCPs (Gmail, Hostinger...) e sem web. Recusa raiz do disco, pasta do
  usuário, pastas do Windows e o HD E:. Limite de 20 min por tarefa; uma tarefa por vez.
- Pedidos seguidos no mesmo projeto (em até 2 h) continuam a mesma sessão: "agora muda a cor do botão".
- Medido: criar uma calculadora com teste e rodar levou 54 s; o ajuste seguinte, na mesma sessão, 12 s.
- "Como está a tarefa?" / "cancela a programação" usam `tarefa_programacao`. "O que está aberto no VS Code?" usa `vscode`.

## Conversa natural
- Prompt de conversa: ele participa, comenta e devolve perguntas quando você só quer conversar; ordens ele confirma curto.
- **Resposta instantânea:** se uma ação vai demorar e ele ainda não disse nada, fala na hora "Já cuido disso, senhor"
  (uma vez por pedido; não nas ações instantâneas nem quando vai pedir autorização).
- O chamado varia ("Pois não?", "Sim, senhor?", "Às ordens."...) e as frases curtas repetidas saem da memória, sem gerar de novo.
- Fim da fala detectado em 0,7 s (`vad.silencio_ms`); se ele terminar com uma pergunta, espera sua resposta por 15 s.

## Uso da assinatura (claude-cli)
Cada pedido fica em `data/logs/uso_claude_cli.jsonl` (tokens e custo equivalente em preço de API). Pergunte a ele:
"quanto eu usei do Claude hoje?" (ferramenta `uso_claude`: hoje, últimas 5 h ou semana). Medido com o Sonnet:
pedido simples ~9 mil tokens lidos do cache (~US$ 0,003 equivalente); com uma ferramenta ~18 mil (~US$ 0,005);
com um print da tela ~US$ 0,01. Na assinatura nada disso é cobrado à parte: conta para o limite de uso dela.

## Calar e interromper
- **"Fica quieto"**, "para de falar", "silêncio", "chega", "para, Jarvis", "shh"…: ele para de falar e de fazer, **não responde nada**
  e volta a esperar o "Hey Jarvis". ("Tchau, Jarvis" continua sendo desligar.)
- **Falar por cima**: enquanto ele pensa ou fala, o microfone continua ouvindo. Se for a sua voz, ele pausa na hora,
  ouve a sua frase e responde a ela (o pedido anterior é cancelado). Se era só o eco dele no microfone, retoma do ponto exato.
- **"Hey Jarvis" no meio da fala** sempre interrompe.
- Ajustes em `interrupcao:` no `config.yaml`. Se ele se interromper sozinho (alto-falante alto perto do microfone), suba `fator_eco`
  para 4–5, ou use `por_voz: false` (aí só o "Hey Jarvis" interrompe). Com fone de ouvido funciona melhor.

## Trocar a mentalidade (Qwen ⇄ Claude), a qualquer hora
Dois jeitos, sem reiniciar: por voz/texto ("usa o Claude", "muda para o Qwen", "volta pro local") ou o `cerebro.motor`
do `config.yaml` (só a escolha inicial). O painel da HUD foi tirado (2026-10-17): para o usuário, é um Jarvis só.
- A escolha fica salva em `data/preferencias.json` (ignorado pelo git) e vale na próxima vez que ligar.
- A troca espera o pedido atual acabar, sobe e testa a nova mentalidade **antes** de largar a antiga; se falhar, continua na atual e avisa.
- A conversa continua ao trocar (a nova mentalidade recebe a conversa recente); a memória de longo prazo é a mesma.
- Opções: **Qwen local** (grátis, privado), **Claude · assinatura** (CLI) e **Claude · API** (só aparece disponível com a chave em `data/anthropic.key`).
- Ao sair do Claude, o processo `claude` é encerrado (não fica gastando nada).

## Cérebro `claude-cli` (a sua assinatura, sem chave) — o motor ativo
`cerebro.motor: claude-cli` roda o programa `claude` (Claude Code) por trás, com o login da sua assinatura (hoje: Claude Pro). Ele é só o "pensador": as 31 ferramentas continuam sendo do JARVIS e passam pelo Executor (🟢🟡🔴, confirmação, bloqueio da HUD, log).

```
JARVIS ──stream-json──► claude (processo persistente) ──MCP stdio──► ponte_mcp.py ──HTTP local + token──► Executor do JARVIS
```
- **Isolamento fail-closed.** O processo sobe com `--restricted --strict-mcp-config --tools "" --disable-slash-commands`. O evento `init` é conferido: se aparecer **qualquer** ferramenta que não seja `mcp__jarvis__*`, servidor MCP seu (Gmail, Supabase, Hostinger...), skill ou plugin externo, o JARVIS **recusa usar o cérebro e cai no modelo local**. Testado ao vivo: pedir "use o Bash" ou "leia meu Gmail" não funciona.
- A chave de API (`ANTHROPIC_API_KEY`) é removida do ambiente do processo: o uso é da assinatura.
- Modelo em `claude_cli.modelo`: `sonnet` (padrão, rápido e gasta menos cota), `opus` (mais capaz, gasta ~5x mais cota), `haiku`. `claude_cli.esforco: low` mantém a voz rápida.
- Medido: partida ~2 s; respostas de 2 a 4 s; ~8 s quando há duas ferramentas encadeadas.
- Se o login cair, a cota acabar ou a internet falhar **antes de ele falar**, o modelo local responde aquele pedido (`cerebro.reserva_local`).
- "Parar tudo" e falar por cima só interrompem o pedido; queda do processo reinicia o `claude`, que recebe a conversa recente do banco.
- A cota da assinatura é **compartilhada com o OpenClaw e com o seu uso no Claude**. Um assistente de voz faz muitas chamadas.
- ⚠️ **Zona cinza da política.** A [política da Anthropic](https://code.claude.com/docs/en/legal-and-compliance) diz que o login por assinatura é para "uso ordinário" do Claude Code e apps nativos, e que quem constrói ferramentas deve usar chave de API; permite que um usuário entre no `claude` original com a própria assinatura, e proíbe rotear a assinatura para terceiros ou intermediar credenciais. Este uso é pessoal, com o `claude` sem modificação, mas **não é garantido ser permitido** e a Anthropic pode agir sem aviso. Para sair dessa zona: `cerebro.motor: claude` (chave de API).
- Para voltar ao modelo local: `cerebro.motor: ollama`.

## Cérebro Claude por API (alternativa)
O modelo local de 8B é o gargalo (erra instruções simples, inventa resultados). Dá para trocar o cérebro pelo **Claude (API da Anthropic)** sem mudar mais nada: ferramentas, permissões 🟢🟡🔴, bloqueio da HUD, confirmação e memória continuam no seu computador. Se a internet ou a chave falharem, o modelo local assume (`cerebro.reserva_local`).

**Ativar:**
1. Crie uma chave em console.anthropic.com (é separada da assinatura do Claude/Claude Code, com cobrança por uso).
2. Salve a chave em `data/anthropic.key` (uma linha, fora do git) **ou** na variável de ambiente `ANTHROPIC_API_KEY`. Nunca no `config.yaml`.
3. No `config.yaml`: `cerebro.motor: claude`. Modelo em `claude.modelo`: `claude-opus-5-5` (mais capaz), `claude-sonnet-5-5` (mais rápido e barato) ou `claude-haiku-5-5` (o mais barato). `claude.esforco: low` mantém a voz rápida.
4. Reinicie o Jarvis. O chip no topo da HUD mostra o modelo em uso.

**Custo:** cada pedido envia ~5 mil tokens (prompt + 31 ferramentas), que ficam em cache (leitura ~10% do preço). Cada resposta é registrada em `data/logs/uso_claude.jsonl` (entrada, saída, cache lido/escrito) para você acompanhar.

**Privacidade:** com `motor: claude`, o texto das conversas, os fatos da memória que entram no pedido e o conteúdo de janelas/arquivos lidos pelas ferramentas vão para a Anthropic. Para 100% local, `motor: ollama`.

**Para quem mexer no código:** o histórico enviado é somente-acréscimo (o contexto fica dentro da mensagem do usuário) e usa `prefix_mismatch_behavior: drop_block`; ferramentas e prompt de sistema têm de ser idênticos em todo pedido (cache); nunca `tool_choice` forçado (400 nestes modelos).

## HUD (fase 4)
Abre sozinha como janela de aplicativo (Edge) em **http://127.0.0.1:8765** (se a porta estiver ocupada, usa a próxima: veja a linha "HUD em ..." no console).
Reator animado que reage à sua voz e à dele, conversa ao vivo, painel de memória (filtrar e esquecer), CPU/RAM/GPU/VRAM, caixa de texto, **PARAR TUDO** (Esc) e Mudo. As ações continuam registradas em `data/logs/acoes.jsonl`, só não aparecem na tela.
- **Confirmação 🔴 por voz ou por clique**, o que vier primeiro. O botão Confirmar só libera após 0,7 s; pedido digitado na HUD nunca abre o microfone.
- **Segurança:** só `127.0.0.1`, token + checagem de Host/Origin + CSP; todo texto de modelo/site/arquivo entra como texto puro. **Nunca** mude `hud.host` para `0.0.0.0`.
- **Bandeja do Windows** (abrir HUD, mudo, parar tudo, sair). **Início com o PC:** `python main.py --instalar-inicio` (atalho só com ícone na bandeja) e `--remover-inicio`.
- `--silencioso` não abre a janela. `JARVIS_SEM_JANELA=1` desliga janela e bandeja (testes).

## Visão e mouse de verdade (`ver_tela`, `mouse`): só com o Claude como cérebro
O Jarvis tira um print da tela (`ver_tela`), o Claude enxerga a imagem e usa o mouse (`mouse`: mover, clicar, duplo clique,
botão direito, arrastar, rolar) em qualquer programa, site ou jogo. Depois de cada ação volta um print novo para ele conferir.
- Coordenadas em pixels da última imagem; o código converte para a tela real (2 monitores, escala do Windows). Sem `ver_tela`
  recente (2 min, mesmo pedido) não há clique.
- **Autonomia (escolha de 2026-10-17):** o mouse anda e clica sozinho (🟢). Pede confirmação 🔴 só onde o `clicar` já pede:
  janelas sensíveis (terminal, Registro, UAC, banco, senhas) e botões de risco (excluir, enviar, pagar, instalar, **fechar**...),
  detectados pelo que está debaixo do ponto (acessibilidade do Windows + `WM_NCHITTEST` para o X de apps Electron).
  A HUD continua intocável. Ao contrário do `clicar`, o mouse não passa a pedir confirmação depois de ler um site.
- O Qwen local não recebe essas duas ferramentas (não enxerga imagem). Cada print custa ~1.500 tokens da cota.
- Testado ao vivo: o Claude calculou 7 + 8 na Calculadora só com o mouse em 10 s.

## Controle de qualquer programa (`ver_janela`, `clicar`, `digitar`, `atalho`, `focar_janela`, `rolar`)
Usa a acessibilidade do Windows (UI Automation), dentro do Jarvis, sem servidor externo. O nível é decidido pelo **contexto**, não pelo clique:
- **A HUD do Jarvis é intocável** (senão ele clicaria no "Confirmar" dela e se autorizaria).
- 🔴 em terminais, Registro, UAC, janelas de banco/pagamento/senhas; em botões de risco (excluir, enviar, pagar, instalar, **fechar**, sair...); em atalhos como Win+R, Alt+F4, Ctrl+Alt+Del.
- Os números de `ver_janela` expiram a cada pedido e em 2 min. Depois de agir, a ferramenta devolve **o que a janela mostra** (o modelo já inventou "deu 1023" depois de clicar errado).
- Janela de navegador/e-mail/chat/documento = texto de terceiros: marcado como não confiável e as ações seguintes pedem confirmação.
- **Limite do modelo de 8B:** `digitar` no teclado ("12*12=") é confiável; clicar traduzindo números em nomes de botão ("Um, Dois") erra. O prompt orienta o teclado.
- Opt-in ao vivo: `JARVIS_TESTE_UI=1 pytest tests/test_interface.py` opera a Calculadora de verdade.

```
microfone → openWakeWord → webrtcvad → faster-whisper (GPU) → Qwen3 8B (Ollama) ⇄ ferramentas → Piper TTS → alto-falante
                                                                   ↑      ↓              ↑
                                         memória (SQLite + embeddinggemma na CPU)   permissões 🟢🟡🔴 + log
                                                                   ↑
                                                  reflexão: aprende com as conversas
```

## Como usar
O Ollama precisa estar aberto.

- **Voz:** dois cliques em `iniciar.bat`. Diga **"Hey Jarvis"**, espere o "Pois não?" e fale. Depois da resposta há 10 s para continuar sem repetir o chamado.
- **Texto:** `texto.bat` (ou `python main.py --texto`). Digite em vez de falar; não carrega microfone nem Whisper. `--texto --falar` também responde em voz.
- **"Tchau Jarvis"**, **"Desligar Jarvis"** ou **"Pode dormir"** encerram. **"Esqueça a conversa"** recomeça. Ctrl+C também encerra.

## Memória (fase 3)
- **A cada pedido** o Jarvis recebe seu perfil (sempre) + os 5 fatos mais relevantes ao que você disse + o resumo de um dia anterior, se tiver relação. Vai só na mensagem atual, então o cache do prompt continua valendo e a latência não sobe.
- **Explícito:** "lembre que minha mãe se chama Maria" (`memorizar`), "esqueça que eu gosto de pizza" (`esquecer_fato`), "o que conversamos ontem?" (`lembrar`).
- **Aprendizado sozinho (reflexão):** relê as conversas, extrai fatos (nome, família, trabalho, rotina, preferências, apelidos como "o painel = site X", correções), junta repetidos, **atualiza o que mudou** (mudou de cidade → substitui) e escreve um resumo do dia. Roda quando o Jarvis está ocioso: dias anteriores logo que ele abre; conversas de hoje depois de 15 min sem falar com ele. Se você chamar no meio, ela pausa.
- **Ver e editar o que ele sabe:**
  ```
  .venv\Scripts\python.exe -m jarvis.memoria listar
  .venv\Scripts\python.exe -m jarvis.memoria apagar 12
  .venv\Scripts\python.exe -m jarvis.memoria dias
  .venv\Scripts\python.exe -m jarvis.memoria refletir --hoje
  ```
- **Proteções:** só o que **você** disse vira fato (conteúdo de sites e arquivos não entra na reflexão); `memorizar`/`esquecer_fato` são 🟡, então depois de ler um site ou arquivo pedem confirmação; senhas, cartões e CPF nunca são guardados; "esquecer" apaga de verdade.
- Tudo fica em `data/memoria/jarvis.db` (fora do git). `JARVIS_MEMORIA=outro.db` usa outro banco (testes).

## O que ele faz (31 ferramentas + `ver_tela` e `mouse` com o Claude)
| Área | Ferramentas | Nível |
|---|---|---|
| Informação | `hora_data`, `info_sistema` (CPU, RAM, discos, GPU e temperatura, IP local), `listar_janelas`, `calcular` | 🟢 |
| Programas | `abrir_programa` (inclui apps da Store: Spotify, WhatsApp...), `fechar_programa` (como clicar no X) | 🟢 / 🟡 |
| Som | `volume`, `controle_midia` (pausar/continuar, próxima, anterior) | 🟢 |
| Memória | `lembrar` 🟢, `memorizar` e `esquecer_fato` 🟡 | 🟢 / 🟡 |
| Web | `pesquisar_web`, `abrir_site` (só http/https) | 🟢 |
| Arquivos | `listar_pasta`, `buscar_arquivos`, `ler_arquivo`, `abrir_pasta_ou_arquivo` | 🟢 (executável 🔴) |
| | `criar_pasta`, `escrever_arquivo`, `mover`, `copiar` | 🟡 (sobrescrever ou área do sistema 🔴) |
| | `apagar` (sempre para a Lixeira) | 🔴 |
| Energia | `energia`: bloquear / cancelar desligamento 🟢, suspender / reiniciar / desligar 🔴 | 🟢 / 🔴 |
| Resto | `executar_powershell`: nível decidido pela análise do comando | 🟢 / 🟡 / 🔴 |

**Apelidos de pasta:** "área de trabalho", "documentos", "downloads", "imagens"... Nesta máquina Área de Trabalho e Documentos ficam no **OneDrive**; o código resolve isso pela API do Windows.

## Segurança: como as permissões funcionam
- **Quem decide o nível é o código, nunca o LLM.** Cada ferramenta avalia os argumentos reais antes de executar.
- 🟢 executa · 🟡 executa e grava no log · 🔴 pergunta "Confirma?" e só executa com um **sim claro**. Silêncio, dúvida ou qualquer "não" = cancela.
- Na tela aparece o pedido completo antes de você confirmar.
- Depois de um "não", outras ações 🔴 no mesmo pedido são bloqueadas sem perguntar de novo.
- **Nunca, nem com confirmação:** apagar ou mover raízes de disco, a pasta do usuário e as pastas conhecidas (Área de Trabalho, Documentos...). Mover/copiar nunca sobrescreve.
- **Áreas protegidas** (escrever = 🔴): Windows, Program Files, ProgramData, AppData (inclui a pasta Inicializar) e a própria pasta do Jarvis.
- **PowerShell:** o comando é analisado pelo parser oficial do PowerShell (`ps_analisar.ps1`), sem executar. Política de **lista de liberação**: só leitura conhecida é 🟢; tudo desconhecido, nome de comando montado em tempo de execução (`& ("Re"+"move-Item")`), chamadas .NET, `.Delete()`, `iex`, `Start-Process` etc. é 🔴. Comando com erro de sintaxe é recusado.
- **Injeção de instruções:** conteúdo de arquivos, sites e saída do PowerShell vai ao modelo marcado como não confiável. Depois de ler conteúdo externo, **toda ação 🟡 passa a pedir confirmação por 3 pedidos** (um arquivo malicioso não consegue mandar o Jarvis criar ou fechar coisas sozinho).
- **Ações inventadas:** se o modelo disser que fez algo ("o volume foi ajustado", "abro a calculadora", "vou verificar") sem ter chamado ferramenta, a frase é descartada antes de ser falada e ele é obrigado a refazer.
- **Log:** toda ação (inclusive as negadas) vai para `data/logs/acoes.jsonl`.

## Ajustes (`config.yaml`)
| Sintoma | Ajuste |
|---|---|
| Não reage ao "Hey Jarvis" | `wakeword.limiar` para 0.3 |
| Corta sua fala no meio / não te ouve | `vad.agressividade` para 2, ou aumente o ganho do microfone no Windows |
| Demora a perceber que você terminou | `vad.silencio_ms` para 700 |
| Erra nomes de apps | acrescente em `stt.vocabulario` |
| Respostas sem graça / repetitivas | `llm.temperatura` para 0.5 (piora um pouco a escolha de ferramentas) |
| Quer mais precisão e aceita +450 MB de VRAM | `stt.modelo: large-v3-turbo`, `stt.tipo: int8_float16` |

## Armadilhas já resolvidas (não desfazer)
- **Carregar bibliotecas nativas (numpy, onnxruntime, ctranslate2) ANTES de criar qualquer thread** (`precarregar` em `main.py`): no Windows, criar thread durante o carregamento de uma DLL trava o processo. A HUD criava threads e pendurava o Jarvis no `import numpy`.
- **Nunca chame o Ollama sem `num_ctx` e `keep_alive` iguais aos do Jarvis** (6144 / -1): qualquer diferença descarrega e recarrega o modelo (~15 s) e troca o "Forever" por 5 min.
- Janelas somem entre a listagem e a leitura (`COMError`): a enumeração de janelas é tolerante a isso.
- **`llm.host` precisa ser `127.0.0.1`**, nunca `localhost`: no Windows isso custa +2 s por pergunta (tenta IPv6 primeiro).
- **Caminho com acento** (`PROGRAMAÇÃO`) quebra o espeak do Piper; `tts.py` copia os dados para `C:\ProgramData\jarvis_espeak`.
- **Modelo fixo e prompt aquecido** (`keep_alive: -1`, `Cerebro.aquecer` com as ferramentas), senão a 1ª pergunta paga segundos.
- **`llm.contexto: 6144`**: prompt + 24 ferramentas já usam ~3.100 tokens. 8192 aperta a VRAM; 4096 não sobra espaço. Cada ferramenta nova custa ~100 tokens.
- **Nome e descrição de ferramenta importam:** com `abrir_arquivo` o modelo não abria pastas (chamava uma ferramenta inexistente e o Ollama descartava em silêncio); com opções `tocar_pausar/proxima/parar` sem descrição, "pula essa música" virou `parar`. Ao mudar nomes/descrições, rode a avaliação de novo.
- **Embeddings na CPU** (`num_gpu: 0`): na GPU o Ollama tiraria o Qwen da memória. `nomic-embed-text` é ruim em português; use `embeddinggemma`.
- **Reflexão usa o mesmo `num_ctx` do Jarvis**: num_ctx diferente faz o Ollama recarregar o modelo.
- **Consolidação de fatos não confia só no modelo:** fatos de categorias incompatíveis nunca se fundem; "substitui" usa a frase nova (o modelo misturava "mora em Valinhos. mora em Campinas."); juntar que perde nome/número/site é recusado.
- VRAM com tudo carregado: ~7,5 de 8,2 GB. Feche apps pesados de GPU antes de usar.
- **Com o Claude como cérebro, o Qwen não mora na GPU** (`llm.keep_alive_reserva: 2m`): ele só sobe se o Claude falhar ou
  para a reflexão da memória, e sai 2 min depois. Antes ficava carregado para sempre junto com a voz clonada e o Whisper,
  estourava os 8 GB, o Windows jogava o excesso para a RAM e o PC travava.
- **As ferramentas rodam em threads** (ponte do claude-cli): a UI Automation precisa de `InitializeUIAutomationInCurrentThread`
  em cada uma (`interface._exigir`).

## Testes
```
.venv\Scripts\python.exe -m pytest tests -q
```
669 testes: programar pelo VS Code, resposta instantânea e avisos, voz única e fala emendada, continuidade da conversa, uso da assinatura, visão e mouse (coordenadas, travas, imagem chegando ao Claude), calar e interromper (comandos, detector de eco, escuta em paralelo), seletor de mentalidade (voz, escolha, troca com falha segura, persistência), claude-cli (isolamento, ponte HTTP e MCP, processo com `claude` falso, reserva), cérebro Claude por API, HUD (autenticação, confirmação por voz/clique, parar tudo), controle de interface, guarda do PowerShell (incluindo tentativas de burla), arquivos, executor/permissões, comandos de voz, sim/não, calculadora, detector de ações inventadas e memória (banco, segredos, consolidação, reflexão, ferramentas).

## Medidas (RTX 3050 8 GB)
- Conversa: transcrição ~0,4 s · 1ª frase ~0,35 s (já com a busca na memória, ~30 ms) · LLM 37 tokens/s.
- Com ferramenta: ~1,35 s em média até a resposta (duas chamadas ao modelo); pesquisa web ~3,5 s.
- Escolha da ferramenta certa: 47/47 pedidos em português, em duas rodadas seguidas, sem nenhuma ação inventada.
- Reflexão de um dia de conversa: ~15 s.

## Próximas fases
4. Interface HUD web
5. Visão de tela + proatividade (lembretes, alertas)
