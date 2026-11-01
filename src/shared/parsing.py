"""Parse amounts, dates, currencies and signs as printed on bank statements.

Statements print numbers in local conventions (`1.234,56` in Uruguay, `1,234.56`
in USD exports), day-first dates, and the direction of a movement as a column,
a marker or a trailing minus. This module turns those strings into values in
code, so the LLM only ever transcribes what is printed (v1.6 spec §3.4).

Rules:
- An unparsable value is ``None``, never zero.
- Ambiguous amounts (``4.000``: four or four thousand) return every plausible
  reading, preferred first, so the balance check can pick the one that closes.
- Signs follow the user's perspective: negative is money out or a charge,
  positive is money in, a payment to a card, or a refund.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Literal

DecimalSeparator = Literal[",", "."]

# ── Text helpers ──────────────────────────────────────────────────────────────


def fold(text: str) -> str:
    """Return text upper-cased, without accents, with whitespace collapsed."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped).strip().upper()


# ── Amounts ───────────────────────────────────────────────────────────────────

# Currency markers that may be glued to a printed amount; longest first so that
# "US$" is removed before "$".
_AMOUNT_NOISE = re.compile(
    r"(US\$|U\$S|U\$D|U\$|\$U|R\$|USD|UYU|EUR|BRL|ARS|€|\$)", re.IGNORECASE
)
_SIGN_MARKER = re.compile(r"\b(CR|DB|DR|CRED|DEB)\.?$|^(CR|DB|DR)\b", re.IGNORECASE)
_NUMBER_BODY = re.compile(r"^\d[\d.,]*$")


@dataclass(frozen=True)
class ParsedAmount:
    """A printed amount: its possible magnitudes and the sign it carries, if any."""

    raw: str
    candidates: tuple[Decimal, ...]
    sign: int | None  # -1 / +1 when the string itself says so, else None
    sign_word: bool = (
        False  # the sign came from a CR/DB word, not a minus or parentheses
    )

    @property
    def magnitude(self) -> Decimal | None:
        """Return the preferred magnitude, or None when the string is not a number."""
        return self.candidates[0] if self.candidates else None

    def signed(self, default_sign: int = 1) -> Decimal | None:
        """Return the preferred magnitude with the printed sign (or ``default_sign``)."""
        if self.magnitude is None:
            return None
        sign = self.sign if self.sign is not None else default_sign
        return self.magnitude * sign


def _to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _magnitudes(body: str, decimal: DecimalSeparator | None) -> list[Decimal]:
    """Return the readings of an unsigned number body such as ``1.234,56``."""
    out: list[Decimal] = []

    def add(text: str) -> None:
        value = _to_decimal(text)
        if value is not None and value not in out:
            out.append(value)

    has_comma, has_dot = "," in body, "." in body
    if decimal is not None:
        thousands = "." if decimal == "," else ","
        if body.count(decimal) > 1:
            return []
        if (
            decimal in body
            and thousands in body
            and body.rfind(thousands) > body.rfind(decimal)
        ):
            return []  # contradicts the declared convention
        add(body.replace(thousands, "").replace(decimal, "."))
        return out

    if has_comma and has_dot:
        # The last separator is the decimal: 1.234,56 or 1,234.56.
        if body.rfind(",") > body.rfind("."):
            if body.count(",") == 1:
                add(body.replace(".", "").replace(",", "."))
        elif body.count(".") == 1:
            add(body.replace(",", ""))
        return out

    sep = "," if has_comma else "." if has_dot else None
    if sep is None:
        add(body)
        return out

    if re.fullmatch(rf"\d{{1,3}}(\{sep}\d{{3}})+", body):
        # 1.234 / 1,234 / 1.234.567: thousands grouping. Money on a statement
        # has two decimals, so thousands is the preferred reading; with a
        # single separator the 3-decimal reading is kept as an alternative.
        add(body.replace(sep, ""))
        if body.count(sep) == 1:
            add(body.replace(sep, "."))
        return out

    if body.count(sep) == 1:
        add(body.replace(sep, "."))
    return out


def parse_amount(
    raw: str | None, decimal: DecimalSeparator | None = None
) -> ParsedAmount:
    """Parse a printed amount such as ``-1.234,56``, ``(45.00)``, ``1.234,56-`` or ``120,00 CR``.

    Args:
        raw: The amount exactly as printed. ``None`` or blank yields no candidates.
        decimal: The statement's decimal separator when known (see
            ``detect_decimal_separator``); removes the ambiguity of ``1.234``.
    """
    text = (raw or "").strip()
    if not text:
        return ParsedAmount(raw=raw or "", candidates=(), sign=None)

    sign: int | None = None
    sign_word = False
    body = text.replace(" ", " ")

    marker = _SIGN_MARKER.search(body)
    if marker:
        word = (marker.group(1) or marker.group(2)).upper()
        sign = 1 if word.startswith("CR") else -1
        sign_word = True
        body = _SIGN_MARKER.sub("", body)

    body = _AMOUNT_NOISE.sub("", body).strip()
    if body.startswith("(") and body.endswith(")"):
        sign, body = -1, body[1:-1].strip()
    if body.startswith(("-", "−")):
        sign, body = -1, body[1:].strip()
    elif body.startswith("+"):
        sign, body = 1, body[1:].strip()
    if body.endswith("-"):
        sign, body = -1, body[:-1].strip()

    # Spaces used as thousands separators: "1 234,56".
    body = re.sub(r"(?<=\d) (?=\d{3}\b)", "", body)
    if not _NUMBER_BODY.match(body):
        return ParsedAmount(raw=text, candidates=(), sign=None)
    return ParsedAmount(
        raw=text,
        candidates=tuple(_magnitudes(body, decimal)),
        sign=sign,
        sign_word=sign_word,
    )


def detect_decimal_separator(raws: Iterable[str | None]) -> DecimalSeparator | None:
    """Infer a statement's decimal separator from all of its printed amounts.

    Any amount that shows both separators (``1.234,56``) settles it, as does a
    separator followed by exactly two digits (``120,50``). Returns None when the
    amounts do not say, or when they contradict each other.
    """
    votes: set[DecimalSeparator] = set()
    for raw in raws:
        text = (raw or "").strip()
        if "," in text and "." in text:
            votes.add("," if text.rfind(",") > text.rfind(".") else ".")
            continue
        two_decimals = re.search(r"\d([.,])\d{2}(?!\d)", text)
        if two_decimals:
            votes.add("," if two_decimals.group(1) == "," else ".")
    return votes.pop() if len(votes) == 1 else None


def to_cents(value: Decimal) -> Decimal:
    """Round a value to cents (statement balances are exact to the cent)."""
    return value.quantize(Decimal("0.01"))


# ── Dates ─────────────────────────────────────────────────────────────────────

_MONTHS = {
    "ENE": 1,
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "ABR": 4,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "AUG": 8,
    "SET": 9,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DIC": 12,
    "DEC": 12,
}

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_NUMERIC = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2}|\d{4}))?$")
_NAMED = re.compile(r"^(\d{1,2})[ /.\-]?([A-Z]{3})[A-Z]*\.?(?:[ /.\-]?(\d{2}|\d{4}))?$")
_DATE_TOKEN = re.compile(
    r"^(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.\-]\d{1,2}([/.\-](\d{2}|\d{4}))?"
    r"|\d{1,2}[ /.\-]?(ENE|JAN|FEB|MAR|ABR|APR|MAY|JUN|JUL|AGO|AUG|SET|SEP|OCT|NOV|DIC|DEC)[A-Z]*\.?([ /.\-]?(\d{2}|\d{4}))?)$"
)


def looks_like_date(token: str) -> bool:
    """Return True when a token has the shape of a statement date (``03/01/2026``, ``03-ENE``)."""
    return bool(_DATE_TOKEN.match(fold(token)))


def _year(text: str | None) -> int | None:
    if text is None:
        return None
    year = int(text)
    return 2000 + year if year < 100 else year


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(
    raw: str | None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> str | None:
    """Parse a statement date (day first) and return it as ``YYYY-MM-DD``.

    Accepts ``dd/mm/yyyy``, ``dd/mm/yy``, ``dd-mm-yyyy``, ``dd.mm.yyyy``, ISO, and
    month names (``03 ENE 2026``, ``03-JAN-26``). A date printed without a year
    takes it from the statement period. An impossible date (31/02) is None.
    """
    text = fold(raw or "")
    if not text:
        return None

    year: int | None
    iso = _ISO.match(text)
    if iso:
        found = _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        return found.isoformat() if found else None

    numeric = _NUMERIC.match(text)
    named = None if numeric else _NAMED.match(text)
    if numeric:
        day, month, year = (
            int(numeric.group(1)),
            int(numeric.group(2)),
            _year(numeric.group(3)),
        )
    elif named and named.group(2) in _MONTHS:
        day, month, year = (
            int(named.group(1)),
            _MONTHS[named.group(2)],
            _year(named.group(3)),
        )
    else:
        return None

    if year is None:
        year = _year_from_period(month, period_start, period_end)
        if year is None:
            return None
    found = _safe_date(year, month, day)
    return found.isoformat() if found else None


def _year_from_period(
    month: int, period_start: str | None, period_end: str | None
) -> int | None:
    """Pick the year that puts ``month`` inside the statement period (periods can span New Year)."""
    start = date.fromisoformat(period_start) if period_start else None
    end = date.fromisoformat(period_end) if period_end else None
    if start and end and start.year != end.year:
        return start.year if month >= start.month else end.year
    anchor = end or start
    return anchor.year if anchor else None


# ── Currency ──────────────────────────────────────────────────────────────────

# Longest/most specific markers first: "US$" and "R$" must win over "$".
_CURRENCY_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"US\$|U\$S|U\$D|U\$|\bUSD\b|\bDOLARES\b|\bDOLLARS?\b|\bDLS\b"), "USD"),
    (re.compile(r"R\$|\bBRL\b|\bREALES\b"), "BRL"),
    (re.compile(r"\$AR|\bARS\b|PESOS ARGENTINOS"), "ARS"),
    (re.compile(r"€|\bEUR\b|\bEUROS?\b"), "EUR"),
    (re.compile(r"\$U|\bUYU\b|\bPESOS\b|\bMN\b|MONEDA NACIONAL"), "UYU"),
]


def parse_currency(raw: str | None, bare_dollar: str | None = None) -> str | None:
    """Return the ISO currency a printed marker names (``US$`` → USD, ``$U`` → UYU).

    Args:
        raw: Text that names a currency: a column header, a header line, a cell.
        bare_dollar: What a lone ``$`` means in this context. Only pass it where
            the context makes ``$`` unambiguous (a Uruguayan bank's statement
            header); on a receipt a bare ``$`` stays None.
    """
    text = fold(raw or "")
    if not text:
        return None
    for pattern, code in _CURRENCY_MARKERS:
        if pattern.search(text):
            return code
    if bare_dollar and "$" in text:
        return bare_dollar
    return None


# ── Statement vocabulary ──────────────────────────────────────────────────────

_OPENING = re.compile(
    r"\bSALDO (ANTERIOR|INICIAL|AL INICIO|PERIODO ANTERIOR|PREVIO)\b|\bOPENING BALANCE\b"
    r"|\bPREVIOUS BALANCE\b|\bBALANCE BROUGHT FORWARD\b|\bSALDO DEL (ESTADO|RESUMEN) ANTERIOR\b"
)
_CLOSING = re.compile(
    r"\bSALDO (FINAL|ACTUAL|AL CIERRE|A LA FECHA|DE CIERRE)\b|\bNUEVO SALDO\b"
    r"|\bCLOSING BALANCE\b|\bNEW BALANCE\b|\bSALDO A PAGAR\b"
)


def balance_label(text: str) -> tuple[Literal["opening", "closing"], int] | None:
    """Return the balance kind a folded line names and where its label ends, or None."""
    opening = _OPENING.search(text)
    if opening:
        return "opening", opening.end()
    closing = _CLOSING.search(text)
    if closing:
        return "closing", closing.end()
    return None


def balance_row_kind(description: str | None) -> Literal["opening", "closing"] | None:
    """Return whether a row is the statement's opening or closing balance, not a movement."""
    found = balance_label(fold(description or ""))
    return found[0] if found else None


_BALANCE_ROW_START = (
    "SALDO",
    "OPENING",
    "CLOSING",
    "PREVIOUS BALANCE",
    "NEW BALANCE",
    "BALANCE",
    "NUEVO SALDO",
)


def leading_balance_label(
    description: str | None,
) -> Literal["opening", "closing"] | None:
    """Return the balance kind when a row *starts* with a balance label.

    A movement may mention a balance ("PAGO SALDO A PAGAR VISA"); only a row
    whose description starts with the label is the statement's own balance.
    """
    text = fold(description or "")
    return balance_row_kind(text) if text.startswith(_BALANCE_ROW_START) else None


_INSTALLMENT = re.compile(
    r"\b(?:CUOTAS?|CTAS?\.?|C\.)\s*(\d{1,2})\s*(?:/|DE)\s*(\d{1,2})\b|\b(\d{1,2})\s*/\s*(\d{1,2})\s+CUOTAS?\b"
)


def parse_installment(description: str | None) -> tuple[int, int] | None:
    """Return ``(n, m)`` for an installment line such as ``CUOTA 03/06``; None otherwise.

    A bare ``03/06`` is not an installment (it is usually a date): the word
    CUOTA (or its abbreviation) must be present.
    """
    match = _INSTALLMENT.search(fold(description or ""))
    if not match:
        return None
    groups = [g for g in match.groups() if g is not None]
    n, m = int(groups[0]), int(groups[1])
    if not 1 <= n <= m <= 99:
        return None
    return n, m
