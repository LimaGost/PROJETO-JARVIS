"""Seletor de mentalidade: comandos de voz, escolha, troca (com falha segura), persistência e integração com o Controle."""
import json
import threading
import time

import pytest

from jarvis.brain import seletor as modulo
from jarvis.brain.comandos import destino_cerebro, identificar
from jarvis.brain.seletor import Seletor
from jarvis.hud.bus import bus
from jarvis.hud.controle import Controle

CFG = {"nome": "Jarvis", "tratamento": "senhor",
       "llm": {"modelo": "qwen3:8b", "host": "http://127.0.0.1:1", "keep_alive": -1, "temperatura": 0.3, "max_turnos": 10},
       "claude_cli": {"modelo": "sonnet"}, "claude": {"modelo": "claude-opus-5-5"}, "cerebro": {"motor": "ollama"}}


@pytest.fixture(autouse=True)
def isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo, "ARQUIVO_PREFERENCIAS", tmp_path / "preferencias.json")
    bus.limpar()
    yield
    bus.limpar()


# ------------------------------------------------------------------ voz
@pytest.mark.parametrize("fala,esperado", [
    ("Usa o Qwen", "qwen"), ("usar o qwen", "qwen"), ("Muda para o Qwen.", "qwen"), ("troca pro quen", "qwen"),
    ("usa o queen", "qwen"), ("modo qwen", "qwen"), ("volta pro modelo local", "qwen"), ("coloca no local", "qwen"),
    ("Usa o Claude", "claude"), ("muda para o claude", "claude"), ("troca para o clode", "claude"),
    ("ativa o claudio", "claude"), ("modo claude", "claude"), ("mentalidade claude", "claude"),
    ("muda a mentalidade para o claude", "claude"),
])
def test_destino_por_voz(fala, esperado):
    assert destino_cerebro(fala) == esperado
    assert identificar(fala) == f"trocar:{esperado}"


@pytest.mark.parametrize("fala", [
    "O que é o Claude?", "Quem criou o Claude", "Claude", "Qwen", "me explica o que é o qwen",
    "usa o spotify", "muda o volume", "troca a música", "volta a música anterior", "abre o chrome e pesquisa claude",
    "qual a diferença entre o claude e o qwen", "pesquisa sobre o modelo local",
    "usa o claude para escrever um e-mail bem longo para o meu chefe sobre as férias",   # frase longa: é um pedido
])
def test_frases_normais_nao_trocam(fala):
    assert destino_cerebro(fala) is None


def test_comandos_antigos_continuam_funcionando():
    assert identificar("Tchau, Jarvis!") == "desligar" and identificar("Esqueça a conversa.") == "esquecer"
    assert identificar("Que horas são?") is None


# ------------------------------------------------------------------ fábricas falsas
class Cerebro:
    def __init__(self, rotulo, erro_aquecer=None, reserva=False):
        self.rotulo, self.erro_aquecer = rotulo, erro_aquecer
        self.principal = self
        self.usando_reserva, self.ultimo_erro = reserva, "falhou ao subir" if reserva else None
        self.aqueceu = self.esqueceu = self.cancelou = self.encerrou = 0
        self.memoria = self.executor = None
        self.sessao, self.historico = "s", []

    def aquecer(self):
        self.aqueceu += 1
        if self.erro_aquecer:
            raise self.erro_aquecer

    def esquecer(self):
        self.esqueceu += 1

    def cancelar(self):
        self.cancelou += 1

    def encerrar(self):
        self.encerrou += 1

    def responder(self, t):
        yield f"{self.rotulo}: {t}"


def seletor(cfg=None, fabricas=None, indisponiveis=None):
    indisponiveis = indisponiveis or {}
    s = Seletor(cfg or CFG, None, None, fabricas=fabricas if fabricas is not None else {
        "claude-cli": lambda: Cerebro("claude · sonnet"), "claude-api": lambda: Cerebro("claude-opus-5-5")},
        disponibilidade=lambda id_: indisponiveis.get(id_))
    s.local.aquecer = lambda: None                      # sem Ollama nos testes
    s.local.rotulo = "qwen3:8b"
    return s


# ------------------------------------------------------------------ escolha inicial e opções
def test_padrao_e_o_motor_do_config():
    assert seletor().atual == "qwen"
    assert seletor({**CFG, "cerebro": {"motor": "claude-cli"}}).atual == "claude-cli"
    assert seletor({**CFG, "cerebro": {"motor": "claude"}}).atual == "claude-api"


def test_preferencia_salva_vence_o_config():
    modulo.salvar_preferencia("claude-api")
    assert seletor().atual == "claude-api"


def test_preferencia_ou_motor_indisponivel_cai_no_proximo_candidato():
    modulo.salvar_preferencia("claude-cli")
    assert seletor(indisponiveis={"claude-cli": "sem claude"}).atual == "qwen"
    modulo.ARQUIVO_PREFERENCIAS.unlink()
    assert seletor({**CFG, "cerebro": {"motor": "claude"}}, indisponiveis={"claude-api": "sem chave"}).atual == "qwen"


def test_preferencia_corrompida_e_ignorada():
    modulo.ARQUIVO_PREFERENCIAS.write_text("{isso não é json", encoding="utf-8")
    assert seletor().atual == "qwen"
    modulo.ARQUIVO_PREFERENCIAS.write_text(json.dumps({"cerebro": "gpt-9"}), encoding="utf-8")
    assert seletor().atual == "qwen"


def test_opcoes_para_a_hud():
    s = seletor(indisponiveis={"claude-api": "falta a chave em data/anthropic.key"})
    o = {x["id"]: x for x in s.opcoes()}
    assert list(o) == ["qwen", "claude-cli", "claude-api"]
    assert o["qwen"]["ativo"] and o["qwen"]["disponivel"]
    assert o["claude-cli"]["disponivel"] and not o["claude-cli"]["ativo"] and "sonnet" in o["claude-cli"]["descricao"]
    assert not o["claude-api"]["disponivel"] and "chave" in o["claude-api"]["motivo"]
    assert s.estado()["atual"] == "qwen" and s.estado()["rotulo"] == "qwen3:8b"


def test_resolver_claude_prefere_assinatura_depois_api():
    assert seletor().resolver("claude") == "claude-cli"
    assert seletor(indisponiveis={"claude-cli": "x"}).resolver("claude") == "claude-api"
    assert seletor(indisponiveis={"claude-cli": "x", "claude-api": "y"}).resolver("claude") is None
    assert seletor().resolver("qwen") == "qwen"


# ------------------------------------------------------------------ trocar
def test_trocar_ok_salva_preferencia_zera_conversa_e_libera_o_anterior():
    s = seletor({**CFG, "cerebro": {"motor": "claude-cli"}})
    s.aquecer()
    cli = s._objetos["claude-cli"]
    r = s.trocar("qwen")
    assert r == {"ok": True, "atual": "qwen", "erro": None} and s.atual == "qwen"
    assert cli.encerrou == 1                                  # o processo do claude não fica ligado à toa
    assert json.loads(modulo.ARQUIVO_PREFERENCIAS.read_text(encoding="utf-8")) == {"cerebro": "qwen"}
    assert s.rotulo == "qwen3:8b"


def test_trocar_para_claude_cria_so_na_hora_e_comeca_conversa_nova():
    criados = []
    s = seletor(fabricas={"claude-cli": lambda: criados.append(1) or Cerebro("claude · sonnet")})
    assert criados == []                                       # o claude não sobe se você não pedir
    r = s.trocar("claude-cli")
    assert r["ok"] and s.atual == "claude-cli" and criados == [1]
    novo = s._objetos["claude-cli"]
    assert novo.aqueceu == 1 and novo.esqueceu == 1 and s.rotulo == "claude · sonnet"
    assert list(s.responder("oi")) == ["claude · sonnet: oi"]
    s.trocar("qwen")
    s.trocar("claude-cli")
    assert criados == [1]                                      # reutiliza o mesmo objeto


def test_trocar_para_o_mesmo_nao_faz_nada():
    s = seletor()
    assert s.trocar("qwen") == {"ok": True, "atual": "qwen", "erro": None}


@pytest.mark.parametrize("fabrica,trecho", [
    (lambda: Cerebro("x", erro_aquecer=RuntimeError("isolamento do claude falhou")), "isolamento"),
    (lambda: (_ for _ in ()).throw(RuntimeError("não achei o programa claude")), "claude"),
])
def test_troca_que_falha_mantem_o_cerebro_atual_e_nao_salva(fabrica, trecho):
    s = seletor(fabricas={"claude-cli": fabrica})
    r = s.trocar("claude-cli")
    assert r["ok"] is False and trecho in r["erro"] and r["atual"] == "qwen"
    assert s.atual == "qwen" and "claude-cli" not in s._objetos       # a próxima tentativa começa limpa
    assert not modulo.ARQUIVO_PREFERENCIAS.exists()


def test_reserva_assumindo_em_silencio_conta_como_falha_na_troca_manual():
    s = seletor(fabricas={"claude-cli": lambda: Cerebro("claude · sonnet", reserva=True)})
    r = s.trocar("claude-cli")
    assert r["ok"] is False and "falhou ao subir" in r["erro"] and s.atual == "qwen"


@pytest.mark.parametrize("id_,motivo", [("claude-api", "falta a chave"), ("gpt-9", "opção desconhecida")])
def test_troca_para_indisponivel_ou_desconhecida(id_, motivo):
    s = seletor(indisponiveis={"claude-api": "falta a chave em data/anthropic.key"})
    r = s.trocar(id_)
    assert r["ok"] is False and motivo in r["erro"] and s.atual == "qwen"


def test_falha_do_claude_escolhido_na_partida_cai_no_qwen():
    s = seletor({**CFG, "cerebro": {"motor": "claude-cli"}},
                fabricas={"claude-cli": lambda: Cerebro("claude · sonnet", erro_aquecer=RuntimeError("login"))})
    s.aquecer()
    assert s.atual == "qwen"


def test_cancelar_e_esquecer_chegam_a_todos_os_cerebros_criados():
    s = seletor()
    s.trocar("claude-cli")
    s.cancelar()
    s.esquecer()
    assert s._objetos["claude-cli"].cancelou == 1 and s._objetos["claude-cli"].esqueceu >= 2


# ------------------------------------------------------------------ Controle
class Voz:
    def __init__(self):
        self.ditas = []

    def falar(self, t):
        self.ditas.append(t)

    def esperar(self): pass
    def calar(self): pass
    def nivel(self): return 0.0


def controle(sel, voz=None):
    c = Controle({"nome": "Jarvis", "tratamento": "senhor", "llm": {"modelo": "qwen3:8b"}, "vad": {"espera_inicio_s": 1}}, sel, voz=voz)
    return c


def eventos(tipo):
    capturados = []
    bus.assinar(lambda e: capturados.append(e) if e["tipo"] == tipo else None)
    return capturados


def test_controle_troca_avisa_a_hud_e_fala():
    voz, s = Voz(), seletor()
    c = controle(s, voz)
    cer, sis = eventos("cerebro"), eventos("sistema")
    c.trocar_cerebro("claude-cli")
    assert s.atual == "claude-cli"
    assert cer[0]["trocando"] == "claude-cli" and cer[-1]["trocando"] is None and cer[-1]["atual"] == "claude-cli"
    assert any("claude · sonnet" in e["texto"] and "continua de onde parou" in e["texto"] for e in sis)
    assert voz.ditas == ["Mentalidade Claude ativada."]


def test_controle_troca_falha_avisa_e_continua_com_a_atual():
    voz = Voz()
    s = seletor(fabricas={"claude-cli": lambda: Cerebro("x", erro_aquecer=RuntimeError("login expirou"))})
    c = controle(s, voz)
    sis = eventos("sistema")
    c.trocar_cerebro("claude-cli")
    assert s.atual == "qwen"
    assert any("Não consegui trocar" in e["texto"] and "login expirou" in e["texto"] and "qwen3:8b" in e["texto"] for e in sis)
    assert "Não consegui trocar" in voz.ditas[0]


def test_controle_espera_o_pedido_em_andamento_acabar_antes_de_trocar():
    s = seletor()
    c = controle(s)
    c.turno_lock.acquire()                                   # simula um pedido rodando
    th = threading.Thread(target=c.trocar_cerebro, args=("claude-cli",))
    th.start()
    time.sleep(0.4)
    assert s.atual == "qwen" and th.is_alive()               # esperando, sem trocar no meio do pedido
    c.turno_lock.release()
    th.join(5)
    assert s.atual == "claude-cli"


def test_controle_ocupado_demais_devolve_o_estado_para_a_hud():
    class Travado:
        def acquire(self, timeout=None): return False
    s = seletor()
    c = controle(s)
    c.turno_lock = Travado()
    cer = eventos("cerebro")
    c.trocar_cerebro("claude-cli")
    assert s.atual == "qwen"
    assert cer and cer[-1]["trocando"] is None and cer[-1]["atual"] == "qwen"   # a HUD sai do "trocando"


def test_controle_por_alvo_claude_indisponivel_avisa():
    voz = Voz()
    s = seletor(indisponiveis={"claude-cli": "x", "claude-api": "y"})
    c = controle(s, voz)
    c.trocar_por_alvo("claude")
    assert s.atual == "qwen" and "não está disponível" in voz.ditas[0]


def test_comando_digitado_na_hud_troca():
    s = seletor()
    c = controle(s)
    c.texto_do_hud("usa o claude")
    assert s.atual == "claude-cli"
    c.texto_do_hud("muda para o qwen")
    assert s.atual == "qwen"


def test_hello_antes_de_aquecer_com_o_claude_salvo_nao_quebra():
    modulo.salvar_preferencia("claude-cli")
    s = seletor()
    c = controle(s)
    h = c.hello()                                            # a HUD conecta enquanto o Jarvis ainda carrega
    assert s.atual == "claude-cli" and h["cerebro"]["rotulo"] == "Claude · assinatura"


def test_hello_leva_o_estado_do_seletor():
    s = seletor()
    c = controle(s)
    h = c.hello()
    assert h["cerebro"]["atual"] == "qwen" and h["modelo"] == "qwen3:8b" and len(h["cerebro"]["opcoes"]) == 3


def test_controle_sem_seletor_nao_quebra():
    class SoUm:
        rotulo = "x"
    c = controle(SoUm())
    c.trocar_cerebro("qwen")
    assert c.hello()["cerebro"] is None


# ------------------------------------------------------------------ VRAM: o Qwen só mora na GPU quando é o cérebro
def test_qwen_so_fica_na_gpu_quando_e_o_cerebro():
    s = seletor({**CFG, "llm": {**CFG["llm"], "keep_alive_reserva": "2m"}, "cerebro": {"motor": "claude-cli"}})
    assert s.local.keep_alive == "2m"                         # reserva do Claude: sai da GPU depois de usar
    descargas = []
    s.local.descarregar = lambda: descargas.append(1)
    s.aquecer()
    assert s.trocar("qwen")["ok"] and s.local.keep_alive == -1 and descargas == []
    assert s.trocar("claude-cli")["ok"] and s.local.keep_alive == "2m"
    assert descargas == [1]                                   # largou o Qwen: libera a VRAM na hora


def test_reserva_sem_keep_alive_configurado_usa_2_minutos():
    assert seletor({**CFG, "cerebro": {"motor": "claude-cli"}}).local.keep_alive == "2m"
    assert seletor().local.keep_alive == -1
