"""Build Statements from a header and rows, check them, and summarize them.

Used by both extractors: ``legacy`` builds rows from the per-page LLM
normalization (v1.6 phase 1 measures it without changing it); the v1.6 reader
builds rows from the printed table.
"""

from __future__ import annotations

import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from agents.reconciliator.statements.balance import check_balance, diagnostic
from agents.reconciliator.statements.header import HeaderSection, StatementHeader
from shared.models import Statement, StatementRow, Transaction
from shared.parsing import balance_row_kind

LEGACY_EXTRACTOR_VERSION = "legacy-1"


def new_statement(
    source_file: str,
    file_sha256: str,
    header: StatementHeader,
    section: HeaderSection | None,
    rows: list[StatementRow],
    extractor: str,
    extractor_version: str,
    diagnostics: list[dict[str, Any]] | None = None,
) -> Statement:
    """Return a Statement for one currency section of a file, not yet checked."""
    return Statement(
        id=str(uuid.uuid4()),
        source_file=source_file,
        file_sha256=file_sha256,
        institution=header.institution,
        account=header.account,
        account_type=header.account_type,
        currency=section.currency if section else None,
        period_start=header.period_start,
        period_end=header.period_end,
        opening_balance=section.opening if section else None,
        opening_balance_raw=section.opening_raw if section else None,
        closing_balance=section.closing if section else None,
        closing_balance_raw=section.closing_raw if section else None,
        printed_totals=section.printed_totals() if section else {},
        rows=rows,
        extractor=extractor,
        extractor_version=extractor_version,
        diagnostics=[*header.diagnostics, *(diagnostics or [])],
    )


def row_diagnostics(statement: Statement) -> list[dict[str, Any]]:
    """Return content diagnostics for a statement's rows: none at all, balance rows, out-of-period dates."""
    found: list[dict[str, Any]] = []
    if not statement.rows:
        found.append(
            diagnostic("no_rows", "no movements were extracted from this statement")
        )
        return found
    balance_rows = [r for r in statement.rows if balance_row_kind(r.description_raw)]
    if balance_rows:
        found.append(
            diagnostic(
                "balance_row_as_transaction",
                "opening/closing balance lines were extracted as movements",
                count=len(balance_rows),
                amount=sum(r.amount or 0.0 for r in balance_rows),
            )
        )
    if statement.period_start and statement.period_end:
        outside = [
            r for r in statement.rows
            if r.date and not statement.period_start <= r.date <= statement.period_end
        ]  # fmt: skip
        if outside:
            found.append(
                diagnostic(
                    "date_outside_period",
                    f"dates outside {statement.period_start} – {statement.period_end}",
                    count=len(outside),
                )
            )
    undated = [r for r in statement.rows if not r.date]
    if undated:
        found.append(
            diagnostic("no_date", "rows without a readable date", count=len(undated))
        )
    return found


def legacy_statements(
    source_file: str,
    file_sha256: str,
    header: StatementHeader,
    transactions: list[Transaction],
) -> tuple[list[Statement], list[Transaction]]:
    """Return checked Statements for one PDF from the transactions the legacy normalize produced.

    Transactions only gain their ``statement_id``: phase 1 measures them without
    changing them. Rows whose currency differs from every currency the header
    names are counted, since that is how the page-by-page USD default (v1.6 F1)
    shows up.
    """
    sections = header.sections or [HeaderSection(currency=None)]
    known = {s.currency for s in sections if s.currency}
    by_section: dict[int, list[Transaction]] = {i: [] for i in range(len(sections))}
    foreign: list[Transaction] = []
    for t in transactions:
        index = next(
            (i for i, s in enumerate(sections) if s.currency == t.currency), None
        )
        if index is None:
            if known:
                foreign.append(t)
            index = 0
        by_section[index].append(t)

    statements: list[Statement] = []
    linked: list[Transaction] = []
    for index, section in enumerate(sections):
        rows = [
            StatementRow(
                row_index=i,
                description_raw=t.merchant,
                date=t.date,
                amount=t.amount_original,
                sign_source="llm",
            )
            for i, t in enumerate(by_section[index])
        ]
        extra: list[dict[str, Any]] = []
        if foreign and index == 0:
            extra.append(
                diagnostic(
                    "currency_differs_from_header",
                    f"rows booked in a currency the statement does not report ({', '.join(sorted(known))} expected)",
                    count=len(foreign),
                    currencies=sorted({t.currency for t in foreign}),
                )
            )
        statement = new_statement(
            source_file, file_sha256, header, section, rows,
            "legacy", LEGACY_EXTRACTOR_VERSION, extra,
        )  # fmt: skip
        statement = statement.model_copy(
            update={
                "diagnostics": [*statement.diagnostics, *row_diagnostics(statement)]
            }
        )
        statements.append(check_balance(statement))
        linked.extend(
            t.model_copy(update={"statement_id": statement.id})
            for t in by_section[index]
        )
    return statements, linked


def unreadable_statement(
    source_file: str, file_sha256: str, reason: str, detail: str
) -> Statement:
    """Return a placeholder Statement for a file that could not be read, so it is never silently missing."""
    return Statement(
        id=str(uuid.uuid4()),
        source_file=source_file,
        file_sha256=file_sha256,
        balance_status="mismatch",
        balance_detail=detail,
        diagnostics=[diagnostic(reason, detail)],
    )


def summarize(statements: list[Statement]) -> dict[str, Any]:
    """Return a compact, JSON-friendly summary of statements for reports and API responses."""
    counts = Counter(s.balance_status or "unchecked" for s in statements)
    return {
        "total": len(statements),
        "by_status": dict(counts),
        "statements": [
            {
                "id": s.id,
                "file": Path(s.source_file).name,
                "institution": s.institution,
                "account": s.account,
                "currency": s.currency,
                "period": [s.period_start, s.period_end],
                "rows": len(s.rows),
                "extractor": s.extractor,
                "balance_status": s.balance_status,
                "balance_detail": s.balance_detail,
                "diagnostics": [d.get("reason") for d in s.diagnostics],
            }
            for s in statements
        ],
    }


def report_lines(statements: list[Statement]) -> list[str]:
    """Return human-readable report lines: one per statement, with what failed and why."""
    if not statements:
        return []
    summary = summarize(statements)
    by_status = ", ".join(f"{k} {v}" for k, v in sorted(summary["by_status"].items()))
    lines = [f"Statements: {summary['total']} ({by_status})"]
    for s in statements:
        label = f"{Path(s.source_file).name} [{s.currency or '?'}]"
        lines.append(f"  - {label}: {s.balance_status} — {s.balance_detail}")
        for d in s.diagnostics:
            count = f" ({d['count']})" if d.get("count") else ""
            lines.append(f"      · {d.get('reason')}{count}: {d.get('detail')}")
    return lines
