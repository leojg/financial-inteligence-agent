"""Unit tests for shared.parsing (amounts, dates, currency, statement vocabulary)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from shared.parsing import (
    balance_row_kind,
    detect_decimal_separator,
    looks_like_date,
    parse_amount,
    parse_currency,
    parse_date,
    parse_installment,
)

D = Decimal


# ── Amounts ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", D("1234.56")),
        ("1,234.56", D("1234.56")),
        ("1.234.567,89", D("1234567.89")),
        ("120,50", D("120.50")),
        ("54.99", D("54.99")),
        ("15000", D("15000")),
        ("-18500", D("-18500")),
        ("-3,200.00", D("-3200.00")),
        ("+1,800.00", D("1800.00")),
        ("1.234,56-", D("-1234.56")),
        ("(45.00)", D("-45.00")),
        ("−1.234,56", D("-1234.56")),
        ("$ 1.234,56", D("1234.56")),
        ("US$ 54,99", D("54.99")),
        ("1 234,56", D("1234.56")),
        ("120,00 CR", D("120.00")),
        ("120,00 DB", D("-120.00")),
    ],
)
def test_parse_amount_preferred_reading(raw: str, expected: Decimal) -> None:
    assert parse_amount(raw).signed() == expected


def test_parse_amount_keeps_both_readings_of_ambiguous_thousands() -> None:
    parsed = parse_amount("4.000")
    assert parsed.candidates == (D("4000"), D("4.000"))


def test_parse_amount_declared_separator_removes_ambiguity() -> None:
    assert parse_amount("4.000", decimal=",").candidates == (D("4000"),)
    assert parse_amount("4.000", decimal=".").candidates == (D("4.000"),)


def test_parse_amount_declared_separator_contradicted_is_unparsable() -> None:
    assert parse_amount("1,234.56", decimal=",").candidates == ()


@pytest.mark.parametrize("raw", ["", None, "   ", "abc", "12/01/2026", "1,2,3", "N/A"])
def test_parse_amount_unparsable_is_none_never_zero(raw: str | None) -> None:
    parsed = parse_amount(raw)
    assert parsed.magnitude is None
    assert parsed.signed() is None


def test_parse_amount_sign_none_when_not_printed() -> None:
    parsed = parse_amount("1.234,56")
    assert parsed.sign is None
    assert parsed.signed(default_sign=-1) == D("-1234.56")


def test_detect_decimal_separator() -> None:
    assert detect_decimal_separator(["1.234,56", "120,00", "4.000"]) == ","
    assert detect_decimal_separator(["1,234.56", "54.99"]) == "."
    assert detect_decimal_separator(["4.000", "15000"]) is None
    assert detect_decimal_separator(["1.234,56", "54.99"]) is None  # contradiction


# ── Dates ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("03/01/2026", "2026-01-03"),
        ("03/01/26", "2026-01-03"),
        ("3-1-2026", "2026-01-03"),
        ("03.01.2026", "2026-01-03"),
        ("2026-01-03", "2026-01-03"),
        ("03 ENE 2026", "2026-01-03"),
        ("03-JAN-26", "2026-01-03"),
        ("15 setiembre 2026", "2026-09-15"),
    ],
)
def test_parse_date_day_first(raw: str, expected: str) -> None:
    assert parse_date(raw) == expected


def test_parse_date_never_month_first() -> None:
    assert parse_date("01/03/2026") == "2026-03-01"


@pytest.mark.parametrize("raw", ["31/02/2026", "", None, "SUPERMERCADO", "13/13/2026"])
def test_parse_date_invalid_is_none(raw: str | None) -> None:
    assert parse_date(raw) is None


def test_parse_date_without_year_uses_period() -> None:
    assert parse_date("03/01", "2026-01-01", "2026-01-31") == "2026-01-03"
    # A period spanning New Year: December belongs to the first year.
    assert parse_date("28/12", "2025-12-15", "2026-01-14") == "2025-12-28"
    assert parse_date("05/01", "2025-12-15", "2026-01-14") == "2026-01-05"
    assert parse_date("03/01") is None


def test_looks_like_date() -> None:
    assert looks_like_date("03/01/2026")
    assert looks_like_date("03/01")
    assert looks_like_date("03-ENE")
    assert not looks_like_date("1.234,56")
    assert not looks_like_date("SUPER")


# ── Currency ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("US$", "USD"),
        ("U$S 120", "USD"),
        ("Dólares", "USD"),
        ("All amounts in USD", "USD"),
        ("$U", "UYU"),
        ("Importe (UYU)", "UYU"),
        ("Moneda: Pesos", "UYU"),
        ("Pesos argentinos", "ARS"),
        ("R$", "BRL"),
        ("€ 20", "EUR"),
    ],
)
def test_parse_currency_markers(raw: str, expected: str) -> None:
    assert parse_currency(raw) == expected


def test_parse_currency_bare_dollar_only_with_context() -> None:
    assert parse_currency("$ 1.234") is None
    assert parse_currency("$ 1.234", bare_dollar="UYU") == "UYU"
    assert parse_currency("US$ 1.234", bare_dollar="UYU") == "USD"


# ── Statement vocabulary ──────────────────────────────────────────────────────


def test_balance_row_kind() -> None:
    assert balance_row_kind("SALDO ANTERIOR") == "opening"
    assert balance_row_kind("Saldo inicial") == "opening"
    assert balance_row_kind("Opening balance") == "opening"
    assert balance_row_kind("SALDO FINAL") == "closing"
    assert balance_row_kind("Saldo a pagar") == "closing"
    assert balance_row_kind("SUPERMERCADO DISCO") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TIENDA X CUOTA 03/06", (3, 6)),
        ("TIENDA X CUOTAS 3 DE 6", (3, 6)),
        ("TIENDA X CTA 2/12", (2, 12)),
        ("TIENDA X C.03/06", (3, 6)),
        ("TIENDA X 3/6 CUOTAS", (3, 6)),
        ("TIENDA X 03/06", None),  # a bare n/m is a date, not an installment
        ("TIENDA X CUOTA 7/6", None),
    ],
)
def test_parse_installment(raw: str, expected: tuple[int, int] | None) -> None:
    assert parse_installment(raw) == expected
