"""Insights agent tools — thin @tool wrappers over DatabaseService query methods.

All SQL lives in DatabaseService. These functions exist solely to expose
DatabaseService methods to the LangChain tool interface.

The pipeline subgraph calls these as plain Python functions inside
compute_aggregations. The chat subgraph passes them to bind_tools() for
the ReAct loop. The @tool decorator is transparent in both contexts.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from langchain_core.tools import tool

from shared.services.database_service import DatabaseService

# ── Aggregation tools ─────────────────────────────────────────────────────────
# Called by the pipeline's compute_aggregations node.
# Also available to the chat agent for broad spending questions.

@tool
def get_spending_by_category(
    date_from: str,
    date_to: str,
    accounts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return total spending per category for the given period.

    Sums absolute spend per category, expenses only. Excludes duplicates.

    Args:
        date_from: Start date inclusive, format YYYY-MM-DD.
        date_to: End date inclusive, format YYYY-MM-DD.
        accounts: Optional list of account names to restrict the query.

    Returns:
        Dict mapping category name to total spend.
        Example: {"Restaurants": 430.50, "Groceries": 210.00}
    """
    return DatabaseService().get_spending_by_category(date_from, date_to, accounts)


@tool
def get_month_over_month_deltas(
    date_from: str,
    date_to: str,
    lookback_months: int = 3,
    accounts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return monthly spending with delta vs prior month and vs baseline average.

    Use this to detect upward or downward trends in overall spending.

    Args:
        date_from: Start date inclusive, format YYYY-MM-DD.
        date_to: End date inclusive, format YYYY-MM-DD.
        lookback_months: Months used to compute the rolling baseline average.
        accounts: Optional list of account names to restrict the query.

    Returns:
        List of monthly records: {month, total, delta_pct, avg_baseline}.
        delta_pct is None for the first month (no prior month to compare).
        avg_baseline is None when no lookback data exists.
    """
    rows = DatabaseService().get_monthly_spend(date_from, date_to, accounts)
    result = []
    for i, row in enumerate(rows):
        prior = rows[i - 1]["total"] if i > 0 else None
        delta_pct = ((row["total"] - prior) / prior * 100) if prior else None
        lookback = rows[max(0, i - lookback_months):i]
        avg_baseline = sum(r["total"] for r in lookback) / len(lookback) if lookback else None
        result.append({
            "month": row["month"],
            "total": row["total"],
            "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
            "avg_baseline": round(avg_baseline, 2) if avg_baseline is not None else None,
        })
    return result


@tool
def get_recurring_charges(
    date_from: str,
    date_to: str,
    accounts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Detect merchants with stable periodic charges across multiple months.

    A merchant qualifies as recurring when it appears in 2+ distinct months
    with a stable amount (low coefficient of variation). Useful for identifying
    subscriptions, utilities, and regular fees.

    Args:
        date_from: Start date inclusive, format YYYY-MM-DD.
        date_to: End date inclusive, format YYYY-MM-DD.
        accounts: Optional list of account names to restrict the query.

    Returns:
        List of {merchant_normalized, months_seen, avg_amount, cv},
        sorted by avg_amount descending.
    """
    rows = DatabaseService().get_merchant_monthly_spend(date_from, date_to, accounts)
    by_merchant: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_merchant[row["merchant_normalized"]].append(row["total"])
    result: list[dict[str, Any]] = []
    for merchant, monthly_totals in by_merchant.items():
        if len(monthly_totals) < 2:
            continue
        mean = sum(monthly_totals) / len(monthly_totals)
        if mean == 0:
            continue
        cv = statistics.stdev(monthly_totals) / mean if len(monthly_totals) > 1 else 0.0
        if cv <= 0.10:
            result.append({
                "merchant_normalized": merchant,
                "months_seen": len(monthly_totals),
                "avg_amount": round(mean, 2),
                "cv": round(cv, 4),
            })
    result.sort(key=lambda x: float(x["avg_amount"]), reverse=True)
    return result


@tool
def get_transfer_fees_summary(
    date_from: str,
    date_to: str,
    accounts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Summarize transfer fees and commissions paid during the period.

    Queries transactions in fee-related categories. Returns an overview plus
    individual transactions so spending patterns can be analyzed
    (e.g. many small transfers that could be batched to reduce total fees).

    Args:
        date_from: Start date inclusive, format YYYY-MM-DD.
        date_to: End date inclusive, format YYYY-MM-DD.
        accounts: Optional list of account names to restrict the query.

    Returns:
        Dict with keys: count, total, avg_per_transfer, transactions.
        transactions: list of {date, merchant, amount_base, account, category}.
    """
    return DatabaseService().get_transfer_fees_summary(date_from, date_to, accounts)

# ── Narrow tools ──────────────────────────────────────────────────────────────
# Used exclusively by the chat agent for drill-down on specific questions.

@tool
def get_top_merchants_by_amount(
    date_from: str,
    date_to: str,
    category: str | None = None,
    limit: int = 10,
    accounts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return merchants ranked by total spend. Optional category and account filters.

    Use this to identify the highest-spend merchants overall or within a
    specific category. Useful for spotting unexpectedly high charges.

    Args:
        date_from: Start date inclusive, format YYYY-MM-DD.
        date_to: End date inclusive, format YYYY-MM-DD.
        category: Optional exact category name to filter by.
        limit: Maximum number of merchants to return.
        accounts: Optional list of account names to restrict the query.

    Returns:
        List of {merchant, category, total, transaction_count}, sorted by total descending.
    """
    return DatabaseService().get_top_merchants_by_amount(
        date_from, date_to, category, limit, accounts
    )


@tool
def get_transactions_by_merchant(
    merchant_pattern: str,
    date_from: str,
    date_to: str,
    accounts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return all transactions matching a merchant name pattern.

    Use this to investigate a specific merchant — see all charges, their
    dates, amounts, and accounts. Matches on partial merchant name.

    Args:
        merchant_pattern: Partial merchant name to search (case-insensitive).
        date_from: Start date inclusive, format YYYY-MM-DD.
        date_to: End date inclusive, format YYYY-MM-DD.
        accounts: Optional list of account names to restrict the query.

    Returns:
        List of {date, merchant, amount_base, account, category}, sorted by date ascending.
    """
    return DatabaseService().get_transactions_by_merchant(
        merchant_pattern, date_from, date_to, accounts
    )


@tool
def get_category_trend(
    category: str,
    date_from: str,
    date_to: str,
    accounts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return monthly spending detail for a single category.

    More granular than get_month_over_month_deltas — covers one category with
    transaction count and average per transaction. Use this when the user asks
    to drill into a specific category.

    Args:
        category: Exact category name to query.
        date_from: Start date inclusive, format YYYY-MM-DD.
        date_to: End date inclusive, format YYYY-MM-DD.
        accounts: Optional list of account names to restrict the query.

    Returns:
        List of {month, total, transaction_count, avg_per_transaction}, sorted by month ascending.
    """
    return DatabaseService().get_category_trend(category, date_from, date_to, accounts)


@tool
def get_account_summary(
    date_from: str,
    date_to: str,
) -> list[dict[str, Any]]:
    """Return a per-account financial summary showing inflows, outflows, and net position.

    Use this when the user asks about a specific account's balance or overall
    cash flow across accounts.

    Args:
        date_from: Start date inclusive, format YYYY-MM-DD.
        date_to: End date inclusive, format YYYY-MM-DD.

    Returns:
        List of {account, total_in, total_out, net, transaction_count}, sorted by net ascending.
    """
    return DatabaseService().get_account_summary(date_from, date_to)


@tool
def get_largest_transactions(
    date_from: str,
    date_to: str,
    limit: int = 10,
    category: str | None = None,
    accounts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the largest individual expense transactions by absolute amount.

    Use this to spot unusually high one-off charges. Optional category and
    account filters allow scoping to a specific area of interest.

    Args:
        date_from: Start date inclusive, format YYYY-MM-DD.
        date_to: End date inclusive, format YYYY-MM-DD.
        limit: Maximum number of transactions to return.
        category: Optional exact category name to filter by.
        accounts: Optional list of account names to restrict the query.

    Returns:
        List of {date, merchant, amount_base, account, category}, sorted by amount descending.
    """
    return DatabaseService().get_largest_transactions(
        date_from, date_to, limit, category, accounts
    )