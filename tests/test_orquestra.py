"""AI Command Center (jarvis/orquestra): gateway de processos, segurança (pastas, segredos, comandos, checkpoint git),
adaptadores de Claude Code e Codex (rodando a CLI SIMULADA, que fala o formato real), roteador, planejador,
orquestrador (paralelo, travas, novas tentativas, troca de agente, custo, cancelamento, reparo, retomada), MCP,
terminal, painel da HUD e as ferramentas do cérebro."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from jarvis.connect.cofre import Cofre
from jarvis.orquestra import execucao as execucao_mod
from jarvis.orquestra import ferramentas, gateway, mcp, seguranca
from jarvis.orquestra.agentes.base import Agente, Controle, Deteccao, Disponibilidade, Resultado, Trabalho
from jarvis.orquestra.agentes.claude_code import ClaudeCode
from jarvis.orquestra.agentes.codex import Codex
from jarvis.orquestra.agentes.testes import ExecutorDeTestes
from jarvis.orquestra.banco import Banco
from jarvis.orquestra.nucleo import Orquestra
from jarvis.orquestra.painel import PainelOrquestra
from jarvis.orquestra.planejador import ordem_topologica, plano_por_modelo, validar_plano
from jarvis.orquestra.roteador import Similaridade, classificar
from jarvis.orquestra.terminal import Terminal
from jarvis.tools.base import REGISTRO, ErroFerramenta, Nivel

SIMULADOR = Path(__file__).resolve().parents[1] / "jarvis" / "orquestra" / "simuladores" / "cli_simulado.py"
PY = sys.executable


# ------------------------------------------------------------------ apoio
def git(raiz, *args):
    return subprocess.run(["git", "-C", str(raiz), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def projeto(tmp_path):
    """O projeto de exemplo da demonstração (bug de desconto + busca O(n²)), num repositório git."""
    from jarvis.orquestra import demo
    return demo.criar_projeto(tmp_path / "loja")


@pytest.fixture
def cofre(tmp_path):
    return Cofre(tmp_path / "cofre.json")


@pytest.fixture(autouse=True)
def rapido(monkeypatch):
    monkeypatch.setenv("JARVIS_SIM_ATRASO", "0")
    monkeypatch.setattr(execucao_mod, "ESPERAS_TRANSITORIO_S", (0, 0))
    for var in ("JARVIS_SIM_FALHAR", "JARVIS_SIM_RECONEXAO", "JARVIS_SIM_FERRAMENTA_EXTRA"):
        monkeypatch.delenv(var, raising=False)


def cfg_sim(**orq):
    """Config com Claude Code e Codex apontando para a CLI simulada e os testes no Python destes testes."""
    return {"orquestra": {"planejar_com_ia": False, "relatorio_com_ia": False,
                          "testes": {"loja": [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"]},
                          "agentes": {"claude_code": {"comando": [PY, str(SIMULADOR), "claude"]},
                                      "codex": {"comando": [PY, str(SIMULADOR), "codex"]}}, **orq}}


class Falso(Agente):
    """Agente de teste: executa uma função Python em vez de uma IA."""
    tipo = "cli"
    pode_ler_projeto = True
    custo_modo = "assinatura"
    latencia = "baixa"
    max_simultaneos = 4

    def __init__(self, id_, capacidades, fazer, pode_editar=False, cfg=None, cofre=None):
        self.id, self.nome = id_, id_.capitalize()
        self.capacidades = capacidades
        self.pode_editar = pode_editar
        self.fazer = fazer
        self.chamadas: list[Trabalho] = []
        super().__init__(cfg, cofre)

    def _detectar(self):
        return Deteccao(True, "1.0")

    def _autenticacao(self):
        return Disponibilidade("disponivel", "ok", True, "assinatura")

    def executar(self, t, c):
        self.chamadas.append(t)
        return self.fazer(t, c, len(self.chamadas))


def nucleo(agentes, cfg=None, cofre=None, tmp=None, eventos=None, avisos=None):
    o = Orquestra(cfg or {"orquestra": {"planejar_com_ia": False, "relatorio_com_ia": False}}, Banco(":memory:"),
                  cofre or Cofre(Path(tmp or ".") / "c.json"), agentes=agentes,
                  emitir=lambda tipo, **k: eventos.append((tipo, k)) if eventos is not None else None,
                  avisar=lambda texto, fala="": avisos.append(texto) if avisos is not None else None)
    return o


def esperar(o, id_, limite=60):
    t = time.time()
    while time.time() - t < limite:
        if not o.tarefas.rodando(id_) and o.detalhe(id_)["status"] not in ("executando", "planejada"):
            return o.detalhe(id_)
        time.sleep(0.05)
    raise AssertionError(f"a execução {id_} não terminou: {o.detalhe(id_)['status']}")


def passo(pid, cat, deps=(), escrita=False, agente=None, escopo=None, opcional=False):
    return {"id": pid, "titulo": f"passo {pid}", "categoria": cat, "instrucoes": f"faça {pid}", "depende_de": list(deps),
            "escrita": escrita, "agente": agente, "escopo": escopo or [], "opcional": opcional}


ok = lambda texto="feito\nRESUMO: ok": (lambda t, c, n: Resultado(True, texto, custo_usd=0.01))


# ------------------------------------------------------------------ gateway
def test_processo_entrada_saida_e_codigo(tmp_path):
    linhas = []
    p = gateway.Processo([PY, "-c", "import sys; print(sys.stdin.read().upper()); print('erro', file=sys.stderr); sys.exit(3)"],
                         tmp_path, seguranca.ambiente_limpo(), "olá", 30, 512, lambda f, l: linhas.append((f, l))).iniciar()
    assert p.esperar(30) == 3
    assert ("stdout", "OLÁ") in linhas and ("stderr", "erro") in linhas


def test_processo_estoura_tempo_e_cancela(tmp_path):
    p = gateway.Processo([PY, "-c", "import time; time.sleep(30)"], tmp_path, None, None, 0.5, 512, lambda f, l: None).iniciar()
    p.esperar(15)
    assert p.estado == "estourou_tempo" and not p.rodando
    p = gateway.Processo([PY, "-c", "import time; time.sleep(30)"], tmp_path, None, None, 60, 512, lambda f, l: None).iniciar()
    time.sleep(0.3)
    p.cancelar("teste")
    p.esperar(15)
    assert p.estado == "cancelado" and "teste" in p.motivo_fim


def test_pausa_nao_conta_no_tempo(tmp_path):
    p = gateway.Processo([PY, "-c", "import time; time.sleep(3)"], tmp_path, None, None, 60, 512, lambda f, l: None).iniciar()
    time.sleep(0.2)
    assert p.pausar()
    time.sleep(1.0)
    assert p.retomar()
    assert p.tempo_ativo() < 1.0
    p.cancelar()
    p.esperar(10)


def test_resolver_executavel_com_override():
    cmd = gateway.resolver_executavel("qualquer", [PY, "x.py"])
    assert Path(cmd[0]).resolve() == Path(PY).resolve() and cmd[1:] == ["x.py"]
    assert gateway.resolver_executavel("nao-existe-mesmo-xyz") is None
    assert gateway.caminho_em_args_ok("sessao-123") and not gateway.caminho_em_args_ok("a & del *")


# ------------------------------------------------------------------ segurança
def test_validar_pasta(tmp_path):
    assert seguranca.validar_pasta(tmp_path) == tmp_path.resolve()
    with pytest.raises(ErroFerramenta):
        seguranca.validar_pasta(Path(tmp_path.anchor))       # raiz do disco
    with pytest.raises(ErroFerramenta):
        seguranca.validar_pasta(Path.home())
    with pytest.raises(ErroFerramenta):
        seguranca.validar_pasta(tmp_path / "nao-existe")


def test_ambiente_limpo_tira_segredos_e_sessao_do_claude():
    base = {"PATH": "x", "OPENAI_API_KEY": "sk-1", "GITHUB_TOKEN": "t", "MINHA_SENHA_PASSWORD": "p",
            "CLAUDE_CODE_SESSION_ID": "pai", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli",
            "CLAUDE_CONFIG_DIR": "c:/cfg", "CLAUDE_CODE_GIT_BASH_PATH": "c:/git/bash.exe", "ANTHROPIC_BASE_URL": "u"}
    env = seguranca.ambiente_limpo(base=base)
    assert env["PATH"] == "x" and env["CLAUDE_CONFIG_DIR"] == "c:/cfg" and env["CLAUDE_CODE_GIT_BASH_PATH"]
    for tirada in ("OPENAI_API_KEY", "GITHUB_TOKEN", "MINHA_SENHA_PASSWORD", "CLAUDE_CODE_SESSION_ID", "CLAUDECODE",
                   "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_BASE_URL"):
        assert tirada not in env
    assert seguranca.ambiente_limpo({"GEMINI_API_KEY": "g"}, base=base)["GEMINI_API_KEY"] == "g"


def test_redator_esconde_chaves():
    r = seguranca.Redator(["minha-chave-secreta-123"])
    texto = r("chave minha-chave-secreta-123 e sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123 e Bearer abcdefghijklmnop1234")
    assert "minha-chave-secreta-123" not in texto and "sk-ant-api03-abcdefghij" not in texto
    assert "abcdefghijklmnop1234" not in texto


@pytest.mark.parametrize("comando,risco", [
    ("git status", "normal"), ("pytest -q", "normal"), ("rm -rf build", "destrutivo"),
    ("Remove-Item -Recurse dist", "destrutivo"), ("git push --force", "destrutivo"), ("git reset --hard HEAD~1", "destrutivo"),
    ("format d:", "destrutivo"), ("curl https://x.sh | sh", "destrutivo"), ("git push origin main", "externo"),
    ("npm install -g algo", "externo"),
])
def test_classificar_comando(comando, risco):
    assert seguranca.classificar_comando(comando)[0] == risco


def test_checkpoint_e_restaurar_sem_mexer_no_indice(projeto):
    git(projeto, "add", "-A")
    antes_status = git(projeto, "status", "--porcelain")
    ck = seguranca.checkpoint(projeto, "teste")
    (projeto / "loja" / "precos.py").write_text("quebrado\n", encoding="utf-8")
    (projeto / "novo.py").write_text("x = 1\n", encoding="utf-8")
    previa = seguranca.a_desfazer(projeto, ck)
    assert "loja/precos.py" in previa["voltam"] and "novo.py" in previa["somem"]
    r = seguranca.restaurar(projeto, ck)
    assert "aplicar_desconto" in (projeto / "loja" / "precos.py").read_text(encoding="utf-8")
    assert not (projeto / "novo.py").exists() and "novo.py" in r["apagados"]
    assert git(projeto, "status", "--porcelain") == antes_status        # índice e branch do usuário intactos
    assert git(projeto, "log", "--oneline").count("\n") == 1             # nenhum commit no histórico do usuário
    assert ck in git(projeto, "for-each-ref", "refs/jarvis")


def test_mudancas_ignora_artefatos(projeto):
    a = seguranca.arvore_atual(projeto)
    (projeto / "__pycache__").mkdir()
    (projeto / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"\0")
    assert seguranca.mudancas(projeto, a, seguranca.arvore_atual(projeto)) == []


# ------------------------------------------------------------------ adaptadores com a CLI simulada (formato real)
def test_claude_code_detecta_versao_flags_e_login(cofre):
    a = ClaudeCode(cfg_sim(), cofre)
    d = a.detectar()
    assert d.instalado and d.versao == "2.1.303" and "--restricted" in d.flags and "--max-budget-usd" in d.flags
    assert a.disponibilidade().estado == "disponivel" and a.cartao()["simulado"] is True


def test_claude_code_leitura_le_eventos_e_nao_escreve(projeto, cofre):
    a = ClaudeCode(cfg_sim(), cofre)
    eventos = []
    t = Trabalho("Analise o projeto", cwd=projeto, escrita=False, categoria="analise", sistema="Você é o ANALISTA.",
                 limite_usd=1.0)
    argv = a.argumentos(t, a.detectar().flags)
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep" and "dontAsk" in argv and "--restricted" in argv
    assert "Você é o ANALISTA." in argv[argv.index("--append-system-prompt") + 1]       # o papel chega à CLI
    r = a.executar(t, Controle(lambda tipo, texto, dados: eventos.append((tipo, texto))))
    assert r.ok, r.erro
    assert "loja/precos.py:6" in r.texto and r.sessao.startswith("sim-") and r.custo_usd > 0 and r.tokens_entrada > 0
    assert any(tipo == "ferramenta" and "Read" in texto for tipo, texto in eventos)
    assert "percentual / 100" not in (projeto / "loja" / "precos.py").read_text(encoding="utf-8")


def test_claude_code_escrita_altera_arquivos(projeto, cofre):
    a = ClaudeCode(cfg_sim(), cofre)
    t = Trabalho("corrija", cwd=projeto, escrita=True, categoria="depuracao", sistema="Você é o DEPURADOR.")
    argv = a.argumentos(t, a.detectar().flags)
    assert "acceptEdits" in argv and "Bash(git push:*)" in argv[argv.index("--disallowedTools") + 1]
    r = a.executar(t, Controle())
    assert r.ok and any(p.endswith("precos.py") for p in r.arquivos)
    assert "percentual / 100" in (projeto / "loja" / "precos.py").read_text(encoding="utf-8")


def test_claude_code_fail_closed_com_ferramenta_inesperada(projeto, cofre, monkeypatch):
    monkeypatch.setenv("JARVIS_SIM_FERRAMENTA_EXTRA", "mcp__estranho__apagar")
    r = ClaudeCode(cfg_sim(), cofre).executar(Trabalho("analise", cwd=projeto, categoria="analise",
                                                       sistema="Você é o ANALISTA."), Controle())
    assert not r.ok and not r.cancelado and "não autorizadas" in r.erro


def test_claude_code_erro_no_resultado(projeto, cofre, monkeypatch):
    monkeypatch.setenv("JARVIS_SIM_FALHAR", "overloaded_error: tente de novo")
    r = ClaudeCode(cfg_sim(), cofre).executar(Trabalho("x", cwd=projeto, categoria="analise"), Controle())
    assert not r.ok and r.transitorio


def test_codex_sandbox_leitura_e_escrita(projeto, cofre):
    a = Codex(cfg_sim(), cofre)
    assert a.detectar().versao == "0.163.0" and a.disponibilidade().metodo == "assinatura"
    t = Trabalho("analise", cwd=projeto, escrita=False, categoria="analise", sistema="Você é o ANALISTA.")
    argv = a.argumentos(t)
    assert argv[:2] == ["exec", "--json"] and "read-only" in argv and argv[-1] == "-" and "approval_policy=never" in argv
    r = a.executar(t, Controle())
    assert r.ok and "gravidade alta" in r.texto and r.tokens_entrada > 0 and r.sessao
    assert "percentual / 100" not in (projeto / "loja" / "precos.py").read_text(encoding="utf-8")
    r = a.executar(Trabalho("corrija", cwd=projeto, escrita=True, categoria="implementacao",
                            sistema="Você é o IMPLEMENTADOR."), Controle())
    assert r.ok and len(r.arquivos) == 2
    retomar = a.argumentos(Trabalho("x", cwd=projeto, escrita=True, retomar=r.sessao))
    assert retomar[:3] == ["exec", "resume", r.sessao] and "sandbox_mode=workspace-write" in retomar


def test_codex_reconexao_nao_e_fatal_e_turno_falho_e(projeto, cofre, monkeypatch):
    avisos = []
    monkeypatch.setenv("JARVIS_SIM_RECONEXAO", "1")
    r = Codex(cfg_sim(), cofre).executar(Trabalho("analise", cwd=projeto, sistema="ANALISTA"),
                                         Controle(lambda tipo, texto, d: avisos.append((tipo, texto))))
    assert r.ok and any(tipo == "aviso" and "Reconnecting" in texto for tipo, texto in avisos)
    monkeypatch.setenv("JARVIS_SIM_FALHAR", "stream disconnected before completion")
    r = Codex(cfg_sim(), cofre).executar(Trabalho("analise", cwd=projeto), Controle())
    assert not r.ok and "stream disconnected" in r.erro


def test_cli_ausente_fica_desconectada(cofre):
    a = Codex({"orquestra": {"agentes": {"codex": {"comando": ["programa-que-nao-existe-xyz"]}}}}, cofre)
    assert not a.detectar().instalado and a.disponibilidade().estado == "desconectado"


def test_executor_de_testes_conta_falhas(projeto, cofre):
    a = ExecutorDeTestes(cfg_sim(), cofre)
    r = a.executar(Trabalho("", cwd=projeto, categoria="testes"), Controle())
    assert r.ok and r.dados["falhou"] == 1 and r.dados["passou"] == 4 and not r.dados["verde"]
    assert r.dados["falhas"] == ["tests/test_precos.py::test_desconto_de_10_por_cento"]


# ------------------------------------------------------------------ roteador e planejador
def test_classificar_semantico_lexico():
    sim = Similaridade(None)
    assert classificar("corrija o erro que dá no login", sim)[0][0] == "depuracao"
    assert classificar("escreva a documentação do README", sim)[0][0] == "documentacao"


def test_roteador_escolhe_pelo_que_cada_um_faz_bem(tmp_path):
    analista = Falso("analista", {"analise": 0.95, "implementacao": 0.6}, ok())
    impl = Falso("impl", {"analise": 0.6, "implementacao": 0.95}, ok(), pode_editar=True)
    o = nucleo({"analista": analista, "impl": impl}, tmp=tmp_path)
    p = {"chave": "a", "titulo": "x", "categoria": "analise", "instrucoes": "analise", "escrita": False, "agente": None,
         "agente_fixo": False}
    assert o.roteador.escolher(p, True).agente == "analista"
    assert o.roteador.escolher({**p, "categoria": "implementacao", "escrita": True}, True).agente == "impl"
    # quem não edita não entra em passo de escrita
    _, recusados = o.roteador.ranquear({**p, "categoria": "implementacao", "escrita": True}, True)
    assert "analista" in recusados
    # histórico ruim derruba a nota
    for _ in range(8):
        o.banco.registrar_desempenho("analista", "analise", False, 1, 0)
    assert o.roteador.escolher(p, True).agente == "impl"


def test_plano_modelo_para_o_pedido_da_demo():
    plano = plano_por_modelo("analise meu projeto, encontre problemas, corrija os erros e melhore o desempenho", True,
                             Similaridade(None), True)
    cats = [(p["id"], p["categoria"], p["escrita"]) for p in plano["passos"]]
    assert ("analise", "analise", False) in cats and ("testes_antes", "testes", False) in cats
    mudar = next(p for p in plano["passos"] if p["id"] == "mudar")
    assert mudar["escrita"] and set(mudar["depende_de"]) == {"analise", "testes_antes"}
    assert ordem_topologica(plano["passos"])[0] == ["analise", "testes_antes"]       # os dois rodam juntos


def test_plano_com_agentes_citados():
    plano = plano_por_modelo("peça ao Claude Code para revisar o login e ao Codex para implementar o cadastro", True,
                             Similaridade(None), False)
    agentes = [p.get("agente") for p in plano["passos"] if p.get("agente")]
    assert agentes[:2] == ["claude_code", "codex"]


def test_validar_plano_rejeita_ciclo_e_escrita_sem_projeto():
    with pytest.raises(ValueError):
        validar_plano({"passos": [passo("a", "analise", ["b"]), passo("b", "analise", ["a"])]}, {}, True)
    with pytest.raises(ValueError):
        validar_plano({"passos": [passo("a", "implementacao", escrita=True)]}, {}, False)


# ------------------------------------------------------------------ orquestrador
def test_demo_simulada_ponta_a_ponta(projeto, cofre):
    """Claude Code analisa, Codex corrige, os testes de verdade passam de vermelho para verde, Claude revisa, desfaz."""
    eventos, avisos = [], []
    o = Orquestra(cfg_sim(), Banco(":memory:"), cofre, emitir=lambda tipo, **k: eventos.append((tipo, k)),
                  avisar=lambda texto, fala="": avisos.append(texto))
    for id_ in ("gemini", "ollama", "openrouter", "openai"):
        o.agentes.pop(id_)
    exe = o.criar("analise meu projeto, encontre problemas, corrija os erros e melhore o desempenho", projeto,
                  "supervisionado")
    assert exe["status"] == "aguardando_aprovacao"
    o.aprovar(exe["id"], autorizar_projeto=True)
    exe = esperar(o, exe["id"])
    assert exe["status"] == "concluida", [(p["chave"], p["status"], p["erro"]) for p in exe["passos"]]
    quem = {p["chave"]: p["agente"] for p in exe["passos"]}
    assert quem["analise"] == "claude_code" and quem["mudar"] == "codex" and quem["revisao"] == "claude_code"
    assert quem["testes_antes"] == quem["testes_depois"] == "testes"
    assert "Corrigidas: tests/test_precos.py::test_desconto_de_10_por_cento" in exe["relatorio"]
    assert "Antes das mudanças: com falhas (1 failed, 4 passed" in exe["relatorio"]
    assert o.autorizado(projeto) and avisos and any(t == "orq" for t, _ in eventos)
    logs = o.banco.logs(exe["id"], 0, 1000)
    assert any(l["tipo"] == "arquivo" and l["agente"] == "codex" for l in logs)
    r = o.desfazer(exe["id"])
    assert sorted(r["restaurados"]) == ["loja/estoque.py", "loja/precos.py"]
    assert "preco * percentual, 2)" in (projeto / "loja" / "precos.py").read_text(encoding="utf-8")


def test_automatico_so_executa_direto_em_projeto_autorizado(projeto, tmp_path):
    o = nucleo({"impl": Falso("impl", {"implementacao": 0.9}, ok(), pode_editar=True)}, tmp=tmp_path)
    passos = [passo("p1", "implementacao", escrita=True)]
    exe = o.criar("implemente", projeto, "automatico", passos)
    assert exe["status"] == "aguardando_aprovacao" and "autorizado" in exe["motivo_espera"]
    o.autorizar_projeto(projeto)
    exe = o.criar("implemente", projeto, "automatico", passos)
    assert esperar(o, exe["id"])["status"] == "concluida"


def test_paralelo_e_trava_de_escrita(projeto, tmp_path):
    rodando, maximo_leitura, escritores = [], [0], []
    trava = threading.Lock()

    def ler(t, c, n):
        with trava:
            rodando.append(1)
            maximo_leitura[0] = max(maximo_leitura[0], len(rodando))
        time.sleep(0.4)
        with trava:
            rodando.pop()
        return Resultado(True, "li")

    def escrever(t, c, n):
        with trava:
            escritores.append(("inicio", time.time()))
        time.sleep(0.3)
        (t.cwd / f"arq_{time.time_ns()}.txt").write_text("x", encoding="utf-8")
        with trava:
            escritores.append(("fim", time.time()))
        return Resultado(True, "escrevi")

    o = nucleo({"leitor": Falso("leitor", {"analise": 0.9}, ler),
                "escritor": Falso("escritor", {"implementacao": 0.9}, escrever, pode_editar=True)}, tmp=tmp_path)
    o.autorizar_projeto(projeto)
    exe = o.criar("x", projeto, "automatico", [passo("a", "analise"), passo("b", "analise"), passo("c", "analise"),
                                               passo("w1", "implementacao", escrita=True),
                                               passo("w2", "implementacao", escrita=True)])
    exe = esperar(o, exe["id"])
    assert exe["status"] == "concluida"
    assert maximo_leitura[0] >= 2                                  # leituras em paralelo
    marcas = [m for m, _ in escritores]
    assert marcas == ["inicio", "fim", "inicio", "fim"]            # escritas no mesmo projeto: uma de cada vez


def test_erro_passageiro_tenta_de_novo_e_erro_fixo_troca_de_agente(projeto, tmp_path):
    def instavel(t, c, n):
        return Resultado(False, erro="503 overloaded", transitorio=True) if n == 1 else Resultado(True, "foi")

    ruim = Falso("ruim", {"analise": 0.99}, lambda t, c, n: Resultado(False, erro="não sei fazer"))
    bom = Falso("bom", {"analise": 0.5}, ok())
    inst = Falso("inst", {"revisao": 0.9}, instavel)
    o = nucleo({"ruim": ruim, "bom": bom, "inst": inst}, tmp=tmp_path)
    exe = esperar(o, o.criar("x", None, "automatico", [passo("a", "analise"), passo("r", "revisao")])["id"])
    assert exe["status"] == "concluida"
    a, r = (next(p for p in exe["passos"] if p["chave"] == k) for k in ("a", "r"))
    assert a["agente"] == "bom" and a["tentativas"] == 2 and len(ruim.chamadas) == 1
    assert r["agente"] == "inst" and r["tentativas"] == 2 and len(inst.chamadas) == 2


def test_falha_de_escrita_desfaz_mudancas_parciais(projeto, tmp_path):
    def quebra(t, c, n):
        (t.cwd / "loja" / "precos.py").write_text("lixo", encoding="utf-8")
        return Resultado(False, erro="desisti")

    o = nucleo({"q": Falso("q", {"implementacao": 0.9}, quebra, pode_editar=True)}, tmp=tmp_path)
    o.autorizar_projeto(projeto)
    exe = esperar(o, o.criar("x", projeto, "automatico", [passo("w", "implementacao", escrita=True)])["id"])
    assert exe["status"] == "falhou"
    assert "aplicar_desconto" in (projeto / "loja" / "precos.py").read_text(encoding="utf-8")


def test_passo_de_leitura_que_escreve_e_desfeito(projeto, tmp_path):
    def intrometido(t, c, n):
        (t.cwd / "README.md").write_text("mexi", encoding="utf-8")
        return Resultado(True, "li só")

    o = nucleo({"x": Falso("x", {"analise": 0.9}, intrometido)}, tmp=tmp_path)
    o.autorizar_projeto(projeto)
    exe = esperar(o, o.criar("x", projeto, "automatico", [passo("a", "analise")])["id"])
    assert "mexi" not in (projeto / "README.md").read_text(encoding="utf-8")
    assert any(l["tipo"] == "alerta" for l in o.banco.logs(exe["id"], 0, 100))


def test_limite_de_custo_para_a_execucao(tmp_path):
    caro = Falso("caro", {"analise": 0.9}, lambda t, c, n: Resultado(True, "ok", custo_usd=0.8))
    o = nucleo({"caro": caro}, cfg={"orquestra": {"planejar_com_ia": False, "relatorio_com_ia": False,
                                                  "custos": {"limite_tarefa_usd": 1.0}}}, tmp=tmp_path)
    exe = esperar(o, o.criar("x", None, "automatico", [passo("a", "analise"), passo("b", "analise", ["a"]),
                                                       passo("c", "analise", ["b"])])["id"])
    estados = {p["chave"]: p["status"] for p in exe["passos"]}
    assert estados["a"] == estados["b"] == "concluido" and estados["c"] == "bloqueado"
    assert exe["status"] == "falhou" and "limite" in exe["erro"]
    assert len(caro.chamadas) == 2


def test_cancelar_encerra_o_passo(tmp_path):
    def lento(t, c, n):
        c.cancelado.wait(20)
        return Resultado(False, erro="cancelado", cancelado=True)

    o = nucleo({"l": Falso("l", {"analise": 0.9}, lento)}, tmp=tmp_path)
    exe = o.criar("x", None, "automatico", [passo("a", "analise")])
    time.sleep(0.3)
    o.cancelar(exe["id"])
    exe = esperar(o, exe["id"])
    assert exe["status"] == "cancelada" and exe["passos"][0]["status"] == "cancelado"


def test_reparo_quando_os_testes_falham_depois(projeto, tmp_path, cofre):
    def implementa(t, c, n):
        alvo = t.cwd / "loja" / "precos.py"
        texto = alvo.read_text(encoding="utf-8")
        if n == 1:          # primeira vez: mexe sem corrigir (testes continuam vermelhos)
            alvo.write_text(texto + "\n# ajuste\n", encoding="utf-8")
        else:               # no reparo, com a falha no contexto, corrige
            assert "test_desconto_de_10_por_cento" in t.prompt
            alvo.write_text(texto.replace("preco * percentual,", "preco * percentual / 100,"), encoding="utf-8")
        return Resultado(True, f"rodada {n}", sessao="s1")

    impl = Falso("impl", {"implementacao": 0.9}, implementa, pode_editar=True)
    impl.suporta_retomar = True
    o = nucleo({"impl": impl, "testes": ExecutorDeTestes(cfg_sim(), cofre)}, cfg=cfg_sim(), cofre=cofre)
    o.autorizar_projeto(projeto)
    exe = esperar(o, o.criar("corrija", projeto, "automatico",
                            [passo("mudar", "implementacao", escrita=True),
                             passo("testes_depois", "testes", ["mudar"], agente="testes")])["id"], limite=90)
    chaves = [p["chave"] for p in exe["passos"]]
    assert "reparo_1" in chaves and "testes_reparo_1" in chaves, chaves
    assert len(impl.chamadas) == 2 and impl.chamadas[1].retomar == "s1"
    final = next(p for p in exe["passos"] if p["chave"] == "testes_reparo_1")
    assert final["dados"]["verde"]


def test_recuperar_marca_interrompida_e_retoma(tmp_path):
    o = nucleo({"a": Falso("a", {"analise": 0.9}, ok())}, tmp=tmp_path)
    exe = o.criar("x", None, "supervisionado", [passo("p", "analise")])
    o.banco.mudar_execucao(exe["id"], status="executando")
    o.banco.mudar_passo(exe["id"], "p", status="executando")
    assert o.recuperar() == 1
    assert o.detalhe(exe["id"])["status"] == "interrompida"
    o.aprovar(exe["id"])
    assert esperar(o, exe["id"])["status"] == "concluida"


def test_trocar_agente_no_modo_manual(tmp_path):
    a, b = Falso("a", {"analise": 0.9}, ok()), Falso("b", {"analise": 0.4}, ok())
    o = nucleo({"a": a, "b": b}, tmp=tmp_path)
    exe = o.criar("x", None, "manual", [passo("p", "analise")])
    assert exe["status"] == "aguardando_aprovacao"
    o.trocar_agente(exe["id"], "p", "b")
    o.aprovar(exe["id"])
    exe = esperar(o, exe["id"])
    assert exe["passos"][0]["agente"] == "b" and not a.chamadas


def test_contexto_nao_leva_segredos(tmp_path):
    vistos = []
    o = nucleo({"a": Falso("a", {"analise": 0.9}, lambda t, c, n: (vistos.append(t.prompt), Resultado(True, "ok"))[1])},
               tmp=tmp_path)
    o.cofre.guardar("openrouter", {"apikey": "sk-or-v1-segredodemais1234567890"})
    o.redator = seguranca.redator_do_cofre(o.cofre)
    o.contexto.redator = o.redator
    esperar(o, o.criar("use a chave sk-or-v1-segredodemais1234567890 para testar", None, "automatico",
                       [passo("p", "analise")])["id"])
    assert vistos and "segredodemais" not in vistos[0]


# ------------------------------------------------------------------ MCP
@pytest.fixture
def gateway_mcp(tmp_path, monkeypatch):
    g = mcp.GatewayMCP(tmp_path / "mcp.json")
    g.salvar_servidor("teste", PY, [str(Path(__file__).parent / "mcp_servidor_teste.py")])
    monkeypatch.setattr(mcp, "_gateway", g)
    yield g
    g.desconectar()


def test_mcp_lista_paginada_chama_e_niveis(gateway_mcp):
    nomes = [f["name"] for f in gateway_mcp.ferramentas("teste")]
    assert nomes == ["eco", "falha"]                                     # duas páginas (nextCursor)
    assert gateway_mcp.cliente("teste").info["serverInfo"]["name"] == "teste"
    assert mcp.texto_do_resultado(gateway_mcp.chamar("teste", "eco", {"texto": "oi"})) == "eco: oi"
    assert gateway_mcp.chamar("teste", "falha")["isError"] is True
    assert gateway_mcp.nivel("teste", "eco") == "registrado"             # readOnlyHint
    assert gateway_mcp.nivel("teste", "falha") == "confirmar"            # padrão: 🔴
    with pytest.raises(mcp.ErroMCP):
        gateway_mcp.chamar("teste", "nao_existe")
    assert gateway_mcp.estado()[0]["conectado"]


# ------------------------------------------------------------------ terminal e painel
def test_terminal_roda_confirma_e_bloqueia(projeto, tmp_path):
    o = nucleo({}, tmp=tmp_path)
    t = Terminal(o)
    with pytest.raises(ValueError):
        t.executar(str(projeto), "git status")                          # pasta não autorizada
    o.autorizar_projeto(projeto)
    assert t.executar(str(projeto), "rm -rf build")["precisa_confirmar"]
    r = t.executar(str(projeto), "git status --short")
    s = t.sessoes[r["id"]]
    s.processo.esperar(30)
    time.sleep(0.2)
    assert s.processo.codigo == 0
    assert o.banco.historico_terminal(5)[0]["comando"] == "git status --short"


def test_painel_ops(projeto, tmp_path):
    o = nucleo({"a": Falso("a", {"analise": 0.9}, ok())}, tmp=tmp_path)
    painel = PainelOrquestra(o)
    pedir = lambda op, **k: painel.tratar({"tipo": "central", "op": op, "pedido": 7, **k})
    r = pedir("orq_criar", texto="analise o projeto", projeto=str(projeto), modo="supervisionado")
    assert r["ok"] and r["pedido"] == 7 and r["dados"]["status"] == "aguardando_aprovacao"
    d = pedir("orq_execucao", id=r["dados"]["id"])["dados"]
    assert d["niveis"] and d["nomes"]["a"] == "A" and "plano" not in d
    assert pedir("orq_desfazer", id=d["id"])["ok"] is False                # sem confirmado
    chave = pedir("orq_chave", provedor="openrouter", apikey="sk-or-v1-abcdef1234567890")["dados"]
    assert chave["guardada"] and "abcdef" not in json.dumps(chave)
    assert "sk-or-v1-abcdef" not in json.dumps(pedir("orq_estado")["dados"])
    assert pedir("orq_inexistente")["ok"] is False and pedir("__init__")["ok"] is False
    assert pedir("orq_modo", modo="manual")["dados"]["modo"] == "manual"
    assert pedir("orq_modo", modo="turbo")["ok"] is False


# ------------------------------------------------------------------ ferramentas do cérebro
def test_niveis_das_ferramentas(projeto, tmp_path):
    o = nucleo({"impl": Falso("impl", {"implementacao": 0.9, "analise": 0.9}, ok(), pode_editar=True)}, tmp=tmp_path)
    ferramentas.ativar(o)
    try:
        f = REGISTRO["orquestrar"]
        assert f.so_claude
        args = {"pedido": "corrija os erros do projeto", "projeto": str(projeto), "modo": "automatico"}
        assert f.avaliacao(args).nivel == Nivel.CONFIRMAR                   # automático em projeto não autorizado
        assert f.avaliacao({**args, "modo": "supervisionado"}).nivel == Nivel.REGISTRADO
        assert REGISTRO["modo_orquestra"].avaliacao({"modo": "automatico"}).nivel == Nivel.CONFIRMAR
        assert REGISTRO["modo_orquestra"].avaliacao({"modo": "manual"}).nivel == Nivel.REGISTRADO
        exe = o.criar("x", projeto, "supervisionado", [passo("p", "implementacao", escrita=True)])
        assert REGISTRO["aprovar_plano"].avaliacao({"id": exe["id"]}).nivel == Nivel.CONFIRMAR
        assert REGISTRO["autorizar_projeto"].avaliacao({"projeto": str(projeto)}).nivel == Nivel.CONFIRMAR
        assert "esperando aprovação" in REGISTRO["orquestrar"].funcao(**{**args, "modo": "supervisionado"})
    finally:
        ferramentas.ativar(None)
