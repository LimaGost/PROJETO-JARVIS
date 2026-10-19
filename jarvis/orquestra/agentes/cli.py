"""Peças comuns dos agentes que são CLIs (Claude Code, Codex, Gemini): achar o executável, ler a versão e as
opções que a versão instalada aceita, rodar com o gateway e juntar os eventos."""
import json
import re
import time

from jarvis.orquestra import gateway
from jarvis.orquestra.agentes.base import Agente, Controle, Deteccao, Resultado, Trabalho, parece_transitorio
from jarvis.orquestra.seguranca import ambiente_limpo

_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]+)")


class AgenteCLI(Agente):
    tipo = "cli"
    suporta_pausar = True              # o gateway congela a árvore de processos (psutil)
    ler_flags = False                  # lê `--help` para saber o que a versão instalada aceita

    def comando(self) -> list[str] | None:
        return gateway.resolver_executavel(self.comando_base, self.cfg.get("comando"))

    def _detectar(self) -> Deteccao:
        cmd = self.comando()
        if cmd is None:
            return Deteccao(False, detalhe=f"não achei '{self.comando_base}' no PATH. Instale: {self.instalar}")
        codigo, saida, erros = gateway.rodar([*cmd, "--version"], env=ambiente_limpo(), limite_s=30)
        texto = (saida or erros).strip()
        if codigo != 0 and not texto:
            return Deteccao(False, comando=cmd, detalhe=f"'{self.comando_base} --version' falhou (código {codigo})")
        versao = re.search(r"\d+\.\d+(?:\.\d+)?", texto)
        flags: set = set()
        if self.ler_flags:
            _, ajuda, ajuda_err = gateway.rodar([*cmd, "--help"], env=ambiente_limpo(), limite_s=30)
            flags = set(_FLAG.findall(ajuda + "\n" + ajuda_err))
        return Deteccao(True, versao.group(0) if versao else texto[:40], cmd, texto.splitlines()[0][:120] if texto else "",
                        flags)

    # ------------------------------------------------------------ execução comum
    def rodar_cli(self, argv: list[str], trabalho: Trabalho, controle: Controle, ao_json, env_extra: dict | None = None,
                  entrada: str | None = None) -> tuple[object, list[str]]:
        """Roda a CLI, entrega cada linha JSON a `ao_json(evento)`. Devolve (processo, linhas que não eram JSON).
        `processo.stdout_bruto` guarda as linhas do stdout que não eram JSON (JSON em várias linhas, por exemplo)."""
        outras: list[str] = []
        brutas: list[str] = []

        def ao_linha(fluxo: str, linha: str):
            if not linha.strip():
                return
            if fluxo == "stdout":
                try:
                    ev = json.loads(linha)
                except ValueError:
                    brutas.append(linha)
                    outras.append(linha)
                    return
                if isinstance(ev, dict):
                    ao_json(ev)
                else:
                    brutas.append(linha)       # pedaço de um JSON em várias linhas (ex.: um número sozinho)
            else:
                outras.append(linha)
                if len(outras) <= 200:
                    controle.evento("stderr", linha[:500])
        p = gateway.Processo(argv, trabalho.cwd, ambiente_limpo(env_extra), trabalho.prompt if entrada is None else entrada,
                             trabalho.limite_s, trabalho.limite_mem_mb, ao_linha, nome=self.id)
        p.stdout_bruto = brutas
        controle.registrar(p)
        p.iniciar()
        p.esperar(trabalho.limite_s + 120)
        return p, outras

    @staticmethod
    def finalizar(r: Resultado, p, outras: list[str], inicio: float) -> Resultado:
        r.duracao_s = round(time.time() - inicio, 1)
        if p.estado == "cancelado":
            r.ok, r.cancelado, r.erro = False, True, r.erro or p.motivo_fim
        elif p.estado in ("estourou_tempo", "estourou_memoria", "falhou_iniciar"):
            r.ok, r.erro = False, p.motivo_fim
        elif p.codigo not in (0, None) and r.ok:
            r.ok = False
            r.erro = r.erro or f"terminou com código {p.codigo}: " + " | ".join(outras[-3:])[:400]
        if not r.ok and not r.cancelado:
            r.transitorio = parece_transitorio(r.erro + " " + " ".join(outras[-5:]))
        return r
