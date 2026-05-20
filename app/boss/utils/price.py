import re


class PriceParseError(ValueError):
    pass


def parse_price_to_mesos(raw: str) -> int:
    value = raw.strip().replace(",", "")
    if value.isdigit():
        return int(value)

    pattern = re.compile(r"^(?:(\d+(?:\.\d+)?)억)?(?:(\d+(?:\.\d+)?)만)?$")
    m = pattern.match(value)
    if not m:
        raise PriceParseError("가격 형식이 올바르지 않습니다.")

    eok, man = m.groups()
    if not eok and not man:
        raise PriceParseError("가격 형식이 올바르지 않습니다.")

    mesos = 0
    if eok:
        mesos += int(float(eok) * 100_000_000)
    if man:
        mesos += int(float(man) * 10_000)

    if mesos <= 0:
        raise PriceParseError("가격은 0보다 커야 합니다.")
    return mesos


def format_mesos_kr(mesos: int) -> str:
    eok = mesos // 100_000_000
    rem = mesos % 100_000_000
    man = rem // 10_000

    if eok and man:
        return f"{eok}억{man}만"
    if eok:
        return f"{eok}억"
    if man:
        return f"{man}만"
    return str(mesos)
