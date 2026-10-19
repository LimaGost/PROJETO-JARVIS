"""Agentes por API (só texto: não enxergam os arquivos; recebem do Context Manager o que precisam).

  - Ollama (local, grátis, privado): POST /api/chat. Respeita o modo jogo e a VRAM de 8 GB (num_ctx 6144, keep_alive
    curto pelo `recursos`).
  - OpenRouter (muitos modelos numa API): POST /api/v1/chat/completions; o custo vem pronto em usage.cost.
  - GPT pela API da OpenAI: POST /v1/responses; custo pela tabela de preços do config (sem tabela = desconhecido).
Chaves: no cofre criptografado (Central > Integrações), nunca no config.yaml nem no contexto de outro modelo.
"""
import json
import time
import urllib.request

from jarvis import recursos
from jarvis.orquestra.agentes.base import Agente, Controle, Deteccao, Disponibilidade, Resultado, Trabalho, agente, \
    parece_transitorio

LIMITE_REQUISICAO_S = 300


def _requisitar(conector: str, metodo: str, url: str, corpo=None, cabecalhos=None, timeout=60, tentativas=2):
    from jarvis.connect import rede
    return rede.requisitar(conector, metodo, url, corpo, cabecalhos, tentativas=tentativas, timeout=timeout)


class AgenteAPI(Agente):
    tipo = "api"
    pode_ler_projeto = pode_editar = False
    latencia = "baixa"

    def _detectar(self) -> Deteccao:
        return Deteccao(True, "API", detalhe="não precisa instalar")

    def _mensagens(self, trabalho: Trabalho) -> list[dict]:
        msgs = [{"role": "system", "content": trabalho.sistema}] if trabalho.sistema else []
        return msgs + [{"role": "user", "content": trabalho.prompt}]

    def _falha(self, inicio: float, e: Exception) -> Resultado:
        texto = str(e)[:500]
        return Resultado(False, erro=texto, transitorio=parece_transitorio(texto), duracao_s=round(time.time() - inicio, 1))


@agente
class Ollama(AgenteAPI):
    id = "ollama"
    nome = "Ollama (local)"
    provedor = "Local"
    tipo = "local"
    icone = "cpu"
    descricao = "Modelo rodando no seu PC: grátis e privado. Bom para resumos e perguntas simples."
    descricao_semantica = "responder perguntas simples, resumir textos, rascunhar, sem enviar nada para a internet"
    capacidades = {"consulta": 0.6, "documentacao": 0.5, "planejamento": 0.5, "julgamento": 0.45, "revisao": 0.4}
    custo_modo = "gratis"
    latencia = "media"
    max_simultaneos = 1
    docs = "https://github.com/ollama/ollama/blob/main/docs/api.md"
    instalar = "https://ollama.com/download"

    def _host(self) -> str:
        return str((self.cfg_geral.get("llm", {}) or {}).get("host", "http://127.0.0.1:11434")).replace(
            "localhost", "127.0.0.1").rstrip("/")

    def modelo_padrao(self) -> str:
        return str(self.cfg.get("modelo") or (self.cfg_geral.get("llm", {}) or {}).get("modelo") or "qwen3:8b")

    def modelos(self) -> list[str]:
        try:
            with urllib.request.urlopen(f"{self._host()}/api/tags", timeout=3) as r:
                return [m.get("name") for m in json.load(r).get("models", []) if m.get("name")]
        except Exception:
            return [self.modelo_padrao()]

    def _detectar(self) -> Deteccao:
        try:
            with urllib.request.urlopen(f"{self._host()}/api/version", timeout=2) as r:
                versao = json.load(r).get("version", "")
            return Deteccao(True, versao, detalhe="Ollama respondendo")
        except Exception:
            return Deteccao(False, detalhe="o Ollama não está aberto")

    def _autenticacao(self) -> Disponibilidade:
        if recursos.em_jogo():
            return Disponibilidade("aguardando", "modo jogo: a GPU está com outro programa", True, "local")
        if self.modelo_padrao() not in self.modelos():
            return Disponibilidade("erro", f"o modelo {self.modelo_padrao()} não está baixado (ollama pull)", True, "local")
        return Disponibilidade("disponivel", f"modelo {self.modelo_padrao()}", True, "local")

    def executar(self, trabalho: Trabalho, controle: Controle) -> Resultado:
        inicio = time.time()
        modelo = trabalho.modelo or self.modelo_padrao()
        keep = (self.cfg_geral.get("llm", {}) or {}).get("keep_alive_reserva", "2m")
        corpo = {"model": modelo, "messages": self._mensagens(trabalho), "stream": False,
                 "options": {"num_ctx": 6144}, "keep_alive": recursos.keep_alive(keep), "think": False}
        controle.evento("sistema", f"Ollama local · modelo {modelo}")
        try:
            r = _requisitar("ollama", "POST", f"{self._host()}/api/chat", corpo,
                            timeout=min(trabalho.limite_s, LIMITE_REQUISICAO_S), tentativas=1)
        except Exception as e:
            return self._falha(inicio, e)
        texto = str(((r or {}).get("message") or {}).get("content") or "").strip()
        res = Resultado(bool(texto), texto, "" if texto else "o modelo não respondeu", modelo=modelo,
                        tokens_entrada=int((r or {}).get("prompt_eval_count") or 0),
                        tokens_saida=int((r or {}).get("eval_count") or 0), duracao_s=round(time.time() - inicio, 1))
        controle.evento("texto", texto[:1500])
        controle.evento("uso", f"{res.tokens_entrada} tokens de entrada, {res.tokens_saida} de saída (local, grátis)")
        return res


@agente
class OpenRouter(AgenteAPI):
    id = "openrouter"
    nome = "OpenRouter"
    provedor = "OpenRouter"
    icone = "workflow"
    descricao = "Centenas de modelos (GPT, Claude, Gemini, Llama...) numa API só, cobrados por uso."
    descricao_semantica = ("raciocinar, planejar, comparar opções, responder perguntas difíceis, segunda opinião "
                           "de outro modelo, julgar propostas")
    capacidades = {"consulta": 0.85, "planejamento": 0.85, "julgamento": 0.85, "documentacao": 0.75, "revisao": 0.6}
    custo_modo = "por_token"
    docs = "https://openrouter.ai/docs/api-reference/overview"
    instalar = "crie uma chave em https://openrouter.ai/keys e cadastre na Central"
    URL = "https://openrouter.ai/api/v1"

    def modelo_padrao(self) -> str:
        return str(self.cfg.get("modelo") or "openrouter/auto")

    def _chave(self) -> str:
        return str(self.cofre.ler("openrouter").get("apikey") or "")

    def _autenticacao(self) -> Disponibilidade:
        if not self._chave():
            return Disponibilidade("desconectado", "cadastre a chave do OpenRouter (Central > Integrações)", False)
        return Disponibilidade("disponivel", f"chave cadastrada · modelo {self.modelo_padrao()}", True, "chave")

    def modelos(self) -> list[str]:
        return [self.modelo_padrao()]

    def executar(self, trabalho: Trabalho, controle: Controle) -> Resultado:
        inicio = time.time()
        chave = self._chave()
        if not chave:
            return Resultado(False, erro="sem chave do OpenRouter")
        modelo = trabalho.modelo or self.modelo_padrao()
        controle.evento("sistema", f"OpenRouter · modelo {modelo}")
        try:
            r = _requisitar("openrouter", "POST", f"{self.URL}/chat/completions",
                            {"model": modelo, "messages": self._mensagens(trabalho)},
                            {"Authorization": f"Bearer {chave}", "X-Title": "Jarvis"},
                            timeout=min(trabalho.limite_s, LIMITE_REQUISICAO_S))
        except Exception as e:
            return self._falha(inicio, e)
        escolha = ((r or {}).get("choices") or [{}])[0]
        texto = str((escolha.get("message") or {}).get("content") or "").strip()
        u = (r or {}).get("usage") or {}
        res = Resultado(bool(texto), texto, "" if texto else "resposta vazia", cobrado=True,
                        modelo=str((r or {}).get("model") or modelo), custo_usd=round(float(u.get("cost") or 0), 6),
                        tokens_entrada=int(u.get("prompt_tokens") or 0), tokens_saida=int(u.get("completion_tokens") or 0),
                        duracao_s=round(time.time() - inicio, 1))
        controle.evento("texto", texto[:1500])
        controle.evento("uso", f"{res.tokens_entrada}+{res.tokens_saida} tokens · US$ {res.custo_usd:.4f} ({res.modelo})",
                        custo=res.custo_usd)
        return res


@agente
class OpenAIGPT(AgenteAPI):
    id = "openai"
    nome = "GPT (API da OpenAI)"
    provedor = "OpenAI"
    icone = "bot"
    descricao = "Modelos GPT pela API oficial: raciocínio, planejamento e interpretação. Cobrado por uso."
    descricao_semantica = ("raciocinar, planejar etapas, interpretar pedidos, explicar, comparar alternativas, "
                           "escrever textos e documentação")
    capacidades = {"planejamento": 0.9, "consulta": 0.9, "julgamento": 0.9, "documentacao": 0.8, "revisao": 0.6}
    custo_modo = "por_token"
    docs = "https://platform.openai.com/docs/api-reference/responses"
    instalar = "crie uma chave em https://platform.openai.com/api-keys e cadastre na Central"
    URL = "https://api.openai.com/v1"

    def modelo_padrao(self) -> str:
        return str(self.cfg.get("modelo") or "")

    def _chave(self) -> str:
        return str(self.cofre.ler("openai").get("apikey") or "")

    def _autenticacao(self) -> Disponibilidade:
        if not self._chave():
            return Disponibilidade("desconectado", "cadastre a chave da OpenAI (Central > Integrações)", False)
        if not self.modelo_padrao():
            return Disponibilidade("erro", "defina orquestra.agentes.openai.modelo no config.yaml", True, "chave")
        return Disponibilidade("disponivel", f"chave cadastrada · modelo {self.modelo_padrao()}", True, "chave")

    def executar(self, trabalho: Trabalho, controle: Controle) -> Resultado:
        inicio = time.time()
        chave, modelo = self._chave(), trabalho.modelo or self.modelo_padrao()
        if not chave or not modelo:
            return Resultado(False, erro="sem chave ou sem modelo configurado para a OpenAI")
        corpo = {"model": modelo, "input": trabalho.prompt}
        if trabalho.sistema:
            corpo["instructions"] = trabalho.sistema
        controle.evento("sistema", f"OpenAI Responses API · modelo {modelo}")
        try:
            r = _requisitar("openai", "POST", f"{self.URL}/responses", corpo, {"Authorization": f"Bearer {chave}"},
                            timeout=min(trabalho.limite_s, LIMITE_REQUISICAO_S))
        except Exception as e:
            return self._falha(inicio, e)
        partes = []
        for item in (r or {}).get("output") or []:
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    partes.append(c["text"])
        texto = "\n".join(partes).strip()
        u = (r or {}).get("usage") or {}
        tabela = (self.cfg_geral.get("orquestra", {}) or {}).get("precos_usd_por_milhao") or {}
        preco = tabela.get(modelo)
        entrada, saida = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
        custo = round((entrada * float(preco[0]) + saida * float(preco[1])) / 1e6, 6) if preco else 0.0
        res = Resultado(bool(texto), texto, "" if texto else "resposta vazia", cobrado=True, modelo=modelo,
                        custo_usd=custo, tokens_entrada=entrada, tokens_saida=saida,
                        duracao_s=round(time.time() - inicio, 1), dados={"custo_conhecido": bool(preco)})
        controle.evento("texto", texto[:1500])
        controle.evento("uso", f"{entrada}+{saida} tokens" + (f" · US$ {custo:.4f}" if preco else " · sem tabela de preço"),
                        custo=custo)
        return res
