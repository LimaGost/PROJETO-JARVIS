"""Habilidades (formato Claude Skills): ler, criar, usar, instalar por link, esquecer; e leitura de vídeos do YouTube."""
import sys
import types

import pytest

from jarvis.tools import habilidades as hab
from jarvis.tools import leitura
from jarvis.tools.base import REGISTRO, ErroFerramenta, Nivel
from jarvis.tools.executor import NEGADO, Executor

SKILL_PDF = """---
name: pdf
description: Use para extrair texto e tabelas de PDFs, juntar e anotar arquivos PDF.
license: Proprietary
---

# PDF

1. Leia o arquivo com ler_documento.
2. Extraia as tabelas.
"""


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(hab, "PASTA", tmp_path / "habilidades")
    monkeypatch.setattr(hab, "APAGADAS", tmp_path / "apagadas")
    hab._baixadas.clear()
    return tmp_path / "habilidades"


def executor(tmp_path, respostas):
    perguntas = []
    return Executor(lambda resumo, **k: perguntas.append(resumo) or respostas.pop(0), str(tmp_path / "logs"),
                    avisar=lambda t: None), perguntas


# ------------------------------------------------------------------ formato
def test_le_skill_no_formato_da_anthropic():
    meta, corpo = hab.ler_skill(SKILL_PDF)
    assert meta["name"] == "pdf" and "PDFs" in meta["description"] and corpo.startswith("# PDF")


@pytest.mark.parametrize("texto", ["# sem cabeçalho", "---\nname: x\n---\ncorpo", "---\n: [quebrado\n---\nx"])
def test_skill_invalida_e_recusada(texto):
    with pytest.raises(ErroFerramenta):
        hab.ler_skill(texto)


def test_habilidades_que_vem_com_o_jarvis_sao_validas():
    nomes = {h["nome"] for h in hab.todas()}
    assert {"estudo-guiado", "triagem-de-email", "depuracao-sistematica", "verificar-antes-de-concluir"} <= nomes


def test_contexto_mostra_so_nome_e_descricao(pasta):
    hab.criar_habilidade("Relatório de vendas", "Quando o usuário pedir o relatório diário de vendas da loja.",
                         "1. Passo secreto e longo que não deve ir no contexto.")
    ctx = hab.para_contexto()
    assert "Relatório de vendas: Quando o usuário pedir" in ctx and "Passo secreto" not in ctx


# ------------------------------------------------------------------ criar e usar
def test_criar_usar_e_atualizar(pasta):
    r = hab.criar_habilidade("Fechamento de caixa", "Quando o usuário pedir para conferir o fechamento de caixa.",
                             "1. Abra o Microvix.\n2. Confira o relatório.")
    assert "criada" in r and (pasta / "fechamento-de-caixa" / "SKILL.md").exists()
    texto = REGISTRO["usar_habilidade"].funcao(nome="fechamento de caixa")
    assert "Abra o Microvix" in texto and "instalada de fora" not in texto      # ensinada pelo usuário: confiável
    hab.criar_habilidade("Fechamento de caixa", "Quando o usuário pedir para conferir o fechamento de caixa.",
                         "1. Abra o Microvix.\n2. Confira o relatório.\n3. Mande o resumo por e-mail.")
    assert len(hab.todas()) == 1 and "Mande o resumo" in REGISTRO["usar_habilidade"].funcao(nome="fechamento-de-caixa")


def test_criar_e_amarelo_e_vira_vermelho_depois_de_ler_conteudo_externo(pasta, tmp_path):
    ex, perguntas = executor(tmp_path, [False])
    args = {"nome": "X", "descricao": "Quando o usuário pedir a tarefa X do trabalho.", "instrucoes": "faça X"}
    assert REGISTRO["criar_habilidade"].avaliacao(args).nivel == Nivel.REGISTRADO
    ex.novo_turno()
    ex._contaminado_ate = ex._turno + 2                  # acabou de ler um site
    assert ex.executar("criar_habilidade", args) == NEGADO and not pasta.exists()  # um site não planta procedimento


def test_descricao_vaga_e_recusada(pasta):
    with pytest.raises(ErroFerramenta, match="QUANDO"):
        REGISTRO["criar_habilidade"].avaliacao({"nome": "x", "descricao": "faz coisa", "instrucoes": "y"})


def test_arquivos_de_apoio_e_bloqueio_de_fuga_de_pasta(pasta):
    hab.criar_habilidade("Guia", "Quando o usuário pedir o guia de implantação do sistema.", "Veja references/a.md")
    (pasta / "guia" / "references").mkdir()
    (pasta / "guia" / "references" / "a.md").write_text("conteúdo de apoio", encoding="utf-8")
    assert "references/a.md" in REGISTRO["usar_habilidade"].funcao(nome="guia")
    assert REGISTRO["ler_arquivo_habilidade"].funcao(nome="guia", arquivo="references/a.md") == "conteúdo de apoio"
    with pytest.raises(ErroFerramenta):
        REGISTRO["ler_arquivo_habilidade"].funcao(nome="guia", arquivo="../../config.yaml")


def test_habilidade_inexistente_lista_as_que_existem(pasta):
    hab.criar_habilidade("Uma", "Quando o usuário pedir a primeira tarefa de exemplo.", "x")
    with pytest.raises(ErroFerramenta, match="Uma"):
        hab.achar("outra")


def test_esquecer_guarda_para_recuperar(pasta, tmp_path):
    hab.criar_habilidade("Temporária", "Quando o usuário pedir algo temporário de teste.", "x")
    assert "Esqueci" in REGISTRO["esquecer_habilidade"].funcao(nome="temporaria")
    assert hab.todas() == [] and any((tmp_path / "apagadas").iterdir())


# ------------------------------------------------------------------ instalar por link
@pytest.mark.parametrize("url,bruta", [
    ("https://github.com/anthropics/skills/tree/main/skills/pdf",
     "https://raw.githubusercontent.com/anthropics/skills/main/skills/pdf/SKILL.md"),
    ("https://github.com/anthropics/skills/blob/main/skills/pdf/SKILL.md",
     "https://raw.githubusercontent.com/anthropics/skills/main/skills/pdf/SKILL.md"),
    ("https://exemplo.com/minha/SKILL.md", "https://exemplo.com/minha/SKILL.md"),
])
def test_link_do_github_vira_arquivo_puro(url, bruta):
    assert hab.url_bruta(url) == bruta


def test_instalar_sempre_confirma_e_instala_o_que_foi_mostrado(pasta, tmp_path, monkeypatch):
    baixados = []
    monkeypatch.setattr(leitura, "baixar", lambda u: baixados.append(u) or (SKILL_PDF.encode(), "text/plain", u))
    url = "https://github.com/anthropics/skills/tree/main/skills/pdf"
    ex, perguntas = executor(tmp_path, [False, True])
    assert ex.executar("instalar_habilidade", {"url": url}) == NEGADO and not pasta.exists()
    assert "instalar a habilidade 'pdf'" in perguntas[0] and "PDFs" in perguntas[0]
    ex.novo_turno()
    assert "instalada" in ex.executar("instalar_habilidade", {"url": url})
    h = hab.achar("pdf")
    assert h["origem"] == "instalada" and "instalada de fora" in REGISTRO["usar_habilidade"].funcao(nome="pdf")
    assert baixados.count(baixados[0]) == 2       # 1 por confirmação; a instalação usa o texto já conferido


def _github_falso(arquivos: dict[str, bytes], skill: str = SKILL_PDF, pasta: str = "skills/pdf"):
    """Simula raw.githubusercontent + API de conteúdo do GitHub para uma pasta de skill."""
    import json
    pedidos = []

    def baixar(u):
        pedidos.append(u)
        if "raw.githubusercontent.com" in u:
            if u.split("/", 6)[-1] == f"{pasta}/SKILL.md":
                return skill.encode(), "text/plain", u
            raise ErroFerramenta("o site respondeu com erro 404")
        if u.startswith("https://api.github.com/"):
            caminho = u.split("/contents/", 1)[1].split("?")[0]
            itens = []
            for rel, dados in arquivos.items():
                completo = f"{pasta}/{rel}"
                if completo.rsplit("/", 1)[0] == caminho:
                    itens.append({"path": completo, "type": "file", "size": len(dados), "download_url": "dl:" + completo})
            dirs = {f"{pasta}/{r.split('/')[0]}" for r in arquivos if "/" in r}
            itens += [{"path": d, "type": "dir"} for d in dirs if d.rsplit("/", 1)[0] == caminho]
            return json.dumps(itens).encode(), "application/json", u
        if u.startswith("dl:"):
            return arquivos[u[3 + len(pasta) + 1:]], "text/plain", u
        raise AssertionError(u)
    return baixar, pedidos


def test_instala_a_pasta_inteira_mas_nao_os_scripts(pasta, tmp_path, monkeypatch):
    baixar, _ = _github_falso({"reference.md": b"guia de apoio", "forms/campos.md": b"campos",
                               "scripts/extrair.py": b"import os", "logo.png": b"\x89PNG"})
    monkeypatch.setattr(leitura, "baixar", baixar)
    ex, perguntas = executor(tmp_path, [True])
    r = ex.executar("instalar_habilidade", {"url": "https://github.com/anthropics/skills/tree/main/skills/pdf"})
    assert "2 arquivos de texto de apoio" in perguntas[0] and "ficam de fora" in perguntas[0]
    assert "com 2 arquivos de apoio" in r and "scripts/extrair.py" in r
    assert (pasta / "pdf" / "reference.md").read_text(encoding="utf-8") == "guia de apoio"
    assert (pasta / "pdf" / "forms" / "campos.md").exists() and not (pasta / "pdf" / "scripts").exists()
    assert REGISTRO["ler_arquivo_habilidade"].funcao(nome="pdf", arquivo="forms/campos.md") == "campos"


def test_instala_pelo_id_do_catalogo_procurando_a_pasta(pasta, tmp_path, monkeypatch):
    baixar, pedidos = _github_falso({}, pasta="find-skills")
    monkeypatch.setattr(leitura, "baixar", baixar)
    ex, _ = executor(tmp_path, [True])
    assert "instalada" in ex.executar("instalar_habilidade", {"url": "vercel-labs/skills/find-skills"})
    tentados = [u for u in pedidos if u.endswith("SKILL.md")]
    assert tentados[0].endswith("HEAD/skills/find-skills/SKILL.md") and tentados[1].endswith("HEAD/find-skills/SKILL.md")
    assert any(u.startswith("https://api.github.com/repos/vercel-labs/skills/contents/find-skills") for u in pedidos)


@pytest.mark.parametrize("fonte,primeiro", [
    ("anthropics/skills/pdf", "https://raw.githubusercontent.com/anthropics/skills/HEAD/skills/pdf/SKILL.md"),
    ("https://skills.sh/obra/superpowers/systematic-debugging",
     "https://raw.githubusercontent.com/obra/superpowers/HEAD/skills/systematic-debugging/SKILL.md"),
    ("https://exemplo.com/minha/SKILL.md", "https://exemplo.com/minha/SKILL.md"),
])
def test_candidatos(fonte, primeiro):
    assert hab.candidatos(fonte)[0][0] == primeiro


def test_caminho_malicioso_da_api_nao_sai_da_pasta(pasta, tmp_path, monkeypatch):
    skill = {"meta": {"name": "pdf", "description": "Use para PDFs e coisas assim."}, "corpo": "x",
             "fonte": "f", "apoio": {"../../fora.md": "mal", "ok.md": "bom"}, "ignorados": []}
    hab._baixadas["u"] = skill
    hab.instalar_habilidade("u")
    assert (pasta / "pdf" / "ok.md").exists() and not (tmp_path / "fora.md").exists()


def test_buscar_no_catalogo_ordena_por_instalacoes(monkeypatch):
    import json
    pedidos = []
    resposta = {"skills": [
        {"id": "fulano/x/pdf-tool", "name": "pdf-tool", "installs": 40, "source": "fulano/x"},
        {"id": "anthropics/skills/pdf", "name": "pdf", "installs": 125000, "source": "anthropics/skills"}]}
    monkeypatch.setattr(leitura, "baixar", lambda u: pedidos.append(u) or (json.dumps(resposta).encode(), "", u))
    r = REGISTRO["buscar_habilidade"].funcao(assunto="pdf files")
    assert pedidos[0] == "https://skills.sh/api/search?q=pdf+files&limit=12"
    assert r.index("anthropics/skills/pdf") < r.index("fulano/x/pdf-tool") and "125.000 instalações" in r
    assert "fonte oficial" in r.split("\n")[1] and "não confiável" in r
    assert REGISTRO["buscar_habilidade"].externo and REGISTRO["buscar_habilidade"].so_claude


def test_buscar_sem_resultado(monkeypatch):
    monkeypatch.setattr(leitura, "baixar", lambda u: (b'{"skills": []}', "", u))
    assert "Não achei" in REGISTRO["buscar_habilidade"].funcao(assunto="zzz")


def test_link_que_nao_e_skill_e_recusado(pasta, tmp_path, monkeypatch):
    monkeypatch.setattr(leitura, "baixar", lambda u: (b"<html>pagina</html>", "text/html", u))
    ex, perguntas = executor(tmp_path, [])
    assert "SKILL.md" in ex.executar("instalar_habilidade", {"url": "https://exemplo.com/x"}) and perguntas == []


def test_ferramentas_de_habilidade_so_para_o_claude():
    for nome in ("usar_habilidade", "ler_arquivo_habilidade", "criar_habilidade", "instalar_habilidade",
                 "listar_habilidades", "esquecer_habilidade", "buscar_habilidade"):
        assert REGISTRO[nome].so_claude


# ------------------------------------------------------------------ YouTube
@pytest.mark.parametrize("url,vid", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"), ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ", "dQw4w9WgXcQ"), ("https://exemplo.com/video", None),
])
def test_id_do_youtube(url, vid):
    assert leitura.id_youtube(url) == vid


def test_le_a_legenda_do_video(monkeypatch):
    pedidos = []

    class Api:
        def fetch(self, video, languages):
            pedidos.append((video, list(languages)))
            return [types.SimpleNamespace(text=t) for t in ["[Música]", "olá pessoal", "hoje vamos falar de APIs.",
                                                             "uma API\nREST usa HTTP."]]
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", types.SimpleNamespace(YouTubeTranscriptApi=Api))
    monkeypatch.setattr(leitura, "baixar", lambda u: (b'{"title": "Curso de APIs", "author_name": "Canal Dev"}', "", u))
    texto, titulo, origem = leitura.ler("https://youtu.be/abcdefghijk")
    assert texto == "olá pessoal hoje vamos falar de APIs. uma API REST usa HTTP."
    assert titulo == "Curso de APIs (Canal Dev)" and origem.endswith("v=abcdefghijk")
    assert pedidos == [("abcdefghijk", ["pt-BR", "pt", "en"])]


def test_video_sem_legenda_avisa(monkeypatch):
    class Api:
        def fetch(self, video, languages):
            raise RuntimeError("TranscriptsDisabled")
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", types.SimpleNamespace(YouTubeTranscriptApi=Api))
    with pytest.raises(ErroFerramenta, match="legenda"):
        leitura.ler("https://www.youtube.com/watch?v=abcdefghijk")
