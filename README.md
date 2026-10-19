# JARVIS: assistente de voz pessoal para Windows

Assistente que roda no seu computador: você diz **"Hey Jarvis"**, ele ouve, lembra do que já sabe sobre você, pensa, **age no computador** (abre programas, mexe em arquivos, controla volume, Spotify e mídia, pesquisa na web, enxerga a tela e usa o mouse, roda PowerShell com segurança, programa pelo VS Code) e responde falando com a voz dele. Com o tempo, **aprende** com as conversas.

Fases concluídas: **1** (voz), **2** (controle do computador), **3** (memória) e **4** (HUD).

```
microfone → openWakeWord → webrtcvad → faster-whisper (GPU) → cérebro ⇄ 42 ferramentas → voz clonada (XTTS-v2) → alto-falante
                                                               │   ↑            ↑
                       claude-cli (assinatura) | Claude API | Qwen3 8B local    permissões 🟢🟡🔴 + log
                                                               ↑
                                     memória (SQLite + embeddinggemma na CPU) ← reflexão: aprende com as conversas
                                                               ↓
                                              HUD no navegador (127.0.0.1:8765) + ícone na bandeja
```

| Etapa | Tecnologia | Onde está no código |
|---|---|---|
| Palavra de ativação | openWakeWord (`hey_jarvis`) | `jarvis/audio/mic.py` |
| Detecção de fala e interrupção | webrtcvad + detector de eco | `jarvis/audio/mic.py`, `jarvis/audio/interrupcao.py` |
| Fala → texto | faster-whisper na GPU (CUDA) | `jarvis/audio/stt.py` |
| Cérebro (troca a qualquer hora) | `claude` CLI pela assinatura · API da Anthropic · Qwen3 8B no Ollama | `jarvis/brain/` (`claude_cli.py`, `claude.py`, `llm.py`, `seletor.py`) |
| Ferramentas e permissões | Python + PowerShell + UI Automation do Windows | `jarvis/tools/` |
| Memória e reflexão | SQLite + embeddings `embeddinggemma` | `jarvis/memoria/` |
| Texto → fala | XTTS-v2 com voz clonada (Piper como alternativa) | `jarvis/audio/tts.py`, `clone.py`, `clone_worker.py` |
| Interface | FastAPI + página web + bandeja (pystray) | `jarvis/hud/` |

### Estrutura
```
main.py              ponto de entrada (voz, texto, HUD, início com o Windows)
config.yaml          todas as configurações (cérebro, voz, microfone, HUD, limiares)
iniciar.bat          atalho: modo voz
texto.bat            atalho: modo texto
jarvis/
  audio/             microfone, wake word, interrupção, Whisper, voz clonada e Piper
  brain/             cérebros (Claude CLI/API, Qwen), seletor de mentalidade, comandos fixos, avisos
  hud/               servidor local, página web, bandeja e início com o Windows
  tools/             as ferramentas, o executor de permissões e a análise de PowerShell
  memoria/           banco, embeddings, reflexão e CLI (python -m jarvis.memoria)
tests/               testes com pytest
data/                modelos baixados, voz, memória, tokens e logs (fora do git)
```

## Requisitos
- **Windows 10/11** com o **Microsoft Edge** (a HUD abre nele como janela de aplicativo).
- **Python 3.11 ou mais novo, 64 bits.**
- **GPU NVIDIA com ~8 GB de VRAM** (testado numa RTX 3050 8 GB) e driver atualizado. As bibliotecas CUDA vêm pelo `pip`; não precisa do CUDA Toolkit.
- **[Ollama](https://ollama.com/download)**: necessário mesmo com o Claude como cérebro (embeddings da memória, reflexão e reserva local).
- **[Claude Code](https://code.claude.com)** instalado e logado, para o cérebro padrão (`cerebro.motor: claude-cli`) e para a ferramenta `programar`. Sem ele, use `motor: ollama` (100% local) ou `motor: claude` (chave de API).
- Microfone e alto-falante (fone de ouvido funciona melhor para interromper por voz).
- Opcional: **Spotify Premium** para tocar músicas pelo nome.

## Instalação
Tudo a partir da pasta do projeto, no PowerShell.

**1. Ambiente principal e dependências**
```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.lock   # versões exatas testadas
```
`requirements.txt` tem a lista sem versões fixas, caso queira atualizar os pacotes.

**2. Modelos do Ollama** (LLM local e embeddings da memória)
```powershell
ollama pull qwen3:8b
ollama pull embeddinggemma
```

**3. Login do Claude Code** (se for usar `motor: claude-cli`)
```powershell
claude            # na primeira vez, faça o login com a sua assinatura
claude auth status
```
Para a API em vez da assinatura, salve a chave em `data/anthropic.key` (uma linha) e use `cerebro.motor: claude`. Detalhes em [Cérebro Claude por API](#cérebro-claude-por-api-alternativa).

**4. Modelos de wake word** em `data/wakeword/` (da [release do openWakeWord](https://github.com/dscripka/openWakeWord/releases/tag/v0.5.1)):
```powershell
mkdir data\wakeword -Force
$base = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
foreach ($m in "hey_jarvis_v0.1.onnx", "melspectrogram.onnx", "embedding_model.onnx") {
  Invoke-WebRequest "$base/$m" -OutFile "data\wakeword\$m"
}
```

**5. Voz do Jarvis (XTTS-v2, padrão `tts.motor: clone`)**: roda num ambiente separado, `.venv-voz`, com PyTorch.
```powershell
python -m venv .venv-voz
.venv-voz\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
.venv-voz\Scripts\python.exe -m pip install coqui-tts soundfile numpy
```
Gere o áudio de referência a partir do vídeo/áudio com a voz (10 a 30 s de fala, sem música). O script deixa mono 24 kHz,
corta silêncio, encurta pausas longas e acerta o volume, e salva em `data/voices/jarvis_ucm/referencia.wav`:
```powershell
.venv\Scripts\python.exe -m jarvis.audio.preparar_voz "VOZ JARVIS UCM]\Gravando 2026-10-17 131614.mp4"
``` O modelo XTTS-v2 (~1,8 GB) é baixado na primeira vez que a voz sobe. A licença do XTTS-v2 (CPML) é só para **uso não comercial**.

**5b. Voz Qwen3-TTS (opcional, mais fiel à referência, `tts.clone.modelo: qwen3`)**: ambiente próprio, `.venv-qwen3`.
```powershell
python -m venv .venv-qwen3
.venv-qwen3\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
.venv-qwen3\Scripts\python.exe -m pip install faster-qwen3-tts==0.5.4
```
O modelo (`Qwen3-TTS-12Hz-1.7B-Base`, ~4 GB) é baixado na primeira vez. Ele usa a mesma referência do XTTS **mais a
transcrição exata dela** (`tts.clone.qwen3.texto_referencia`, já preenchida com o texto do mp4). Depois compare:
```powershell
.venv\Scripts\python.exe -m jarvis.diagnostico --so voz --comparar --ouvir
```
O diagnóstico gera as mesmas frases com os dois, mede engasgos, nitidez (Whisper) e 1º som, salva
`data/logs/diagnostico_voz_xtts.wav` e `..._qwen3.wav` e diz qual usar. **Por que existe:** num teste independente
(RTX 4050, 1.620 falas) o Qwen3-TTS 1.7B foi o mais fiel à voz de referência (0,85 contra 0,64 do XTTS), com 1,2% de
palavras erradas contra 10,6% e nenhuma palavra inventada; em troca usa ~5 GB de VRAM (contra ~2,7 GB) e só fala em
tempo real com o `faster-qwen3-tts` (1,8x o tempo real numa RTX 4060; numa 3050 deve ficar perto do limite). Com ele,
use o Claude como cérebro: o Qwen local não cabe junto na GPU.

**6. Voz Piper (alternativa leve, `tts.motor: piper`)** em `data/voices/` (precisa do `.onnx` e do `.onnx.json`):
```powershell
.venv\Scripts\python.exe -m piper.download_voices pt_BR-faber-medium --download-dir data\voices
```
Ou baixe os dois arquivos em [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/pt/pt_BR/faber/medium).

**7. Spotify (opcional)**: autorize uma vez; o token fica em `data/spotify/token.json`.
```powershell
.venv\Scripts\python.exe -m jarvis.tools.spotify
```

**8. Gmail, Agenda e Contatos do Google (opcional; o WhatsApp usa os contatos para achar o número)**. Uma vez só:
1. Em [console.cloud.google.com](https://console.cloud.google.com), crie um projeto e ative as APIs **Gmail API**,
   **Google Calendar API** e **People API** ("APIs e serviços" → "Biblioteca").
2. "Tela de consentimento OAuth": tipo **Externo**; coloque o seu e-mail como usuário de teste. Depois clique em
   **Publicar aplicativo**: em modo "Teste" o Google derruba o login a cada 7 dias.
3. "Credenciais" → "Criar credenciais" → **ID do cliente OAuth** → tipo **App para computador**. Baixe o JSON e salve como
   `data\google\cliente.json`.
4. Autorize (abre o navegador; como o app é seu e não foi verificado pelo Google, clique em "Avançado" → "Acessar"):
```powershell
.venv\Scripts\python.exe -m jarvis.tools.google_api
```
O token fica em `data/google/token.json` (fora do git). Permissões pedidas: ler e enviar e-mail (**não** apagar), eventos
da agenda e ler contatos.

O modelo do Whisper (`small`) é baixado sozinho na primeira execução. Ao final, a pasta `data/` deve ter:
```
data/wakeword/hey_jarvis_v0.1.onnx
data/wakeword/melspectrogram.onnx
data/wakeword/embedding_model.onnx
data/voices/jarvis_ucm/referencia.wav          (voz clonada)
data/voices/pt_BR-faber-medium.onnx(.json)     (só se usar o Piper)
```
Memória, logs e preferências (`data/memoria/`, `data/logs/`, `data/preferencias.json`) são criados automaticamente.

## Como rodar
O **Ollama precisa estar aberto** (ícone na bandeja ou `ollama serve`). Na partida o Jarvis espera até 2 min por ele; depois disso avisa e encerra.

| Modo | Atalho | Comando |
|---|---|---|
| Voz (completo, com HUD) | dois cliques em `iniciar.bat` | `.venv\Scripts\python.exe main.py` |
| Texto (sem microfone nem Whisper) | dois cliques em `texto.bat` | `.venv\Scripts\python.exe main.py --texto` |
| Texto, respondendo em voz | `texto.bat --falar` | `.venv\Scripts\python.exe main.py --texto --falar` |
| Sem abrir a janela da HUD | | `.venv\Scripts\python.exe main.py --silencioso` |
| Iniciar junto com o Windows | | `.venv\Scripts\python.exe main.py --instalar-inicio` (desfaz com `--remover-inicio`) |

- **Voz:** espere "Sistemas online" (a voz clonada leva ~30 s para ficar pronta), diga **"Hey Jarvis"**, espere o chamado e fale. Depois da resposta há 10 s para continuar sem repetir o chamado.
- **HUD:** abre sozinha em **http://127.0.0.1:8765** (ou na próxima porta livre; veja a linha "HUD em ..." no console). Dá para digitar pedidos, ver a conversa e a memória, e usar **PARAR TUDO** (Esc) e Mudo.
- **"Tchau Jarvis"**, **"Desligar Jarvis"** ou **"Pode dormir"** encerram. **"Esqueça a conversa"** recomeça. **"Fica quieto"** só cala. Ctrl+C também encerra.
- Ações perigosas (🔴) sempre pedem **"Confirma?"** por voz ou clique na HUD; veja [Segurança](#segurança-como-as-permissões-funcionam).
- **Ligado o tempo todo:** `python main.py --instalar-inicio` põe o Jarvis para subir com o Windows (só o ícone na
  bandeja). Deixe o Ollama também iniciando com o Windows (o instalador dele já faz isso).
- **Modo jogo (automático):** com outro programa usando a GPU acima de 50% por 20 s (jogo, render, IA), o Jarvis tira
  da memória do Ollama os modelos **dele** (Qwen ~5,5 GB de VRAM, embeddinggemma ~1 GB de RAM), segura a reflexão
  da memória (que subia o Qwen no meio do jogo) e o que o Ollama carregar durante o jogo sai 30 s depois. Voz e ouvido
  continuam: "Hey Jarvis" funciona jogando. Com a GPU livre por 90 s, volta ao normal. Ajustes em `recursos:` no
  `config.yaml`. Os embeddings agora saem da memória após 30 min sem uso (antes: nunca).
- **Partida lenta?** No fim da partida o console mostra `[partida] total Xs: imports … · Whisper … · cérebro … · esperando a voz …`,
  e a mesma linha fica em `data/logs/partida.log`. As fases da voz clonada (bibliotecas, modelo na GPU, molde da voz,
  aquecimento) ficam em `data/logs/voz_clonada.log`. O molde da voz é calculado uma vez e guardado em
  `referencia.wav.latentes.pt` (refeito sozinho se você trocar o áudio). Para não esperar nunca, use `--instalar-inicio`:
  ele sobe junto com o Windows e já está pronto quando você precisar.

## Um Jarvis só (2026-10-17)
- **A conversa não recomeça.** Toda conversa fica gravada no banco; quando um cérebro começa do zero (Jarvis reaberto,
  processo do `claude` reiniciado, reserva local assumindo, troca de mentalidade), o primeiro pedido leva os últimos
  pedidos das últimas 12 h e ele continua de onde parou. Só "esqueça a conversa" corta essa continuidade.
- **Interromper não derruba a conversa:** falar por cima ou "parar tudo" interrompe só o pedido em andamento
  (`control_request interrupt` do `claude`); o processo e o contexto continuam. Antes o processo era morto.
- **A HUD mostra o Jarvis, não as engrenagens:** sem painel de ações, de permissões, chip do modelo e botão
  "Nova conversa". Ficam o núcleo, a conversa, o sistema, a memória, o seletor de mentalidade, Mudo e Parar.
  A autorização 🔴 continua.
- **Uma voz só:** a do Jarvis (XTTS). Na partida ele espera ela ficar pronta antes de falar; se ela cair, espera voltar
  (`tts.reserva_piper: false`). Erros do processo da voz ficam em `data/logs/voz_clonada.log`.
- **Voz sem engasgo (2026-10-18):** a fala só começa a tocar com 1,2 s de áudio de folga (ou na hora, se a GPU já está
  gerando 2x mais rápido que o tempo real), e frase acima de 110 caracteres é quebrada nas vírgulas: no streaming do
  XTTS o fim de uma frase longa sai cada vez mais devagar, e era aí que travava. O molde usa a referência inteira
  (até 30 s), não só os 6 s do padrão, e a amostragem é mais contida (`top_k 30`, `top_p 0.8`). Ajustes em `tts.clone`.
- **Fala emendada:** cada frase é pedida à voz no momento em que o cérebro a escreve, então a próxima já está pronta
  quando a atual acaba. Medido: 0 silêncios entre 4 frases (antes, 3 pausas somando 1,5 s).
- **Conversa corrida:** depois de responder ele escuta por 10 s sem precisar do "Hey Jarvis" (`conversa.janela_followup_s`).

## Ler e estudar: a base de conhecimento (só com o Claude como cérebro)
"Lê esse link pra mim", "estuda esse manual do Microvix", "aprende esse PDF". Depois: "como emito nota no Microvix?"
e ele responde **a partir do que estudou**, dizendo de qual documento veio.

**Como o Jarvis sabe das coisas (três camadas):**
| Camada | O que é | Onde fica |
|---|---|---|
| O que o Claude já sabe | conhecimento geral do modelo (programação, ciência, idiomas...) | no modelo |
| Memória sobre você | fatos da sua vida, preferências, correções, resumos dos dias | `data/memoria/jarvis.db` |
| **Base de conhecimento** | o que você mandou ele **estudar**: manuais, páginas, PDFs, contratos | `data/conhecimento/conhecimento.db` |

- **Como ele "aprende" um documento:** `aprender` lê o texto, divide em trechos de ~1.000 caracteres (com sobreposição) e
  guarda cada trecho com um vetor de significado (`embeddinggemma`, local, na CPU). Numa pergunta, os trechos de
  significado mais próximo voltam para o Claude ler antes de responder: é estudar com o livro aberto do lado, não
  "treinar" o modelo (isso não dá para fazer num PC, e nem precisa). Estudar de novo a mesma fonte atualiza.
- **O que ele lê:** páginas da internet, PDFs (com texto; PDF escaneado precisaria de OCR), Google Docs/Planilhas
  **públicos**, e arquivos do PC (`txt`, `md`, `pdf`, `docx`, `csv`, código). Páginas que exigem login não.
- **Ferramentas:** `ler_documento` (lê em partes de 6 mil caracteres), `aprender`, `consultar_conhecimento`,
  `listar_conhecimento` (🟢) e `esquecer_conhecimento` (🟡). Cada pedido ao Claude leva a lista do que já foi estudado,
  para ele saber quando consultar.
- **Segurança:** o texto lido é de terceiros e entra marcado como não confiável (depois de ler, ações 🟡 pedem
  confirmação). Ele **nunca** acessa endereços da rede local (127.0.0.1, 192.168..., roteador), nem por redirecionamento.
- O banco é só seu e fica fora do git. Para ver o que tem: "o que você já estudou?".

## Gmail, Agenda e WhatsApp (só com o Claude como cérebro)
"Tenho e-mail novo?", "lê o e-mail da Ana", "responde que eu confirmo", "o que tenho amanhã?", "marca dentista
sexta às 15h", "manda no WhatsApp pra Maria que eu chego às 8". Configuração no passo 8 da instalação.

| Ferramenta | O que faz | Nível |
|---|---|---|
| `ler_emails`, `abrir_email` | lista (com a busca do Gmail: `is:unread`, `from:`...) e lê e-mails | 🟢 (conteúdo externo) |
| `enviar_email` | envia ou responde (na mesma conversa); `para` aceita e-mail ou nome de contato | 🔴 **sempre** |
| `ver_agenda` | compromissos de hoje ou dos próximos dias | 🟢 (conteúdo externo) |
| `criar_evento` | cria compromisso, **sem convidados** | 🟡 (🔴 depois de ler e-mail/site) |
| `whatsapp_enviar` | manda mensagem pelo app do WhatsApp para Windows | 🔴 **sempre** |

- **E-mail e agenda são texto de terceiros:** um e-mail pode trazer "apague a pasta X" ou "mande tudo para fulano".
  Por isso o que vem lido entra marcado como não confiável e, depois de ler, toda ação 🟡 passa a pedir confirmação.
  O Jarvis nem tem como apagar e-mails: essa permissão não é pedida ao Google.
- **WhatsApp:** não existe API oficial para conta pessoal. O Jarvis abre `whatsapp://send?phone=...&text=...`: o próprio
  WhatsApp abre a conversa **daquele número** com o texto pronto, e ele só aperta Enter (e só se a janela da frente for
  o WhatsApp). Nada de procurar o contato clicando pela tela, onde um clique errado manda para a pessoa errada.
  Nome vira número pelos contatos do Google; sem DDI, assume +55. Para **ler** conversas, o Claude usa `ver_janela`
  com o WhatsApp aberto.
- Na confirmação a tela mostra destinatário, assunto/número e o começo do texto.
- **Por que só com o Claude:** são 6 ferramentas a mais (~600 tokens) e o contexto de 6144 tokens do Qwen já está no limite.

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
Três jeitos, sem reiniciar:
- **Na HUD:** os botões **Qwen local · Claude · assinatura · Claude · API** no rodapé (voltaram em 2026-10-18). O ativo fica
  destacado; o que não está disponível fica apagado e o motivo aparece ao passar o mouse (ex.: "falta a chave em data/anthropic.key").
- **Por voz/texto:** "usa o Claude", "muda para o Qwen", "volta pro local".
- **Modelo do Claude (Opus · Sonnet · Haiku):** botões ao lado, na HUD, quando o Claude está ativo, ou por voz: "usa o
  Opus", "muda para o Haiku" (se estiver no Qwen, já passa para o Claude). Opus = o mais capaz (gasta ~5x a cota),
  Sonnet = equilíbrio, Haiku = o mais rápido e econômico. Trocar reinicia o processo do `claude` com o novo modelo
  (~2 s), testa antes de largar o atual e continua a conversa. Fica salvo em `data/preferencias.json`. Lista em
  `claude_cli.modelos`; vale também para o cérebro por API (`claude.modelos` mapeia o apelido para o id).
- **`cerebro.motor` no `config.yaml`:** só a escolha inicial.
- A escolha fica salva em `data/preferencias.json` (ignorado pelo git) e vale na próxima vez que ligar.
- A troca espera o pedido atual acabar, sobe e testa a nova mentalidade **antes** de largar a antiga; se falhar, continua na atual e avisa.
- A conversa continua ao trocar (a nova mentalidade recebe a conversa recente); a memória de longo prazo é a mesma.
- Opções: **Qwen local** (grátis, privado), **Claude · assinatura** (CLI) e **Claude · API** (só aparece disponível com a chave em `data/anthropic.key`).
- Ao sair do Claude, o processo `claude` é encerrado (não fica gastando nada).

## Cérebro `claude-cli` (a sua assinatura, sem chave) — o motor ativo
`cerebro.motor: claude-cli` roda o programa `claude` (Claude Code) por trás, com o login da sua assinatura (hoje: Claude Pro). Ele é só o "pensador": as 42 ferramentas continuam sendo do JARVIS e passam pelo Executor (🟢🟡🔴, confirmação, bloqueio da HUD, log).

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

**Custo:** cada pedido envia ~5 mil tokens (prompt + 42 ferramentas), que ficam em cache (leitura ~10% do preço). Cada resposta é registrada em `data/logs/uso_claude.jsonl` (entrada, saída, cache lido/escrito) para você acompanhar.

**Privacidade:** com `motor: claude`, o texto das conversas, os fatos da memória que entram no pedido e o conteúdo de janelas/arquivos lidos pelas ferramentas vão para a Anthropic. Para 100% local, `motor: ollama`.

**Para quem mexer no código:** o histórico enviado é somente-acréscimo (o contexto fica dentro da mensagem do usuário) e usa `prefix_mismatch_behavior: drop_block`; ferramentas e prompt de sistema têm de ser idênticos em todo pedido (cache); nunca `tool_choice` forçado (400 nestes modelos).

## HUD (fase 4)
**Visual:** globo de filamentos de luz ciano (a "mente" do Jarvis), com pulsos de energia correndo pelos filamentos
(mais rápidos pensando), névoa de luz por dentro, reflexos nos pontos mais fortes e faíscas que se soltam quando ele
fala, no ritmo da voz. Cada estado tem a sua luz. `hud.tema: dourado` é uma variação âmbar opcional.
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

## Memória (fase 3)
- **A cada pedido** o Jarvis recebe seu perfil (sempre) + os 5 fatos mais relevantes ao que você disse + o resumo de um dia anterior, se tiver relação. Vai só na mensagem atual, então o cache do prompt continua valendo e a latência não sobe.
- **Explícito:** "lembre que minha mãe se chama Maria" (`memorizar`), "esqueça que eu gosto de pizza" (`esquecer_fato`), "o que conversamos ontem?" (`lembrar`).
- **Aprendizado sozinho (reflexão):** relê as conversas, extrai fatos (nome, família, trabalho, rotina, preferências, apelidos como "o painel = site X", correções), junta repetidos, **atualiza o que mudou** (mudou de cidade → substitui) e escreve um resumo do dia. Roda quando o Jarvis está ocioso: com o **Qwen** como cérebro, dias anteriores logo que ele abre e conversas de hoje depois de 15 min sem falar com ele; com o **Claude**, só depois de 15 min sem conversa (contando da partida), porque ela sobe o Qwen na GPU e Qwen + voz clonada + Whisper não cabem em 8 GB. Se você chamar no meio, ela pausa.
- **Ver e editar o que ele sabe:**
  ```
  .venv\Scripts\python.exe -m jarvis.memoria listar
  .venv\Scripts\python.exe -m jarvis.memoria apagar 12
  .venv\Scripts\python.exe -m jarvis.memoria dias
  .venv\Scripts\python.exe -m jarvis.memoria refletir --hoje
  ```
- **Proteções:** só o que **você** disse vira fato (conteúdo de sites e arquivos não entra na reflexão); `memorizar`/`esquecer_fato` são 🟡, então depois de ler um site ou arquivo pedem confirmação; senhas, cartões e CPF nunca são guardados; "esquecer" apaga de verdade.
- Tudo fica em `data/memoria/jarvis.db` (fora do git). `JARVIS_MEMORIA=outro.db` usa outro banco (testes).

## O que ele faz (31 ferramentas + `ver_tela`, `mouse`, Gmail, Agenda e WhatsApp com o Claude)
| Área | Ferramentas | Nível |
|---|---|---|
| Informação | `hora_data`, `info_sistema` (CPU, RAM, discos, GPU e temperatura, IP local), `listar_janelas`, `calcular` | 🟢 |
| Programas | `abrir_programa` (inclui apps da Store: Spotify, WhatsApp...), `fechar_programa` (como clicar no X) | 🟢 / 🟡 |
| Som | `volume`, `controle_midia` (pausar/continuar, próxima, anterior) | 🟢 |
| Memória | `lembrar` 🟢, `memorizar` e `esquecer_fato` 🟡 | 🟢 / 🟡 |
| Web | `pesquisar_web`, `abrir_site` (só http/https) | 🟢 |
| Google e WhatsApp (só Claude) | `ler_emails`, `abrir_email`, `ver_agenda` 🟢 · `criar_evento` 🟡 · `enviar_email`, `whatsapp_enviar` 🔴 | 🟢 / 🟡 / 🔴 |
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
Duas camadas: os **testes automáticos** conferem a lógica (sem GPU, microfone nem internet) e o **diagnóstico ao vivo**
confere as peças reais deste PC.

**1. Testes automáticos** (`pytest`):
```
.venv\Scripts\python.exe -m pytest tests -q
```
- `tests/test_jornada.py`: um dia de uso passando pelas peças **reais** (Controle, Executor 🟢🟡🔴, ferramentas,
  bancos SQLite, contexto do cérebro, fila da voz). Só o "pensar" é roteirizado e o hardware é simulado.
  **Aprendizado:** estuda um manual e responde com base nele, inclusive depois de reiniciar. **Memória:** lembra o que você
  contou depois de reiniciar, esquece de verdade, recusa senha. **Fala:** frases na ordem, frase longa em trechos sem perder
  palavra, PARAR cala. **Ações:** 🟢 executa, 🟡 vai para o log, depois de ler documento a 🟡 pede confirmação, 🔴 só com
  "sim" (este último só no Windows: usa a Lixeira).
- Os demais arquivos testam cada peça a fundo: memória e reflexão, base de conhecimento e leitura (PDF, DOCX, bloqueio
  da rede local), voz (reserva, trechos), guarda do PowerShell, HUD, Claude, Gmail/Agenda/WhatsApp e o próprio diagnóstico.

**2. Diagnóstico ao vivo** (com o Jarvis **fechado** e o Ollama aberto):
```
.venv\Scripts\python.exe -m jarvis.diagnostico            # memória, aprendizado, internet, voz, ações
.venv\Scripts\python.exe -m jarvis.diagnostico --ouvir    # + toca a voz gerada
.venv\Scripts\python.exe -m jarvis.diagnostico --abrir    # + abre e fecha o Bloco de Notas de verdade
.venv\Scripts\python.exe -m jarvis.diagnostico --cerebro  # + pergunta real ao Claude com ferramenta (gasta cota)
.venv\Scripts\python.exe -m jarvis.diagnostico --so voz   # só uma parte
```
| Parte | O que mede |
|---|---|
| Memória | 3 perguntas com **outras palavras** ("qual o nome da minha mãe?" → "se chama Maria") com o embeddinggemma real |
| Aprendizado | estuda um manual de 4 seções e confere se 4 perguntas acham a seção certa |
| Internet | lê uma página real e confere o bloqueio da rede local |
| Voz | partida da voz clonada, velocidade de geração (x tempo real), 1º som, **engasgos previstos**; depois o Whisper transcreve a própria voz gerada e dá a **nitidez** (% de palavras entendidas). O áudio fica em `data/logs/diagnostico_voz.wav` |
| Ações | ferramentas 🟢 de verdade e um 🔴 que **não pode** executar sem "sim" |
| Cérebro | o Claude resolve uma conta usando a calculadora (confere que usou a ferramenta e acertou) |

Nada mexe nos seus dados (bancos temporários). Relatório em `data/logs/diagnostico.txt`, com dica para cada falha.

## Medidas (RTX 3050 8 GB, com o Qwen local)
Com o `claude-cli` os tempos estão na seção do [cérebro `claude-cli`](#cérebro-claude-cli-a-sua-assinatura-sem-chave--o-motor-ativo).
- Conversa: transcrição ~0,4 s · 1ª frase ~0,35 s (já com a busca na memória, ~30 ms) · LLM 37 tokens/s.
- Com ferramenta: ~1,35 s em média até a resposta (duas chamadas ao modelo); pesquisa web ~3,5 s.
- Escolha da ferramenta certa: 47/47 pedidos em português, em duas rodadas seguidas, sem nenhuma ação inventada.
- Reflexão de um dia de conversa: ~15 s.

## Próximas fases
5. Proatividade (lembretes, alertas). A HUD (fase 4) e a visão de tela (`ver_tela`) já estão prontas.
