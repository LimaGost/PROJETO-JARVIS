"""Arquivos e pastas.

Regras: nunca sobrescreve em silêncio, apagar vai para a Lixeira e sempre pede confirmação,
e raízes de disco / pasta do usuário / pastas conhecidas não podem ser apagadas nem movidas.
"""
import os
import shutil
import time
from pathlib import Path

from send2trash import send2trash

from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, conteudo_externo, ferramenta
from jarvis.tools.caminhos import intocavel, protegido, resolver, sem_acento

CAMINHO = {"type": "string", "description": "Caminho. Aceita apelidos: 'área de trabalho', 'documentos', "
                                            "'downloads', 'imagens', ex.: 'área de trabalho/relatórios'"}
EXTENSOES_TEXTO = {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".ts", ".html", ".css", ".xml",
                   ".yaml", ".yml", ".ini", ".cfg", ".sql", ".bat", ".ps1", ".java", ".c", ".cpp", ".cs", ".php"}
EXTENSOES_ABRIR_LIVRE = {".txt", ".md", ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".csv", ".jpg",
                         ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp3", ".wav", ".flac", ".mp4", ".mkv",
                         ".avi", ".mov", ".json", ".log", ".odt", ".ods"}
IGNORAR_BUSCA = {"appdata", ".git", "node_modules", "$recycle.bin", ".venv", "__pycache__", "windows",
                 "program files", "program files (x86)", "programdata"}


def _onde(p: Path) -> str:
    """Descrição curta para falar: 'relatorio.txt, em Downloads'."""
    return f"'{p.name}', em {p.parent.name or p.parent}"


def _tamanho(n: int) -> str:
    for unidade in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unidade == "GB":
            return f"{n:.0f} {unidade}" if unidade == "bytes" else f"{n:.1f} {unidade}"
        n /= 1024


@ferramenta("listar_pasta", "Lista o conteúdo de uma pasta.", {"caminho": CAMINHO}, obrigatorios=("caminho",))
def listar_pasta(caminho: str) -> str:
    p = resolver(caminho)
    if not p.is_dir():
        raise ErroFerramenta(f"a pasta {p} não existe")
    pastas, arquivos = [], []
    for item in p.iterdir():
        try:
            if item.is_dir():
                pastas.append(item.name)
            else:
                arquivos.append(f"{item.name} ({_tamanho(item.stat().st_size)})")
        except OSError:
            continue
    linhas = [f"{p}: {len(pastas)} pastas, {len(arquivos)} arquivos"]
    linhas += [f"[pasta] {n}" for n in sorted(pastas, key=str.lower)[:30]]
    linhas += sorted(arquivos, key=str.lower)[:40]
    return "\n".join(linhas)


@ferramenta(
    "buscar_arquivos",
    "Procura arquivos ou pastas pelo nome (ou parte do nome).",
    {"nome": {"type": "string", "description": "Nome ou parte do nome"},
     "onde": {**CAMINHO, "description": "Pasta onde procurar (padrão: pasta do usuário)"}},
    obrigatorios=("nome",),
)
def buscar_arquivos(nome: str, onde: str = "~") -> str:
    base = resolver(onde)
    alvo = sem_acento(nome)
    achados, limite = [], time.time() + 5
    for raiz, dirs, arquivos in os.walk(base):
        dirs[:] = [d for d in dirs if d.lower() not in IGNORAR_BUSCA and not d.startswith(".")]
        if raiz.count(os.sep) - str(base).count(os.sep) >= 6:
            dirs[:] = []
        for n in dirs + arquivos:
            if alvo in sem_acento(n):
                achados.append(os.path.join(raiz, n))
        if len(achados) >= 20 or time.time() > limite:
            break
    if not achados:
        return f"Nada encontrado com '{nome}' em {base}."
    return "\n".join(achados[:20]) + ("\n(busca parcial: tempo esgotado)" if time.time() > limite else "")


@ferramenta("ler_arquivo", "Lê o conteúdo de um arquivo de texto.", {"caminho": CAMINHO}, obrigatorios=("caminho",),
            externo=True)
def ler_arquivo(caminho: str) -> str:
    p = resolver(caminho)
    if not p.is_file():
        raise ErroFerramenta(f"o arquivo {p} não existe")
    if p.suffix.lower() not in EXTENSOES_TEXTO:
        raise ErroFerramenta(f"ainda não sei ler arquivos {p.suffix}")
    if p.stat().st_size > 1_000_000:
        raise ErroFerramenta("arquivo grande demais (mais de 1 MB)")
    bruto = p.read_bytes()
    try:
        texto = bruto.decode("utf-8")
    except UnicodeDecodeError:
        texto = bruto.decode("cp1252", "replace")
    # o conteúdo é DADO: um arquivo pode conter "instruções" escritas por outra pessoa
    return conteudo_externo(f"arquivo {p.name}", texto[:1100])


# ---------------------------------------------------------------- escrita
def _nivel_escrita(*caminhos: Path) -> Nivel:
    return Nivel.CONFIRMAR if any(protegido(c) for c in caminhos) else Nivel.REGISTRADO


def _avaliar_criar_pasta(args):
    p = resolver(args["caminho"])
    return Avaliacao(_nivel_escrita(p), f"criar a pasta {_onde(p)}")


@ferramenta("criar_pasta", "Cria uma pasta (e as pastas intermediárias).", {"caminho": CAMINHO},
            obrigatorios=("caminho",), avaliar=_avaliar_criar_pasta)
def criar_pasta(caminho: str) -> str:
    p = resolver(caminho)
    p.mkdir(parents=True, exist_ok=True)
    return f"Pasta criada: {p}"


def _avaliar_escrever(args):
    p = resolver(args["caminho"])
    modo = args.get("modo", "criar")
    if p.is_dir():
        raise ErroFerramenta(f"{p} é uma pasta")
    if modo == "criar" and p.exists():
        raise ErroFerramenta(f"{p.name} já existe. Use modo 'substituir' ou 'acrescentar'")
    if modo == "substituir" and p.exists():
        return Avaliacao(Nivel.CONFIRMAR, f"substituir todo o conteúdo do arquivo {_onde(p)}")
    acao = {"criar": "criar o arquivo", "substituir": "criar o arquivo", "acrescentar": "acrescentar texto em"}[modo]
    return Avaliacao(_nivel_escrita(p), f"{acao} {_onde(p)}")


@ferramenta(
    "escrever_arquivo",
    "Cria um arquivo de texto, substitui o conteúdo ou acrescenta texto no final.",
    {"caminho": CAMINHO, "conteudo": {"type": "string"},
     "modo": {"type": "string", "enum": ["criar", "substituir", "acrescentar"], "description": "Padrão: criar"}},
    obrigatorios=("caminho", "conteudo"),
    avaliar=_avaliar_escrever,
)
def escrever_arquivo(caminho: str, conteudo: str, modo: str = "criar") -> str:
    p = resolver(caminho)
    if len(conteudo) > 200_000:
        raise ErroFerramenta("conteúdo grande demais")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if modo == "acrescentar" else "w", encoding="utf-8") as f:
        f.write(conteudo)
    return f"Arquivo salvo: {p}"


def _origem_destino(args) -> tuple[Path, Path]:
    origem, destino = resolver(args["origem"]), resolver(args["destino"])
    if not origem.exists():
        raise ErroFerramenta(f"{origem} não existe")
    if destino.is_dir():
        destino = destino / origem.name
    if destino.exists():
        raise ErroFerramenta(f"já existe {destino}; não vou sobrescrever")
    return origem, destino


def _avaliar_mover(args):
    origem, destino = _origem_destino(args)
    if intocavel(origem):
        raise ErroFerramenta(f"não posso mover {origem}: é uma pasta essencial do sistema")
    verbo = "renomear" if origem.parent == destino.parent else "mover"
    alvo = f"para '{destino.name}'" if verbo == "renomear" else f"para {destino.parent.name}"
    return Avaliacao(_nivel_escrita(origem, destino), f"{verbo} {_onde(origem)} {alvo}")


@ferramenta(
    "mover",
    "Move ou renomeia um arquivo ou pasta. Nunca sobrescreve.",
    {"origem": CAMINHO, "destino": CAMINHO},
    obrigatorios=("origem", "destino"),
    avaliar=_avaliar_mover,
)
def mover(origem: str, destino: str) -> str:
    o, d = _origem_destino({"origem": origem, "destino": destino})
    shutil.move(str(o), str(d))
    return f"Movido para {d}"


def _avaliar_copiar(args):
    origem, destino = _origem_destino(args)
    return Avaliacao(_nivel_escrita(destino), f"copiar {_onde(origem)} para {destino.parent.name}")


@ferramenta(
    "copiar",
    "Copia um arquivo ou pasta. Nunca sobrescreve.",
    {"origem": CAMINHO, "destino": CAMINHO},
    obrigatorios=("origem", "destino"),
    avaliar=_avaliar_copiar,
)
def copiar(origem: str, destino: str) -> str:
    o, d = _origem_destino({"origem": origem, "destino": destino})
    if o.is_dir():
        shutil.copytree(o, d)
    else:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(o, d)
    return f"Copiado para {d}"


def _avaliar_apagar(args):
    p = resolver(args["caminho"])
    if not p.exists():
        raise ErroFerramenta(f"{p} não existe")
    if intocavel(p):
        raise ErroFerramenta(f"não posso apagar {p}: é uma pasta essencial")
    if p.is_dir():
        n = sum(1 for _ in zip(range(10_001), p.rglob("*")))
        qtd = "mais de 10 mil itens" if n > 10_000 else f"{n} itens"
        return Avaliacao(Nivel.CONFIRMAR, f"mandar para a lixeira a pasta {_onde(p)}, com {qtd}")
    return Avaliacao(Nivel.CONFIRMAR, f"mandar para a lixeira o arquivo {_onde(p)}")


@ferramenta(
    "apagar",
    "Manda um arquivo ou pasta para a Lixeira (pede confirmação ao usuário automaticamente).",
    {"caminho": CAMINHO},
    obrigatorios=("caminho",),
    avaliar=_avaliar_apagar,
)
def apagar(caminho: str) -> str:
    p = resolver(caminho)
    send2trash(str(p))
    return f"{p.name} foi para a Lixeira."


def _avaliar_abrir(args):
    p = resolver(args["caminho"])
    if not p.exists():
        raise ErroFerramenta(f"{p} não existe")
    if p.is_dir() or p.suffix.lower() in EXTENSOES_ABRIR_LIVRE:
        return Avaliacao(Nivel.LIVRE, f"abrir {_onde(p)}")
    # .exe, .bat, .ps1, .lnk, .vbs... abrir = executar
    return Avaliacao(Nivel.CONFIRMAR, f"executar o arquivo {_onde(p)}")


@ferramenta(
    "abrir_pasta_ou_arquivo",
    "Abre uma PASTA no Explorador de Arquivos (ex.: downloads, documentos) ou um arquivo com o programa padrão (PDF, foto, planilha, música...).",
    {"caminho": CAMINHO},
    obrigatorios=("caminho",),
    avaliar=_avaliar_abrir,
)
def abrir_pasta_ou_arquivo(caminho: str) -> str:
    p = resolver(caminho)
    os.startfile(str(p))
    return f"Abrindo {p.name}."
