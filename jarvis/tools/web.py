import re
import time
import urllib.parse
import webbrowser

from ddgs import DDGS

from jarvis.tools.base import ErroFerramenta, conteudo_externo, ferramenta

# Os buscadores bloqueiam scraping de vez em quando; tenta vários até um responder.
BACKENDS = ["duckduckgo", "yahoo", "bing", "brave", "google"]
ORCAMENTO_S = 8
_cache: dict[str, tuple[float, str]] = {}


@ferramenta(
    "pesquisar_web",
    "Pesquisa na internet e devolve os principais resultados. Use para fatos atuais, notícias, preços, "
    "cotações, clima, esportes ou qualquer dado que você não tenha certeza.",
    {"consulta": {"type": "string", "description": "O que pesquisar"}},
    obrigatorios=("consulta",),
    externo=True,
)
def pesquisar_web(consulta: str) -> str:
    chave = consulta.strip().lower()
    if chave in _cache and time.time() - _cache[chave][0] < 600:
        return _cache[chave][1]
    ddgs = DDGS(timeout=4)
    limite = time.time() + ORCAMENTO_S
    resultados, falhas = [], []
    for backend in BACKENDS:
        if time.time() > limite:
            break
        try:
            resultados = ddgs.text(consulta, region="br-pt", max_results=5, backend=backend)
            if resultados:
                break
        except Exception as e:
            falhas.append(f"{backend}: {type(e).__name__}")
    if not resultados:
        raise ErroFerramenta("a pesquisa não retornou resultados agora (" + "; ".join(falhas) + ")")
    # resultados da web são DADOS: um site pode tentar dar ordens ao assistente
    linhas = [f"- {r.get('title', '')}: {r.get('body', '')[:220]} ({r.get('href', '')})" for r in resultados]
    texto = conteudo_externo("pesquisa na web", "\n".join(linhas))
    _cache[chave] = (time.time(), texto)
    return texto


@ferramenta(
    "abrir_site",
    "Abre um site no navegador. Recebe um endereço (youtube.com) ou um termo, que vira uma pesquisa no Google.",
    {"endereco": {"type": "string", "description": "URL, domínio ou termo de pesquisa"}},
    obrigatorios=("endereco",),
)
def abrir_site(endereco: str) -> str:
    e = endereco.strip()
    if re.match(r"^https?://", e, re.I):
        url = e
    elif re.match(r"^[\w-]+(\.[\w-]+)+(/\S*)?$", e):
        url = "https://" + e
    elif ":" in e.split("/")[0]:
        raise ErroFerramenta("só abro endereços http ou https")
    else:
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(e)
    webbrowser.open(url)
    return f"Abrindo {url}"
