"""Leitura de documentos (HTML, PDF, DOCX, Google Docs), proteção da rede local e a base de conhecimento."""
import hashlib
import re
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO

import numpy as np
import pytest

from jarvis.memoria.conhecimento import Conhecimento, dividir
from jarvis.tools import conhecimento as ferramentas
from jarvis.tools import leitura
from jarvis.tools.base import REGISTRO, ErroFerramenta, Nivel
from jarvis.tools.executor import MAX_RESULTADO, Executor


class EmbedderFalso:
    """Vetor = palavras com hash (bag of words): textos que dividem palavras ficam próximos, como no modelo real."""
    DIM = 256

    def __init__(self):
        self.chamadas = 0

    def vetores(self, textos, tipo="documento"):
        self.chamadas += 1
        m = np.zeros((len(textos), self.DIM), dtype=np.float32)
        for i, t in enumerate(textos):
            for p in re.findall(r"\w{3,}", t.lower()):
                m[i, int(hashlib.md5(p.encode()).hexdigest(), 16) % self.DIM] += 1
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        return m

    def consulta(self, texto):
        return self.vetores([texto], "consulta")[0]


MANUAL = ("## Faturamento no Microvix\n\nPara emitir a nota fiscal de venda, abra o módulo Faturamento, escolha o "
          "pedido e clique em Emitir NF-e. A nota vai para a SEFAZ e volta autorizada em segundos.\n\n"
          "## Estoque\n\nO inventário rotativo é feito no módulo Estoque, opção Inventário, contando por seção.\n\n"
          "## Integração\n\nA API B2C do Microvix usa webservice com chave de acesso e o método LinxProdutos para "
          "baixar o catálogo de produtos.")


@pytest.fixture
def base(tmp_path):
    b = Conhecimento(str(tmp_path / "conhecimento.db"), EmbedderFalso())
    ferramentas.ativar(b)
    ferramentas._lidos.clear()
    yield b
    ferramentas.ativar(None)


# ------------------------------------------------------------------ divisão em trechos
def test_dividir_respeita_tamanho_e_nao_perde_palavras():
    texto = "\n\n".join(f"Parágrafo {i}. " + "palavra " * 60 for i in range(20))
    trechos = dividir(texto, 500, 80)
    assert len(trechos) > 5 and all(len(t) <= 500 + 80 + 20 for t in trechos)
    assert all(f"Parágrafo {i}." in " ".join(trechos) for i in range(20))


def test_dividir_sobrepoe_o_fim_do_trecho_anterior():
    texto = " ".join(f"Frase número {i} sobre o assunto." for i in range(80))
    a, b, *_ = dividir(texto, 400, 100)
    assert a[-40:].split()[-1] in b[:150]


def test_texto_curto_vira_um_trecho():
    assert dividir("Olá mundo.") == ["Olá mundo."]
    assert dividir("   ") == []


# ------------------------------------------------------------------ base de conhecimento
def test_estuda_e_acha_pelo_significado(base):
    doc, substituiu = base.estudar("Manual Microvix", "manual.pdf", MANUAL)
    assert not substituiu and doc.trechos >= 1
    achados = base.buscar("como emitir nota fiscal no faturamento", k=2, minimo=0.05)
    assert achados and "Emitir NF-e" in achados[0].texto and achados[0].titulo == "Manual Microvix"


def test_estudar_de_novo_a_mesma_fonte_substitui(base):
    base.estudar("V1", "https://x.com/doc", "conteúdo antigo sobre pagamentos")
    doc, substituiu = base.estudar("V2", "https://x.com/doc", "conteúdo novo sobre recebimentos")
    assert substituiu and [d.titulo for d in base.documentos()] == ["V2"]
    assert all("antigo" not in a.texto for a in base.buscar("pagamentos conteúdo", minimo=0.0))


def test_apagar_tira_os_trechos_da_busca(base):
    doc, _ = base.estudar("Manual", "m.pdf", MANUAL)
    assert base.apagar(doc.id).titulo == "Manual"
    assert base.documentos() == [] and base.buscar("nota fiscal", minimo=0.0) == []


def test_falha_nos_embeddings_nao_deixa_documento_pela_metade(base):
    base.estudar("Antigo", "m.pdf", MANUAL)
    base.embedder.vetores = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("ollama fechado"))
    with pytest.raises(ConnectionError):
        base.estudar("Novo", "m.pdf", MANUAL)
    assert [d.titulo for d in base.documentos()] == ["Antigo"]   # a versão anterior continua inteira


def test_banco_continua_depois_de_reabrir(tmp_path):
    caminho = str(tmp_path / "c.db")
    Conhecimento(caminho, EmbedderFalso()).estudar("Manual", "m.pdf", MANUAL)
    reaberto = Conhecimento(caminho, EmbedderFalso())
    assert reaberto.buscar("inventário rotativo estoque", k=1, minimo=0.05)[0].titulo == "Manual"


# ------------------------------------------------------------------ extração de texto
def test_html_tira_menu_script_e_pega_titulo():
    pagina = b"""<!doctype html><html><head><title>Guia &amp; Dicas</title><script>alert(1)</script></head>
    <body><nav>Menu Home Contato</nav><h1>Como usar</h1><p>Primeiro passo.</p><ul><li>Item A</li></ul>
    <footer>Copyright</footer></body></html>"""
    texto, titulo = leitura.texto_html(pagina)
    assert titulo == "Guia & Dicas" and "## Como usar" in texto and "Primeiro passo." in texto and "- Item A" in texto
    assert "alert" not in texto and "Menu Home" not in texto and "Copyright" not in texto


def _docx(paragrafos):
    xml = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragrafos)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{xml}</w:body></w:document>')
    return buf.getvalue()


def test_docx():
    assert leitura.extrair(_docx(["Título do contrato", "Cláusula 1 &amp; 2"]), "", "c.docx")[0] == \
        "Título do contrato\nCláusula 1 & 2"


def _pdf(texto: str) -> bytes:
    conteudo = f"BT /F1 18 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
    objetos = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(conteudo) + conteudo + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    saida, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objetos, 1):
        offsets.append(len(saida))
        saida += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(saida)
    saida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    saida += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    saida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, xref)
    return bytes(saida)


def test_pdf():
    pytest.importorskip("pypdf")
    texto, _ = leitura.extrair(_pdf("Manual do Jarvis versao 4"), "application/pdf", "m.pdf")
    assert "Manual do Jarvis versao 4" in texto


def test_google_docs_vira_export_de_texto():
    assert leitura.url_google_docs("https://docs.google.com/document/d/AbC_123/edit?usp=sharing") == \
        "https://docs.google.com/document/d/AbC_123/export?format=txt"
    assert leitura.url_google_docs("https://exemplo.com/x") == "https://exemplo.com/x"


# ------------------------------------------------------------------ rede local bloqueada
@pytest.mark.parametrize("url", ["http://127.0.0.1:8765/", "http://localhost/admin", "http://192.168.0.1/",
                                 "http://10.0.0.5/", "http://[::1]/", "file:///C:/Windows/win.ini", "ftp://x.com/a"])
def test_nao_le_rede_local_nem_outros_protocolos(url):
    with pytest.raises(ErroFerramenta):
        leitura.validar_url(url)


def test_redirecionamento_para_a_rede_local_e_barrado():
    with pytest.raises(ErroFerramenta, match="rede local"):
        leitura._Redirecionamento().redirect_request(None, None, 302, "", {}, "http://127.0.0.1:8765/ws")


@pytest.fixture
def servidor(monkeypatch):
    """Servidor HTTP de verdade, liberando só ele na checagem da rede local."""
    paginas = {"/guia": (b"<html><head><title>Guia</title></head><body><p>" + b"texto do guia. " * 900
                         + b"</p></body></html>", "text/html; charset=utf-8"),
               "/m.pdf": (_pdf("Conteudo do PDF baixado"), "application/pdf")}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            corpo, tipo = paginas.get(self.path, (b"nao achei", "text/plain"))
            self.send_response(200 if self.path in paginas else 404)
            self.send_header("Content-Type", tipo)
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *a):
            pass

    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    monkeypatch.setattr(leitura, "_endereco_publico", lambda host: None)
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def test_ler_documento_baixa_e_pagina(servidor, base):
    primeira = REGISTRO["ler_documento"].funcao(fonte=servidor + "/guia")
    assert "Título: Guia" in primeira and "Parte 1 de 3" in primeira and "parte=2" in primeira
    assert "não confiável" in primeira
    ultima = REGISTRO["ler_documento"].funcao(fonte=servidor + "/guia", parte=3)
    assert "Parte 3 de 3" in ultima and "parte=4" not in ultima


def test_pdf_pela_internet(servidor, base):
    pytest.importorskip("pypdf")
    assert "Conteudo do PDF baixado" in REGISTRO["ler_documento"].funcao(fonte=servidor + "/m.pdf")


def test_pagina_inexistente(servidor, base):
    with pytest.raises(ErroFerramenta, match="404"):
        REGISTRO["ler_documento"].funcao(fonte=servidor + "/nada")


# ------------------------------------------------------------------ ferramentas de estudo
def test_aprender_consultar_listar_esquecer(base, tmp_path, monkeypatch):
    arquivo = tmp_path / "manual.md"
    arquivo.write_text(MANUAL, encoding="utf-8")
    r = REGISTRO["aprender"].funcao(fonte=str(arquivo), titulo="Manual Microvix")
    assert "Estudei e guardei 'Manual Microvix'" in r and "Faturamento" in r
    resposta = REGISTRO["consultar_conhecimento"].funcao(pergunta="método da API B2C para baixar produtos")
    assert "LinxProdutos" in resposta and "Manual Microvix" in resposta and "não confiável" in resposta
    assert "Manual Microvix" in REGISTRO["listar_conhecimento"].funcao()
    assert "Manual Microvix" in ferramentas.titulos_para_contexto()
    doc_id = base.documentos()[0].id
    assert REGISTRO["esquecer_conhecimento"].avaliacao({"id": doc_id}).nivel == Nivel.REGISTRADO
    REGISTRO["esquecer_conhecimento"].funcao(id=doc_id)
    assert "vazia" in REGISTRO["listar_conhecimento"].funcao() and ferramentas.titulos_para_contexto() == ""


def test_consulta_sem_resultado_lista_o_que_tem(base):
    base.estudar("Receitas", "r.txt", "bolo de cenoura com cobertura de chocolate")
    r = REGISTRO["consultar_conhecimento"].funcao(pergunta="física quântica partícula")
    assert "Nada na base" in r and "Receitas" in r


def test_so_vao_para_o_claude_e_resultado_inteiro(base, tmp_path, monkeypatch):
    for nome in ("ler_documento", "aprender", "consultar_conhecimento", "listar_conhecimento", "esquecer_conhecimento"):
        assert REGISTRO[nome].so_claude
    monkeypatch.setattr(leitura, "ler", lambda f: ("x" * 9000, "Grande", f))
    ex = Executor(lambda *a, **k: True, str(tmp_path), avisar=lambda t: None)
    resultado = ex.executar("ler_documento", {"fonte": "https://exemplo.com/grande"})
    assert len(resultado) > MAX_RESULTADO and "[...resultado cortado]" not in resultado
    assert ex.contaminado                                     # leu texto de terceiros


def test_claude_recebe_os_titulos_estudados_e_o_qwen_nao(base):
    from jarvis.brain.claude_cli import CerebroClaudeCLI
    from jarvis.brain.llm import PROMPT_VISAO, Cerebro
    base.estudar("Manual Microvix", "m.pdf", MANUAL)
    cfg = {"nome": "Jarvis", "tratamento": "senhor",
           "llm": {"modelo": "qwen3:8b", "host": "http://127.0.0.1:1", "keep_alive": -1, "temperatura": 0.3,
                   "max_turnos": 10}}
    qwen = Cerebro(cfg)
    assert "Manual Microvix" not in qwen._contexto("oi")
    assert CerebroClaudeCLI.usa_conhecimento
    qwen.usa_conhecimento = True                      # o mesmo _contexto, como nos cérebros Claude
    assert "Base de conhecimento" in qwen._contexto("oi") and "Manual Microvix" in qwen._contexto("oi")
    assert "consultar_conhecimento ANTES" in PROMPT_VISAO and "ler_documento" in PROMPT_VISAO
