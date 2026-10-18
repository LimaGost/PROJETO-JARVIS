"""Cérebro Claude (API da Anthropic): mesma interface do Cerebro local, muito mais capaz.

Não muda nada fora do cérebro: ferramentas, permissões 🟢🟡🔴, bloqueio da HUD, memória e confirmação
continuam no seu computador, em código. O Claude só decide O QUE pedir; quem decide se PODE é o Executor.

Privacidade: com este cérebro, o texto das conversas, os fatos da memória que entram no pedido e o conteúdo
de janelas/arquivos lidos pelas ferramentas são enviados à Anthropic. Para ficar 100% local, `cerebro.motor: ollama`.

Detalhes que importam (da documentação atual da API):
- o histórico é SOMENTE-ACRÉSCIMO: o contexto/memória vai dentro da mensagem do usuário e fica lá, e os blocos de
  raciocínio voltam sem alteração. Ao cortar turnos antigos usamos `prefix_mismatch_behavior: drop_block`;
- ferramentas e prompt de sistema são idênticos em todo pedido (cache de prompt: leituras custam ~10%);
- nunca `tool_choice` forçado (400 nestes modelos); na última rodada usa `none`.
"""
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

from jarvis.brain.llm import _FIM_FRASE, _LIMPAR, _TRAVESSAO, PROMPT_VISAO, Cerebro
from jarvis.config import RAIZ

ARQUIVO_CHAVE = RAIZ / "data" / "anthropic.key"   # fora do git; ou a variável ANTHROPIC_API_KEY
BETA_VINCULO = "thinking-binding-controls-2026-08-01"
MODELOS_SUPORTADOS = re.compile(r"^claude-(opus|sonnet|haiku|fable)-5")  # família 5.x (pensamento adaptativo)
RECUSA = "Isso eu não posso fazer, senhor."


class ErroCerebro(Exception):
    """Falha do cérebro externo (rede, login, cota, isolamento): com reserva local, o modelo local assume."""


def carregar_chave() -> str | None:
    """A chave nunca fica no config.yaml (que vai para o git): variável de ambiente ou data/anthropic.key."""
    chave = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not chave and ARQUIVO_CHAVE.exists():
        chave = ARQUIVO_CHAVE.read_text(encoding="utf-8").strip()
    return chave or None


def schema_para_claude(schemas_ollama: list[dict]) -> list[dict]:
    """{'type':'function','function':{name,description,parameters}} -> formato de ferramenta da Anthropic."""
    saida = []
    for s in schemas_ollama:
        f = s["function"]
        parametros = dict(f.get("parameters") or {"type": "object", "properties": {}})
        parametros.setdefault("type", "object")
        parametros.setdefault("properties", {})
        saida.append({"name": f["name"], "description": f["description"], "input_schema": parametros})
    return saida


def _turno_real(msg: dict) -> bool:
    """Mensagem do usuário que abre um pedido (texto), não um bloco de resultado de ferramenta."""
    return msg["role"] == "user" and isinstance(msg["content"], str)


class CerebroClaude(Cerebro):
    usa_conhecimento = True
    def __init__(self, cfg: dict, executor=None, memoria=None, cliente=None):
        super().__init__(cfg, executor, memoria)
        c = cfg.get("claude", {})
        self.modelo_claude = c.get("modelo", "claude-opus-5-5")
        if not MODELOS_SUPORTADOS.match(self.modelo_claude):
            raise ValueError(f"modelo '{self.modelo_claude}' não suportado: use a família 5.x (claude-opus-5-5, "
                             "claude-sonnet-5-5, claude-haiku-5-5)")
        self.esforco = c.get("esforco", "low")
        self.max_tokens = c.get("max_tokens", 4096)  # limite de raciocínio + resposta juntos
        self.max_turnos_claude = c.get("max_turnos", 40)
        self.max_rodadas = c.get("max_rodadas", 20)     # com o mouse, uma tarefa leva vários passos de ver e agir
        self.limite_continuidade = 5000
        self.rotulo = self.modelo_claude
        self.ferramentas_claude = schema_para_claude(executor.schemas(visao=True)) if executor else []
        if self.ferramentas_claude:
            self.sistema += PROMPT_VISAO
        if cliente is None:
            chave = carregar_chave()
            if not chave:
                raise RuntimeError("sem chave da Anthropic (ANTHROPIC_API_KEY ou data/anthropic.key)")
            import anthropic
            cliente = anthropic.Anthropic(api_key=chave, max_retries=2, timeout=c.get("timeout_s", 60.0))
        self.claude = cliente
        self.hist: list[dict] = []
        self.pasta_uso = RAIZ / "data" / "logs"
        self.uso_total = {"pedidos": 0, "entrada": 0, "saida": 0, "cache_lido": 0, "cache_escrito": 0}

    # ------------------------------------------------------------ ciclo de vida
    def aquecer(self):
        """Valida a chave e a conexão com uma chamada mínima (não gasta quase nada) e já escreve o cache."""
        with self.claude.beta.messages.stream(**self._parametros([{"role": "user", "content": "oi"}], max_tokens=16,
                                                                 sem_cache=False)) as s:
            s.get_final_message()

    def nova_sessao(self):
        self.hist.clear()
        self.historico.clear()

    # ------------------------------------------------------------ montagem da chamada
    def _parametros(self, mensagens: list[dict], max_tokens: int | None = None, sem_ferramentas: bool = False,
                    sem_cache: bool = False) -> dict:
        p = {
            "model": self.modelo_claude,
            "max_tokens": max_tokens or self.max_tokens,
            # ponto de cache próprio: ferramentas + prompt (~4 mil tokens, iguais em todo pedido) ficam em cache
            # mesmo quando a primeira mensagem da conversa muda
            "system": [{"type": "text", "text": self.sistema, "cache_control": {"type": "ephemeral"}}],
            "messages": mensagens,
            "tools": self.ferramentas_claude,                 # idênticas em todo pedido: mantêm o cache
            "thinking": {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
            "output_config": {"effort": self.esforco},
            "betas": [BETA_VINCULO],
        }
        if not self.ferramentas_claude:
            p.pop("tools")
        elif sem_ferramentas:
            p["tool_choice"] = {"type": "none"}               # nunca 'any'/'tool' forçado: 400 nestes modelos
        if not sem_cache:
            p["cache_control"] = {"type": "ephemeral"}
        return p

    def _aparar(self):
        """Mantém só os últimos N pedidos, cortando sempre no início de um pedido (nunca no meio de tool_use)."""
        inicios = [i for i, m in enumerate(self.hist) if _turno_real(m)]
        if len(inicios) > self.max_turnos_claude:
            self.hist = self.hist[inicios[-self.max_turnos_claude]:]  # drop_block cobre o raciocínio que ficou órfão

    def _registrar_uso(self, usage):
        if usage is None:
            return
        u = self.uso_total
        u["pedidos"] += 1
        u["entrada"] += getattr(usage, "input_tokens", 0) or 0
        u["saida"] += getattr(usage, "output_tokens", 0) or 0
        u["cache_lido"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        u["cache_escrito"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        try:
            self.pasta_uso.mkdir(parents=True, exist_ok=True)
            with open(self.pasta_uso / "uso_claude.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"quando": datetime.now().isoformat(timespec="seconds"), "modelo": self.modelo_claude,
                                    "entrada": usage.input_tokens, "saida": usage.output_tokens,
                                    "cache_lido": getattr(usage, "cache_read_input_tokens", 0) or 0,
                                    "cache_escrito": getattr(usage, "cache_creation_input_tokens", 0) or 0}) + "\n")
        except Exception:
            pass  # telemetria de custo nunca atrapalha a conversa

    # ------------------------------------------------------------ pedido
    def responder(self, texto: str) -> Iterator[str]:
        self._cancelado.clear()
        base = len(self.hist)
        contexto = self._contexto(texto, sessao_nova=not self.hist)
        # o contexto fica NA mensagem para sempre (histórico somente-acréscimo: não se reescreve turno antigo)
        self.hist.append({"role": "user", "content": f"{contexto}\n\n{texto}"})
        if self.executor:
            self.executor.novo_turno()
        falado: list[str] = []
        usadas: list[str] = []
        try:
            for rodada in range(self.max_rodadas + 1):
                ultima = rodada == self.max_rodadas
                buffer = ""
                with self.claude.beta.messages.stream(**self._parametros(self.hist, sem_ferramentas=ultima)) as stream:
                    for pedaco in stream.text_stream:
                        if self._cancelado.is_set():
                            self.hist = self.hist[:base]
                            return
                        buffer = _TRAVESSAO.sub(", ", buffer + pedaco)
                        while (m := _FIM_FRASE.search(buffer)):
                            frase, buffer = buffer[: m.end()], buffer[m.end():]
                            frase = _LIMPAR.sub("", frase).strip()
                            if len(frase) > 1:
                                falado.append(frase)
                                yield frase
                    final = stream.get_final_message()
                resto = _LIMPAR.sub("", buffer).strip()
                if resto:
                    falado.append(resto)
                    yield resto
                self._registrar_uso(getattr(final, "usage", None))
                self.hist.append({"role": "assistant", "content": final.content})  # blocos como vieram

                if final.stop_reason == "refusal":
                    falado.append(RECUSA)
                    yield RECUSA
                    break
                chamadas = [b for b in final.content if b.type == "tool_use"]
                if final.stop_reason != "tool_use" or not chamadas:
                    break
                resultados = []
                for b in chamadas:
                    if self._cancelado.is_set():
                        self.hist = self.hist[:base]
                        return
                    usadas.append(b.name)
                    texto_res = self.executor.executar(b.name, dict(b.input or {}))
                    conteudo = str(texto_res)
                    if getattr(texto_res, "imagem", None):    # ver_tela / mouse: o Claude enxerga o print
                        conteudo = [{"type": "text", "text": conteudo},
                                    {"type": "image", "source": {"type": "base64", "media_type": texto_res.tipo,
                                                                 "data": texto_res.imagem}}]
                    resultados.append({"type": "tool_result", "tool_use_id": b.id, "content": conteudo,
                                       "is_error": texto_res.startswith(("Erro", "Ação perigosa bloqueada",
                                                                         "O usuário NÃO autorizou"))})
                self.hist.append({"role": "user", "content": resultados})  # todos os resultados numa só mensagem
            self._registrar(texto, falado, usadas)
            self._aparar()
        except Exception:
            self.hist = self.hist[:base]  # não deixa um tool_use sem resultado no histórico
            raise


def _erros_de_reserva() -> tuple:
    """Falhas que justificam passar o pedido para o modelo local (rede, login, cota, isolamento)."""
    erros: list = [ErroCerebro]
    try:
        import anthropic
        erros += [anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError,
                  anthropic.AuthenticationError, anthropic.InternalServerError]
    except ImportError:
        pass
    return tuple(erros)


class CerebroComReserva:
    """Claude como principal; se a internet cair ou a chave falhar ANTES de qualquer fala, o modelo local responde."""

    def __init__(self, principal: CerebroClaude, reserva: Cerebro):
        self.principal, self.reserva = principal, reserva
        self.memoria, self.executor, self.sessao = principal.memoria, principal.executor, principal.sessao
        self.usando_reserva = False
        self.ultimo_erro: str | None = None

    @property
    def rotulo(self) -> str:
        """Mostra a verdade na HUD: se caiu na reserva, o rótulo é o do modelo local."""
        if self.usando_reserva:
            return f"{self.reserva.rotulo} (reserva)"
        return self.principal.rotulo

    @property
    def historico(self):
        return self.principal.historico

    def aquecer(self):
        try:
            self.principal.aquecer()
        except Exception as e:
            print(f"[cérebro] Claude indisponível agora ({type(e).__name__}); usando o modelo local.")
            self.ultimo_erro = f"{type(e).__name__}: {str(e)[:140]}"
            self.usando_reserva = True
        if not self.usando_reserva:
            return  # o local só sobe se precisar: Qwen parado na GPU + voz clonada + Whisper não cabem em 8 GB
        try:
            self.reserva.aquecer()
        except Exception as e:
            print(f"[cérebro] modelo local também indisponível ({type(e).__name__}).")

    def cancelar(self):
        self.principal.cancelar()
        self.reserva.cancelar()

    def esquecer(self):
        self.principal.esquecer()
        self.reserva.esquecer()

    def nova_sessao(self):
        self.principal.nova_sessao()
        self.reserva.nova_sessao()

    def responder(self, texto: str) -> Iterator[str]:
        if not self.usando_reserva:
            falou = False
            try:
                for frase in self.principal.responder(texto):
                    falou = True
                    yield frase
                return
            except Exception as e:
                if falou:
                    raise  # já falou: não dá para recomeçar sem repetir
                if not isinstance(e, _erros_de_reserva()):
                    raise
                print(f"[cérebro] {self.rotulo} falhou ({type(e).__name__}: {str(e)[:120]}); este pedido vai para o modelo local.")
        yield from self.reserva.responder(texto)
        if self.usando_reserva:
            self._tentar_voltar()

    def _tentar_voltar(self):
        """Na reserva por falha de partida: tenta o Claude de novo de tempos em tempos."""
        agora = time.time()
        if agora - getattr(self, "_ultima_tentativa", 0) > 120:
            self._ultima_tentativa = agora
            try:
                self.principal.aquecer()
                self.usando_reserva = False
                print("[cérebro] Claude voltou.")
            except Exception:
                pass


def criar_cerebro(cfg: dict, executor=None, memoria=None):
    """Escolhe o cérebro pelo config.yaml (`cerebro.motor`): ollama | claude. Sem chave, cai no local."""
    motor = cfg.get("cerebro", {}).get("motor", "ollama")
    local = Cerebro(cfg, executor, memoria)
    if motor not in ("claude", "claude-cli"):
        return local
    try:
        if motor == "claude-cli":
            from jarvis.brain.claude_cli import CerebroClaudeCLI
            principal = CerebroClaudeCLI(cfg, executor, memoria)
        else:
            principal = CerebroClaude(cfg, executor, memoria)
    except (RuntimeError, ValueError) as e:
        print(f"[cérebro] {e}. Usando o modelo local.")
        return local
    local.so_reserva = True
    return CerebroComReserva(principal, local) if cfg.get("cerebro", {}).get("reserva_local", True) else principal
