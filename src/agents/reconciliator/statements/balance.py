"""Balance check: does a statement's own arithmetic close? (v1.6 spec §3.2).

Bank balances are exact to the cent, so a statement checks itself:

- **Statement closes:** opening balance + movements = closing balance.
- **Running balance chain:** each printed balance follows from the previous one
  plus the movements in between. A break points at the rows that are wrong.
- **Printed totals:** Σ debits and Σ credits match the footer, when printed.

For a deposit account the balance is what the user has, so a movement adds to
it. For a card the balance is what the user owes, so a charge (negative
amount) increases it.

When a check fails by exactly what one row's sign flip, or one row's other
reading of an ambiguous number (``4.000``), would explain — and only one row
fits — that row is corrected and the correction is recorded. Anything else is
left as printed and the statement is a ``mismatch``: never patched with an
invented row.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from shared.models import AccountType, Statement, StatementRow
from shared.parsing import to_cents

_ZERO = Decimal("0")


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else to_cents(Decimal(str(value)))


def _fmt(value: Decimal | None) -> str:
    return "?" if value is None else f"{value:,.2f}"


def _readable(rows: list[StatementRow]) -> list[Decimal]:
    return [a for a in (_dec(r.amount) for r in rows) if a is not None]


def diagnostic(reason: str, detail: str, **extra: Any) -> dict[str, Any]:
    """Build a diagnostics entry (shared shape across extraction and checks)."""
    return {"reason": reason, "detail": detail, **extra}


@dataclass
class _Fix:
    row_index: int
    amount: Decimal
    kind: str  # "sign" or "reading"


def _single_fix(rows: list[StatementRow], gap: Decimal, direction: int) -> _Fix | None:
    """Return the one row correction that explains ``gap``, or None if zero or several fit."""
    fits: list[_Fix] = []
    for row in rows:
        amount = _dec(row.amount)
        if amount is None or amount == _ZERO:
            continue
        if direction * (-amount - amount) == gap:
            fits.append(_Fix(row.row_index, -amount, "sign"))
        sign = -1 if amount < 0 else 1
        for alternative in row.amount_alternatives:
            magnitude = to_cents(Decimal(str(abs(alternative))))
            for candidate in {sign * magnitude, -sign * magnitude}:
                if candidate != amount and direction * (candidate - amount) == gap:
                    fits.append(_Fix(row.row_index, candidate, "reading"))
    return fits[0] if len(fits) == 1 else None


def _apply(rows: list[StatementRow], fix: _Fix) -> dict[str, Any]:
    """Apply a correction in place and return the diagnostic that records it."""
    position = next(i for i, r in enumerate(rows) if r.row_index == fix.row_index)
    row = rows[position]
    update: dict[str, Any] = {"amount": float(fix.amount)}
    if fix.kind == "sign":
        update["sign_source"] = "balance"
    rows[position] = row.model_copy(update=update)
    note = "; the printed column disagreed" if row.sign_source == "column" else ""
    return diagnostic(
        "sign_from_balance" if fix.kind == "sign" else "reading_from_balance",
        f"row {fix.row_index}: {_fmt(_dec(row.amount))} → {_fmt(fix.amount)} "
        f"(the only correction that makes the balance close{note})",
        row_index=fix.row_index,
        page=row.page,
    )


def _check_chain(
    statement: Statement,
    rows: list[StatementRow],
    direction: int,
    diagnostics: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    """Walk the running balance; return (verified links, breaks). Corrects rows in place."""
    verified = 0
    breaks: list[dict[str, Any]] = []
    previous = _dec(statement.opening_balance)
    segment: list[int] = []  # positions since the last printed balance
    for position in range(len(rows)):
        segment.append(position)
        printed = _dec(rows[position].balance_after)
        if printed is None:
            continue
        if previous is not None:
            members = [rows[i] for i in segment]
            amounts = _readable(members)
            if len(amounts) != len(members):
                breaks.append(
                    {"row_index": rows[position].row_index, "page": rows[position].page,
                     "expected": None, "printed": float(printed), "gap": None,
                     "reason": "unreadable amount"}
                )  # fmt: skip
            else:
                expected = previous + direction * sum(amounts, _ZERO)
                fix = None
                if expected != printed:
                    fix = _single_fix(members, printed - expected, direction)
                    if fix is not None:
                        diagnostics.append(_apply(rows, fix))
                if expected == printed or fix is not None:
                    verified += 1
                else:
                    breaks.append(
                        {"row_index": rows[position].row_index, "page": rows[position].page,
                         "first_row_index": members[0].row_index,
                         "expected": float(expected), "printed": float(printed),
                         "gap": float(printed - expected), "reason": "balance does not follow"}
                    )  # fmt: skip
        previous = printed  # resync: one bad row does not cascade
        segment = []
    return verified, breaks


def _check(statement: Statement, account_type: AccountType) -> Statement:
    direction = -1 if account_type == "card" else 1
    rows = list(statement.rows)
    diagnostics = list(statement.diagnostics)
    failures: list[str] = []
    verified = 0

    unreadable = len(rows) - len(_readable(rows))
    if unreadable:
        failures.append(f"{unreadable} row(s) without a readable amount")

    # 1. Running balance chain.
    has_chain = any(r.balance_after is not None for r in rows)
    breaks: list[dict[str, Any]] = []
    if has_chain:
        links, breaks = _check_chain(statement, rows, direction, diagnostics)
        verified += links
        if breaks:
            where = ", ".join(str(b["row_index"]) for b in breaks[:10])
            failures.append(f"running balance breaks at row(s) {where}")

    # 2. Statement closes: opening + movements = closing.
    opening, closing = _dec(statement.opening_balance), _dec(statement.closing_balance)
    if opening is not None and closing is not None and not unreadable:
        total = opening + direction * sum(_readable(rows), _ZERO)
        if total != closing and not has_chain:
            fix = _single_fix(rows, closing - total, direction)
            if fix is not None:
                diagnostics.append(_apply(rows, fix))
                total = opening + direction * sum(_readable(rows), _ZERO)
        if total == closing:
            verified += 1
        else:
            failures.append(
                f"opening {_fmt(opening)} {'+' if direction > 0 else '−'} movements "
                f"{_fmt(sum(_readable(rows), _ZERO))} = {_fmt(total)}, "
                f"statement says {_fmt(closing)} (gap {_fmt(closing - total)})"
            )
    elif (
        closing is not None
        and has_chain
        and rows
        and rows[-1].balance_after is not None
    ):
        last = _dec(rows[-1].balance_after)
        if last == closing:
            verified += 1
        else:
            failures.append(
                f"last running balance {_fmt(last)} ≠ closing {_fmt(closing)}"
            )

    # 3. Printed footer totals.
    amounts = _readable(rows)
    sums = {
        "debits": sum((-a for a in amounts if a < 0), _ZERO),
        "credits": sum((a for a in amounts if a > 0), _ZERO),
    }
    for key, printed_total in statement.printed_totals.items():
        if key not in sums:
            continue
        if sums[key] == _dec(printed_total):
            verified += 1
        else:
            failures.append(
                f"total {key} {_fmt(sums[key])} ≠ printed {_fmt(_dec(printed_total))}"
            )

    if failures:
        status, detail = "mismatch", "; ".join(failures)
    elif verified:
        status, detail = "ok", f"{verified} check(s) passed"
    else:
        status = "no_balance"
        detail = (
            "no opening/closing balance, running balance or totals to check against"
        )
    return statement.model_copy(
        update={
            "rows": rows,
            "balance_status": status,
            "balance_detail": detail,
            "breaks": breaks,
            "diagnostics": diagnostics,
        }
    )


def check_balance(statement: Statement) -> Statement:
    """Return the statement with ``balance_status``, ``balance_detail``, ``breaks`` and any corrections.

    When the account type is unknown, the deposit arithmetic is tried first; if it
    fails and the card arithmetic closes, the statement is a card statement.
    """
    if statement.account_type is not None:
        return _check(statement, statement.account_type)
    as_deposit = _check(statement, "deposit")
    if as_deposit.balance_status != "mismatch":
        return as_deposit
    as_card = _check(statement, "card")
    if as_card.balance_status == "ok":
        inferred = diagnostic(
            "account_type_inferred",
            "closes only as a card statement (the balance is the amount owed)",
        )
        return as_card.model_copy(
            update={
                "account_type": "card",
                "diagnostics": [*as_card.diagnostics, inferred],
            }
        )
    return as_deposit
