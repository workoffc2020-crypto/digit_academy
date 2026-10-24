"""Money helpers. Everything is integer paise; Decimal is used only to parse/format."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def to_paise(value) -> int:
    """'1,250.50' / 1250.5 / Decimal -> 125050. Rejects garbage and more than 2 decimals of real precision."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value * 100
    s = str(value).strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    if s == "":
        return 0
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Invalid amount: {value!r}")
    if not d.is_finite():
        raise ValueError(f"Invalid amount: {value!r}")
    return int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _indian_group(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def fmt(paise: int, symbol: str = "₹", decimals: bool = True) -> str:
    neg = paise < 0
    p = abs(int(paise))
    rupees, ps = divmod(p, 100)
    s = _indian_group(rupees) + (f".{ps:02d}" if decimals else "")
    return f"{'-' if neg else ''}{symbol}{' ' if symbol else ''}{s}"


def plain(paise: int) -> str:
    """For spreadsheets/CSV: 1250.50 with no symbol or grouping."""
    neg = paise < 0
    r, p = divmod(abs(int(paise)), 100)
    return f"{'-' if neg else ''}{r}.{p:02d}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
         "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_1000(n: int) -> str:
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10] + ((" " + _ONES[n % 10]) if n % 10 else ""))
    elif n > 0:
        out.append(_ONES[n])
    return " ".join(out)


def _int_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for div, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand")):
        if n >= div:
            q, n = divmod(n, div)
            parts.append((_int_words(q) if q >= 100 and name == "Crore" else _below_1000(q)) + " " + name)
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def amount_in_words(paise: int) -> str:
    """Indian Rupees in words, e.g. 125050 -> 'Rupees One Thousand Two Hundred Fifty and Fifty Paise Only'."""
    neg = paise < 0
    rupees, ps = divmod(abs(int(paise)), 100)
    s = "Rupees " + _int_words(rupees)
    if ps:
        s += f" and {_below_1000(ps)} Paise"
    return ("Minus " if neg else "") + s + " Only"
