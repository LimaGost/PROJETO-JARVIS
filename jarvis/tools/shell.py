"""executar_powershell: a 'porta de emergência' para o que as outras ferramentas não cobrem.

O nível de risco é decidido analisando o comando com o parser oficial do PowerShell
(ps_analisar.ps1). Política: lista de LIBERAÇÃO. Tudo que não está explicitamente
liberado é 🔴. Um nome de comando que só existe em tempo de execução
(ex.: & ("Re"+"move-Item")) também é 🔴, porque não dá para saber o que é.
"""
import base64
import json
import subprocess
from pathlib import Path

from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, ferramenta
from jarvis.tools.caminhos import caminho_literal_seguro

PWSH = "pwsh"
ANALISADOR = Path(__file__).with_name("ps_analisar.ps1")
TIMEOUT_S = 30

# --- 🟢 leitura --------------------------------------------------------------
# Verbos só de leitura. NÃO incluir Format (Format-Volume formata disco) nem Test
# (Test-ComputerSecureChannel -Repair altera a máquina): esses vão na lista explícita.
VERBOS_LEITURA = {"get", "measure", "select", "where", "sort", "convertto", "convertfrom",
                  "group", "compare", "resolve", "split", "join"}
CMDLETS_LIVRES = {
    "format-table", "format-list", "format-wide", "format-custom",
    "test-path", "test-connection", "test-netconnection", "test-json",
    "foreach-object", "out-string", "out-null", "out-default", "write-output", "write-host",
    "set-location", "push-location", "pop-location", "start-sleep", "new-timespan", "clear-host",
}
EXTERNOS_LIVRES = {"ipconfig", "ping", "hostname", "whoami", "systeminfo", "tasklist", "nvidia-smi",
                   "where", "getmac", "netstat", "tracert", "nslookup", "driverquery"}

# --- 🟡 escrita reversível ---------------------------------------------------
# Start-Process e Invoke-Item NÃO entram: executam qualquer coisa
# (Start-Process pwsh -ArgumentList '-c Remove-Item ...'). Para abrir apps/sites há ferramentas próprias.
CMDLETS_REGISTRADOS = {"new-item", "copy-item", "move-item", "rename-item", "add-content",
                       "set-clipboard", "compress-archive", "expand-archive", "mkdir"}
PARAMS_ESCALAM = {"force", "recurse"}  # sobrescrevem / atingem pastas inteiras
CMDLETS_SEM_CAMINHO = {"set-clipboard"}

# subcomandos de programas comuns
SUBCOMANDOS = {
    "winget": {"livre": {"search", "list", "show", "--version"}, "registrado": {"install", "upgrade"}},
    "git": {"livre": {"status", "log", "diff", "show", "branch", "remote", "--version"},
            "registrado": {"add", "commit", "pull", "fetch", "clone", "checkout", "switch", "stash"}},
}

# --- métodos .NET ------------------------------------------------------------
CLASSES_SEGURAS = {"math", "system.math", "io.path", "system.io.path", "string", "system.string",
                   "datetime", "system.datetime", "timespan", "guid", "convert", "char", "int", "double"}
METODOS_ESTATICOS_SEGUROS = {("environment", "getfolderpath"), ("environment", "getenvironmentvariable"),
                             ("environment", "getlogicaldrives"), ("system.environment", "getfolderpath")}
METODOS_INSTANCIA_SEGUROS = {
    "tostring", "toupper", "tolower", "trim", "trimstart", "trimend", "split", "replace", "substring",
    "contains", "startswith", "endswith", "indexof", "lastindexof", "padleft", "padright", "equals",
    "gettype", "toshortdatestring", "tolongdatestring", "adddays", "addhours", "addminutes",
    "compareto", "where", "foreach", "round", "format", "join",
}
METODOS_INSTANCIA_REGISTRADOS = {"closemainwindow"}


def _analisar(comando: str) -> dict:
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(ANALISADOR)],
        input=comando.encode("utf-8"), capture_output=True, timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:300])
    return json.loads(proc.stdout.decode("utf-8-sig"))


def classificar(comando: str) -> tuple[Nivel, list[str]]:
    """Retorna o nível e os motivos (o que levou ao nível mais alto)."""
    a = _analisar(comando)
    if a["erros_sintaxe"]:
        raise ErroFerramenta("o comando tem erro de sintaxe: " + a["erros_sintaxe"][0])

    nivel, motivos = Nivel.LIVRE, []

    def subir(n: Nivel, motivo: str):
        nonlocal nivel
        if n > nivel:
            nivel = n
        if n >= Nivel.REGISTRADO and motivo not in motivos:
            motivos.append(motivo)

    for c in a["comandos"]:
        nome = (c["resolvido"] or "").lower()
        if c["tipo"] == "dinamico":
            subir(Nivel.CONFIRMAR, "um nome de comando montado em tempo de execução")
            continue
        if c["invocacao"] == "Dot":
            subir(Nivel.CONFIRMAR, f"execução de script com ponto ({c['nome']})")
            continue
        if c["tipo"] in ("Cmdlet", "Function"):
            params = {p.lower() for p in c["params"]}
            verbo = nome.split("-")[0]
            if nome in CMDLETS_LIVRES or (verbo in VERBOS_LEITURA and "-" in nome):
                subir(Nivel.LIVRE, nome)
            elif nome in CMDLETS_REGISTRADOS:
                escaladores = params & PARAMS_ESCALAM
                inseguros = [] if nome in CMDLETS_SEM_CAMINHO else \
                    [v for v in c["valores"] if not caminho_literal_seguro(v)]
                if escaladores:
                    subir(Nivel.CONFIRMAR, f"{c['resolvido']} com -{', -'.join(sorted(escaladores))}")
                elif inseguros:
                    subir(Nivel.CONFIRMAR, f"{c['resolvido']} em {inseguros[0][:50]}")
                else:
                    subir(Nivel.REGISTRADO, c["resolvido"])
            else:
                subir(Nivel.CONFIRMAR, c["resolvido"])
        elif c["tipo"] == "externo":
            sub = SUBCOMANDOS.get(nome)
            primeiro = (c["valores"][0].lower() if c["valores"] else "")
            if nome in EXTERNOS_LIVRES:
                subir(Nivel.LIVRE, nome)
            elif sub and primeiro in sub["livre"]:
                subir(Nivel.LIVRE, nome)
            elif sub and primeiro in sub["registrado"]:
                subir(Nivel.REGISTRADO, f"{nome} {primeiro}")
            else:
                subir(Nivel.CONFIRMAR, f"o programa {c['nome']}" + (f" {primeiro}" if primeiro else ""))
        else:  # script externo, comando desconhecido, etc.
            subir(Nivel.CONFIRMAR, f"o comando desconhecido {c['nome']}")

    for m in a["metodos"]:
        metodo = m["metodo"].lower()
        if m["estatico"]:
            classe = m["alvo"].strip("[]").lower()
            if classe in CLASSES_SEGURAS or (classe, metodo) in METODOS_ESTATICOS_SEGUROS:
                continue
            subir(Nivel.CONFIRMAR, f"a chamada .NET {m['alvo']}::{m['metodo']}")
        elif metodo in METODOS_INSTANCIA_SEGUROS:
            continue
        elif metodo in METODOS_INSTANCIA_REGISTRADOS:
            subir(Nivel.REGISTRADO, f"o método {m['metodo']}")
        else:
            subir(Nivel.CONFIRMAR, f"o método {m['metodo']}")

    for alvo in a["atribuicoes_membro"]:
        subir(Nivel.CONFIRMAR, f"alteração de propriedade ({alvo[:40]})")
    for alvo in a["redirecionamentos"]:
        if alvo.strip().lower() != "$null":
            subir(Nivel.CONFIRMAR, f"gravação em arquivo com > ({alvo})")

    return nivel, motivos


def _avaliar(args: dict) -> Avaliacao:
    nivel, motivos = classificar(args["comando"])
    if nivel == Nivel.CONFIRMAR:
        resumo = "executar no PowerShell um comando com " + ", ".join(motivos[:3])
    else:
        resumo = "executar um comando PowerShell" + (f" ({', '.join(motivos)})" if motivos else " de leitura")
    return Avaliacao(nivel, resumo)


@ferramenta(
    "executar_powershell",
    "Executa um comando PowerShell no Windows e devolve a saída. Use SOMENTE quando nenhuma outra "
    "ferramenta resolver. Comandos que alteram o sistema pedirão confirmação ao usuário automaticamente.",
    {"comando": {"type": "string", "description": "O comando PowerShell completo"}},
    obrigatorios=("comando",),
    avaliar=_avaliar,
    externo=True,  # a saída pode trazer conteúdo de arquivos (Get-Content) ou da rede
)
def executar_powershell(comando: str) -> str:
    script = ("$ProgressPreference = 'SilentlyContinue'\n"
              "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + comando)
    codificado = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        proc = subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", codificado],
            capture_output=True, timeout=TIMEOUT_S, cwd=str(Path.home()),
        )
    except subprocess.TimeoutExpired:
        raise ErroFerramenta(f"o comando passou de {TIMEOUT_S} segundos e foi interrompido")
    saida = proc.stdout.decode("utf-8", "replace").strip()
    erro = proc.stderr.decode("utf-8", "replace").strip()
    partes = []
    if saida:
        partes.append(saida)
    if erro:
        partes.append("ERROS:\n" + erro)
    if not partes:
        partes.append("(comando executado, sem saída)" if proc.returncode == 0 else f"(falhou, código {proc.returncode})")
    return "\n".join(partes)
