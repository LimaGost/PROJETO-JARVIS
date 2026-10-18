"""Gmail, Agenda e WhatsApp: níveis de permissão, contatos, montagem do e-mail e o que vai para cada cérebro.
A API do Google é trocada por um falso: nenhum teste sai para a internet."""
import base64
import email
from datetime import datetime
from email import policy

import pytest

from jarvis.tools import agenda, gmail, google_api, whatsapp
from jarvis.tools.base import REGISTRO, ErroFerramenta, Nivel
from jarvis.tools.executor import NEGADO, Executor

NOVAS = ("ler_emails", "abrir_email", "enviar_email", "ver_agenda", "criar_evento", "whatsapp_enviar")


def b64(texto: str) -> str:
    return base64.urlsafe_b64encode(texto.encode()).decode().rstrip("=")


class GoogleFalso:
    """Responde pela URL; guarda o que foi enviado."""

    def __init__(self, contatos=None):
        self.enviados, self.criados = [], []
        self.contatos = contatos or []

    def __call__(self, metodo, url, params=None, corpo=None):
        if url.endswith("/messages/send"):
            self.enviados.append(corpo)
            return {"id": "novo1"}
        if url.endswith("/messages"):
            return {"messages": [{"id": "m1"}]}
        if "/messages/m1" in url and (params or {}).get("format") == "metadata":
            return {"id": "m1", "threadId": "t9", "labelIds": ["UNREAD"], "snippet": "Oi &amp; tchau",
                    "payload": {"headers": [{"name": "From", "value": "Ana <ana@x.com>"},
                                            {"name": "Subject", "value": "Reunião"},
                                            {"name": "Message-ID", "value": "<abc@x.com>"}]}}
        if "/messages/m1" in url:
            return {"payload": {"headers": [{"name": "From", "value": "Ana <ana@x.com>"}], "mimeType": "multipart/alternative",
                                "parts": [{"mimeType": "text/html", "body": {"data": b64("<p>Olá <b>Iury</b></p>")}}]}}
        if "searchContacts" in url:
            if not (params or {}).get("query"):
                return {}
            return {"results": [{"person": c} for c in self.contatos if params["query"].lower() in
                                c["names"][0]["displayName"].lower()]}
        if url == agenda.EVENTOS and metodo == "POST":
            self.criados.append(corpo)
            return {"htmlLink": "https://calendar.google.com/x"}
        if url == agenda.EVENTOS:
            return {"items": [{"summary": "Dentista", "start": {"dateTime": "2026-10-20T15:00:00-03:00"},
                               "end": {"dateTime": "2026-10-20T16:00:00-03:00"}}]}
        raise AssertionError(f"URL inesperada: {metodo} {url}")


def pessoa(nome, emails=(), telefones=()):
    return {"names": [{"displayName": nome}], "emailAddresses": [{"value": e} for e in emails],
            "phoneNumbers": [{"value": t, "canonicalForm": t} for t in telefones]}


@pytest.fixture
def google(monkeypatch):
    falso = GoogleFalso([pessoa("Maria Souza", ["maria@x.com"], ["+5511987654321"]),
                         pessoa("João Lima", ["joao@x.com"]), pessoa("João Pedro", ["jp@x.com"])])
    for modulo in (google_api, gmail, agenda):
        monkeypatch.setattr(modulo, "api", falso)
    monkeypatch.setattr(google_api, "_aquecido", False)
    return falso


def executor(tmp_path, respostas):
    perguntas = []

    def confirmar(resumo, **_):
        perguntas.append(resumo)
        return respostas.pop(0)
    return Executor(confirmar, str(tmp_path), avisar=lambda t: None), perguntas


# ------------------------------------------------------------------ quem recebe as ferramentas
def test_ferramentas_novas_so_vao_para_o_claude(tmp_path):
    ex, _ = executor(tmp_path, [])
    qwen = {s["function"]["name"] for s in ex.schemas()}
    claude = {s["function"]["name"] for s in ex.schemas(visao=True)}
    assert all(n in claude for n in NOVAS)
    assert not any(n in qwen for n in NOVAS)        # o contexto de 6144 tokens do Qwen não comporta mais 6


def test_niveis():
    assert REGISTRO["ler_emails"].e_externo({}) and REGISTRO["abrir_email"].e_externo({})
    assert REGISTRO["ver_agenda"].e_externo({})     # convites de terceiros têm título e descrição livres
    assert "apagar_email" not in REGISTRO           # nem a permissão de apagar é pedida ao Google


# ------------------------------------------------------------------ Gmail
def test_ler_emails_marca_conteudo_externo(google):
    texto = REGISTRO["ler_emails"].funcao(busca="is:unread")
    assert "id m1" in texto and "Ana <ana@x.com>" in texto and "Oi & tchau" in texto and "não confiável" in texto


def test_abrir_email_tira_o_html(google):
    texto = REGISTRO["abrir_email"].funcao(id="m1")
    assert "Olá Iury" in texto and "<b>" not in texto


def test_enviar_email_sempre_pede_confirmacao_e_mostra_o_destinatario(google, tmp_path):
    ex, perguntas = executor(tmp_path, [False])
    assert ex.executar("enviar_email", {"para": "maria", "assunto": "Oi", "corpo": "Tudo bem?"}) == NEGADO
    assert google.enviados == []
    assert "Maria Souza <maria@x.com>" in perguntas[0] and "Tudo bem?" in perguntas[0]


def test_enviar_email_com_sim_monta_a_mensagem(google, tmp_path):
    ex, _ = executor(tmp_path, [True])
    r = ex.executar("enviar_email", {"para": "bob@y.com", "assunto": "Teste", "corpo": "Corpo do e-mail"})
    assert "enviado para bob@y.com" in r
    msg = email.message_from_bytes(base64.urlsafe_b64decode(google.enviados[0]["raw"]), policy=policy.default)
    assert msg["To"] == "bob@y.com" and msg["Subject"] == "Teste" and "Corpo do e-mail" in msg.get_content()


def test_responder_email_usa_a_conversa_original(google, tmp_path):
    ex, perguntas = executor(tmp_path, [True])
    ex.executar("enviar_email", {"responder_a": "m1", "corpo": "Combinado."})
    enviado = google.enviados[0]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(enviado["raw"]), policy=policy.default)
    assert enviado["threadId"] == "t9" and msg["To"] == "ana@x.com" and msg["Subject"] == "Re: Reunião"
    assert msg["In-Reply-To"] == "<abc@x.com>" and "responder" in perguntas[0]


def test_contato_ambiguo_pede_para_perguntar(google, tmp_path):
    ex, perguntas = executor(tmp_path, [])
    r = ex.executar("enviar_email", {"para": "joão", "corpo": "oi"})
    assert "mais de uma opção" in r and "João Lima" in r and "João Pedro" in r and perguntas == []


def test_email_invalido_e_recusado(google, tmp_path):
    ex, _ = executor(tmp_path, [])
    assert "não parece um e-mail" in ex.executar("enviar_email", {"para": "fulano@", "corpo": "oi"})


def test_depois_de_ler_email_criar_evento_pede_confirmacao(google, tmp_path):
    ex, perguntas = executor(tmp_path, [False])
    ex.novo_turno()
    ex.executar("ler_emails", {})                   # um e-mail poderia mandar "marque uma reunião às 3h"
    assert ex.executar("criar_evento", {"titulo": "X", "inicio": "2026-10-20T15:00"}) == NEGADO
    assert google.criados == [] and "conteúdo externo" in perguntas[0]


# ------------------------------------------------------------------ Agenda
def test_criar_evento_e_registrado_e_usa_o_fuso_local(google, tmp_path):
    ex, perguntas = executor(tmp_path, [])
    r = ex.executar("criar_evento", {"titulo": "Dentista", "inicio": "2026-10-20T15:00", "duracao_min": 30})
    assert "Evento criado" in r and perguntas == []
    ev = google.criados[0]
    assert ev["summary"] == "Dentista" and "attendees" not in ev
    assert ev["start"]["dateTime"].startswith("2026-10-20T15:00:00") and ev["end"]["dateTime"].startswith("2026-10-20T15:30:00")
    assert ev["start"]["dateTime"][19] in "+-"   # com fuso


def test_criar_evento_com_data_errada(google, tmp_path):
    ex, _ = executor(tmp_path, [])
    assert "formato" in ex.executar("criar_evento", {"titulo": "X", "inicio": "amanhã às 3"})


def test_ver_agenda(google):
    texto = REGISTRO["ver_agenda"].funcao(dias=3)
    ini = datetime.fromisoformat("2026-10-20T15:00:00-03:00").astimezone()   # mostrado no fuso do computador
    fim = datetime.fromisoformat("2026-10-20T16:00:00-03:00").astimezone()
    assert "Dentista" in texto and f"{ini:%d/%m %H:%M}–{fim:%H:%M}" in texto


# ------------------------------------------------------------------ WhatsApp
@pytest.mark.parametrize("entrada,esperado", [
    ("(11) 98765-4321", "5511987654321"), ("11 3333-4444", "551133334444"), ("+55 11 98765-4321", "5511987654321"),
    ("+1 415 555 0100", "14155550100"), ("0055 11 98765 4321", "5511987654321"), ("98765-4321", None), ("Maria", None),
])
def test_normalizar_numero(entrada, esperado):
    assert whatsapp.normalizar_numero(entrada) == esperado


def test_whatsapp_sempre_confirma_com_nome_numero_e_mensagem(google, tmp_path, monkeypatch):
    abertos = []
    monkeypatch.setattr(whatsapp.os, "startfile", abertos.append, raising=False)
    ex, perguntas = executor(tmp_path, [False])
    assert ex.executar("whatsapp_enviar", {"contato": "maria", "mensagem": "Chego às 8"}) == NEGADO
    assert abertos == [] and "Maria Souza (+5511987654321)" in perguntas[0] and "Chego às 8" in perguntas[0]


def test_whatsapp_contato_sem_telefone(google, tmp_path):
    ex, _ = executor(tmp_path, [])
    assert "telefone" in ex.executar("whatsapp_enviar", {"contato": "João Lima", "mensagem": "oi"})


def test_whatsapp_numero_incompleto(google, tmp_path):
    ex, _ = executor(tmp_path, [])
    assert "telefone completo" in ex.executar("whatsapp_enviar", {"contato": "98765-4321", "mensagem": "oi"})


def test_whatsapp_so_aperta_enter_com_o_whatsapp_na_frente(google, monkeypatch):
    abertos, teclas = [], []

    class Auto:
        def SendKeys(self, t, **_): teclas.append(t)
    monkeypatch.setattr(whatsapp.os, "startfile", abertos.append, raising=False)
    monkeypatch.setattr(whatsapp, "ESPERA_CONVERSA_S", 0)
    monkeypatch.setattr(whatsapp, "_janela_do_whatsapp", lambda: (Auto(), None))
    with pytest.raises(ErroFerramenta, match="NÃO foi enviada"):
        whatsapp.whatsapp_enviar("+55 11 98765-4321", "Olá & até já")
    assert teclas == [] and abertos[0] == "whatsapp://send?phone=5511987654321&text=Ol%C3%A1%20%26%20at%C3%A9%20j%C3%A1"

    monkeypatch.setattr(whatsapp, "_janela_do_whatsapp", lambda: (Auto(), object()))
    assert "enviada" in whatsapp.whatsapp_enviar("+55 11 98765-4321", "oi") and teclas == ["{Enter}"]


# ------------------------------------------------------------------ login
def test_sem_configurar_avisa_como_fazer(tmp_path, monkeypatch):
    monkeypatch.setattr(google_api, "CLIENTE", tmp_path / "cliente.json")
    monkeypatch.setattr(google_api, "TOKEN", tmp_path / "token.json")
    with pytest.raises(ErroFerramenta, match="python -m jarvis.tools.google_api"):
        google_api._token()
