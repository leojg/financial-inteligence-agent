"""LLM rung: transcribe a statement page by page when no deterministic reader fits (v1.6 §3.3 rung 3).

The model transcribes; code interprets. For each page it returns the cells
exactly as printed — date, description, debit / credit / amount, balance — and
the shared parser (``table_reader.parse_cells``) turns them into values, the
same way as for the table reader. The model sees the statement header found
on the first page, the page's text layer and the page image: the text gives
exact strings, the image gives the layout (which column a number sits in).

Checks on what comes back: the number of movements per page against a count
of date-led lines in the text layer, and whether printed amounts appear in
the text layer at all. Signs the model chose (by putting a number under Débito
or Crédito) are marked ``llm`` so the balance check may correct them.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from agents.reconciliator.statements.balance import diagnostic
from agents.reconciliator.statements.header import StatementHeader
from agents.reconciliator.statements.pdf import PdfDocument, PdfPage, page_image_png
from agents.reconciliator.statements.profiles import GENERIC, BankProfile
from agents.reconciliator.statements.table_reader import (
    Cells,
    TableReading,
    parse_cells,
)
from shared.parsing import looks_like_date, parse_amount

logger = logging.getLogger(__name__)

LLM_EXTRACTOR_VERSION = "llm-1"


class TranscribedRow(BaseModel):
    """One printed movement, every cell exactly as printed (null when the cell is empty)."""

    date_as_printed: str | None = Field(
        description="The movement's date exactly as printed, or null"
    )
    description_as_printed: str = Field(
        description="The full description; join lines that wrap onto the next line"
    )
    debit_as_printed: str | None = Field(
        description="Number under a Débito/Debit/Cargos column, exactly as printed, else null"
    )
    credit_as_printed: str | None = Field(
        description="Number under a Crédito/Credit/Abonos column, exactly as printed, else null"
    )
    amount_as_printed: str | None = Field(
        description="Number under a single Importe/Amount column (with its sign or CR/DB), else null"
    )
    balance_as_printed: str | None = Field(
        description="Number under a Saldo/Balance column on this row, exactly as printed, else null"
    )
    currency_as_printed: str | None = Field(
        description="The row's currency marker if the row or its column shows one ($, U$S, UYU, USD), else null"
    )


class PageTranscription(BaseModel):
    """Structured LLM output for one page."""

    rows: list[TranscribedRow] = Field(
        description="Every movement on the page, in printed order"
    )
    opening_balance_as_printed: str | None = Field(
        description="The opening/previous balance if printed on this page, else null"
    )
    closing_balance_as_printed: str | None = Field(
        description="The closing/new balance if printed on this page, else null"
    )


PAGE_PROMPT = """You transcribe the movements table of ONE page of a bank or credit card statement.

Statement (from its first page): {context}

Rules:
- One element per printed movement, in printed order. Include every movement on this page.
- Copy values EXACTLY as printed: same digits, thousands and decimal separators, minus signs, CR/DB markers.
  Do not compute, convert, reformat or guess. Empty cell → null.
- Put each number in the field of the column it is printed under: Débito/Debit/Cargos → debit;
  Crédito/Credit/Abonos → credit; a single Importe/Amount column → amount; Saldo/Balance → balance.
- A description that wraps onto the next line belongs to the same movement.
- Opening/closing balance lines (Saldo anterior, Saldo final, Saldo a pagar, Previous/New balance)
  are not movements: put them in the opening/closing fields.
- Skip page headers and footers, column headers, subtotals ("Transporte", "Total"), summaries,
  credit limits, minimum payments.
{retry}
Page {page} of {pages}. Text layer of the page:
{text}
"""

PageTranscriber = Callable[[str, bytes | None], "PageTranscription | dict[str, Any]"]


def make_page_transcriber(llm: Any) -> PageTranscriber:
    """Wrap a chat model (vision-capable) as a page transcriber: prompt + page image → PageTranscription."""
    structured = llm.with_structured_output(PageTranscription)

    def transcribe(
        prompt: str, image_png: bytes | None
    ) -> PageTranscription | dict[str, Any]:
        content: list[str | dict[Any, Any]] = [{"type": "text", "text": prompt}]
        if image_png:
            data = base64.standard_b64encode(image_png).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{data}"},
                }
            )
        result: PageTranscription | dict[str, Any] = structured.invoke(
            [HumanMessage(content=content)]
        )
        return result

    return transcribe


def _context(header: StatementHeader) -> str:
    parts = [
        header.institution or "institution unknown",
        f"account {header.account}" if header.account else "account unknown",
        {"card": "credit card statement", "deposit": "deposit account statement"}.get(
            header.account_type or "", "account type unknown"
        ),
        "currencies " + ", ".join(s.currency for s in header.sections if s.currency)
        if any(s.currency for s in header.sections)
        else "currency unknown",
        f"period {header.period_start} to {header.period_end}"
        if header.period_start
        else "period unknown",
    ]
    return "; ".join(parts)


def page_prompt(
    header: StatementHeader, page: PdfPage, pages: int, retry: str = ""
) -> str:
    """Return the transcription prompt for one page."""
    text = page.layout_text.strip() or "(no text layer: read the page from the image)"
    return PAGE_PROMPT.format(
        context=_context(header),
        retry=f"\nIMPORTANT — a previous transcription did not add up: {retry}\n"
        if retry
        else "",
        page=page.number,
        pages=pages,
        text=text,
    )


def expected_rows(page: PdfPage) -> int:
    """Count the page's lines that start with a date and carry an amount (a deterministic estimate)."""
    count = 0
    for line in page.lines:
        words = line.words
        if not words or not looks_like_date(words[0].text):
            continue
        if any(
            parse_amount(w.text).candidates and not looks_like_date(w.text)
            for w in words[1:]
        ):
            count += 1
    return count


@dataclass
class LlmReading:
    """Rows transcribed by the LLM, parsed, plus the balances it found and what looked off."""

    reading: TableReading
    opening_raw: str | None = None
    closing_raw: str | None = None


def read_with_llm(
    document: PdfDocument,
    header: StatementHeader,
    transcribe: PageTranscriber,
    pages: list[int] | None = None,
    retry_note: str = "",
    profile: BankProfile = GENERIC,
    local_currency: str | None = "UYU",
    previous: dict[int, list[Cells]] | None = None,
) -> tuple[LlmReading, dict[int, list[Cells]]]:
    """Transcribe the given pages (default: all) and parse them like the table reader.

    ``previous`` holds the cells of pages already transcribed (a retry only
    re-asks the pages that broke); they are reused for the other pages.
    Returns the parsed reading and the cells per page.
    """
    reading = TableReading(found_table=True)
    result = LlmReading(reading=reading)
    by_page: dict[int, list[Cells]] = dict(previous or {})
    targets = pages or [p.number for p in document.pages]
    text_haystack = re.sub(r"\s+", "", document.text)

    for page in document.pages:
        if page.number not in targets:
            continue
        prompt = page_prompt(header, page, len(document.pages), retry_note)
        try:
            image = page_image_png(document.path, page.number, document.password)
        except Exception as e:  # rendering is best-effort; the text still goes
            logger.warning(
                "Could not render page %s of %s: %s", page.number, document.path, e
            )
            image = None
        try:
            raw = transcribe(prompt, image)
            out = (
                raw
                if isinstance(raw, PageTranscription)
                else PageTranscription.model_validate(raw)
            )
        except Exception as e:
            logger.warning(
                "LLM transcription failed for page %s of %s: %s",
                page.number,
                document.path,
                e,
            )
            reading.diagnostics.append(
                diagnostic("llm_page_failed", str(e), page=page.number)
            )
            by_page[page.number] = []
            continue

        for name, raw_balance in (
            ("opening", out.opening_balance_as_printed),
            ("closing", out.closing_balance_as_printed),
        ):
            if not raw_balance:
                continue
            if (
                document.has_text_layer
                and re.sub(r"\s+", "", raw_balance) not in text_haystack
            ):
                reading.diagnostics.append(
                    diagnostic(
                        "llm_value_not_in_text",
                        f"{name} balance {raw_balance!r} is not in the text layer; ignored",
                        page=page.number,
                    )
                )
            elif name == "opening" and result.opening_raw is None:
                result.opening_raw = raw_balance
            elif name == "closing":
                result.closing_raw = raw_balance
        by_page[page.number] = [
            Cells(
                page=page.number,
                top=0.0,
                raw_text=" | ".join(
                    v
                    for v in (
                        r.date_as_printed,
                        r.description_as_printed,
                        r.debit_as_printed,
                        r.credit_as_printed,
                        r.amount_as_printed,
                        r.balance_as_printed,
                    )
                    if v
                ),
                date_raw=r.date_as_printed,
                description=[r.description_as_printed.strip()],
                numbers={
                    k: v.strip()
                    for k, v in (
                        ("debit", r.debit_as_printed),
                        ("credit", r.credit_as_printed),
                        ("amount", r.amount_as_printed),
                        ("balance", r.balance_as_printed),
                    )
                    if v and v.strip()
                },
                currency_raw=r.currency_as_printed,
            )
            for r in out.rows
        ]

        estimate = expected_rows(page)
        got = len(by_page[page.number])
        if document.has_text_layer and estimate and got != estimate:
            reading.diagnostics.append(
                diagnostic(
                    "page_row_count_differs",
                    f"page {page.number}: {got} movements transcribed, {estimate} date-led lines with amounts in the text",
                    page=page.number,
                    count=got - estimate,
                )
            )

    cells_rows = [c for number in sorted(by_page) for c in by_page[number]]
    if document.has_text_layer:
        missing = [
            raw
            for c in cells_rows
            for raw in c.numbers.values()
            if re.sub(r"\s+", "", raw) not in text_haystack
        ]
        if missing:
            reading.diagnostics.append(
                diagnostic(
                    "llm_amount_not_in_text",
                    "amounts the model returned that the text layer does not contain",
                    count=len(missing),
                    examples=missing[:3],
                )
            )
    parse_cells(
        cells_rows,
        reading,
        header.period_start,
        header.period_end,
        header.account_type,
        profile,
        local_currency,
    )
    reading.rows = [
        r.model_copy(update={"sign_source": "llm"}) if r.sign_source == "column" else r
        for r in reading.rows
    ]
    if result.opening_raw:
        reading.opening_raw[None] = result.opening_raw
    if result.closing_raw:
        reading.closing_raw[None] = result.closing_raw
    return result, by_page
