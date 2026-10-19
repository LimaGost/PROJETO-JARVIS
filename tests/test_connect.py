"""JARVIS Connect: banco, cofre, rede, motor de automações (gatilhos, condições, ações, aprovação de envio), conectores,
webhooks, Central e as ferramentas do cérebro."""
import json
import urllib.error
from datetime import datetime, timedelta
from io import BytesIO

import pytest

from jarvis.connect import central as central_mod
from jarvis.connect import evolution, ferramentas, modelos, rede, webhooks
from jarvis.connect import motor as m
from jarvis.connect.banco import Banco
from jarvis.connect.cofre import Cofre, Protecao, usar
from jarvis.connect.conectores import catalogo, instancia
from jarvis.connect.conectores.base import CATEGORIAS
from jarvis.tools.base import REGISTRO, ErroFerramenta, Nivel
from jarvis.tools.executor import NEGADO, Executor

AGORA = datetime(2026, 10, 19, 10, 0)


class Relogio:
    def __init__(self, t=AGORA):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def cofre(tmp_path):
    c = Cofre(tmp_path / "cofre.json")
    usar(c)
    yield c
    usar(None)


@pytest.fixture
def mundo(cofre):
    """Motor com serviços falsos que anotam tudo."""
    banco = Banco(":memory:")
    feito = {"whatsapp": [], "email": [], "falas": [], "eventos": [], "ia": []}

    def pensar(pedido, contexto=""):
        feito["ia"].append((pedido, contexto))
        if "SIM ou NÃO" in pedido:
            return "SIM" if "amanhã" in contexto else "NÃO"
        if "JSON" in pedido:
            return '```json\n{"titulo": "Reunião com Ana", "inicio": "2026-10-20T15:00"}\n```'
        return "Resumo: 2 e-mails importantes."

    servicos = {
        "enviar_whatsapp": lambda n, t: feito["whatsapp"].append((n, t)) or f"enviado para {n}",
        "enviar_email": lambda p, a, t: feito["email"].append((p, a, t)) or "e-mail enviado",
        "ler_emails": lambda b, q: "<<<e-mail 1: boleto vence hoje>>>",
        "ver_agenda": lambda d: "seg 10h reunião",
        "criar_evento": lambda ti, ini, d: feito["eventos"].append((ti, ini, d)) or f"evento {ti}",
        "anunciar": lambda t, f="": feito["falas"].append(t),
        "pensar": pensar,
    }
    relogio = Relogio()
    mo = m.Motor(banco, servicos, {"connect": {"meu_whatsapp": "11999998888", "meu_email": "eu@exemplo.com"}},
                 relogio=relogio)
    mo.enfileirar = lambda auto, ctx, chave: mo.executar(auto, ctx, chave)     # síncrono nos testes
    mo.relogio_falso = relogio
    m._motor = mo
    yield mo, feito
    m._motor = None


def fluxo_simples(**g):
    return {"gatilho": {"tipo": "horario", "repetir": "diario", "hora": "08:00", **g},
            "acoes": [{"tipo": "avisar", "params": {"texto": "bom dia {data}"}}]}


# ------------------------------------------------------------------ banco e cofre
def test_banco_tarefas_ordem_e_lembretes(tmp_path):
    b = Banco(tmp_path / "c.db")
    b.criar_tarefa("baixa", prioridade="baixa")
    b.criar_tarefa("urgente", prioridade="urgente", quando="2026-10-19T09:00")
    b.criar_tarefa("prioridade estranha", prioridade="xyz")
    assert [t["titulo"] for t in b.tarefas()] == ["urgente", "prioridade estranha", "baixa"]
    assert b.tarefas()[1]["prioridade"] == "normal"
    t = b.mudar_tarefa(1, status="concluida")
    assert t["concluida"] and b.resumo()["tarefas_concluidas"] == 1


def test_execucao_com_a_mesma_chave_nao_duplica():
    b = Banco(":memory:")
    assert b.abrir_execucao(1, "x") is not None
    assert b.abrir_execucao(1, "x") is None


def test_cofre_guarda_mescla_mascara_e_nao_e_texto_puro(tmp_path):
    class Inverte(Protecao):
        nome = "teste"

        def fechar(self, d):
            return d[::-1]

        def abrir(self, d):
            return d[::-1]
    c = Cofre(tmp_path / "c.json", Inverte())
    c.guardar("evolution", {"apikey": "SEGREDO-123456"})
    c.guardar("evolution", {"url": "http://127.0.0.1:8080"})
    assert c.ler("evolution") == {"apikey": "SEGREDO-123456", "url": "http://127.0.0.1:8080"}
    assert "SEGREDO" not in (tmp_path / "c.json").read_text()
    assert Cofre.mascarar("SEGREDO-123456") == "••••••3456"
    s1 = c.segredo("evolution")
    assert s1 == c.segredo("evolution") and len(s1) > 30
    assert c.apagar("evolution") and c.ler("evolution") == {}


def test_cofre_ignora_item_que_nao_abre(tmp_path):
    class Quebra(Protecao):
        def abrir(self, d):
            raise OSError("outro usuário")
    Cofre(tmp_path / "c.json").guardar("a", {"x": "1"})
    assert Cofre(tmp_path / "c.json", Quebra()).ler("a") == {}


# ------------------------------------------------------------------ rede
class Resposta:
    def __init__(self, corpo: bytes):
        self.corpo = corpo

    def read(self):
        return self.corpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _erro(codigo, corpo=b"{}", cab=None):
    return urllib.error.HTTPError("u", codigo, "x", cab or {}, BytesIO(corpo))


def test_rede_repete_429_respeitando_retry_after_e_conta_consumo():
    banco = Banco(":memory:")
    rede.ligar_banco(banco)
    chamadas, esperas = [], []

    def abrir(req, timeout):
        chamadas.append(req.full_url)
        if len(chamadas) == 1:
            raise _erro(429, cab={"Retry-After": "3"})
        return Resposta(b'{"ok": true}')
    assert rede.requisitar("teste", "GET", "https://x/y", abrir=abrir, dormir=esperas.append) == {"ok": True}
    assert esperas == [3.0] and len(chamadas) == 2
    assert banco.consumo()[0] == {"conector": "teste", "chamadas": 2, "erros": 1}
    rede.ligar_banco(None)


def test_rede_nao_repete_erro_do_pedido_e_explica():
    chamadas = []

    def abrir(req, timeout):
        chamadas.append(1)
        raise _erro(401, b'{"message": "chave errada"}')
    with pytest.raises(ErroFerramenta, match="401: credencial inválida.*chave errada"):
        rede.requisitar("evolution", "GET", "https://x", abrir=abrir, dormir=lambda s: None)
    assert len(chamadas) == 1


def test_balde_limita_o_ritmo():
    t = [0.0]
    esperas = []
    b = rede.Balde(por_s=1, rajada=2, relogio=lambda: t[0], dormir=esperas.append)
    b.pegar()
    b.pegar()
    b.pegar()
    assert esperas == [1.0]


# ------------------------------------------------------------------ horários
@pytest.mark.parametrize("g,depois,esperado", [
    ({"repetir": "diario", "hora": "08:00"}, AGORA, datetime(2026, 10, 20, 8, 0)),
    ({"repetir": "diario", "hora": "11:30"}, AGORA, datetime(2026, 10, 19, 11, 30)),
    ({"repetir": "semanal", "dias": [0], "hora": "07:30"}, AGORA, datetime(2026, 10, 26, 7, 30)),   # 19/10 é segunda
    ({"repetir": "semanal", "dias": [2, 4], "hora": "9h"}, AGORA, datetime(2026, 10, 21, 9, 0)),
    ({"repetir": "mensal", "dia": 5, "hora": "08:00"}, AGORA, datetime(2026, 11, 5, 8, 0)),
    ({"repetir": "intervalo", "a_cada_min": 30}, AGORA, AGORA + timedelta(minutes=30)),
    ({"repetir": "nunca", "quando": "2026-10-20T09:00"}, AGORA, datetime(2026, 10, 20, 9, 0)),
    ({"repetir": "nunca", "quando": "2026-10-18T09:00"}, AGORA, None),
])
def test_proxima_execucao(g, depois, esperado):
    assert m.proxima({"tipo": "horario", **g}, depois) == esperado


def test_descricao_do_gatilho():
    assert m.descrever_gatilho({"tipo": "horario", "repetir": "semanal", "dias": [0, 4], "hora": "8:00"}) == \
        "toda segunda, sexta às 08:00"
    assert "WhatsApp" in m.descrever_gatilho({"tipo": "evento", "evento": "whatsapp.mensagem"})


# ------------------------------------------------------------------ condições e variáveis
def test_render_de_variaveis():
    ctx = {"mensagem": {"nome": "Ana"}, "resultado": {"x": {"a": 1}}}
    assert m.render("Oi {mensagem.nome}, {resultado.x} {nada}", ctx) == 'Oi Ana, {"a": 1} {nada}'


@pytest.mark.parametrize("c,ok", [
    ({"campo": "mensagem.texto", "op": "contem", "valor": "orçamento"}, True),
    ({"campo": "mensagem.texto", "op": "contem", "valor": "boleto, ORCAMENTO"}, True),     # sem acento, maiúscula
    ({"campo": "mensagem.texto", "op": "nao_contem", "valor": "orçamento"}, False),
    ({"campo": "mensagem.texto", "op": "comeca", "valor": "oi"}, True),
    ({"campo": "mensagem.texto", "op": "regex", "valor": r"\d{2}h"}, True),
    ({"op": "de", "valor": "ana"}, True),
    ({"op": "de", "valor": "joão"}, False),
])
def test_condicoes(c, ok):
    ctx = {"mensagem": {"texto": "Oi, quero um orçamento amanhã 15h", "nome": "Ana Lima", "de": "5511988887777"}}
    assert m.condicao_ok(c, ctx) is ok


# ------------------------------------------------------------------ política de envio
@pytest.mark.parametrize("acao,erro", [
    ({"tipo": "whatsapp", "params": {"para": "5511988887777", "texto": "{resultado.resumo}"}}, "texto precisa ser fixo"),
    ({"tipo": "whatsapp", "params": {"para": "5511988887777", "texto": "Oi {mensagem.texto}"}}, "texto precisa ser fixo"),
    ({"tipo": "whatsapp", "params": {"para": "{resultado.numero}", "texto": "oi"}}, "destinatário precisa ser fixo"),
    ({"tipo": "email", "params": {"para": "{mensagem.de}", "texto": "oi"}}, "só vale para mensagem de WhatsApp"),
    ({"tipo": "email", "params": {"para": "a@b.com", "assunto": "{resultado.x}", "texto": "oi"}}, "texto precisa ser fixo"),
])
def test_politica_recusa_envio_perigoso_a_terceiros(acao, erro):
    with pytest.raises(ValueError, match=erro):
        m.validar_fluxo({"gatilho": {"tipo": "manual"}, "acoes": [acao]})


def test_politica_aceita_ia_para_mim_e_texto_fixo_para_terceiros():
    envios = m.analisar_envios(m.validar_fluxo({"gatilho": {"tipo": "manual"}, "acoes": [
        {"tipo": "whatsapp", "params": {"para": "eu", "texto": "{resultado.resumo}"}},
        {"tipo": "whatsapp", "params": {"para": "5511988887777", "texto": "Lembrete da reunião {amanha} às 9h"}}]}))
    assert [e["terceiro"] for e in envios] == [False, True]


def test_fluxo_invalido_explica():
    with pytest.raises(ValueError, match="pelo menos uma ação"):
        m.validar_fluxo({"gatilho": {"tipo": "manual"}, "acoes": []})
    with pytest.raises(ValueError, match="evento desconhecido"):
        m.validar_fluxo({"gatilho": {"tipo": "evento", "evento": "x"}, "acoes": [{"tipo": "avisar"}]})
    with pytest.raises(ValueError, match="hora inválida"):
        m.validar_fluxo(fluxo_simples(hora="25:00"))


# ------------------------------------------------------------------ motor
def test_agendador_dispara_no_horario_e_reagenda(mundo):
    mo, feito = mundo
    auto = mo.criar("Bom dia", fluxo_simples())
    assert auto["proxima"] == "2026-10-20T08:00:00"
    mo.tick()
    assert feito["falas"] == []
    mo.relogio_falso.t = datetime(2026, 10, 20, 8, 0, 10)
    mo.tick()
    mo.tick()                                     # a mesma vez não roda duas
    assert feito["falas"] == ["[automação Bom dia] bom dia 20/10/2026"]
    assert mo.banco.automacao(auto["id"])["proxima"] == "2026-10-21T08:00:00"


def test_uma_vez_desliga_depois_e_horario_muito_atrasado_nao_roda(mundo):
    mo, feito = mundo
    a = mo.criar("Uma vez", {"gatilho": {"tipo": "horario", "repetir": "nunca", "quando": "2026-10-19T11:00"},
                             "acoes": [{"tipo": "avisar", "params": {"texto": "agora"}}]})
    mo.relogio_falso.t = datetime(2026, 10, 19, 11, 0, 5)
    mo.tick()
    assert feito["falas"] and not mo.banco.automacao(a["id"])["ativa"]
    b = mo.criar("Atrasada", fluxo_simples(hora="10:30"))
    mo.relogio_falso.t = datetime(2026, 10, 20, 9, 0)        # PC ficou desligado: passou 22h30 do horário
    feito["falas"].clear()
    mo.tick()
    assert feito["falas"] == [] and mo.banco.automacao(b["id"])["proxima"] == "2026-10-20T10:30:00"


def test_resumo_diario_com_ia_salva_e_encadeia(mundo):
    mo, feito = mundo
    a = mo.criar("Resumo", modelos.modelo("resumo_emails")["fluxo"])
    mo.rodar_agora(a["id"])
    ex = mo.banco.execucoes(a["id"])[0]
    assert ex["status"] == "ok"
    assert feito["ia"][0][1] == "<<<e-mail 1: boleto vence hoje>>>"           # o que ler_emails salvou
    assert feito["falas"][-1].endswith("Resumo dos e-mails: Resumo: 2 e-mails importantes.")


def test_mensagem_com_orcamento_vira_tarefa(mundo):
    mo, feito = mundo
    mo.criar("Orçamento", modelos.modelo("orcamento_tarefa")["fluxo"])
    msg = {"id": "M1", "de": "5511988887777", "nome": "Ana", "texto": "Pode mandar um orcamento?", "grupo": False}
    assert mo.publicar("whatsapp.mensagem", msg, "M1") == 1
    assert mo.publicar("whatsapp.mensagem", msg, "M1") == 1           # webhook repetido: tenta, mas não duplica
    assert [t["titulo"] for t in mo.banco.tarefas()] == ["Orçamento: Ana"]
    assert mo.banco.tarefas()[0]["prioridade"] == "alta"
    assert mo.publicar("whatsapp.mensagem", {**msg, "id": "M2", "texto": "bom dia"}, "M2") == 0


def test_envio_sem_aprovacao_e_bloqueado_e_aprovado_vai(mundo):
    mo, feito = mundo
    a = mo.criar("Plano", modelos.modelo("planejamento_semanal")["fluxo"])
    assert mo.precisa_aprovar(a)
    mo.rodar_agora(a["id"])
    ex = mo.banco.execucoes(a["id"])[0]
    assert ex["status"] == "erro" and "não foi aprovada" in ex["resultado"] and feito["whatsapp"] == []
    mo.aprovar(a["id"], m.hash_fluxo(a["fluxo"]))
    mo.rodar_agora(a["id"])
    assert feito["whatsapp"] == [("11999998888", "Planejamento da semana:\nResumo: 2 e-mails importantes.")]


def test_mudar_o_fluxo_perde_a_aprovacao(mundo):
    mo, _ = mundo
    f = modelos.modelo("planejamento_semanal")["fluxo"]
    a = mo.criar("Plano", f, aprovado=True)
    assert not mo.precisa_aprovar(a)
    f2 = json.loads(json.dumps(f))
    f2["acoes"][2]["params"]["texto"] = "outro {resultado.plano}"
    assert mo.precisa_aprovar(mo.atualizar(a["id"], "Plano", f2))
    with pytest.raises(ValueError, match="mudou"):
        mo.aprovar(a["id"], m.hash_fluxo(a["fluxo"]))                   # o hash antigo não vale para o fluxo novo


def test_aprovacao_editada_no_banco_nao_burla_texto_variavel_para_terceiro(mundo):
    mo, feito = mundo
    a = mo.criar("X", {"gatilho": {"tipo": "manual"},
                       "acoes": [{"tipo": "whatsapp", "params": {"para": "5511988887777", "texto": "oi"}}]}, aprovado=True)
    ruim = {**a["fluxo"], "acoes": [{**a["fluxo"]["acoes"][0], "params": {"para": "5511988887777",
                                                                          "texto": "{resultado.segredo}"}}]}
    mo.banco.mudar_automacao(a["id"], fluxo=ruim, aprovacao={"hash": m.hash_fluxo(ruim)})   # alguém mexeu no .db
    mo.rodar_agora(a["id"])
    assert feito["whatsapp"] == [] and "texto variável" in mo.banco.execucoes(a["id"])[0]["resultado"]


def test_compromisso_no_whatsapp_vira_evento_e_responde_texto_fixo(mundo):
    mo, feito = mundo
    a = mo.criar("Agenda", modelos.modelo("compromisso_agenda")["fluxo"], aprovado=True)
    msg = {"id": "Z", "de": "5511977776666", "nome": "Ana", "texto": "Podemos nos ver amanhã às 15h?", "grupo": False}
    mo.publicar("whatsapp.mensagem", msg, "Z")
    assert feito["eventos"] == [("Reunião com Ana", "2026-10-20T15:00", 60)]
    assert feito["whatsapp"] == [("5511977776666", "Anotado na agenda! Até lá. ✅")]
    mo.publicar("whatsapp.mensagem", {**msg, "id": "Y", "texto": "obrigado!"}, "Y")         # a IA diz NÃO
    assert len(feito["eventos"]) == 1
    assert mo.banco.execucoes(a["id"])[0]["status"] == "ignorada"


def test_teste_nao_envia_nem_cria_mas_le_de_verdade(mundo):
    mo, feito = mundo
    a = mo.criar("Agenda", modelos.modelo("compromisso_agenda")["fluxo"], aprovado=True)
    r = mo.testar(a["id"])
    assert r["status"] == "ok" and r["teste"]
    assert feito["eventos"] == [] and feito["whatsapp"] == [] and feito["falas"] == []
    saidas = " ".join(p.get("saida", "") for p in r["passos"])
    assert "(teste) criaria o evento 'Reunião com Ana'" in saidas and "enviaria WhatsApp para 5511999990000" in saidas
    assert mo.banco.automacao(a["id"])["execucoes"] == 0          # teste não conta como execução


def test_acoes_paralelas_repeticao_e_continuar_em_falha(mundo):
    mo, feito = mundo
    tentativas = []

    def instavel(d):
        tentativas.append(d)
        if len(tentativas) < 2:
            raise RuntimeError("Google fora do ar")
        return "agenda ok"
    mo.s["ver_agenda"] = instavel
    import jarvis.connect.motor as mm
    dormiu = []
    original = mm.time.sleep
    mm.time.sleep = dormiu.append
    try:
        a = mo.criar("P", {"gatilho": {"tipo": "manual"}, "acoes": [
            {"tipo": "ver_agenda", "params": {"dias": 1}, "tentativas": 2, "salvar_como": "ag"},
            {"tipo": "criar_evento", "params": {"titulo": "x", "inicio": "{nao_existe}"}, "se_falhar": "continuar"},
            {"tipo": "avisar", "params": {"texto": "A: {resultado.ag}"}, "paralelo": True},
            {"tipo": "criar_tarefa", "params": {"titulo": "depois"}}]})
        mo.s["criar_evento"] = lambda *a: (_ for _ in ()).throw(RuntimeError("data inválida"))
        mo.rodar_agora(a["id"])
    finally:
        mm.time.sleep = original
    ex = mo.banco.execucoes(a["id"])[0]
    assert ex["status"] == "ok" and len(tentativas) == 2 and dormiu
    assert [p["status"] for p in ex["passos"][1:]] == ["ok", "erro", "ok", "ok"]
    assert feito["falas"][-1].endswith("A: agenda ok")


def test_falha_avisa_voce(mundo):
    mo, feito = mundo
    mo.s.pop("ler_emails")
    a = mo.criar("Quebra", {"gatilho": {"tipo": "manual"}, "acoes": [{"tipo": "ler_emails", "params": {}}]})
    mo.rodar_agora(a["id"])
    assert "falhou" in feito["falas"][-1] and mo.banco.automacao(a["id"])["falhas"] == 1


def test_lembrete_de_tarefa_na_hora(mundo):
    mo, feito = mundo
    mo.banco.criar_tarefa("Ligar para o cliente", quando="2026-10-19T10:30")
    mo.tick()
    assert feito["falas"] == []
    mo.relogio_falso.t = datetime(2026, 10, 19, 10, 31)
    mo.tick()
    mo.tick()
    assert feito["falas"] == ["Lembrete: Ligar para o cliente"]


def test_reuniao_chegando_respeita_minutos_antes_e_vigia_gmail(mundo):
    mo, feito = mundo
    mo.criar("Reunião", modelos.modelo("reuniao_whatsapp")["fluxo"], aprovado=True)
    ev = {"id": "e1", "titulo": "Implantação", "inicio": "x", "hora": "10:20", "minutos": 30}
    assert mo.publicar("agenda.proxima", ev, "e1") == 0
    assert mo.publicar("agenda.proxima", {**ev, "minutos": 12}, "e1") == 1
    assert feito["whatsapp"] == [("11999998888", "Daqui a 12 min: Implantação (10:20)")]
    mo.criar("Email", {"gatilho": {"tipo": "evento", "evento": "gmail.novo"},
                       "condicoes": [{"campo": "email.assunto", "op": "contem", "valor": "urgente"}],
                       "acoes": [{"tipo": "avisar", "params": {"texto": "E-mail urgente de {email.de}"}}]})
    caixa = [{"id": "a", "de": "x", "assunto": "URGENTE antigo"}]
    mo.s["emails_novos"] = lambda: caixa
    mo.vigiar()                                       # 1ª vez: só marca o que já existia
    caixa.append({"id": "b", "de": "cliente@x.com", "assunto": "Urgente: proposta"})
    mo.vigiar()
    assert feito["falas"][-1].endswith("E-mail urgente de cliente@x.com")


def test_todos_os_modelos_sao_validos_menos_o_lembrete_sem_data():
    for mod in modelos.MODELOS:
        if mod["id"] == "lembrete_unico":
            with pytest.raises(ValueError):
                m.validar_fluxo(mod["fluxo"])
        else:
            m.validar_fluxo(mod["fluxo"])


# ------------------------------------------------------------------ conectores
def test_catalogo_tem_todas_as_categorias_e_situacoes_honestas(cofre):
    cartoes = catalogo(Banco(":memory:"), cofre)
    assert {c["categoria"] for c in cartoes} == set(CATEGORIAS)
    por_id = {c["id"]: c for c in cartoes}
    for implementado in ("gmail", "agenda", "evolution", "claude", "agendador", "webhooks"):
        assert por_id[implementado]["situacao"] == "implementado"
    for planejado in ("outlook", "teams", "onedrive", "ms_todo", "whatsapp_meta", "home_assistant", "drive"):
        assert por_id[planejado]["situacao"] == "planejado" and por_id[planejado]["docs"].startswith("https://")
    for c in cartoes:
        assert c["estado"] in ("conectado", "desconectado", "erro", "aguardando")
        assert c["custo"] and c["requisitos"], c["id"]


def test_evolution_configurar_status_e_segredo_nunca_volta(cofre, monkeypatch):
    evo = instancia("evolution", cofre)
    assert evo.status().estado == "desconectado"
    evo.configurar({"apikey": "CHAVE-SUPER-SECRETA", "url": "http://127.0.0.1:8080", "lixo": "x"})
    cartao = json.dumps(evo.cartao(), ensure_ascii=False)
    assert "CHAVE-SUPER-SECRETA" not in cartao and "••••••RETA" in cartao and "lixo" not in cofre.ler("evolution")
    monkeypatch.setattr(evolution.Evolution, "estado", lambda self: "open")
    assert evo.status().estado == "conectado"
    monkeypatch.setattr(evolution.Evolution, "estado", lambda self: "connecting")
    assert evo.status().estado == "aguardando"


def test_evolution_conectar_cria_instancia_liga_webhook_e_traz_qr(cofre, monkeypatch):
    evo = instancia("evolution", cofre)
    evo.configurar({"apikey": "k"})
    pedidos = []

    def req(conector, metodo, url, corpo=None, cab=None, **k):
        pedidos.append((metodo, url.replace("http://127.0.0.1:8080", ""), corpo, cab))
        if "connectionState" in url:
            raise ErroFerramenta("evolution recusou o pedido (erro 404: não encontrado)")
        if url.endswith("/instance/connect/jarvis"):
            return {"base64": "data:image/png;base64,AAAA", "pairingCode": "ABCD-1234"}
        return {}
    monkeypatch.setattr(rede, "requisitar", req)
    monkeypatch.setattr(evolution.Evolution, "__init__", lambda self, url="", apikey="", instancia="jarvis", requisitar=None:
                        (setattr(self, "url", url), setattr(self, "apikey", apikey), setattr(self, "instancia", instancia),
                         setattr(self, "_req", req))[0])
    r = evo.conectar()
    assert r["qrcode"].startswith("data:image/png") and r["codigo"] == "ABCD-1234"
    caminhos = [p[1] for p in pedidos]
    assert "/instance/create" in caminhos and "/webhook/set/jarvis" in caminhos
    webhook = next(p for p in pedidos if p[1] == "/webhook/set/jarvis")[2]["webhook"]
    assert webhook["headers"]["x-jarvis-segredo"] == cofre.segredo("evolution")
    assert webhook["events"] == ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]
    assert all(p[3] == {"apikey": "k"} for p in pedidos)


def test_mensagem_do_webhook_da_evolution():
    corpo = {"event": "messages.upsert", "data": {"key": {"remoteJid": "5511988887777@s.whatsapp.net", "fromMe": False,
                                                          "id": "ABC"}, "pushName": "Ana",
                                                  "message": {"extendedTextMessage": {"text": "oi, orçamento?"}}}}
    assert evolution.mensagem_do_webhook(corpo) == {"id": "ABC", "de": "5511988887777", "jid": "5511988887777@s.whatsapp.net",
                                                    "grupo": False, "nome": "Ana", "texto": "oi, orçamento?", "quando": None}
    corpo["data"]["key"]["fromMe"] = True
    assert evolution.mensagem_do_webhook(corpo) is None
    assert evolution.mensagem_do_webhook({"event": "connection.update"}) is None


def test_google_por_servico(cofre, tmp_path, monkeypatch):
    from jarvis.tools import google_api
    monkeypatch.setattr(google_api, "CLIENTE", tmp_path / "cliente.json")
    monkeypatch.setattr(google_api, "TOKEN", tmp_path / "token.json")
    gmail, agenda = instancia("gmail", cofre), instancia("agenda", cofre)
    assert gmail.status().estado == "desconectado"
    (tmp_path / "cliente.json").write_text("{}")
    assert gmail.status().estado == "aguardando"
    (tmp_path / "token.json").write_text(json.dumps({"scope": " ".join(google_api.ESCOPOS_POR_SERVICO["agenda"])}))
    assert agenda.status().estado == "conectado" and gmail.status().estado == "aguardando"


def test_google_revogar_apaga_o_token(tmp_path, monkeypatch):
    from jarvis.tools import google_api
    monkeypatch.setattr(google_api, "TOKEN", tmp_path / "token.json")
    (tmp_path / "token.json").write_text(json.dumps({"refresh_token": "r"}))
    revogados = []
    monkeypatch.setattr(google_api.urllib.request, "urlopen", lambda req, timeout: revogados.append(req.data))
    assert google_api.revogar() and not (tmp_path / "token.json").exists() and b"token=r" in revogados[0]


# ------------------------------------------------------------------ webhooks
def test_webhook_da_evolution_exige_segredo_e_dispara_motor(mundo, cofre):
    from fastapi.testclient import TestClient
    mo, _ = mundo
    mo.criar("Orçamento", modelos.modelo("orcamento_tarefa")["fluxo"])
    cliente = TestClient(webhooks.criar_app(mo, cofre, mo.banco))
    corpo = {"event": "messages.upsert", "data": {"key": {"remoteJid": "5511988887777@s.whatsapp.net", "fromMe": False,
                                                          "id": "W1"}, "pushName": "Ana",
                                                  "message": {"conversation": "quero orçamento"}}}
    assert cliente.post("/webhook/evolution", json=corpo).status_code == 401
    assert cliente.post("/webhook/evolution", json=corpo, headers={"x-jarvis-segredo": "errado"}).status_code == 401
    ok = {"x-jarvis-segredo": cofre.segredo("evolution")}
    assert cliente.post("/webhook/evolution", json=corpo, headers=ok).status_code == 200
    assert cliente.post("/webhook/evolution", json=corpo, headers=ok).status_code == 200     # repetido
    assert len(mo.banco.tarefas()) == 1
    grande = {"event": "messages.upsert", "x": "a" * (webhooks.MAX_CORPO + 10)}
    assert cliente.post("/webhook/evolution", json=grande, headers=ok).status_code == 413
    assert any("recusado" in e["resumo"] for e in mo.banco.eventos())


def test_webhook_geral_com_segredo_no_caminho(mundo, cofre):
    from fastapi.testclient import TestClient
    mo, feito = mundo
    mo.criar("W", {"gatilho": {"tipo": "evento", "evento": "webhook"},
                   "acoes": [{"tipo": "avisar", "params": {"texto": "chegou {webhook.tipo}"}}]})
    cliente = TestClient(webhooks.criar_app(mo, cofre, mo.banco))
    assert cliente.post("/webhook/geral/errado", json={"tipo": "x"}).status_code == 401
    assert cliente.post(f"/webhook/geral/{cofre.segredo('webhooks')}", json={"tipo": "deploy"}).status_code == 200
    assert feito["falas"][-1].endswith("chegou deploy")


def test_assinatura_hmac():
    import hashlib
    import hmac
    corpo = b'{"a":1}'
    assinatura = "sha256=" + hmac.new(b"s", corpo, hashlib.sha256).hexdigest()
    assert webhooks.assinatura_ok("s", corpo, assinatura)
    assert not webhooks.assinatura_ok("s", corpo + b" ", assinatura) and not webhooks.assinatura_ok("s", corpo, "")


# ------------------------------------------------------------------ Central (HUD)
def test_central_valida_e_nao_vaza_segredo(mundo, cofre):
    mo, _ = mundo
    c = central_mod.Central(mo, cofre)
    assert c.tratar({"op": "__class__"})["ok"] is False
    assert c.tratar({"op": "naoexiste"})["erro"] == "operação desconhecida"
    r = c.tratar({"op": "configurar", "id": "evolution", "valores": {"apikey": "SEGREDO-ABCDEFGH"}, "pedido": 7})
    assert r["ok"] and r["pedido"] == 7 and "SEGREDO" not in json.dumps(r, ensure_ascii=False)
    c.tratar({"op": "configurar", "id": "evolution", "valores": {"apikey": "••••••EFGH", "instancia": "j2"}})
    assert cofre.ler("evolution")["apikey"] == "SEGREDO-ABCDEFGH"          # o mascarado não sobrescreve
    assert "SEGREDO" not in json.dumps(c.tratar({"op": "integracoes"}))
    assert c.tratar({"op": "tarefa_status", "id": "1; DROP TABLE", "status": "concluida"})["ok"] is False


def test_central_tarefas_e_automacoes_com_aprovacao(mundo, cofre):
    mo, feito = mundo
    c = central_mod.Central(mo, cofre)
    t = c.tratar({"op": "tarefa_salvar", "tarefa": {"titulo": "Revisar contrato", "quando": "2026-10-20T09:00",
                                                    "prioridade": "alta"}})["dados"]
    assert t["quando"] == "2026-10-20T09:00"
    assert c.tratar({"op": "tarefa_status", "id": t["id"], "status": "concluida"})["dados"]["status"] == "concluida"
    r = c.tratar({"op": "automacao_salvar", "nome": "Plano", "fluxo": modelos.modelo("planejamento_semanal")["fluxo"]})
    a = r["dados"]
    assert a["precisa_aprovar"] and "manda WhatsApp para você mesmo" in a["resumo_aprovacao"]
    assert not c.tratar({"op": "automacao_aprovar", "id": a["id"], "hash": "x" * 64})["ok"]
    assert not c.tratar({"op": "automacao_aprovar", "id": a["id"], "hash": a["hash"]})["dados"]["precisa_aprovar"]
    teste = c.tratar({"op": "automacao_testar", "id": a["id"]})["dados"]
    assert teste["status"] == "ok" and feito["whatsapp"] == []
    ruim = c.tratar({"op": "automacao_salvar", "nome": "Ruim", "fluxo": {"gatilho": {"tipo": "manual"}, "acoes": [
        {"tipo": "whatsapp", "params": {"para": "5511900000000", "texto": "{resultado.x}"}}]}})
    assert not ruim["ok"] and "fixo" in ruim["erro"]
    painel = c.tratar({"op": "painel"})["dados"]
    assert painel["automacoes_ativas"] == 1 and painel["linha_do_tempo"]
    meta = c.tratar({"op": "automacoes"})["dados"]
    assert set(meta["tipos_acao"]) == set(m.TIPOS_ACAO) and len(meta["modelos"]) == len(modelos.MODELOS)


# ------------------------------------------------------------------ ferramentas do cérebro
def _executor(tmp_path, respostas):
    perguntas = []
    return Executor(lambda resumo, **k: perguntas.append(resumo) or respostas.pop(0), str(tmp_path / "logs"),
                    avisar=lambda t: None), perguntas


def test_criar_automacao_que_envia_pede_confirmacao_com_destino_e_texto(mundo, tmp_path, monkeypatch):
    mo, _ = mundo
    from jarvis.tools import whatsapp
    monkeypatch.setattr(whatsapp, "um_contato", lambda nome, campo: ("João Silva", "(11) 98888-7777"))
    ferramentas._preparados.clear()
    ex, perguntas = _executor(tmp_path, [False, True])
    args = {"nome": "Lembrar João", "gatilho": {"tipo": "horario", "repetir": "nunca", "quando": "2026-10-20T09:00"},
            "acoes": [{"tipo": "whatsapp", "params": {"para": "João", "texto": "Bom dia! Lembrete da reunião de hoje."}}]}
    assert ex.executar("criar_automacao", args) == NEGADO and mo.banco.automacoes() == []
    assert "manda WhatsApp para 5511988887777" in perguntas[0] and "Lembrete da reunião" in perguntas[0]
    ex.novo_turno()
    r = ex.executar("criar_automacao", args)
    assert "criada e ligada" in r and "2026-10-20 09:00" in r
    a = mo.banco.automacoes()[0]
    assert a["fluxo"]["acoes"][0]["params"]["para"] == "5511988887777" and not mo.precisa_aprovar(a)


def test_criar_automacao_sem_envio_e_amarela(mundo):
    ferramentas._preparados.clear()
    args = {"nome": "Bom dia", "gatilho": {"tipo": "horario", "repetir": "diario", "hora": "08:00"},
            "acoes": [{"tipo": "avisar", "params": {"texto": "bom dia"}}]}
    assert REGISTRO["criar_automacao"].avaliacao(args).nivel == Nivel.REGISTRADO
    with pytest.raises(ErroFerramenta, match="fixo"):
        REGISTRO["criar_automacao"].avaliacao({**args, "acoes": [{"tipo": "email", "params": {
            "para": "a@b.com", "texto": "{resultado.x}"}}]})


def test_ferramentas_de_tarefa(mundo):
    mo, _ = mundo
    assert "Tarefa 1 anotada" in REGISTRO["criar_tarefa"].funcao(titulo="Pagar boleto", quando="2026-10-20T10:00")
    assert "Pagar boleto" in REGISTRO["listar_tarefas"].funcao()
    assert "Concluída" in REGISTRO["concluir_tarefa"].funcao(id=1)
    with pytest.raises(ErroFerramenta, match="ISO"):
        REGISTRO["criar_tarefa"].funcao(titulo="x", quando="amanhã")
    assert not REGISTRO["criar_tarefa"].so_claude and REGISTRO["criar_automacao"].so_claude


def test_ferramentas_sem_connect_explicam():
    m._motor = None
    with pytest.raises(ErroFerramenta, match="Connect não está ligado"):
        REGISTRO["listar_tarefas"].funcao()


def test_teste_de_automacao_nao_aprovada_mostra_o_envio_e_avisa(mundo):
    mo, feito = mundo
    a = mo.criar("Plano", modelos.modelo("planejamento_semanal")["fluxo"])
    r = mo.testar(a["id"])
    assert r["status"] == "ok" and feito["whatsapp"] == []
    assert "enviaria WhatsApp para eu" in r["passos"][-1]["saida"] and "não foi aprovada" in r["passos"][-1]["saida"]
