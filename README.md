# JARVIS (Fases 1 a 3: voz, controle do computador e memória)

Assistente 100% local: **"Hey Jarvis"** → ouve → lembra → pensa → age no computador → responde falando → aprende.

```
microfone → openWakeWord → webrtcvad → faster-whisper (GPU) → Qwen3 8B (Ollama) ⇄ ferramentas → Piper TTS → alto-falante
                                                                   ↑      ↓              ↑
                                         memória (SQLite + embeddinggemma na CPU)   permissões 🟢🟡🔴 + log
                                                                   ↑
                                                  reflexão: aprende com as conversas
```

## Como usar
O Ollama precisa estar aberto.

- **Voz:** dois cliques em `iniciar.bat`. Diga **"Hey Jarvis"**, espere o "Pois não?" e fale. Depois da resposta há 6 s para continuar sem repetir o chamado.
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

## O que ele faz (24 ferramentas)
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
- **`llm.host` precisa ser `127.0.0.1`**, nunca `localhost`: no Windows isso custa +2 s por pergunta (tenta IPv6 primeiro).
- **Caminho com acento** (`PROGRAMAÇÃO`) quebra o espeak do Piper; `tts.py` copia os dados para `C:\ProgramData\jarvis_espeak`.
- **Modelo fixo e prompt aquecido** (`keep_alive: -1`, `Cerebro.aquecer` com as ferramentas), senão a 1ª pergunta paga segundos.
- **`llm.contexto: 6144`**: prompt + 24 ferramentas já usam ~3.100 tokens. 8192 aperta a VRAM; 4096 não sobra espaço. Cada ferramenta nova custa ~100 tokens.
- **Nome e descrição de ferramenta importam:** com `abrir_arquivo` o modelo não abria pastas (chamava uma ferramenta inexistente e o Ollama descartava em silêncio); com opções `tocar_pausar/proxima/parar` sem descrição, "pula essa música" virou `parar`. Ao mudar nomes/descrições, rode a avaliação de novo.
- **Embeddings na CPU** (`num_gpu: 0`): na GPU o Ollama tiraria o Qwen da memória. `nomic-embed-text` é ruim em português; use `embeddinggemma`.
- **Reflexão usa o mesmo `num_ctx` do Jarvis**: num_ctx diferente faz o Ollama recarregar o modelo.
- **Consolidação de fatos não confia só no modelo:** fatos de categorias incompatíveis nunca se fundem; "substitui" usa a frase nova (o modelo misturava "mora em Valinhos. mora em Campinas."); juntar que perde nome/número/site é recusado.
- VRAM com tudo carregado: ~7,5 de 8,2 GB. Feche apps pesados de GPU antes de usar.

## Testes
```
.venv\Scripts\python.exe -m pytest tests -q
```
239 testes: guarda do PowerShell (incluindo tentativas de burla), arquivos, executor/permissões, comandos de voz, sim/não, calculadora, detector de ações inventadas e memória (banco, segredos, consolidação, reflexão, ferramentas).

## Medidas (RTX 3050 8 GB)
- Conversa: transcrição ~0,4 s · 1ª frase ~0,35 s (já com a busca na memória, ~30 ms) · LLM 37 tokens/s.
- Com ferramenta: ~1,35 s em média até a resposta (duas chamadas ao modelo); pesquisa web ~3,5 s.
- Escolha da ferramenta certa: 47/47 pedidos em português, em duas rodadas seguidas, sem nenhuma ação inventada.
- Reflexão de um dia de conversa: ~15 s.

## Próximas fases
4. Interface HUD web
5. Visão de tela + proatividade (lembretes, alertas)
