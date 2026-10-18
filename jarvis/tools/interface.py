"""Controle de qualquer programa do Windows pela acessibilidade (UI Automation): ver, clicar, digitar, atalhos.

Por que não um servidor MCP pronto: o Windows-MCP exige Python 3.14, liga telemetria por padrão e traz
ferramentas (PowerShell, Registro, Arquivos, Processos) que contornariam o guarda de segurança do Jarvis.

Segurança. O que um clique faz não dá para saber pelo clique, então o nível é decidido pelo CONTEXTO:
  - a HUD do próprio Jarvis é intocável (senão ele clicaria no "Confirmar" dela e se autorizaria);
  - janelas sensíveis (terminal, Registro, UAC, banco/pagamento...) e botões de risco (excluir, enviar,
    pagar, instalar, fechar...) viram 🔴;
  - atalhos que abrem portas (Win+R, Ctrl+Alt+Del, Alt+F4...) viram 🔴;
  - o resto é 🟡. Depois de ler uma janela que mostra texto de terceiros (navegador, e-mail, chat, documento)
    tudo vira 🔴 por alguns pedidos (regra geral do Executor): o texto pode conter ordens escondidas.
"""
import re
import threading
import time
from dataclasses import dataclass

import psutil

from jarvis.tools.base import AO_NOVO_PEDIDO, Avaliacao, ErroFerramenta, Nivel, conteudo_externo, ferramenta
from jarvis.tools.caminhos import sem_acento

try:
    import uiautomation as auto
except Exception:  # sem a biblioteca as ferramentas de interface só avisam
    auto = None

MAX_ELEMENTOS = 70
MAX_VISITADOS = 2500
ORCAMENTO_S = 3.0
ORCAMENTO_CHARS = 1000   # texto da lista de elementos (o aviso de segurança que vai junto ocupa o resto dos 1500)
VALIDADE_LISTA_S = 120   # os números de ver_janela envelhecem: a tela muda

TIPOS = {  # tipo de controle -> como o modelo vai ler
    "ButtonControl": "botão", "EditControl": "campo", "CheckBoxControl": "caixa", "RadioButtonControl": "opção",
    "ComboBoxControl": "lista", "MenuItemControl": "menu", "TabItemControl": "aba", "ListItemControl": "item",
    "HyperlinkControl": "link", "TreeItemControl": "item", "SplitButtonControl": "botão", "SliderControl": "controle",
    "DataItemControl": "item", "DocumentControl": "documento",
}
TIPOS_TEXTO = {"TextControl"}

# ---------------------------------------------------------------- classificação (puro, testável)
PROCESSOS_SENSIVEIS = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe", "openconsole.exe",
    "regedit.exe", "mmc.exe", "taskmgr.exe", "consent.exe", "systemsettings.exe", "gpedit.msc", "msiexec.exe",
    "securityhealthsystray.exe", "windowsdefender.exe", "control.exe", "compmgmt.msc", "eventvwr.exe", "diskmgmt.msc",
}
TITULOS_SENSIVEIS = re.compile(
    r"controle de conta|user account control|prompt de comando|command prompt|administrador:|powershell|"
    r"editor do registro|registry editor|gerenciador de tarefas|task manager|gerenciamento de disco|"
    r"banco|bank|nubank|itau|bradesco|santander|caixa econ|paypal|mercado ?pago|\bpix\b|cartao|fatura|boleto|"
    r"pagamento|payment|checkout|carrinho|binance|corretora|investiment|senha|password|gerenciador de senhas|"
    r"bitwarden|lastpass|1password|keepass|autenticad|token|configuracoes de seguranca|firewall")
BOTOES_DE_RISCO = re.compile(
    r"\b(excluir|apagar|remover|deletar|delete|desinstalar|uninstall|formatar|format|esvaziar|descartar|"
    r"nao salvar|don.?t save|enviar|send|publicar|postar|post|pagar|pay|comprar|buy|purchase|finalizar compra|"
    r"confirmar pedido|place order|transferir|transfer|autorizar|permitir|allow|aceitar|concordo|accept|"
    r"instalar|install|executar|run|reiniciar|restart|desligar|shut ?down|suspender|sair da conta|logout|"
    r"sign out|encerrar sessao|redefinir|reset|restaurar|limpar tudo|clear all|assinar|subscribe|cancelar assinatura|"
    r"fechar|close|sair|exit|quit)\b")
ATALHOS_DE_RISCO = {
    frozenset(x) for x in (
        ("win", "r"), ("ctrl", "alt", "delete"), ("ctrl", "alt", "del"), ("alt", "f4"), ("ctrl", "shift", "esc"),
        ("win", "x"), ("shift", "delete"), ("shift", "del"), ("ctrl", "shift", "delete"), ("ctrl", "shift", "del"),
        ("win", "l"), ("win", "e"), ("win", "i"), ("win", "s"), ("win", "d"), ("alt", "tab"),
    )
}
APELIDOS_TECLAS = {"control": "ctrl", "windows": "win", "super": "win", "meta": "win", "return": "enter",
                   "escape": "esc", "del": "delete", "pgup": "pageup", "pgdn": "pagedown", "espaco": "space"}

# janelas que mostram texto escrito por outras pessoas/sites: ler isso pode trazer ordens escondidas
PROCESSOS_COM_TEXTO_DE_TERCEIROS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "iexplore.exe",
    "outlook.exe", "olk.exe", "thunderbird.exe", "mailspring.exe", "hxoutlook.exe",
    "discord.exe", "whatsapp.exe", "whatsapp.root.exe", "telegram.exe", "slack.exe", "teams.exe", "ms-teams.exe", "signal.exe",
    "acrord32.exe", "acrobat.exe", "winword.exe", "excel.exe", "powerpnt.exe", "foxitreader.exe", "sumatrapdf.exe",
}
TITULOS_COM_TEXTO_DE_TERCEIROS = re.compile(
    r"gmail|outlook|e-?mail|caixa de entrada|whatsapp|telegram|discord|slack|teams|chrome|edge|firefox|navegador|"
    r"\.pdf|\.docx?|\.xlsx?|google (docs|drive|planilhas)|notion|linkedin|facebook|instagram|twitter|reddit")


@dataclass
class Contexto:
    titulo: str
    processo: str


def eh_hud(titulo: str, processo: str = "") -> bool:
    """A HUD do Jarvis (janela de aplicativo ou aba): nunca pode ser operada pela interface."""
    t = (titulo or "").strip().lower()
    return t.startswith("j.a.r.v.i.s") or "127.0.0.1:87" in t  # título da página ou o endereço dela numa aba


def janela_sensivel(ctx: Contexto) -> str | None:
    """Motivo, se interagir com esta janela exige confirmação."""
    if ctx.processo.lower() in PROCESSOS_SENSIVEIS:
        return f"a janela {ctx.processo.removesuffix('.exe')}"
    if TITULOS_SENSIVEIS.search(sem_acento(ctx.titulo)):
        return f"a janela “{ctx.titulo[:40]}”"
    return None


def texto_de_terceiros(ctx: Contexto) -> bool:
    return ctx.processo.lower() in PROCESSOS_COM_TEXTO_DE_TERCEIROS or bool(TITULOS_COM_TEXTO_DE_TERCEIROS.search(sem_acento(ctx.titulo)))


def botao_de_risco(nome: str) -> bool:
    return bool(nome) and bool(BOTOES_DE_RISCO.search(sem_acento(nome)))


def normalizar_atalho(texto: str) -> list[str]:
    return [APELIDOS_TECLAS.get(t.strip().lower(), t.strip().lower())
            for t in re.split(r"[+\s]+", texto.strip()) if t.strip()]


def atalho_de_risco(teclas: list[str]) -> bool:
    return frozenset(teclas) in ATALHOS_DE_RISCO


def nivel_da_acao(*, ctx: Contexto, elementos: list[str] | None = None, atalho: list[str] | None = None) -> tuple[Nivel, str]:
    """(nível, motivo) de uma ação de interface. Erro (ErroFerramenta) se for a HUD."""
    if eh_hud(ctx.titulo, ctx.processo):
        raise ErroFerramenta("essa é a minha própria interface (HUD): não posso operá-la, só você")
    motivo = janela_sensivel(ctx)
    if motivo:
        return Nivel.CONFIRMAR, motivo
    for nome in elementos or []:
        if botao_de_risco(nome):
            return Nivel.CONFIRMAR, f"o botão “{nome[:40]}”"
    if atalho and atalho_de_risco(atalho):
        return Nivel.CONFIRMAR, f"o atalho {'+'.join(atalho)}"
    return Nivel.REGISTRADO, ""


def nome_do_app(ctx: Contexto) -> str:
    """Para falar: 'Calculadora', não 'ApplicationFrameHost' (todo app da Microsoft Store aparece assim)."""
    proc = ctx.processo.removesuffix(".exe")
    return ctx.titulo[:30] if proc.lower() in ("applicationframehost", "") else proc


def casar_nome(token: str, nomes: list[str]) -> int | None:
    """Índice do elemento cujo nome corresponde ao texto (sem acento/maiúsculas): exato, depois começa com, depois contém."""
    alvo = sem_acento(token).strip()
    if not alvo:
        return None
    norm = [sem_acento(n).strip() for n in nomes]
    for teste in (lambda n: n == alvo, lambda n: n.startswith(alvo), lambda n: alvo in n):
        for i, n in enumerate(norm):
            if teste(n):
                return i
    return None


# ---------------------------------------------------------------- acesso à tela
_ELEMENTOS: dict[int, object] = {}   # id do último ver_janela -> controle
_ULTIMA: dict = {"titulo": "", "processo": "", "quando": 0.0, "externo": True}


def _esquecer_lista():
    _ELEMENTOS.clear()


AO_NOVO_PEDIDO.append(_esquecer_lista)  # números de um pedido não valem no seguinte


_THREAD = threading.local()


def _exigir():
    if auto is None:
        raise ErroFerramenta("a biblioteca uiautomation não está instalada")
    if not getattr(_THREAD, "com", False):
        # as ferramentas rodam em threads (ponte do claude-cli, conversa): sem isso a UI Automation falha nelas
        try:
            auto.InitializeUIAutomationInCurrentThread()
        except Exception:
            pass
        _THREAD.com = True


def _processo(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except psutil.Error:
        return ""


def _limpo(texto: str) -> str:
    """Remove ícones de fonte (caracteres da área privada) e espaços sobrando."""
    return re.sub(r"[-]", "", texto or "").strip()


def _janelas_de_topo():
    """Janelas visíveis. Uma janela pode sumir entre a listagem e a leitura (COMError): ela só é pulada."""
    _exigir()
    for tentativa in range(3):
        try:
            filhos = auto.GetRootControl().GetChildren()
            break
        except Exception:
            time.sleep(0.2)
    else:
        return []
    visiveis = []
    for j in filhos:
        try:
            if j.Name and not j.IsOffscreen:
                visiveis.append(j)
        except Exception:
            continue
    return visiveis


def _achar_janela(titulo: str = ""):
    """Janela pelo nome (parte do título ou do programa). Vazio = a que está em primeiro plano."""
    _exigir()
    if not titulo.strip():
        j = auto.GetForegroundControl().GetTopLevelControl()
        if j is None:
            raise ErroFerramenta("não há janela em primeiro plano")
        return j
    alvo = sem_acento(titulo).strip()
    candidatas = []
    for j in _janelas_de_topo():
        try:
            nome = sem_acento(j.Name)
            proc = sem_acento(_processo(j.ProcessId).removesuffix(".exe"))
        except Exception:
            continue  # a janela sumiu agora
        if alvo in nome or alvo == proc or (len(alvo) > 3 and alvo in proc):
            candidatas.append(j)
    # nunca devolve a HUD por engano: se o nome só casa com ela, é erro
    candidatas = [j for j in candidatas if not eh_hud(_limpo(j.Name))] or candidatas
    if not candidatas:
        nomes = set()
        for j in _janelas_de_topo():
            try:
                if j.Name not in ("Barra de Tarefas", "Program Manager"):
                    nomes.add(_limpo(j.Name)[:30])
            except Exception:
                continue
        abertas = ", ".join(sorted(nomes)[:8])
        raise ErroFerramenta(f"não achei uma janela com '{titulo}'. Abertas: {abertas}")
    return candidatas[0]


def _contexto(janela) -> Contexto:
    return Contexto(_limpo(janela.Name), _processo(janela.ProcessId))


def _varrer(janela) -> tuple[list[tuple[object, str, str]], list[str]]:
    """(elementos interativos [(controle, rótulo do tipo, nome)], textos visíveis)."""
    inicio, visitados = time.time(), 0
    elementos, textos, vistos = [], [], set()
    pilha = [janela]
    while pilha and visitados < MAX_VISITADOS and time.time() - inicio < ORCAMENTO_S:
        c = pilha.pop()
        try:
            filhos = c.GetChildren()
        except Exception:
            continue
        for f in reversed(filhos):
            visitados += 1
            try:
                if f.IsOffscreen:
                    continue
                tipo, nome = f.ControlTypeName, _limpo(f.Name)
            except Exception:
                continue
            if tipo in TIPOS and (nome or tipo in ("EditControl", "ComboBoxControl")):
                chave = (tipo, nome)
                if chave not in vistos or tipo == "EditControl":
                    vistos.add(chave)
                    elementos.append((f, TIPOS[tipo], nome or f.AutomationId or "sem nome"))
            elif tipo in TIPOS_TEXTO and nome and len(textos) < 12 and nome not in textos:
                textos.append(nome)
            pilha.append(f)

    def pos(item):  # ordem de leitura: cima->baixo, esquerda->direita
        try:
            r = item[0].BoundingRectangle
            return (round(r.top / 40), r.left)
        except Exception:
            return (0, 0)
    elementos.sort(key=pos)
    return elementos, textos


# ---------------------------------------------------------------- ferramentas
def _avaliar_ver(args: dict) -> Avaliacao:
    ctx = _contexto(_achar_janela(args.get("janela", "")))
    if eh_hud(ctx.titulo, ctx.processo):
        raise ErroFerramenta("essa é a minha própria interface (HUD)")
    return Avaliacao(Nivel.LIVRE, f"olhar a janela {ctx.titulo[:40]}")  # só leitura


@ferramenta(
    "ver_janela",
    "Lista os botões, campos e textos de uma janela aberta, cada um com um número. Use ANTES de clicar em "
    "qualquer programa. 'janela' é parte do nome (ex.: Calculadora, Bloco de notas); vazio = janela ativa.",
    {"janela": {"type": "string", "description": "Parte do nome da janela ou do programa"}},
    externo=lambda args: _ULTIMA.get("externo", True),  # decidido em ver_janela, conforme a janela
    avaliar=_avaliar_ver,
)
def ver_janela(janela: str = "") -> str:
    j = _achar_janela(janela)
    ctx = _contexto(j)
    if eh_hud(ctx.titulo):
        raise ErroFerramenta("essa é a minha própria interface (HUD)")
    elementos, textos = _varrer(j)
    _ELEMENTOS.clear()
    terceiros = texto_de_terceiros(ctx)
    _ULTIMA.update(titulo=ctx.titulo, processo=ctx.processo, quando=time.time(), externo=terceiros)
    cabecalho = f"Janela: {ctx.titulo[:50]} ({nome_do_app(ctx)})"
    # o Executor corta resultados em 1500 caracteres e o aviso de segurança vem no FIM: a lista precisa caber antes.
    # Formato compacto, agrupado por tipo ("botão: [1] Sete · [2] Oito"): no formato "um por linha" os últimos
    # botões (Mais, Igual a) eram cortados e o modelo concluía que não existiam.
    textos_linha = ("Textos: " + " | ".join(t[:50] for t in textos[:6])) if textos else ""
    orcamento = ORCAMENTO_CHARS - len(cabecalho) - len(textos_linha) - 40
    grupos: dict[str, list[str]] = {}
    listados = 0
    for i, (ctrl, tipo, nome) in enumerate(elementos[:MAX_ELEMENTOS], 1):
        item = f"[{i}] {nome[:36]}"
        if orcamento - len(item) - 3 < 0:
            break
        orcamento -= len(item) + 3
        _ELEMENTOS[i] = ctrl
        grupos.setdefault(tipo, []).append(item)
        listados += 1
    linhas = [cabecalho] + [f"{tipo}: " + " · ".join(itens) for tipo, itens in grupos.items()]
    if len(elementos) > listados:
        linhas.append(f"(+{len(elementos) - listados} elementos não listados; peça 'janela' mais específica ou role)")
    if textos_linha:
        linhas.append(textos_linha)
    corpo = "\n".join(linhas)
    return conteudo_externo(f"janela {ctx.titulo[:30]}", corpo) if terceiros else corpo


def _textos_agora(topo, limite=6, orcamento_s=1.2) -> str:
    """O que a janela mostra DEPOIS da ação (visor da calculadora, campo de texto...). Sem isso o modelo adivinha
    o resultado: já disse 'deu 1023' depois de clicar numa sequência errada."""
    if topo is None:
        return ""
    try:
        if texto_de_terceiros(_contexto(topo)):
            return ""  # navegador/e-mail/chat: o texto é de terceiros; quem lê isso é ver_janela, que o marca como não confiável
        inicio, textos, pilha = time.time(), [], [topo]
        while pilha and len(textos) < 30 and time.time() - inicio < orcamento_s:
            for f in pilha.pop().GetChildren():
                if f.IsOffscreen:
                    continue
                nome = _limpo(f.Name)
                if f.ControlTypeName in TIPOS_TEXTO and nome and nome not in textos:
                    textos.append(nome)
                elif f.ControlTypeName == "EditControl":
                    try:
                        valor = f.GetValuePattern().Value if f.GetValuePattern() else ""
                    except Exception:
                        valor = ""
                    if valor and valor not in textos:
                        textos.append(f"campo '{nome[:20]}': {valor[:80]}")
                pilha.append(f)
        # 'A exibição é 144' é o que importa; textos soltos de interface vêm depois
        textos.sort(key=lambda t: 0 if re.search(r"exibi[cç][aã]o|resultado|display|campo '", t, re.I) else 1)
        return " Textos agora na janela: " + " | ".join(t[:70] for t in textos[:limite]) if textos else ""
    except Exception:
        return ""


def _checar_validade():
    if not _ELEMENTOS:
        raise ErroFerramenta("use ver_janela antes, para saber os botões e campos da janela")
    if time.time() - _ULTIMA["quando"] > VALIDADE_LISTA_S:
        _ELEMENTOS.clear()
        raise ErroFerramenta("a lista de elementos está velha; use ver_janela de novo")


def _elemento(numero: int):
    _checar_validade()
    ctrl = _ELEMENTOS.get(numero)
    if ctrl is None:
        raise ErroFerramenta(f"o elemento {numero} não existe; use ver_janela de novo para ver os números atuais")
    try:
        existe = ctrl.Exists(0, 0)
    except Exception:
        existe = False
    if not existe:
        raise ErroFerramenta(f"o elemento {numero} sumiu da tela; use ver_janela de novo")
    return ctrl


def _resolver(elementos: str) -> list[object]:
    """'7 12 8' (números) ou 'Sete, Multiplicar por, Oito' (nomes) -> controles, na ordem pedida."""
    _checar_validade()
    pedido = [t.strip() for t in re.split(r"[,;\n]+", str(elementos)) if t.strip()]
    if len(pedido) <= 1 and re.fullmatch(r"[\d\s]+", str(elementos).strip() or "x"):
        pedido = str(elementos).split()  # '7 12 8': números separados por espaço
    if not pedido:
        raise ErroFerramenta("diga quais elementos: números de ver_janela ou nomes dos botões")
    if len(pedido) > 25:
        raise ErroFerramenta("no máximo 25 cliques por vez")
    ids = sorted(_ELEMENTOS)
    nomes = [_limpo(_ELEMENTOS[i].Name) or _ELEMENTOS[i].AutomationId for i in ids]
    achados = []
    for token in pedido:
        k = casar_nome(token, nomes) if not token.isdigit() or sem_acento(token) in [sem_acento(n) for n in nomes] else None
        if k is not None:
            achados.append(_elemento(ids[k]))
        elif token.isdigit():
            achados.append(_elemento(int(token)))
        else:
            raise ErroFerramenta(f"não achei o elemento '{token}' na janela; use ver_janela e confira os nomes")
    return achados


def _ctx_do_elemento(ctrl) -> Contexto:
    topo = ctrl.GetTopLevelControl()
    return _contexto(topo) if topo else Contexto(_ULTIMA["titulo"], _ULTIMA["processo"])


def _avaliar_clicar(args: dict) -> Avaliacao:
    ctrls = _resolver(args["elementos"])
    nomes = [_limpo(c.Name) for c in ctrls]
    ctx = _ctx_do_elemento(ctrls[0])
    nivel, motivo = nivel_da_acao(ctx=ctx, elementos=nomes)
    onde = f" no {nome_do_app(ctx)}"
    if nivel == Nivel.CONFIRMAR:
        return Avaliacao(nivel, f"clicar em “{nomes[0][:35]}”{onde}, porque envolve {motivo}")
    resumo = "clicar em " + ", ".join(f"“{n[:25]}”" for n in nomes[:4]) + (" …" if len(nomes) > 4 else "") + onde
    return Avaliacao(nivel, resumo)


def _acionar(ctrl):
    """Invoke/Toggle/Select sem mexer no mouse; se o controle não suporta, clica de verdade."""
    for obter, metodo in (("GetInvokePattern", "Invoke"), ("GetTogglePattern", "Toggle"),
                          ("GetSelectionItemPattern", "Select"), ("GetExpandCollapsePattern", "Expand")):
        try:
            padrao = getattr(ctrl, obter)()
            if padrao is not None:
                getattr(padrao, metodo)()
                return
        except Exception:
            continue
    ctrl.Click(simulateMove=False)


@ferramenta(
    "clicar",
    "Clica em um ou vários elementos de ver_janela, em ordem, pelo NOME do botão ou pelo número. Exemplo para a "
    "calculadora: 'Um, Dois, Multiplicar por, Um, Dois, Igual a'. Faça ver_janela antes.",
    {"elementos": {"type": "string", "description": "Nomes ou números separados por vírgula, ex.: 'Sete, Mais, Oito, Igual a'"}},
    obrigatorios=("elementos",),
    avaliar=_avaliar_clicar,
)
def clicar(elementos: str) -> str:
    feitos, topo = [], None
    for ctrl in _resolver(elementos):
        nome = _limpo(ctrl.Name) or "elemento"
        _acionar(ctrl)
        feitos.append(nome)
        topo = topo or ctrl.GetTopLevelControl()
        time.sleep(0.12)
    time.sleep(0.3)
    return ("Cliquei em: " + ", ".join(feitos) + "." + _textos_agora(topo)
            + " Confira se o resultado é o esperado antes de responder.")


def _avaliar_digitar(args: dict) -> Avaliacao:
    alvo = args.get("elemento")
    if alvo not in (None, ""):
        ctrl = _resolver(alvo)[0]
        ctx, nome = _ctx_do_elemento(ctrl), _limpo(ctrl.Name)
    else:
        ctx, nome = _contexto(_achar_janela(args.get("janela", ""))), ""
    nivel, motivo = nivel_da_acao(ctx=ctx, elementos=[nome] if nome else [])
    texto = args["texto"]
    resumo = f"digitar “{texto[:30]}{'…' if len(texto) > 30 else ''}” no {nome_do_app(ctx)}"
    return Avaliacao(nivel, resumo + (f", porque envolve {motivo}" if motivo else ""))


def _escapar_sendkeys(texto: str) -> str:
    return texto.replace("{", "{{}").replace("}", "{}}")


@ferramenta(
    "digitar",
    "Digita texto no teclado: num campo (nome ou número de ver_janela) ou na janela indicada. Em programas que "
    "aceitam teclado (calculadora: '12*12=') é mais simples que clicar.",
    {"texto": {"type": "string"},
     "elemento": {"type": "string", "description": "Campo de ver_janela (opcional)"},
     "janela": {"type": "string", "description": "Janela onde digitar, ex.: Calculadora (opcional)"}},
    obrigatorios=("texto",),
    avaliar=_avaliar_digitar,
)
def digitar(texto: str, elemento: str = "", janela: str = "") -> str:
    if len(texto) > 2000:
        raise ErroFerramenta("texto grande demais (máx. 2000 caracteres)")
    topo = None
    if elemento not in (None, ""):
        ctrl = _resolver(elemento)[0]
        topo = ctrl.GetTopLevelControl()
        try:
            valor = ctrl.GetValuePattern()
            if valor is not None and not valor.IsReadOnly and "\n" not in texto:
                valor.SetValue(texto)
                return f"Digitei no campo '{_limpo(ctrl.Name)[:30]}'." + _textos_agora(topo)
        except Exception:
            pass
        ctrl.SetFocus()
    elif janela.strip():
        topo = _achar_janela(janela)
        _focar(topo)
    else:
        topo = auto.GetForegroundControl().GetTopLevelControl()
    time.sleep(0.15)
    auto.SendKeys(_escapar_sendkeys(texto), interval=0.012, waitTime=0.2, charMode=True)
    time.sleep(0.3)
    return "Digitei o texto." + _textos_agora(topo) + " Confira se o resultado é o esperado antes de responder."


def _vk(tecla: str) -> int:
    k = auto.Keys
    fixas = {"ctrl": k.VK_CONTROL, "shift": k.VK_SHIFT, "alt": k.VK_MENU, "win": k.VK_LWIN, "enter": k.VK_RETURN,
             "esc": k.VK_ESCAPE, "tab": k.VK_TAB, "delete": k.VK_DELETE, "backspace": k.VK_BACK, "space": k.VK_SPACE,
             "home": k.VK_HOME, "end": k.VK_END, "pageup": k.VK_PRIOR, "pagedown": k.VK_NEXT,
             "up": k.VK_UP, "down": k.VK_DOWN, "left": k.VK_LEFT, "right": k.VK_RIGHT}
    if tecla in fixas:
        return fixas[tecla]
    if re.fullmatch(r"f([1-9]|1[0-2])", tecla):
        return getattr(k, "VK_F" + tecla[1:])
    if len(tecla) == 1 and tecla.isalnum():
        return ord(tecla.upper())
    raise ErroFerramenta(f"tecla desconhecida: '{tecla}'")


def _avaliar_atalho(args: dict) -> Avaliacao:
    _exigir()
    teclas = normalizar_atalho(args["teclas"])
    if not teclas:
        raise ErroFerramenta("informe as teclas, ex.: ctrl+c")
    for t in teclas:
        _vk(t)  # valida
    ctx = _contexto(_achar_janela(args.get("janela", "")))
    nivel, motivo = nivel_da_acao(ctx=ctx, atalho=teclas)
    return Avaliacao(nivel, f"apertar {'+'.join(teclas)} no {nome_do_app(ctx)}"
                     + (f", porque envolve {motivo}" if motivo else ""))


@ferramenta(
    "atalho",
    "Aperta um atalho de teclado na janela ativa (ex.: ctrl+c, ctrl+s, enter, alt+left, f5).",
    {"teclas": {"type": "string", "description": "Ex.: ctrl+shift+t"},
     "janela": {"type": "string", "description": "Janela a ativar antes (opcional)"}},
    obrigatorios=("teclas",),
    avaliar=_avaliar_atalho,
)
def atalho(teclas: str, janela: str = "") -> str:
    lista = normalizar_atalho(teclas)
    if janela.strip():
        _focar(_achar_janela(janela))
        time.sleep(0.2)
    *mods, principal = [_vk(t) for t in lista]
    for m in mods:
        auto.PressKey(m)
    try:
        auto.PressKey(principal)
        time.sleep(0.04)
        auto.ReleaseKey(principal)
    finally:
        for m in reversed(mods):
            auto.ReleaseKey(m)
    time.sleep(0.3)
    return f"Apertei {'+'.join(lista)}." + _textos_agora(auto.GetForegroundControl().GetTopLevelControl())


def _focar(j):
    try:
        padrao = j.GetWindowPattern()
        if padrao is not None and padrao.WindowVisualState == auto.WindowVisualState.Minimized:
            padrao.SetWindowVisualState(auto.WindowVisualState.Normal)
    except Exception:
        pass
    try:
        j.SwitchToThisWindow()
    except Exception:
        j.SetFocus()
    time.sleep(0.3)


@ferramenta(
    "focar_janela",
    "Traz uma janela aberta para a frente, pelo nome (ex.: Chrome, Spotify).",
    {"janela": {"type": "string"}},
    obrigatorios=("janela",),
)
def focar_janela(janela: str) -> str:
    j = _achar_janela(janela)
    if eh_hud(j.Name):
        raise ErroFerramenta("essa é a minha própria interface (HUD)")
    _focar(j)
    return f"Janela em primeiro plano: {_limpo(j.Name)}."


@ferramenta(
    "rolar",
    "Rola a janela para baixo ou para cima.",
    {"direcao": {"type": "string", "enum": ["baixo", "cima"]},
     "vezes": {"type": "integer", "description": "Quantos 'cliques' da roda (padrão 5)"},
     "janela": {"type": "string", "description": "Nome da janela (opcional)"}},
    obrigatorios=("direcao",),
)
def rolar(direcao: str, vezes: int = 5, janela: str = "") -> str:
    j = _achar_janela(janela)
    if eh_hud(j.Name):
        raise ErroFerramenta("essa é a minha própria interface (HUD)")
    vezes = max(1, min(int(vezes), 30))
    (j.WheelDown if direcao == "baixo" else j.WheelUp)(wheelTimes=vezes, interval=0.03)
    return f"Rolei {direcao} {vezes} vezes."
