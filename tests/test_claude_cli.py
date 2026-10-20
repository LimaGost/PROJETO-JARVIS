"""claude-cli: isolamento fail-closed, ponte HTTP/MCP, processo (com `claude` falso) e reserva local."""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jarvis.brain import claude_cli as modulo
from jarvis.brain.claude import CerebroComReserva, ErroCerebro, criar_cerebro
from jarvis.brain.claude_cli import (PREFIXO, CerebroClaudeCLI, PonteHTTP, dividir_frases, schema_para_mcp,
                                     validar_init)
from jarvis.config import RAIZ
from jarvis.hud.bus import bus
from jarvis.hud.controle import Controle
from jarvis.tools.base import Ferramenta, Nivel
from jarvis.tools.executor import NEGADO, Executor

FALSO = str(Path(__file__).with_name("fake_claude.py"))
CFG = {"nome": "Jarvis", "tratamento": "senhor",
       "llm": {"modelo": "qwen3:8b", "host": "http://127.0.0.1:1", "keep_alive": -1, "temperatura": 0.3, "max_turnos": 10},
       "claude_cli": {"modelo": "sonnet", "esforco": "low", "timeout_s": 5}}


def executor(tmp_path, registro_extra=None):
    chamadas = []
    registro = {
        "hora_data": Ferramenta("hora_data", "Hora atual.", lambda: chamadas.append("hora_data") or "13:32"),
        "somar": Ferramenta("somar", "Soma.", lambda a, b: chamadas.append(f"somar {a} {b}") or str(a + b),
                            {"a": {"type": "integer"}, "b": {"type": "integer"}}, ("a", "b")),
        "apagar": Ferramenta("apagar", "Apaga.", lambda: "apagado",
                             avaliar=lambda a: __import__("jarvis.tools.base", fromlist=["Avaliacao"]).Avaliacao(Nivel.CONFIRMAR, "apagar x")),
        **(registro_extra or {}),
    }
    ex = Executor(lambda r, **k: False, str(tmp_path), registro, avisar=lambda t: None)
    ex.chamadas = chamadas
    return ex


# ------------------------------------------------------------------ isolamento (fail-closed)
INIT_OK = {"tools": [f"{PREFIXO}hora_data", f"{PREFIXO}somar"], "mcp_servers": [{"name": "jarvis"}], "skills": [],
           "plugins": [{"name": "cc-plugin-agents-md", "path": "builtin"}]}


def test_init_confinado_e_aceito():
    assert validar_init(INIT_OK) is None
    assert validar_init({"tools": [], "mcp_servers": [], "skills": [], "plugins": []}) is None


@pytest.mark.parametrize("mudanca,trecho", [
    ({"tools": ["Bash"]}, "fora do JARVIS"),
    ({"tools": [f"{PREFIXO}hora_data", "Edit", "WebFetch"]}, "fora do JARVIS"),
    ({"tools": ["mcp__claude_ai_Gmail__send_message"]}, "fora do JARVIS"),      # conector seu: nunca
    ({"tools": ["mcp__supabase__execute_sql"]}, "fora do JARVIS"),
    ({"mcp_servers": [{"name": "jarvis"}, {"name": "claude.ai Gmail"}]}, "terceiros"),
    ({"mcp_servers": [{"name": "hostinger-vps"}]}, "terceiros"),
    ({"skills": ["deploy"]}, "skills"),
    ({"plugins": [{"name": "meu-plugin", "path": "C:\\x"}]}, "plugins externos"),
])
def test_qualquer_coisa_fora_do_jarvis_recusa(mudanca, trecho):
    motivo = validar_init({**INIT_OK, **mudanca})
    assert motivo and trecho in motivo


def test_ferramenta_com_prefixo_parecido_mas_diferente_e_recusada():
    assert validar_init({**INIT_OK, "tools": ["mcp__jarvis2__x"]})        # 'jarvis2' não é 'jarvis'
    assert validar_init({**INIT_OK, "tools": ["mcp_jarvis_x"]})


# ------------------------------------------------------------------ utilidades
def test_dividir_frases():
    assert dividir_frases("Olá, senhor. Tudo bem? Sim — claro!") == ["Olá, senhor.", "Tudo bem?", "Sim, claro!"]
    assert dividir_frases("Sem ponto final") == ["Sem ponto final"]
    assert dividir_frases("1.023 reais.") == ["1.023 reais."]


def test_schema_para_mcp():
    s = schema_para_mcp([{"type": "function", "function": {"name": "x", "description": "d", "parameters": {}}}])
    assert s == [{"name": "x", "description": "d", "inputSchema": {"type": "object", "properties": {}}}]


# ------------------------------------------------------------------ ponte HTTP
@pytest.fixture
def ponte(tmp_path):
    p = PonteHTTP(executor(tmp_path))
    yield p
    p.parar()


def http(p, caminho, corpo=None, token=True, host=None):
    cab = {"X-Jarvis-Token": p.token if token is True else (token or ""), "Content-Type": "application/json"}
    if host:
        cab["Host"] = host
    req = urllib.request.Request(p.url + caminho, data=json.dumps(corpo).encode() if corpo is not None else None,
                                 headers=cab, method="POST" if corpo is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_ponte_exige_token_e_host(ponte):
    assert http(ponte, "/ferramentas")[0] == 200
    assert http(ponte, "/ferramentas", token="errado")[0] == 403
    assert http(ponte, "/ferramentas", token=None)[0] == 403
    assert http(ponte, "/executar", {"nome": "hora_data", "args": {}}, token="errado")[0] == 403
    assert http(ponte, "/ferramentas", host="atacante.com")[0] == 403
    assert http(ponte, "/ferramentas", host=f"localhost:{ponte.porta}")[0] == 403   # só o endereço exato
    assert ponte.executor.chamadas == []                                               # nada rodou sem autorização


def test_ponte_lista_e_executa(ponte):
    status, corpo = http(ponte, "/ferramentas")
    assert {f["name"] for f in corpo["ferramentas"]} == {"hora_data", "somar", "apagar"}
    assert http(ponte, "/executar", {"nome": "somar", "args": {"a": "2", "b": 3}}) == (200, {"texto": "5", "erro": False})
    assert ponte.executor.chamadas == ["somar 2 3"]


def test_ponte_passa_pelo_executor_com_permissoes(ponte):
    status, corpo = http(ponte, "/executar", {"nome": "apagar", "args": {}})
    assert corpo["texto"] == NEGADO and corpo["erro"] is True                          # 🔴 negado, não executou
    status, corpo = http(ponte, "/executar", {"nome": "nao_existe", "args": {}})
    assert corpo["erro"] is True and "não existe" in corpo["texto"]


@pytest.mark.parametrize("corpo", [{"args": {}}, {"nome": 5, "args": {}}, {"nome": "x", "args": [1]}, "texto solto"])
def test_ponte_recusa_pedido_mal_formado(ponte, corpo):
    cab = {"X-Jarvis-Token": ponte.token, "Content-Type": "application/json"}
    req = urllib.request.Request(ponte.url + "/executar", data=json.dumps(corpo).encode(), headers=cab, method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 400


def test_ponte_serializa_chamadas_paralelas(tmp_path):
    ordem = []

    def lenta(n):
        ordem.append(f"ini {n}")
        time.sleep(0.25)
        ordem.append(f"fim {n}")
        return "ok"
    ex = executor(tmp_path, {"lenta": Ferramenta("lenta", "x", lenta, {"n": {"type": "integer"}}, ("n",))})
    p = PonteHTTP(ex)
    try:
        ts = [threading.Thread(target=http, args=(p, "/executar", {"nome": "lenta", "args": {"n": i}})) for i in (1, 2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    finally:
        p.parar()
    assert ordem[0].startswith("ini") and ordem[1].startswith("fim") and ordem[2].startswith("ini")   # nunca intercaladas


# ------------------------------------------------------------------ ponte MCP (processo real, protocolo stdio)
def conversar_mcp(ponte, mensagens, token=None):
    env = {**os.environ, "JARVIS_PONTE_URL": ponte.url, "JARVIS_PONTE_TOKEN": token or ponte.token, "PYTHONUTF8": "1"}
    entrada = "\n".join(json.dumps(m) for m in mensagens) + "\n"
    r = subprocess.run([sys.executable, "-X", "utf8", str(RAIZ / "jarvis" / "brain" / "ponte_mcp.py")], input=entrada,
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
    return [json.loads(l) for l in r.stdout.splitlines() if l.strip()], r.stderr


def test_ponte_mcp_protocolo_completo(ponte):
    respostas, _ = conversar_mcp(ponte, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                                       "clientInfo": {"name": "claude", "version": "x"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "somar", "arguments": {"a": 4, "b": 5}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "apagar", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        {"jsonrpc": "2.0", "id": 6, "method": "metodo/que/nao/existe"},
    ])
    por_id = {r["id"]: r for r in respostas}
    assert len(respostas) == 6                                                    # a notificação não ganha resposta
    assert por_id[1]["result"]["protocolVersion"] == "2025-03-26" and por_id[1]["result"]["serverInfo"]["name"] == "jarvis"
    assert {t["name"] for t in por_id[2]["result"]["tools"]} == {"hora_data", "somar", "apagar"}
    assert all("inputSchema" in t for t in por_id[2]["result"]["tools"])
    assert por_id[3]["result"] == {"content": [{"type": "text", "text": "9"}], "isError": False}
    assert por_id[4]["result"]["isError"] is True and "NÃO autorizou" in por_id[4]["result"]["content"][0]["text"]
    assert por_id[5]["result"] == {}
    assert por_id[6]["error"]["code"] == -32601


def test_ponte_mcp_com_token_errado_nao_executa_nada(ponte):
    respostas, _ = conversar_mcp(ponte, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "somar", "arguments": {"a": 1, "b": 1}}}],
        token="token-errado")
    assert respostas[0]["result"]["isError"] is True and "recusou" in respostas[0]["result"]["content"][0]["text"]
    assert ponte.executor.chamadas == []


def test_ponte_mcp_aguenta_lixo_na_entrada(ponte):
    env = {**os.environ, "JARVIS_PONTE_URL": ponte.url, "JARVIS_PONTE_TOKEN": ponte.token}
    r = subprocess.run([sys.executable, str(RAIZ / "jarvis" / "brain" / "ponte_mcp.py")],
                       input='isto não é json\n\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n', capture_output=True, text=True,
                       env=env, timeout=30)
    assert json.loads(r.stdout.splitlines()[0]) == {"jsonrpc": "2.0", "id": 9, "result": {}}   # seguiu depois do lixo


# ------------------------------------------------------------------ cérebro com `claude` falso
@pytest.fixture
def fabrica(tmp_path, monkeypatch):
    criados = []

    def criar(modo="ok", **cfg_extra):
        monkeypatch.setenv("FAKE_INIT", modo)
        cfg = {**CFG, "claude_cli": {**CFG["claude_cli"], **cfg_extra}}
        c = CerebroClaudeCLI(cfg, executor(tmp_path), None, executavel=sys.executable)
        monkeypatch.setattr(c, "_comando", lambda: [sys.executable, "-u", FALSO])
        criados.append(c)
        return c
    yield criar
    for c in criados:
        c.encerrar()
        c.ponte.parar()


def test_aquecer_valida_isolamento_e_responder_fala_por_frase(fabrica):
    c = fabrica()
    c.aquecer()
    assert c.init["tools"] == ["mcp__jarvis__hora_data"] and c._vivo()
    usadas = []
    c._registrar = lambda texto, falado, ferramentas: usadas.extend(ferramentas)
    falas = list(c.responder("que horas são?"))
    assert falas[:2] == ["Olá, senhor.", "Tudo bem por aqui."]
    todo = " ".join(falas)
    assert "[Agora" in todo and "que horas" in todo          # a data (e a memória, quando há) seguem junto do pedido
    assert usadas == ["hora_data"]                                                       # prefixo do MCP removido


@pytest.mark.parametrize("modo,trecho", [("ruim_bash", "fora do JARVIS"), ("ruim_mcp", "terceiros"), ("ruim_skill", "skills")])
def test_isolamento_quebrado_recusa_e_mata_o_processo(fabrica, modo, trecho):
    c = fabrica(modo)
    with pytest.raises(ErroCerebro, match="isolamento") as e:
        c.aquecer()
    assert trecho in str(e.value) and not c._vivo()           # fail-closed: nada de usar um claude com ferramentas soltas


def test_responder_tambem_confere_o_isolamento(fabrica):
    c = fabrica("ruim_bash")
    with pytest.raises(ErroCerebro, match="isolamento"):
        list(c.responder("oi"))
    assert not c._vivo()


def test_erro_da_api_vira_erro_do_cerebro(fabrica):
    c = fabrica()
    c.aquecer()
    with pytest.raises(ErroCerebro, match="erro do claude"):
        list(c.responder("ERRO_API"))


def test_max_turnos_vira_frase_e_nao_erro(fabrica):
    c = fabrica()
    c.aquecer()
    assert list(c.responder("MAXTURNOS")) == ["Não consegui concluir isso, senhor."]


def test_resposta_sem_pedacos_parciais_ainda_e_falada(fabrica):
    c = fabrica()
    c.aquecer()
    assert list(c.responder("SEM_STREAM")) == ["Sem pedaços parciais.", "Chegou inteira, senhor."]


def test_processo_que_morre_no_meio_vira_erro(fabrica):
    c = fabrica()
    c.aquecer()
    with pytest.raises(ErroCerebro, match="encerrou"):
        list(c.responder("MORRER"))
    assert not c._vivo()


def test_timeout_mata_o_processo_e_avisa(fabrica):
    c = fabrica(timeout_s=1)
    c.aquecer()
    t = time.time()
    with pytest.raises(ErroCerebro, match="sem responder"):
        list(c.responder("DEMORA"))
    assert time.time() - t < 6 and not c._vivo()


def test_processo_caido_sobe_de_novo_no_proximo_pedido(fabrica):
    c = fabrica()
    c.aquecer()
    with pytest.raises(ErroCerebro):
        list(c.responder("MORRER"))
    assert list(c.responder("de volta?"))[0] == "Olá, senhor."      # reiniciou sozinho (conversa nova)


def test_cancelar_interrompe_so_o_pedido_e_a_conversa_continua(fabrica):
    c = fabrica()
    c.aquecer()
    pid_antigo = c.proc.pid
    falas = []
    for frase in c.responder("LENTO"):
        falas.append(frase)
        c.cancelar()                                            # 'parar tudo' / falou por cima no meio da resposta
    assert falas == ["Primeira frase."] and c._vivo() and c._drenar  # o processo (e a conversa) continuam
    seguinte = list(c.responder("outro pedido"))
    assert seguinte[0] == "Olá, senhor." and c.proc.pid == pid_antigo  # o resto do pedido interrompido foi descartado
    assert "Segunda" not in " ".join(seguinte)


def test_processo_novo_recebe_a_conversa_recente(fabrica, tmp_path):
    class Mem:
        marco_conversa = None

        def turnos_recentes(self, n, horas=12, desde=None):
            return [("2026-10-17T20:00:00", "Meu número da sorte é 47", "Anotado, senhor.")]

        def contexto(self, texto):
            return ""
    c = fabrica()
    c.memoria = Mem()
    primeira = " ".join(c.responder("qual é o meu número?"))
    assert "Conversa recente" in primeira and "47" in primeira          # continua de onde parou, como um só
    assert "Conversa recente" not in " ".join(c.responder("e agora?"))  # só no primeiro pedido do processo
    c.esquecer()
    assert c.memoria.marco_conversa is not None                          # 'esqueça a conversa' corta a continuidade


def test_uso_de_cada_pedido_fica_registrado(fabrica):
    c = fabrica()
    c.aquecer()
    list(c.responder("oi"))
    linhas = [json.loads(l) for l in c.arquivo_uso.read_text(encoding="utf-8").splitlines()]
    assert [l["tipo"] for l in linhas] == ["partida", "pedido"] and all("custo_api_usd" in l for l in linhas)


def test_esquecer_encerra_a_conversa(fabrica):
    c = fabrica()
    c.aquecer()
    c.executor._contaminado_ate = 99
    c.esquecer()
    assert not c._vivo() and not c.executor.contaminado


# ------------------------------------------------------------------ linha de comando real
def test_comando_de_producao_tem_todas_as_travas(tmp_path):
    c = CerebroClaudeCLI(CFG, executor(tmp_path), None, executavel="claude")
    try:
        cmd = c._comando()
        for obrigatoria in ("--restricted", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
            assert obrigatoria in cmd
        assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"               # nativas: só busca/leitura web
        assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
        assert cmd[cmd.index("--allowedTools") + 1] == "mcp__jarvis,WebSearch,WebFetch"
        assert cmd[cmd.index("--model") + 1] == "sonnet" and cmd[cmd.index("--effort") + 1] == "low"
        for proibida in ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--bare",
                         "bypassPermissions", "--add-dir", "--chrome", "--ide", "--plugin-dir", "--plugin-url"):
            assert proibida not in cmd
        mcp = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]
        assert list(mcp) == ["jarvis"]                                             # só a ponte
        assert mcp["jarvis"]["env"]["JARVIS_PONTE_URL"].startswith("http://127.0.0.1:")
        assert len(mcp["jarvis"]["env"]["JARVIS_PONTE_TOKEN"]) >= 24
        sistema = Path(cmd[cmd.index("--system-prompt-file") + 1]).read_text(encoding="utf-8")
        assert "mcp__jarvis__" in sistema and "Jarvis" in sistema
        assert Path(c.pasta / "cwd").is_dir() and not any((c.pasta / "cwd").iterdir())   # pasta de trabalho vazia
    finally:
        c.ponte.parar()


def test_sem_web_nativa_nenhuma_ferramenta_nativa(tmp_path):
    c = CerebroClaudeCLI({**CFG, "claude_cli": {**CFG.get("claude_cli", {}), "web_nativa": False}}, executor(tmp_path), None,
                         executavel="claude")
    try:
        cmd = c._comando()
        assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--allowedTools") + 1] == "mcp__jarvis"
    finally:
        c.ponte.parar()


def test_init_aceita_so_a_web_nativa_declarada():
    assert validar_init({**INIT_OK, "tools": ["mcp__jarvis__hora_data", "WebSearch", "WebFetch"]},
                        ("WebSearch", "WebFetch")) is None
    assert validar_init({**INIT_OK, "tools": ["mcp__jarvis__hora_data", "WebSearch"]})            # sem permitir: recusa
    assert validar_init({**INIT_OK, "tools": ["WebSearch", "Bash"]}, ("WebSearch", "WebFetch"))    # Bash nunca


def test_assinatura_nao_chave_de_api_no_ambiente_do_processo(fabrica, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-nao-deve-passar")
    c = fabrica()
    c.aquecer()
    assert c.init["env_chave"] is False


# ------------------------------------------------------------------ reserva local e fábrica
class Local:
    rotulo, memoria, executor, sessao, historico = "local", None, None, "s", []

    def __init__(self):
        self.pedidos = []

    def aquecer(self):
        pass

    def cancelar(self):
        pass

    def esquecer(self):
        pass

    def responder(self, t):
        self.pedidos.append(t)
        yield "Resposta local."


@pytest.mark.parametrize("modo", ["ruim_bash", "ruim_mcp"])
def test_isolamento_quebrado_cai_no_modelo_local(fabrica, modo):
    local = Local()
    r = CerebroComReserva(fabrica(modo), local)
    assert list(r.responder("oi")) == ["Resposta local."] and local.pedidos == ["oi"]


def test_erro_do_claude_antes_de_falar_cai_no_local(fabrica):
    local = Local()
    r = CerebroComReserva(fabrica(), local)
    assert list(r.responder("ERRO_API")) == ["Resposta local."]


def test_fabrica_sem_claude_instalado_cai_no_local(monkeypatch):
    monkeypatch.setattr(modulo.shutil, "which", lambda n: None)
    c = criar_cerebro({**CFG, "cerebro": {"motor": "claude-cli"}}, executor(Path(os.environ["TEMP"]) / "x_cli"), None)
    assert type(c).__name__ == "Cerebro"


def test_fabrica_monta_claude_cli_com_reserva(monkeypatch, tmp_path):
    monkeypatch.setattr(modulo.shutil, "which", lambda n: "claude")
    c = criar_cerebro({**CFG, "cerebro": {"motor": "claude-cli"}}, executor(tmp_path), None)
    try:
        assert type(c).__name__ == "CerebroComReserva" and c.rotulo == "claude · sonnet"
    finally:
        c.principal.ponte.parar()


def test_cerebro_cli_sem_executor_e_recusado():
    with pytest.raises(RuntimeError, match="Executor"):
        CerebroClaudeCLI(CFG, None, None, executavel="claude")


# ------------------------------------------------------------------ origem do pedido chega às ferramentas (outra thread)
class CerebroQueChamaDeOutraThread:
    """Imita o claude-cli: a ferramenta 🔴 é chamada por uma thread da ponte, não pela do pedido."""
    def __init__(self, executor):
        self.executor, self.resultado = executor, None

    def responder(self, texto):
        t = threading.Thread(target=lambda: setattr(self, "resultado", self.executor.executar("apagar", {})))
        t.start()
        t.join(10)
        yield "ok."

    def cancelar(self):
        pass

    def esquecer(self):
        pass


class MicQueNaoPodeSerUsado:
    silenciado, encerrar, nivel = False, threading.Event(), 0.0

    def __init__(self):
        self.usado = False

    def gravar_fala(self, *a, **k):
        self.usado = True
        return None


@pytest.fixture(autouse=True)
def bus_limpo():
    bus.limpar()
    yield
    bus.limpar()


def test_pedido_da_hud_chamando_ferramenta_de_outra_thread_nao_abre_o_microfone(tmp_path):
    class Voz:
        def falar(self, t): pass
        def esperar(self): pass
        def calar(self): pass
        def nivel(self): return 0.0
    mic = MicQueNaoPodeSerUsado()
    ex = executor(tmp_path)
    ctl = Controle({"nome": "J", "tratamento": "senhor", "llm": {"modelo": "x"}, "vad": {"espera_inicio_s": 1}},
                   None, voz=Voz(), mic=mic, ouvido=object())
    ex.confirmar = ctl.confirmar
    ctl.cerebro = CerebroQueChamaDeOutraThread(ex)
    bus.conexoes = 1
    th = threading.Thread(target=ctl.texto_do_hud, args=("apaga tudo",))      # pedido digitado na HUD
    th.start()
    t = time.time()
    while ctl._pendente is None and time.time() - t < 5:
        time.sleep(0.02)
    assert ctl._pendente is not None and ctl._origem_turno == "hud"
    time.sleep(0.3)
    assert mic.usado is False                                                  # "sim" falado perto do mic não vale
    ctl.responder_confirmacao(ctl._pendente["id"], False, "hud")
    th.join(10)
    assert ctl.cerebro.resultado == NEGADO and mic.usado is False
