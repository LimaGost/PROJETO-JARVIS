"""JARVIS Connect: a central de integrações, tarefas e automações do Jarvis.

    conectores/   um arquivo por serviço (Gmail, Agenda, WhatsApp/Evolution, Microsoft...). Serviço novo = arquivo
                  novo com @conector; o núcleo não muda.
    banco.py      SQLite local (data/connect/connect.db): tarefas, automações, execuções, linha do tempo, consumo de API
    cofre.py      credenciais criptografadas (DPAPI do Windows): nunca vão para a HUD nem para o config.yaml
    rede.py       chamadas HTTP com repetição, limite de ritmo e registro de consumo
    motor.py      agendador + gatilhos por evento + execução das automações (fila, sem duplicar)
    webhooks.py   servidor separado (127.0.0.1:8766) só para receber webhooks, com segredo/assinatura
    ferramentas.py  o que o cérebro usa: criar tarefa/automação por linguagem natural, listar, testar...

Segurança (escolhas do Iury, 2026-10-19):
  - automação que ENVIA (WhatsApp/e-mail) é aprovada uma vez, ao criar, vendo destinatário e texto; ela só pode
    mandar para quem foi aprovado. Mudou o fluxo, perdeu a aprovação. Mandar para o próprio Iury é livre;
    para terceiros só texto fixo aprovado (nada gerado por IA nem copiado de mensagem recebida);
  - webhooks chegam num servidor à parte (o túnel Cloudflare aponta só para ele); a HUD continua só em 127.0.0.1.
"""
