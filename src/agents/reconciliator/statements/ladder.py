"""The extraction ladder: try readers from most to least trusted until one closes (v1.6 §3.3).

1. **Table reader** (deterministic, by position). Accepted when every statement
   it produces closes, or when there is nothing to check against and nothing
   looked off.
2. **Header fallback**: when the rules found no balances, the LLM may find
   them; the table rows are then checked again before going further.
3. **LLM rung** (page transcription with text + image). When it does not close,
   the breaking pages are asked again once, with the discrepancy stated.
4. Whatever is best is kept and, if it does not close, it is a ``mismatch``
   whose breaking rows go to review.

Results are cached by the file's bytes and the extractor version, and only
when every statement closed (v1.6 principle 10): a mismatch is re-read next time.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.reconciliator.configuration import ReconciliationConfig
from agents.reconciliator.statements.balance import check_balance, diagnostic
from agents.reconciliator.statements.build import (
    table_statements,
    unreadable_statement,
)
from agents.reconciliator.statements.header import (
    HeaderTranscriber,
    StatementHeader,
    read_header,
    read_header_with_fallback,
)
from agents.reconciliator.statements.llm_reader import (
    LLM_EXTRACTOR_VERSION,
    PageTranscriber,
    read_with_llm,
)
from agents.reconciliator.statements.pdf import (
    PdfDocument,
    PdfReadError,
    file_sha256,
    load_pdf,
)
from agents.reconciliator.statements.profiles import BankProfile, detect_profile
from agents.reconciliator.statements.table_reader import (
    TABLE_EXTRACTOR_VERSION,
    read_table,
)
from shared.models import Statement
from shared.repositories.database_repository import DatabaseRepository

logger = logging.getLogger(__name__)

LADDER_VERSION = f"ladder-1+{TABLE_EXTRACTOR_VERSION}+{LLM_EXTRACTOR_VERSION}"

_RANK = {"ok": 0, "no_balance": 1, "mismatch": 2, None: 3}
_DOUBTFUL = {
    "ignored_lines_with_amounts",
    "number_placement",
    "no_rows",
    "debit_and_credit",
}


def _rank(statements: list[Statement]) -> tuple[int, int, int]:
    """Lower is better: worst status, then number of breaks, then number of failures."""
    worst = max((_RANK[s.balance_status] for s in statements), default=3)
    breaks = sum(len(s.breaks) for s in statements)
    failures = sum(1 for s in statements if s.balance_status == "mismatch")
    return worst, breaks, failures


def _accepted(statements: list[Statement]) -> bool:
    """Accept when everything closes, or nothing can be checked and nothing looked off."""
    if not statements or any(not s.rows for s in statements):
        return False
    if all(s.balance_status == "ok" for s in statements):
        return True
    doubtful = any(
        d.get("reason") in _DOUBTFUL for s in statements for d in s.diagnostics
    )
    return all(s.balance_status == "no_balance" for s in statements) and not doubtful


def _retry_note(statements: list[Statement]) -> tuple[str, list[int]]:
    """Describe why the statements did not close and which pages to ask again."""
    notes: list[str] = []
    pages: set[int] = set()
    for s in statements:
        if s.balance_status != "mismatch":
            continue
        notes.append(f"[{s.currency or '?'}] {s.balance_detail}")
        for b in s.breaks:
            if b.get("page"):
                pages.add(int(b["page"]))
    note = (
        " ".join(notes)
        + ". Check every movement on the page: missed or extra rows, a number under the wrong column,"
        " a misread digit."
    )
    return note, sorted(pages)


@dataclass
class StatementExtractor:
    """Extract and verify the statements in a PDF, using the ladder."""

    config: ReconciliationConfig
    header_transcriber: HeaderTranscriber | None = None
    page_transcriber: PageTranscriber | None = None
    repository: DatabaseRepository | None = None
    profiles: list[BankProfile] | None = None

    def _cache_key(self, sha: str) -> str:
        model = self.config.statement_model_name if self.page_transcriber else "rules"
        return f"{LADDER_VERSION}:{model}:{sha}"

    def extract(self, path: str) -> list[Statement]:
        """Return the checked statements of one PDF (one per currency); never an empty list."""
        try:
            sha = file_sha256(path)
        except OSError as e:
            return [unreadable_statement(path, "", "unreadable_file", str(e))]

        cached = self._from_cache(sha, path)
        if cached is not None:
            return cached

        try:
            document = load_pdf(path, self.config.pdf_passwords)
        except PdfReadError as e:
            return [unreadable_statement(path, sha, "unreadable_pdf", str(e))]

        statements = self._ladder(document)
        if self.repository is not None and all(
            s.balance_status == "ok" for s in statements
        ):
            self.repository.save_statement_cache(
                self._cache_key(sha),
                sha,
                json.dumps([s.model_dump() for s in statements]),
            )
        return statements

    def _from_cache(self, sha: str, path: str) -> list[Statement] | None:
        if self.repository is None:
            return None
        payload = self.repository.get_statement_cache(self._cache_key(sha))
        if payload is None:
            return None
        # New ids: a statement belongs to the run that reads it.
        return [
            Statement.model_validate(
                {**s, "id": str(uuid.uuid4()), "source_file": path}
            )
            for s in json.loads(payload)
        ]

    def _table(
        self, document: PdfDocument, header: StatementHeader, profile: BankProfile
    ) -> list[Statement]:
        reading = read_table(
            document,
            header.period_start,
            header.period_end,
            header.account_type,
            profile,
            self.config.statement_local_currency,
        )
        if not reading.found_table:
            return []
        built = table_statements(document.path, document.sha256, header, reading)
        return [check_balance(s) for s in built]

    def _ladder(self, document: PdfDocument) -> list[Statement]:
        local = self.config.statement_local_currency
        profile = detect_profile(
            document.pages[0].text if document.pages else "", self.profiles
        )
        header = read_header(document, local)
        header.institution = header.institution or profile.institution
        header.account_type = profile.account_type or header.account_type
        notes: list[dict[str, Any]] = []
        if profile.name != "generic":
            notes.append(diagnostic("profile", f"bank profile {profile.name}"))

        table = (
            self._table(document, header, profile) if document.has_text_layer else []
        )
        if _accepted(table):
            return self._finish(table, notes)

        if self.header_transcriber is not None and not header.complete:
            header = read_header_with_fallback(document, self.header_transcriber, local)
            if table:
                table = self._table(document, header, profile)
                if _accepted(table):
                    return self._finish(table, notes)

        if self.page_transcriber is None:
            if table:
                return self._finish(table, notes)
            reason = "no_table_found" if document.has_text_layer else "no_text_layer"
            detail = (
                "no table header row was recognised and no LLM is configured"
                if document.has_text_layer
                else "the PDF has no text layer (a scan) and no LLM is configured"
            )
            return [
                unreadable_statement(document.path, document.sha256, reason, detail)
            ]

        llm = self._llm(document, header, profile)
        candidates = [c for c in (table, llm) if c]
        best = min(candidates, key=_rank)  # ties keep the table reader (listed first)
        if table and llm:
            other = llm if best is table else table
            notes.append(
                diagnostic(
                    "ladder",
                    f"table reader: {', '.join(str(s.balance_status) for s in table)}; "
                    f"LLM: {', '.join(str(s.balance_status) for s in llm)}; "
                    f"kept {best[0].extractor}",
                    discarded=other[0].extractor,
                )
            )
        return self._finish(best, notes)

    def _llm(
        self, document: PdfDocument, header: StatementHeader, profile: BankProfile
    ) -> list[Statement]:
        assert self.page_transcriber is not None
        local = self.config.statement_local_currency
        first, by_page = read_with_llm(
            document,
            header,
            self.page_transcriber,
            profile=profile,
            local_currency=local,
        )
        statements = [
            check_balance(s)
            for s in table_statements(
                document.path,
                document.sha256,
                header,
                first.reading,
                "llm",
                LLM_EXTRACTOR_VERSION,
            )
        ]
        if not statements or all(s.balance_status != "mismatch" for s in statements):
            return statements

        note, pages = _retry_note(statements)
        second, _ = read_with_llm(
            document,
            header,
            self.page_transcriber,
            pages=pages or None,
            retry_note=note,
            profile=profile,
            local_currency=local,
            previous=by_page,
        )
        retried = [
            check_balance(s)
            for s in table_statements(
                document.path,
                document.sha256,
                header,
                second.reading,
                "llm",
                LLM_EXTRACTOR_VERSION,
            )
        ]
        kept, label = (
            (retried, "closed")
            if _rank(retried) < _rank(statements)
            else (statements, "did not help")
        )
        return [
            s.model_copy(
                update={
                    "diagnostics": [
                        *s.diagnostics,
                        diagnostic(
                            "llm_retry", f"retried pages {pages or 'all'}: {label}"
                        ),
                    ]
                }
            )
            for s in kept
        ]

    @staticmethod
    def _finish(
        statements: list[Statement], notes: list[dict[str, Any]]
    ) -> list[Statement]:
        if not notes:
            return statements
        first = statements[0]
        return [
            first.model_copy(update={"diagnostics": [*first.diagnostics, *notes]}),
            *statements[1:],
        ]


def statement_files(source_files: list[str]) -> list[str]:
    """Return the PDF files among the run's sources (the ones the ladder reads)."""
    return [f for f in source_files if Path(f).suffix.lower() == ".pdf"]
