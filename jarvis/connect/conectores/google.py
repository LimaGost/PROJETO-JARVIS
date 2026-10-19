"""Google: Gmail e Agenda (implementados), Drive e Tasks (no catálogo). Um login só (data/google/token.json), mas cada
serviço pede só os escopos dele (autorização incremental)."""
import threading

from jarvis.connect.conectores.base import Conector, Status, conector
from jarvis.tools import google_api

CONSOLE = "https://console.cloud.google.com/apis/credentials"
REQUISITO_COMUM = ("Projeto no Google Cloud com a API ligada, um ID do cliente OAuth do tipo 'App para computador' "
                   "salvo em data/google/cliente.json e o seu e-mail como usuário de teste (tela de consentimento).")
LIMITE_TESTE = "Com o app em modo 'Teste' no Google Cloud, o login vence a cada 7 dias (é só conectar de novo)."
_autorizando = threading.Lock()


class _Google(Conector):
    autenticacao = "oauth2_pkce"
    servico = ""
    teste_url = ""

    def status(self) -> Status:
        if self.situacao == "planejado":
            return super().status()
        if not google_api.CLIENTE.exists():
            return Status("desconectado", "falta data/google/cliente.json (veja os requisitos)")
        if not google_api.TOKEN.exists():
            return Status("aguardando", "autorize a conta Google")
        if not google_api.tem_servico(self.servico):
            return Status("aguardando", "falta autorizar este serviço na conta Google")
        if _autorizando.locked():
            return Status("aguardando", "esperando você autorizar no navegador")
        return Status("conectado", "conta Google autorizada")

    def testar(self) -> tuple[bool, str]:
        st = self.status()
        if st.estado != "conectado":
            return False, st.detalhe
        try:
            google_api.api("GET", self.teste_url)
        except Exception as e:
            return False, str(e)
        return True, f"{self.nome} respondeu."

    def conectar(self) -> dict:
        if self.situacao == "planejado":
            raise ValueError(f"{self.nome} ainda não está implementado")
        if not google_api.CLIENTE.exists():
            raise ValueError("falta data/google/cliente.json: crie o ID do cliente OAuth no Google Cloud (veja o README)")
        if not _autorizando.acquire(blocking=False):
            return {"mensagem": "Já há uma autorização aberta no navegador."}

        def autorizar():
            try:
                google_api.autorizar((self.servico, "contatos"))
            finally:
                _autorizando.release()
        threading.Thread(target=autorizar, daemon=True, name="google-oauth").start()
        return {"mensagem": "Abri o navegador: entre na sua conta Google e autorize. Volte aqui depois."}

    def desconectar(self) -> str:
        google_api.revogar()
        return ("Conta Google desconectada: o acesso foi revogado no Google e o token apagado deste PC "
                "(vale para todos os serviços Google, que dividem o mesmo login).")


@conector
class Gmail(_Google):
    id = "gmail"
    nome = "Gmail"
    categoria = "Comunicação"
    icone = "mail"
    descricao = "Ler, resumir e enviar e-mails. Gatilho 'e-mail novo' nas automações."
    situacao = "implementado"
    servico = "gmail"
    teste_url = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
    docs = "https://developers.google.com/gmail/api/guides"
    requisitos = REQUISITO_COMUM + " Ligue a 'Gmail API'."
    custo = "Grátis (cota de 1 bilhão de unidades/dia por projeto; ler 1 e-mail ≈ 5 unidades)."
    limites = LIMITE_TESTE + " Organizar (marcadores/arquivar) ainda não: pediria o escopo gmail.modify."
    permissoes = ("gmail.readonly (ler)", "gmail.send (enviar, sempre com sua aprovação)")
    recursos = ("Ler e resumir", "Enviar", "Gatilho de e-mail novo (consulta a cada poucos minutos)")


@conector
class Agenda(_Google):
    id = "agenda"
    nome = "Google Agenda"
    categoria = "Produtividade"
    icone = "calendar"
    descricao = "Consultar compromissos e criar eventos. Gatilho 'reunião chegando' nas automações."
    situacao = "implementado"
    servico = "agenda"
    teste_url = "https://www.googleapis.com/calendar/v3/calendars/primary"
    docs = "https://developers.google.com/calendar/api/guides/overview"
    requisitos = REQUISITO_COMUM + " Ligue a 'Google Calendar API'."
    custo = "Grátis (1 milhão de consultas/dia por projeto)."
    limites = LIMITE_TESTE + " Só a agenda principal."
    permissoes = ("calendar.events (ver e criar eventos; não apaga)",)
    recursos = ("Ver compromissos", "Criar eventos", "Gatilho de reunião próxima")


@conector
class Drive(_Google):
    id = "drive"
    nome = "Google Drive"
    categoria = "Armazenamento"
    icone = "hard-drive"
    descricao = "Localizar e ler documentos do Drive para estudar ou resumir."
    situacao = "planejado"
    docs = "https://developers.google.com/drive/api/guides/about-sdk"
    requisitos = REQUISITO_COMUM + " Ligue a 'Google Drive API'."
    custo = "Grátis (cota por usuário: 12 mil consultas/min)."
    limites = "drive.readonly é escopo restrito: app público exigiria verificação do Google (uso pessoal em 'Teste' tudo bem)."
    permissoes = ("drive.readonly (planejado: só leitura)",)
    recursos = ("Buscar arquivos", "Ler documentos")


@conector
class GoogleTasks(_Google):
    id = "google_tasks"
    nome = "Google Tasks"
    categoria = "Produtividade"
    icone = "list-checks"
    descricao = "Sincronizar as tarefas do Jarvis com o Google Tasks (celular)."
    situacao = "planejado"
    docs = "https://developers.google.com/tasks/overview"
    requisitos = REQUISITO_COMUM + " Ligue a 'Google Tasks API'."
    custo = "Grátis (50 mil consultas/dia)."
    limites = "Sem prioridade nem recorrência nativas (ficam só no Jarvis)."
    permissoes = ("tasks (planejado)",)
    recursos = ("Criar, editar e concluir tarefas",)
