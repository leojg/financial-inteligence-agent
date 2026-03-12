"""Persistence service for normalized_document_cache, merchant_categories, duplicate_pairs, runs, and transactions."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from agent.db import get_connection


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
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        cur.close()
        return [dict(zip(colnames, r)) for r in rows]

    def get_run_transactions(self, run_id: str) -> list[dict[str, Any]]:
        """Return all transactions for a given run_id."""
        conn = get_connection()
        cur = conn.execute(
            "SELECT * FROM transactions WHERE run_id = ? ORDER BY date ASC",
            (run_id,),
        )
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        cur.close()
        return [dict(zip(colnames, r)) for r in rows]

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
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        cur.close()
        return [dict(zip(colnames, r)) for r in rows]
