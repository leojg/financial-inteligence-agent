"""Persistence service for normalized_document_cache, merchant_categories, duplicate_pairs, runs, transactions, user_goals, and insights_cache."""

from __future__ import annotations

import hashlib
import json
import re
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

    def upsert_goal(self, content: str, goal_id: int | None = None) -> None:
        """Insert a new goal (goal_id=None) or update an existing one."""
        conn = get_connection()
        now = _now()
        if goal_id is None:
            conn.execute(
                "INSERT INTO user_goals (content, active, created_at, updated_at) VALUES (?, 1, ?, ?)",
                (content, now, now),
            )
        else:
            conn.execute(
                "UPDATE user_goals SET content = ?, updated_at = ? WHERE id = ?",
                (content, now, goal_id),
            )
        conn.commit()

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

    def get_monthly_spend(
        self,
        date_from: str,
        date_to: str,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return monthly expense totals. Each row: {month, total}."""
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
        result = self._rows_to_dicts(cur)
        cur.close()
        return result

    def get_merchant_monthly_spend(
        self,
        date_from: str,
        date_to: str,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return per-merchant per-month expense totals. Each row: {merchant_normalized, month, total}."""
        account_clause, account_params = self._build_account_filter(accounts)
        sql = f"""
            SELECT merchant_normalized,
                   strftime('%Y-%m', date) AS month,
                   SUM(ABS(amount_base)) AS total
            FROM transactions
            WHERE date >= ? AND date <= ?
              AND amount_base < 0
              AND duplicate_of IS NULL
              {account_clause}
            GROUP BY merchant_normalized, month
            ORDER BY merchant_normalized, month
        """
        conn = get_connection()
        cur = conn.execute(sql, [date_from, date_to] + account_params)
        result = self._rows_to_dicts(cur)
        cur.close()
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

    def get_transaction_date_range(
        self,
        accounts: list[str] | None = None,
    ) -> tuple[str, str] | None:
        """Return (min_date, max_date) from the transactions table, or None if empty."""
        account_clause, account_params = self._build_account_filter(accounts)
        sql = f"""
            SELECT MIN(date) AS min_date, MAX(date) AS max_date
            FROM transactions
            WHERE 1=1
            {account_clause}
        """
        conn = get_connection()
        cur = conn.execute(sql, account_params)
        row = cur.fetchone()
        cur.close()
        if row is None or row[0] is None or row[1] is None:
            return None
        return str(row[0]), str(row[1])

    @staticmethod
    def _accounts_hash(accounts: list[str] | None) -> str:
        """Stable hash for account list for cache keys (sorted for consistency)."""
        key = ",".join(sorted(accounts or []))
        return hashlib.sha256(key.encode()).hexdigest()

    def get_insights_cache(
        self,
        date_from: str,
        date_to: str,
        accounts: list[str] | None,
    ) -> dict[str, Any] | None:
        """Return cached insights for (date_from, date_to, accounts), or None on miss."""
        conn = get_connection()
        h = self._accounts_hash(accounts)
        cur = conn.execute(
            """
            SELECT aggregations_json, habits_json, suggestions_json, created_at
            FROM insights_cache
            WHERE date_from = ? AND date_to = ? AND accounts_hash = ?
            """,
            (date_from, date_to, h),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        return {
            "aggregations": json.loads(row[0]),
            "habits": json.loads(row[1]),
            "suggestions": json.loads(row[2]),
            "created_at": str(row[3]),
        }

    def get_latest_insights_cache(self) -> dict[str, Any] | None:
        """Return the most recent cached insights (by created_at), or None if the cache is empty."""
        conn = get_connection()
        cur = conn.execute(
            """
            SELECT date_from, date_to, aggregations_json, habits_json, suggestions_json
            FROM insights_cache
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        return {
            "date_from": str(row[0]),
            "date_to": str(row[1]),
            "aggregations": json.loads(row[2]),
            "habits": json.loads(row[3]),
            "suggestions": json.loads(row[4]),
        }

    def save_insights_cache(
        self,
        date_from: str,
        date_to: str,
        accounts: list[str] | None,
        aggregations: dict[str, Any],
        habits: list[Any],
        suggestions: list[Any],
    ) -> None:
        """Store or replace cached insights for (date_from, date_to, accounts)."""
        conn = get_connection()
        h = self._accounts_hash(accounts)
        now = _now()
        conn.execute(
            """
            INSERT INTO insights_cache
                (date_from, date_to, accounts_hash, aggregations_json, habits_json, suggestions_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date_from, date_to, accounts_hash) DO UPDATE SET
                aggregations_json = excluded.aggregations_json,
                habits_json = excluded.habits_json,
                suggestions_json = excluded.suggestions_json,
                created_at = excluded.created_at
            """,
            (
                date_from,
                date_to,
                h,
                json.dumps(aggregations),
                json.dumps(habits),
                json.dumps(suggestions),
                now,
            ),
        )
        conn.commit()

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

    def get_latest_reconciliation_run_date(self) -> str | None:
        """Return MAX(completed_at) from runs_history where status = 'complete', or None."""
        sql = """
            SELECT MAX(completed_at) AS latest_completed
            FROM runs_history
            WHERE status = 'complete'
        """
        conn = get_connection()
        cur = conn.execute(sql)
        row = cur.fetchone()
        cur.close()
        return str(row[0]) if row and row[0] is not None else None

    # ── Receipts ─────────────────────────────────────────────────────────────

    def upsert_receipts(self, receipts: list[dict[str, Any]]) -> None:
        """Insert or replace receipt rows. Sets created_at automatically."""
        if not receipts:
            return
        now = _now()
        for r in receipts:
            r.setdefault("created_at", now)
        conn = get_connection()
        conn.executemany(
            """
            INSERT OR REPLACE INTO receipts (
                id, run_id, transaction_id, source_file,
                merchant, merchant_normalized,
                date, currency,
                subtotal, tax_amount, tax_rate, total,
                receipt_number, confidence, raw_content, created_at
            ) VALUES (
                :id, :run_id, :transaction_id, :source_file,
                :merchant, :merchant_normalized,
                :date, :currency,
                :subtotal, :tax_amount, :tax_rate, :total,
                :receipt_number, :confidence, :raw_content, :created_at
            )
            """,
            receipts,
        )
        conn.commit()

    def upsert_receipt_lines(self, lines: list[dict[str, Any]]) -> None:
        """Insert or replace receipt line items."""
        if not lines:
            return
        conn = get_connection()
        conn.executemany(
            """
            INSERT OR REPLACE INTO receipt_lines (
                id, receipt_id, line_number, description,
                quantity, unit_price, amount, category
            ) VALUES (
                :id, :receipt_id, :line_number, :description,
                :quantity, :unit_price, :amount, :category
            )
            """,
            lines,
        )
        conn.commit()

    def auto_link_receipts(self, run_id: str) -> int:
        """Link unlinked receipts to transactions within the same run.

        Match on: date + abs(total) == abs(amount_original) + currency + merchant_normalized.
        Only attempts linking when receipt has non-null date and currency.
        Returns the number of receipts linked.
        """
        conn = get_connection()
        cur = conn.execute(
            """
            UPDATE receipts
            SET transaction_id = (
                SELECT t.id
                FROM transactions t
                WHERE t.run_id = receipts.run_id
                AND t.date = receipts.date
                AND abs(t.amount_original) = abs(receipts.total)
                AND t.currency = receipts.currency
                AND t.merchant_normalized = receipts.merchant_normalized
                LIMIT 1
            )
            WHERE receipts.run_id = ?
            AND receipts.transaction_id IS NULL
            AND receipts.date IS NOT NULL
            AND receipts.currency IS NOT NULL
            """,
            (run_id,),
        )
        linked = cur.rowcount
        conn.commit()
        return linked

    def get_receipt_line_breakdown(
        self,
        date_from: str,
        date_to: str,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate receipt line spending by category and description.

        Joins receipt_lines → receipts → transactions to get the transaction's
        category, then groups by (category, line description).
        Only includes receipts linked to a transaction.
        """
        account_clause, account_params = self._build_account_filter(accounts)
        sql = f"""
            SELECT t.category,
                rl.description,
                COUNT(*) AS occurrences,
                SUM(ABS(rl.amount)) AS total,
                AVG(ABS(rl.amount)) AS avg_amount
            FROM receipt_lines rl
            JOIN receipts r ON rl.receipt_id = r.id
            JOIN transactions t ON r.transaction_id = t.id
            WHERE t.date >= ? AND t.date <= ?
            AND t.duplicate_of IS NULL
            AND r.transaction_id IS NOT NULL
            {account_clause}
            GROUP BY t.category, rl.description
            ORDER BY t.category, total DESC
        """
        conn = get_connection()
        cur = conn.execute(sql, [date_from, date_to] + account_params)
        result = self._rows_to_dicts(cur)
        cur.close()
        return result