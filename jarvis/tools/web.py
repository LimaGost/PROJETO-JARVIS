import re
import time
import unicodedata
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor, wait

from ddgs import DDGS

from jarvis.tools.base import ErroFerramenta, conteudo_externo, ferramenta

# Os buscadores bloqueiam scraping de vez em quando; tenta vários até um responder.
BACKENDS = ["duckduckgo", "yahoo", "bing", "brave", "google"]
ORCAMENTO_S = 12
_cache: dict[str, tuple[float, str]] = {}
MAX_PAGINAS = 8
ORCAMENTO_LEITURA_S = 30         # baixar e ler todas as páginas juntas (em paralelo) não passa disso
TOTAL_CARACTERES = 11000         # cabe no limite das ferramentas do Claude (12 mil)
_PALAVRA = re.compile(r"\w{3,}")
_VAZIAS = {"que", "para", "com", "uma", "como", "por", "mais", "dos", "das", "nos", "nas", "sobre", "qual", "quais",
           "the", "and", "for", "what", "how"}


def buscar(consulta: str, quantos: int = 5) -> list[dict]:
    """[{title, body, href}] do primeiro buscador que responder (eles bloqueiam scraping de vez em quando)."""
    ddgs = DDGS(timeout=5)
    limite = time.time() + ORCAMENTO_S
    falhas = []
    for backend in BACKENDS:
        if time.time() > limite:
            break
        try:
            resultados = ddgs.text(consulta, region="br-pt", max_results=quantos, backend=backend)
            if resultados:
                return resultados
        except Exception as e:
            falhas.append(f"{backend}: {type(e).__name__}")
    raise ErroFerramenta("a pesquisa não retornou resultados agora (" + "; ".join(falhas) + ")")


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
    resultados = buscar(consulta, 6)
    # resultados da web são DADOS: um site pode tentar dar ordens ao assistente
    linhas = [f"- {r.get('title', '')}: {r.get('body', '')[:220]} ({r.get('href', '')})" for r in resultados]
    texto = conteudo_externo("pesquisa na web", "\n".join(linhas))
    _cache[chave] = (time.time(), texto)
    return texto


def _termos(texto: str) -> set[str]:
    sem = unicodedata.normalize("NFD", texto.lower()).encode("ascii", "ignore").decode()
    return {p for p in _PALAVRA.findall(sem) if p not in _VAZIAS}


def trechos_relevantes(texto: str, consulta: str, limite: int) -> str:
    """Os parágrafos que mais falam do assunto, na ordem em que aparecem na página, até `limite` caracteres."""
    paragrafos = [p.strip() for p in re.split(r"\n\s*\n|\n", texto) if len(p.strip()) > 40]
    if not paragrafos:
        return texto[:limite]
    alvo = _termos(consulta)
    notas = [(len(alvo & _termos(p)) + (0.3 if i < 3 else 0), i) for i, p in enumerate(paragrafos)]
    escolhidos, total = set(), 0
    for nota, i in sorted(notas, key=lambda x: (-x[0], x[1])):
        if total >= limite:
            break
        escolhidos.add(i)
        total += len(paragrafos[i]) + 1
    return "\n".join(paragrafos[i] for i in sorted(escolhidos))[:limite]


def ler_varias(enderecos: list[str], consulta: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Baixa e lê as páginas EM PARALELO. ([(título, endereço, trechos)], [falhas])."""
    from jarvis.tools import leitura
    enderecos = list(dict.fromkeys(e.strip() for e in enderecos if e and e.strip()))[:MAX_PAGINAS]
    if not enderecos:
        return [], []
    por_pagina = max(1200, TOTAL_CARACTERES // len(enderecos))
    lidas, falhas = [], []
    pool = ThreadPoolExecutor(max_workers=min(6, len(enderecos)), thread_name_prefix="ler-web")
    futuros = {pool.submit(leitura.ler, e): e for e in enderecos}
    feitos, pendentes = wait(futuros, timeout=ORCAMENTO_LEITURA_S)
    for f in futuros:                       # na ordem do buscador (a mais relevante primeiro)
        endereco = futuros[f]
        if f in pendentes:
            falhas.append(f"{endereco}: demorou demais")
            continue
        try:
            texto, titulo, origem = f.result()
            lidas.append((titulo, origem, trechos_relevantes(texto, consulta, por_pagina)))
        except Exception as e:
            falhas.append(f"{endereco}: {str(e)[:100]}")
    pool.shutdown(wait=False, cancel_futures=True)
    return lidas, falhas


@ferramenta(
    "pesquisar_a_fundo",
    "Pesquisa na internet e LÊ várias páginas de uma vez (em paralelo), devolvendo os trechos que importam de cada "
    "uma, com o endereço. Use para pesquisas de verdade: comparar fontes, levantar informações, notícias com "
    "detalhes, preços em várias lojas, documentação, 'pesquise sobre X em vários sites'. Também lê uma lista de "
    "endereços que você já tem (enderecos). Prefira isto a chamar ler_documento página por página.",
    {"consulta": {"type": "string", "description": "O que pesquisar (também guia quais trechos importam)"},
     "paginas": {"type": "integer", "description": f"Quantas páginas ler (padrão 4, máximo {MAX_PAGINAS})"},
     "enderecos": {"type": "array", "items": {"type": "string"},
                   "description": "Opcional: endereços para ler direto (sem buscar)"}},
    obrigatorios=("consulta",),
    externo=True, so_claude=True,
)
def pesquisar_a_fundo(consulta: str, paginas: int = 4, enderecos: list | None = None) -> str:
    paginas = min(max(int(paginas or 4), 1), MAX_PAGINAS)
    resumo_busca = ""
    alvos = [str(e) for e in (enderecos or []) if isinstance(e, str)]
    if not alvos:
        resultados = buscar(consulta, paginas + 4)          # sobra: algumas páginas falham ou são repetidas
        dominios, alvos = set(), []
        for r in resultados:
            href = r.get("href", "")
            dominio = urllib.parse.urlparse(href).netloc.removeprefix("www.")
            if href.startswith("http") and dominio not in dominios:
                dominios.add(dominio)
                alvos.append(href)
        resumo_busca = "\n".join(f"- {r.get('title', '')}: {r.get('body', '')[:160]} ({r.get('href', '')})"
                                  for r in resultados[:paginas + 4])
    lidas, falhas = ler_varias(alvos[:paginas + 2], consulta)
    lidas = lidas[:paginas]
    if not lidas and not resumo_busca:
        raise ErroFerramenta("não consegui ler nenhuma das páginas (" + "; ".join(falhas[:3]) + ")")
    partes = [f"## {titulo}\n{origem}\n{trechos}" for titulo, origem, trechos in lidas]
    if not lidas:
        partes.append("Nenhuma página abriu; os resultados da busca:\n" + resumo_busca)
    texto = f"Pesquisa: {consulta} · {len(lidas)} página(s) lida(s)\n\n" + "\n\n".join(partes)
    if falhas:
        texto += "\n\nNão abriram: " + "; ".join(falhas[:4])
    return conteudo_externo("várias páginas da web", texto[:TOTAL_CARACTERES + 800])


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
