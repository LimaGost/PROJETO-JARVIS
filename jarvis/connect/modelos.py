"""Modelos prontos de automação (a Central cria a partir deles; dá para editar antes de ligar)."""

MODELOS = [
    {"id": "resumo_emails", "nome": "Resumo diário dos e-mails", "icone": "mail",
     "descricao": "Todo dia às 8h, lê os e-mails não lidos e te conta só o que importa.",
     "fluxo": {"gatilho": {"tipo": "horario", "repetir": "diario", "hora": "08:00"},
               "acoes": [{"tipo": "ler_emails", "params": {"busca": "is:unread newer_than:1d", "quantos": 15},
                          "salvar_como": "emails"},
                         {"tipo": "ia", "params": {"pedido": "Resuma em até 5 frases os e-mails importantes (clientes, "
                                                             "prazos, cobranças). Ignore propaganda.",
                                                   "contexto": "{resultado.emails}"}, "salvar_como": "resumo"},
                         {"tipo": "avisar", "params": {"texto": "Resumo dos e-mails: {resultado.resumo}"}}]}},
    {"id": "orcamento_tarefa", "nome": "Orçamento no WhatsApp vira tarefa", "icone": "list-checks",
     "descricao": "Mensagem com 'orçamento' cria uma tarefa de prioridade alta e te avisa.",
     "fluxo": {"gatilho": {"tipo": "evento", "evento": "whatsapp.mensagem"},
               "condicoes": [{"campo": "mensagem.texto", "op": "contem", "valor": "orçamento, orcamento"}],
               "acoes": [{"tipo": "criar_tarefa", "params": {"titulo": "Orçamento: {mensagem.nome}",
                                                             "descricao": "{mensagem.texto}", "prioridade": "alta"}},
                         {"tipo": "avisar", "params": {"texto": "{mensagem.nome} pediu um orçamento no WhatsApp."}}]}},
    {"id": "planejamento_semanal", "nome": "Planejamento semanal", "icone": "calendar-days",
     "descricao": "Toda segunda às 7h30, monta o plano da semana pela agenda e te manda no WhatsApp.",
     "fluxo": {"gatilho": {"tipo": "horario", "repetir": "semanal", "dias": [0], "hora": "07:30"},
               "acoes": [{"tipo": "ver_agenda", "params": {"dias": 7}, "salvar_como": "agenda"},
                         {"tipo": "ia", "params": {"pedido": "Monte um planejamento curto da semana, por dia, com os "
                                                             "compromissos e um lembrete de preparação.",
                                                   "contexto": "{resultado.agenda}"}, "salvar_como": "plano"},
                         {"tipo": "whatsapp", "params": {"para": "eu", "texto": "Planejamento da semana:\n{resultado.plano}"}}]}},
    {"id": "reuniao_whatsapp", "nome": "Reunião chegando: aviso no WhatsApp", "icone": "alarm-clock",
     "descricao": "15 minutos antes de cada compromisso, te avisa no WhatsApp.",
     "fluxo": {"gatilho": {"tipo": "evento", "evento": "agenda.proxima", "minutos_antes": 15},
               "acoes": [{"tipo": "whatsapp", "params": {"para": "eu", "texto": "Daqui a {evento.minutos} min: "
                                                                               "{evento.titulo} ({evento.hora})"}},
                         {"tipo": "avisar", "params": {"texto": "Daqui a pouco: {evento.titulo}.", },
                          "paralelo": True}]}},
    {"id": "compromisso_agenda", "nome": "Compromisso no WhatsApp vai para a Agenda", "icone": "calendar-plus",
     "descricao": "A IA vê se a mensagem marca um compromisso, cria o evento e responde confirmando.",
     "fluxo": {"gatilho": {"tipo": "evento", "evento": "whatsapp.mensagem"},
               "condicoes": [{"campo": "mensagem.texto", "op": "ia",
                              "valor": "Esta mensagem combina um compromisso com dia e horário?"}],
               "acoes": [{"tipo": "ia", "params": {"pedido": "Extraia o compromisso. Hoje é {data}. Responda só JSON: "
                                                             "{\"titulo\": \"...\", \"inicio\": \"AAAA-MM-DDTHH:MM\"}",
                                                   "contexto": "{mensagem.nome}: {mensagem.texto}", "json": True},
                          "salvar_como": "compromisso"},
                         {"tipo": "criar_evento", "params": {"titulo": "{resultado.compromisso.titulo}",
                                                             "inicio": "{resultado.compromisso.inicio}",
                                                             "duracao_min": 60}},
                         {"tipo": "whatsapp", "params": {"para": "{mensagem.de}",
                                                         "texto": "Anotado na agenda! Até lá. ✅"}},
                         {"tipo": "avisar", "params": {"texto": "Marquei na agenda: {resultado.compromisso.titulo} "
                                                                "({resultado.compromisso.inicio})."}, "paralelo": True}]}},
    {"id": "lembrete_unico", "nome": "Lembrete com hora marcada", "icone": "bell",
     "descricao": "Um aviso só, no dia e hora que você escolher.",
     "fluxo": {"gatilho": {"tipo": "horario", "repetir": "nunca", "quando": ""},
               "acoes": [{"tipo": "avisar", "params": {"texto": "Lembrete: "}}]}},
]


def modelo(id_: str) -> dict:
    for m in MODELOS:
        if m["id"] == id_:
            return m
    raise KeyError(id_)
