# JARVIS Connect: central de integrações e automações

O Connect é a parte do Jarvis que liga ele aos seus serviços (WhatsApp, Gmail, Agenda...), guarda **tarefas** e roda
**automações** sozinho: **Gatilho → Condições → Ações → Resultado**. Tudo local, no seu PC.

- **Por voz** (com o Claude como cérebro): "todo dia às 8h resuma meus e-mails importantes", "quando chegar mensagem
  com orçamento, crie uma tarefa", "amanhã às 9h manda pro João lembrando da reunião".
- **Pela Central**: botão **Central** no topo da HUD (ou "Jarvis, abre a central"). Ela tem quatro abas: Painel,
  Integrações, Tarefas e Automações. As automações têm editor visual e modelos prontos.

## O que existe de verdade (2026-10-19)

| Situação | Integração | O que faz hoje |
|---|---|---|
| ✅ Implementado | **WhatsApp (Evolution API)** | Enviar; receber por webhook; conversas e contatos; gatilho de automação. Grátis. |
| ✅ Implementado | **WhatsApp Desktop** | Só envio, pelo app do Windows (quando a Evolution não está ligada). |
| ✅ Implementado | **Gmail** | Ler, resumir e enviar; gatilho "e-mail novo" (consulta a cada 2 min). |
| ✅ Implementado | **Google Agenda** | Ver e criar eventos; gatilho "compromisso chegando". |
| ✅ Implementado | **Motor de tarefas** | Tarefas com prioridade, data/hora e lembrete falado. |
| ✅ Implementado | **Agendador** | Uma vez, todo dia, dias da semana, todo mês, a cada N minutos. |
| ✅ Implementado | **Webhooks + Cloudflare Tunnel** | Servidor só de webhooks, com segredo/assinatura. |
| ✅ Implementado | **IA nas automações** | Resumir, classificar (condição "a IA responde SIM") e extrair dados (JSON). |
| ✅ Implementado | Claude, Claude API, Ollama, VS Code | Status na Central. |
| 🗂️ Planejado | Google Drive, Google Tasks | Catálogo com documentação e escopos levantados. |
| 🗂️ Planejado | Outlook, Calendário MS, OneDrive, To Do, Teams | Microsoft Graph com OAuth + PKCE; permissões listadas. |
| 🗂️ Planejado | WhatsApp oficial (Meta Cloud API), Z-API | Alternativas pagas ou oficiais. |
| 🗂️ Planejado | GitHub, Home Assistant | Gatilhos de CI e casa inteligente. |

A Central mostra essa mesma situação em cada cartão (**Implementado / Em desenvolvimento / Planejado**) e o estado
real na sua máquina (**Conectado / Desconectado / Erro / Aguardando**).

## Arquitetura

```
HUD (127.0.0.1:8765) ──WebSocket──► jarvis/hud/servidor.py ──► jarvis/connect/central.py ─┐
voz/texto ──► cérebro Claude ──► ferramentas (jarvis/connect/ferramentas.py) ──────────────┤
                                                                                          ▼
                     jarvis/connect/motor.py  (agendador · gatilhos · condições · ações · fila)
                       │            │                   │                    │
           banco.py (SQLite)   cofre.py (DPAPI)    rede.py (repetição,    servicos.py → Gmail, Agenda,
           tarefas, automações, credenciais         limite de ritmo,      Evolution, IA isolada (ia.py)
           execuções, auditoria                      consumo)
                       ▲
webhooks.py (127.0.0.1:8766) ◄── Evolution (Docker, local) / Cloudflare Tunnel (serviços da internet)
```

| Camada | Arquivo | Papel |
|---|---|---|
| Frontend | `jarvis/hud/web/central.js`, `central.css`, `icones.js` (Lucide) | A Central dentro da HUD. Não usa innerHTML: o que vem de fora entra como texto. |
| Backend | `jarvis/connect/central.py` | Valida cada pedido da HUD; segredos nunca voltam (só `••••••1234`). |
| Banco | `jarvis/connect/banco.py` → `data/connect/connect.db` | Tarefas, automações, execuções (com chave única: não duplica), linha do tempo, consumo. |
| Autenticação | `jarvis/connect/cofre.py` → `data/connect/cofre.json`; `jarvis/tools/google_api.py` | Chaves cifradas com a DPAPI do Windows. OAuth do Google com PKCE e escopo por serviço. |
| Webhooks | `jarvis/connect/webhooks.py` | Servidor à parte; segredo no cabeçalho, segredo no caminho ou HMAC-SHA256; até 256 KB. |
| Fila | `Motor.enfileirar` (2 trabalhadores) | Execuções em segundo plano; repetição por ação; sem duplicar. |
| IA | `jarvis/connect/ia.py` | Claude Haiku **sem ferramentas** (`--tools ""`), numa pasta vazia. Sem o claude, usa o Qwen local. |
| Conectores | `jarvis/connect/conectores/*.py` | Um arquivo por serviço, com `@conector`. |

### Adicionar um serviço novo (sem mexer no núcleo)

Crie `jarvis/connect/conectores/meuservico.py`:

```python
from jarvis.connect.conectores.base import Campo, Conector, Status, conector

@conector
class MeuServico(Conector):
    id = "meuservico"; nome = "Meu Serviço"; categoria = "Produtividade"; icone = "plug"
    descricao = "..."; autenticacao = "api_key"; situacao = "implementado"
    docs = "https://..."; requisitos = "..."; custo = "..."; limites = "..."
    permissoes = ("...",); campos = (Campo("apikey", "Chave", segredo=True),)

    def status(self):
        return Status("conectado") if self.cofre.ler(self.id).get("apikey") else Status("desconectado")
```

Ele aparece sozinho na Central. Para virar gatilho, publique eventos com `motor.publicar("meuservico.evento", dados, id)`.
Para virar ação, inclua o tipo em `TIPOS_ACAO` (`motor.py`) e a função em `servicos.py`.

## Segurança (as escolhas do Iury)

- **Envio aprovado ao criar.** Uma automação que manda WhatsApp ou e-mail pede um 🔴 uma vez. Por voz, a confirmação
  de sempre. Na Central, o botão **Aprovar e ligar**. As duas mostram destinatário e texto exatos. O código guarda o
  hash do fluxo: **mudou qualquer coisa, o envio fica bloqueado até aprovar de novo**.
- **Para você ("eu")**, qualquer conteúdo, inclusive o resumo da IA. Configure `connect.meu_whatsapp` e
  `connect.meu_email`.
- **Para outra pessoa**, só os destinos e o texto que você aprovou:
  - os destinos: um número/e-mail fixo, ou `{mensagem.de}` (responder quem mandou a mensagem no WhatsApp);
  - o texto: **fixo**. Valem só as variáveis seguras: `{data}`, `{hora}`, `{amanha}` e os dados do seu compromisso.
  Nada gerado por IA nem copiado do que chegou. Assim um e-mail malicioso não consegue usar o Jarvis para mandar
  mensagem a ninguém. A regra é conferida na criação **e de novo na hora de enviar**.
- **Fora das automações**, nada mudou: `whatsapp_enviar` e `enviar_email` continuam sempre 🔴. Ler um site ou e-mail
  continua deixando as ações 🟡 como 🔴 nos 3 pedidos seguintes, inclusive `criar_automacao`.
- **A IA das automações não tem ferramentas.** Ela só devolve texto, então uma instrução escondida num e-mail não tem
  o que executar.
- **Webhooks:**
  - o servidor é separado da HUD;
  - a Evolution manda o cabeçalho `x-jarvis-segredo`;
  - serviços externos usam `/webhook/geral/{segredo}` ou assinatura HMAC (`X-Hub-Signature-256`);
  - corpo grande é recusado e evento repetido é descartado.
- **Credenciais:**
  - cifradas com a DPAPI: só abrem com o seu usuário do Windows, neste PC;
  - nunca no `config.yaml`, nunca para a HUD;
  - desconectar apaga deste PC. O Google é revogado no próprio Google.
- **Robustez:**
  - **repetição:** 429 e 5xx são repetidos, respeitando o `Retry-After`;
  - **limite de ritmo:** um balde de fichas por serviço;
  - **sem duplicar:** cada execução tem uma chave única.
- **Auditoria.** Cada criação, aprovação, teste, conexão, webhook recusado e execução vai para a linha do tempo,
  que aparece no Painel e no histórico de cada integração.

## Configurar

### WhatsApp pela Evolution API (grátis)

1. Instale o **Docker Desktop**.
2. Prepare a pasta `servicos/evolution`: copie `.env.exemplo` para `.env` e troque `AUTHENTICATION_API_KEY` e
   `POSTGRES_PASSWORD` por textos longos.
3. Na mesma pasta, rode `docker compose up -d`. A Evolution fica em `http://127.0.0.1:8080`, só neste PC.
4. Na Central, abra **Integrações > WhatsApp (Evolution API) > Configurar**, cole a chave e salve.
5. Clique em **Conectar**. A Central cria a instância, liga o webhook e mostra o QR code. No celular, abra o WhatsApp
   e vá em **Aparelhos conectados > Conectar**.
6. Clique em **Testar**: deve responder "WhatsApp pareado e webhook de mensagens ligado".

- **Docs:** <https://doc.evolution-api.com/v2/pt/get-started/introduction>
- **Custo:** zero (Apache 2.0).
- **Limitação:** a Evolution não é oficial (usa o protocolo do WhatsApp Web). Uso pessoal é o normal; disparo em
  massa pode bloquear o número.
- **O webhook:** a Evolution, no Docker, alcança o Jarvis por `http://host.docker.internal:8766/webhook/evolution`.
  Se o Docker não for o Desktop, troque esse endereço no campo "Endereço do webhook".

### Gmail e Agenda (Google)

1. No Google Cloud Console, crie um projeto e ligue a **Gmail API** e a **Google Calendar API** (e a **People API**,
   para achar contatos).
2. Na tela de consentimento OAuth, coloque o seu e-mail como usuário de teste.
3. Crie um **ID do cliente OAuth** do tipo **App para computador** e salve o JSON em `data/google/cliente.json`.
4. Na Central, clique em **Gmail > Conectar** (ou **Agenda > Conectar**). Cada serviço pede só os escopos dele:
   - **Gmail:** `gmail.readonly` + `gmail.send`;
   - **Agenda:** `calendar.events`;
   - **Contatos**, junto: `contacts.readonly`.

- **Custo:** zero. Cota do Gmail: 1 bilhão de unidades/dia por projeto. Cota da Agenda: 1 milhão de consultas/dia.
- **Limitação:** com o app em modo **Teste**, o login vence a cada 7 dias (é só conectar de novo). "Organizar e-mails"
  (marcadores/arquivar) ainda não está pronto: pediria o escopo `gmail.modify`.
- **Docs:** <https://developers.google.com/gmail/api/guides> · <https://developers.google.com/calendar/api/guides/overview>

### Webhooks da internet (Cloudflare Tunnel, grátis)

Só é preciso para serviços **de fora** do PC; a Evolution é local e não precisa.

1. Instale o `cloudflared`.
2. Rode `cloudflared tunnel --url http://127.0.0.1:8766`.
3. Cole o endereço `https://….trycloudflare.com` em **Central > Webhooks > Configurar**.
4. A Central mostra o endereço completo para copiar no serviço (`https://…/webhook/geral/<segredo>`). O segredo
   é gerado e guardado no cofre.

O túnel só alcança o servidor de webhooks; a HUD continua inalcançável de fora. O Quick Tunnel muda de endereço a cada
reinício; para um endereço fixo, crie um túnel com nome num domínio seu (também grátis).
Docs: <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/>

### Microsoft (planejado)

Usará o Microsoft Graph, com OAuth 2.0 + PKCE (app "cliente público" no Microsoft Entra). Ficha de cada serviço:

| Serviço | Permissões delegadas | Documentação |
|---|---|---|
| Outlook | `Mail.Read`, `Mail.Send` | <https://learn.microsoft.com/graph/outlook-mail-concept-overview> |
| Calendário | `Calendars.ReadWrite` | <https://learn.microsoft.com/graph/outlook-calendar-concept-overview> |
| OneDrive | `Files.Read` | <https://learn.microsoft.com/graph/onedrive-concept-overview> |
| To Do | `Tasks.ReadWrite` | <https://learn.microsoft.com/graph/todo-concept-overview> |
| Teams | `Chat.Read`, `ChatMessage.Send` | <https://learn.microsoft.com/graph/teams-concept-overview> |

Todos usam também `offline_access`. É grátis para conta pessoal.

### WhatsApp oficial (Meta Cloud API, planejado)

- **Requisitos:** conta Meta Business verificada, um número próprio (não pode ser o do app no celular), token permanente
  e o túnel para o webhook (assinado com `X-Hub-Signature-256`; a validação já existe em `webhooks.assinatura_ok`).
- **Custo:** responder dentro de 24 h é grátis; iniciar conversa usa modelo aprovado, cobrado por mensagem.
- **Docs:** <https://developers.facebook.com/docs/whatsapp/cloud-api>

## Automações: o formato

```json
{"gatilho":  {"tipo": "horario", "repetir": "semanal", "dias": [0], "hora": "07:30"},
 "condicoes": [{"campo": "mensagem.texto", "op": "contem", "valor": "orçamento, orcamento"}],
 "acoes": [{"tipo": "ver_agenda", "params": {"dias": 7}, "salvar_como": "agenda"},
           {"tipo": "ia", "params": {"pedido": "Monte o plano da semana", "contexto": "{resultado.agenda}"},
            "salvar_como": "plano"},
           {"tipo": "whatsapp", "params": {"para": "eu", "texto": "{resultado.plano}"}, "tentativas": 2},
           {"tipo": "avisar", "params": {"texto": "Plano enviado."}, "paralelo": true}]}
```

**Gatilhos:**
- `horario`, com `repetir` = `nunca`, `diario`, `semanal`, `mensal` ou `intervalo`;
- `evento`, com `evento` = `whatsapp.mensagem`, `gmail.novo`, `agenda.proxima` (com `minutos_antes`) ou `webhook`;
- `manual`.

**Condições:** `contem`, `nao_contem`, `igual`, `comeca`, `regex`, `de` e `ia` (uma pergunta de sim/não para a IA).
O `modo_condicoes` diz se valem `todas` ou `qualquer`.

**Ações:** `avisar`, `criar_tarefa`, `ler_emails`, `ver_agenda`, `ia` (`json: true` para extrair dados), `whatsapp`,
`email` e `criar_evento`. Cada ação aceita:
- `salvar_como`: guarda o resultado para as próximas usarem como `{resultado.nome}`;
- `paralelo`: roda junto com a anterior;
- `tentativas`: de 1 a 5;
- `se_falhar`: `parar` ou `continuar`.

**Testar** roda de mentira: lê de verdade (e-mails, agenda, IA), mas só **descreve** o que enviaria ou criaria.

**Modelos prontos:**
- resumo diário dos e-mails;
- mensagem com "orçamento" vira tarefa;
- planejamento semanal;
- reunião chegando: aviso no WhatsApp;
- compromisso no WhatsApp vai para a Agenda (com resposta automática fixa);
- lembrete com hora marcada.

## Pedido com várias integrações

Exemplo: "veja meus compromissos de amanhã, confira se tenho algum e-mail importante relacionado e me envie um resumo
pelo WhatsApp". O Claude resolve esse pedido em sete passos:
1. Entende o que foi pedido.
2. Escolhe as integrações necessárias.
3. Confere as conexões com `ver_integracoes`.
4. Consulta a agenda com `ver_agenda` e os e-mails com `ler_emails`.
5. Junta as informações e monta o resumo.
6. Envia com `whatsapp_enviar` (🔴, você confirma).
7. Conta o resultado.

Para repetir todo dia, peça para virar automação.

## Ainda não feito

- Gmail push (Pub/Sub): hoje o Gmail é consultado a cada 2 min, só quando alguma automação precisa.
- Organizar e-mails (marcadores), Drive, Google Tasks e todos os da Microsoft: estão no catálogo, com os requisitos.
- Resposta automática com texto gerado por IA para terceiros: bloqueada de propósito pela política de envio.
