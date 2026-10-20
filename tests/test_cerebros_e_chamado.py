"""Chamado por frase ("Ei Jarvis", "Jarvis, está aí?"), pesquisa em várias páginas, cérebros ChatGPT (Codex CLI) e
Gemini (Gemini CLI) com a trava de isolamento, login automático e automações desligadas."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from jarvis.audio import chamado
from jarvis.brain import logins, outros_cli
from jarvis.brain.claude import ErroCerebro
from jarvis.tools import web
from jarvis.tools.base import ErroFerramenta

PY = sys.executable


# ------------------------------------------------------------------ chamado
@pytest.mark.parametrize("frase,pedido", [
    ("Ei Jarvis", ""), ("Hey Jarvis", ""), ("Jarvis, está aí?", ""), ("Oi, Jarvis.", ""), ("E aí, Jarvis", ""),
    ("Você está aí, Jarvis?", ""), ("Bom dia Jarvis", ""), ("Jarvis tá me ouvindo?", ""), ("Ô Jarvis", ""),
    ("Jarbas!", ""), ("Ei Jarves", ""), ("Djarvis", ""),
    ("Jarvis, que horas são?", "que horas são"), ("Hey Jarvis, abre o Spotify", "abre o Spotify"),
    ("Jarvis, pesquise o preço do iPhone em várias lojas", "pesquise o preço do iPhone em várias lojas"),
])
def test_chamados_que_acordam(frase, pedido):
    assert chamado.analisar(frase) == (True, pedido)


@pytest.mark.parametrize("frase", [
    "Ontem eu vi o filme do Homem de Ferro com o Jarvis no cinema", "Eu estava programando em Java hoje",
    "o jarvis é legal demais cara", "que horas são", "", "Jarvis",
])
def test_conversa_que_nao_e_chamado(frase):
    if frase == "Jarvis":                       # o nome sozinho É chamado
        assert chamado.analisar(frase) == (True, "")
    else:
        assert chamado.analisar(frase)[0] is False


class _Wake:
    def __init__(self):
        self.n = 0

    def reset(self):
        pass

    def predict(self, bloco):
        return {"hey_jarvis": 0.0}


class _Ouvido:
    def __init__(self, texto):
        self.texto, self.chamadas = texto, 0

    def transcrever(self, audio):
        self.chamadas += 1
        return self.texto


def _mic_com_audio(blocos_voz: int, blocos_silencio: int):
    """Um Microfone sem placa de som: a fila recebe 'voz' e depois silêncio."""
    import queue
    import threading
    from jarvis.audio.mic import BLOCO, Microfone
    m = Microfone.__new__(Microfone)
    m.taxa, m.limiar, m.silenciado = 16000, 0.5, False
    m.fila, m.encerrar, m.wake = queue.Queue(), threading.Event(), _Wake()
    voz = (np.sin(np.arange(BLOCO) / 3) * 8000).astype(np.int16)
    for _ in range(blocos_voz):
        m.fila.put(voz)
    for _ in range(blocos_silencio):
        m.fila.put(np.zeros(BLOCO, dtype=np.int16))
    m._tem_voz = lambda b: bool(np.abs(b).max() > 100)
    m.limpar = lambda: None
    return m


def test_microfone_acorda_por_frase_e_traz_o_pedido():
    m = _mic_com_audio(15, 10)
    ouvido = _Ouvido("Jarvis, que horas são?")
    assert m.esperar_chamado(ouvido) == (True, "que horas são") and ouvido.chamadas == 1


def test_microfone_ignora_frase_que_nao_e_chamado():
    m = _mic_com_audio(15, 10)
    ouvido = _Ouvido("vamos almoçar agora")
    m.fila.put(None)                             # depois do áudio, encerra o laço
    m.encerrar.set()
    assert m.esperar_chamado(ouvido) == (False, "")


# ------------------------------------------------------------------ pesquisa em várias páginas
@pytest.fixture
def web_falsa(monkeypatch):
    from jarvis.tools import leitura
    paginas = {
        "https://a.com/x": ("Menu\n\nO dólar fechou a R$ 5,10 nesta segunda, alta de 0,3%.\n\nRodapé qualquer coisa aqui", "Site A"),
        "https://b.com/y": ("Notícia: o dólar comercial subiu para R$ 5,11 com o mercado atento ao juro.", "Site B"),
        "https://lento.com": None,
        "https://quebrado.com": "erro",
    }

    def ler(url):
        p = paginas.get(url)
        if p is None:
            time.sleep(3)
            return "tarde", "Lento", url
        if p == "erro":
            raise ErroFerramenta("a página não abriu")
        return p[0], p[1], url
    monkeypatch.setattr(leitura, "ler", ler)
    monkeypatch.setattr(web, "ORCAMENTO_LEITURA_S", 1)
    monkeypatch.setattr(web, "buscar", lambda consulta, quantos=5: [
        {"title": "A", "body": "dólar hoje", "href": "https://a.com/x"},
        {"title": "A de novo", "body": "repetido", "href": "https://www.a.com/z"},
        {"title": "B", "body": "dólar sobe", "href": "https://b.com/y"},
        {"title": "Q", "body": "", "href": "https://quebrado.com"},
        {"title": "L", "body": "", "href": "https://lento.com"}])
    return paginas


def test_pesquisar_a_fundo_le_varias_paginas_em_paralelo(web_falsa):
    t = time.time()
    r = web.pesquisar_a_fundo("cotação do dólar hoje", paginas=4)
    assert time.time() - t < 3                                    # não espera a página lenta inteira
    assert "Site A" in r and "Site B" in r and "R$ 5,10" in r and "R$ 5,11" in r
    assert "https://a.com/x" in r and "www.a.com/z" not in r      # um resultado por site
    assert "Não abriram" in r and "quebrado.com" in r
    assert "não confiável" in r                                   # conteúdo de terceiros marcado


def test_pesquisar_a_fundo_com_enderecos_diretos(web_falsa):
    r = web.pesquisar_a_fundo("dólar", enderecos=["https://b.com/y"])
    assert "Site B" in r and "Site A" not in r


def test_trechos_relevantes_pega_o_que_fala_do_assunto():
    texto = "\n".join(["Bem-vindo ao portal de notícias da cidade grande!"] * 5 +
                      ["O preço da gasolina subiu para R$ 6,20 nos postos da capital."] +
                      ["Veja também outras matérias interessantes hoje aqui."] * 20)
    trecho = web.trechos_relevantes(texto, "preço da gasolina", 200)
    assert "gasolina" in trecho and len(trecho) <= 200


# ------------------------------------------------------------------ cérebros ChatGPT e Gemini
class _Executor:
    log = "x.log"

    def schemas(self, visao=False):
        return [{"function": {"name": n, "description": "d", "parameters": {}}} for n in ("hora_data", "pesquisar_web")]

    def executar(self, nome, args):
        return "ok"

    def novo_turno(self):
        pass


CFG = {"nome": "Jarvis", "tratamento": "senhor",
       "llm": {"host": "http://127.0.0.1:11434", "modelo": "qwen3:8b", "keep_alive": -1, "temperatura": 0.5,
               "max_turnos": 6}}


def _cli_falsa(tmp_path, eventos: list[dict], codigo: int = 0, nome="cli") -> list[str]:
    """Um 'programa' que imprime os eventos JSON (um por linha), como a CLI real."""
    script = tmp_path / f"{nome}.py"
    script.write_text("import sys, json\nsys.stdin.read()\n" +
                      "".join(f"print({json.dumps(json.dumps(e))}, flush=True)\n" for e in eventos) +
                      f"sys.exit({codigo})\n", encoding="utf-8")
    return [PY, str(script)]


def test_chatgpt_fala_e_guarda_a_sessao(tmp_path):
    cmd = _cli_falsa(tmp_path, [
        {"type": "thread.started", "thread_id": "th-1"}, {"type": "turn.started"},
        {"type": "error", "message": "Reconnecting... 1/5"},
        {"type": "item.started", "item": {"id": "1", "type": "mcp_tool_call", "server": "jarvis", "tool": "hora_data"}},
        {"type": "item.completed", "item": {"id": "1", "type": "mcp_tool_call", "server": "jarvis", "tool": "hora_data"}},
        {"type": "item.completed", "item": {"id": "2", "type": "agent_message", "text": "São dez horas, senhor. Algo mais?"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}])
    c = outros_cli.CerebroCodexCLI(CFG, _Executor(), None, comando=cmd)
    try:
        assert list(c.responder("que horas são?")) == ["São dez horas, senhor.", "Algo mais?"]
        assert c.sessao_cli == "th-1"
        argv = c.argumentos("th-1")
        assert argv[:3] == ["exec", "resume", "th-1"] and "--ignore-user-config" in argv and argv[-1] == "-"
        assert "shell_tool" in argv and "browser_use" in argv and "sandbox_mode='read-only'" in argv
        assert "web_search='disabled'" in argv and any(a.startswith("mcp_servers.jarvis.command=") for a in argv)
    finally:
        c.ponte.parar()


@pytest.mark.parametrize("item", [
    {"type": "command_execution", "command": "rm -rf /"},
    {"type": "file_change", "changes": [{"path": "x", "kind": "add"}]},
    {"type": "web_search", "query": "x"},
    {"type": "mcp_tool_call", "server": "github", "tool": "delete_repo"},
    {"type": "mcp_tool_call", "server": "jarvis", "tool": "ferramenta_inventada"},
])
def test_chatgpt_fail_closed(tmp_path, item):
    cmd = _cli_falsa(tmp_path, [{"type": "thread.started", "thread_id": "t"},
                                {"type": "item.started", "item": {"id": "1", **item}},
                                {"type": "item.completed", "item": {"id": "2", "type": "agent_message", "text": "Feito."}},
                                {"type": "turn.completed"}])
    c = outros_cli.CerebroCodexCLI(CFG, _Executor(), None, comando=cmd)
    try:
        with pytest.raises(ErroCerebro, match="isolamento"):
            list(c.responder("faz algo"))
        assert c.sessao_cli is None
    finally:
        c.ponte.parar()


def test_chatgpt_turno_falho_vira_erro(tmp_path):
    cmd = _cli_falsa(tmp_path, [{"type": "thread.started", "thread_id": "t"},
                                {"type": "turn.failed", "error": {"message": "usage limit reached"}}], codigo=1)
    c = outros_cli.CerebroCodexCLI(CFG, _Executor(), None, comando=cmd)
    try:
        with pytest.raises(ErroCerebro, match="usage limit"):
            list(c.responder("oi"))
    finally:
        c.ponte.parar()


def test_gemini_fala_em_pedacos_e_trava_ferramenta_de_fora(tmp_path):
    ok = _cli_falsa(tmp_path, [
        {"type": "init", "session_id": "s", "model": "gemini"},
        {"type": "tool_use", "tool_name": "mcp_jarvis_hora_data", "tool_id": "1", "parameters": {}},
        {"type": "message", "role": "assistant", "content": "São dez horas ", "delta": True},
        {"type": "message", "role": "assistant", "content": "da manhã, senhor.", "delta": True},
        {"type": "result", "status": "success", "stats": {}}], nome="ok")
    c = outros_cli.CerebroGeminiCLI(CFG, _Executor(), None, comando=ok)
    try:
        assert " ".join(c.responder("que horas são?")) == "São dez horas da manhã, senhor."
        conf = json.loads((c.pasta / "cwd" / ".gemini" / "settings.json").read_text(encoding="utf-8"))
        assert list(conf["mcpServers"]) == ["jarvis"]
        assert 'decision = "deny"' in (c.pasta / "politica.toml").read_text(encoding="utf-8")
        argv = c.argumentos(None)
        assert "--allowed-mcp-server-names" in argv and "--policy" in argv and "yolo" not in argv
    finally:
        c.ponte.parar()
    ruim = _cli_falsa(tmp_path, [{"type": "init", "session_id": "s"},
                                 {"type": "tool_use", "tool_name": "run_shell_command", "tool_id": "1"},
                                 {"type": "result", "status": "success"}], nome="ruim")
    c = outros_cli.CerebroGeminiCLI(CFG, _Executor(), None, comando=ruim)
    try:
        with pytest.raises(ErroCerebro, match="isolamento"):
            list(c.responder("apaga tudo"))
    finally:
        c.ponte.parar()


def test_cancelar_mata_o_processo(tmp_path):
    script = tmp_path / "lento.py"
    script.write_text("import sys, time, json\nsys.stdin.read()\nprint(json.dumps({'type':'thread.started','thread_id':'t'}), flush=True)\n"
                      "time.sleep(30)\n", encoding="utf-8")
    c = outros_cli.CerebroCodexCLI(CFG, _Executor(), None, comando=[PY, str(script)])
    try:
        import threading
        threading.Timer(0.8, c.cancelar).start()
        t = time.time()
        assert list(c.responder("oi")) == [] and time.time() - t < 10
    finally:
        c.ponte.parar()


# ------------------------------------------------------------------ login automático
def test_login_automatico_abre_e_confere_ate_logar(monkeypatch):
    abertos, respostas = [], iter([(False, "sem login"), (False, "sem login"), (True, "conta do ChatGPT")])
    monkeypatch.setattr(logins, "abrir_login", lambda cli, configurado=None: abertos.append(cli) or "Abri o login.")
    monkeypatch.setattr(logins, "verificar", lambda cli, configurado=None, usar_cache=True: next(respostas))
    monkeypatch.setattr(logins, "INTERVALO_S", 0.01)
    fim = []
    assert logins.login_automatico("codex", lambda ok, d: fim.append((ok, d))) == "Abri o login."
    assert logins.esperando("codex")
    assert logins.login_automatico("codex", None).startswith("Já estou esperando")   # não abre duas janelas
    t = time.time()
    while not fim and time.time() - t < 5:
        time.sleep(0.02)
    assert fim == [(True, "conta do ChatGPT")] and abertos == ["codex"] and not logins.esperando("codex")


def test_verificar_le_o_status_das_clis(monkeypatch, tmp_path):
    import subprocess as sp
    saidas = {"claude": '{"loggedIn": true, "authMethod": "claude.ai"}', "codex": "Not logged in"}
    monkeypatch.setattr(logins, "_comando", lambda cli, configurado=None: [cli])
    monkeypatch.setattr(sp, "run", lambda argv, **k: sp.CompletedProcess(argv, 0, saidas[argv[0]], ""))
    logins.esquecer_cache()
    assert logins.verificar("claude") == (True, "assinatura")
    assert logins.verificar("codex") == (False, "sem login")
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    assert logins.verificar("gemini")[0] is False
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "oauth_creds.json").write_text("{}")
    logins.esquecer_cache()
    assert logins.verificar("gemini")[0] is True
    monkeypatch.setattr(logins, "_comando", lambda cli, configurado=None: None)
    logins.esquecer_cache()
    assert logins.verificar("codex")[0] is None


# ------------------------------------------------------------------ automações desligadas
def test_automacoes_desligadas_nao_rodam_mas_lembretes_sim(tmp_path):
    from jarvis.connect import motor as m
    from jarvis.connect.banco import Banco
    falados = []
    b = Banco(tmp_path / "c.db")
    mo = m.Motor(b, {"anunciar": lambda t, f="": falados.append(t)}, {"connect": {"automacoes": False}},
                 relogio=lambda: datetime(2026, 10, 20, 10, 0))
    assert mo.publicar("whatsapp.mensagem", {"texto": "orçamento"}, "1") == 0
    assert not mo.precisa("gmail.novo")
    b.criar_tarefa("Ligar para o cliente", "", "alta", "2026-10-20T09:59")
    mo.tick()
    assert any("Ligar para o cliente" in f for f in falados)


def test_ferramentas_de_automacao_somem_do_cerebro():
    from jarvis.connect import ferramentas as f
    from jarvis.tools.base import REGISTRO
    guardadas = {n: REGISTRO[n] for n in f.FERRAMENTAS_DE_AUTOMACAO if n in REGISTRO}
    try:
        f.desligar_automacoes()
        assert not any(n in REGISTRO for n in f.FERRAMENTAS_DE_AUTOMACAO)
        assert "criar_tarefa" in REGISTRO and "abrir_central" in REGISTRO
    finally:
        REGISTRO.update(guardadas)
