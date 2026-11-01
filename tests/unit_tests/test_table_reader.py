"""Unit tests for the deterministic statement table reader (v1.6 phase 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.reconciliator.statements.balance import check_balance
from agents.reconciliator.statements.build import table_statements
from agents.reconciliator.statements.header import read_header
from agents.reconciliator.statements.pdf import load_pdf
from agents.reconciliator.statements.profiles import BankProfile, detect_profile
from agents.reconciliator.statements.table_reader import (
    TableReading,
    header_columns,
    read_table,
)
from shared.models import Statement

from .statement_pdfs import (
    DEPOSIT_COLUMNS,
    Column,
    StatementLayout,
    build_statement_pdf,
    card_layout,
    deposit_layout,
)

DATA = Path(__file__).resolve().parents[2] / "data"


def _read(path: Path) -> tuple[list[Statement], TableReading]:
    document = load_pdf(str(path))
    header = read_header(document)
    reading = read_table(
        document, header.period_start, header.period_end, header.account_type
    )
    statements = table_statements(str(path), document.sha256, header, reading)
    return [check_balance(s) for s in statements], reading


# ── Deposit account: Débito / Crédito / Saldo, several pages ──────────────────


def test_deposit_statement_over_several_pages_closes(tmp_path: Path) -> None:
    statements, reading = _read(
        build_statement_pdf(tmp_path / "d.pdf", deposit_layout(rows_per_page=3))
    )
    assert reading.found_table
    assert len(statements) == 1
    s = statements[0]
    assert s.balance_status == "ok", s.balance_detail
    assert s.currency == "UYU"
    assert [r.amount for r in s.rows] == [-8500.0, 4000.0, -2340.5, -1200.0, -1500.0]
    assert {r.sign_source for r in s.rows} == {"column"}
    assert [r.page for r in s.rows] == [
        1,
        1,
        2,
        2,
        2,
    ]  # the SALDO ANTERIOR row takes a slot on page 1
    assert (
        s.rows[0].description_raw == "PAGO ALQUILER APTO 302 ENERO"
    )  # wrapped line joined
    assert s.rows[0].date == "2026-01-03"
    assert s.rows[-1].balance_after == 5459.5
    assert s.opening_balance == 15000.0  # from the SALDO ANTERIOR row, not a movement
    assert s.closing_balance == 5459.5  # from the footer line
    assert s.printed_totals == {"debits": 13540.5, "credits": 4000.0}


def test_misread_debit_breaks_the_chain_at_that_row(tmp_path: Path) -> None:
    layout = deposit_layout()
    layout.rows[3][2] = "2.430,50"  # printed amount disagrees with the balance column
    statements, _ = _read(build_statement_pdf(tmp_path / "d.pdf", layout))
    s = statements[0]
    assert s.balance_status == "mismatch"
    assert [b["row_index"] for b in s.breaks] == [2]


def test_daily_balance_line_and_same_day_movement(tmp_path: Path) -> None:
    layout = StatementLayout(
        title_lines=[
            "Cuenta Corriente ****1111 Moneda: $",
            "Período: 01/01/2026 al 31/01/2026",
        ],
        columns=DEPOSIT_COLUMNS,
        rows=[
            ["01/01/2026", "SALDO ANTERIOR", "", "", "1.000,00"],
            ["03/01/2026", "COMPRA A", "100,00", "", ""],
            ["", "COMPRA B", "50,00", "", ""],  # same day, no date printed
            ["", "", "", "", "850,00"],  # that day's balance on its own line
            ["04/01/2026", "DEPOSITO", "", "200,00", "1.050,00"],
        ],
        footer_lines=["SALDO FINAL 1.050,00"],
    )
    statements, reading = _read(build_statement_pdf(tmp_path / "d.pdf", layout))
    s = statements[0]
    assert s.balance_status == "ok", s.balance_detail
    assert [r.date for r in s.rows] == ["2026-01-03", "2026-01-03", "2026-01-04"]
    assert s.rows[1].balance_after == 850.0
    assert any(d["reason"] == "date_inherited" for d in reading.diagnostics)


def test_subtotals_footers_and_stray_numbers(tmp_path: Path) -> None:
    layout = deposit_layout()
    layout.rows.insert(3, ["", "TRANSPORTE", "8.500,00", "4.000,00", "10.500,00"])
    layout.footer_lines.append(
        "Disponible en otras cuentas 99.999,99"
    )  # far below, not a movement
    statements, reading = _read(build_statement_pdf(tmp_path / "d.pdf", layout))
    assert statements[0].balance_status == "ok", statements[0].balance_detail
    assert len(statements[0].rows) == 5


def test_numbers_in_descriptions_are_not_amounts(tmp_path: Path) -> None:
    layout = deposit_layout()
    layout.rows[4][1] = "COMPRA DISCO PEDIDO 4521"
    statements, _ = _read(build_statement_pdf(tmp_path / "d.pdf", layout))
    row = statements[0].rows[3]
    assert row.description_raw == "COMPRA DISCO PEDIDO 4521"
    assert row.amount == -1200.0


# ── Credit card: two currencies, issuer sign convention, installments ────────


def test_two_currency_card_statement(tmp_path: Path) -> None:
    statements, reading = _read(build_statement_pdf(tmp_path / "c.pdf", card_layout()))
    by_currency = {s.currency: s for s in statements}
    assert set(by_currency) == {"UYU", "USD"}
    uyu, usd = by_currency["UYU"], by_currency["USD"]
    assert uyu.account_type == "card"
    assert uyu.balance_status == "ok", uyu.balance_detail
    assert usd.balance_status == "ok", usd.balance_detail
    assert [r.amount for r in uyu.rows] == [
        1000.0,
        -2500.0,
        -1200.0,
    ]  # payment +, charges -
    assert [r.amount for r in usd.rows] == [-15.99, -34.01]
    assert (uyu.opening_balance, uyu.closing_balance) == (1000.0, 3700.0)
    assert (usd.opening_balance, usd.closing_balance) == (50.0, 100.0)
    cuota = next(r for r in uyu.rows if "CUOTA" in r.description_raw)
    assert (cuota.installment_n, cuota.installment_m) == (2, 6)
    assert any(d["reason"] == "card_sign_convention" for d in reading.diagnostics)


# ── The repo's synthetic statements ───────────────────────────────────────────


@pytest.mark.parametrize(
    "name", ["wise_usd_account_january_2026.pdf", "visa_credit_card_january_2026.pdf"]
)
def test_synthetic_statements_match_their_labels(name: str) -> None:
    labels = json.loads((DATA / "eval_labels.json").read_text())["normalization"]
    expected = next(
        e["expected_transactions"] for e in labels if e["source_file"] == name
    )
    statements, _ = _read(DATA / name)
    rows = [r for s in statements for r in s.rows]
    got = sorted((r.date, round(r.amount or 0, 2), r.description_raw) for r in rows)
    want = sorted((e["date"], round(e["amount"], 2), e["merchant"]) for e in expected)
    assert got == want
    assert {s.currency for s in statements} == {expected[0]["currency"]}
    assert all(s.balance_status == "ok" for s in statements)  # printed totals close


# ── Header rows, profiles, no table ───────────────────────────────────────────


def test_header_row_with_currency_named_columns(tmp_path: Path) -> None:
    document = load_pdf(str(build_statement_pdf(tmp_path / "c.pdf", card_layout())))
    line = next(ln for ln in document.pages[0].lines if ln.text.startswith("Fecha"))
    columns = header_columns(line)
    assert columns is not None
    assert [(c.kind, c.currency) for c in columns] == [
        ("date", None),
        ("description", None),
        ("amount", "UYU"),
        ("amount", "USD"),
    ]


def test_no_table_header_is_reported(tmp_path: Path) -> None:
    layout = deposit_layout()
    layout.columns = [
        Column("", c.x, c.align) for c in layout.columns
    ]  # labels missing
    document = load_pdf(str(build_statement_pdf(tmp_path / "d.pdf", layout)))
    reading = read_table(document)
    assert not reading.found_table
    assert reading.rows == []


def test_profile_adds_labels_the_vocabulary_lacks(tmp_path: Path) -> None:
    layout = deposit_layout()
    layout.columns = [Column("Fch", 40), *DEPOSIT_COLUMNS[1:]]
    document = load_pdf(str(build_statement_pdf(tmp_path / "d.pdf", layout)))
    assert not read_table(document).found_table
    profile = BankProfile(
        name="test", detect="BANCO DE LA REPUBLICA", labels={"date": ("FCH",)}
    )
    assert detect_profile(document.pages[0].text, [profile]) is profile
    assert read_table(document, profile=profile).found_table
