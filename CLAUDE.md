# JARVIS: regras do projeto

- **Pasta oficial: `D:\PROJETO JARVIS`.** Trabalhe, rode e teste somente aqui.
- **Não abra, leia nem grave nada em `E:\`.** A cópia antiga em `E:\PROGRAMAÇÃO\Lumax\PROJETO JARVIS` está desatualizada, e o HD E: está falhando: cai com leitura pesada.
- Usuário: Iury (Lima). Responda em português.

## Rodar
- Jarvis por voz: `iniciar.bat`. Modo texto: `texto.bat`.
- Python do projeto: `.venv\Scripts\python.exe`. A voz clonada (XTTS-v2) roda num processo separado, com `.venv-voz\Scripts\python.exe`.
- Testes: `.venv\Scripts\python.exe -m pytest -q`. Em 2026-10-18 passaram 669 testes (1 pulado).
- Git e Node agora ficam em `C:\Users\iurys\AppData\Local\Programs\Git` e `...\nodejs` (antes estavam no E: e travavam o PC).

## Armadilhas (detalhes no README)
- Carregue numpy, onnxruntime e ctranslate2 na thread principal antes de criar qualquer thread (`precarregar` no `main.py`). Senão o Jarvis trava no Windows.
- Chamadas ao Ollama: sempre `num_ctx: 6144` e `keep_alive: -1`. Senão ele recarrega o modelo (~15 s). Use o host `127.0.0.1`, nunca `localhost`.
- A VRAM é de 8 GB. Qwen + Whisper + voz clonada não cabem juntos na GPU: com o Claude como cérebro, o Qwen só sobe quando precisa e sai em 2 min.
- `ver_tela` e `mouse` (visão + mouse real) só vão para os cérebros Claude (`visao=True`); o mouse é 🟢 exceto janelas sensíveis e botões de risco.
- O Iury quer um Jarvis só: nada de mostrar engrenagens na HUD (ações, chip do modelo, permissões); as exceções são o seletor Qwen/Claude no rodapé (pedido em 2026-10-18) e a **Central** do JARVIS Connect, uma gaveta fechada por padrão (pedida em 2026-10-19), nunca trocar de voz (só a clonada) e a conversa nunca recomeça sozinha (continuidade pelo banco, interrupção sem matar o `claude`).
- `programar` põe o Claude Code (Sonnet) para trabalhar no projeto aberto no VS Code, em segundo plano, com arquivos + comandos (escolha do Iury), sem git push e fora do E:. Avisos de tarefas terminadas passam por `jarvis/brain/avisos.py`.
- Os `__pycache__` de testes vieram do E: com caminhos de lá: se aparecer `E:\` num traceback, apague os `__pycache__` do projeto.
- Gmail, Agenda e WhatsApp (`gmail.py`, `agenda.py`, `whatsapp.py`, login em `google_api.py`) são `so_claude=True`: o Qwen não recebe. Enviar e-mail e WhatsApp é sempre 🔴 (escolha do Iury); não há ferramenta de apagar e-mail.
- Base de conhecimento (o que ele ESTUDA) é separada da memória sobre o Iury: `jarvis/memoria/conhecimento.py` + `jarvis/tools/conhecimento.py`, banco em `data/conhecimento/`. Leitura de páginas/PDF/docx em `jarvis/tools/leitura.py`, que bloqueia a rede local. Ferramentas `so_claude` podem devolver até 12 mil caracteres (`MAX_RESULTADO_CLAUDE`); as do Qwen, 1.500.
- Dois motores de voz clonada, mesmo protocolo de processo: `clone_worker.py` (XTTS, `.venv-voz`, padrão) e `qwen3_worker.py` (Qwen3-TTS via faster-qwen3-tts, `.venv-qwen3`). Escolha em `tts.clone.modelo`; troque só depois de `python -m jarvis.diagnostico --so voz --comparar`. Continua valendo: uma voz só, nunca trocar sozinho de motor.
- Modo jogo (`jarvis/recursos.py`): GPU ocupada por outro programa → descarrega só os modelos do Jarvis no Ollama, segura a reflexão e `keep_alive` vira 30s. Por isso o `keep_alive` do Qwen e dos embeddings passa sempre por `recursos.keep_alive(...)`. O `keep_alive: -1` do Qwen continua valendo fora do jogo.
- Modelo do Claude (opus/sonnet/haiku) troca em tempo real: `Seletor.trocar_modelo_claude` (reinicia o `claude --model`, testa antes, salva `modelo_claude` em `data/preferencias.json`), botões na HUD e voz ("usa o Opus").
- Habilidades (`jarvis/tools/habilidades.py`, pasta `habilidades/`): formato Claude Skills (SKILL.md). Contexto leva só nome+descrição; `usar_habilidade` carrega o corpo. Criar é 🟡, instalar por link é 🔴, scripts nunca são executados. YouTube: `leitura.ler` usa a legenda (youtube-transcript-api).
- Chaves de API nunca vão no `config.yaml`. A HUD só escuta em 127.0.0.1.
- JARVIS Connect (`jarvis/connect/`, `docs/CONNECT.md`): tarefas, automações (Gatilho → Condições → Ações), catálogo de conectores (um arquivo por serviço com `@conector`) e a Central na HUD (`web/central.js`, sem innerHTML). Envio em automação = "aprovar ao criar" (escolha do Iury): o hash do fluxo é aprovado uma vez; para terceiros só destino fixo/`{mensagem.de}` e texto fixo (sem IA nem conteúdo recebido), conferido na criação e no envio. Fora das automações, WhatsApp/e-mail continuam sempre 🔴.
- WhatsApp gratuito = Evolution API (escolha do Iury), em Docker local (`servicos/evolution`, 127.0.0.1:8080). Webhooks num servidor separado (127.0.0.1:8766, segredo/HMAC); o Cloudflare Tunnel aponta só para ele, nunca para a HUD. Chaves dos conectores no cofre DPAPI (`data/connect/cofre.json`), nunca na HUD. A IA das automações (`connect/ia.py`) roda sem ferramentas.
