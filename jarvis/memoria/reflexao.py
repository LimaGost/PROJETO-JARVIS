"""Reflexão diária: relê as conversas, extrai fatos, consolida com o que já sabia e resume o dia.

Só o que o USUÁRIO disse é fonte de fatos. Conteúdo de sites e arquivos não entra aqui
(nem é registrado no histórico), para que uma página não consiga plantar "memórias".
"""
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import ollama

from jarvis.memoria.banco import CATEGORIAS, Memoria, contem_segredo

DUPLICADO = 0.94    # acima: o mesmo fato com outras palavras (paráfrases deram 0,93-0,99)
RELACIONADO = 0.60  # abaixo: assunto diferente. Entre os dois o LLM decide: atualizações deram
                    # 0,76-0,89 e fatos vizinhos diferentes 0,62-0,75, então não dá para cortar por número
MAX_CHARS_TRECHO = 3500

# Categorias que podem falar do mesmo assunto. Fatos de grupos diferentes nunca se fundem, sem nem perguntar
# ao modelo: o Qwen 8B insistia que "projeto Jarvis" e "prefere que o Jarvis responda curto" eram o mesmo assunto.
# "outro" (categoria de quem não informou) é compatível com todos.
_GRUPOS = {"perfil": "quem", "trabalho": "quem", "pessoa": "pessoas", "preferencia": "jeito",
           "procedimento": "jeito", "correcao": "jeito", "projeto": "projeto", "rotina": "rotina"}


def _compativeis(cat_a: str, cat_b: str) -> bool:
    ga, gb = _GRUPOS.get(cat_a), _GRUPOS.get(cat_b)
    return ga is None or gb is None or ga == gb

PROMPT_EXTRAIR = """Você analisa conversas entre um usuário e seu assistente pessoal, o Jarvis, para descobrir \
fatos duradouros sobre o USUÁRIO que valham a pena lembrar nas próximas semanas.

Extraia apenas o que o próprio usuário disse ou deixou claro sobre:
- quem ele é (nome, profissão, onde mora), família, amigos e animais;
- trabalho, empresa, projetos em andamento;
- preferências e gostos, rotina e horários;
- como ele quer que o Jarvis faça as coisas (procedimentos) e correções que ele fez ao Jarvis.

NÃO extraia:
- pedidos momentâneos ("abre o Spotify", "que horas são") nem o resultado deles;
- conhecimento geral, opiniões do Jarvis ou coisas que só o Jarvis disse;
- senhas, documentos, números de cartão ou qualquer dado sigiloso;
- suposições: só o que foi dito.

Escreva cada fato como uma frase curta, completa e independente, em terceira pessoa, começando com "O usuário" \
ou com a pessoa de quem se fala ("A mãe do usuário...", "O irmão do usuário..."). Um assunto por fato: nome, \
profissão e cidade são fatos separados; cada pessoa da família também.
Preserve os detalhes exatamente como foram ditos: nomes próprios, empresas, números, horários, endereços de sites \
e o gênero das pessoas (filha é filha, não filho). Um fato sem o detalhe principal não serve: se o usuário disse \
"o portal é portal.empresa.com", o fato precisa conter "portal.empresa.com".
No máximo 12 fatos. Se não houver nada, devolva lista vazia.

Categorias:
- perfil: quem o usuário é (nome, idade, profissão, empresa, cidade onde mora);
- pessoa: outras pessoas e animais da vida dele;
- preferencia: gostos e o jeito que ele prefere as coisas;
- trabalho: detalhes do trabalho além do que está no perfil;
- projeto: projetos em andamento;
- rotina: horários e compromissos que se repetem;
- procedimento: como ele quer que o Jarvis faça algo, apelidos que ele usa ("o painel" = um site);
- correcao: quando ele corrigiu o Jarvis;
- outro: o que não se encaixar.

Conversa:
{conversa}"""

ESQUEMA_EXTRAIR = {
    "type": "object",
    "properties": {"fatos": {"type": "array", "maxItems": 12, "items": {
        "type": "object",
        "properties": {"texto": {"type": "string"}, "categoria": {"type": "string", "enum": CATEGORIAS}},
        "required": ["texto", "categoria"]}}},
    "required": ["fatos"],
}

PROMPT_CONSOLIDAR = """Você mantém a memória de um assistente pessoal sobre o usuário.
Chegou uma informação NOVA, mais recente que todas as existentes. Primeiro escreva, em poucas palavras, o ASSUNTO \
da nova e o assunto da existente mais parecida. Depois compare e decida:

- "igual": a nova diz a mesma coisa que uma existente (mesmo que com outras palavras).
- "substitui": a nova CONTRADIZ uma existente sobre o mesmo assunto, ou seja, a existente deixou de ser verdade \
(mudou de cidade, de emprego, de horário, o animal tem outro nome). Informe o id da existente.
- "completa": a nova ACRESCENTA um detalhe compatível a uma existente, e as duas continuam verdadeiras \
(ex.: "trabalha como analista" + "trabalha na empresa X"). Informe o id e o texto final juntando as duas.
- "novo": é um assunto diferente de todas.

Assuntos diferentes nunca se juntam, mesmo quando as frases se parecem. Exemplos de assuntos DIFERENTES: \
esposa e filho; gostar de rock e gostar de pizza; "o projeto que o usuário está montando" e "como o Jarvis deve \
responder" (os dois citam o Jarvis, mas são assuntos diferentes). Na dúvida, escolha "novo".

NOVA: {novo}

EXISTENTES:
{existentes}"""

ESQUEMA_CONSOLIDAR = {  # assuntos antes da ação: o modelo pequeno decide melhor depois de nomeá-los
    "type": "object",
    "properties": {"assunto_nova": {"type": "string"}, "assunto_existente": {"type": "string"},
                   "acao": {"type": "string", "enum": ["igual", "substitui", "completa", "novo"]},
                   "id": {"type": "integer"}, "texto": {"type": "string"}},
    "required": ["assunto_nova", "assunto_existente", "acao"],
}

PROMPT_RESUMIR = """Resuma em 2 a 4 frases, em português e em terceira pessoa, o que o usuário conversou e fez \
com o assistente Jarvis no dia {dia}. Foque em assuntos, decisões e tarefas importantes; ignore pedidos triviais \
(hora, volume, abrir programas). {anterior}

Conversa:
{conversa}"""

ESQUEMA_RESUMIR = {"type": "object", "properties": {"resumo": {"type": "string"}}, "required": ["resumo"]}


class Consolidador:
    """Decide se um fato candidato é novo, repetido ou atualização de um antigo. Usado pela
    reflexão e pela ferramenta 'memorizar'."""

    def __init__(self, memoria: Memoria, cliente: ollama.Client, modelo: str, opcoes: dict, keep_alive):
        self.memoria = memoria
        self.cliente = cliente
        self.modelo = modelo
        # mesmo num_ctx do Jarvis: num_ctx diferente faz o Ollama recarregar o modelo (vários segundos)
        self.opcoes = {**opcoes, "temperature": 0}
        # valor fixo ou função: com o Claude como cérebro, o Qwen da reflexão não pode ficar morando na GPU
        self.keep_alive = keep_alive

    def perguntar(self, prompt: str, esquema: dict) -> dict:
        keep_alive = self.keep_alive() if callable(self.keep_alive) else self.keep_alive
        r = self.cliente.chat(model=self.modelo, messages=[{"role": "user", "content": prompt}], format=esquema,
                              think=False, keep_alive=keep_alive, options=self.opcoes)
        return json.loads(r.message.content)

    def consolidar(self, texto: str, categoria: str, origem: str) -> tuple[str, int | None]:
        """Retorna (ação, id): 'novo', 'igual', 'substitui', ou 'recusado' (segredo/vazio)."""
        texto = texto.strip()
        if not texto or contem_segredo(texto):
            return "recusado", None
        parecidos = self.memoria.semelhantes(texto, k=3, minimo=RELACIONADO)
        if parecidos and parecidos[0][1] >= DUPLICADO:
            return "igual", parecidos[0][0].id
        parecidos = [(f, s) for f, s in parecidos if _compativeis(categoria, f.categoria)]
        if not parecidos:
            return "novo", self.memoria.adicionar_fato(texto, categoria, origem)

        existentes = "\n".join(f"[id {f.id}] {f.texto}" for f, _ in parecidos)
        try:
            d = self.perguntar(PROMPT_CONSOLIDAR.format(novo=texto, existentes=existentes), ESQUEMA_CONSOLIDAR)
        except Exception:
            d = {"acao": "novo"}  # na dúvida não apaga nada: guarda os dois
        acao = d.get("acao")
        antigo = next((f for f, _ in parecidos if f.id == d.get("id")), None)
        if antigo is None or acao not in ("igual", "substitui", "completa"):
            return "novo", self.memoria.adicionar_fato(texto, categoria, origem)
        # "outro" é a categoria padrão de quem não informou: não rebaixa um fato de "perfil"
        cat = categoria if categoria != "outro" else None

        if acao == "igual":
            if _empobrece(texto, antigo.texto, antigo.texto):
                # "igual", mas a nova tem um detalhe que a antiga não tem (ex.: o endereço): fica a mais rica
                self.memoria.atualizar_fato(antigo.id, texto, cat)
                return "substitui", antigo.id
            return "igual", antigo.id

        if acao == "substitui":
            # o antigo deixou de ser verdade: o texto final é a frase nova, não uma mistura escrita pelo modelo
            # (que juntou "mora em Valinhos. mora em Campinas.")
            if _empobrece(antigo.texto, texto, texto):
                return "igual", antigo.id  # versão mais pobre do mesmo fato: fica o antigo
            self.memoria.atualizar_fato(antigo.id, texto, cat)
            return "substitui", antigo.id

        final = (d.get("texto") or "").strip()  # completa: o modelo junta as duas
        if not final or _faltando(antigo.texto, final) or _faltando(texto, final):
            return "novo", self.memoria.adicionar_fato(texto, categoria, origem)  # junção ruim: guarda separado
        self.memoria.atualizar_fato(antigo.id, final, cat)
        return "substitui", antigo.id


class Reflexao:
    def __init__(self, memoria: Memoria, consolidador: Consolidador, pasta_logs: str,
                 ocupado: threading.Event | None = None):
        self.memoria = memoria
        self.consolidador = consolidador
        self.ocupado = ocupado or threading.Event()  # setado enquanto o usuário conversa: a reflexão espera
        self.log = Path(pasta_logs) / "reflexao.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self._rodando = threading.Lock()

    def pendente(self) -> bool:
        return bool(self.memoria.dias_pendentes())

    def em_segundo_plano(self, aviso=print, incluir_hoje: bool = False):
        """Roda numa thread se houver dias pendentes e nenhuma reflexão em andamento."""
        if not self.memoria.dias_pendentes(incluir_hoje) or self._rodando.locked():
            return

        def tarefa():
            try:
                r = self.executar(incluir_hoje)
                if r["dias"]:
                    aviso(f"\n  [memória] reflexão de {', '.join(r['dias'])}: {r['novos']} fatos novos, "
                          f"{r['atualizados']} atualizados")
            except Exception as e:
                self._escrever(f"ERRO: {type(e).__name__}: {e}")
        threading.Thread(target=tarefa, daemon=True).start()

    def iniciar_zelador(self, aviso=print, intervalo_s: int = 300, ocioso_s: int = 900,
                        exigir_ocio=lambda: False):
        """Verifica de tempos em tempos: dias anteriores pendentes, ou conversas de hoje depois de
        `ocioso_s` sem falar com o Jarvis. Assim o que foi dito hoje vira memória ainda hoje.
        `exigir_ocio()` True (Claude como cérebro): nada roda antes de `ocioso_s` parado, contando da partida.
        A reflexão sobe o Qwen na GPU, e Qwen + voz clonada + Whisper não cabem em 8 GB: tudo fica lento."""
        inicio = datetime.now()

        def vigiar():
            while True:
                time.sleep(intervalo_s)
                if self.ocupado.is_set():
                    continue
                hoje = self.pode_rodar(ocioso_s, inicio, exigir_ocio())
                if hoje is not None:
                    self.em_segundo_plano(aviso, incluir_hoje=hoje)
        threading.Thread(target=vigiar, daemon=True).start()

    def pode_rodar(self, ocioso_s: int, inicio: datetime, exigir_ocio: bool) -> bool | None:
        """None = não rodar agora; senão, se as conversas de hoje já podem entrar (parado há `ocioso_s`)."""
        ultimo = self.memoria.ultimo_turno()
        parado_conversa = ultimo is not None and (datetime.now() - ultimo).total_seconds() >= ocioso_s
        if not exigir_ocio:
            return parado_conversa
        referencia = max(ultimo or inicio, inicio)
        if (datetime.now() - referencia).total_seconds() < ocioso_s:
            return None
        return parado_conversa

    def _esperar_livre(self):
        while self.ocupado.is_set():
            time.sleep(0.5)

    def executar(self, incluir_hoje: bool = False) -> dict:
        relatorio = {"dias": [], "novos": 0, "atualizados": 0, "repetidos": 0}
        if not self._rodando.acquire(blocking=False):
            return relatorio
        try:
            for dia in self.memoria.dias_pendentes(incluir_hoje):
                turnos = self.memoria.turnos_do_dia(dia)
                if not turnos:
                    continue
                conversa = _formatar(turnos)
                for trecho in _trechos(conversa, MAX_CHARS_TRECHO):
                    self._esperar_livre()
                    try:
                        fatos = self.consolidador.perguntar(PROMPT_EXTRAIR.format(conversa=trecho), ESQUEMA_EXTRAIR)
                    except Exception as e:
                        self._escrever(f"{dia}: falha ao extrair ({e})")
                        continue
                    fatos = fatos.get("fatos", [])[:12]
                    # o modelo às vezes guarda "o painel é o site da empresa" sem o endereço: segunda chance
                    for linha in _linhas_com_site_esquecido(trecho, fatos):
                        try:
                            extra = self.consolidador.perguntar(PROMPT_EXTRAIR.format(conversa=linha), ESQUEMA_EXTRAIR)
                            fatos += [f for f in extra.get("fatos", [])[:3] if _SITE.search(f.get("texto", ""))]
                        except Exception:
                            pass
                    for f in fatos:
                        self._esperar_livre()
                        acao, id_ = self.consolidador.consolidar(f.get("texto", ""), f.get("categoria", "outro"),
                                                                 f"reflexão {dia}")
                        chave = {"novo": "novos", "substitui": "atualizados", "igual": "repetidos"}.get(acao)
                        if chave:
                            relatorio[chave] += 1
                        self._escrever(f"{dia}: {acao} [{id_}] {f.get('texto', '')}")

                self._esperar_livre()
                anterior = self.memoria.episodio(dia)
                try:
                    r = self.consolidador.perguntar(PROMPT_RESUMIR.format(
                        dia=f"{dia[8:10]}/{dia[5:7]}/{dia[:4]}", conversa=conversa[:MAX_CHARS_TRECHO],
                        anterior=f"Resumo já feito antes, incorpore-o: {anterior}" if anterior else ""), ESQUEMA_RESUMIR)
                    if r.get("resumo", "").strip():
                        self.memoria.salvar_episodio(dia, r["resumo"].strip())
                        self._escrever(f"{dia}: resumo: {r['resumo'].strip()}")
                except Exception as e:
                    self._escrever(f"{dia}: falha ao resumir ({e})")
                # só até o último turno lido: o que chegou durante a reflexão fica para a próxima
                self.memoria.marcar_refletido(dia, ate_id=turnos[-1][0])
                relatorio["dias"].append(dia)
            return relatorio
        finally:
            self._rodando.release()

    def _escrever(self, texto: str):
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {texto}\n")


_DETALHE = re.compile(r"\b[\w-]+(?:\.[\w-]+)+\b|\d+(?:[:h,.]\d+)?|(?<!^)(?<![.!?] )\b[A-ZÀ-Ú][\wà-ú]+")
_NAO_E_DETALHE = {"Jarvis", "O", "A", "Os", "As", "Usuário"}


def _detalhes(texto: str) -> set[str]:
    """Sites, números e nomes próprios: o que faz um fato ser útil."""
    return {d.lower() for d in _DETALHE.findall(texto.strip()) if d not in _NAO_E_DETALHE}


def _faltando(fonte: str, destino: str) -> set[str]:
    """Detalhes da fonte que não aparecem no destino. Por palavra inteira no texto todo: 'Lumax' aparece em
    'painel.lumax.com.br', mas 'Rio' não aparece em 'usuário' só porque a palavra termina em 'rio'."""
    return {d for d in _detalhes(fonte)
            if re.search(rf"(?<!\w){re.escape(d)}(?!\w)", destino.lower()) is None}


def _empobrece(antigo: str, final: str, novo: str) -> bool:
    """O texto final perdeu detalhes do antigo sem a informação nova trazer nenhum detalhe novo.
    'mora em Campinas' -> 'mora em Valinhos' é atualização (Valinhos é novo);
    'o painel é painel.lumax.com.br' -> 'o painel é o site da Lumax' só perdeu o endereço."""
    return bool(_faltando(antigo, final)) and not _faltando(novo, antigo)


_SITE = re.compile(r"(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?", re.IGNORECASE)


def _linhas_com_site_esquecido(trecho: str, fatos: list[dict]) -> list[str]:
    """Falas do usuário que citam um site que não apareceu em nenhum fato extraído."""
    textos = " ".join(f.get("texto", "") for f in fatos).lower()
    linhas = []
    for linha in trecho.splitlines():
        if "Usuário:" not in linha:
            continue
        sites = [s.rstrip(".,;") for s in _SITE.findall(linha)]
        if any(s.lower() not in textos for s in sites):
            linhas.append(linha)
    return linhas


def _formatar(turnos) -> str:
    linhas = []
    for _, quando, usuario, jarvis, ferramentas in turnos:
        linhas.append(f"[{quando[11:16]}] Usuário: {usuario}")
        linhas.append(f"Jarvis: {jarvis}" + (f" (usou: {ferramentas})" if ferramentas else ""))
    return "\n".join(linhas)


def _trechos(texto: str, tamanho: int) -> list[str]:
    """Corta em blocos de linhas inteiras."""
    blocos, atual = [], ""
    for linha in texto.splitlines():
        if len(atual) + len(linha) > tamanho and atual:
            blocos.append(atual)
            atual = ""
        atual += linha + "\n"
    if atual:
        blocos.append(atual)
    return blocos
