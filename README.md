# JARVIS: assistente de voz 100% local para Windows

Assistente pessoal que roda inteiro no seu computador, sem nuvem: você diz **"Hey Jarvis"**, ele ouve, lembra do que já sabe sobre você, pensa com um LLM local, **age no computador** (abre programas, mexe em arquivos, controla volume e mídia, pesquisa na web, roda PowerShell com segurança) e responde falando. Com o tempo, **aprende** com as conversas.

Fases concluídas: **1** (voz), **2** (controle do computador) e **3** (memória).

```
microfone → openWakeWord → webrtcvad → faster-whisper (GPU) → Qwen3 8B (Ollama) ⇄ ferramentas → Piper TTS → alto-falante
                                                                   ↑      ↓              ↑
                                         memória (SQLite + embeddinggemma na CPU)   permissões 🟢🟡🔴 + log
                                                                   ↑
                                                  reflexão: aprende com as conversas
```

| Etapa | Tecnologia | Onde está no código |
|---|---|---|
| Palavra de ativação | openWakeWord (`hey_jarvis`) | `jarvis/audio/mic.py` |
| Detecção de fala | webrtcvad | `jarvis/audio/mic.py` |
| Fala → texto | faster-whisper na GPU (CUDA) | `jarvis/audio/stt.py` |
| Cérebro | Qwen3 8B via Ollama, com chamada de ferramentas | `jarvis/brain/llm.py` |
| Comandos fixos (tchau, sim/não...) | regras em Python | `jarvis/brain/comandos.py` |
| Ferramentas e permissões | Python + PowerShell | `jarvis/tools/` |
| Memória e reflexão | SQLite + embeddings `embeddinggemma` | `jarvis/memoria/` |
| Texto → fala | Piper (voz `pt_BR-faber-medium`) | `jarvis/audio/tts.py` |

### Estrutura
```
main.py              ponto de entrada (modo voz e modo texto)
config.yaml          todas as configurações (modelo, microfone, voz, limiares)
iniciar.bat          atalho: modo voz
texto.bat            atalho: modo texto
jarvis/
  audio/             microfone, wake word, Whisper e Piper
  brain/             conversa com o LLM e comandos fixos
  tools/             as 24 ferramentas, o executor de permissões e a análise de PowerShell
  memoria/           banco, embeddings, reflexão e CLI (python -m jarvis.memoria)
tests/               testes com pytest
data/                modelos baixados, banco de memória e logs (fora do git)
```

## Requisitos
- **Windows 10/11** (o controle do computador usa APIs do Windows, PowerShell, pycaw e as pastas do usuário).
- **Python 3.11 ou mais novo, 64 bits.**
- **GPU NVIDIA com ~8 GB de VRAM** (testado numa RTX 3050 8 GB) e driver atualizado. As bibliotecas CUDA (cuBLAS/cuDNN) vêm pelo `pip`; não precisa instalar o CUDA Toolkit.
- **[Ollama](https://ollama.com/download)** instalado.
- Microfone e alto-falante.

## Instalação
Tudo a partir da pasta do projeto, no PowerShell.

**1. Ambiente Python e dependências**
```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.lock   # versões exatas testadas
```
`requirements.txt` tem a lista sem versões fixas, caso queira atualizar os pacotes.

**2. Modelos do Ollama** (o do LLM e o de embeddings da memória)
```powershell
ollama pull qwen3:8b
ollama pull embeddinggemma
```

**3. Modelos de wake word** em `data/wakeword/` (da [release do openWakeWord](https://github.com/dscripka/openWakeWord/releases/tag/v0.5.1)):
```powershell
mkdir data\wakeword -Force
$base = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
foreach ($m in "hey_jarvis_v0.1.onnx", "melspectrogram.onnx", "embedding_model.onnx") {
  Invoke-WebRequest "$base/$m" -OutFile "data\wakeword\$m"
}
```

**4. Voz do Piper** em `data/voices/` (precisa do `.onnx` e do `.onnx.json`):
```powershell
.venv\Scripts\python.exe -m piper.download_voices pt_BR-faber-medium --download-dir data\voices
```
Ou baixe os dois arquivos manualmente em [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/pt/pt_BR/faber/medium).

**5. Modelo do Whisper:** não precisa fazer nada; o `faster-whisper` baixa o modelo `small` sozinho na primeira execução.

Ao final, a pasta `data/` deve ter:
```
data/wakeword/hey_jarvis_v0.1.onnx
data/wakeword/melspectrogram.onnx
data/wakeword/embedding_model.onnx
data/voices/pt_BR-faber-medium.onnx
data/voices/pt_BR-faber-medium.onnx.json
```
Os bancos e logs (`data/memoria/`, `data/logs/`) são criados automaticamente.

## Como rodar
O **Ollama precisa estar aberto** (ícone na bandeja ou `ollama serve`). Se não estiver, o Jarvis avisa e encerra.

| Modo | Atalho | Comando |
|---|---|---|
| Voz (completo) | dois cliques em `iniciar.bat` | `.venv\Scripts\python.exe main.py` |
| Texto (sem microfone nem Whisper) | dois cliques em `texto.bat` | `.venv\Scripts\python.exe main.py --texto` |
| Texto, respondendo em voz | `texto.bat --falar` | `.venv\Scripts\python.exe main.py --texto --falar` |

- **Voz:** espere "Sistemas online", diga **"Hey Jarvis"**, espere o "Pois não?" e fale. Depois da resposta há 6 s para continuar sem repetir o chamado.
- **Texto:** digite em vez de falar. Bom para testar sem GPU para o Whisper ou sem microfone.
- **"Tchau Jarvis"**, **"Desligar Jarvis"** ou **"Pode dormir"** encerram. **"Esqueça a conversa"** recomeça. Ctrl+C também encerra.
- Ações perigosas (🔴) sempre pedem **"Confirma?"** antes de executar; veja [Segurança](#segurança-como-as-permissões-funcionam).

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
