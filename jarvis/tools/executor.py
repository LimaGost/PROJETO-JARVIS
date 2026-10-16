"""Executa as ferramentas pedidas pelo LLM aplicando as permissões 🟢🟡🔴 e gravando tudo em log."""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from jarvis.tools.base import REGISTRO, Avaliacao, ErroFerramenta, Ferramenta, Nivel

MAX_RESULTADO = 1500  # caracteres devolvidos ao LLM (o contexto do modelo é pequeno)

NEGADO = ("O usuário NÃO autorizou esta ação. Ela não foi executada. "
          "Não tente fazer o mesmo por outra ferramenta; apenas avise que cancelou.")
BLOQUEADO_NO_TURNO = ("Ação perigosa bloqueada: o usuário já negou uma ação neste pedido. "
                      "Não foi executada. Avise o usuário e pare.")
TURNOS_CONTAMINADO = 3  # pedidos em que o conteúdo externo lido ainda está na memória da conversa


class Executor:
    def __init__(self, confirmar: Callable[[str], bool], pasta_logs: str,
                 registro: dict[str, Ferramenta] | None = None, avisar: Callable[[str], None] | None = None):
        """confirmar(resumo) -> True só se o usuário disse sim. avisar(texto) mostra detalhes na tela."""
        self.registro = REGISTRO if registro is None else registro
        self.confirmar = confirmar
        self.avisar = avisar or print
        Path(pasta_logs).mkdir(parents=True, exist_ok=True)
        self.log = Path(pasta_logs) / "acoes.jsonl"
        self._negou_no_turno = False
        self._turno = 0
        self._contaminado_ate = -1  # último turno em que conteúdo externo ainda pode influenciar o modelo

    def schemas(self) -> list[dict]:
        return [f.schema() for f in self.registro.values()]

    def novo_turno(self):
        self._negou_no_turno = False
        self._turno += 1

    def esquecer(self):
        """A conversa foi apagada: o conteúdo externo lido saiu da memória do modelo."""
        self._contaminado_ate = -1

    @property
    def contaminado(self) -> bool:
        return self._turno <= self._contaminado_ate

    def executar(self, nome: str, args: dict | None) -> str:
        ferramenta = self.registro.get(nome)
        if ferramenta is None:
            return f"Erro: a ferramenta '{nome}' não existe."
        args = _normalizar_args(ferramenta, args or {})
        faltando = [p for p in ferramenta.obrigatorios if args.get(p) in (None, "")]
        if faltando:
            return f"Erro: faltou informar {', '.join(faltando)}."

        try:
            avaliacao = ferramenta.avaliacao(args)
        except ErroFerramenta as e:
            self._registrar(nome, args, None, "erro", str(e))
            return f"Erro: {e}"
        except Exception as e:  # se não deu para avaliar o risco, não executa
            self._registrar(nome, args, None, "erro", f"falha ao avaliar: {e}")
            return "Erro interno ao avaliar a segurança desta ação; ela não foi executada."

        if avaliacao.nivel == Nivel.REGISTRADO and self.contaminado:
            # depois de ler um arquivo/site, um pedido de escrita pode ter vindo de instruções escondidas nele
            avaliacao = Avaliacao(Nivel.CONFIRMAR, avaliacao.resumo + ", um pedido feito depois de ler conteúdo externo")

        if avaliacao.nivel == Nivel.CONFIRMAR:
            if self._negou_no_turno:
                self._registrar(nome, args, avaliacao, "bloqueado", "")
                return BLOQUEADO_NO_TURNO
            # o resumo falado é curto; na tela vai o pedido completo, para conferir antes de dizer sim
            self.avisar(f"  🔴 pedindo confirmação: {nome} {json.dumps(args, ensure_ascii=False)}")
            if not self.confirmar(avaliacao.resumo):
                self._negou_no_turno = True
                self._registrar(nome, args, avaliacao, "negado", "")
                return NEGADO

        t = time.time()
        try:
            resultado = str(ferramenta.funcao(**args))
            status = "ok"
        except ErroFerramenta as e:
            resultado, status = f"Erro: {e}", "erro"
        except Exception as e:
            resultado, status = f"Erro inesperado: {type(e).__name__}: {e}", "erro"

        if ferramenta.externo and status == "ok":
            self._contaminado_ate = self._turno + TURNOS_CONTAMINADO - 1
        if len(resultado) > MAX_RESULTADO:
            resultado = resultado[:MAX_RESULTADO] + "\n[...resultado cortado]"
        self._registrar(nome, args, avaliacao, status, resultado, time.time() - t)
        return resultado

    def _registrar(self, nome, args, avaliacao, status, resultado, duracao=0.0):
        nivel = avaliacao.nivel.name if avaliacao else "?"
        icone = {"LIVRE": "🟢", "REGISTRADO": "🟡", "CONFIRMAR": "🔴"}.get(nivel, "⚪")
        self.avisar(f"  {icone} {nome} {json.dumps(args, ensure_ascii=False)[:160]} -> {status}")
        registro = {
            "quando": datetime.now().isoformat(timespec="seconds"),
            "ferramenta": nome,
            "args": args,
            "nivel": nivel,
            "resumo": avaliacao.resumo if avaliacao else None,
            "status": status,
            "duracao_s": round(duracao, 2),
            "resultado": resultado[:500],
        }
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def _normalizar_args(ferramenta: Ferramenta, args: dict) -> dict:
    """Descarta argumentos desconhecidos e corrige tipos (o modelo às vezes manda '30' em vez de 30)."""
    limpos = {}
    for nome, valor in args.items():
        schema = ferramenta.parametros.get(nome)
        if schema is None:
            continue
        tipo = schema.get("type")
        try:
            if tipo == "integer" and not isinstance(valor, int):
                valor = int(float(str(valor).replace("%", "").strip()))
            elif tipo == "number" and not isinstance(valor, (int, float)):
                valor = float(str(valor).replace("%", "").strip())
            elif tipo == "boolean" and not isinstance(valor, bool):
                valor = str(valor).strip().lower() in ("true", "sim", "1", "yes")
            elif tipo == "string" and not isinstance(valor, str):
                valor = str(valor)
        except ValueError:
            continue
        if "enum" in schema and valor not in schema["enum"]:
            continue
        limpos[nome] = valor
    return limpos
