"""Read what a statement says about itself: institution, account, period, currency, balances, totals.

Rules first (labels and patterns on the printed lines); an LLM transcription as
fallback when the rules miss a balance or the currency. Every value the LLM
returns must appear in the PDF's text layer, or it is dropped (v1.6 principle 8).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from agents.reconciliator.statements.balance import diagnostic
from agents.reconciliator.statements.pdf import PdfDocument, PdfLine
from shared.models import AccountType
from shared.parsing import (
    balance_label,
    fold,
    looks_like_date,
    parse_amount,
    parse_currency,
    parse_date,
)

logger = logging.getLogger(__name__)

_INSTITUTIONS: list[tuple[str, str]] = [
    (r"\bBROU\b|BANCO DE LA REPUBLICA ORIENTAL", "BROU"),
    (r"\bITAU\b", "Itaú"),
    (r"\bSANTANDER\b", "Santander"),
    (r"\bSCOTIABANK\b", "Scotiabank"),
    (r"\bBBVA\b", "BBVA"),
    (r"\bHSBC\b", "HSBC"),
    (r"\bHERITAGE\b", "Heritage"),
    (r"\bBANDES\b", "Bandes"),
    (r"\bOCA\b", "OCA"),
    (r"\bCREDITEL\b", "Creditel"),
    (r"\bPREX\b", "Prex"),
    (r"\bMIDINERO\b|\bMI DINERO\b", "Mi Dinero"),
    (r"\bWISE\b", "Wise"),
]

_CARD_WORDS = re.compile(
    r"TARJETA|CREDIT CARD|CARD NUMBER|LIMITE DE CREDITO|CREDIT LIMIT|PAGO MINIMO|MINIMUM PAYMENT"
)
_DEPOSIT_WORDS = re.compile(
    r"CAJA DE AHORRO|CUENTA CORRIENTE|CTA\.? CTE|SAVINGS|CHECKING|CURRENT ACCOUNT|ACCOUNT STATEMENT"
)
_ACCOUNT = re.compile(
    r"(?:\*{2,}[\s-]*)+(\d{3,4})\b"  # ****7203, **** **** **** 2847, ****-7203
    r"|(?:CUENTA|CTA|ACCOUNT|TARJETA|CARD)\D{0,20}?(\d[\d -]{4,}\d)"
)
_PERIOD = re.compile(
    r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{2}-\d{2})\s*(?:-|–|—|AL|A|TO|HASTA)\s*"
    r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{2}-\d{2})"
)
_CURRENCY_LINE = re.compile(
    r"MONEDA|CURRENCY|AMOUNTS IN|IMPORTES? EN|EXPRESAD|EN DOLARES|EN PESOS|IMPORTE \(|DIVISA"
)
_TOTAL_DEBITS = re.compile(r"\bTOTAL (DE )?(DEBITOS|DEBITS|CARGOS|DEBE)\b")
_TOTAL_CREDITS = re.compile(r"\bTOTAL (DE )?(CREDITOS|CREDITS|ABONOS|HABER)\b")


@dataclass
class HeaderSection:
    """The balances a statement prints for one currency."""

    currency: str | None
    opening_raw: str | None = None
    closing_raw: str | None = None
    debits_raw: str | None = None
    credits_raw: str | None = None

    @property
    def opening(self) -> float | None:
        """Return the opening balance as printed, parsed."""
        return _amount(self.opening_raw)

    @property
    def closing(self) -> float | None:
        """Return the closing balance as printed, parsed."""
        return _amount(self.closing_raw)

    def printed_totals(self) -> dict[str, float]:
        """Return the footer totals that were printed and readable."""
        totals = {
            "debits": _amount(self.debits_raw),
            "credits": _amount(self.credits_raw),
        }
        return {k: abs(v) for k, v in totals.items() if v is not None}


@dataclass
class StatementHeader:
    """What the statement says about itself, before its rows are read."""

    institution: str | None = None
    account: str | None = None
    account_type: AccountType | None = None
    period_start: str | None = None
    period_end: str | None = None
    sections: list[HeaderSection] = field(default_factory=list)
    source: Literal["rules", "llm", "rules+llm"] = "rules"
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Return True when every section has a currency and both balances."""
        return bool(self.sections) and all(
            s.currency and s.opening is not None and s.closing is not None
            for s in self.sections
        )

    def section(self, currency: str | None) -> HeaderSection:
        """Return the section for a currency, creating it if needed."""
        for s in self.sections:
            if s.currency == currency:
                return s
        created = HeaderSection(currency=currency)
        self.sections.append(created)
        return created


def _amount(raw: str | None) -> float | None:
    value = parse_amount(raw).signed()
    return None if value is None else float(value)


def labeled_amounts(
    text: str, local_currency: str | None
) -> list[tuple[str | None, str]]:
    """Return ``(currency, amount)`` pairs printed after a label, e.g. ``$ 1.234,56 U$S 12,00``.

    ``text`` is the folded line from the end of the label on. A few words may sit
    between the label and the first amount (``AL 31/12/2025``); after an amount,
    the next word starts another label and ends the scan.
    """
    pairs: list[tuple[str | None, str]] = []
    pending: str | None = None
    skipped = 0
    for token in text.replace(":", " ").split():
        currency = parse_currency(token, bare_dollar=local_currency)
        if currency and not parse_amount(token).candidates:
            pending = currency
            continue
        if parse_amount(token).candidates and not looks_like_date(token):
            pairs.append((pending or parse_currency(token), token))
            pending = None
            continue
        if pairs or skipped >= 4:
            break
        skipped += 1
    return pairs


def _amount_tokens(line: PdfLine) -> list[str]:
    return [
        w.text
        for w in line.words
        if parse_amount(w.text).candidates and not looks_like_date(w.text)
    ]


def _header_lines(document: PdfDocument, limit: int = 25) -> list[PdfLine]:
    """Return the lines above the first transaction table, or the first ``limit`` lines."""
    if not document.pages:
        return []
    lines = document.pages[0].lines
    for i, line in enumerate(lines):
        if line.words and looks_like_date(line.words[0].text) and _amount_tokens(line):
            return lines[:i]
    return lines[:limit]


def read_header(
    document: PdfDocument, local_currency: str | None = "UYU"
) -> StatementHeader:
    """Read the header with rules only. ``local_currency`` is what a bare ``$`` means in a header."""
    header = StatementHeader()
    top = _header_lines(document)
    top_text = fold("\n".join(line.text for line in top))
    all_text = fold(document.text)

    for pattern, name in _INSTITUTIONS:
        if re.search(pattern, top_text):
            header.institution = name
            break

    account = _ACCOUNT.search(top_text)
    if account:
        header.account = next(g for g in account.groups() if g).strip()

    if _CARD_WORDS.search(top_text):
        header.account_type = "card"
    elif _DEPOSIT_WORDS.search(top_text):
        header.account_type = "deposit"

    period = _PERIOD.search(all_text)
    if period:
        header.period_start = parse_date(period.group(1))
        header.period_end = parse_date(period.group(2))

    currencies: list[str] = []
    for line in top:
        text = fold(line.text)
        if _CURRENCY_LINE.search(text):
            code = parse_currency(text, bare_dollar=local_currency)
            if code and code not in currencies:
                currencies.append(code)
    for code in currencies:
        header.section(code)

    def section_for(currency: str | None) -> HeaderSection:
        if currency is None:
            currency = currencies[0] if len(currencies) == 1 else None
        if currency is None and header.sections:
            return header.sections[0]
        return header.section(currency)

    for page in document.pages:
        for line in page.lines:
            text = fold(line.text)
            label = balance_label(text)
            if label:
                kind, end = label
                for currency, raw in labeled_amounts(text[end:], local_currency):
                    section = section_for(currency)
                    if kind == "opening" and section.opening_raw is None:
                        section.opening_raw = raw
                    elif kind == "closing":
                        section.closing_raw = raw  # the last closing line wins
            for total_label, attribute in (
                (_TOTAL_DEBITS, "debits_raw"),
                (_TOTAL_CREDITS, "credits_raw"),
            ):
                match = total_label.search(text)
                if match:
                    for currency, raw in labeled_amounts(
                        text[match.end() :], local_currency
                    ):
                        setattr(section_for(currency), attribute, raw)
    return header


# ── LLM fallback ──────────────────────────────────────────────────────────────


class _SectionOut(BaseModel):
    currency_as_printed: str | None = Field(
        description="The currency marker exactly as printed for this section (e.g. '$', 'U$S', 'UYU'), or null"
    )
    opening_balance_as_printed: str | None = Field(
        description="Opening / previous balance exactly as printed, or null if not printed"
    )
    closing_balance_as_printed: str | None = Field(
        description="Closing / new balance exactly as printed, or null if not printed"
    )
    total_debits_as_printed: str | None = Field(
        description="Printed total of debits/charges, or null"
    )
    total_credits_as_printed: str | None = Field(
        description="Printed total of credits/payments, or null"
    )


class HeaderTranscription(BaseModel):
    """Structured LLM output: the statement header, transcribed exactly as printed."""

    institution: str | None = Field(description="Bank or card issuer name, or null")
    account_as_printed: str | None = Field(
        description="Account or card number as printed (often masked), or null"
    )
    account_type: Literal["deposit", "card"] | None = Field(
        description="'card' for a credit card statement, 'deposit' for a savings/checking account, null if unclear"
    )
    period_start_as_printed: str | None = Field(
        description="Statement period start exactly as printed, or null"
    )
    period_end_as_printed: str | None = Field(
        description="Statement period end exactly as printed, or null"
    )
    sections: list[_SectionOut] = Field(
        description="One entry per currency the statement reports balances for (a card can have two)"
    )


HEADER_PROMPT = """You transcribe the header of a bank or credit card statement.

Copy values EXACTLY as printed — same digits, separators, signs and currency markers.
Do not compute, convert, reformat or guess. If a value is not printed, return null.

- Opening balance: "Saldo anterior", "Saldo inicial", "Previous balance", "Opening balance".
- Closing balance: "Saldo final", "Saldo actual", "Nuevo saldo", "Saldo a pagar", "New balance", "Closing balance".
- A credit card statement may report balances in two currencies (e.g. pesos and dollars): return one section per currency.
- Do not take credit limits, available credit or minimum payments as balances.

Statement text (first and last page):
{text}
"""


def _in_text(raw: str | None, haystack: str) -> bool:
    return raw is not None and re.sub(r"\s+", "", raw) in haystack


def merge_llm_header(
    header: StatementHeader,
    transcription: HeaderTranscription,
    document: PdfDocument,
    local_currency: str | None = "UYU",
) -> StatementHeader:
    """Fill what the rules missed from an LLM transcription, keeping only values found in the text."""
    haystack = re.sub(r"\s+", "", document.text)
    dropped: list[str] = []

    def anchored(raw: str | None, name: str) -> str | None:
        if raw is None:
            return None
        if _in_text(raw, haystack):
            return raw
        dropped.append(f"{name}={raw!r}")
        return None

    header.institution = header.institution or transcription.institution
    header.account = header.account or anchored(
        transcription.account_as_printed, "account"
    )
    header.account_type = header.account_type or transcription.account_type
    if header.period_start is None:
        header.period_start = parse_date(
            anchored(transcription.period_start_as_printed, "period_start")
        )
    if header.period_end is None:
        header.period_end = parse_date(
            anchored(transcription.period_end_as_printed, "period_end")
        )

    for out in transcription.sections:
        currency = parse_currency(out.currency_as_printed, bare_dollar=local_currency)
        if currency is None and len(header.sections) == 1:
            currency = header.sections[0].currency
        section = header.section(currency)
        section.opening_raw = section.opening_raw or anchored(
            out.opening_balance_as_printed, "opening"
        )
        section.closing_raw = section.closing_raw or anchored(
            out.closing_balance_as_printed, "closing"
        )
        section.debits_raw = section.debits_raw or anchored(
            out.total_debits_as_printed, "total_debits"
        )
        section.credits_raw = section.credits_raw or anchored(
            out.total_credits_as_printed, "total_credits"
        )

    header.source = "rules+llm" if header.source == "rules" else header.source
    if dropped:
        header.diagnostics.append(
            diagnostic(
                "llm_value_not_in_text",
                "header values not found in the PDF text: " + ", ".join(dropped),
            )
        )
    return header


def header_prompt(document: PdfDocument, max_chars: int = 12000) -> str:
    """Return the header prompt for a document (first and last page text)."""
    pages = document.pages
    chosen = pages if len(pages) <= 2 else [pages[0], pages[-1]]
    text = "\n\n".join(p.text for p in chosen)[:max_chars]
    return HEADER_PROMPT.format(text=text)


HeaderTranscriber = Callable[[PdfDocument], "HeaderTranscription | dict[str, Any]"]


def make_header_transcriber(llm: Any) -> HeaderTranscriber:
    """Wrap a chat model as a header transcriber (structured output, exact transcription)."""
    structured = llm.with_structured_output(HeaderTranscription)

    def transcribe(document: PdfDocument) -> HeaderTranscription | dict[str, Any]:
        result: HeaderTranscription | dict[str, Any] = structured.invoke(
            header_prompt(document)
        )
        return result

    return transcribe


def read_header_with_fallback(
    document: PdfDocument,
    transcribe: HeaderTranscriber | None,
    local_currency: str | None = "UYU",
) -> StatementHeader:
    """Read the header with rules; ask the LLM only for what is still missing.

    Args:
        document: The loaded PDF.
        transcribe: Returns a ``HeaderTranscription`` for the document (see
            ``make_header_transcriber``), or None to use rules only.
        local_currency: What a bare ``$`` means in a header.
    """
    header = read_header(document, local_currency)
    if header.complete or transcribe is None or not document.has_text_layer:
        return header
    try:
        transcription = transcribe(document)
        if isinstance(transcription, dict):
            transcription = HeaderTranscription.model_validate(transcription)
        return merge_llm_header(header, transcription, document, local_currency)
    except Exception as e:  # an LLM failure leaves the rules' header, recorded
        logger.warning("Header LLM fallback failed for %s: %s", document.path, e)
        header.diagnostics.append(diagnostic("header_llm_failed", str(e)))
        return header
