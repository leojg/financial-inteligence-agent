"""LLM `generate_insights` eval: coverage + numeric grounding (v1.4 §3.2). Chat eval excluded."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from tabulate import tabulate

from agents.insights.configuration import DEFAULT_CONFIG
from agents.insights.nodes import make_generate_insights_node

# Tune after baseline runs on the production insights model.
COVERAGE_RATE_THRESHOLD = 0.55
NUMERIC_GROUNDING_RATE_THRESHOLD = 0.52


def _item_text(item: dict[str, Any]) -> str:
    if "observation" in item:
        return f'{item.get("category", "")} {item["observation"]}'
    return f'{item.get("title", "")} {item.get("body", "")}'


def _field_items(field: str, habits: list[dict], suggestions: list[dict]) -> list[dict]:
    return habits if field == "habits" else suggestions


def _coverage_hits(
    expected_coverage: list[dict[str, str]],
    habits: list[dict],
    suggestions: list[dict],
) -> tuple[int, int, list[tuple[str, bool]]]:
    hits = 0
    details: list[tuple[str, bool]] = []
    for rule in expected_coverage:
        field = rule["field"]
        must = rule["must_reference"].lower()
        items = _field_items(field, habits, suggestions)
        ok = any(must in _item_text(x).lower() for x in items)
        if ok:
            hits += 1
        details.append((f'{field}:{rule["must_reference"]}', ok))
    n = len(expected_coverage)
    return hits, n, details


def _assert_severity_floor(severity_floor: dict[str, str], habits: list[dict], pid: str) -> None:
    order = {"info": 0, "warning": 1, "critical": 2}
    for cat_key, min_sev in severity_floor.items():
        min_v = order[min_sev]
        cat_l = cat_key.lower()
        matched = False
        for h in habits:
            if cat_l in (h.get("category") or "").lower():
                matched = True
                got = order.get(h.get("severity") or "info", 0)
                assert got >= min_v, (
                    f"[{pid}] expected severity>={min_sev} for habit category "
                    f"matching {cat_key!r}, got {h.get('severity')!r}"
                )
                break
        assert matched, f"[{pid}] no habit with category matching {cat_key!r}"


def _numeric_grounding_score(
    aggregations: dict[str, Any],
    habits: list[dict],
    suggestions: list[dict],
) -> tuple[int, int]:
    """Fraction of substantive numbers in habit/suggestion text that appear in the aggregation JSON."""
    blob_in = json.dumps(aggregations, ensure_ascii=False)
    texts = [_item_text(x) for x in habits + suggestions]
    num_re = re.compile(r"-?\d[\d,]*\.?\d*")
    checked = 0
    grounded = 0
    for text in texts:
        for m in num_re.finditer(text):
            raw = m.group(0).replace(",", "")
            try:
                val = float(raw)
            except ValueError:
                continue
            if abs(val) < 10 and val == int(val) and 1990 <= int(val) <= 2100:
                continue
            if abs(val) < 2 and "." not in raw:
                continue
            checked += 1
            if m.group(0) in blob_in or raw in blob_in:
                grounded += 1
    return grounded, max(checked, 1)


# Hand-crafted aggregation snapshots (no DB). Shape matches compute_aggregations output keys.
INSIGHTS_PROFILES: list[dict[str, Any]] = [
    {
        "id": "overspending",
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
        "goals_prompt": (
            "The user has defined the following financial goals.\n"
            "Take them into consideration when making observations or flagging habits:\n"
            "- Reduce restaurant spending\n"
            "- Save 20% of monthly income"
        ),
        "aggregations": {
            "spending_by_category": [
                {"category": "Dining", "total": 4800.0, "share_of_expenses_pct": 48.0},
                {"category": "Groceries", "total": 2100.0, "share_of_expenses_pct": 21.0},
                {"category": "Transportation", "total": 900.0, "share_of_expenses_pct": 9.0},
                {"category": "Entertainment", "total": 800.0, "share_of_expenses_pct": 8.0},
                {"category": "Utilities", "total": 700.0, "share_of_expenses_pct": 7.0},
            ],
            "month_deltas": [
                {
                    "month": "2026-01",
                    "total": 10000.0,
                    "delta_pct": 63.0,
                    "avg_baseline": 6130.0,
                },
            ],
            "recurring_charges": [
                {
                    "merchant_normalized": "SUBSCRIPTION SVCS",
                    "months_seen": 3,
                    "avg_amount": 99.0,
                    "cv": 0.02,
                }
                for _ in range(6)
            ],
            "transfer_fees_summary": {
                "count": 2,
                "total": 12.5,
                "avg_per_transfer": 6.25,
                "transactions": [],
            },
            "receipt_line_breakdown": [],
        },
        "expected_coverage": [
            {
                "field": "habits",
                "must_reference": "Dining",
                "reason": "Dining dominates spending share",
            },
            {
                "field": "habits",
                "must_reference": "63",
                "reason": "MoM delta percent in aggregation JSON",
            },
            {
                "field": "suggestions",
                "must_reference": "Dining",
                "reason": "Goal targets restaurant spending",
            },
        ],
        "severity_floor": {"Dining": "warning"},
    },
    {
        "id": "healthy",
        "date_from": "2026-02-01",
        "date_to": "2026-02-28",
        "goals_prompt": (
            "The user has defined the following financial goals.\n"
            "- Save 20% of monthly income"
        ),
        "aggregations": {
            "spending_by_category": [
                {"category": "Groceries", "total": 1200.0, "share_of_expenses_pct": 24.0},
                {"category": "Dining", "total": 1100.0, "share_of_expenses_pct": 22.0},
                {"category": "Transportation", "total": 1000.0, "share_of_expenses_pct": 20.0},
                {"category": "Utilities", "total": 900.0, "share_of_expenses_pct": 18.0},
                {"category": "Healthcare", "total": 800.0, "share_of_expenses_pct": 16.0},
            ],
            "month_deltas": [
                {
                    "month": "2026-02",
                    "total": 5000.0,
                    "delta_pct": -4.0,
                    "avg_baseline": 5200.0,
                },
            ],
            "recurring_charges": [],
            "transfer_fees_summary": {"count": 0, "total": 0.0, "transactions": []},
            "receipt_line_breakdown": [],
        },
        "expected_coverage": [
            {
                "field": "suggestions",
                "must_reference": "income",
                "reason": "Savings goal references income in prompt",
            },
        ],
        "severity_floor": {},
    },
    {
        "id": "no_goals_dramatic_shift",
        "date_from": "2026-03-01",
        "date_to": "2026-03-31",
        "goals_prompt": None,
        "aggregations": {
            "spending_by_category": [
                {"category": "Shopping", "total": 3900.0, "share_of_expenses_pct": 39.0},
                {"category": "Groceries", "total": 2000.0, "share_of_expenses_pct": 20.0},
                {"category": "Dining", "total": 200.0, "share_of_expenses_pct": 2.0},
            ],
            "month_deltas": [
                {
                    "month": "2026-03",
                    "total": 6100.0,
                    "delta_pct": 32.0,
                    "avg_baseline": 4620.0,
                },
            ],
            "recurring_charges": [],
            "transfer_fees_summary": {"count": 0, "total": 0.0, "transactions": []},
            "receipt_line_breakdown": [],
        },
        "expected_coverage": [
            {
                "field": "habits",
                "must_reference": "Shopping",
                "reason": "Category drove MoM change",
            },
        ],
        "severity_floor": {},
    },
]


@pytest.mark.eval
def test_insights_eval_profiles():
    """Layer 1: keyword coverage + severity floors; layer 2: numbers trace to aggregation JSON."""
    generate_insights = make_generate_insights_node(DEFAULT_CONFIG)
    sev_ok = frozenset({"info", "warning", "critical"})
    rows = []
    coverage_rates: list[float] = []
    grounding_rates: list[float] = []

    for profile in INSIGHTS_PROFILES:
        pid = profile["id"]
        state: dict[str, Any] = {
            "date_from": profile["date_from"],
            "date_to": profile["date_to"],
            "goals_prompt": profile.get("goals_prompt"),
            "aggregations": profile["aggregations"],
        }
        out = generate_insights(state)
        habits = out.get("habits") or []
        suggestions = out.get("suggestions") or []

        assert habits or suggestions, f"[{pid}] expected non-empty habits or suggestions"
        for h in habits:
            assert h.get("severity") in sev_ok
        for s in suggestions:
            assert s.get("severity") in sev_ok

        exp = profile.get("expected_coverage") or []
        hits, n_cov, _ = _coverage_hits(exp, habits, suggestions)
        cov_rate = hits / n_cov if n_cov else 1.0
        coverage_rates.append(cov_rate)

        sf = profile.get("severity_floor") or {}
        if sf:
            _assert_severity_floor(sf, habits, pid)

        g, c = _numeric_grounding_score(profile["aggregations"], habits, suggestions)
        gr = g / c if c else 1.0
        grounding_rates.append(gr)

        rows.append([pid, f"{cov_rate:.0%}", f"{gr:.0%}", len(habits), len(suggestions)])

    avg_cov = sum(coverage_rates) / len(coverage_rates) if coverage_rates else 1.0
    avg_ground = sum(grounding_rates) / len(grounding_rates) if grounding_rates else 1.0

    print("\n" + tabulate(rows, headers=["profile", "coverage", "numeric_ground", "habits", "sug"], tablefmt="github"))
    print(f"\nMean coverage rate: {avg_cov:.1%}  Mean numeric grounding: {avg_ground:.1%}")

    assert avg_cov >= COVERAGE_RATE_THRESHOLD, "Insights keyword coverage below threshold"
    assert avg_ground >= NUMERIC_GROUNDING_RATE_THRESHOLD, (
        "Insights numeric grounding below threshold (possible hallucinated figures)"
    )
