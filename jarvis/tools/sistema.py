import ctypes
import socket
import subprocess
import time
from datetime import datetime

import psutil

from jarvis.tools.base import Avaliacao, ErroFerramenta, Nivel, ferramenta

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]


@ferramenta("hora_data", "Informa a data e a hora atuais. Use sempre que precisar saber que horas ou que dia é.")
def hora_data() -> str:
    agora = datetime.now()
    return f"{DIAS[agora.weekday()]}, {agora.day} de {MESES[agora.month - 1]} de {agora.year}, {agora:%H:%M}"


def _gb(b: float) -> str:
    return f"{b / 1024**3:.1f} GB"


@ferramenta(
    "info_sistema",
    "Informações deste computador: endereço IP local, uso de CPU, memória RAM, espaço livre nos discos, "
    "uso e temperatura da GPU (placa de vídeo), tempo ligado e os programas que mais usam memória.",
)
def info_sistema() -> str:
    linhas = []
    ips = [a.address for nome, enderecos in psutil.net_if_addrs().items() for a in enderecos
           if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254."))]
    if ips:
        linhas.append("IP local: " + ", ".join(ips))
    cpu = psutil.cpu_percent(interval=0.3)
    linhas.append(f"CPU: {cpu:.0f}% em uso ({psutil.cpu_count()} núcleos lógicos)")
    mem = psutil.virtual_memory()
    linhas.append(f"RAM: {_gb(mem.used)} de {_gb(mem.total)} ({mem.percent:.0f}%)")
    for part in psutil.disk_partitions():
        if "fixed" not in part.opts and "rw" not in part.opts:
            continue
        try:
            uso = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        linhas.append(f"Disco {part.mountpoint} {_gb(uso.free)} livres de {_gb(uso.total)}")
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout.strip()
        nome, uso, usada, total, temp = [x.strip() for x in gpu.split(",")]
        linhas.append(f"GPU {nome}: {uso}% de uso, {usada} de {total} MB de memória, {temp} graus")
    except Exception:
        pass
    horas = (time.time() - psutil.boot_time()) / 3600
    linhas.append(f"Ligado há {horas:.1f} horas")
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except (psutil.Error, TypeError, AttributeError):
            continue
    agrupado: dict[str, int] = {}
    for rss, nome in procs:
        agrupado[nome] = agrupado.get(nome, 0) + rss
    top = sorted(agrupado.items(), key=lambda x: -x[1])[:5]
    linhas.append("Mais memória: " + ", ".join(f"{n.removesuffix('.exe')} {_gb(r)}" for n, r in top))
    return "\n".join(linhas)


_ACOES_ENERGIA = {
    "bloquear": (Nivel.LIVRE, "bloquear a tela"),
    "cancelar": (Nivel.LIVRE, "cancelar o desligamento agendado"),
    "suspender": (Nivel.CONFIRMAR, "suspender o computador agora"),
    "reiniciar": (Nivel.CONFIRMAR, "reiniciar o computador em 30 segundos"),
    "desligar": (Nivel.CONFIRMAR, "desligar o computador em 30 segundos"),
}


def _avaliar_energia(args: dict) -> Avaliacao:
    nivel, resumo = _ACOES_ENERGIA[args["acao"]]
    return Avaliacao(nivel, resumo)


@ferramenta(
    "energia",
    "Bloqueia a tela, suspende, reinicia ou desliga o computador. 'cancelar' cancela um desligamento agendado.",
    {"acao": {"type": "string", "enum": list(_ACOES_ENERGIA)}},
    obrigatorios=("acao",),
    avaliar=_avaliar_energia,
)
def energia(acao: str) -> str:
    if acao == "bloquear":
        ctypes.windll.user32.LockWorkStation()
        return "Tela bloqueada."
    if acao == "cancelar":
        r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True)
        return "Desligamento cancelado." if r.returncode == 0 else "Não havia desligamento agendado."
    if acao == "suspender":
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        return "Suspendendo."
    flag = "/r" if acao == "reiniciar" else "/s"
    r = subprocess.run(["shutdown", flag, "/t", "30"], capture_output=True, text=True)
    if r.returncode != 0:
        raise ErroFerramenta(r.stderr.strip() or "o Windows recusou o comando")
    return f"O computador vai {acao} em 30 segundos. Diga 'cancelar desligamento' para impedir."
