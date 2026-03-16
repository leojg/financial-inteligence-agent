"""Persistence service for all app tables via SQLAlchemy Core."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from shared.db import get_session


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
    def _rows(result: Any) -> list[dict[str, Any]]:
        """Convert a SQLAlchemy CursorResult to a list of dicts."""
        return [dict(r._mapping) for r in result]

    @staticmethod
    def _build_account_filter(accounts: list[str] | None) -> tuple[str, dict[str, Any]]:
        """Return (sql_clause, named_params) for an IN filter on the account column."""
        if not accounts:
            return "", {}
        placeholders = ", ".join(f":account_{i}" for i in range(len(accounts)))
        params = {f"account_{i}": acc for i, acc in enumerate(accounts)}
        return f"AND account IN ({placeholders})", params

    def get_cached_transactions(self, content_hash: str) -> list[dict[str, Any]] | None:
        """Return cached transactions JSON for this content_hash, or None on miss."""
        with get_session() as session:
            result = session.execute(
                text("SELECT transactions_json FROM normalized_document_cache WHERE content_hash = :h"),
                {"h": content_hash},
            )
            row = result.fetchone()
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
        with get_session() as session:
            session.execute(
                text("""
                    INSERT INTO normalized_document_cache
                        (content_hash, source_file, transactions_json)
                    VALUES (:content_hash, :source_file, :transactions_json)
                    ON CONFLICT (content_hash) DO UPDATE SET
                        source_file = :source_file,
                        transactions_json = :transactions_json
                """),
                {"content_hash": content_hash, "source_file": source_file, "transactions_json": transactions_json},
            )
            session.commit()

    @staticmethod
    def normalize_merchant(merchant: str) -> str:
        """Lowercase and strip punctuation from merchant name for cache lookups."""
        return re.sub(r"[^a-z0-9\s]", "", merchant.lower()).strip()

    def get_merchant_category(self, merchant_normalized: str) -> str | None:
        """Return cached category for a normalized merchant name, or None on miss."""
        with get_session() as session:
            result = session.execute(
                text("SELECT category FROM merchant_categories WHERE merchant_normalized = :m"),
                {"m": merchant_normalized},
            )
            row = result.fetchone()
        return str(row[0]) if row else None

    def upsert_merchant_category(
        self,
        merchant_normalized: str,
        category: str,
        source: str = "llm",
    ) -> None:
        """Insert or replace a merchant→category mapping."""
        with get_session() as session:
            session.execute(
                text("""
                    INSERT INTO merchant_categories
                        (merchant_normalized, category, source, updated_at)
                    VALUES (:merchant_normalized, :category, :source, :updated_at)
                    ON CONFLICT (merchant_normalized) DO UPDATE SET
                        category = :category,
                        source = :source,
                        updated_at = :updated_at
                """),
                {"merchant_normalized": merchant_normalized, "category": category, "source": source, "updated_at": _now()},
            )
            session.commit()

    # ── Duplicate pairs ──────────────────────────────────────────────────────

    @staticmethod
    def transaction_fingerprint(date: str, amount: float, currency: str, merchant: str) -> str:
        """Return a stable SHA-256 fingerprint for a transaction (identifies same real-world charge)."""
        key = f"{date}|{amount}|{currency}|{DatabaseService.normalize_merchant(merchant)}"
        return hashlib.sha256(key.encode()).hexdigest()

    def get_duplicate_pair(self, fp_a: str, fp_b: str) -> dict[str, Any] | None:
        """Return cached duplicate-pair result, or None on miss. Order-insensitive."""
        a, b = _ordered(fp_a, fp_b)
        with get_session() as session:
            result = session.execute(
                text("SELECT is_duplicate, reason FROM duplicate_pairs WHERE fingerprint_a = :a AND fingerprint_b = :b"),
                {"a": a, "b": b},
            )
            row = result.fetchone()
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
        with get_session() as session:
            session.execute(
                text("""
                    INSERT INTO duplicate_pairs
                        (fingerprint_a, fingerprint_b, is_duplicate, reason)
                    VALUES (:a, :b, :is_duplicate, :reason)
                    ON CONFLICT (fingerprint_a, fingerprint_b) DO UPDATE SET
                        is_duplicate = :is_duplicate,
                        reason = :reason
                """),
                {"a": a, "b": b, "is_duplicate": int(is_duplicate), "reason": reason},
            )
            session.commit()

    def insert_run(
        self,
        run_id: str,
        thread_id: str,
        source_paths: list[str],
        base_currency: str = "USD",
    ) -> None:
        """Insert a new run record with status 'running'."""
        with get_session() as session:
            session.execute(
                text("""
                    INSERT INTO runs_history
                        (run_id, thread_id, created_at, source_paths, status, base_currency)
                    VALUES (:run_id, :thread_id, :created_at, :source_paths, 'running', :base_currency)
                """),
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "created_at": _now(),
                    "source_paths": json.dumps(source_paths),
                    "base_currency": base_currency,
                },
            )
            session.commit()

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
        with get_session() as session:
            session.execute(
                text("""
                    UPDATE runs_history
                    SET status = :status,
                        completed_at = COALESCE(:completed_at, completed_at),
                        total_transactions = :total_transactions,
                        total_duplicates = :total_duplicates,
                        total_suspicious = :total_suspicious
                    WHERE run_id = :run_id
                """),
                {
                    "status": status,
                    "completed_at": completed_at,
                    "total_transactions": total_transactions,
                    "total_duplicates": total_duplicates,
                    "total_suspicious": total_suspicious,
                    "run_id": run_id,
                },
            )
            session.commit()

    def upsert_transactions(self, transactions: list[dict[str, Any]]) -> None:
        """Insert or replace fully processed transactions into the transactions table."""
        if not transactions:
            return
        with get_session() as session:
            session.execute(
                text("""
                    INSERT INTO transactions (
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
                    ON CONFLICT (id) DO UPDATE SET
                        run_id = :run_id, fingerprint = :fingerprint,
                        date = :date, amount_original = :amount_original, amount_base = :amount_base, currency = :currency,
                        merchant = :merchant, merchant_normalized = :merchant_normalized, account = :account, source_file = :source_file,
                        category = :category, duplicate_of = :duplicate_of,
                        suspicious = :suspicious, suspicious_reason = :suspicious_reason,
                        needs_review = :needs_review, review_reason = :review_reason, review_status = :review_status,
                        confidence = :confidence
                """),
                transactions,
            )
            session.commit()

    def get_runs(self) -> list[dict[str, Any]]:
        """Return all runs ordered newest-first."""
        with get_session() as session:
            result = session.execute(text("""
                SELECT run_id, thread_id, created_at, completed_at, source_paths,
                       status, total_transactions, total_duplicates, total_suspicious, base_currency
                FROM runs_history
                ORDER BY created_at DESC
            """))
            return self._rows(result)

    def get_run_transactions(self, run_id: str) -> list[dict[str, Any]]:
        """Return all transactions for a given run_id."""
        with get_session() as session:
            result = session.execute(
                text("SELECT * FROM transactions WHERE run_id = :run_id ORDER BY date ASC"),
                {"run_id": run_id},
            )
            return self._rows(result)

    def get_distinct_filter_values(self) -> dict[str, list[str]]:
        """Return distinct accounts and categories across all stored transactions."""
        with get_session() as session:
            accounts = [
                str(r[0]) for r in session.execute(
                    text("SELECT DISTINCT account FROM transactions ORDER BY account")
                ).fetchall() if r[0]
            ]
            categories = [
                str(r[0]) for r in session.execute(
                    text("SELECT DISTINCT category FROM transactions WHERE category IS NOT NULL ORDER BY category")
                ).fetchall() if r[0]
            ]
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
        params: dict[str, Any] = {"limit": limit}

        if run_id is not None:
            conditions.append("run_id = :run_id")
            params["run_id"] = run_id
        if date_from is not None:
            conditions.append("date >= :date_from")
            params["date_from"] = date_from
        if date_to is not None:
            conditions.append("date <= :date_to")
            params["date_to"] = date_to
        if accounts:
            phs = ", ".join(f":account_{i}" for i in range(len(accounts)))
            conditions.append(f"account IN ({phs})")
            params.update({f"account_{i}": acc for i, acc in enumerate(accounts)})
        if categories:
            phs = ", ".join(f":category_{i}" for i in range(len(categories)))
            conditions.append(f"category IN ({phs})")
            params.update({f"category_{i}": cat for i, cat in enumerate(categories)})
        if amount_min is not None:
            conditions.append("amount_original >= :amount_min")
            params["amount_min"] = amount_min
        if amount_max is not None:
            conditions.append("amount_original <= :amount_max")
            params["amount_max"] = amount_max

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with get_session() as session:
            result = session.execute(
                text(f"SELECT * FROM transactions {where} ORDER BY date DESC LIMIT :limit"),
                params,
            )
            return self._rows(result)

    # ── User goals ────────────────────────────────────────────────────────────

    def get_active_goals(self) -> list[dict[str, Any]]:
        """Return all active user goals."""
        with get_session() as session:
            result = session.execute(text(
                "SELECT id, content, created_at, updated_at FROM user_goals WHERE active = 1 ORDER BY created_at ASC"
            ))
            return self._rows(result)

    def upsert_goal(self, content: str, goal_id: int | None = None) -> None:
        """Insert a new goal (goal_id=None) or update an existing one."""
        now = _now()
        with get_session() as session:
            if goal_id is None:
                session.execute(
                    text("INSERT INTO user_goals (content, active, created_at, updated_at) VALUES (:content, 1, :now, :now)"),
                    {"content": content, "now": now},
                )
            else:
                session.execute(
                    text("UPDATE user_goals SET content = :content, updated_at = :now WHERE id = :goal_id"),
                    {"content": content, "now": now, "goal_id": goal_id},
                )
            session.commit()

    def deactivate_goal(self, goal_id: int) -> None:
        """Mark a goal as inactive (soft-delete)."""
        with get_session() as session:
            session.execute(
                text("UPDATE user_goals SET active = 0, updated_at = :now WHERE id = :goal_id"),
                {"now": _now(), "goal_id": goal_id},
            )
            session.commit()

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
            WHERE date >= :date_from AND date <= :date_to
              AND amount_base < 0
              AND duplicate_of IS NULL
              {account_clause}
            GROUP BY category
            ORDER BY total DESC
        """
        with get_session() as session:
            result = session.execute(
                text(sql), {"date_from": date_from, "date_to": date_to, **account_params}
            )
            return self._rows(result)

    def get_monthly_spend(
        self,
        date_from: str,
        date_to: str,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return monthly expense totals. Each row: {month, total}."""
        account_clause, account_params = self._build_account_filter(accounts)
        sql = f"""
            SELECT SUBSTRING(date, 1, 7) AS month, SUM(ABS(amount_base)) AS total
            FROM transactions
            WHERE date >= :date_from AND date <= :date_to
              AND amount_base < 0
              AND duplicate_of IS NULL
              {account_clause}
            GROUP BY month
            ORDER BY month ASC
        """
        with get_session() as session:
            result = session.execute(
                text(sql), {"date_from": date_from, "date_to": date_to, **account_params}
            )
            return self._rows(result)

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
                   SUBSTRING(date, 1, 7) AS month,
                   SUM(ABS(amount_base)) AS total
            FROM transactions
            WHERE date >= :date_from AND date <= :date_to
              AND amount_base < 0
              AND duplicate_of IS NULL
              {account_clause}
            GROUP BY merchant_normalized, month
            ORDER BY merchant_normalized, month
        """
        with get_session() as session:
            result = session.execute(
                text(sql), {"date_from": date_from, "date_to": date_to, **account_params}
            )
            return self._rows(result)

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
        fee_phs = ", ".join(f":fee_cat_{i}" for i in range(len(fee_categories)))
        fee_params = {f"fee_cat_{i}": cat for i, cat in enumerate(fee_categories)}
        sql = f"""
            SELECT category, merchant_normalized,
                   COUNT(*) AS count, SUM(ABS(amount_base)) AS total
            FROM transactions
            WHERE date >= :date_from AND date <= :date_to
              AND category IN ({fee_phs})
              AND duplicate_of IS NULL
              {account_clause}
            GROUP BY category, merchant_normalized
            ORDER BY total DESC
        """
        with get_session() as session:
            result = session.execute(
                text(sql),
                {"date_from": date_from, "date_to": date_to, **fee_params, **account_params},
            )
            return self._rows(result)

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
        category_clause = "AND category = :category" if category else ""
        category_params = {"category": category} if category else {}
        sql = f"""
            SELECT merchant_normalized, category,
                   COUNT(*) AS count, SUM(ABS(amount_base)) AS total
            FROM transactions
            WHERE date >= :date_from AND date <= :date_to
              AND amount_base < 0
              AND duplicate_of IS NULL
              {category_clause}
              {account_clause}
            GROUP BY merchant_normalized, category
            ORDER BY total DESC
            LIMIT :limit
        """
        with get_session() as session:
            result = session.execute(
                text(sql),
                {"date_from": date_from, "date_to": date_to, "limit": limit, **category_params, **account_params},
            )
            return self._rows(result)

    def get_transactions_by_merchant(
        self,
        merchant_pattern: str,
        date_from: str | None = None,
        date_to: str | None = None,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return transactions matching a LIKE pattern on merchant_normalized."""
        account_clause, account_params = self._build_account_filter(accounts)
        date_clauses = []
        date_params: dict[str, Any] = {}
        if date_from:
            date_clauses.append("AND date >= :date_from")
            date_params["date_from"] = date_from
        if date_to:
            date_clauses.append("AND date <= :date_to")
            date_params["date_to"] = date_to
        sql = f"""
            SELECT * FROM transactions
            WHERE merchant_normalized LIKE :pattern
              AND duplicate_of IS NULL
              {' '.join(date_clauses)}
              {account_clause}
            ORDER BY date DESC
        """
        with get_session() as session:
            result = session.execute(
                text(sql),
                {"pattern": f"%{merchant_pattern}%", **date_params, **account_params},
            )
            return self._rows(result)

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
        with get_session() as session:
            result = session.execute(text(sql), account_params)
            row = result.fetchone()
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
        h = self._accounts_hash(accounts)
        with get_session() as session:
            result = session.execute(
                text("""
                    SELECT aggregations_json, habits_json, suggestions_json, created_at
                    FROM insights_cache
                    WHERE date_from = :date_from AND date_to = :date_to AND accounts_hash = :h
                """),
                {"date_from": date_from, "date_to": date_to, "h": h},
            )
            row = result.fetchone()
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
        with get_session() as session:
            result = session.execute(text("""
                SELECT date_from, date_to, aggregations_json, habits_json, suggestions_json
                FROM insights_cache
                ORDER BY created_at DESC
                LIMIT 1
            """))
            row = result.fetchone()
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
        h = self._accounts_hash(accounts)
        now = _now()
        with get_session() as session:
            session.execute(
                text("""
                    INSERT INTO insights_cache
                        (date_from, date_to, accounts_hash, aggregations_json, habits_json, suggestions_json, created_at)
                    VALUES (:date_from, :date_to, :h, :aggregations_json, :habits_json, :suggestions_json, :now)
                    ON CONFLICT(date_from, date_to, accounts_hash) DO UPDATE SET
                        aggregations_json = excluded.aggregations_json,
                        habits_json = excluded.habits_json,
                        suggestions_json = excluded.suggestions_json,
                        created_at = excluded.created_at
                """),
                {
                    "date_from": date_from,
                    "date_to": date_to,
                    "h": h,
                    "aggregations_json": json.dumps(aggregations),
                    "habits_json": json.dumps(habits),
                    "suggestions_json": json.dumps(suggestions),
                    "now": now,
                },
            )
            session.commit()

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
            SELECT SUBSTRING(date, 1, 7) AS month,
                   SUM(ABS(amount_base)) AS total,
                   COUNT(*) AS count,
                   AVG(ABS(amount_base)) AS avg_per_transaction
            FROM transactions
            WHERE category = :category
              AND date >= :date_from AND date <= :date_to
              AND duplicate_of IS NULL
              {account_clause}
            GROUP BY month
            ORDER BY month ASC
        """
        with get_session() as session:
            result = session.execute(
                text(sql),
                {"category": category, "date_from": date_from, "date_to": date_to, **account_params},
            )
            return self._rows(result)

    def get_account_summary(
        self,
        date_from: str,
        date_to: str,
    ) -> list[dict[str, Any]]:
        """Per-account inflow, outflow, net, and transaction count."""
        with get_session() as session:
            result = session.execute(
                text("""
                    SELECT account,
                           SUM(CASE WHEN amount_base > 0 THEN amount_base ELSE 0 END) AS inflow,
                           SUM(CASE WHEN amount_base < 0 THEN ABS(amount_base) ELSE 0 END) AS outflow,
                           SUM(amount_base) AS net,
                           COUNT(*) AS count
                    FROM transactions
                    WHERE date >= :date_from AND date <= :date_to
                      AND duplicate_of IS NULL
                    GROUP BY account
                    ORDER BY account ASC
                """),
                {"date_from": date_from, "date_to": date_to},
            )
            return self._rows(result)

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
        category_clause = "AND category = :category" if category else ""
        category_params = {"category": category} if category else {}
        sql = f"""
            SELECT * FROM transactions
            WHERE date >= :date_from AND date <= :date_to
              AND amount_base < 0
              AND duplicate_of IS NULL
              {category_clause}
              {account_clause}
            ORDER BY ABS(amount_base) DESC
            LIMIT :limit
        """
        with get_session() as session:
            result = session.execute(
                text(sql),
                {"date_from": date_from, "date_to": date_to, "limit": limit, **category_params, **account_params},
            )
            return self._rows(result)

    def get_latest_reconciliation_run_date(self) -> str | None:
        """Return MAX(completed_at) from runs_history where status = 'complete', or None."""
        with get_session() as session:
            result = session.execute(text("""
                SELECT MAX(completed_at) AS latest_completed
                FROM runs_history
                WHERE status = 'complete'
            """))
            row = result.fetchone()
        return str(row[0]) if row and row[0] is not None else None

    # ── Receipts ─────────────────────────────────────────────────────────────

    def upsert_receipts(self, receipts: list[dict[str, Any]]) -> None:
        """Insert or replace receipt rows. Sets created_at automatically."""
        if not receipts:
            return
        now = _now()
        for r in receipts:
            r.setdefault("created_at", now)
        with get_session() as session:
            session.execute(
                text("""
                    INSERT INTO receipts (
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
                    ON CONFLICT (id) DO UPDATE SET
                        run_id = :run_id, transaction_id = :transaction_id, source_file = :source_file,
                        merchant = :merchant, merchant_normalized = :merchant_normalized,
                        date = :date, currency = :currency,
                        subtotal = :subtotal, tax_amount = :tax_amount, tax_rate = :tax_rate, total = :total,
                        receipt_number = :receipt_number, confidence = :confidence, raw_content = :raw_content
                """),
                receipts,
            )
            session.commit()

    def upsert_receipt_lines(self, lines: list[dict[str, Any]]) -> None:
        """Insert or replace receipt line items."""
        if not lines:
            return
        with get_session() as session:
            session.execute(
                text("""
                    INSERT INTO receipt_lines (
                        id, receipt_id, line_number, description,
                        quantity, unit_price, amount, category
                    ) VALUES (
                        :id, :receipt_id, :line_number, :description,
                        :quantity, :unit_price, :amount, :category
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        receipt_id = :receipt_id, line_number = :line_number, description = :description,
                        quantity = :quantity, unit_price = :unit_price, amount = :amount, category = :category
                """),
                lines,
            )
            session.commit()

    def auto_link_receipts(self, run_id: str) -> int:
        """Link unlinked receipts to transactions within the same run.

        Match on: date + abs(total) == abs(amount_original) + currency + merchant_normalized.
        Only attempts linking when receipt has non-null date and currency.
        Returns the number of receipts linked.
        """
        with get_session() as session:
            result = session.execute(
                text("""
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
                    WHERE receipts.run_id = :run_id
                    AND receipts.transaction_id IS NULL
                    AND receipts.date IS NOT NULL
                    AND receipts.currency IS NOT NULL
                """),
                {"run_id": run_id},
            )
            linked = result.rowcount
            session.commit()
        return linked

    def get_receipt_line_breakdown(
        self,
        date_from: str,
        date_to: str,
        accounts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate receipt line spending by category and description."""
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
            WHERE t.date >= :date_from AND t.date <= :date_to
            AND t.duplicate_of IS NULL
            AND r.transaction_id IS NOT NULL
            {account_clause}
            GROUP BY t.category, rl.description
            ORDER BY t.category, total DESC
        """
        with get_session() as session:
            result = session.execute(
                text(sql), {"date_from": date_from, "date_to": date_to, **account_params}
            )
            return self._rows(result)
