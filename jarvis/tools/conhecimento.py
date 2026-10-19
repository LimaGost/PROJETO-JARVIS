"""Ferramentas de leitura e estudo: ler qualquer documento (internet ou deste PC) e guardar o que foi estudado na
base de conhecimento local, para consultar depois. Só para os cérebros Claude (o contexto do Qwen não comporta).

Tudo que vem de documento é texto de terceiros: entra marcado como não confiável (conteudo_externo) e, depois de
ler, ações 🟡 passam a pedir confirmação (regra geral do Executor). Estudar não executa nada: só guarda texto.
"""
from collections import OrderedDict

from jarvis.tools import leitura
from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, conteudo_externo, ferramenta

TAMANHO_PARTE = 6000
INICIO_NO_ESTUDO = 4000
_base = None
_lidos: "OrderedDict[str, tuple[str, str, str]]" = OrderedDict()   # fonte -> (texto, título, origem): paginar sem baixar de novo


def ativar(base):
    global _base
    _base = base


def _exigir():
    if _base is None:
        raise ErroFerramenta("a base de conhecimento está desligada (a memória precisa estar ativa)")
    return _base


def _ler(fonte: str) -> tuple[str, str, str]:
    chave = fonte.strip()
    if chave in _lidos:
        _lidos.move_to_end(chave)
        return _lidos[chave]
    lido = leitura.ler(chave)
    _lidos[chave] = lido
    while len(_lidos) > 6:
        _lidos.popitem(last=False)
    return lido


@ferramenta(
    "ler_documento",
    "Lê o texto de uma página da internet, PDF, Google Docs público, vídeo do YouTube (pela legenda) ou arquivo "
    "deste computador (txt, md, pdf, docx). "
    "Documento longo vem em partes: chame de novo com parte=2, 3... até ler o que precisa.",
    {"fonte": {"type": "string", "description": "Endereço (https://...) ou caminho do arquivo"},
     "parte": {"type": "integer", "description": "Padrão 1"}},
    obrigatorios=("fonte",),
    externo=True, so_claude=True,
)
def ler_documento(fonte: str, parte: int = 1) -> str:
    texto, titulo, origem = _ler(fonte)
    total = max(1, -(-len(texto) // TAMANHO_PARTE))
    parte = min(max(int(parte or 1), 1), total)
    pedaco = texto[(parte - 1) * TAMANHO_PARTE: parte * TAMANHO_PARTE]
    cabecalho = f"Título: {titulo}\nOrigem: {origem}\nParte {parte} de {total} ({len(texto)} caracteres no total)\n\n"
    seguir = f"\n\n[Há mais: chame ler_documento com parte={parte + 1}.]" if parte < total else ""
    return conteudo_externo("documento", cabecalho + pedaco) + seguir


@ferramenta(
    "aprender",
    "Estuda um documento (página, PDF, Google Docs público, vídeo do YouTube ou arquivo deste PC) e guarda na base "
    "de conhecimento "
    "local, para consultar depois com consultar_conhecimento. Use quando o usuário pedir para aprender, estudar, "
    "guardar ou ficar bom em algum conteúdo. Estudar de novo a mesma fonte atualiza a versão guardada.",
    {"fonte": {"type": "string", "description": "Endereço (https://...) ou caminho do arquivo"},
     "titulo": {"type": "string", "description": "Nome curto para o assunto (opcional)"}},
    obrigatorios=("fonte",),
    externo=True, so_claude=True,
)
def aprender(fonte: str, titulo: str = "") -> str:
    base = _exigir()
    texto, titulo_lido, origem = _ler(fonte)
    titulo = (titulo or titulo_lido or origem).strip()[:150]
    try:
        doc, substituiu = base.estudar(titulo, origem, texto)
    except ValueError:
        raise ErroFerramenta("o documento não tem texto para estudar")
    except Exception as e:     # embeddings fora do ar (Ollama fechado): nada foi guardado
        raise ErroFerramenta(f"não consegui guardar na base ({type(e).__name__}); o Ollama está aberto?")
    aviso = " Era grande demais: guardei só o começo." if doc.cortado else ""
    acao = "Atualizei" if substituiu else "Estudei e guardei"
    inicio = texto[:INICIO_NO_ESTUDO] + ("\n[...]" if len(texto) > INICIO_NO_ESTUDO else "")
    return (f"{acao} '{doc.titulo}' (id {doc.id}): {doc.caracteres} caracteres em {doc.trechos} trechos.{aviso} "
            "Para responder sobre ele depois, use consultar_conhecimento.\n"
            + conteudo_externo("documento estudado", inicio))


@ferramenta(
    "consultar_conhecimento",
    "Busca na base de conhecimento local (o que você estudou com aprender) os trechos mais relevantes para uma "
    "pergunta. Consulte ANTES de responder sobre assuntos que o usuário mandou estudar.",
    {"pergunta": {"type": "string"},
     "quantos": {"type": "integer", "description": "Trechos (padrão 4, máximo 8)"},
     "documento": {"type": "integer", "description": "Limitar a um documento pelo id (opcional)"}},
    obrigatorios=("pergunta",),
    externo=True, so_claude=True,
)
def consultar_conhecimento(pergunta: str, quantos: int = 4, documento: int = 0) -> str:
    base = _exigir()
    quantos = min(max(int(quantos or 4), 1), 8)
    try:
        achados = base.buscar(pergunta, quantos, documento=documento or None)
    except Exception as e:
        raise ErroFerramenta(f"não consegui buscar na base ({type(e).__name__}); o Ollama está aberto?")
    if not achados:
        docs = base.documentos()
        lista = "; ".join(d.titulo for d in docs[:15]) or "nenhum"
        return f"Nada na base de conhecimento sobre isso. Documentos estudados: {lista}."
    blocos, usados = [], set()
    for a in achados:
        if (a.documento, a.ordem) in usados:
            continue
        usados.update({(a.documento, a.ordem - 1), (a.documento, a.ordem), (a.documento, a.ordem + 1)})
        blocos.append(f"### {a.titulo} (trecho {a.ordem + 1}, relevância {a.nota:.2f})\nFonte: {a.fonte}\n"
                      f"{base.vizinhos(a)}")
    return conteudo_externo("base de conhecimento", "\n\n".join(blocos))


@ferramenta(
    "listar_conhecimento",
    "Lista os documentos que você já estudou (id, título, origem, data).",
    so_claude=True,
)
def listar_conhecimento() -> str:
    docs = _exigir().documentos()
    if not docs:
        return "A base de conhecimento está vazia: ainda não estudei nenhum documento."
    return "\n".join(f"- id {d.id}: {d.titulo} | {d.fonte} | estudado em {d.estudado[:10]} | {d.caracteres} caracteres"
                     for d in docs)


def _avaliar_esquecer(args: dict) -> Avaliacao:
    doc = _exigir().documento(int(args["id"]))
    if doc is None:
        raise ErroFerramenta(f"não há documento com id {args['id']} na base")
    return Avaliacao(Nivel.REGISTRADO, f"apagar da base de conhecimento o documento '{doc.titulo}'")


@ferramenta(
    "esquecer_conhecimento",
    "Apaga um documento da base de conhecimento pelo id (veja listar_conhecimento).",
    {"id": {"type": "integer"}},
    obrigatorios=("id",),
    avaliar=_avaliar_esquecer, so_claude=True,
)
def esquecer_conhecimento(id: int) -> str:
    doc = _exigir().apagar(int(id))
    return f"Apaguei '{doc.titulo}' da base de conhecimento." if doc else f"Não há documento com id {id}."


def titulos_para_contexto(limite: int = 25) -> str:
    """Linha curta que vai em cada pedido ao Claude: assim ele sabe o que já estudou e quando consultar."""
    if _base is None:
        return ""
    try:
        docs = _base.documentos()
    except Exception:
        return ""
    if not docs:
        return ""
    nomes = "; ".join(d.titulo for d in docs[:limite]) + ("; ..." if len(docs) > limite else "")
    return f"[Base de conhecimento (consulte com consultar_conhecimento quando o assunto aparecer): {nomes}]"
