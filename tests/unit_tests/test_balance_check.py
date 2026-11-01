"""Unit tests for the statement balance check (v1.6 §3.2)."""

from __future__ import annotations

from typing import Any

from agents.reconciliator.statements.balance import check_balance
from shared.models import Statement, StatementRow


def _row(
    i: int, amount: float | None, balance: float | None = None, **extra: Any
) -> StatementRow:
    return StatementRow(row_index=i, amount=amount, balance_after=balance, **extra)


def _statement(rows: list[StatementRow], **extra: Any) -> Statement:
    return Statement(id="s1", source_file="s.pdf", file_sha256="x", rows=rows, **extra)


# ── Statement closes ──────────────────────────────────────────────────────────


def test_deposit_statement_closes() -> None:
    s = _statement(
        [_row(0, -200.0), _row(1, 50.25)],
        account_type="deposit",
        opening_balance=1000.0,
        closing_balance=850.25,
    )
    result = check_balance(s)
    assert result.balance_status == "ok"
    assert result.breaks == []


def test_deposit_statement_does_not_close_reports_gap() -> None:
    s = _statement(
        [_row(0, -200.0), _row(1, 50.0)],
        account_type="deposit",
        opening_balance=1000.0,
        closing_balance=900.0,
    )
    result = check_balance(s)
    assert result.balance_status == "mismatch"
    assert "gap 50.00" in (result.balance_detail or "")


def test_card_statement_closes_with_owed_balance() -> None:
    # Owed 0 → two purchases and a payment → owed 400.
    rows = [_row(0, -300.0), _row(1, -200.0), _row(2, 100.0)]
    s = _statement(
        rows, account_type="card", opening_balance=0.0, closing_balance=400.0
    )
    assert check_balance(s).balance_status == "ok"


def test_unknown_account_type_inferred_as_card() -> None:
    rows = [_row(0, -300.0), _row(1, -200.0), _row(2, 100.0)]
    result = check_balance(_statement(rows, opening_balance=0.0, closing_balance=400.0))
    assert result.balance_status == "ok"
    assert result.account_type == "card"
    assert any(d["reason"] == "account_type_inferred" for d in result.diagnostics)


def test_no_balances_is_no_balance() -> None:
    result = check_balance(_statement([_row(0, -10.0), _row(1, -20.0)]))
    assert result.balance_status == "no_balance"


def test_unreadable_amount_is_mismatch() -> None:
    s = _statement(
        [_row(0, -200.0), _row(1, None, amount_raw="1.2.3")],
        account_type="deposit",
        opening_balance=1000.0,
        closing_balance=800.0,
    )
    result = check_balance(s)
    assert result.balance_status == "mismatch"
    assert "without a readable amount" in (result.balance_detail or "")


def test_printed_totals_are_checked() -> None:
    rows = [_row(0, -200.0), _row(1, -50.0), _row(2, 300.0)]
    ok = check_balance(
        _statement(rows, printed_totals={"debits": 250.0, "credits": 300.0})
    )
    assert ok.balance_status == "ok"
    bad = check_balance(_statement(rows, printed_totals={"debits": 260.0}))
    assert bad.balance_status == "mismatch"


# ── Running balance chain ─────────────────────────────────────────────────────


def test_running_balance_chain_verifies_each_link() -> None:
    rows = [_row(0, -200.0, 800.0), _row(1, 50.0, 850.0), _row(2, -25.5, 824.5)]
    s = _statement(
        rows, account_type="deposit", opening_balance=1000.0, closing_balance=824.5
    )
    result = check_balance(s)
    assert result.balance_status == "ok"
    assert result.balance_detail == "4 check(s) passed"  # 3 links + closing


def test_chain_derives_the_sign_of_a_row() -> None:
    # Printed (or transcribed) as a debit, but the balance went up: it was a credit.
    rows = [_row(0, -200.0, 800.0), _row(1, -50.0, 850.0, sign_source="llm")]
    s = _statement(rows, account_type="deposit", opening_balance=1000.0)
    result = check_balance(s)
    assert result.balance_status == "ok"
    assert result.rows[1].amount == 50.0
    assert result.rows[1].sign_source == "balance"
    assert any(d["reason"] == "sign_from_balance" for d in result.diagnostics)


def test_chain_break_points_at_the_row_and_does_not_cascade() -> None:
    rows = [
        _row(0, -200.0, 800.0),
        _row(1, -150.0, 700.0),  # balance says -100: misread amount
        _row(2, -50.0, 650.0),
    ]
    s = _statement(rows, account_type="deposit", opening_balance=1000.0)
    result = check_balance(s)
    assert result.balance_status == "mismatch"
    assert len(result.breaks) == 1
    assert result.breaks[0]["row_index"] == 1
    assert result.breaks[0]["gap"] == 50.0


def test_chain_gap_reveals_a_missing_row() -> None:
    # A -75 movement between rows 0 and 1 was not extracted.
    rows = [_row(0, -200.0, 800.0), _row(1, -25.0, 700.0)]
    s = _statement(rows, account_type="deposit", opening_balance=1000.0)
    result = check_balance(s)
    assert result.balance_status == "mismatch"
    assert result.breaks[0]["gap"] == -75.0


def test_chain_with_balances_only_on_some_rows() -> None:
    rows = [_row(0, -200.0), _row(1, -100.0), _row(2, 50.0, 750.0)]
    s = _statement(rows, account_type="deposit", opening_balance=1000.0)
    assert check_balance(s).balance_status == "ok"


# ── Ambiguous numbers and refusing to guess ──────────────────────────────────


def test_balance_picks_the_reading_of_an_ambiguous_amount() -> None:
    # "4.000" read as 4.0 but the statement only closes with 4000.
    rows = [
        _row(0, -4.0, amount_raw="4.000", amount_alternatives=[4000.0]),
        _row(1, -100.0),
    ]
    s = _statement(
        rows, account_type="deposit", opening_balance=10000.0, closing_balance=5900.0
    )
    result = check_balance(s)
    assert result.balance_status == "ok"
    assert result.rows[0].amount == -4000.0
    assert any(d["reason"] == "reading_from_balance" for d in result.diagnostics)


def test_two_rows_that_could_explain_the_gap_are_not_guessed() -> None:
    rows = [_row(0, -100.0), _row(1, -100.0)]
    s = _statement(
        rows, account_type="deposit", opening_balance=1000.0, closing_balance=1000.0
    )
    result = check_balance(s)
    assert result.balance_status == "mismatch"
    assert [r.amount for r in result.rows] == [-100.0, -100.0]


def test_check_does_not_mutate_the_input() -> None:
    rows = [_row(0, -200.0, 800.0), _row(1, -50.0, 850.0)]
    s = _statement(rows, account_type="deposit", opening_balance=1000.0)
    check_balance(s)
    assert s.rows[1].amount == -50.0
    assert s.balance_status is None
