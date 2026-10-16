import pytest

from jarvis.brain.comandos import identificar, sim_ou_nao

CASOS = [
    ("Tchau, Jarvis!", "desligar"), ("Tchau Jarvis.", "desligar"), ("Desligar Jarvis.", "desligar"),
    ("Pode dormir.", "desligar"), ("Encerrar Jarvis", "desligar"), ("Tchau Jarvis, até amanhã", "desligar"),
    ("Esqueça a conversa.", "esquecer"), ("Esquisa convés.", "esquecer"), ("Limpar conversa", "esquecer"),
    ("Como desligar o computador?", None), ("Jarvis.", None), ("Que horas são?", None),
    ("Pode dormir tranquilo que eu cuido disso hoje", None), ("Me explique como encerrar um processo no Windows", None),
    ("Obrigado.", None), ("Tchau", None), ("Você pode me ajudar?", None),
]


@pytest.mark.parametrize("texto,esperado", CASOS)
def test_identificar(texto, esperado):
    assert identificar(texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("Sim.", True), ("Sim, pode.", True), ("Confirmo", True), ("Pode apagar", True), ("Autorizo.", True),
    ("Claro", True), ("Isso mesmo", True), ("Pode sim", True), ("Pode apagar para mim", True),
    ("Não.", False), ("Não, cancela", False), ("Cancela", False), ("Pode não", False), ("Sim... não, espera", False),
    ("Negativo", False), ("Para", False), ("", False), ("Hã?", False), ("Obrigado.", False), ("Talvez", False),
])
def test_sim_ou_nao(texto, esperado):
    """Na dúvida é NÃO: só executa com um sim claro e sem nenhuma negação."""
    assert sim_ou_nao(texto) is esperado
