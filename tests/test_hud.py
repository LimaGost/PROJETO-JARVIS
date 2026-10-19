import queue
import threading
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.audio.mic import BLOCO, Microfone
from jarvis.brain.llm import Cerebro
from jarvis.hud import controle as modulo_controle
from jarvis.hud.bus import bus
from jarvis.hud.controle import Controle
from jarvis.hud.servidor import ServidorHUD

CFG = {"nome": "Jarvis", "tratamento": "senhor", "llm": {"modelo": "qwen3:8b"}, "vad": {"espera_inicio_s": 1}}


@pytest.fixture(autouse=True)
def bus_limpo():
    bus.limpar()
    yield
    bus.limpar()


class CerebroFalso:
    def __init__(self, frases=("Pronto.",)):
        self.frases, self.pedidos, self.cancelado, self.esqueceu = list(frases), [], False, False

    def responder(self, texto):
        self.pedidos.append(texto)
        yield from self.frases

    def cancelar(self):
        self.cancelado = True

    def esquecer(self):
        self.esqueceu = True


class VozFalsa:
    def __init__(self):
        self.ditas = []

    def falar(self, t):
        self.ditas.append(t)

    def esperar(self):
        pass

    def calar(self):
        self.ditas.append("<calou>")

    def nivel(self):
        return 0.0


class MicFalso:
    """gravar_fala devolve o 'áudio' programado; o ouvido falso o transforma em texto."""
    def __init__(self, audios=()):
        self.audios, self.chamadas = list(audios), 0
        self.silenciado, self.encerrar, self.nivel = False, threading.Event(), 0.0

    def gravar_fala(self, espera, cancelar=None):
        self.chamadas += 1
        return self.audios.pop(0) if self.audios else None


class OuvidoFalso:
    def transcrever(self, audio):
        return audio


def montar(**kw):
    return Controle(CFG, kw.pop("cerebro", CerebroFalso()), **kw)


# ------------------------------------------------------------------ barramento
def test_bus_historico_limitado_e_estado():
    for i in range(300):
        bus.emitir("jarvis", texto=str(i))
    assert len(bus.historico()) == 120 and bus.historico()[-1]["texto"] == "299"
    bus.emitir("estado", estado="pensando")
    bus.emitir("nivel", mic=0.5)  # telemetria nunca entra no histórico
    assert bus.estado == "pensando" and all(e["tipo"] != "nivel" for e in bus.historico())


def test_bus_corta_texto_gigante_e_assinante_com_erro_nao_derruba():
    recebidos = []
    bus.assinar(lambda e: 1 / 0)
    bus.assinar(recebidos.append)
    bus.emitir("acao", resultado="x" * 100_000)
    assert len(recebidos) == 1 and len(recebidos[0]["resultado"]) < 5000


# ------------------------------------------------------------------ confirmação
def esperar_pendente(ctl, limite=3):
    t = time.time()
    while ctl._pendente is None and time.time() - t < limite:
        time.sleep(0.01)
    return ctl._pendente


def pedir(ctl, **kw):
    res = {}
    th = threading.Thread(target=lambda: res.setdefault("sim", ctl.confirmar("apagar o arquivo x", **kw)))
    th.start()
    return th, res


def test_confirmacao_por_clique_sim_e_nao():
    ctl = montar(voz=VozFalsa())
    bus.conexoes = 1
    for resposta in (True, False):
        th, res = pedir(ctl, nome="apagar", args={"caminho": "x"})
        p = esperar_pendente(ctl)
        assert ctl.responder_confirmacao(p["id"], resposta, "hud")
        th.join(3)
        assert res["sim"] is resposta


def test_confirmacao_id_errado_ou_repetido_e_ignorado():
    ctl = montar(voz=VozFalsa())
    bus.conexoes = 1
    th, res = pedir(ctl)
    p = esperar_pendente(ctl)
    assert not ctl.responder_confirmacao(p["id"] + 99, True)       # id que não é o pendente
    assert th.is_alive()
    assert ctl.responder_confirmacao(p["id"], False)               # a primeira resposta vence
    assert not ctl.responder_confirmacao(p["id"], True)            # a segunda é ignorada
    th.join(3)
    assert res["sim"] is False
    assert not ctl.responder_confirmacao(p["id"], True)            # nada pendente: ignorada


@pytest.mark.parametrize("fala,esperado", [("Sim, pode", True), ("Não", False), ("Hã?", False), ("Talvez", False)])
def test_confirmacao_por_voz(fala, esperado):
    ctl = montar(voz=VozFalsa(), mic=MicFalso([fala]), ouvido=OuvidoFalso())
    th, res = pedir(ctl)
    th.join(3)
    assert res["sim"] is esperado


def test_voz_vence_se_responder_primeiro_e_clique_atrasado_nao_muda_nada():
    ctl = montar(voz=VozFalsa(), mic=MicFalso(["não"]), ouvido=OuvidoFalso())
    th, res = pedir(ctl)
    th.join(3)
    assert res["sim"] is False
    assert not ctl.responder_confirmacao(1, True)


def test_silencio_sem_hud_nega_na_hora():
    ctl = montar(voz=VozFalsa(), mic=MicFalso([None]), ouvido=OuvidoFalso())
    bus.conexoes = 0
    t = time.time()
    th, res = pedir(ctl)
    th.join(3)
    assert res["sim"] is False and time.time() - t < 2


def test_silencio_com_hud_espera_o_clique(monkeypatch):
    ctl = montar(voz=VozFalsa(), mic=MicFalso([None]), ouvido=OuvidoFalso())
    bus.conexoes = 1
    th, res = pedir(ctl)
    p = esperar_pendente(ctl)
    time.sleep(0.3)
    assert th.is_alive()  # voz ficou em silêncio, mas a HUD ainda pode responder
    ctl.responder_confirmacao(p["id"], True, "hud")
    th.join(3)
    assert res["sim"] is True


def test_tempo_esgotado_nega(monkeypatch):
    monkeypatch.setattr(modulo_controle, "TEMPO_CONFIRMACAO_S", 0.3)
    ctl = montar(voz=VozFalsa())
    bus.conexoes = 1
    th, res = pedir(ctl)
    th.join(3)
    assert res["sim"] is False
    assert [e for e in bus.historico() if e["tipo"] == "sistema"] == []
    resolvida = [e for e in bus.emitir("x") and []]  # (sem uso: só garante que emitir funciona)


def test_pedido_vindo_da_hud_nao_abre_o_microfone():
    mic = MicFalso(["sim"])
    ctl = montar(voz=VozFalsa(), mic=mic, ouvido=OuvidoFalso())
    bus.conexoes = 1
    res = {}

    def tarefa():
        ctl._origem.valor = "hud"  # como em texto_do_hud
        res["sim"] = ctl.confirmar("apagar x")

    th = threading.Thread(target=tarefa)
    th.start()
    p = esperar_pendente(ctl)
    time.sleep(0.2)
    assert mic.chamadas == 0 and th.is_alive()  # o "sim" falado perto do microfone NÃO vale para texto digitado
    ctl.responder_confirmacao(p["id"], True)
    th.join(3)
    assert res["sim"] is True


def test_microfone_mudo_nao_ouve_confirmacao():
    mic = MicFalso(["sim"])
    mic.silenciado = True
    ctl = montar(voz=VozFalsa(), mic=mic, ouvido=OuvidoFalso())
    bus.conexoes = 1
    th, res = pedir(ctl)
    p = esperar_pendente(ctl)
    time.sleep(0.2)
    assert mic.chamadas == 0
    ctl.responder_confirmacao(p["id"], False)
    th.join(3)


def test_parar_nega_confirmacao_cancela_cerebro_e_cala_a_voz():
    voz, cerebro = VozFalsa(), CerebroFalso()
    ctl = montar(cerebro=cerebro, voz=voz)
    bus.conexoes = 1
    th, res = pedir(ctl)
    esperar_pendente(ctl)
    ctl.parar()
    th.join(3)
    assert res["sim"] is False and cerebro.cancelado and "<calou>" in voz.ditas


# ------------------------------------------------------------------ pedidos
def test_processar_emite_eventos_e_fala():
    voz = VozFalsa()
    ctl = montar(cerebro=CerebroFalso(["Primeira.", "Segunda."]), voz=voz)
    ctl.processar("oi", "voz")
    tipos = [e["tipo"] for e in bus.historico()]
    assert tipos == ["usuario", "jarvis", "jarvis", "fim_turno"] and voz.ditas == ["Primeira.", "Segunda."]


def test_fim_turno_sai_mesmo_se_o_cerebro_falhar():
    class Quebrado(CerebroFalso):
        def responder(self, t):
            raise RuntimeError("ollama caiu")
            yield
    ctl = montar(cerebro=Quebrado())
    with pytest.raises(RuntimeError):
        ctl.processar("oi")
    assert bus.historico()[-1]["tipo"] == "fim_turno"


def test_pedidos_sao_serializados():
    ordem = []

    class Lento(CerebroFalso):
        def responder(self, t):
            ordem.append(f"ini {t}")
            time.sleep(0.2)
            ordem.append(f"fim {t}")
            yield "ok"
    ctl = montar(cerebro=Lento())
    a = threading.Thread(target=ctl.processar, args=("a",))
    b = threading.Thread(target=ctl.processar, args=("b",))
    a.start(); time.sleep(0.05); b.start(); a.join(); b.join()
    assert ordem == ["ini a", "fim a", "ini b", "fim b"]


def test_texto_da_hud_comandos_e_limites():
    cerebro = CerebroFalso()
    mic = MicFalso()
    ctl = montar(cerebro=cerebro, mic=mic)
    ctl.texto_do_hud("   ")
    assert cerebro.pedidos == []
    ctl.texto_do_hud("esqueça a conversa")
    assert cerebro.esqueceu and cerebro.pedidos == []
    ctl.texto_do_hud("x" * 5000)
    assert len(cerebro.pedidos[0]) == 1500
    ctl.texto_do_hud("tchau jarvis")
    assert ctl.encerrar.is_set() and mic.encerrar.is_set()


def test_ocupado_so_durante_o_pedido_da_hud():
    visto = []

    class Observa(CerebroFalso):
        def responder(self, t):
            visto.append(ctl.ocupado.is_set())
            yield "ok"
    ctl = montar(cerebro=Observa())
    ctl.texto_do_hud("oi")
    assert visto == [True] and not ctl.ocupado.is_set()


# ------------------------------------------------------------------ cérebro: cancelar
class TrechoFalso:
    def __init__(self, texto="", chamadas=None, done=False):
        self.message = NS(content=texto, tool_calls=chamadas)
        self.done = done


class StreamFalso:
    def __init__(self, partes, ao_ler=None):
        self.partes, self.ao_ler, self.fechado = partes, ao_ler, False

    def __iter__(self):
        for i, p in enumerate(self.partes):
            if self.ao_ler:
                self.ao_ler(i)
            yield p

    def close(self):
        self.fechado = True


def cerebro_com(stream):
    cfg = {"nome": "Jarvis", "tratamento": "senhor",
           "llm": {"modelo": "x", "host": "http://127.0.0.1:1", "keep_alive": -1, "temperatura": 0.3, "max_turnos": 10}}
    c = Cerebro(cfg)
    c.cliente = NS(chat=lambda **kw: stream)
    return c


def test_cancelar_interrompe_e_desfaz_o_pedido():
    c = cerebro_com(None)
    c.historico = [{"role": "user", "content": "antigo"}, {"role": "assistant", "content": "resposta antiga"}]
    stream = StreamFalso([TrechoFalso("Vou "), TrechoFalso("contar uma história muito longa. "),
                          TrechoFalso("Era uma vez. "), TrechoFalso("Fim.", done=True)],
                         ao_ler=lambda i: c.cancelar() if i == 1 else None)
    c.cliente = NS(chat=lambda **kw: stream)
    saida = list(c.responder("conta uma história"))
    assert stream.fechado and len(saida) < 3
    assert c.historico == [{"role": "user", "content": "antigo"}, {"role": "assistant", "content": "resposta antiga"}]


def test_cancelamento_nao_vaza_para_o_proximo_pedido():
    c = cerebro_com(StreamFalso([TrechoFalso("Olá. ", done=True)]))
    c.cancelar()  # clicou em parar sem nada em andamento
    assert list(c.responder("oi")) == ["Olá."]


# ------------------------------------------------------------------ microfone (sem dispositivo real)
def mic_sem_dispositivo():
    m = Microfone.__new__(Microfone)
    m.fila, m.nivel, m.silenciado, m.encerrar = queue.Queue(), 0.0, False, threading.Event()
    m.taxa, m.silencio_ms, m.max_fala_s, m.limiar = 16000, 900, 20, 0.5
    m.wake = NS(reset=lambda: None, predict=lambda b: {"x": 1.0})
    m.vad = NS(is_speech=lambda *a: False)
    return m


def test_esperar_wakeword_respeita_encerrar_e_mudo():
    m = mic_sem_dispositivo()
    m.encerrar.set()
    assert m.esperar_wakeword() is False
    # esperar_wakeword descarta o áudio acumulado ao começar (eco), então o áudio entra depois que a espera começou
    bloco = np.zeros(BLOCO, dtype=np.int16)
    m.encerrar.clear()
    m.silenciado = True
    threading.Timer(0.2, m.fila.put, args=(bloco,)).start()
    threading.Timer(0.6, m.encerrar.set).start()
    assert m.esperar_wakeword() is False  # mudo: o "Hey Jarvis" (score 1.0) foi ignorado
    m.encerrar.clear()
    m.silenciado = False
    threading.Timer(0.2, m.fila.put, args=(bloco,)).start()
    assert m.esperar_wakeword() is True


def test_gravar_fala_cancelavel():
    m = mic_sem_dispositivo()
    cancelar = threading.Event()
    threading.Timer(0.3, cancelar.set).start()
    t = time.time()
    assert m.gravar_fala(30, cancelar=cancelar) is None and time.time() - t < 2


# ------------------------------------------------------------------ servidor: autenticação
@pytest.fixture
def servidor():
    ctl = montar(voz=VozFalsa(), mic=MicFalso(), memoria=None)
    srv = ServidorHUD(ctl, porta=8830)
    base = f"http://127.0.0.1:{srv.porta}"
    return srv, TestClient(srv.app, base_url=base), base


def ws_url(srv, token=None):
    return f"/ws?token={srv.token if token is None else token}"


def tentar_ws(cliente, url, origin):
    # o TestClient manda "Host: testserver" em WebSocket; um navegador mandaria o host real
    host = origin.removeprefix("http://") if origin.startswith("http://127.0.0.1") else "127.0.0.1"
    host = host if ":" in host else f"{host}:{cliente.base_url.port}"
    try:
        with cliente.websocket_connect(url, headers={"origin": origin, "host": host}) as ws:
            return ws.receive_json()
    except WebSocketDisconnect as e:
        return {"fechou": e.code}


def test_pagina_traz_token_e_cabecalhos_de_seguranca(servidor):
    srv, c, _ = servidor
    r = c.get("/")
    assert r.status_code == 200 and srv.token in r.text and "__TOKEN__" not in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"] and "'unsafe-inline'" not in r.headers["content-security-policy"].split("script-src")[1].split(";")[0]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"


def test_host_invalido_dns_rebinding(servidor):
    srv, c, _ = servidor
    assert c.get("/", headers={"host": "atacante.com"}).status_code == 403
    assert c.get("/", headers={"host": f"127.0.0.1:{srv.porta + 1}"}).status_code == 403


def test_arquivos_estaticos_sem_fuga_de_pasta(servidor):
    _, c, _ = servidor
    assert c.get("/web/hud.js").status_code == 200 and c.get("/web/hud.css").status_code == 200
    assert c.get("/web/index.html").status_code == 404           # a página só sai por "/", com o token
    for caminho in ("/web/..%2fservidor.py", "/web/%2e%2e/bus.py", "/web/..\\servidor.py", "/web/../../../config.yaml"):
        assert c.get(caminho).status_code in (404, 400, 403)


def test_websocket_exige_token_origem_e_host(servidor):
    srv, c, base = servidor
    ok = tentar_ws(c, ws_url(srv), base)
    assert ok["tipo"] == "hello" and ok["nome"] == "Jarvis"
    assert tentar_ws(c, ws_url(srv, "errado"), base) == {"fechou": 4403}
    assert tentar_ws(c, "/ws", base) == {"fechou": 4403}
    assert tentar_ws(c, ws_url(srv), "http://atacante.com") == {"fechou": 4403}   # outro site tentando conectar
    assert tentar_ws(c, ws_url(srv), "null") == {"fechou": 4403}
    assert tentar_ws(c, ws_url(srv), "") == {"fechou": 4403}
    try:
        with c.websocket_connect(ws_url(srv), headers={"origin": base, "host": "atacante.com"}) as ws:
            ws.receive_json()
        assert False, "deveria ter recusado o host"
    except WebSocketDisconnect as e:
        assert e.code == 4403


def test_mensagens_invalidas_sao_ignoradas(servidor):
    srv, c, base = servidor
    ctl = srv.controle
    bus.conexoes = 1  # há HUD conectada: a confirmação espera o clique em vez de negar na hora
    th, res = pedir(ctl)
    p = esperar_pendente(ctl)
    with c.websocket_connect(ws_url(srv), headers={"origin": base, "host": f"127.0.0.1:{srv.porta}"}) as ws:
        ws.receive_json()
        for lixo in ['{"tipo":"confirmar","id":"1","sim":true}',            # id não é inteiro
                     f'{{"tipo":"confirmar","id":{p["id"]},"sim":"true"}}',  # sim não é booleano
                     f'{{"tipo":"confirmar","id":{p["id"]},"sim":1}}',       # 1 não é True
                     f'{{"tipo":"confirmar","id":{p["id"] + 7},"sim":true}}',
                     "isso não é json", "[1,2,3]", '"texto"', '{"tipo":"formatar_disco"}', "x" * 9000]:
            ws.send_text(lixo)
        time.sleep(0.4)
        assert th.is_alive() and ctl._pendente["id"] == p["id"]           # nada disso confirmou a ação
        ws.send_text(f'{{"tipo":"confirmar","id":{p["id"]},"sim":true}}')
        th.join(3)
        assert res["sim"] is True


def test_mensagens_validas_chegam_ao_controle(servidor):
    srv, c, base = servidor
    ctl = srv.controle
    ctl.cerebro = CerebroFalso()
    with c.websocket_connect(ws_url(srv), headers={"origin": base, "host": f"127.0.0.1:{srv.porta}"}) as ws:
        ws.receive_json()
        ws.send_json({"tipo": "texto", "texto": "que horas são?"})
        ws.send_json({"tipo": "mudo", "valor": True})
        ws.send_json({"tipo": "parar"})
        time.sleep(0.6)
    assert ctl.cerebro.pedidos == ["que horas são?"] and ctl.mic.silenciado is True and ctl.cerebro.cancelado


def test_hello_traz_historico_e_estado(servidor):
    srv, c, base = servidor
    bus.emitir("usuario", texto="oi", origem="voz")
    bus.emitir("estado", estado="ouvindo")
    hello = tentar_ws(c, ws_url(srv), base)
    assert hello["estado"] == "ouvindo" and hello["historico"][0]["texto"] == "oi" and hello["tem_mic"] and hello["tem_voz"]


def test_porta_ocupada_escolhe_outra():
    from jarvis.hud.servidor import porta_livre
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        ocupada = s.getsockname()[1]
        assert porta_livre("127.0.0.1", ocupada) != ocupada



@pytest.mark.parametrize("tema,esperado", [(None, "ciano"), ("ciano", "ciano"), ("dourado", "dourado"),
                                           ('"><script>alert(1)</script>', "ciano")])
def test_tema_do_config_vai_no_html_e_valor_estranho_nao_entra(tema, esperado):
    ctl = montar(voz=VozFalsa(), mic=MicFalso(), memoria=None)
    if tema is not None:
        ctl.cfg = {**ctl.cfg, "hud": {"tema": tema}}
    srv = ServidorHUD(ctl, porta=8831)
    html = TestClient(srv.app, base_url=f"http://127.0.0.1:{srv.porta}").get("/").text
    assert f'data-tema="{esperado}"' in html and "__TEMA__" not in html and "<script>alert" not in html
