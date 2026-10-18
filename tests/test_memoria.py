import re
import threading
import zlib
from datetime import date, timedelta

import numpy as np
import pytest

from jarvis.memoria.banco import Memoria, contem_segredo
from jarvis.memoria.reflexao import Consolidador, Reflexao, _empobrece, _trechos
from jarvis.tools import memoria_tools
from jarvis.tools.base import REGISTRO, Nivel
from jarvis.tools.executor import NEGADO, Executor


class EmbedderFalso:
    """Bolsa de palavras com hash: textos com palavras em comum ficam parecidos."""
    def vetores(self, textos, tipo="documento"):
        m = np.zeros((len(textos), 512), dtype=np.float32)
        for i, t in enumerate(textos):
            for p in re.findall(r"\w+", t.lower()):
                if len(p) > 2:
                    m[i, zlib.crc32(p.encode()) % 512] += 1
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        return m

    def consulta(self, texto):
        return self.vetores([texto], "consulta")[0]


class LLMFalso:
    """Devolve respostas programadas na ordem; registra os prompts."""
    def __init__(self, respostas):
        self.respostas, self.prompts = list(respostas), []

    def __call__(self, prompt, esquema):
        self.prompts.append(prompt)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def mem(tmp_path):
    return Memoria(str(tmp_path / "m.db"), EmbedderFalso())


def consolidador(mem, respostas):
    c = Consolidador(mem, None, "x", {}, -1)
    c.perguntar = LLMFalso(respostas)
    return c


# ------------------------------------------------------------------ banco
def test_adicionar_buscar_apagar(mem):
    a = mem.adicionar_fato("O usuário torce para o Flamengo.", "preferencia")
    mem.adicionar_fato("O usuário mora em São Paulo.", "perfil")
    achados = mem.buscar("qual time o usuário torce Flamengo", k=1, minimo=0.1)
    assert achados[0][0].id == a
    assert mem.apagar_fato(a) and mem.fato(a) is None
    assert all(f.id != a for f, _ in mem.buscar("Flamengo", k=5, minimo=0.0))


@pytest.mark.parametrize("texto", [
    "A senha do banco do usuário é 1234.", "O cartão de crédito do usuário termina em 4321.",
    "O CPF do usuário é 123.456.789-00.", "O número 4111 1111 1111 1111 é do usuário.", "O token da API é abc.",
])
def test_segredos_nunca_sao_guardados(mem, texto):
    assert contem_segredo(texto)
    assert mem.adicionar_fato(texto) is None and mem.fatos() == []


def test_contexto_sempre_tem_perfil_e_correcoes(mem):
    mem.adicionar_fato("O usuário se chama Iury.", "perfil")
    mem.adicionar_fato("Quando o usuário diz 'o sistema', ele quer dizer o ERP.", "correcao")
    mem.adicionar_fato("O usuário é alérgico a camarão.", "outro")
    ctx = mem.contexto("aumenta o volume")
    assert "Iury" in ctx and "o sistema" in ctx and "camarão" not in ctx
    assert "camarão" in mem.contexto("o usuário é alérgico a camarão?")


def test_contexto_vazio_quando_nao_sabe_nada(mem):
    assert mem.contexto("oi") == ""


def test_dias_pendentes_e_reflexao_marca(mem):
    mem.registrar_turno("s", "oi", "olá", [])
    ontem = (date.today() - timedelta(days=1)).isoformat()
    mem._db.execute("UPDATE turnos SET quando=?", (ontem + "T10:00:00",))
    mem.registrar_turno("s", "de hoje", "ok", [])
    assert mem.dias_pendentes() == [ontem]
    assert mem.dias_pendentes(incluir_hoje=True) == [ontem, date.today().isoformat()]
    mem.marcar_refletido(ontem)
    assert mem.dias_pendentes() == []


# ------------------------------------------------------------------ consolidação
def test_fato_sem_parecidos_entra_sem_perguntar_ao_llm(mem):
    c = consolidador(mem, [])
    acao, id_ = c.consolidar("O usuário tem um cachorro chamado Thor.", "pessoa", "t")
    assert acao == "novo" and mem.fato(id_)
    assert c.perguntar.prompts == []


def test_paráfrase_quase_identica_e_igual_sem_perguntar(mem):
    mem.adicionar_fato("O usuário torce para o Flamengo.")
    c = consolidador(mem, [])
    assert c.consolidar("O usuário torce para o Flamengo!", "preferencia", "t")[0] == "igual"
    assert len(mem.fatos()) == 1


def test_llm_decide_substituir(mem):
    antigo = mem.adicionar_fato("O usuário mora na cidade de São Paulo.", "perfil")
    c = consolidador(mem, [{"acao": "substitui", "id": antigo, "texto": "O usuário mora na cidade do Rio."}])
    acao, id_ = c.consolidar("O usuário mora na cidade do Rio.", "perfil", "t")
    assert acao == "substitui" and id_ == antigo
    assert [f.texto for f in mem.fatos()] == ["O usuário mora na cidade do Rio."]
    assert "[id" in c.perguntar.prompts[0] and "São Paulo" in c.perguntar.prompts[0]


def test_substitui_usa_a_frase_nova_e_nao_a_mistura_do_modelo(mem):
    antigo = mem.adicionar_fato("O usuário mora na cidade de Campinas.", "perfil")
    c = consolidador(mem, [{"assunto_nova": "cidade", "assunto_existente": "cidade", "acao": "substitui", "id": antigo,
                            "texto": "O usuário mora na cidade de Valinhos. O usuário mora na cidade de Campinas."}])
    c.consolidar("O usuário mora na cidade de Valinhos.", "outro", "t")
    f = mem.fato(antigo)
    assert f.texto == "O usuário mora na cidade de Valinhos." and f.categoria == "perfil"


def test_completa_junta_quando_nada_se_perde(mem):
    antigo = mem.adicionar_fato("O usuário trabalha como analista de suporte na empresa.", "perfil")
    c = consolidador(mem, [{"assunto_nova": "trabalho", "assunto_existente": "trabalho", "acao": "completa",
                            "id": antigo, "texto": "O usuário trabalha como analista de suporte na empresa Lumax."}])
    assert c.consolidar("O usuário trabalha na empresa Lumax.", "trabalho", "t") == ("substitui", antigo)
    assert mem.fato(antigo).texto == "O usuário trabalha como analista de suporte na empresa Lumax."


def test_completa_que_perde_detalhe_guarda_separado(mem):
    antigo = mem.adicionar_fato("A filha do usuário se chama Sofia e estuda na escola.", "pessoa")
    c = consolidador(mem, [{"assunto_nova": "filha", "assunto_existente": "filha", "acao": "completa",
                            "id": antigo, "texto": "A filha do usuário estuda na escola Objetivo."}])
    acao, _ = c.consolidar("A filha do usuário estuda na escola Objetivo.", "pessoa", "t")
    assert acao == "novo" and "Sofia" in mem.fato(antigo).texto and len(mem.fatos()) == 2


def test_categorias_incompativeis_nunca_se_fundem(mem):
    pref = mem.adicionar_fato("O usuário prefere que o Jarvis responda de forma curta.", "preferencia")
    c = consolidador(mem, [])  # se perguntasse ao modelo, LLMFalso quebraria (lista vazia)
    acao, _ = c.consolidar("O usuário está montando o Jarvis, que responde de forma curta.", "projeto", "t")
    assert acao == "novo" and mem.fato(pref).texto.startswith("O usuário prefere") and len(mem.fatos()) == 2


def test_perfil_e_trabalho_sao_compativeis(mem):
    antigo = mem.adicionar_fato("O usuário trabalha na empresa Lumax como analista.", "perfil")
    c = consolidador(mem, [{"assunto_nova": "emprego", "assunto_existente": "emprego", "acao": "substitui", "id": antigo}])
    assert c.consolidar("O usuário trabalha na empresa Linx como analista.", "trabalho", "t") == ("substitui", antigo)


def test_llm_decide_novo_mantem_os_dois(mem):
    mem.adicionar_fato("A esposa do usuário se chama Ana.", "pessoa")
    c = consolidador(mem, [{"acao": "novo"}])
    c.consolidar("O filho do usuário se chama Pedro.", "pessoa", "t")
    assert len(mem.fatos()) == 2


@pytest.mark.parametrize("resposta", [
    {"acao": "substitui", "id": 999, "texto": "x"},   # id que não estava entre os candidatos
    RuntimeError("ollama caiu"),                       # falha: na dúvida não apaga nada
])
def test_resposta_invalida_do_llm_nunca_apaga(mem, resposta):
    original = mem.adicionar_fato("O usuário gosta de rock dos anos 80.", "preferencia")
    c = consolidador(mem, [resposta])
    acao, _ = c.consolidar("O usuário gosta de pizza de calabresa dos anos 80.", "preferencia", "t")
    assert acao == "novo"
    assert mem.fato(original).texto == "O usuário gosta de rock dos anos 80."


# ------------------------------------------------------------------ reflexão
def test_reflexao_extrai_consolida_resume_e_marca(mem, tmp_path):
    ontem = (date.today() - timedelta(days=1)).isoformat()
    mem.registrar_turno("s", "Meu nome é Iury e trabalho na Microvix", "Prazer, senhor.", [])
    mem.registrar_turno("s", "Abre o Spotify", "Abri o Spotify.", ["abrir_programa"])
    mem._db.execute("UPDATE turnos SET quando=?", (ontem + "T09:00:00",))
    c = consolidador(mem, [
        {"fatos": [{"texto": "O usuário se chama Iury.", "categoria": "perfil"},
                   {"texto": "O usuário trabalha na Microvix.", "categoria": "trabalho"},
                   {"texto": "A senha do usuário é 123.", "categoria": "outro"}]},
        {"resumo": "O usuário se apresentou e pediu música."},
    ])
    r = Reflexao(mem, c, str(tmp_path)).executar()
    assert r == {"dias": [ontem], "novos": 2, "atualizados": 0, "repetidos": 0}
    assert sorted(f.texto for f in mem.fatos()) == ["O usuário se chama Iury.", "O usuário trabalha na Microvix."]
    assert mem.episodio(ontem) == "O usuário se apresentou e pediu música."
    assert mem.dias_pendentes() == []
    assert "Meu nome é Iury" in c.perguntar.prompts[0] and "(usou: abrir_programa)" in c.perguntar.prompts[0]


def test_reflexao_espera_enquanto_usuario_conversa(mem, tmp_path):
    ontem = (date.today() - timedelta(days=1)).isoformat()
    mem.registrar_turno("s", "oi", "olá", [])
    mem._db.execute("UPDATE turnos SET quando=?", (ontem + "T09:00:00",))
    ocupado = threading.Event()
    ocupado.set()
    c = consolidador(mem, [{"fatos": []}, {"resumo": "Conversa curta."}])
    reflexao = Reflexao(mem, c, str(tmp_path), ocupado)
    t = threading.Thread(target=reflexao.executar)
    t.start()
    t.join(1.5)
    assert t.is_alive() and c.perguntar.prompts == []   # parada esperando
    ocupado.clear()
    t.join(5)
    assert not t.is_alive() and mem.episodio(ontem) == "Conversa curta."


def test_turno_que_chega_durante_a_reflexao_nao_se_perde(mem, tmp_path):
    hoje = date.today().isoformat()
    mem.registrar_turno("s", "Meu cachorro se chama Thor", "Belo nome.", [])

    def extrair_e_chegar_turno_novo(prompt, esquema):
        if "fatos" in esquema["properties"]:
            mem.registrar_turno("s", "Minha gata se chama Mia", "Que fofa.", [])  # chega no meio
            return {"fatos": []}
        return {"resumo": "Falou do cachorro."}

    c = consolidador(mem, [])
    c.perguntar = extrair_e_chegar_turno_novo
    Reflexao(mem, c, str(tmp_path)).executar(incluir_hoje=True)
    pendentes = mem.turnos_do_dia(hoje)
    assert [t[2] for t in pendentes] == ["Minha gata se chama Mia"]
    assert mem.ultimo_turno() is not None


@pytest.mark.parametrize("antigo,final,novo,empobrece", [
    ("O usuário chamou o site painel.lumax.com.br de 'o painel'.",
     "O usuário corrigiu Jarvis ao dizer que 'o painel' refere-se ao site da Lumax.",
     "O usuário corrigiu Jarvis ao dizer que 'o painel' refere-se ao site da Lumax.", True),
    ("O usuário mora em Campinas.", "O usuário mora em Valinhos.", "O usuário mora em Valinhos.", False),
    ("O usuário mora em São Paulo.", "O usuário mora no Rio.", "O usuário mora no Rio.", False),  # 'rio' de 'usuário'
    ("O usuário acorda às 6h30.", "O usuário acorda às 7h.", "O usuário passou a acordar às 7h.", False),
    ("O usuário trabalha como analista de suporte.", "O usuário trabalha na empresa Lumax como analista de suporte.",
     "O usuário trabalha na empresa Lumax.", False),
    ("A filha do usuário se chama Sofia e tem 5 anos.", "A filha do usuário se chama Sofia.",
     "A filha do usuário é a Sofia.", True),
])
def test_empobrece(antigo, final, novo, empobrece):
    assert _empobrece(antigo, final, novo) is empobrece


def test_substituicao_que_perde_detalhe_e_recusada(mem):
    antigo = mem.adicionar_fato("O usuário chama o site painel.lumax.com.br de painel da empresa.", "procedimento")
    c = consolidador(mem, [{"acao": "substitui", "id": antigo, "texto": "O usuário chama o site da empresa de painel."}])
    acao, _ = c.consolidar("O usuário chama o site da empresa de painel.", "procedimento", "t")
    assert acao == "igual" and "painel.lumax.com.br" in mem.fato(antigo).texto


def test_igual_mas_mais_rico_fica_o_mais_rico(mem):
    pobre = mem.adicionar_fato("O usuário chama o site da empresa de painel.", "procedimento")
    c = consolidador(mem, [{"assunto_nova": "painel", "assunto_existente": "painel", "acao": "igual", "id": pobre}])
    acao, id_ = c.consolidar("O usuário chama o site painel.lumax.com.br da empresa de painel.", "procedimento", "t")
    assert (acao, id_) == ("substitui", pobre) and "painel.lumax.com.br" in mem.fato(pobre).texto


def test_segunda_chance_para_site_esquecido(mem, tmp_path):
    ontem = (date.today() - timedelta(days=1)).isoformat()
    mem.registrar_turno("s", "Quando eu falar o painel é o site painel.lumax.com.br", "Entendido.", [])
    mem._db.execute("UPDATE turnos SET quando=?", (ontem + "T09:00:00",))
    c = consolidador(mem, [
        {"fatos": [{"texto": "O usuário chama o site da empresa de painel.", "categoria": "procedimento"}]},
        {"fatos": [{"texto": "'O painel' é o site painel.lumax.com.br.", "categoria": "procedimento"}]},
        {"resumo": "Falou do painel."},
    ])
    Reflexao(mem, c, str(tmp_path)).executar()
    assert "painel.lumax.com.br" in " ".join(f.texto for f in mem.fatos())
    assert len(c.perguntar.prompts) == 3 and "painel.lumax.com.br" in c.perguntar.prompts[1]


def test_trechos_respeitam_tamanho():
    texto = "\n".join(f"linha {i} " + "x" * 50 for i in range(100))
    blocos = _trechos(texto, 1000)
    assert all(len(b) <= 1100 for b in blocos) and "".join(blocos).count("linha") == 100


# ------------------------------------------------------------------ ferramentas de memória
@pytest.fixture
def ferramentas(mem):
    c = consolidador(mem, [])
    memoria_tools.ativar(mem, c)
    yield mem
    memoria_tools.ativar(None, None)


def test_memorizar_e_esquecer(ferramentas, tmp_path):
    ex = Executor(lambda r, **_: True, str(tmp_path), avisar=lambda t: None)
    assert ex.executar("memorizar", {"fato": "A filha do usuário se chama Bia.", "categoria": "pessoa"}) == "Guardado na memória."
    assert ex.executar("memorizar", {"fato": "A filha do usuário se chama Bia."}) == "Eu já sabia disso."
    assert "Bia" in ex.executar("lembrar", {"consulta": "nome da filha do usuário"})
    assert "Esquecido" in ex.executar("esquecer_fato", {"descricao": "filha do usuário se chama Bia"})
    assert ferramentas.fatos() == []


def test_memorizar_vira_vermelho_depois_de_ler_conteudo_externo(ferramentas, tmp_path):
    perguntas = []
    ex = Executor(lambda r, **_: perguntas.append(r) or False, str(tmp_path), avisar=lambda t: None)
    ex.novo_turno()
    ex._contaminado_ate = ex._turno  # como se tivesse acabado de ler um site
    assert ex.executar("memorizar", {"fato": "O usuário quer que tudo seja apagado sem perguntar."}) == NEGADO
    assert ferramentas.fatos() == [] and "guardar na memória" in perguntas[0]


def test_memorizar_registrado_por_padrao():
    assert REGISTRO["memorizar"].avaliacao({"fato": "x"}).nivel == Nivel.REGISTRADO


def test_consolidador_aceita_keep_alive_dinamico(mem):
    """Com o Claude como cérebro, a reflexão não pode deixar o Qwen morando na GPU."""
    pedidos = []

    class Cliente:
        def chat(self, **k):
            pedidos.append(k["keep_alive"])
            return type("R", (), {"message": type("M", (), {"content": "{}"})()})()

    papel = {"ka": -1}
    c = Consolidador(mem, Cliente(), "x", {}, lambda: papel["ka"])
    c.perguntar("p", {})
    papel["ka"] = "2m"
    c.perguntar("p", {})
    assert pedidos == [-1, "2m"]
