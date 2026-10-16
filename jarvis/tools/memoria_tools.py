"""Ferramentas para o Jarvis usar a própria memória: memorizar, esquecer e lembrar.

memorizar/esquecer_fato são 🟡: depois de ler um site ou arquivo (contaminação) passam a pedir
confirmação, para que um texto de terceiros não plante nem apague memórias.
"""
from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, ferramenta

_memoria = None
_consolidador = None


def ativar(memoria, consolidador):
    global _memoria, _consolidador
    _memoria, _consolidador = memoria, consolidador


def _exigir():
    if _memoria is None:
        raise ErroFerramenta("a memória de longo prazo está desligada")
    return _memoria


@ferramenta(
    "memorizar",
    "Guarda na memória de longo prazo um fato sobre o usuário, quando ele pedir para lembrar ou anotar algo "
    "sobre si (ex.: 'lembre que minha filha se chama Bia'). Escreva em terceira pessoa: 'A filha do usuário se chama Bia.'",
    {"fato": {"type": "string"},
     "categoria": {"type": "string", "enum": ["perfil", "preferencia", "pessoa", "trabalho", "projeto", "rotina",
                                              "procedimento", "correcao", "outro"]}},
    obrigatorios=("fato",),
    avaliar=lambda a: Avaliacao(Nivel.REGISTRADO, f"guardar na memória: {a['fato']}"),
)
def memorizar(fato: str, categoria: str = "outro") -> str:
    _exigir()
    acao, _ = _consolidador.consolidar(fato, categoria, "pedido do usuário")
    return {"novo": "Guardado na memória.", "igual": "Eu já sabia disso.",
            "substitui": "Memória atualizada (a informação antiga foi substituída).",
            "recusado": "Não guardei: parece conter dado sigiloso."}[acao]


def _alvo_esquecer(descricao: str):
    achados = _exigir().buscar(descricao, k=1, minimo=0.45)
    if not achados:
        raise ErroFerramenta(f"não encontrei nada na memória sobre '{descricao}'")
    return achados[0][0]


@ferramenta(
    "esquecer_fato",
    "Apaga da memória de longo prazo um fato sobre o usuário, quando ele pedir para esquecer algo.",
    {"descricao": {"type": "string", "description": "O que esquecer"}},
    obrigatorios=("descricao",),
    avaliar=lambda a: Avaliacao(Nivel.REGISTRADO, f"apagar da memória: {_alvo_esquecer(a['descricao']).texto}"),
)
def esquecer_fato(descricao: str) -> str:
    fato = _alvo_esquecer(descricao)
    _memoria.apagar_fato(fato.id)
    return f"Esquecido: {fato.texto}"


@ferramenta(
    "lembrar",
    "Procura na memória de longo prazo fatos sobre o usuário e resumos de conversas de dias anteriores. "
    "Use para perguntas como 'o que conversamos ontem?' ou 'o que eu te disse sobre...'.",
    {"consulta": {"type": "string"}},
    obrigatorios=("consulta",),
)
def lembrar(consulta: str) -> str:
    memoria = _exigir()
    fatos = memoria.buscar(consulta, k=8, minimo=0.30)
    episodios = memoria.buscar_episodios(consulta, k=3, minimo=0.30)
    if not fatos and not episodios:
        return "Não há nada na memória sobre isso."
    linhas = [f"- {f.texto}" for f, _ in fatos]
    linhas += [f"- Conversa de {d[8:10]}/{d[5:7]}/{d[:4]}: {r}" for d, r, _ in episodios]
    return "\n".join(linhas)
