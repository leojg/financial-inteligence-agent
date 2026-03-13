"""Persistence service for normalized_document_cache, merchant_categories, duplicate_pairs, runs, and transactions."""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from shared.db import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ordered(a: str, b: str) -> tuple[str, str]:
    """Return (a, b) in consistent order so (a,b) and (b,a) map to the same row."""
    return (a, b) if a <= b else (b, a)


class DatabaseService:
    """Centralized persistence for the finance agent (cache, categories, duplicates, runs, transactions)."""

    def __init__(self) -> None:
        """Initialize with no dependencies."""
        pass

    @staticmethod
    def _rows_to_dicts(cursor) -> list[dict[str, Any]]:
        colnames = [d[0] for d in cursor.description]
        return [dict(zip(colnames, row)) for row in cursor.fetchall()]

    @staticmethod
    def _build_account_filter(accounts: list[str] | None) -> tuple[str, list[Any]]:
        if not accounts:
            return "", []
        placeholders = ",".join("?" * len(accounts))
        return f"AND account IN ({placeholders})", list(accounts)

    def get_cached_transactions(self, content_hash: str) -> list[dict[str, Any]] | None:
        """Return cached transactions JSON for this content_hash, or None on miss."""
        conn = get_connection()
        cur = conn.execute(
            "SELECT transactions_json FROM normalized_document_cache WHERE content_hash = ?",
            (content_hash,),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        return list(json.loads(str(row[0])))

    def save_normalized_document(
        self,
        content_hash: str,
        source_file: str,
        transactions_json: str,
    ) -> None:
        """Insert or replace a normalized document cache row."""
        conn = get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO normalized_document_cache
                (content_hash, source_file, transactions_json)
            VALUES (?, ?, ?)
            """,
            (content_hash, source_file, transactions_json),
        )
        conn.commit()

    @staticmethod
    def normalize_merchant(merchant: str) -> str:
        """Lowercase and strip punctuation from merchant name for cache lookups."""
        return re.sub(r"[^a-z0-9\s]", "", merchant.lower()).strip()

    def get_merchant_category(self, merchant_normalized: str) -> str | None:
        """Return cached category for a normalized merchant name, or None on miss."""
        conn = get_connection()
        cur = conn.execute(
            "SELECT category FROM merchant_categories WHERE merchant_normalized = ?",
            (merchant_normalized,),
        )
        row = cur.fetchone()
        cur.close()
        return str(row[0]) if row else None

    def upsert_merchant_category(
        self,
        merchant_normalized: str,
        category: str,
        source: str = "llm",
    ) -> None:
        """Insert or replace a merchant→category mapping."""
        conn = get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO merchant_categories
                (merchant_normalized, category, source, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (merchant_normalized, category, source, _now()),
        )
        conn.commit()

    # ── Duplicate pairs ──────────────────────────────────────────────────────

    @staticmethod
    def transaction_fingerprint(date: str, amount: float, currency: str, merchant: str) -> str:
        """Return a stable SHA-256 fingerprint for a transaction (identifies same real-world charge)."""
        key = f"{date}|{amount}|{currency}|{DatabaseService.normalize_merchant(merchant)}"
        return hashlib.sha256(key.encode()).hexdigest()

    def get_duplicate_pair(self, fp_a: str, fp_b: str) -> dict[str, Any] | None:
        """Return cached duplicate-pair result, or None on miss. Order-insensitive."""
        a, b = _ordered(fp_a, fp_b)
        conn = get_connection()
        cur = conn.execute(
            "SELECT is_duplicate, reason FROM duplicate_pairs WHERE fingerprint_a = ? AND fingerprint_b = ?",
            (a, b),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        return {"is_duplicate": bool(row[0]), "reason": (row[1] or "")}

    def upsert_duplicate_pair(
        self,
        fp_a: str,
        fp_b: str,
        is_duplicate: bool,
        reason: str = "",
    ) -> None:
        """Insert or replace a duplicate-pair result (order-insensitive)."""
        a, b = _ordered(fp_a, fp_b)
        conn = get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO duplicate_pairs
                (fingerprint_a, fingerprint_b, is_duplicate, reason)
            VALUES (?, ?, ?, ?)
            """,
            (a, b, int(is_duplicate), reason),
        )
        conn.commit()

    def insert_run(
        self,
        run_id: str,
        thread_id: str,
        source_paths: list[str],
        base_currency: str = "USD",
    ) -> None:
        """Insert a new run record with status 'running'."""
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO runs_history
                (run_id, thread_id, created_at, source_paths, status, base_currency)
            VALUES (?, ?, ?, ?, 'running', ?)
            """,
            (run_id, thread_id, _now(), json.dumps(source_paths), base_currency),
        )
        conn.commit()

    def update_run(
        self,
        run_id: str,
        *,
        status: str,
        total_transactions: int = 0,
        total_duplicates: int = 0,
        total_suspicious: int = 0,
    ) -> None:
        """Update run totals and status; set completed_at when status is 'complete'."""
        completed_at = _now() if status == "complete" else None
        conn = get_connection()
        conn.execute(
            """
            UPDATE runs_history
            SET status = ?,
                completed_at = COALESCE(?, completed_at),
                total_transactions = ?,
                total_duplicates = ?,
                total_suspicious = ?
            WHERE run_id = ?
            """,
            (status, completed_at, total_transactions, total_duplicates, total_suspicious, run_id),
        )
        conn.commit()

    def upsert_transactions(self, transactions: list[dict[str, Any]]) -> None:
        """Insert or replace fully processed transactions into the transactions table."""
        if not transactions:
            return
        conn = get_connection()
        conn.executemany(
            """
            INSERT OR REPLACE INTO transactions (
                id, run_id, fingerprint,
                date, amount_original, amount_base, currency,
                merchant, merchant_normalized, account, source_file,
                category, duplicate_of,
                suspicious, suspicious_reason,
                needs_review, review_reason, review_status,
                confidence
            ) VALUES (
                :id, :run_id, :fingerprint,
                :date, :amount_original, :amount_base, :currency,
                :merchant, :merchant_normalized, :account, :source_file,
                :category, :duplicate_of,
                :suspicious, :suspicious_reason,
                :needs_review, :review_reason, :review_status,
                :confidence
            )
            """,
            transactions,
        )
        conn.commit()

    def get_runs(self) -> list[dict[str, Any]]:
        """Return all runs ordered newest-first."""
        conn = get_connection()
        cur = conn.execute(
            """
            SELECT run_id, thread_id, created_at, completed_at, source_paths,
                   status, total_transactions, total_duplicates, total_suspicious, base_currency
            FROM runs_history
            ORDER BY created_at DESC
            """
        )
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    def get_run_transactions(self, run_id: str) -> list[dict[str, Any]]:
        """Return all transactions for a given run_id."""
        conn = get_connection()
        cur = conn.execute(
            "SELECT * FROM transactions WHERE run_id = ? ORDER BY date ASC",
            (run_id,),
        )
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    def get_distinct_filter_values(self) -> dict[str, list[str]]:
        """Return distinct accounts and categories across all stored transactions."""
        conn = get_connection()
        cur = conn.execute("SELECT DISTINCT account FROM transactions ORDER BY account")
        accounts = [row[0] for row in cur.fetchall() if row[0]]
        cur.close()
        cur = conn.execute(
            "SELECT DISTINCT category FROM transactions WHERE category IS NOT NULL ORDER BY category"
        )
        categories = [row[0] for row in cur.fetchall() if row[0]]
        cur.close()
        return {"accounts": accounts, "categories": categories}

    def query_transactions(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        accounts: list[str] | None = None,
        categories: list[str] | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        run_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return filtered transactions with DB-side WHERE clause. None = cross-run."""
        conditions: list[str] = []
        params: list[Any] = []

        if run_id is not None:
            conditions.append("run_id = ?")
            params.append(run_id)
        if date_from is not None:
            conditions.append("date >= ?")
            params.append(date_from)
        if date_to is not None:
            conditions.append("date <= ?")
            params.append(date_to)
        if accounts:
            placeholders = ",".join("?" * len(accounts))
            conditions.append(f"account IN ({placeholders})")
            params.extend(accounts)
        if categories:
            placeholders = ",".join("?" * len(categories))
            conditions.append(f"category IN ({placeholders})")
            params.extend(categories)
        if amount_min is not None:
            conditions.append("amount_original >= ?")
            params.append(amount_min)
        if amount_max is not None:
            conditions.append("amount_original <= ?")
            params.append(amount_max)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM transactions {where} ORDER BY date DESC LIMIT ?"
        params.append(limit)

        conn = get_connection()
        cur = conn.execute(sql, params)
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    # ── User goals ────────────────────────────────────────────────────────────

    def get_active_goals(self) -> list[dict[str, Any]]:
        """Return all active user goals."""
        conn = get_connection()
        cur = conn.execute(
            "SELECT id, content, created_at, updated_at FROM user_goals WHERE active = 1 ORDER BY created_at ASC"
        )
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    def upsert_goal(self, content: str, goal_id: int | None = None) -> int:
        """Insert a new goal (goal_id=None) or update an existing one. Returns the goal id."""
        conn = get_connection()
        now = _now()
        if goal_id is None:
            cur = conn.execute(
                "INSERT INTO user_goals (content, active, created_at, updated_at) VALUES (?, 1, ?, ?)",
                (content, now, now),
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]
        else:
            conn.execute(
                "UPDATE user_goals SET content = ?, updated_at = ? WHERE id = ?",
                (content, now, goal_id),
            )
            conn.commit()
            return goal_id

    def deactivate_goal(self, goal_id: int) -> None:
        """Mark a goal as inactive (soft-delete)."""
        conn = get_connection()
        conn.execute(
            "UPDATE user_goals SET active = 0, updated_at = ? WHERE id = ?",
            (_now(), goal_id),
        )
        conn.commit()

    # ── Insights queries ──────────────────────────────────────────────────────

    def get_spending_by_category(
        self,
        date_from: str,
        date_to: str,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Total absolute spend per category for expenses in the given date range."""
        account_clause, account_params = self._build_account_filter(accounts)
        sql = f"""
            SELECT category, SUM(ABS(amount_base)) AS total
            FROM transactions
            WHERE date >= ? AND date <= ?
              AND amount_base < 0
              AND duplicate_of IS NULL
              {account_clause}
            GROUP BY category
            ORDER BY total DESC
        """
        conn = get_connection()
        cur = conn.execute(sql, [date_from, date_to] + account_params)
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    def get_month_over_month_deltas(
        self,
        date_from: str,
        date_to: str,
        lookback_months: int = 3,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Monthly spend totals with delta_pct vs prior month and avg_baseline over lookback_months."""
        account_clause, account_params = self._build_account_filter(accounts)
        sql = f"""
            SELECT strftime('%Y-%m', date) AS month, SUM(ABS(amount_base)) AS total
            FROM transactions
            WHERE date >= ? AND date <= ?
              AND amount_base < 0
              AND duplicate_of IS NULL
              {account_clause}
            GROUP BY month
            ORDER BY month ASC
        """
        conn = get_connection()
        cur = conn.execute(sql, [date_from, date_to] + account_params)
        rows = self._rows_to_dicts(cur)
        cur.close()

        result = []
        for i, row in enumerate(rows):
            prior = rows[i - 1]["total"] if i > 0 else None
            delta_pct = ((row["total"] - prior) / prior * 100) if prior else None
            lookback = rows[max(0, i - lookback_months):i]
            avg_baseline = (
                sum(r["total"] for r in lookback) / len(lookback) if lookback else None
            )
            result.append({
                "month": row["month"],
                "total": row["total"],
                "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
                "avg_baseline": round(avg_baseline, 2) if avg_baseline is not None else None,
            })
        return result

    def get_recurring_charges(
        self,
        date_from: str,
        date_to: str,
        accounts: list[str] | None = None,
        min_months: int = 2,
        amount_tolerance_pct: float = 0.10,
    ) -> list[dict[str, Any]]:
        """Merchants with stable periodic charges (CV-based detection in Python)."""
        account_clause, account_params = self._build_account_filter(accounts)
        sql = f"""
            SELECT merchant_normalized, strftime('%Y-%m', date) AS month,
                   ABS(amount_base) AS amount
            FROM transactions
            WHERE date >= ? AND date <= ?
              AND amount_base < 0
              AND duplicate_of IS NULL
              {account_clause}
            ORDER BY merchant_normalized, month
        """
        conn = get_connection()
        cur = conn.execute(sql, [date_from, date_to] + account_params)
        rows = self._rows_to_dicts(cur)
        cur.close()

        by_merchant: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for row in rows:
            by_merchant[row["merchant_normalized"]][row["month"]].append(row["amount"])

        result = []
        for merchant, months_data in by_merchant.items():
            if len(months_data) < min_months:
                continue
            monthly_totals = [sum(v) for v in months_data.values()]
            mean = sum(monthly_totals) / len(monthly_totals)
            if mean == 0:
                continue
            cv = (statistics.stdev(monthly_totals) / mean) if len(monthly_totals) > 1 else 0.0
            if cv <= amount_tolerance_pct:
                result.append({
                    "merchant_normalized": merchant,
                    "months_seen": len(months_data),
                    "avg_amount": round(mean, 2),
                    "cv": round(cv, 4),
                })
        result.sort(key=lambda x: x["avg_amount"], reverse=True)
        return result

    def get_transfer_fees_summary(
        self,
        date_from: str,
        date_to: str,
        accounts: list[str] | None = None,
        fee_categories: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fee and commission summary by category."""
        if fee_categories is None:
            fee_categories = ["Fees & Charges"]
        account_clause, account_params = self._build_account_filter(accounts)
        placeholders = ",".join("?" * len(fee_categories))
        sql = f"""
            SELECT category, merchant_normalized,
                   COUNT(*) AS count, SUM(ABS(amount_base)) AS total
            FROM transactions
            WHERE date >= ? AND date <= ?
              AND category IN ({placeholders})
              AND duplicate_of IS NULL
              {account_clause}
            GROUP BY category, merchant_normalized
            ORDER BY total DESC
        """
        conn = get_connection()
        cur = conn.execute(sql, [date_from, date_to] + fee_categories + account_params)
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    def get_top_merchants_by_amount(
        self,
        date_from: str,
        date_to: str,
        category: str | None = None,
        limit: int = 10,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Top merchants by total spend."""
        account_clause, account_params = self._build_account_filter(accounts)
        category_clause = "AND category = ?" if category else ""
        category_params = [category] if category else []
        sql = f"""
            SELECT merchant_normalized, category,
                   COUNT(*) AS count, SUM(ABS(amount_base)) AS total
            FROM transactions
            WHERE date >= ? AND date <= ?
              AND amount_base < 0
              AND duplicate_of IS NULL
              {category_clause}
              {account_clause}
            GROUP BY merchant_normalized, category
            ORDER BY total DESC
            LIMIT ?
        """
        conn = get_connection()
        cur = conn.execute(sql, [date_from, date_to] + category_params + account_params + [limit])
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    def get_transactions_by_merchant(
        self,
        merchant_pattern: str,
        date_from: str | None = None,
        date_to: str | None = None,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return transactions matching a LIKE pattern on merchant_normalized."""
        account_clause, account_params = self._build_account_filter(accounts)
        date_clause = ""
        date_params: list[Any] = []
        if date_from:
            date_clause += " AND date >= ?"
            date_params.append(date_from)
        if date_to:
            date_clause += " AND date <= ?"
            date_params.append(date_to)
        sql = f"""
            SELECT * FROM transactions
            WHERE merchant_normalized LIKE ?
              AND duplicate_of IS NULL
              {date_clause}
              {account_clause}
            ORDER BY date DESC
        """
        conn = get_connection()
        cur = conn.execute(sql, [f"%{merchant_pattern}%"] + date_params + account_params)
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    def get_category_trend(
        self,
        category: str,
        date_from: str,
        date_to: str,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Monthly totals, count, and avg per transaction for a single category."""
        account_clause, account_params = self._build_account_filter(accounts)
        sql = f"""
            SELECT strftime('%Y-%m', date) AS month,
                   SUM(ABS(amount_base)) AS total,
                   COUNT(*) AS count,
                   AVG(ABS(amount_base)) AS avg_per_transaction
            FROM transactions
            WHERE category = ?
              AND date >= ? AND date <= ?
              AND duplicate_of IS NULL
              {account_clause}
            GROUP BY month
            ORDER BY month ASC
        """
        conn = get_connection()
        cur = conn.execute(sql, [category, date_from, date_to] + account_params)
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    def get_account_summary(
        self,
        date_from: str,
        date_to: str,
    ) -> list[dict[str, Any]]:
        """Per-account inflow, outflow, net, and transaction count."""
        sql = """
            SELECT account,
                   SUM(CASE WHEN amount_base > 0 THEN amount_base ELSE 0 END) AS inflow,
                   SUM(CASE WHEN amount_base < 0 THEN ABS(amount_base) ELSE 0 END) AS outflow,
                   SUM(amount_base) AS net,
                   COUNT(*) AS count
            FROM transactions
            WHERE date >= ? AND date <= ?
              AND duplicate_of IS NULL
            GROUP BY account
            ORDER BY account ASC
        """
        conn = get_connection()
        cur = conn.execute(sql, [date_from, date_to])
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    def get_largest_transactions(
        self,
        date_from: str,
        date_to: str,
        limit: int = 20,
        category: str | None = None,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Largest individual expense transactions in the given date range."""
        account_clause, account_params = self._build_account_filter(accounts)
        category_clause = "AND category = ?" if category else ""
        category_params = [category] if category else []
        sql = f"""
            SELECT * FROM transactions
            WHERE date >= ? AND date <= ?
              AND amount_base < 0
              AND duplicate_of IS NULL
              {category_clause}
              {account_clause}
            ORDER BY ABS(amount_base) DESC
            LIMIT ?
        """
        conn = get_connection()
        cur = conn.execute(sql, [date_from, date_to] + category_params + account_params + [limit])
        result = self._rows_to_dicts(cur)
        cur.close()
        return result
