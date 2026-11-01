"""Phase 1 of v1.6: statement header, legacy statements, the check node, persistence, migration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect

import shared.db as _db
from agents.reconciliator.configuration import ReconciliationConfig
from agents.reconciliator.nodes import generate_report, make_check_statements_node
from agents.reconciliator.statements.build import legacy_statements, summarize
from agents.reconciliator.statements.header import (
    HeaderSection,
    HeaderTranscription,
    StatementHeader,
    labeled_amounts,
    read_header,
    read_header_with_fallback,
)
from agents.reconciliator.statements.pdf import load_pdf
from shared.models import Transaction
from shared.repositories.database_repository import DatabaseRepository

from .statement_pdfs import (
    StatementLayout,
    build_statement_pdf,
    deposit_layout,
)

# ── Header ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def deposit_pdf(tmp_path: Path) -> Path:
    return build_statement_pdf(tmp_path / "brou_enero.pdf", deposit_layout())


def test_read_header_from_rules(deposit_pdf: Path) -> None:
    header = read_header(load_pdf(str(deposit_pdf)))
    assert header.institution == "BROU"
    assert header.account == "7203"
    assert header.account_type == "deposit"
    assert (header.period_start, header.period_end) == ("2026-01-01", "2026-01-31")
    assert len(header.sections) == 1
    section = header.sections[0]
    assert section.currency == "UYU"  # "Moneda: $" in a Uruguayan header
    assert (section.opening_raw, section.closing_raw) == ("15.000,00", "5.459,50")
    assert section.printed_totals() == {"debits": 13540.5, "credits": 4000.0}
    assert header.complete


def test_labeled_amounts_pairs_currencies() -> None:
    assert labeled_amounts(" $ 1.234,56 U$S 12,00", "UYU") == [
        ("UYU", "1.234,56"),
        ("USD", "12,00"),
    ]
    assert labeled_amounts(" 13.540,50 TOTAL CREDITOS 4.000,00", "UYU") == [
        (None, "13.540,50")
    ]


def test_header_llm_is_not_called_when_rules_are_complete(deposit_pdf: Path) -> None:
    calls: list[Any] = []
    read_header_with_fallback(
        load_pdf(str(deposit_pdf)), lambda d: calls.append(d) or {}
    )
    assert calls == []


def _no_balance_pdf(tmp_path: Path) -> Path:
    layout = deposit_layout()
    layout.rows = [r for r in layout.rows if "SALDO" not in r[1]]
    layout.footer_lines = [
        "Saldo al cierre del período 5.459,50"
    ]  # wording the rules do not know
    return build_statement_pdf(tmp_path / "no_balance.pdf", layout)


def test_header_llm_fills_gaps_and_values_must_be_in_the_text(tmp_path: Path) -> None:
    document = load_pdf(str(_no_balance_pdf(tmp_path)))
    transcription = HeaderTranscription(
        institution=None, account_as_printed=None, account_type=None,
        period_start_as_printed=None, period_end_as_printed=None,
        sections=[{
            "currency_as_printed": "$",
            "opening_balance_as_printed": "15.000,00",  # not printed anywhere: dropped
            "closing_balance_as_printed": "5.459,50",
            "total_debits_as_printed": None, "total_credits_as_printed": None,
        }],
    )  # fmt: skip
    header = read_header_with_fallback(document, lambda d: transcription)
    section = header.sections[0]
    assert section.closing_raw == "5.459,50"
    assert section.opening_raw is None
    assert header.source == "rules+llm"
    assert any(d["reason"] == "llm_value_not_in_text" for d in header.diagnostics)


def test_header_llm_failure_is_recorded(tmp_path: Path) -> None:
    def boom(_: Any) -> Any:
        raise RuntimeError("rate limited")

    header = read_header_with_fallback(load_pdf(str(_no_balance_pdf(tmp_path))), boom)
    assert any(d["reason"] == "header_llm_failed" for d in header.diagnostics)


# ── Legacy statements (measure without changing) ─────────────────────────────


def _tx(
    i: int,
    merchant: str,
    amount: float,
    currency: str = "UYU",
    date: str = "2026-01-05",
) -> Transaction:
    return Transaction(
        id=f"t{i}", date=date, amount_original=amount, currency=currency,
        merchant=merchant, account="BROU", source_file="s.pdf",
    )  # fmt: skip


def _header(
    opening: str | None = "15.000,00", closing: str | None = "5.459,50"
) -> StatementHeader:
    return StatementHeader(
        account="7203", period_start="2026-01-01", period_end="2026-01-31",
        sections=[HeaderSection(currency="UYU", opening_raw=opening, closing_raw=closing)],
    )  # fmt: skip


LEGACY_OK = [
    ("PAGO ALQUILER", -8500.0), ("TRANSFERENCIA RECIBIDA ITAU", 4000.0),
    ("UTE FACTURA", -2340.5), ("COMPRA DISCO", -1200.0), ("RETIRO CAJERO", -1500.0),
]  # fmt: skip


def test_legacy_statement_closes_and_links_transactions() -> None:
    txs = [_tx(i, m, a) for i, (m, a) in enumerate(LEGACY_OK)]
    statements, linked = legacy_statements("s.pdf", "sha", _header(), txs)
    assert [s.balance_status for s in statements] == ["ok"]
    assert {t.statement_id for t in linked} == {statements[0].id}
    assert [t.amount_original for t in linked] == [a for _, a in LEGACY_OK]  # unchanged


def test_legacy_measures_balance_rows_and_foreign_currency() -> None:
    txs = [_tx(0, "SALDO ANTERIOR", 15000.0)] + [
        _tx(i + 1, m, a, currency="USD" if i == 3 else "UYU")
        for i, (m, a) in enumerate(LEGACY_OK)
    ]
    statements, _ = legacy_statements("s.pdf", "sha", _header(), txs)
    statement = statements[0]
    reasons = {d["reason"]: d for d in statement.diagnostics}
    assert (
        statement.balance_status == "mismatch"
    )  # the opening balance was booked as a movement
    assert reasons["balance_row_as_transaction"]["count"] == 1
    assert reasons["currency_differs_from_header"]["count"] == 1
    assert (
        len(statement.rows) == 6
    )  # the foreign-currency row still belongs to this statement


def test_legacy_without_rows_is_diagnosed() -> None:
    statements, _ = legacy_statements(
        "s.pdf", "sha", _header(opening=None, closing=None), []
    )
    assert statements[0].balance_status == "no_balance"
    assert statements[0].diagnostics[0]["reason"] == "no_rows"


# ── Node, report, persistence ─────────────────────────────────────────────────


def _config(extractor: str = "legacy") -> ReconciliationConfig:
    return ReconciliationConfig(
        statement_extractor=extractor, statement_header_llm=False
    )


def test_check_statements_node_legacy(deposit_pdf: Path) -> None:
    path = str(deposit_pdf)
    txs = [
        _tx(i, m, a).model_copy(update={"source_file": path})
        for i, (m, a) in enumerate(LEGACY_OK)
    ]
    receipt = _tx(99, "CAFE", -100.0).model_copy(update={"source_file": "receipt.png"})
    with patch("agents.reconciliator.nodes.ChatOpenAI"):
        node = make_check_statements_node(_config())
    result = node(
        {
            "transactions": [*txs, receipt],
            "source_files": [path, "receipt.png"],
            "statements": [],
        }
    )
    statements = result["statements"]
    assert len(statements) == 1
    assert statements[0].balance_status == "ok"
    assert statements[0].file_sha256 == load_pdf(path).sha256
    by_id = {t.id: t for t in result["transactions"]}
    assert by_id["t0"].statement_id == statements[0].id
    assert by_id["t99"].statement_id is None  # not a statement


def test_check_statements_node_unreadable_pdf_is_not_silent(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    with patch("agents.reconciliator.nodes.ChatOpenAI"):
        node = make_check_statements_node(_config())
    result = node({"transactions": [], "source_files": [str(broken)], "statements": []})
    assert result["statements"][0].diagnostics[0]["reason"] == "unreadable_pdf"


def test_check_statements_node_does_nothing_for_other_extractors(
    deposit_pdf: Path,
) -> None:
    with patch("agents.reconciliator.nodes.ChatOpenAI"):
        node = make_check_statements_node(_config("ladder"))
    result = node(
        {"transactions": [], "source_files": [str(deposit_pdf)], "statements": []}
    )
    assert result == {"statements": []}


def test_generate_report_persists_statements_and_reports_them() -> None:
    txs = [_tx(i, m, a) for i, (m, a) in enumerate(LEGACY_OK)]
    statements, linked = legacy_statements(
        "s.pdf", "sha", _header(closing="5.000,00"), txs
    )
    state: dict[str, Any] = {
        "transactions": linked, "duplicates": [], "suspicious": [], "receipts": [],
        "statements": statements, "run_id": "run-1", "source_paths": ["s.pdf"],
    }  # fmt: skip
    result = generate_report(state, {"configurable": {"thread_id": "th"}})
    assert "Statements: 1 (mismatch 1)" in result["report"]
    assert "gap -459.50" in result["report"]

    repo = DatabaseRepository()
    stored = repo.get_run_statements("run-1")
    assert len(stored) == 1
    assert stored[0]["balance_status"] == "mismatch"
    assert stored[0]["row_count"] == 5
    assert stored[0]["diagnostics"] == []
    assert {t["statement_id"] for t in repo.get_run_transactions("run-1")} == {
        statements[0].id
    }


def test_summarize_counts_by_status() -> None:
    txs = [_tx(i, m, a) for i, (m, a) in enumerate(LEGACY_OK)]
    ok, _ = legacy_statements("a.pdf", "sha", _header(), txs)
    bad, _ = legacy_statements("b.pdf", "sha", _header(closing="1,00"), txs)
    summary = summarize(ok + bad)
    assert summary["by_status"] == {"ok": 1, "mismatch": 1}
    assert [s["file"] for s in summary["statements"]] == ["a.pdf", "b.pdf"]


# ── Migration ─────────────────────────────────────────────────────────────────


def test_migration_adds_statements_to_an_existing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic import command
    from alembic.config import Config

    url = f"sqlite:///{tmp_path / 'agent.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(_db, "_engine", create_engine(url))
    root = Path(_db.__file__).resolve().parents[3]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(root / "src" / "shared" / "db" / "alembic")
    )

    command.upgrade(cfg, "593168213649")  # a database from before v1.6
    command.upgrade(cfg, "head")

    inspector = inspect(_db._engine)
    assert {"statements", "statement_extraction_cache"} <= set(
        inspector.get_table_names()
    )
    columns = {c["name"] for c in inspector.get_columns("transactions")}
    assert {
        "statement_id",
        "raw_text",
        "balance_after",
        "sign_source",
        "installment_m",
    } <= columns


def test_layout_helper_paginates(tmp_path: Path) -> None:
    layout: StatementLayout = deposit_layout(rows_per_page=2)
    document = load_pdf(str(build_statement_pdf(tmp_path / "paged.pdf", layout)))
    assert len(document.pages) == 3
    assert "Página 2 de 3" in document.pages[1].text
