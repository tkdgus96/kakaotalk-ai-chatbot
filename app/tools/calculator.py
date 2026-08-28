"""Deterministic calculator — removes LLM arithmetic errors.

Safe AST evaluation of math expressions (no eval/exec). Supports + - * / // %
** , parentheses, common math functions and constants.
"""

import ast
import math
import operator

from langchain_core.tools import tool

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor,
    "ceil": math.ceil, "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "pow": pow, "min": min, "max": max, "factorial": math.factorial,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _eval(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("숫자만 허용")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _FUNCS.get(node.func.id)
        if fn is None:
            raise ValueError(f"허용되지 않은 함수: {node.func.id}")
        return fn(*[_eval(a) for a in node.args])
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"허용되지 않은 이름: {node.id}")
    raise ValueError("허용되지 않은 식")


@tool
async def calculate(expression: str) -> str:
    """수식을 정확히 계산한다. 산수·거듭제곱·나눗셈·퍼센트·제곱근 등 수치 계산이 필요하면
    직접 암산하지 말고 이 도구를 써서 정확도를 보장하라.

    expression: 파이썬 수식 문법. 퍼센트는 곱으로 변환해서 넣어라.
      예) "(2500+500)/2", "2**10", "sqrt(144)", "0.15*3000" (=3000의 15%),
          "1256*1350" (환율 계산), "factorial(6)"."""
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval(tree.body)
    except Exception as e:
        return f"계산할 수 없는 식이야: {e}"
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return f"{expression} = {result}"
