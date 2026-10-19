"""Gemini CLI (Google) como agente: modo sem tela `gemini -p ... -o json`.

Conferido na CLI 0.64 e em docs/cli/headless.md: -o json devolve UM objeto {response, stats, error?};
--approval-mode plan = só leitura; auto_edit = edita arquivos (comandos de shell continuam pedindo aprovação, e sem
tela isso vira recusa). Login: o modo sem tela usa o login Google já salvo (rode `gemini` uma vez) ou GEMINI_API_KEY
(guardada no cofre pela Central, nunca no config). -p é somado ao que chega pelo stdin: o pedido grande vai pelo stdin.
"""
import json
import os
import time
from pathlib import Path

from jarvis.orquestra.agentes.base import Controle, Disponibilidade, Resultado, Trabalho, agente
from jarvis.orquestra.agentes.cli import AgenteCLI

INSTRUCAO = "Siga as instruções que chegaram pela entrada padrão e responda em português."


@agente
class Gemini(AgenteCLI):
    id = "gemini"
    nome = "Gemini CLI"
    provedor = "Google"
    icone = "sparkles"
    descricao = "Lê muitos arquivos de uma vez: análise, documentação e revisão. Usa o seu login Google."
    descricao_semantica = ("analisar muitos arquivos e documentos de uma vez, contexto longo, escrever documentação, "
                           "resumir, revisar, responder perguntas sobre o projeto")
    capacidades = {"analise": 0.85, "documentacao": 0.85, "consulta": 0.85, "revisao": 0.8, "planejamento": 0.8,
                   "julgamento": 0.75, "implementacao": 0.7, "depuracao": 0.7, "refatoracao": 0.7}
    pode_ler_projeto = pode_editar = True
    custo_modo = "variavel"
    latencia = "media"
    comando_base = "gemini"
    versao_minima = "0.20.0"
    ler_flags = True
    docs = "https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md"
    instalar = "npm install -g @google/gemini-cli"
    login = "rode `gemini` uma vez e entre com a conta Google (ou cadastre uma chave do AI Studio na Central)"

    @property
    def login_cmd(self) -> list[str]:
        cmd = self.comando()
        return list(cmd) if cmd else []

    def _chave(self) -> str:
        return str(self.cofre.ler("gemini").get("apikey") or "")

    def _autenticacao(self) -> Disponibilidade:
        if self._chave():
            return Disponibilidade("disponivel", "chave do Gemini no cofre", True, "chave")
        pasta = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"
        if (pasta / "oauth_creds.json").exists() or (pasta / "google_accounts.json").exists():
            return Disponibilidade("disponivel", "login Google salvo", True, "assinatura")
        return Disponibilidade("desconectado", "sem login: rode `gemini` uma vez (Login) ou cadastre uma chave", False)

    def argumentos(self, trabalho: Trabalho, flags: set) -> list[str]:
        a = ["-p", INSTRUCAO, "-o", "json", "--approval-mode", "auto_edit" if trabalho.escrita else "plan"]
        if "--skip-trust" in flags:
            a.append("--skip-trust")
        modelo = trabalho.modelo or self.modelo_padrao()
        if modelo:
            a += ["-m", modelo]
        return a

    def executar(self, trabalho: Trabalho, controle: Controle) -> Resultado:
        d = self.detectar()
        if not d.instalado:
            return Resultado(False, erro=d.detalhe)
        chave = self._chave()
        r = Resultado(False, cobrado=bool(chave), modelo=trabalho.modelo or self.modelo_padrao() or "padrão do Gemini")
        objetos: list[dict] = []
        inicio = time.time()
        controle.evento("sistema", f"Gemini sem tela · modo {'auto_edit' if trabalho.escrita else 'plan (só leitura)'}")
        p, outras = self.rodar_cli([*d.comando, *self.argumentos(trabalho, d.flags)], trabalho, controle, objetos.append,
                                   env_extra={"GEMINI_API_KEY": chave} if chave else None,
                                   entrada=(trabalho.sistema + "\n\n" if trabalho.sistema else "") + trabalho.prompt)
        resposta = objetos[-1] if objetos else _json_no_texto("\n".join(getattr(p, "stdout_bruto", [])))
        if resposta:
            r.texto = str(resposta.get("response") or "").strip()
            erro = resposta.get("error")
            if erro:
                r.erro = str(erro.get("message") if isinstance(erro, dict) else erro)[:500]
            r.ok = bool(r.texto) and not erro
            for m in ((resposta.get("stats") or {}).get("models") or {}).values():
                tokens = m.get("tokens") or {}
                r.tokens_entrada += int(tokens.get("prompt") or tokens.get("input") or 0)
                r.tokens_saida += int(tokens.get("candidates") or tokens.get("output") or 0)
            if r.texto:
                controle.evento("texto", r.texto[:1500])
            controle.evento("uso", f"{r.tokens_entrada} tokens de entrada, {r.tokens_saida} de saída")
        else:
            r.erro = "o Gemini não devolveu JSON: " + " | ".join(outras[-3:])[:400]
        return self.finalizar(r, p, outras, inicio)


def _json_no_texto(texto: str) -> dict | None:
    """Às vezes o JSON vem misturado com avisos: pega o maior objeto que fecha."""
    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return None
    try:
        obj = json.loads(texto[inicio:fim + 1])
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None
