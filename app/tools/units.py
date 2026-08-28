"""Deterministic unit conversion (length, weight, temperature, volume, speed)."""

from langchain_core.tools import tool

# base units: meter, gram, liter, (temp handled specially), m/s
_FACTORS = {
    # length -> meter
    "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
    "inch": 0.0254, "in": 0.0254, "ft": 0.3048, "feet": 0.3048,
    "yard": 0.9144, "mile": 1609.344, "마일": 1609.344,
    "미터": 1.0, "센치": 0.01, "센티": 0.01, "킬로미터": 1000.0,
    # weight -> gram
    "mg": 0.001, "g": 1.0, "kg": 1000.0, "ton": 1_000_000.0, "톤": 1_000_000.0,
    "lb": 453.592, "파운드": 453.592, "oz": 28.3495, "온스": 28.3495,
    "그램": 1.0, "킬로": 1000.0, "킬로그램": 1000.0,
    # volume -> liter
    "ml": 0.001, "l": 1.0, "리터": 1.0, "gallon": 3.78541, "갤런": 3.78541,
}
_DIMENSION = {}
for _u in ("mm", "cm", "m", "km", "inch", "in", "ft", "feet", "yard", "mile", "마일",
           "미터", "센치", "센티", "킬로미터"):
    _DIMENSION[_u] = "length"
for _u in ("mg", "g", "kg", "ton", "톤", "lb", "파운드", "oz", "온스", "그램", "킬로", "킬로그램"):
    _DIMENSION[_u] = "weight"
for _u in ("ml", "l", "리터", "gallon", "갤런"):
    _DIMENSION[_u] = "volume"

_TEMP = {"c", "섭씨", "f", "화씨", "k", "켈빈"}


def _norm(u: str) -> str:
    return u.strip().lower().replace("°", "")


def _temp_convert(value: float, src: str, dst: str) -> float | None:
    src = "c" if src in ("c", "섭씨") else "f" if src in ("f", "화씨") else "k" if src in ("k", "켈빈") else src
    dst = "c" if dst in ("c", "섭씨") else "f" if dst in ("f", "화씨") else "k" if dst in ("k", "켈빈") else dst
    # to celsius
    if src == "c":
        c = value
    elif src == "f":
        c = (value - 32) * 5 / 9
    elif src == "k":
        c = value - 273.15
    else:
        return None
    if dst == "c":
        return c
    if dst == "f":
        return c * 9 / 5 + 32
    if dst == "k":
        return c + 273.15
    return None


@tool
async def convert_unit(value: float, from_unit: str, to_unit: str) -> str:
    """길이·무게·부피·온도 단위를 정확히 변환한다. 직접 환산하지 말고 이 도구를 써라.

    value: 숫자
    from_unit/to_unit: 예) km, mile, cm, inch, kg, lb, l, gallon, 섭씨/화씨(c/f/k)."""
    src, dst = _norm(from_unit), _norm(to_unit)
    if src in _TEMP or dst in _TEMP:
        out = _temp_convert(value, src, dst)
        if out is None:
            return f"온도 단위 변환 실패: {from_unit}->{to_unit}"
        return f"{value}{from_unit} = {out:.2f}{to_unit}"
    if src not in _FACTORS or dst not in _FACTORS:
        return f"지원하지 않는 단위야: {from_unit} 또는 {to_unit}"
    if _DIMENSION.get(src) != _DIMENSION.get(dst):
        return f"서로 다른 종류의 단위는 변환할 수 없어: {from_unit}({_DIMENSION.get(src)}) → {to_unit}({_DIMENSION.get(dst)})"
    out = value * _FACTORS[src] / _FACTORS[dst]
    return f"{value} {from_unit} = {out:,.4g} {to_unit}"
