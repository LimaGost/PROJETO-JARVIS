"""Calculadora. Modelos pequenos erram contas de cabeça (7 x 8 = 35), então contas vão para cá.

Avalia pela árvore sintática com uma lista do que é permitido; nunca usa eval().
"""
import ast
import math
import operator

from jarvis.tools.base import ErroFerramenta, ferramenta

_OPERADORES = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCOES = {"sqrt": math.sqrt, "raiz": math.sqrt, "abs": abs, "round": round, "log": math.log,
            "log10": math.log10, "sin": math.sin, "cos": math.cos, "tan": math.tan, "floor": math.floor,
            "ceil": math.ceil}
_CONSTANTES = {"pi": math.pi, "e": math.e}


def _avaliar(no):
    if isinstance(no, ast.Expression):
        return _avaliar(no.body)
    if isinstance(no, ast.Constant) and isinstance(no.value, (int, float)) and not isinstance(no.value, bool):
        return no.value
    if isinstance(no, ast.Name) and no.id in _CONSTANTES:
        return _CONSTANTES[no.id]
    if isinstance(no, ast.UnaryOp) and type(no.op) in _OPERADORES:
        return _OPERADORES[type(no.op)](_avaliar(no.operand))
    if isinstance(no, ast.BinOp) and type(no.op) in _OPERADORES:
        a, b = _avaliar(no.left), _avaliar(no.right)
        if isinstance(no.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
            raise ErroFerramenta("potência grande demais")
        return _OPERADORES[type(no.op)](a, b)
    if isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id in _FUNCOES and not no.keywords:
        return _FUNCOES[no.func.id](*[_avaliar(a) for a in no.args])
    raise ErroFerramenta("expressão não permitida")


def calcular_expressao(expressao: str) -> float:
    texto = expressao.replace("×", "*").replace("÷", "/").replace("^", "**")
    if len(texto) > 200:
        raise ErroFerramenta("expressão longa demais")
    try:
        return _avaliar(ast.parse(texto, mode="eval"))
    except ZeroDivisionError:
        raise ErroFerramenta("divisão por zero")
    except (SyntaxError, TypeError, ValueError):
        raise ErroFerramenta(f"não entendi a expressão '{expressao}'")


@ferramenta(
    "calcular",
    "Calcula uma expressão matemática. Use SEMPRE para qualquer conta, por mais simples que pareça. "
    "Use * para multiplicar, / para dividir, ** para potência, ponto como decimal e sqrt() para raiz.",
    {"expressao": {"type": "string", "description": "Ex.: 7*8, (1500*0.15)+20, sqrt(144)"}},
    obrigatorios=("expressao",),
)
def calcular(expressao: str) -> str:
    r = calcular_expressao(expressao)
    if isinstance(r, float) and r.is_integer() and abs(r) < 1e15:
        r = int(r)
    elif isinstance(r, float):
        r = round(r, 6)
    return f"{expressao} = {r}"
