"""Unit tests for insights aggregation queries and compute_aggregations (no LLM)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

import shared.db as _db
from agents.insights.nodes import compute_aggregations
from agents.insights.tools import (
    get_month_over_month_deltas,
    get_recurring_charges,
    get_spending_by_category,
)
from shared.db.models import RunHistory, TransactionRecord
from shared.repositories.database_repository import DatabaseRepository


def _run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def _insert_run(session, run_id: str) -> None:
    session.add(
        RunHistory(
            run_id=run_id,
            thread_id="thread-1",
            created_at="2026-01-01T00:00:00",
            source_paths="[]",
            status="completed",
            base_currency="UYU",
        )
    )


def _tx(
    run_id: str,
    tid: str,
    *,
    date: str,
    amount_base: float,
    category: str,
    account: str = "Itaú Corriente",
    merchant: str = "MERCHANT",
    merchant_normalized: str = "merchant",
    duplicate_of: str | None = None,
) -> TransactionRecord:
    return TransactionRecord(
        id=tid,
        run_id=run_id,
        fingerprint=f"fp-{tid}",
        date=date,
        amount_original=amount_base,
        amount_base=amount_base,
        currency="UYU",
        merchant=merchant,
        merchant_normalized=merchant_normalized,
        account=account,
        source_file="data/test.xlsx",
        category=category,
        duplicate_of=duplicate_of,
        suspicious=0,
        needs_review=0,
    )


@pytest.fixture()
def session():
    with _db.get_session() as s:
        yield s


class TestGetSpendingByCategory:
    def test_sums_expenses_by_category_excludes_duplicates(self, session) -> None:
        rid = _run_id()
        _insert_run(session, rid)
        session.add(_tx(rid, "a1", date="2026-01-10", amount_base=-100.0, category="Dining"))
        session.add(_tx(rid, "a2", date="2026-01-11", amount_base=-50.0, category="Dining"))
        session.add(
            _tx(
                rid,
                "a3",
                date="2026-01-12",
                amount_base=-100.0,
                category="Dining",
                duplicate_of="a1",
            )
        )
        session.commit()

        repo = DatabaseRepository()
        rows = repo.get_spending_by_category("2026-01-01", "2026-01-31", None)
        by_cat = {r["category"]: r["total"] for r in rows}
        assert by_cat.get("Dining") == pytest.approx(150.0)

    def test_excludes_positive_amounts(self, session) -> None:
        rid = _run_id()
        _insert_run(session, rid)
        session.add(_tx(rid, "p1", date="2026-01-05", amount_base=5000.0, category="Salary"))
        session.add(_tx(rid, "p2", date="2026-01-06", amount_base=-80.0, category="Groceries"))
        session.commit()

        rows = DatabaseRepository().get_spending_by_category("2026-01-01", "2026-01-31", None)
        cats = {r["category"] for r in rows}
        assert "Salary" not in cats
        assert any(r["category"] == "Groceries" and r["total"] == pytest.approx(80.0) for r in rows)

    def test_account_filter(self, session) -> None:
        rid = _run_id()
        _insert_run(session, rid)
        session.add(
            _tx(
                rid,
                "x1",
                date="2026-01-03",
                amount_base=-10.0,
                category="Other",
                account="Account A",
            )
        )
        session.add(
            _tx(
                rid,
                "x2",
                date="2026-01-04",
                amount_base=-20.0,
                category="Other",
                account="Account B",
            )
        )
        session.commit()

        rows = DatabaseRepository().get_spending_by_category(
            "2026-01-01", "2026-01-31", ["Account A"]
        )
        assert len(rows) == 1
        assert rows[0]["total"] == pytest.approx(10.0)


class TestGetMonthOverMonthDeltasTool:
    def test_delta_pct_second_month(self, session) -> None:
        rid = _run_id()
        _insert_run(session, rid)
        for d, amt in (
            ("2026-01-15", -1000.0),
            ("2026-02-10", -500.0),
        ):
            session.add(
                _tx(
                    rid,
                    f"m-{d}",
                    date=d,
                    amount_base=amt,
                    category="Shopping",
                    merchant_normalized="shop",
                )
            )
        session.commit()

        out = get_month_over_month_deltas.invoke(
            {
                "date_from": "2026-01-01",
                "date_to": "2026-02-28",
                "accounts": None,
            }
        )
        assert len(out) == 2
        assert out[0]["delta_pct"] is None
        assert out[1]["month"] == "2026-02"
        assert out[1]["delta_pct"] == pytest.approx(-50.0)


class TestGetRecurringChargesTool:
    def test_finds_stable_two_month_merchant(self, session) -> None:
        rid = _run_id()
        _insert_run(session, rid)
        session.add(
            _tx(
                rid,
                "n1",
                date="2026-01-05",
                amount_base=-99.0,
                category="Entertainment",
                merchant_normalized="netflix sub",
            )
        )
        session.add(
            _tx(
                rid,
                "n2",
                date="2026-02-05",
                amount_base=-99.0,
                category="Entertainment",
                merchant_normalized="netflix sub",
            )
        )
        session.commit()

        out = get_recurring_charges.invoke(
            {"date_from": "2026-01-01", "date_to": "2026-02-28", "accounts": None}
        )
        assert len(out) >= 1
        top = next(x for x in out if x["merchant_normalized"] == "netflix sub")
        assert top["months_seen"] == 2
        assert top["avg_amount"] == pytest.approx(99.0)
        assert top["cv"] <= 0.10


class TestGetTransferFeesSummary:
    def test_groups_fee_category(self, session) -> None:
        rid = _run_id()
        _insert_run(session, rid)
        session.add(
            _tx(
                rid,
                "f1",
                date="2026-01-08",
                amount_base=-5.0,
                category="Fees & Charges",
                merchant_normalized="bank fee a",
            )
        )
        session.add(
            _tx(
                rid,
                "f2",
                date="2026-01-09",
                amount_base=-15.0,
                category="Fees & Charges",
                merchant_normalized="bank fee b",
            )
        )
        session.commit()

        rows = DatabaseRepository().get_transfer_fees_summary(
            "2026-01-01", "2026-01-31", None
        )
        total = sum(float(r["total"]) for r in rows)
        assert total == pytest.approx(20.0)


class TestComputeAggregations:
    def test_returns_all_keys_and_calls_tools(self, session) -> None:
        rid = _run_id()
        _insert_run(session, rid)
        session.add(
            _tx(rid, "c1", date="2026-01-12", amount_base=-40.0, category="Groceries")
        )
        session.commit()

        state: dict[str, Any] = {
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "accounts": None,
            "force_recompute": True,
            "cache_valid": False,
            "goals_prompt": None,
            "aggregations": None,
            "habits": None,
            "suggestions": None,
        }
        result = compute_aggregations(state)
        agg = result["aggregations"]
        assert set(agg.keys()) == {
            "spending_by_category",
            "month_deltas",
            "recurring_charges",
            "transfer_fees_summary",
            "receipt_line_breakdown",
        }
        assert isinstance(agg["spending_by_category"], list)
        assert any(
            r.get("category") == "Groceries" for r in agg["spending_by_category"]
        )


class TestGetSpendingByCategoryTool:
    def test_invoke_matches_repository(self, session) -> None:
        rid = _run_id()
        _insert_run(session, rid)
        session.add(_tx(rid, "g1", date="2026-01-20", amount_base=-33.0, category="Transport"))
        session.commit()

        via_tool = get_spending_by_category.invoke(
            {
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
                "accounts": None,
            }
        )
        via_repo = DatabaseRepository().get_spending_by_category(
            "2026-01-01", "2026-01-31", None
        )
        assert via_tool == via_repo
