"""O Jarvis programa: projeto aberto no VS Code, travas de pasta, o comando do programador, a tarefa em segundo plano
que avisa quando termina, a resposta instantânea e o aviso falado."""
import io
import json
import threading
import time
from pathlib import Path

import pytest

from jarvis.brain import avisos
from jarvis.tools import programador as prog
from jarvis.tools.base import ErroFerramenta, Nivel


@pytest.fixture(autouse=True)
def limpo():
    avisos.limpar()
    prog._ESTADO.update(tarefa=None, sessoes={})
    yield
    avisos.limpar()


# ------------------------------------------------------------------ qual projeto está aberto
@pytest.mark.parametrize("titulo,esperado", [
    ("main.py - PROJETO JARVIS - Visual Studio Code", ("main.py", "PROJETO JARVIS")),
    ("● config.yaml - PROJETO JARVIS - Visual Studio Code", ("config.yaml", "PROJETO JARVIS")),
    ("Migração - parte 2 - meu-site - Visual Studio Code", ("Migração - parte 2", "meu-site")),
    ("meu-site - Visual Studio Code", ("", "meu-site")),
    ("Bloco de notas", ("", "")),
])
def test_ler_titulo(titulo, esperado):
    assert prog.ler_titulo(titulo) == esperado


def test_pastas_vem_da_memoria_do_vscode(tmp_path):
    arq = tmp_path / "storage.json"
    arq.write_text(json.dumps({"backupWorkspaces": {"folders": [{"folderUri": "file:///d%3A/PROJETO%20JARVIS"}]},
                               "windowsState": {"lastActiveWindow": {"folder": "file:///c%3A/dev/meu-site"},
                                                "openedWindows": []}}), encoding="utf-8")
    assert prog.pastas_do_vscode(arq) == [Path("d:/PROJETO JARVIS"), Path("c:/dev/meu-site")]


def test_projeto_aberto_e_o_da_janela_ativa(tmp_path):
    a, b = tmp_path / "PROJETO JARVIS", tmp_path / "meu-site"
    pasta, arquivo = prog.projeto_aberto(["index.html - meu-site - Visual Studio Code"], [a, b])
    assert pasta == b and arquivo == "index.html"
    assert prog.projeto_aberto([], []) == (None, "")


def test_travas_de_pasta(tmp_path, monkeypatch):
    assert prog.validar_pasta(tmp_path) == tmp_path.resolve()
    for ruim in (Path("C:/"), Path.home()):
        with pytest.raises(ErroFerramenta):
            prog.validar_pasta(ruim)
    with pytest.raises(ErroFerramenta, match="não existe"):
        prog.validar_pasta(tmp_path / "nao_existe")


def test_programar_sempre_pede_autorizacao(tmp_path, monkeypatch):
    monkeypatch.setattr(prog, "projeto_aberto", lambda: (tmp_path, "x.py"))
    a = prog._avaliar_programar({"pedido": "cria uma calculadora"})
    assert a.nivel == Nivel.CONFIRMAR and tmp_path.name in a.resumo and "calculadora" in a.resumo
    monkeypatch.setattr(prog, "projeto_aberto", lambda: (None, ""))
    with pytest.raises(ErroFerramenta, match="VS Code"):
        prog._avaliar_programar({"pedido": "x"})


def test_comando_do_programador_e_preso_e_sem_push():
    cmd = prog.comando("faz X", None)
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits" and "--strict-mcp-config" in cmd
    assert "git push" in cmd[cmd.index("--disallowedTools") + 1] and "Bash" in cmd[cmd.index("--allowedTools") + 1]
    assert cmd[cmd.index("--model") + 1] == "sonnet" and "--resume" not in cmd
    assert prog.comando("faz Y", "sessao-1")[-2:] == ["--resume", "sessao-1"]


# ------------------------------------------------------------------ tarefa em segundo plano
class ProcFalso:
    def __init__(self, eventos):
        self.stdout = io.StringIO("\n".join(json.dumps(e) for e in eventos) + "\n")

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


def eventos_ok(arquivo):
    return [{"type": "system", "subtype": "init", "session_id": "s-77"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write",
                                                           "input": {"file_path": arquivo}}]}},
            {"type": "result", "subtype": "success", "result": "Criei a calculadora e os testes passaram.",
             "usage": {"output_tokens": 10}, "total_cost_usd": 0.05, "num_turns": 4}]


def test_tarefa_termina_avisa_e_continua_a_sessao(tmp_path, monkeypatch):
    monkeypatch.setattr(prog, "_registrar_uso", lambda ev: None)
    monkeypatch.setattr(prog, "_abrir_no_vscode", lambda arqs, p: abertos.extend(arqs))
    abertos, comandos, falados = [], [], []
    avisos.ouvir(falados.append)
    rodar = lambda cmd, **k: comandos.append(cmd) or ProcFalso(eventos_ok(str(tmp_path / "calc.py")))
    t = {"pasta": tmp_path, "pedido": "cria a calculadora", "status": "rodando", "inicio": time.time()}
    prog.executar_tarefa(t, rodar)
    assert t["status"] == "concluida" and "calculadora" in t["resumo"] and abertos == [str(tmp_path / "calc.py")]
    assert falados and falados[0].startswith("Senhor, terminei")                # aviso falado, curto
    novidade = avisos.consumir()[0]
    assert "calc.py" in novidade and "concluída" in novidade                     # o cérebro fica sabendo
    prog.executar_tarefa({"pasta": tmp_path, "pedido": "agora multiplica", "status": "rodando", "inicio": time.time()}, rodar)
    assert comandos[1][-2:] == ["--resume", "s-77"]                              # mesma sessão de programação


def test_tarefa_com_erro_avisa_o_problema(tmp_path, monkeypatch):
    monkeypatch.setattr(prog, "_registrar_uso", lambda ev: None)
    rodar = lambda cmd, **k: ProcFalso([{"type": "result", "subtype": "error_max_turns", "is_error": True}])
    t = {"pasta": tmp_path, "pedido": "x", "status": "rodando", "inicio": time.time()}
    prog.executar_tarefa(t, rodar)
    assert t["status"] == "erro" and "grande demais" in t["resumo"]


def test_uma_tarefa_por_vez(tmp_path, monkeypatch):
    monkeypatch.setattr(prog, "projeto_aberto", lambda: (tmp_path, ""))
    prog._ESTADO["tarefa"] = {"pasta": tmp_path, "status": "rodando", "progresso": "mexendo em a.py", "inicio": time.time()}
    with pytest.raises(ErroFerramenta, match="anterior"):
        prog.programar("outra coisa")
    assert "mexendo em a.py" in prog.tarefa_programacao("status")


def test_novidades_entram_no_proximo_pedido_uma_vez():
    from jarvis.brain.llm import Cerebro
    c = Cerebro({"nome": "Jarvis", "tratamento": "senhor", "llm": {"modelo": "x", "host": "http://127.0.0.1:1",
                                                                   "keep_alive": -1, "temperatura": 0.3, "max_turnos": 10}})
    avisos.publicar("Tarefa de programação concluída: criei a calculadora.")
    assert "criei a calculadora" in c._contexto("e aí?")
    assert "Novidades" not in c._contexto("e aí?")


# ------------------------------------------------------------------ resposta instantânea e aviso falado
class VozFalsa:
    def __init__(self):
        self.ditas = []

    def falar(self, t):
        self.ditas.append(t)

    def esperar(self):
        pass


def controle(voz):
    from jarvis.hud.controle import Controle
    return Controle({"nome": "Jarvis", "tratamento": "senhor", "llm": {"modelo": "x"}, "vad": {"espera_inicio_s": 1}},
                    None, voz=voz)


def test_resposta_instantanea_so_uma_vez_e_so_quando_ainda_nao_falou():
    voz = VozFalsa()
    c = controle(voz)
    c._gerando, c._reconheceu, c.falado_turno = True, False, []
    c.reconhecer_acao("hora_data", "LIVRE")                      # ação instantânea: não precisa
    assert voz.ditas == []
    c.reconhecer_acao("abrir_programa", "LIVRE")
    c.reconhecer_acao("pesquisar_web", "LIVRE")                  # só uma vez por pedido
    assert len(voz.ditas) == 1 and c.falado_turno == voz.ditas   # o eco dele conta como fala dele
    c._reconheceu, c.falado_turno = False, ["Abrindo o Spotify, senhor."]
    c.reconhecer_acao("abrir_programa", "LIVRE")                 # o cérebro já falou: não repete
    c.reconhecer_acao("apagar", "CONFIRMAR")                     # a confirmação já fala
    assert len(voz.ditas) == 1


def test_executor_avisa_que_a_acao_vai_comecar(tmp_path):
    from jarvis.tools.base import Ferramenta
    from jarvis.tools.executor import Executor
    vistos = []
    ex = Executor(lambda *a, **k: True, str(tmp_path), {"x": Ferramenta("x", "X.", lambda: "ok")}, avisar=lambda t: None)
    ex.ao_comecar = lambda nome, nivel: vistos.append((nome, nivel))
    ex.executar("x", {})
    assert vistos == [("x", "LIVRE")]


def test_aviso_espera_a_conversa_acabar():
    voz = VozFalsa()
    c = controle(voz)
    c.ocupado.set()
    c.anunciar("Senhor, terminei a tarefa.")
    time.sleep(0.3)
    assert voz.ditas == []                                       # não atropela a conversa
    c.ocupado.clear()
    for _ in range(40):
        if voz.ditas:
            break
        time.sleep(0.1)
    assert voz.ditas == ["Senhor, terminei a tarefa."]
