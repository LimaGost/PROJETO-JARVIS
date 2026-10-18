"""Cérebro Claude: lógica (cliente falso) e compatibilidade com o SDK real (servidor local falso, sem chave)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace as NS

import pytest

from jarvis.brain import claude as modulo
from jarvis.brain.claude import (BETA_VINCULO, CerebroClaude, CerebroComReserva, carregar_chave, criar_cerebro,
                                 schema_para_claude)
from jarvis.tools.base import Ferramenta, Nivel
from jarvis.tools.executor import Executor

CFG = {"nome": "Jarvis", "tratamento": "senhor",
       "llm": {"modelo": "qwen3:8b", "host": "http://127.0.0.1:1", "keep_alive": -1, "temperatura": 0.3, "max_turnos": 10},
       "claude": {"modelo": "claude-opus-5-5", "esforco": "low", "max_turnos": 3}}


# ------------------------------------------------------------------ ferramentas e executor de teste
def executor_falso(tmp_path, chamadas):
    registro = {
        "hora_data": Ferramenta("hora_data", "Hora atual.", lambda: chamadas.append("hora_data") or "13:32"),
        "volume": Ferramenta("volume", "Volume.", lambda valor: chamadas.append(f"volume {valor}") or f"Volume em {valor}%.",
                             {"valor": {"type": "integer"}}, ("valor",), nivel_fixo=Nivel.LIVRE),
    }
    return Executor(lambda r, **k: False, str(tmp_path), registro, avisar=lambda t: None)


# ------------------------------------------------------------------ cliente falso
class Bloco(NS):
    pass


def texto(t):
    return Bloco(type="text", text=t)


def uso(**k):
    return NS(input_tokens=k.get("i", 100), output_tokens=k.get("o", 10), cache_read_input_tokens=k.get("cl", 0),
              cache_creation_input_tokens=k.get("ce", 0))


class FluxoFalso:
    def __init__(self, pedacos, final, ao_ler=None):
        self.pedacos, self.final, self.ao_ler, self.fechou = pedacos, final, ao_ler, False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.fechou = True
        return False

    @property
    def text_stream(self):
        for i, p in enumerate(self.pedacos):
            if self.ao_ler:
                self.ao_ler(i)
            yield p

    def get_final_message(self):
        return self.final


class ClienteFalso:
    """Devolve um fluxo por chamada, na ordem; registra os parâmetros recebidos."""
    def __init__(self, fluxos):
        self.fluxos, self.chamadas = list(fluxos), []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kw):
        # o SDK real serializa a requisição na hora; o falso copia a lista (o cérebro continua alterando-a depois)
        self.chamadas.append({**kw, "messages": list(kw["messages"])})
        f = self.fluxos.pop(0)
        if isinstance(f, Exception):
            raise f
        return f


def msg_final(blocos, parada="end_turn", u=None):
    return NS(content=blocos, stop_reason=parada, usage=u or uso())


def cerebro(tmp_path, fluxos, chamadas=None, cfg=CFG):
    chamadas = [] if chamadas is None else chamadas
    c = CerebroClaude(cfg, executor_falso(tmp_path, chamadas), None, cliente=ClienteFalso(fluxos))
    c.pasta_uso = tmp_path
    return c


# ------------------------------------------------------------------ conversão e chave
def test_schema_para_claude():
    ex = [{"type": "function", "function": {"name": "volume", "description": "d",
                                            "parameters": {"type": "object", "properties": {"v": {"type": "integer"}}, "required": ["v"]}}},
          {"type": "function", "function": {"name": "hora", "description": "h", "parameters": {}}}]
    s = schema_para_claude(ex)
    assert s[0] == {"name": "volume", "description": "d",
                    "input_schema": {"type": "object", "properties": {"v": {"type": "integer"}}, "required": ["v"]}}
    assert s[1]["input_schema"] == {"type": "object", "properties": {}}


def test_chave_vem_do_ambiente_ou_do_arquivo_e_nunca_do_config(monkeypatch, tmp_path):
    arq = tmp_path / "anthropic.key"
    monkeypatch.setattr(modulo, "ARQUIVO_CHAVE", arq)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert carregar_chave() is None
    arq.write_text("  sk-ant-do-arquivo \n", encoding="utf-8")
    assert carregar_chave() == "sk-ant-do-arquivo"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-do-ambiente")
    assert carregar_chave() == "sk-ant-do-ambiente"


@pytest.mark.parametrize("modelo", ["claude-opus-4-8", "claude-sonnet-4-6", "gpt-4", "qwen3:8b", "claude-haiku-4-5"])
def test_modelo_fora_da_familia_5_e_recusado(tmp_path, modelo):
    with pytest.raises(ValueError, match="família 5"):
        CerebroClaude({**CFG, "claude": {"modelo": modelo}}, None, None, cliente=ClienteFalso([]))


# ------------------------------------------------------------------ parâmetros da requisição
def test_parametros_seguem_a_documentacao(tmp_path):
    c = cerebro(tmp_path, [FluxoFalso(["Pronto, senhor."], msg_final([texto("Pronto, senhor.")]))])
    list(c.responder("oi"))
    p = c.claude.chamadas[0]
    assert p["model"] == "claude-opus-5-5" and p["betas"] == [BETA_VINCULO]
    assert p["thinking"] == {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert p["output_config"] == {"effort": "low"}
    assert p["cache_control"] == {"type": "ephemeral"}
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"} and p["system"][0]["type"] == "text"
    assert {t["name"] for t in p["tools"]} == {"hora_data", "volume"}
    for proibido in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert proibido not in p
    assert "tool_choice" not in p                      # nunca forçado
    assert "budget_tokens" not in json.dumps(p["thinking"])


def test_ferramentas_e_sistema_identicos_em_todas_as_chamadas(tmp_path):
    cham = []
    c = cerebro(tmp_path, [
        FluxoFalso([], msg_final([Bloco(type="tool_use", id="t1", name="hora_data", input={})], "tool_use")),
        FluxoFalso(["São 13h32."], msg_final([texto("São 13h32.")])),
        FluxoFalso(["De nada."], msg_final([texto("De nada.")])),
    ], cham)
    list(c.responder("que horas são?"))
    list(c.responder("obrigado"))
    a, b, d = c.claude.chamadas
    assert json.dumps(a["tools"]) == json.dumps(b["tools"]) == json.dumps(d["tools"])    # senão o cache invalida
    assert json.dumps(a["system"]) == json.dumps(b["system"]) == json.dumps(d["system"])


# ------------------------------------------------------------------ laço de ferramentas
def test_fala_por_frase_executa_ferramenta_e_devolve_resultado_numa_mensagem(tmp_path):
    cham = []
    c = cerebro(tmp_path, [
        FluxoFalso(["Vou ver. "], msg_final([texto("Vou ver. "), Bloco(type="tool_use", id="t1", name="volume", input={"valor": "30"}),
                                             Bloco(type="tool_use", id="t2", name="hora_data", input={})], "tool_use")),
        FluxoFalso(["Volume em ", "trinta. ", "São 13h32."], msg_final([texto("Volume em trinta. São 13h32.")])),
    ], cham)
    falas = list(c.responder("ajusta o volume e diz a hora"))
    assert falas == ["Vou ver.", "Volume em trinta.", "São 13h32."]
    assert cham == ["volume 30", "hora_data"]                       # '30' (texto) foi corrigido para inteiro pelo Executor
    ultima = c.claude.chamadas[1]["messages"]
    assert ultima[-1]["role"] == "user" and [r["type"] for r in ultima[-1]["content"]] == ["tool_result", "tool_result"]
    assert [r["tool_use_id"] for r in ultima[-1]["content"]] == ["t1", "t2"]   # todos os resultados na MESMA mensagem
    assert ultima[-2]["role"] == "assistant"


def test_resultado_de_erro_e_marcado(tmp_path):
    c = cerebro(tmp_path, [
        FluxoFalso([], msg_final([Bloco(type="tool_use", id="t1", name="inexistente", input={})], "tool_use")),
        FluxoFalso(["Não deu."], msg_final([texto("Não deu.")])),
    ])
    list(c.responder("faz algo"))
    resultado = c.claude.chamadas[1]["messages"][-1]["content"][0]
    assert resultado["is_error"] is True and "não existe" in resultado["content"]


def test_contexto_e_data_ficam_na_mensagem_para_sempre(tmp_path):
    c = cerebro(tmp_path, [FluxoFalso(["a"], msg_final([texto("a.")])), FluxoFalso(["b"], msg_final([texto("b.")]))])
    list(c.responder("primeiro"))
    primeira = c.hist[0]["content"]
    list(c.responder("segundo"))
    assert c.hist[0]["content"] == primeira and "[Agora:" in primeira and primeira.endswith("primeiro")  # histórico só cresce


def test_ultima_rodada_proibe_ferramentas_sem_forcar(tmp_path):
    tool = lambda: FluxoFalso([], msg_final([Bloco(type="tool_use", id="t", name="hora_data", input={})], "tool_use"))
    c = cerebro(tmp_path, [tool(), tool().__class__(["Chega."], msg_final([texto("Chega.")]))])
    c.max_rodadas = 1
    list(c.responder("loop"))
    assert "tool_choice" not in c.claude.chamadas[0] and c.claude.chamadas[1]["tool_choice"] == {"type": "none"}


def test_recusa_e_falada_sem_travar(tmp_path):
    c = cerebro(tmp_path, [FluxoFalso([], msg_final([], "refusal"))])
    assert list(c.responder("algo")) == [modulo.RECUSA]


# ------------------------------------------------------------------ cancelar, erros, histórico
def test_cancelar_fecha_o_fluxo_e_desfaz_o_pedido(tmp_path):
    antes = [{"role": "user", "content": "antigo"}, {"role": "assistant", "content": [texto("resp")]}]
    fluxo = FluxoFalso(["Uma história. ", "longa. ", "muito longa. "], msg_final([texto("x")]))
    c = cerebro(tmp_path, [fluxo])
    fluxo.ao_ler = lambda i: c.cancelar() if i == 1 else None
    c.hist = list(antes)
    falas = list(c.responder("conta uma história"))
    assert fluxo.fechou and len(falas) < 3 and c.hist == antes


def test_cancelamento_nao_vaza_para_o_proximo_pedido(tmp_path):
    c = cerebro(tmp_path, [FluxoFalso(["Olá. "], msg_final([texto("Olá.")]))])
    c.cancelar()
    assert list(c.responder("oi")) == ["Olá."]


def test_erro_da_api_nao_deixa_tool_use_orfao_no_historico(tmp_path):
    c = cerebro(tmp_path, [
        FluxoFalso([], msg_final([Bloco(type="tool_use", id="t1", name="hora_data", input={})], "tool_use")),
        RuntimeError("caiu no meio"),
    ])
    c.hist = [{"role": "user", "content": "antigo"}, {"role": "assistant", "content": [texto("ok")]}]
    with pytest.raises(RuntimeError):
        list(c.responder("que horas?"))
    assert len(c.hist) == 2                                   # voltou ao estado de antes do pedido


def test_aparar_corta_so_no_inicio_de_um_pedido(tmp_path):
    fluxos = [FluxoFalso([f"r{i}. "], msg_final([texto(f"r{i}.")])) for i in range(6)]
    c = cerebro(tmp_path, fluxos)                              # max_turnos = 3
    for i in range(6):
        list(c.responder(f"pedido {i}"))
    reais = [m for m in c.hist if modulo._turno_real(m)]
    assert len(reais) == 3 and c.hist[0]["role"] == "user" and isinstance(c.hist[0]["content"], str)


def test_aparar_nao_corta_no_meio_de_uma_chamada_de_ferramenta(tmp_path):
    cham = []
    fluxos = [FluxoFalso([], msg_final([Bloco(type="tool_use", id=f"t{i}", name="hora_data", input={})], "tool_use")) if j == 0
              else FluxoFalso(["ok. "], msg_final([texto("ok.")])) for i in range(5) for j in (0, 1)]
    c = cerebro(tmp_path, fluxos, cham)
    for i in range(5):
        list(c.responder(f"p{i}"))
    ids_uso = {b.id for m in c.hist if m["role"] == "assistant" for b in m["content"] if getattr(b, "type", "") == "tool_use"}
    ids_res = {r["tool_use_id"] for m in c.hist if m["role"] == "user" and isinstance(m["content"], list) for r in m["content"]}
    assert ids_uso == ids_res                                  # nenhum tool_use sem resultado e vice-versa


def test_uso_e_registrado_para_acompanhar_o_custo(tmp_path):
    c = cerebro(tmp_path, [FluxoFalso(["a. "], msg_final([texto("a.")], u=uso(i=50, o=7, cl=4000, ce=300)))])
    list(c.responder("oi"))
    linha = json.loads((tmp_path / "uso_claude.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert (linha["entrada"], linha["saida"], linha["cache_lido"], linha["cache_escrito"]) == (50, 7, 4000, 300)
    assert c.uso_total["cache_lido"] == 4000 and c.uso_total["pedidos"] == 1


def test_esquecer_zera_tudo_e_a_contaminacao(tmp_path):
    c = cerebro(tmp_path, [FluxoFalso(["a. "], msg_final([texto("a.")]))])
    list(c.responder("oi"))
    c.executor._contaminado_ate = 99
    c.esquecer()
    assert c.hist == [] and not c.executor.contaminado


# ------------------------------------------------------------------ reserva local
class Local:
    def __init__(self, fala="Resposta local."):
        self.fala, self.pedidos, self.aquecido = fala, [], False
        self.memoria, self.executor, self.sessao, self.historico = None, None, "s", []

    def aquecer(self):
        self.aquecido = True

    def cancelar(self):
        pass

    def esquecer(self):
        pass

    def responder(self, t):
        self.pedidos.append(t)
        yield self.fala


class Principal:
    rotulo, memoria, executor, sessao, historico = "claude-opus-5-5", None, None, "s", []

    def __init__(self, erro=None, falas=("Oi.",), falha_aquecer=False):
        self.erro, self.falas, self.falha_aquecer = erro, falas, falha_aquecer

    def aquecer(self):
        if self.falha_aquecer:
            raise self.erro

    def cancelar(self):
        pass

    def esquecer(self):
        pass

    def responder(self, t):
        if self.erro and not self.falha_aquecer:
            raise self.erro
        yield from self.falas


def erro_api(cls):
    import anthropic
    import httpx2 as httpx
    req = httpx.Request("POST", "http://x")
    if cls is anthropic.APIConnectionError:
        return cls(request=req)
    return cls(message="x", response=httpx.Response(401 if cls is anthropic.AuthenticationError else 429 if cls is anthropic.RateLimitError else 500, request=req), body=None)


@pytest.mark.parametrize("nome", ["APIConnectionError", "AuthenticationError", "RateLimitError", "InternalServerError"])
def test_falha_do_claude_antes_de_falar_vai_para_o_local(nome):
    import anthropic
    local = Local()
    r = CerebroComReserva(Principal(erro=erro_api(getattr(anthropic, nome))), local)
    assert list(r.responder("oi")) == ["Resposta local."] and local.pedidos == ["oi"]


def test_erro_de_pedido_ruim_nao_vai_para_o_local():
    import anthropic
    local = Local()
    r = CerebroComReserva(Principal(erro=ValueError("bug meu")), local)
    with pytest.raises(ValueError):
        list(r.responder("oi"))
    assert local.pedidos == []


def test_depois_de_falar_o_erro_nao_recomeca_em_outro_modelo():
    import anthropic

    class Cai(Principal):
        def responder(self, t):
            yield "Começando."
            raise erro_api(anthropic.APIConnectionError)
    local = Local()
    r = CerebroComReserva(Cai(), local)
    saida = []
    with pytest.raises(anthropic.APIConnectionError):
        for f in r.responder("oi"):
            saida.append(f)
    assert saida == ["Começando."] and local.pedidos == []      # não fala duas respostas diferentes


def test_aquecer_com_chave_ruim_comeca_no_local_e_prepara_o_local():
    import anthropic
    local = Local()
    r = CerebroComReserva(Principal(erro=erro_api(anthropic.AuthenticationError), falha_aquecer=True), local)
    r.aquecer()
    assert r.usando_reserva and local.aquecido
    assert list(r.responder("oi")) == ["Resposta local."]


def test_aquecer_com_claude_ok_nao_carrega_o_local():
    local = Local()
    r = CerebroComReserva(Principal(), local)
    r.aquecer()
    assert not r.usando_reserva and not local.aquecido        # Qwen parado na GPU + voz clonada não cabem em 8 GB


def test_fabrica_com_claude_marca_o_local_como_so_reserva(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-teste")
    c = criar_cerebro({**CFG, "cerebro": {"motor": "claude"}}, None, None)
    assert c.reserva.so_reserva and c.reserva.keep_alive == "2m"


def test_fabrica_sem_chave_cai_no_local(monkeypatch, tmp_path):
    monkeypatch.setattr(modulo, "ARQUIVO_CHAVE", tmp_path / "nao_existe.key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = criar_cerebro({**CFG, "cerebro": {"motor": "claude"}}, None, None)
    assert type(c).__name__ == "Cerebro"


def test_fabrica_motor_ollama_e_o_padrao():
    assert type(criar_cerebro(CFG, None, None)).__name__ == "Cerebro"


def test_fabrica_com_chave_monta_claude_com_reserva(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teste")
    c = criar_cerebro({**CFG, "cerebro": {"motor": "claude"}}, None, None)
    assert type(c).__name__ == "CerebroComReserva" and c.rotulo == "claude-opus-5-5"
    c2 = criar_cerebro({**CFG, "cerebro": {"motor": "claude", "reserva_local": False}}, None, None)
    assert type(c2).__name__ == "CerebroClaude"


# ------------------------------------------------------------------ SDK REAL contra servidor local falso
def sse(evento, dados):
    return f"event: {evento}\ndata: {json.dumps(dados)}\n\n"


def resposta_texto(frases, parada="end_turn"):
    saida = [sse("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 1, "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 0}}}),
        sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})]
    for f in frases:
        saida.append(sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": f}}))
    saida += [sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
              sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": parada, "stop_sequence": None},
                                    "usage": {"output_tokens": 12}}),
              sse("message_stop", {"type": "message_stop"})]
    return "".join(saida)


def resposta_ferramenta(nome, entrada):
    return "".join([
        sse("message_start", {"type": "message_start", "message": {
            "id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 1}}}),
        sse("content_block_start", {"type": "content_block_start", "index": 0,
                                    "content_block": {"type": "tool_use", "id": "toolu_1", "name": nome, "input": {}}}),
        sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(entrada)}}),
        sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                              "usage": {"output_tokens": 20}}),
        sse("message_stop", {"type": "message_stop"})])


@pytest.fixture
def servidor_falso():
    respostas, requisicoes = [], []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requisicoes.append({"caminho": self.path, "cabecalhos": {k.lower(): v for k, v in self.headers.items()},
                                "corpo": corpo})
            dados = respostas.pop(0).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", respostas, requisicoes
    srv.shutdown()


def test_sdk_real_laco_completo_com_ferramenta(servidor_falso, tmp_path):
    import anthropic
    url, respostas, reqs = servidor_falso
    respostas += [resposta_ferramenta("hora_data", {}), resposta_texto(["São treze ", "horas e trinta e dois minutos."])]
    cham = []
    cliente = anthropic.Anthropic(api_key="chave-de-teste", base_url=url, max_retries=0)
    c = CerebroClaude(CFG, executor_falso(tmp_path, cham), None, cliente=cliente)
    c.pasta_uso = tmp_path
    falas = list(c.responder("que horas são?"))
    assert falas == ["São treze horas e trinta e dois minutos."] and cham == ["hora_data"]

    primeira, segunda = reqs
    assert primeira["caminho"].startswith("/v1/messages")
    assert BETA_VINCULO in primeira["cabecalhos"].get("anthropic-beta", "")
    assert primeira["cabecalhos"]["x-api-key"] == "chave-de-teste"
    corpo = primeira["corpo"]
    assert corpo["model"] == "claude-opus-5-5" and corpo["stream"] is True
    assert corpo["thinking"] == {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert corpo["output_config"] == {"effort": "low"} and corpo["cache_control"] == {"type": "ephemeral"}
    assert corpo["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {t["name"] for t in corpo["tools"]} == {"hora_data", "volume"}
    assert "tool_choice" not in corpo and "temperature" not in corpo
    # segunda chamada: o tool_use do assistente + o resultado, na ordem certa
    msgs = segunda["corpo"]["messages"]
    assert msgs[-2]["role"] == "assistant" and msgs[-2]["content"][0]["type"] == "tool_use"
    assert msgs[-1]["content"][0] == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "13:32", "is_error": False}
    assert c.uso_total["cache_lido"] == 4000


def test_sdk_real_aquecer_valida_a_conexao(servidor_falso, tmp_path):
    import anthropic
    url, respostas, reqs = servidor_falso
    respostas.append(resposta_texto(["oi"]))
    c = CerebroClaude(CFG, executor_falso(tmp_path, []), None,
                      cliente=anthropic.Anthropic(api_key="k", base_url=url, max_retries=0))
    c.aquecer()
    assert reqs[0]["corpo"]["max_tokens"] == 16 and reqs[0]["corpo"]["messages"][0]["content"] == "oi"
