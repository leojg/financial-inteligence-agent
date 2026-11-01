"""Read a statement's movements from its printed table, by position, without a model (v1.6 §3.3 rung 2).

How a page is read:

- The **header row** is the line whose words are column labels (Fecha,
  Descripción, Débito, Crédito, Importe, Saldo, Moneda… and their English
  equivalents). It gives the columns and where the numeric ones sit. Pages
  without a header reuse the previous page's columns.
- A **movement** starts with a date. Numbers in the numeric zone go to the
  nearest numeric column — that is what tells a debit from a credit. The words
  between the date and the numbers are the description.
- A line with only text, right under a movement, **continues its description**
  (descriptions that wrap). A line with only a balance is that day's balance.
- **Balance rows** (Saldo anterior / Saldo final) set the statement's balances
  and are not movements. Page footers, subtotals and carried-over lines are skipped.
- Lines with numbers that fit none of the above are **counted**, because they
  may be movements the reader missed.

Values stay as printed in the ``*_raw`` fields; parsing happens in code.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from agents.reconciliator.statements.balance import diagnostic
from agents.reconciliator.statements.pdf import PdfDocument, PdfLine, PdfWord
from agents.reconciliator.statements.profiles import GENERIC, BankProfile
from shared.models import AccountType, SignSource, StatementRow
from shared.parsing import (
    DecimalSeparator,
    balance_label,
    detect_decimal_separator,
    fold,
    looks_like_date,
    parse_amount,
    parse_currency,
    parse_date,
    parse_installment,
)

TABLE_EXTRACTOR_VERSION = "table-1"

NUMERIC_KINDS = frozenset({"debit", "credit", "amount", "balance"})
TEXT_KINDS = frozenset({"currency", "account"})

VOCABULARY: dict[str, tuple[str, ...]] = {
    "date": ("FECHA", "FEC", "DATE", "DIA"),
    "description": (
        "DESCRIPCION",
        "CONCEPTO",
        "DETALLE",
        "DESCRIPTION",
        "DETAILS",
        "MOVIMIENTO",
        "MOVIMIENTOS",
        "COMERCIO",
        "ESTABLECIMIENTO",
        "OPERACION",
        "TRANSACCION",
        "TRANSACTION",
        "PAYEE",
        "NARRATIVE",
        "ASUNTO",
    ),
    "reference": (
        "REFERENCIA",
        "REF",
        "COMPROBANTE",
        "DOCUMENTO",
        "DOC",
        "NRO",
        "NUM",
        "NUMERO",
        "CUPON",
        "AUTORIZACION",
        "DEPENDENCIA",
        "SUCURSAL",
        "OFICINA",
    ),
    "debit": (
        "DEBITO",
        "DEBITOS",
        "DEBE",
        "DEBIT",
        "DEBITS",
        "CARGO",
        "CARGOS",
        "RETIRO",
        "RETIROS",
        "EGRESO",
        "EGRESOS",
        "WITHDRAWAL",
        "WITHDRAWALS",
    ),
    "credit": (
        "CREDITO",
        "CREDITOS",
        "HABER",
        "CREDIT",
        "CREDITS",
        "ABONO",
        "ABONOS",
        "DEPOSITO",
        "DEPOSITOS",
        "INGRESO",
        "INGRESOS",
        "DEPOSIT",
        "DEPOSITS",
    ),
    "amount": ("IMPORTE", "IMPORTES", "MONTO", "AMOUNT"),
    "balance": ("SALDO", "SALDOS", "BALANCE"),
    "currency": ("MONEDA", "CURRENCY", "DIVISA"),
    "account": ("CUENTA", "ACCOUNT"),
}

_SKIP = re.compile(
    r"^(PAGINA|PAG\.?|HOJA|PAGE)\s*\d+\s*(DE|/|OF)\s*\d+|\b(PAGINA|PAGE)\s*\d+\s*(DE|OF)\s*\d+\s*$"
    r"|^(SUB)?TOTAL(ES)?\b|^TRANSPORTE\b|\bA TRANSPORTAR\b|^VIENE DE\b|^CONTINUA\b"
)
_SUMMARY = re.compile(
    r"\b(BALANCE|SALDO|TOTAL|NETO?|RESUMEN|SUBTOTAL|DISPONIBLE|LIMITE|MINIMO)\b"
)
_MONEY = re.compile(r"\d[.,]\d{2}(?:\D*)$")
_SIGN_WORDS = {"-", "+", "CR", "DB", "DR", "CR.", "DB."}


@dataclass
class Column:
    """A table column located from its header label."""

    kind: str
    x0: float
    x1: float
    currency: str | None = None

    @property
    def center(self) -> float:
        """Return the horizontal center of the header label."""
        return (self.x0 + self.x1) / 2

    def distance(self, x0: float, x1: float) -> float:
        """Return the horizontal gap between this column's label and a span (0 if they overlap)."""
        return max(0.0, self.x0 - x1, x0 - self.x1)


@dataclass
class _Cells:
    page: int
    top: float
    raw_text: str
    date_raw: str | None
    description: list[str] = field(default_factory=list)
    numbers: dict[str, str] = field(default_factory=dict)  # column kind → raw
    currency_raw: str | None = None
    amount_currency: str | None = None  # currency of the column the amount sat in


@dataclass
class TableReading:
    """What the table reader found: movements, balance rows, and what it could not place."""

    found_table: bool
    rows: list[StatementRow] = field(default_factory=list)
    row_currencies: list[str | None] = field(default_factory=list)
    opening_raw: dict[str | None, str] = field(default_factory=dict)
    closing_raw: dict[str | None, str] = field(default_factory=dict)
    decimal: DecimalSeparator | None = None
    columns: list[Column] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


# ── Header row ────────────────────────────────────────────────────────────────


def _label(text: str) -> str:
    return fold(text).strip("().:;,*-#º°")


def label_kind(text: str, profile: BankProfile = GENERIC) -> str | None:
    """Return the column kind a header word names, or None."""
    label = _label(text)
    if not label:
        return None
    for kind, labels in VOCABULARY.items():
        if label in labels or label in profile.labels.get(kind, ()):
            return kind
    return None


def header_columns(
    line: PdfLine, profile: BankProfile = GENERIC, local_currency: str | None = "UYU"
) -> list[Column] | None:
    """Return the columns a line declares, or None if it is not a table header row."""
    if not line.words or looks_like_date(line.words[0].text):
        return None
    columns: list[Column] = []
    for word in line.words:
        kind = label_kind(word.text, profile)
        previous = columns[-1] if columns else None
        gap = word.x0 - previous.x1 if previous else 1e9
        if kind is None:
            currency = parse_currency(word.text, bare_dollar=local_currency)
            if currency and not parse_amount(word.text).candidates:
                if (
                    previous
                    and previous.kind in NUMERIC_KINDS
                    and previous.currency is None
                    and gap < 20
                ):
                    previous.currency, previous.x1 = (
                        currency,
                        word.x1,
                    )  # "Importe (UYU)", "Saldo $"
                else:
                    columns.append(
                        Column("amount", word.x0, word.x1, currency)
                    )  # "PESOS", "U$S"
            elif previous and gap < 8:
                previous.x1 = word.x1  # a multi-word label: "Fecha valor", "Nro. Doc."
            continue
        if kind == "date" and any(c.kind == "date" for c in columns):
            kind = "value_date"
        if previous and previous.kind == kind and gap < 8:
            previous.x1 = word.x1
            continue
        columns.append(Column(kind, word.x0, word.x1))
    kinds = {c.kind for c in columns}
    if "date" in kinds and kinds & NUMERIC_KINDS and len(columns) >= 3:
        return columns
    return None


def _numeric_start(columns: list[Column]) -> float:
    """Return the x where the numeric zone begins: left of the first numeric column, with margin."""
    numeric = sorted(
        (c for c in columns if c.kind in NUMERIC_KINDS), key=lambda c: c.x0
    )
    first = numeric[0]
    left = [c for c in columns if c.x1 <= first.x0 and c.kind not in NUMERIC_KINDS]
    neighbour = max((c.x1 for c in left), default=first.x0 - 60)
    margin = min(max((first.x0 - neighbour) * 0.5, 15.0), 80.0)
    return first.x0 - margin


# ── Lines ─────────────────────────────────────────────────────────────────────


def _date_prefix(words: list[PdfWord]) -> tuple[str | None, int]:
    """Return the date the line starts with (up to 3 words: ``03 ENE 2026``) and how many words it used."""
    best: tuple[str | None, int] = (None, 0)
    for n in (1, 2, 3):
        if len(words) < n:
            break
        candidate = " ".join(w.text for w in words[:n])
        if looks_like_date(candidate):
            best = (candidate, n)
    return best


_BALANCE_ROW_START = (
    "SALDO",
    "OPENING",
    "CLOSING",
    "PREVIOUS BALANCE",
    "NEW BALANCE",
    "BALANCE",
    "NUEVO SALDO",
)


def _balance_row(cells: _Cells, text: str) -> str | None:
    """Return "opening"/"closing" when the line is a balance row (its description starts with the label)."""
    description = fold(" ".join(cells.description))
    candidate = description if cells.date_raw else text
    if not candidate.startswith(_BALANCE_ROW_START):
        return None
    found = balance_label(candidate)
    return found[0] if found else None


def _is_amount(word: PdfWord, numeric_start: float, money_only: bool) -> bool:
    if word.center < numeric_start or looks_like_date(word.text):
        return False
    if not parse_amount(word.text).candidates:
        return False
    return not money_only or bool(_MONEY.search(word.text))


def _cells(
    line: PdfLine,
    columns: list[Column],
    numeric_start: float,
    money_only: bool,
) -> tuple[_Cells, list[str]]:
    """Split a line into cells; return them and any problems placing numbers."""
    problems: list[str] = []
    date_raw, used = _date_prefix(line.words)
    rest = line.words[used:]
    if (
        date_raw
        and rest
        and looks_like_date(rest[0].text)
        and any(c.kind == "value_date" for c in columns)
    ):
        rest = rest[1:]  # the value date
    cells = _Cells(page=line.page, top=line.top, raw_text=line.text, date_raw=date_raw)

    numeric_columns = [c for c in columns if c.kind in NUMERIC_KINDS]
    text_columns = [
        c for c in columns if c.kind in TEXT_KINDS and c.x0 >= numeric_start - 10
    ]
    taken: set[int] = set()
    tokens: list[tuple[PdfWord, str]] = []  # (word, raw incl. glued sign words)
    pending_currency: str | None = None
    for word in rest:
        upper = fold(word.text)
        if tokens and upper in _SIGN_WORDS and word.x0 - tokens[-1][0].x1 < 8:
            tokens[-1] = (tokens[-1][0], f"{tokens[-1][1]} {word.text}")
            continue
        if _is_amount(word, numeric_start, money_only):
            tokens.append((word, word.text))
            continue
        marker = parse_currency(word.text)
        if marker and word.center >= numeric_start and not text_columns:
            pending_currency = marker  # "U$S 120,00" in an amount column
            continue
        if word.center >= numeric_start and text_columns:
            column = min(
                text_columns,
                key=lambda c: (
                    c.distance(word.x0, word.x1),
                    abs(c.center - word.center),
                ),
            )
            if column.kind == "currency":
                cells.currency_raw = (
                    f"{cells.currency_raw} {word.text}"
                    if cells.currency_raw
                    else word.text
                )
            continue
        cells.description.append(word.text)

    for word, raw in tokens:
        ranked = sorted(
            (i for i in range(len(numeric_columns)) if i not in taken),
            key=lambda i: (
                numeric_columns[i].distance(word.x0, word.x1),
                abs(numeric_columns[i].center - word.center),
            ),
        )
        if not ranked:
            problems.append(f"more numbers than numeric columns: {raw}")
            continue
        index = ranked[0]
        taken.add(index)
        column = numeric_columns[index]
        cells.numbers[column.kind] = raw
        if column.kind != "balance":
            cells.amount_currency = column.currency or pending_currency
    return cells, problems


# ── Reading ───────────────────────────────────────────────────────────────────


def _money_only(document: PdfDocument) -> bool:
    """Return True when the statement prints money with two decimals (then bare integers are not amounts)."""
    numbers = [
        w.text
        for p in document.pages
        for line in p.lines
        for w in line.words
        if parse_amount(w.text).candidates and not looks_like_date(w.text)
    ]
    if len(numbers) < 5:
        return False
    shaped = sum(1 for n in numbers if _MONEY.search(n))
    return shaped >= 0.5 * len(numbers)


def read_table(
    document: PdfDocument,
    period_start: str | None = None,
    period_end: str | None = None,
    account_type: AccountType | None = None,
    profile: BankProfile = GENERIC,
    local_currency: str | None = "UYU",
) -> TableReading:
    """Read every page's movements. ``found_table`` is False when no header row was found."""
    reading = TableReading(found_table=False)
    money_only = _money_only(document)
    skips = [re.compile(s) for s in profile.skip_lines]
    columns: list[Column] | None = None
    numeric_start = 0.0
    cells_rows: list[_Cells] = []
    ignored_with_numbers: list[str] = []
    placement_problems: list[str] = []
    gaps: list[float] = []

    for page in document.pages:
        page_columns: list[Column] | None = None
        previous_top: float | None = None
        for line in page.lines:
            declared = header_columns(line, profile, local_currency)
            if declared:
                page_columns = columns = declared
                numeric_start = _numeric_start(columns)
                if not reading.columns:
                    reading.columns = declared
                reading.found_table = True
                previous_top = None
                continue
            if columns is None:
                continue  # above the first table header
            text = fold(line.text)
            if _SKIP.search(text) or any(s.search(text) for s in skips):
                continue

            cells, problems = _cells(line, columns, numeric_start, money_only)
            label = _balance_row(cells, text)
            if label:
                if cells.numbers:  # in the table: take the balance column
                    raw = cells.numbers.get("balance") or next(
                        iter(cells.numbers.values())
                    )
                    currency = (
                        parse_currency(cells.currency_raw, bare_dollar=local_currency)
                        or cells.amount_currency
                    )
                    target = (
                        reading.opening_raw
                        if label == "opening"
                        else reading.closing_raw
                    )
                    if label == "closing" or currency not in target:
                        target[currency] = raw
                continue  # outside the columns (a footer line): the header reader takes it

            if cells.date_raw and cells.numbers:
                if page_columns is None and not cells_rows:
                    page_columns = columns  # a page without its own header
                cells_rows.append(cells)
                placement_problems.extend(problems)
                if previous_top is not None:
                    gaps.append(line.top - previous_top)
                previous_top = line.top
                continue

            last = cells_rows[-1] if cells_rows else None
            same_block = (
                last is not None
                and last.page == line.page
                and line.top - last.top
                <= 2.2 * (statistics.median(gaps) if gaps else 14.0)
            )
            if (
                cells.numbers
                and set(cells.numbers) == {"balance"}
                and same_block
                and last
            ):
                last.numbers.setdefault(
                    "balance", cells.numbers["balance"]
                )  # that day's balance
                continue
            if (
                cells.numbers
                and same_block
                and last
                and not cells.date_raw
                and set(cells.numbers) - {"balance"}
                and not _SUMMARY.search(text)
            ):
                cells_rows.append(cells)  # same day as the previous movement
                placement_problems.extend(problems)
                continue
            if not cells.numbers and same_block and last and cells.description:
                last.description.extend(cells.description)  # a wrapped description
                last.raw_text += "\n" + line.text
                last.top = line.top
                continue
            if cells.numbers:
                ignored_with_numbers.append(line.text)

    reading.decimal = profile.decimal or detect_decimal_separator(
        raw for c in cells_rows for raw in c.numbers.values()
    )
    card_as_printed = _card_signs_as_printed(cells_rows, account_type, profile, reading)
    last_date: str | None = None
    for index, cells in enumerate(cells_rows):
        row, currency = _row(
            index,
            cells,
            reading,
            period_start,
            period_end,
            account_type,
            profile,
            local_currency,
            card_as_printed,
        )
        if row.date is None and cells.date_raw is None and last_date:
            row = row.model_copy(update={"date": last_date})
        last_date = row.date or last_date
        reading.rows.append(row)
        reading.row_currencies.append(currency)

    if ignored_with_numbers:
        reading.diagnostics.append(
            diagnostic(
                "ignored_lines_with_amounts",
                "lines with amounts that were not read as movements (possible missed rows)",
                count=len(ignored_with_numbers),
                examples=ignored_with_numbers[:3],
            )
        )
    if placement_problems:
        reading.diagnostics.append(
            diagnostic(
                "number_placement",
                "; ".join(placement_problems[:5]),
                count=len(placement_problems),
            )
        )
    inherited = sum(1 for c in cells_rows if c.date_raw is None)
    if inherited:
        reading.diagnostics.append(
            diagnostic(
                "date_inherited",
                "movements printed without a date take the previous one",
                count=inherited,
            )
        )
    return reading


def _card_signs_as_printed(
    cells_rows: list[_Cells],
    account_type: AccountType | None,
    profile: BankProfile,
    reading: TableReading,
) -> bool:
    """Decide how a card statement's single amount column is signed.

    Issuers usually print charges unsigned and payments with a minus; some
    exports print from the holder's side (charges negative). Most movements on a
    card are charges, so the majority sign says which convention this statement
    uses. The choice is recorded.
    """
    if account_type != "card" or profile.unsigned_amount_is is not None:
        return False
    amounts = [
        parse_amount(c.numbers["amount"]) for c in cells_rows if "amount" in c.numbers
    ]
    unworded = [a for a in amounts if a.magnitude is not None and not a.sign_word]
    if not unworded:
        return False
    negative = sum(1 for a in unworded if a.sign == -1)
    as_printed = negative > len(unworded) / 2
    reading.diagnostics.append(
        diagnostic(
            "card_sign_convention",
            "most amounts are negative: read as printed (negative = charge)"
            if as_printed
            else "most amounts are unsigned: unsigned = charge, minus = payment or refund",
            count=len(unworded),
        )
    )
    return as_printed


def _row(
    index: int,
    cells: _Cells,
    reading: TableReading,
    period_start: str | None,
    period_end: str | None,
    account_type: AccountType | None,
    profile: BankProfile,
    local_currency: str | None,
    card_as_printed: bool = False,
) -> tuple[StatementRow, str | None]:
    decimal = reading.decimal
    debit = parse_amount(cells.numbers.get("debit"), decimal)
    credit = parse_amount(cells.numbers.get("credit"), decimal)
    single = parse_amount(cells.numbers.get("amount"), decimal)
    if debit.magnitude == 0:
        debit = parse_amount(None)
    if credit.magnitude == 0:
        credit = parse_amount(None)

    amount = None
    alternatives: list[float] = []
    sign_source: SignSource | None = None
    if debit.magnitude is not None or credit.magnitude is not None:
        chosen, sign = (debit, -1) if debit.magnitude is not None else (credit, 1)
        magnitude = chosen.magnitude
        assert magnitude is not None
        amount = float(magnitude * sign)
        alternatives = [float(c) for c in chosen.candidates[1:]]
        sign_source = "column"
        if debit.magnitude is not None and credit.magnitude is not None:
            reading.diagnostics.append(
                diagnostic(
                    "debit_and_credit",
                    f"row {index} has both a debit and a credit",
                    row_index=index,
                )
            )
    elif single.magnitude is not None:
        printed = single.sign
        is_card = account_type == "card" and not card_as_printed
        if printed is not None and (single.sign_word or not is_card):
            sign, sign_source = printed, "marker"
        elif printed is not None:  # card: a printed minus is a payment or refund
            sign, sign_source = -printed, "marker"
        else:
            unsigned = profile.unsigned_amount_is or ("debit" if is_card else "credit")
            sign, sign_source = (-1 if unsigned == "debit" else 1), "convention"
        amount = float(single.magnitude * sign)
        alternatives = [float(c) for c in single.candidates[1:]]

    balance = parse_amount(cells.numbers.get("balance"), decimal).signed()
    description = " ".join(cells.description).strip()
    installment = parse_installment(description)
    currency = (
        parse_currency(cells.currency_raw, bare_dollar=local_currency)
        or cells.amount_currency
    )
    row = StatementRow(
        row_index=index,
        page=cells.page,
        raw_text=cells.raw_text,
        date_raw=cells.date_raw,
        description_raw=description,
        debit_raw=cells.numbers.get("debit"),
        credit_raw=cells.numbers.get("credit"),
        amount_raw=cells.numbers.get("amount"),
        balance_raw=cells.numbers.get("balance"),
        date=parse_date(cells.date_raw, period_start, period_end)
        if cells.date_raw
        else None,
        amount=amount,
        amount_alternatives=alternatives,
        balance_after=float(balance) if balance is not None else None,
        sign_source=sign_source,
        installment_n=installment[0] if installment else None,
        installment_m=installment[1] if installment else None,
    )
    return row, currency
