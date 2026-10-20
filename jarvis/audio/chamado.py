"""Chamado por frase: entender "Ei Jarvis", "Jarvis, está aí?", "Oi Jarvis", "E aí, Jarvis", "Jarvis, que horas são?".

O modelo de wake word (openWakeWord) só conhece "Hey Jarvis". Em paralelo, o microfone corta as frases curtas que
você fala, o Whisper (já carregado para a conversa) transcreve e esta função decide se foi um chamado:
  - o nome aparece no começo da frase (até a 3ª palavra, depois de "ei", "oi", "olá", "e aí", "fala", "bom dia"...),
    ou a frase é curta e termina no nome ("você está aí, Jarvis?");
  - o Whisper às vezes escreve o nome de outro jeito: Jarves, Jarbas, Javis, Jervis, Járvis, Djarvis...
Se depois do nome vier um pedido ("Jarvis, que horas são?"), ele volta junto e já é respondido, sem o "Sim, senhor?".
"""
import re
import unicodedata

NOME = re.compile(r"^(?:d?j|g|dg|x)?[ae]r?[vb][iea]?[sz]?$")       # jarvis, jarves, jarbas, javis, jervis, garvis...
SAUDACOES = {"ei", "hey", "hei", "e", "ai", "oi", "ola", "oh", "fala", "alo", "bom", "boa", "dia", "tarde",
             "noite", "salve", "opa", "ow", "psiu", "ok", "okay", "hello", "eae", "eai"}   # "ô" vira "oh"; "o" (artigo) não conta
PRESENCA = re.compile(r"^(?:voce |vc |tu )?(?:(?:es)?ta (?:ai|ae|aqui|on|online|acordado|me ouvindo|ouvindo)|"
                      r"acorda|acordou|ta ai|me ouve|consegue me ouvir|ta me ouvindo|tudo bem|tudo certo|"
                      r"beleza|presente|online|vem ca)?\??$")
MAX_PALAVRAS_SO_NOME = 7


def _normal(texto: str) -> str:
    sem = unicodedata.normalize("NFD", texto.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w\s]", " ", sem)


def eh_nome(palavra: str) -> bool:
    p = _normal(palavra).strip()
    return len(p) >= 4 and p not in {"javas", "java"} and bool(NOME.match(p))


def analisar(texto: str) -> tuple[bool, str]:
    """(foi um chamado, o pedido que veio junto ou ""). O pedido volta com a grafia original."""
    original = texto.strip()
    palavras = original.split()
    normais = [("oh" if p.lower().strip(",.!?") == "ô" else _normal(p).strip()) for p in palavras]
    indice = next((i for i, p in enumerate(normais) if p and eh_nome(p)), None)
    if indice is None:
        return False, ""
    antes = [p for p in normais[:indice] if p]
    so_saudacao = all(p in SAUDACOES for p in antes)
    curto_no_fim = len(normais) <= MAX_PALAVRAS_SO_NOME and indice >= len(normais) - 2
    if not ((indice <= 2 and so_saudacao) or curto_no_fim or (indice <= 3 and so_saudacao)):
        return False, ""                              # o nome no meio de uma conversa: não é com ele
    resto = " ".join(palavras[indice + 1:]).strip(" ,.!?;:-")
    if not resto and indice > 0 and not so_saudacao:  # "você está aí, Jarvis?": a parte de antes é a presença
        resto = " ".join(palavras[:indice]).strip(" ,.!?;:-")
    if PRESENCA.match(" ".join(_normal(resto).split())):
        return True, ""                               # só chamou ("tá aí?"): ele responde e espera o pedido
    return True, resto
