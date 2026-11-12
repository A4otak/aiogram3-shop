from decimal import Decimal, InvalidOperation

MAX_AMOUNT = Decimal("10000000")
_CENT = Decimal("0.01")


def parse_rub(raw: str) -> int:
    try:
        value = Decimal(raw.strip().replace(",", ".").replace(" ", ""))
    except InvalidOperation as exc:
        raise ValueError("Некорректное число") from exc

    if not value.is_finite() or not 0 < value <= MAX_AMOUNT:
        raise ValueError("Сумма вне допустимого диапазона")
    if value != value.quantize(_CENT):
        raise ValueError("Не более двух знаков после запятой")
    return int(value * 100)


def to_decimal(kopecks: int) -> Decimal:
    return Decimal(kopecks) / 100


def format_rub(kopecks: int) -> str:
    rubles, cents = divmod(abs(kopecks), 100)
    sign = "-" if kopecks < 0 else ""
    whole = f"{rubles:,}".replace(",", " ")
    if cents == 0:
        return f"{sign}{whole} ₽"
    return f"{sign}{whole},{cents:02d} ₽"
