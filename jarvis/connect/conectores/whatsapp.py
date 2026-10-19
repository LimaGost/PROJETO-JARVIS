"""WhatsApp: Evolution API (implementado, grátis), WhatsApp Desktop (implementado, só envio) e os provedores pagos
/oficiais no catálogo (Meta Cloud API, Z-API)."""
from jarvis.connect.conectores.base import Campo, Conector, Status, conector
from jarvis.connect.evolution import PADRAO_INSTANCIA, PADRAO_URL, Evolution

URL_WEBHOOK = "http://host.docker.internal:8766/webhook/evolution"


@conector
class EvolutionWhatsApp(Conector):
    id = "evolution"
    nome = "WhatsApp (Evolution API)"
    categoria = "Comunicação"
    icone = "message-circle"
    descricao = "Seu WhatsApp pelo QR code: enviar, receber por webhook, conversas e contatos. Grátis, roda no seu PC."
    autenticacao = "api_key"
    situacao = "implementado"
    docs = "https://doc.evolution-api.com/v2/pt/get-started/introduction"
    requisitos = ("Docker Desktop. Suba servicos/evolution (docker compose up -d), ponha a chave do .env aqui e leia "
                  "o QR code pela Central.")
    custo = "Grátis (código aberto, Apache 2.0). Só a luz do PC."
    limites = ("Não oficial (protocolo do WhatsApp Web): uso pessoal tranquilo; disparo em massa pode bloquear o número. "
               "O PC precisa estar ligado para receber.")
    permissoes = ("Ler mensagens recebidas (webhook)", "Ler conversas e contatos", "Enviar texto (sempre com sua "
                  "aprovação: na hora, ou ao criar a automação)")
    recursos = ("Enviar mensagem", "Receber por webhook", "Conversas e contatos", "Gatilho de automação",
                "Resumo de conversa com IA")
    campos = (Campo("url", "Endereço da Evolution", padrao=PADRAO_URL),
              Campo("apikey", "Chave (AUTHENTICATION_API_KEY do .env)", segredo=True),
              Campo("instancia", "Nome da instância", padrao=PADRAO_INSTANCIA,
                    dica="Pode deixar 'jarvis'; a Central cria se não existir."),
              Campo("url_webhook", "Endereço do webhook visto pela Evolution", padrao=URL_WEBHOOK,
                    dica="Com a Evolution no Docker Desktop, host.docker.internal é este PC."))

    def cliente(self) -> Evolution:
        c = self.cofre.ler(self.id)
        return Evolution(c.get("url") or PADRAO_URL, c.get("apikey", ""), c.get("instancia") or PADRAO_INSTANCIA)

    def configurado(self) -> bool:
        return bool(self.cofre.ler(self.id).get("apikey"))

    def status(self) -> Status:
        if not self.configurado():
            return Status("desconectado", "falta a chave da Evolution")
        try:
            estado = self.cliente().estado()
        except Exception as e:
            return Status("erro", str(e)[:200])
        if estado == "open":
            return Status("conectado", "WhatsApp pareado")
        if estado == "connecting":
            return Status("aguardando", "esperando você ler o QR code")
        return Status("aguardando", f"WhatsApp não pareado (estado: {estado})")

    def testar(self) -> tuple[bool, str]:
        st = self.status()
        if st.estado == "conectado":
            try:
                self.ligar_webhook()
                return True, "Evolution respondeu, o WhatsApp está pareado e o webhook de mensagens está ligado."
            except Exception as e:
                return False, f"WhatsApp pareado, mas não consegui ligar o webhook de mensagens ({e})"
        if st.estado == "erro":
            return False, (f"A Evolution não respondeu ({st.detalhe}). Ela está rodando? (servicos/evolution: "
                           "docker compose up -d)")
        return False, st.detalhe

    def ligar_webhook(self, cli: Evolution | None = None):
        """Diz à Evolution para mandar as mensagens recebidas ao servidor de webhooks do Jarvis, com o segredo."""
        url = self.cofre.ler(self.id).get("url_webhook") or URL_WEBHOOK
        (cli or self.cliente()).configurar_webhook(url, self.cofre.segredo(self.id))

    def conectar(self) -> dict:
        """Cria a instância se precisar e devolve o QR code para a Central mostrar."""
        if not self.configurado():
            raise ValueError("preencha a chave da Evolution e salve antes")
        cli = self.cliente()
        try:
            if cli.estado() == "open":
                return {"mensagem": "Já está conectado."}
        except Exception:
            cli.criar_instancia()               # 404: a instância ainda não existe
        self.ligar_webhook(cli)
        qr = cli.qrcode()
        if not qr.get("base64") and not qr.get("pairingCode"):
            return {"mensagem": "Já está conectado ou a Evolution ainda está gerando o QR; tente de novo em 5 s."}
        return {"mensagem": "Abra o WhatsApp no celular > Aparelhos conectados > Conectar e leia o código.",
                "qrcode": qr.get("base64", ""), "codigo": qr.get("pairingCode") or ""}


@conector
class WhatsAppDesktop(Conector):
    id = "whatsapp_desktop"
    nome = "WhatsApp Desktop"
    categoria = "Comunicação"
    icone = "monitor-smartphone"
    descricao = "Envia mensagem abrindo o app do WhatsApp deste PC. Usado quando a Evolution não está ligada."
    autenticacao = "local"
    situacao = "implementado"
    docs = "https://faq.whatsapp.com/1317564962315842"
    requisitos = "WhatsApp Desktop instalado e logado."
    custo = "Grátis."
    limites = "Só envia (não lê nem recebe). Precisa da janela do WhatsApp; o Jarvis só aperta Enter se ela estiver na frente."
    permissoes = ("Enviar texto (sempre pede confirmação)",)
    recursos = ("Enviar mensagem",)

    def status(self) -> Status:
        import sys
        if sys.platform != "win32":
            return Status("desconectado", "só no Windows")
        return Status("conectado", "usa o app instalado neste PC")


@conector
class MetaCloud(Conector):
    id = "whatsapp_meta"
    nome = "WhatsApp Business (Meta Cloud API)"
    categoria = "Comunicação"
    icone = "badge-check"
    descricao = "API oficial da Meta: número comercial verificado, webhooks assinados, sem risco de bloqueio."
    autenticacao = "token"
    situacao = "planejado"
    docs = "https://developers.facebook.com/docs/whatsapp/cloud-api"
    requisitos = ("Conta Meta Business verificada, app no developers.facebook.com, número próprio (não pode ser o que "
                  "está no app do celular), token permanente de usuário do sistema e o túnel para o webhook.")
    custo = ("Responder em até 24 h após a mensagem do cliente: grátis. Iniciar conversa: modelo aprovado, cobrado por "
             "mensagem (tabela da Meta por país).")
    limites = "Fora da janela de 24 h só modelos aprovados. Limite inicial de destinatários por dia (sobe com a qualidade)."
    permissoes = ("whatsapp_business_messaging", "whatsapp_business_management")
    recursos = ("Enviar e receber", "Webhook assinado (X-Hub-Signature-256)", "Modelos de mensagem")


@conector
class ZApi(Conector):
    id = "zapi"
    nome = "Z-API"
    categoria = "Comunicação"
    icone = "zap"
    descricao = "Provedor brasileiro de WhatsApp pelo seu número (QR code), na nuvem."
    autenticacao = "token"
    situacao = "planejado"
    docs = "https://developer.z-api.io/"
    requisitos = "Conta na Z-API, instância paga, Client-Token."
    custo = "Pago por instância (por mês)."
    limites = "Não oficial, como a Evolution (mesmo risco de bloqueio em uso abusivo)."
    permissoes = ("Enviar e receber mensagens",)
    recursos = ("Enviar e receber", "Webhook")
