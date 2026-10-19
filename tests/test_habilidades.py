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
    assert {"estudo-guiado", "triagem-de-email"} <= nomes


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


def test_link_que_nao_e_skill_e_recusado(pasta, tmp_path, monkeypatch):
    monkeypatch.setattr(leitura, "baixar", lambda u: (b"<html>pagina</html>", "text/html", u))
    ex, perguntas = executor(tmp_path, [])
    assert "SKILL.md" in ex.executar("instalar_habilidade", {"url": "https://exemplo.com/x"}) and perguntas == []


def test_ferramentas_de_habilidade_so_para_o_claude():
    for nome in ("usar_habilidade", "ler_arquivo_habilidade", "criar_habilidade", "instalar_habilidade",
                 "listar_habilidades", "esquecer_habilidade"):
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
