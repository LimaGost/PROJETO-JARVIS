"""IA, automação, desenvolvimento, casa inteligente e o catálogo Microsoft (planejado)."""
import os
import shutil

from jarvis.config import RAIZ
from jarvis.connect.conectores.base import Campo, Conector, Status, conector

# ------------------------------------------------------------------ Inteligência Artificial


@conector
class ClaudeCode(Conector):
    id = "claude"
    nome = "Claude (assinatura)"
    categoria = "Inteligência Artificial"
    icone = "brain"
    descricao = "O cérebro principal: entende o pedido, escolhe as integrações e monta as automações."
    autenticacao = "token"
    situacao = "implementado"
    docs = "https://docs.claude.com/en/docs/claude-code/overview"
    requisitos = "Claude Code instalado e logado ('claude' no terminal) com a sua assinatura."
    custo = "Incluído na assinatura do Claude (sem cobrança por chamada)."
    limites = "Limites de uso da assinatura (janela de 5 h)."
    permissoes = ("Usa as ferramentas do Jarvis pela ponte MCP, com as mesmas permissões 🟢🟡🔴",)
    recursos = ("Linguagem natural", "Visão de tela", "Criar automações", "Resumos com IA nas automações")

    def status(self) -> Status:
        return Status("conectado", "claude encontrado") if shutil.which("claude") else \
            Status("desconectado", "o comando 'claude' não foi encontrado")


@conector
class ClaudeAPI(Conector):
    id = "claude_api"
    nome = "Claude API"
    categoria = "Inteligência Artificial"
    icone = "key-round"
    descricao = "Alternativa à assinatura: Claude pela API da Anthropic."
    autenticacao = "api_key"
    situacao = "implementado"
    docs = "https://docs.claude.com/en/api/overview"
    requisitos = "Chave da API em ANTHROPIC_API_KEY ou data/anthropic.key (nunca no config.yaml)."
    custo = "Pago por token (tabela da Anthropic)."
    limites = "Limites de ritmo do seu nível na Anthropic."
    permissoes = ("Mensagens",)

    def status(self) -> Status:
        if os.environ.get("ANTHROPIC_API_KEY") or (RAIZ / "data" / "anthropic.key").exists():
            return Status("conectado", "chave encontrada")
        return Status("desconectado", "sem chave (opcional)")


@conector
class Ollama(Conector):
    id = "ollama"
    nome = "Ollama (Qwen local)"
    categoria = "Inteligência Artificial"
    icone = "cpu"
    descricao = "IA 100% local: o Qwen e os embeddings da memória e da base de conhecimento."
    autenticacao = "local"
    situacao = "implementado"
    docs = "https://github.com/ollama/ollama/blob/main/docs/api.md"
    requisitos = "Ollama aberto, com qwen3:8b e embeddinggemma baixados."
    custo = "Grátis (roda na sua GPU)."
    limites = "8 GB de VRAM: Qwen + Whisper + voz clonada não cabem juntos (o modo jogo cuida disso)."
    permissoes = ("Local, sem internet",)

    def status(self) -> Status:
        import urllib.request
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2).read()
            return Status("conectado", "Ollama respondendo")
        except Exception:
            return Status("desconectado", "Ollama fechado")


# ------------------------------------------------------------------ Automação


@conector
class Agendador(Conector):
    id = "agendador"
    nome = "Agendador do Jarvis"
    categoria = "Automação"
    icone = "alarm-clock"
    descricao = "Dispara as automações por horário (uma vez, todo dia, dias da semana, a cada N minutos)."
    autenticacao = "local"
    situacao = "implementado"
    requisitos = "Nenhum: roda junto com o Jarvis."
    custo = "Grátis."
    limites = "O Jarvis precisa estar ligado. Horário perdido com o PC desligado roda uma vez quando ele volta."
    permissoes = ("Executa só automações aprovadas por você",)

    def status(self) -> Status:
        from jarvis.connect import motor
        return Status("conectado", "rodando") if motor.ativo() else Status("desconectado", "o motor não iniciou")


@conector
class Webhooks(Conector):
    id = "webhooks"
    nome = "Webhooks (Cloudflare Tunnel)"
    categoria = "Automação"
    icone = "webhook"
    descricao = ("Recebe eventos de serviços da internet num servidor à parte (127.0.0.1:8766). O túnel da Cloudflare "
                 "expõe só esse servidor; a HUD continua fechada.")
    autenticacao = "webhook"
    situacao = "implementado"
    docs = "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/"
    requisitos = ("Para serviços locais (Evolution) nada. Para serviços da internet: cloudflared instalado e "
                  "'cloudflared tunnel --url http://127.0.0.1:8766'; cole o endereço público aqui.")
    custo = "Grátis (Quick Tunnel; endereço fixo precisa de domínio na Cloudflare, também grátis)."
    limites = "Quick Tunnel muda de endereço a cada reinício. Cada rota exige segredo ou assinatura."
    permissoes = ("Recebe eventos; nada é executado sem uma automação aprovada",)
    campos = (Campo("url_publica", "Endereço público do túnel (https://...trycloudflare.com)"),)

    def status(self) -> Status:
        from jarvis.connect import webhooks
        if not webhooks.ativo():
            return Status("desconectado", "servidor de webhooks parado")
        publica = (self.cofre.ler(self.id).get("url_publica") or "").rstrip("/")
        if not publica:
            return Status("conectado", f"ouvindo em {webhooks.endereco()} (sem túnel: só serviços deste PC)")
        # o endereço completo é para copiar no serviço de fora: a HUD é local e protegida por token
        return Status("conectado", f"use {publica}/webhook/geral/{self.cofre.segredo(self.id)}")


# ------------------------------------------------------------------ Desenvolvimento


@conector
class VSCode(Conector):
    id = "vscode"
    nome = "VS Code + Claude Code"
    categoria = "Desenvolvimento"
    icone = "code"
    descricao = "'Programar': o Claude Code trabalha no projeto aberto no VS Code, em segundo plano."
    autenticacao = "local"
    situacao = "implementado"
    requisitos = "VS Code e Claude Code instalados."
    custo = "Incluído na assinatura do Claude."
    limites = "Sem git push; fora do E:."
    permissoes = ("Arquivos e comandos dentro do projeto aberto",)

    def status(self) -> Status:
        return Status("conectado") if shutil.which("code") or shutil.which("claude") else Status("desconectado")


@conector
class GitHub(Conector):
    id = "github"
    nome = "GitHub"
    categoria = "Desenvolvimento"
    icone = "github"
    descricao = "Issues, pull requests e avisos de CI como gatilhos de automação."
    autenticacao = "token"
    situacao = "planejado"
    docs = "https://docs.github.com/en/rest"
    requisitos = "Token fine-grained com acesso só aos repositórios escolhidos; webhook assinado (X-Hub-Signature-256)."
    custo = "Grátis."
    limites = "5.000 requisições/hora com token."
    permissoes = ("issues:read", "pull_requests:read", "metadata:read")


# ------------------------------------------------------------------ Casa Inteligente


@conector
class HomeAssistant(Conector):
    id = "home_assistant"
    nome = "Home Assistant"
    categoria = "Casa Inteligente"
    icone = "house"
    descricao = "Luzes, tomadas e sensores da casa por voz e em automações."
    autenticacao = "token"
    situacao = "planejado"
    docs = "https://developers.home-assistant.io/docs/api/rest/"
    requisitos = "Home Assistant na rede local e um token de longa duração (perfil > Segurança)."
    custo = "Grátis (código aberto)."
    limites = "Só na rede de casa (endereço local liberado só para este conector)."
    permissoes = ("Ler estados", "Chamar serviços escolhidos (ex.: light.turn_on)")


# ------------------------------------------------------------------ Microsoft (Graph): catálogo


_MS_REQUISITO = ("App registrado no Microsoft Entra (portal.azure.com > Registros de aplicativo), tipo 'cliente "
                 "público/nativo' com redirecionamento http://localhost, PKCE, e as permissões delegadas abaixo.")
_MS_CUSTO = "Grátis para conta pessoal (Microsoft Graph)."
_MS_LIMITES = "Limites do Graph por app/usuário (o Jarvis reduz o ritmo sozinho com o Retry-After)."


def _ms(id_, nome, categoria, icone, descricao, permissoes, docs):
    classe = type(id_, (Conector,), {
        "id": id_, "nome": nome, "categoria": categoria, "icone": icone, "descricao": descricao,
        "autenticacao": "oauth2_pkce", "situacao": "planejado", "docs": docs, "requisitos": _MS_REQUISITO,
        "custo": _MS_CUSTO, "limites": _MS_LIMITES, "permissoes": permissoes})
    return conector(classe)


_ms("outlook", "Outlook", "Comunicação", "mail-open", "Ler, resumir e enviar e-mails do Outlook/Hotmail.",
    ("Mail.Read", "Mail.Send", "offline_access"), "https://learn.microsoft.com/graph/outlook-mail-concept-overview")
_ms("ms_agenda", "Calendário Microsoft", "Produtividade", "calendar-days", "Compromissos do Outlook.",
    ("Calendars.ReadWrite", "offline_access"), "https://learn.microsoft.com/graph/outlook-calendar-concept-overview")
_ms("onedrive", "OneDrive", "Armazenamento", "cloud", "Localizar e ler arquivos do OneDrive.",
    ("Files.Read", "offline_access"), "https://learn.microsoft.com/graph/onedrive-concept-overview")
_ms("ms_todo", "Microsoft To Do", "Produtividade", "square-check", "Sincronizar tarefas com o To Do.",
    ("Tasks.ReadWrite", "offline_access"), "https://learn.microsoft.com/graph/todo-concept-overview")
_ms("teams", "Microsoft Teams", "Comunicação", "users", "Mensagens e reuniões do Teams.",
    ("Chat.Read", "ChatMessage.Send", "offline_access"), "https://learn.microsoft.com/graph/teams-concept-overview")
