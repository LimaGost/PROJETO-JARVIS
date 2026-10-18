"""A segurança do controle de interface é decidida pelo contexto: estes casos não podem regredir.
Nada aqui mexe na sua tela. O teste ao vivo (Calculadora/Tk) só roda com JARVIS_TESTE_UI=1."""
import os

import pytest

from jarvis.tools.base import REGISTRO, ErroFerramenta, Nivel
from jarvis.tools.interface import (Contexto, atalho_de_risco, botao_de_risco, casar_nome, eh_hud, janela_sensivel,
                                    nivel_da_acao, nome_do_app, normalizar_atalho, texto_de_terceiros)

R, C = Nivel.REGISTRADO, Nivel.CONFIRMAR


def ctx(titulo="Calculadora", processo="CalculatorApp.exe"):
    return Contexto(titulo, processo)


# ------------------------------------------------------------------ a HUD é intocável
@pytest.mark.parametrize("titulo", ["J.A.R.V.I.S.", "J.A.R.V.I.S. - Microsoft Edge", "j.a.r.v.i.s.", "  J.A.R.V.I.S.",
                                    "127.0.0.1:8765", "127.0.0.1:8770/ - Google Chrome"])
def test_hud_e_reconhecida(titulo):
    assert eh_hud(titulo)


@pytest.mark.parametrize("titulo", ["Jarvis local com Qwen3 - PROJETO JARVIS - Visual Studio Code",
                                    "PROJETO JARVIS – Explorador de Arquivos", "Calculadora", "jarvis - Pesquisa Google"])
def test_outras_janelas_com_jarvis_no_nome_nao_sao_a_hud(titulo):
    assert not eh_hud(titulo)


@pytest.mark.parametrize("acao", [dict(elementos=["Confirmar"]), dict(atalho=["enter"]), dict(elementos=[])])
def test_nunca_opera_a_hud(acao):
    with pytest.raises(ErroFerramenta):
        nivel_da_acao(ctx=ctx("J.A.R.V.I.S.", "msedge.exe"), **acao)


# ------------------------------------------------------------------ janelas sensíveis
@pytest.mark.parametrize("titulo,processo", [
    ("C:\\WINDOWS\\system32\\cmd.exe", "WindowsTerminal.exe"), ("Windows PowerShell", "powershell.exe"),
    ("Editor do Registro", "regedit.exe"), ("Gerenciador de Tarefas", "Taskmgr.exe"),
    ("Controle de Conta de Usuário", "consent.exe"), ("Configurações", "SystemSettings.exe"),
    ("Nubank - Google Chrome", "chrome.exe"), ("Minha conta - Banco do Brasil - Edge", "msedge.exe"),
    ("Checkout - Loja X", "chrome.exe"), ("PayPal: Pagar", "firefox.exe"), ("Meus cartões - Itaú", "chrome.exe"),
    ("Gerenciador de senhas - Bitwarden", "chrome.exe"), ("Administrador: Prompt de Comando", "cmd.exe"),
    ("Pix - Mercado Pago", "chrome.exe"), ("Fatura do cartão", "chrome.exe"),
])
def test_janela_sensivel_exige_confirmacao(titulo, processo):
    assert janela_sensivel(ctx(titulo, processo))
    assert nivel_da_acao(ctx=ctx(titulo, processo), elementos=["OK"])[0] == C


@pytest.mark.parametrize("titulo,processo", [
    ("Calculadora", "CalculatorApp.exe"), ("Sem título - Bloco de notas", "Notepad.exe"), ("Spotify Premium", "Spotify.exe"),
    ("Amigos - Discord", "Discord.exe"), ("jarvis - Pesquisa Google - Google Chrome", "chrome.exe"),
    ("PROJETO JARVIS – Explorador de Arquivos", "explorer.exe"), ("main.py - Visual Studio Code", "Code.exe"),
])
def test_janelas_comuns_nao_pedem_confirmacao(titulo, processo):
    assert janela_sensivel(ctx(titulo, processo)) is None
    assert nivel_da_acao(ctx=ctx(titulo, processo), elementos=["Sete"])[0] == R


# ------------------------------------------------------------------ botões de risco
@pytest.mark.parametrize("nome", ["Excluir", "Apagar tudo", "Enviar", "Pagar agora", "Comprar", "Finalizar compra",
                                  "Desinstalar", "Instalar", "Permitir", "Aceitar", "Confirmar pedido", "Transferir",
                                  "Reiniciar agora", "Desligar", "Sair da conta", "Publicar", "Formatar", "Don't save",
                                  "Send", "Delete", "Install", "Place order", "Sign out", "Não salvar", "Executar",
                                  "Fechar", "Close", "Sair", "Fechar Calculadora", "Exit"])
def test_botoes_de_risco(nome):
    assert botao_de_risco(nome)
    assert nivel_da_acao(ctx=ctx(), elementos=[nome])[0] == C


@pytest.mark.parametrize("nome", ["Sete", "Igual a", "Mais", "Abrir Navegação", "Limpar", "Limpar entrada", "Salvar",
                                  "Próximo", "Voltar", "Pesquisar", "Minimizar Calculadora", "Zero", "OK", "Cancelar",
                                  "Reproduzir", "Pausar", "Novo documento", "Enviado"])
def test_botoes_comuns_sao_so_registrados(nome):
    assert not botao_de_risco(nome)
    assert nivel_da_acao(ctx=ctx(), elementos=[nome])[0] == R


def test_um_botao_de_risco_no_meio_da_sequencia_pede_confirmacao():
    nivel, motivo = nivel_da_acao(ctx=ctx(), elementos=["Sete", "Mais", "Excluir", "Igual a"])
    assert nivel == C and "Excluir" in motivo


# ------------------------------------------------------------------ atalhos
@pytest.mark.parametrize("texto", ["win+r", "Ctrl+Alt+Delete", "alt+f4", "ctrl+shift+esc", "win+x", "shift+delete",
                                   "windows+r", "Control+Shift+Del", "win+l", "alt+tab"])
def test_atalhos_de_risco(texto):
    teclas = normalizar_atalho(texto)
    assert atalho_de_risco(teclas)
    assert nivel_da_acao(ctx=ctx(), atalho=teclas)[0] == C


@pytest.mark.parametrize("texto", ["ctrl+c", "ctrl+v", "ctrl+s", "enter", "alt+left", "f5", "ctrl+shift+t", "esc",
                                   "ctrl+a", "tab", "pagedown", "ctrl+z"])
def test_atalhos_comuns(texto):
    teclas = normalizar_atalho(texto)
    assert not atalho_de_risco(teclas)
    assert nivel_da_acao(ctx=ctx(), atalho=teclas)[0] == R


def test_atalho_em_terminal_sempre_pede_confirmacao():
    # Ctrl+C num Bloco de notas é inofensivo; num terminal pode interromper ou digitar comandos
    assert nivel_da_acao(ctx=ctx("PowerShell", "pwsh.exe"), atalho=["enter"])[0] == C


def test_normalizar_atalho_aceita_apelidos_e_ordem():
    assert normalizar_atalho("Control + SHIFT + Esc") == ["ctrl", "shift", "esc"]
    assert normalizar_atalho("windows r") == ["win", "r"]


# ------------------------------------------------------------------ registro das ferramentas
def test_ferramentas_registradas_e_ver_janela_e_externa():
    for nome in ("ver_janela", "clicar", "digitar", "atalho", "focar_janela", "rolar"):
        assert nome in REGISTRO
    assert callable(REGISTRO["ver_janela"].externo)  # decidido pela janela vista
    assert not REGISTRO["clicar"].externo


def test_clicar_sem_ver_janela_antes_e_erro_claro():
    from jarvis.tools import interface
    interface._ELEMENTOS.clear()
    with pytest.raises(ErroFerramenta, match="ver_janela"):
        REGISTRO["clicar"].avaliacao({"elementos": "3"})


def test_externo_e_dinamico_so_para_janelas_com_texto_de_terceiros():
    from jarvis.tools import interface
    interface._ULTIMA["externo"] = False
    assert REGISTRO["ver_janela"].e_externo({}) is False    # Calculadora: não é vetor de injeção
    interface._ULTIMA["externo"] = True
    assert REGISTRO["ver_janela"].e_externo({}) is True


@pytest.mark.parametrize("titulo,processo,esperado", [
    ("Calculadora", "ApplicationFrameHost.exe", False), ("Sem título - Bloco de notas", "Notepad.exe", False),
    ("Configurações", "ApplicationFrameHost.exe", False), ("Spotify Premium", "Spotify.exe", False),
    ("Google - Google Chrome", "chrome.exe", True), ("Caixa de entrada - Gmail", "msedge.exe", True),
    ("Amigos - Discord", "Discord.exe", True), ("contrato.pdf - Adobe Acrobat", "Acrobat.exe", True),
    ("WhatsApp", "WhatsApp.Root.exe", True), ("Relatório.docx - Word", "WINWORD.EXE", True),
])
def test_quais_janelas_trazem_texto_de_terceiros(titulo, processo, esperado):
    assert texto_de_terceiros(ctx(titulo, processo)) is esperado


def test_nome_do_app_para_falar():
    assert nome_do_app(ctx("Calculadora", "ApplicationFrameHost.exe")) == "Calculadora"
    assert nome_do_app(ctx("main.py - Visual Studio Code", "Code.exe")) == "Code"


@pytest.mark.parametrize("pedido,esperado", [
    ("Sete", 0), ("sete", 0), ("Multiplicar", 1), ("igual", 3), ("Multiplicar por", 1), ("Zero", None), ("", None),
])
def test_casar_nome_ignora_acento_e_maiuscula(pedido, esperado):
    nomes = ["Sete", "Multiplicar por", "Oito", "Igual a"]
    assert casar_nome(pedido, nomes) == esperado
    assert casar_nome("Três", ["Dois", "Três"]) == 1 and casar_nome("tres", ["Dois", "Três"]) == 1


def test_exato_vence_prefixo():
    assert casar_nome("Um", ["Umidade", "Um", "Uma"]) == 1


class _Ctrl:
    def __init__(self, nome, existe=True):
        self.Name, self.AutomationId, self._existe = nome, "", existe

    def Exists(self, *a):
        return self._existe


@pytest.fixture
def lista_viva():
    import time
    from jarvis.tools import interface
    interface._ELEMENTOS.clear()
    interface._ELEMENTOS.update({1: _Ctrl("Sete"), 2: _Ctrl("Multiplicar por"), 3: _Ctrl("Oito"), 4: _Ctrl("Igual a"), 5: _Ctrl("7")})
    interface._ULTIMA.update(quando=time.time(), titulo="Calculadora", processo="ApplicationFrameHost.exe")
    yield interface
    interface._ELEMENTOS.clear()


def test_resolver_por_nome_e_por_numero(lista_viva):
    nomes = lambda texto: [c.Name for c in lista_viva._resolver(texto)]
    assert nomes("Sete, Multiplicar por, Oito, Igual a") == ["Sete", "Multiplicar por", "Oito", "Igual a"]
    assert nomes("1 2 3 4") == ["Sete", "Multiplicar por", "Oito", "Igual a"]
    assert nomes("sete,igual") == ["Sete", "Igual a"]
    assert nomes("5") == ["7"]            # '5' é número de elemento, não o nome '5'
    assert nomes("7") == ["7"]            # '7' é um elemento chamado '7': o nome vence o número


@pytest.mark.parametrize("texto", ["Nove", "", "99", ", , ,"])
def test_resolver_recusa_o_que_nao_existe(lista_viva, texto):
    with pytest.raises(ErroFerramenta):
        lista_viva._resolver(texto)


def test_resolver_limita_cliques(lista_viva):
    with pytest.raises(ErroFerramenta, match="25"):
        lista_viva._resolver(", ".join(["Sete"] * 26))


def test_lista_envelhecida_e_recusada(lista_viva):
    lista_viva._ULTIMA["quando"] -= 500
    with pytest.raises(ErroFerramenta, match="velha"):
        lista_viva._resolver("Sete")
    assert not lista_viva._ELEMENTOS


def test_elemento_que_sumiu_da_tela_e_recusado(lista_viva):
    lista_viva._ELEMENTOS[1] = _Ctrl("Sete", existe=False)
    with pytest.raises(ErroFerramenta, match="sumiu"):
        lista_viva._resolver("Sete")


def test_novo_pedido_esquece_os_numeros(lista_viva):
    from jarvis.tools.base import AO_NOVO_PEDIDO
    assert lista_viva._ELEMENTOS
    for limpar in AO_NOVO_PEDIDO:
        limpar()
    assert not lista_viva._ELEMENTOS
    with pytest.raises(ErroFerramenta, match="ver_janela"):
        lista_viva._resolver("Sete")


def test_executor_zera_a_lista_a_cada_pedido(lista_viva, tmp_path):
    from jarvis.tools.executor import Executor
    Executor(lambda r, **k: False, str(tmp_path), avisar=lambda t: None).novo_turno()
    assert not lista_viva._ELEMENTOS

# ------------------------------------------------------------------ ao vivo (opt-in): usa a sua tela
ao_vivo = pytest.mark.skipif(not os.environ.get("JARVIS_TESTE_UI"), reason="teste ao vivo: JARVIS_TESTE_UI=1")


@ao_vivo
def test_ao_vivo_calculadora():
    import subprocess, time
    from jarvis.tools import interface
    subprocess.Popen(["calc.exe"])
    time.sleep(3)
    saida = interface.ver_janela("Calculadora")
    assert "'Sete'" in saida and "não confiável" in saida
    numero = lambda nome: next(int(l.split("]")[0][1:]) for l in saida.splitlines() if f"'{nome}'" in l)
    interface.clicar(" ".join(str(numero(n)) for n in ("Limpar", "Sete", "Multiplicar por", "Oito", "Igual a")))
    assert "56" in interface.ver_janela("Calculadora")

