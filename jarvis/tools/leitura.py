"""Ler documentos de verdade: páginas da internet, PDFs, Google Docs públicos e arquivos locais (txt, md, pdf, docx).
Devolve só o texto, sem menu, script nem propaganda.

Segurança da internet:
  - só http/https, e NUNCA endereços da rede local (127.0.0.1, 192.168.x, roteador...), nem depois de
    redirecionamento: um site não pode usar o Jarvis para mexer na HUD ou no roteador;
  - no máximo 8 MB e 20 s por download.
O texto lido é de terceiros: quem chama marca como conteúdo externo.
"""
import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

from jarvis.tools.base import ErroFerramenta

MAX_BYTES = 8_000_000
TIMEOUT_S = 20
NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0 Safari/537.36")
EXTENSOES_LOCAIS = {".txt", ".md", ".csv", ".json", ".log", ".html", ".htm", ".pdf", ".docx", ".py", ".js", ".sql",
                    ".xml", ".yaml", ".yml", ".ini"}


# ------------------------------------------------------------------ rede
def _endereco_publico(host: str):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ErroFerramenta(f"não achei o site '{host}' (endereço errado ou sem internet)")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ErroFerramenta(f"'{host}' aponta para a rede local; por segurança eu não leio endereços internos")


def validar_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"(?i)^https?://", url):
        url = "https://" + url
    partes = urllib.parse.urlparse(url)
    if partes.scheme.lower() not in ("http", "https") or not partes.hostname:
        raise ErroFerramenta("só leio endereços http ou https")
    _endereco_publico(partes.hostname)
    return url


class _Redirecionamento(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, novo):
        validar_url(novo)                    # o destino do redirecionamento passa pela mesma checagem
        return super().redirect_request(req, fp, code, msg, headers, novo)


_ABRIR = urllib.request.build_opener(_Redirecionamento())


def url_google_docs(url: str) -> str:
    """Docs/Planilhas/Apresentações públicos do Google: a página é só JavaScript; o export dá o texto."""
    m = re.match(r"https?://docs\.google\.com/(document|spreadsheets|presentation)/d/([\w-]+)", url)
    if not m:
        return url
    tipo, id_ = m.groups()
    formato = {"document": "txt", "spreadsheets": "csv", "presentation": "txt"}[tipo]
    return f"https://docs.google.com/{tipo}/d/{id_}/export?format={formato}"


def baixar(url: str) -> tuple[bytes, str, str]:
    """(conteúdo, tipo, endereço final)."""
    url = url_google_docs(validar_url(url))
    req = urllib.request.Request(url, headers={"User-Agent": NAVEGADOR, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"})
    try:
        with _ABRIR.open(req, timeout=TIMEOUT_S) as r:
            dados = r.read(MAX_BYTES + 1)
            tipo = r.headers.get_content_type() or ""
            final = r.geturl()
    except urllib.error.HTTPError as e:
        raise ErroFerramenta(f"o site respondeu com erro {e.code}"
                             + (" (pede login: só leio páginas públicas)" if e.code in (401, 403) else ""))
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        raise ErroFerramenta(f"não consegui abrir a página ({getattr(e, 'reason', e)})")
    if len(dados) > MAX_BYTES:
        raise ErroFerramenta("o documento passa de 8 MB; peça uma parte menor")
    return dados, tipo, final


# ------------------------------------------------------------------ extração
class _Texto(HTMLParser):
    IGNORAR = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "button", "iframe",
               "template"}
    BLOCOS = {"p", "div", "section", "article", "main", "br", "li", "tr", "table", "ul", "ol", "blockquote", "pre",
              "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self.titulo = ""
        self._ignorando = 0
        self._no_titulo = False

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORAR:
            self._ignorando += 1
        elif tag == "title":
            self._no_titulo = True
        elif tag in self.BLOCOS:
            self.partes.append("\n")
            if tag in ("h1", "h2", "h3"):
                self.partes.append("## ")
            elif tag == "li":
                self.partes.append("- ")

    def handle_endtag(self, tag):
        if tag in self.IGNORAR and self._ignorando:
            self._ignorando -= 1
        elif tag == "title":
            self._no_titulo = False
        elif tag in self.BLOCOS:
            self.partes.append("\n")

    def handle_data(self, data):
        if self._no_titulo:
            self.titulo += data
        elif not self._ignorando:
            self.partes.append(data)


def _arrumar(texto: str) -> str:
    texto = re.sub(r"[ \t ]+", " ", texto)
    linhas = [l.strip() for l in texto.splitlines()]
    texto = "\n".join(l for l in linhas if l not in ("-", "## "))
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def texto_html(dados: bytes) -> tuple[str, str]:
    p = _Texto()
    p.feed(_decodificar(dados))
    return _arrumar("".join(p.partes)), _arrumar(html.unescape(p.titulo))


def texto_pdf(dados: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ErroFerramenta("falta a biblioteca pypdf para ler PDF (pip install -r requirements.lock)")
    try:
        leitor = PdfReader(BytesIO(dados))
        paginas = [p.extract_text() or "" for p in leitor.pages]
    except Exception as e:
        raise ErroFerramenta(f"não consegui ler o PDF ({type(e).__name__})")
    texto = _arrumar("\n\n".join(paginas))
    if not texto:
        raise ErroFerramenta("o PDF não tem texto (é só imagem escaneada); não consigo ler sem OCR")
    return texto


def texto_docx(dados: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(dados)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        raise ErroFerramenta("o arquivo .docx está corrompido")
    paragrafos = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        trechos = re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>", p, flags=re.S)
        paragrafos.append(html.unescape("".join(trechos)))
    return _arrumar("\n".join(paragrafos))


def _decodificar(dados: bytes) -> str:
    for cod in ("utf-8", "cp1252"):
        try:
            return dados.decode(cod)
        except UnicodeDecodeError:
            continue
    return dados.decode("utf-8", "replace")


def extrair(dados: bytes, tipo: str, nome: str) -> tuple[str, str]:
    """(texto, título sugerido) a partir do conteúdo, pelo tipo ou pela extensão do nome."""
    ext = Path(urllib.parse.urlparse(nome).path).suffix.lower()
    if tipo == "application/pdf" or ext == ".pdf" or dados[:5] == b"%PDF-":
        return texto_pdf(dados), ""
    if ext == ".docx" or tipo.endswith("wordprocessingml.document"):
        return texto_docx(dados), ""
    if "html" in tipo or ext in (".html", ".htm") or dados.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return texto_html(dados)
    return _arrumar(_decodificar(dados)), ""


def ler(fonte: str) -> tuple[str, str, str]:
    """(texto, título, origem) de um endereço da internet ou de um arquivo deste computador."""
    fonte = fonte.strip().strip('"')
    local = None
    if not re.match(r"(?i)^https?://", fonte):
        try:
            from jarvis.tools.caminhos import resolver     # entende "documentos/relatório.pdf", OneDrive etc.
            candidato = resolver(fonte)
        except Exception:
            candidato = Path(fonte).expanduser()
        if candidato.exists() or re.match(r"^[a-zA-Z]:[\\/]|^[\\/.~]", fonte):
            local = candidato
    if local is not None:
        if not local.is_file():
            raise ErroFerramenta(f"o arquivo {local} não existe")
        if local.suffix.lower() not in EXTENSOES_LOCAIS:
            raise ErroFerramenta(f"ainda não sei ler arquivos {local.suffix}")
        if local.stat().st_size > MAX_BYTES:
            raise ErroFerramenta("o arquivo passa de 8 MB")
        texto, titulo = extrair(local.read_bytes(), "", local.name)
        return texto, titulo or local.stem, str(local)
    dados, tipo, final = baixar(fonte)
    texto, titulo = extrair(dados, tipo, final)
    if not texto:
        raise ErroFerramenta("a página não tem texto legível (pode depender de JavaScript ou de login)")
    return texto, titulo or urllib.parse.urlparse(final).netloc, final
