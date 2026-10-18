"""Visão da tela e mouse: coordenadas, travas de segurança, imagem chegando ao Claude (API e assinatura) e o Qwen de fora."""
import json
import time

import pytest

from jarvis.tools import tela
from jarvis.tools.base import ComImagem, ErroFerramenta, Ferramenta, Nivel
from jarvis.tools.executor import Executor
from jarvis.tools.interface import Contexto


@pytest.fixture(autouse=True)
def sem_captura():
    tela._ULTIMA.clear()
    tela._ULTIMA["captura"] = None
    yield
    tela._ULTIMA["captura"] = None


def captura(monitor=2, esquerda=1920, largura=2560, altura=1080, idade=0.0):
    return tela.Captura(monitor, esquerda, 0, largura, altura, tela.escala_para(largura, altura), time.time() - idade)


# ------------------------------------------------------------------ geometria
def test_imagem_cabe_no_limite_do_claude():
    assert tela.escala_para(1920, 1080) == pytest.approx(1568 / 1920)
    assert tela.escala_para(1280, 720) == 1.0                         # tela pequena não é ampliada
    assert max(captura().tamanho_imagem) == 1568


def test_ponto_da_imagem_vira_ponto_real_no_monitor_certo():
    cap = captura()                                                   # monitor 2 começa em x=1920
    assert tela.para_tela(cap, 0, 0) == (1920, 0)
    x, y = tela.para_tela(cap, 784, 331)
    assert abs(x - (1920 + 1280)) <= 2 and abs(y - 540) <= 2          # centro da imagem = centro do monitor
    assert tela.para_imagem(cap, *tela.para_tela(cap, 500, 200)) in [(500, 200), (499, 200), (500, 199), (499, 199)]
    assert tela.para_imagem(cap, 100, 100) is None                    # o ponto está no outro monitor


@pytest.mark.parametrize("x,y", [(-1, 10), (10, -1), (1568, 10), (10, 662), (5000, 5000)])
def test_ponto_fora_da_imagem_e_recusado(x, y):
    with pytest.raises(ErroFerramenta, match="fora da imagem"):
        tela.para_tela(captura(), x, y)


# ------------------------------------------------------------------ travas
def test_mouse_sem_ver_tela_antes_e_erro_claro():
    with pytest.raises(ErroFerramenta, match="ver_tela antes"):
        tela._avaliar_mouse({"acao": "clicar", "x": 10, "y": 10})


def test_captura_velha_e_recusada():
    tela._ULTIMA["captura"] = captura(idade=tela.VALIDADE_S + 1)
    with pytest.raises(ErroFerramenta, match="velha"):
        tela._avaliar_mouse({"acao": "clicar", "x": 10, "y": 10})


def test_novo_pedido_esquece_a_captura(tmp_path):
    tela._ULTIMA["captura"] = captura()
    Executor(lambda *a, **k: True, str(tmp_path), {}).novo_turno()
    assert tela._ULTIMA["captura"] is None


def sob_o_ponto(monkeypatch, titulo, processo, nomes):
    tela._ULTIMA["captura"] = captura()
    monkeypatch.setattr(tela, "_janela_no_ponto", lambda x, y: (Contexto(titulo, processo), nomes))


def test_clique_comum_e_livre(monkeypatch):
    sob_o_ponto(monkeypatch, "Spotify Premium", "Spotify.exe", ["Tocar"])
    assert tela._avaliar_mouse({"acao": "clicar", "x": 100, "y": 100}).nivel == Nivel.LIVRE


@pytest.mark.parametrize("nomes", [["Excluir"], ["Enviar mensagem"], ["Pagar agora"], ["Fechar"], ["Instalar"]])
def test_botao_de_risco_pede_confirmacao(monkeypatch, nomes):
    sob_o_ponto(monkeypatch, "Algum app", "app.exe", nomes)
    a = tela._avaliar_mouse({"acao": "clicar", "x": 100, "y": 100})
    assert a.nivel == Nivel.CONFIRMAR and nomes[0] in a.resumo


@pytest.mark.parametrize("titulo,processo", [("Administrador: Prompt de Comando", "cmd.exe"), ("Nubank", "msedge.exe"),
                                             ("Editor do Registro", "regedit.exe")])
def test_janela_sensivel_pede_confirmacao(monkeypatch, titulo, processo):
    sob_o_ponto(monkeypatch, titulo, processo, ["Ok"])
    assert tela._avaliar_mouse({"acao": "duplo_clique", "x": 100, "y": 100}).nivel == Nivel.CONFIRMAR


@pytest.mark.parametrize("acao", ["clicar", "clique_direito", "rolar_baixo", "arrastar"])
def test_nunca_mexe_na_hud(monkeypatch, acao):
    sob_o_ponto(monkeypatch, "J.A.R.V.I.S", "msedge.exe", ["Confirmar"])
    with pytest.raises(ErroFerramenta, match="HUD"):
        tela._avaliar_mouse({"acao": acao, "x": 100, "y": 100, "ate_x": 200, "ate_y": 200})


def test_arrastar_confere_o_ponto_de_chegada(monkeypatch):
    tela._ULTIMA["captura"] = captura()
    alvos = {}

    def no_ponto(x, y):
        alvos[(x, y)] = True
        return (Contexto("Lixeira", "explorer.exe"), ["Esvaziar Lixeira"]) if len(alvos) > 1 else (Contexto("Área de Trabalho", "explorer.exe"), ["foto.png"])
    monkeypatch.setattr(tela, "_janela_no_ponto", no_ponto)
    assert tela._avaliar_mouse({"acao": "arrastar", "x": 10, "y": 10, "ate_x": 500, "ate_y": 300}).nivel == Nivel.CONFIRMAR
    with pytest.raises(ErroFerramenta, match="ate_x"):
        tela._avaliar_mouse({"acao": "arrastar", "x": 10, "y": 10})


def test_mover_e_rolar_sao_livres(monkeypatch):
    sob_o_ponto(monkeypatch, "Algum app", "app.exe", ["Excluir"])
    assert tela._avaliar_mouse({"acao": "mover", "x": 10, "y": 10}).nivel == Nivel.LIVRE
    assert tela._avaliar_mouse({"acao": "rolar_baixo", "x": 10, "y": 10}).nivel == Nivel.LIVRE


def test_trava_do_x_de_apps_electron(monkeypatch):
    """O X do VS Code/Discord não aparece na acessibilidade: quem diz que é 'fechar' é a própria janela."""
    class Ctrl:
        ControlTypeName, Name = "PaneControl", ""

        def GetParentControl(self):
            return None

        def GetTopLevelControl(self):
            return self

    from jarvis.tools import interface
    monkeypatch.setattr(interface, "auto", type("A", (), {"ControlFromPoint": staticmethod(lambda x, y: Ctrl())}))
    monkeypatch.setattr(interface, "_contexto", lambda j: Contexto("Projeto - Visual Studio Code", "Code.exe"))
    monkeypatch.setattr(tela, "_botao_da_moldura", lambda x, y: tela.HT_FECHAR)
    ctx, nomes = tela._janela_no_ponto(100, 100)
    assert nomes == ["Fechar"] and tela.nivel_do_clique(ctx, nomes)[0] == Nivel.CONFIRMAR
    monkeypatch.setattr(tela, "_botao_da_moldura", lambda x, y: 9)      # maximizar: tudo bem
    assert tela.nivel_do_clique(*tela._janela_no_ponto(100, 100))[0] == Nivel.LIVRE


# ------------------------------------------------------------------ a imagem chega a quem enxerga
def ferramenta_de_print():
    return Ferramenta("ver_tela", "Print.", lambda: ComImagem("Tela do monitor 1.", "QUJD", "image/jpeg"), visao=True)


def test_registradas_e_fora_do_qwen(tmp_path):
    import jarvis.tools.todas  # noqa: F401
    from jarvis.tools.base import REGISTRO
    assert REGISTRO["ver_tela"].visao and REGISTRO["mouse"].visao
    ex = Executor(lambda *a, **k: True, str(tmp_path))
    nomes_qwen = {s["function"]["name"] for s in ex.schemas()}
    nomes_claude = {s["function"]["name"] for s in ex.schemas(visao=True)}
    assert "ver_tela" not in nomes_qwen and "mouse" not in nomes_qwen                # o Qwen não enxerga imagem
    assert {"ver_tela", "mouse"} <= nomes_claude and nomes_claude - nomes_qwen == {"ver_tela", "mouse"}


def test_executor_preserva_a_imagem(tmp_path):
    ex = Executor(lambda *a, **k: True, str(tmp_path), {"ver_tela": ferramenta_de_print()}, avisar=lambda t: None)
    r = ex.executar("ver_tela", {})
    assert r == "Tela do monitor 1." and r.imagem == "QUJD" and r.tipo == "image/jpeg"
    log = (tmp_path / "acoes.jsonl").read_text(encoding="utf-8")
    assert "QUJD" not in log                                                            # o log não guarda o print


def test_ponte_http_e_mcp_entregam_a_imagem(tmp_path):
    from jarvis.brain.claude_cli import PonteHTTP
    from test_claude_cli import conversar_mcp, http
    ex = Executor(lambda *a, **k: True, str(tmp_path), {"ver_tela": ferramenta_de_print()}, avisar=lambda t: None)
    p = PonteHTTP(ex)
    try:
        assert {f["name"] for f in http(p, "/ferramentas")[1]["ferramentas"]} == {"ver_tela"}
        assert http(p, "/executar", {"nome": "ver_tela", "args": {}})[1]["imagem"] == {"dados": "QUJD", "tipo": "image/jpeg"}
        respostas, _ = conversar_mcp(p, [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                          "params": {"name": "ver_tela", "arguments": {}}}])
    finally:
        p.parar()
    assert respostas[0]["result"]["content"] == [{"type": "text", "text": "Tela do monitor 1."},
                                                 {"type": "image", "data": "QUJD", "mimeType": "image/jpeg"}]


def test_claude_api_recebe_a_imagem_no_resultado(tmp_path):
    from test_claude import Bloco, FluxoFalso, cerebro, msg_final, texto
    c = cerebro(tmp_path, [FluxoFalso([], msg_final([Bloco(type="tool_use", id="t1", name="ver_tela", input={})], "tool_use")),
                           FluxoFalso(["Vejo a área de trabalho."], msg_final([texto("Vejo a área de trabalho.")]))])
    c.executor = Executor(lambda *a, **k: True, str(tmp_path), {"ver_tela": ferramenta_de_print()}, avisar=lambda t: None)
    list(c.responder("o que tem na tela?"))
    resultado = json.loads(json.dumps(c.hist[2]["content"][0], default=str))
    assert resultado["content"][0] == {"type": "text", "text": "Tela do monitor 1."}
    assert resultado["content"][1]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}
