import re
from typing import Iterator

import ollama

PROMPT_SISTEMA = """Você é {nome}, o assistente pessoal de inteligência artificial do {tratamento}, \
inspirado no J.A.R.V.I.S. do Homem de Ferro. Você roda localmente no computador dele.

Regras de fala (suas respostas serão lidas em voz alta):
- Responda sempre em português do Brasil.
- Seja breve: no máximo 2 frases curtas, a menos que peçam mais detalhes.
- Responda direto ao ponto, sem repetir a pergunta e sem oferecer ajuda extra no final.
- Tom educado, elegante e com leve ironia inglesa, como um mordomo de alta tecnologia.
- Trate o usuário por "{tratamento}" de vez em quando, sem exagerar.
- Nunca use markdown, listas, emojis, asteriscos ou símbolos. Escreva só texto falado natural.
- Escreva números e unidades por extenso quando facilitar a leitura em voz alta.
- Se não souber algo, diga com honestidade. Não invente."""

# fim de frase: ponto, !, ?, : ou quebra de linha (ignora "3.5" e abreviações com 1 letra)
_FIM_FRASE = re.compile(r"(?<![\d])([.!?:]+|\n)(?=\s|$)")
_LIMPAR = re.compile(r"[*_#`>~]+")
_TRAVESSAO = re.compile(r"\s*[—–]\s*")  # o TTS faz uma pausa estranha em travessões


class Cerebro:
    def __init__(self, cfg: dict):
        c = cfg["llm"]
        self.cliente = ollama.Client(host=c["host"].replace("localhost", "127.0.0.1"))
        self.modelo = c["modelo"]
        self.keep_alive = c["keep_alive"]
        self._opcoes = {"temperature": c["temperatura"], "num_ctx": 4096}
        self.max_msgs = c["max_turnos"] * 2
        self.sistema = PROMPT_SISTEMA.format(nome=cfg["nome"], tratamento=cfg["tratamento"])
        self.historico: list[dict] = []
        self.stats = None

    def aquecer(self):
        """Carrega o modelo na GPU e deixa o prompt de sistema em cache.
        Sem isso, a 1ª pergunta paga ~2,4 s para processar o prompt."""
        self.cliente.chat(
            model=self.modelo,
            messages=[
                {"role": "system", "content": self.sistema},
                {"role": "user", "content": "oi"},
            ],
            think=False,
            keep_alive=self.keep_alive,
            options={**self._opcoes, "num_predict": 1},
        )

    def esquecer(self):
        self.historico.clear()

    def responder(self, texto: str) -> Iterator[str]:
        """Gera a resposta frase por frase, assim o TTS começa antes do fim."""
        self.historico.append({"role": "user", "content": texto})
        mensagens = [{"role": "system", "content": self.sistema}] + self.historico[-self.max_msgs :]

        stream = self.cliente.chat(
            model=self.modelo,
            messages=mensagens,
            stream=True,
            think=False,
            keep_alive=self.keep_alive,
            options=self._opcoes,
        )

        completa = ""
        buffer = ""
        for parte in stream:
            if parte.done:
                self.stats = parte  # prompt_eval_count/duration, eval_count... para diagnóstico
            pedaco = parte.message.content or ""
            completa += pedaco
            buffer = _TRAVESSAO.sub(", ", buffer + pedaco)
            while True:
                m = _FIM_FRASE.search(buffer)
                if not m:
                    break
                frase, buffer = buffer[: m.end()], buffer[m.end() :]
                frase = _LIMPAR.sub("", frase).strip()
                if len(frase) > 1:
                    yield frase

        resto = _LIMPAR.sub("", buffer).strip()
        if resto:
            yield resto

        self.historico.append({"role": "assistant", "content": completa.strip()})
        self.historico = self.historico[-self.max_msgs :]
