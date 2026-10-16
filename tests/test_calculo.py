import pytest

from jarvis.tools.base import ErroFerramenta
from jarvis.tools.calculo import calcular, calcular_expressao


@pytest.mark.parametrize("expr,esperado", [
    ("7*8", 56), ("(1500*0.15)+20", 245), ("sqrt(144)", 12), ("2**10", 1024), ("10/4", 2.5),
    ("-3+5", 2), ("7×8", 56), ("2^3", 8), ("17 % 5", 2), ("round(3.14159, 2)", 3.14),
])
def test_contas(expr, esperado):
    assert calcular_expressao(expr) == pytest.approx(esperado)


def test_formatacao():
    assert calcular("7*8") == "7*8 = 56"


@pytest.mark.parametrize("perigoso", [
    "__import__('os').system('del x')", "open('a.txt').read()", "(1).__class__", "[x for x in range(9)]",
    "9**9**9", "lambda: 1", "exec('1')", "a = 1", "'abc' * 3", "True + 1",
])
def test_nada_alem_de_matematica(perigoso):
    with pytest.raises(ErroFerramenta):
        calcular_expressao(perigoso)


def test_divisao_por_zero():
    with pytest.raises(ErroFerramenta):
        calcular_expressao("1/0")
