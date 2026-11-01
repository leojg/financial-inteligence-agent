"""Phase 2 of v1.6: LLM transcription rung, the extraction ladder, statement → transactions, graph wiring.

The LLM is never called: transcribers are plain functions returning what a model would.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents.reconciliator.configuration import ReconciliationConfig
from agents.reconciliator.graph import make_graph
from agents.reconciliator.nodes import (
    make_check_statements_node,
    make_extract_statements_node,
    make_normalize_node,
)
from agents.reconciliator.statements.balance import check_balance
from agents.reconciliator.statements.build import (
    UNKNOWN_CURRENCY,
    statement_transactions,
)
from agents.reconciliator.statements.header import read_header
from agents.reconciliator.statements.ladder import StatementExtractor
from agents.reconciliator.statements.llm_reader import (
    PageTranscription,
    TranscribedRow,
    expected_rows,
    read_with_llm,
)
from agents.reconciliator.statements.pdf import load_pdf
from shared.models import RawDocument, Statement, StatementRow
from shared.repositories.database_repository import DatabaseRepository

from .statement_pdfs import Column, build_statement_pdf, deposit_layout

CONFIG = ReconciliationConfig(statement_extractor="ladder", statement_header_llm=False)


def _row(
    date: str,
    description: str,
    debit: str | None = None,
    credit: str | None = None,
    balance: str | None = None,
) -> TranscribedRow:
    return TranscribedRow(
        date_as_printed=date,
        description_as_printed=description,
        debit_as_printed=debit,
        credit_as_printed=credit,
        amount_as_printed=None,
        balance_as_printed=balance,
        currency_as_printed=None,
    )


# What a model would transcribe from deposit_layout() (all on one page).
GOOD_ROWS = [
    _row(
        "03/01/2026",
        "PAGO ALQUILER APTO 302 ENERO",
        debit="8.500,00",
        balance="6.500,00",
    ),
    _row(
        "05/01/2026",
        "TRANSFERENCIA RECIBIDA ITAU",
        credit="4.000,00",
        balance="10.500,00",
    ),
    _row("08/01/2026", "UTE FACTURA", debit="2.340,50", balance="8.159,50"),
    _row("12/01/2026", "COMPRA DISCO", debit="1.200,00", balance="6.959,50"),
    _row("20/01/2026", "RETIRO CAJERO", debit="1.500,00", balance="5.459,50"),
]


class FakeTranscriber:
    """Returns scripted transcriptions, one per call; records the prompts it was given."""

    def __init__(self, *pages: list[TranscribedRow]) -> None:
        self.pages = list(pages)
        self.prompts: list[str] = []
        self.images: list[bytes | None] = []

    def __call__(self, prompt: str, image: bytes | None) -> PageTranscription:
        self.prompts.append(prompt)
        self.images.append(image)
        rows = self.pages.pop(0) if self.pages else []
        return PageTranscription(
            rows=rows, opening_balance_as_printed=None, closing_balance_as_printed=None
        )


def _unlabeled_deposit(tmp_path: Path, name: str = "d.pdf") -> Path:
    """A statement whose table header the rules cannot recognise (so the table reader fails)."""
    layout = deposit_layout()
    layout.columns = [Column("·", c.x, c.align) for c in layout.columns]
    return build_statement_pdf(tmp_path / name, layout)


# ── LLM rung ──────────────────────────────────────────────────────────────────


def test_llm_rung_parses_transcribed_cells_like_the_table_reader(
    tmp_path: Path,
) -> None:
    path = _unlabeled_deposit(tmp_path)
    document = load_pdf(str(path))
    header = read_header(document)
    transcriber = FakeTranscriber(GOOD_ROWS)
    result, _ = read_with_llm(document, header, transcriber)
    rows = result.reading.rows
    assert [r.amount for r in rows] == [-8500.0, 4000.0, -2340.5, -1200.0, -1500.0]
    assert {r.sign_source for r in rows} == {"llm"}  # the model chose the column
    assert rows[0].date == "2026-01-03"
    assert transcriber.images[0] and transcriber.images[0][:4] == b"\x89PNG"
    assert "BROU" in transcriber.prompts[0] and "UYU" in transcriber.prompts[0]


def test_llm_rung_reports_row_count_and_amounts_not_in_text(tmp_path: Path) -> None:
    document = load_pdf(str(_unlabeled_deposit(tmp_path)))
    invented = [*GOOD_ROWS[:3], _row("12/01/2026", "COMPRA DISCO", debit="1.299,00")]
    result, _ = read_with_llm(
        document, read_header(document), FakeTranscriber(invented)
    )
    reasons = {d["reason"]: d for d in result.reading.diagnostics}
    assert expected_rows(document.pages[0]) == 6  # includes the SALDO ANTERIOR line
    assert reasons["page_row_count_differs"]["count"] == -2
    assert reasons["llm_amount_not_in_text"]["examples"] == ["1.299,00"]


# ── Ladder ────────────────────────────────────────────────────────────────────


def test_ladder_accepts_the_table_reader_without_calling_the_llm(
    tmp_path: Path,
) -> None:
    transcriber = FakeTranscriber()
    statements = StatementExtractor(CONFIG, page_transcriber=transcriber).extract(
        str(build_statement_pdf(tmp_path / "d.pdf", deposit_layout()))
    )
    assert [s.extractor for s in statements] == ["table"]
    assert statements[0].balance_status == "ok"
    assert transcriber.prompts == []


def test_ladder_falls_back_to_the_llm_when_the_table_is_not_recognised(
    tmp_path: Path,
) -> None:
    transcriber = FakeTranscriber(GOOD_ROWS)
    statements = StatementExtractor(CONFIG, page_transcriber=transcriber).extract(
        str(_unlabeled_deposit(tmp_path))
    )
    assert [s.extractor for s in statements] == ["llm"]
    assert statements[0].balance_status == "ok", statements[0].balance_detail
    assert len(transcriber.prompts) == 1


def test_ladder_retries_the_breaking_page_with_the_discrepancy(tmp_path: Path) -> None:
    wrong = [*GOOD_ROWS]
    wrong[2] = _row(
        "08/01/2026", "UTE FACTURA", debit="2.430,50", balance="8.159,50"
    )  # misread digit
    transcriber = FakeTranscriber(wrong, GOOD_ROWS)
    statements = StatementExtractor(CONFIG, page_transcriber=transcriber).extract(
        str(_unlabeled_deposit(tmp_path))
    )
    assert statements[0].balance_status == "ok"
    assert len(transcriber.prompts) == 2
    assert "did not add up" in transcriber.prompts[1]
    assert "running balance breaks at row(s) 2" in transcriber.prompts[1]
    assert any(d["reason"] == "llm_retry" for d in statements[0].diagnostics)


def test_ladder_keeps_a_mismatch_when_the_retry_does_not_help(tmp_path: Path) -> None:
    wrong = [*GOOD_ROWS]
    wrong[2] = _row("08/01/2026", "UTE FACTURA", debit="2.430,50", balance="8.159,50")
    statements = StatementExtractor(
        CONFIG, page_transcriber=FakeTranscriber(wrong, wrong)
    ).extract(str(_unlabeled_deposit(tmp_path)))
    assert statements[0].balance_status == "mismatch"
    assert [b["row_index"] for b in statements[0].breaks] == [2]


def test_ladder_without_llm_reports_an_unrecognised_table(tmp_path: Path) -> None:
    statements = StatementExtractor(CONFIG).extract(str(_unlabeled_deposit(tmp_path)))
    assert statements[0].diagnostics[0]["reason"] == "no_table_found"


def test_ladder_caches_only_statements_that_close(tmp_path: Path) -> None:
    repository = DatabaseRepository()
    good = str(build_statement_pdf(tmp_path / "good.pdf", deposit_layout()))
    first = StatementExtractor(CONFIG, repository=repository).extract(good)
    with patch("agents.reconciliator.statements.ladder.load_pdf") as load:
        second = StatementExtractor(CONFIG, repository=repository).extract(good)
        load.assert_not_called()
    assert [s.balance_status for s in second] == ["ok"]
    assert second[0].id != first[0].id  # a statement belongs to the run that reads it

    layout = deposit_layout()
    layout.rows[3][2] = "2.430,50"
    bad = str(build_statement_pdf(tmp_path / "bad.pdf", layout))
    StatementExtractor(CONFIG, repository=repository).extract(bad)
    with patch(
        "agents.reconciliator.statements.ladder.load_pdf", wraps=load_pdf
    ) as load:
        StatementExtractor(CONFIG, repository=repository).extract(bad)
        load.assert_called_once()  # a mismatch is read again


def test_ladder_unreadable_pdf(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 garbage")
    statements = StatementExtractor(CONFIG).extract(str(broken))
    assert statements[0].diagnostics[0]["reason"] == "unreadable_pdf"


# ── Statement → transactions ──────────────────────────────────────────────────


def _statement(rows: list[StatementRow], **extra: Any) -> Statement:
    fields: dict[str, Any] = {
        "id": "st-1",
        "source_file": "/x/brou.pdf",
        "file_sha256": "sha",
        "institution": "BROU",
        "account": "7203",
        "currency": "UYU",
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
    }
    return Statement(rows=rows, **{**fields, **extra})


def test_statement_transactions_carry_traceability() -> None:
    row = StatementRow(
        row_index=0,
        page=2,
        raw_text="03/01/2026 COMPRA 1.200,00",
        date="2026-01-03",
        description_raw="COMPRA",
        debit_raw="1.200,00",
        amount=-1200.0,
        sign_source="column",
        installment_n=2,
        installment_m=6,
    )
    transactions, _ = statement_transactions(_statement([row]))
    t = transactions[0]
    assert (t.statement_id, t.page, t.row_index, t.amount_raw) == (
        "st-1",
        2,
        0,
        "1.200,00",
    )
    assert (t.account, t.currency, t.amount_original, t.sign_source) == (
        "BROU ****7203",
        "UYU",
        -1200.0,
        "column",
    )
    assert (t.installment_n, t.installment_m) == (2, 6)
    assert not t.needs_review


def test_statement_transactions_flag_what_could_not_be_verified() -> None:
    rows = [
        StatementRow(row_index=0, date="2026-01-03", description_raw="A", amount=-10.0),
        StatementRow(row_index=1, date=None, description_raw="B", amount=-20.0),
        StatementRow(
            row_index=2,
            date="2026-01-05",
            description_raw="C",
            amount=None,
            amount_raw="??",
        ),
    ]
    statement = check_balance(
        _statement(rows, currency=None, opening_balance=100.0, closing_balance=50.0)
    )
    transactions, statement = statement_transactions(statement)
    assert [t.merchant for t in transactions] == ["A", "B"]
    assert {t.currency for t in transactions} == {UNKNOWN_CURRENCY}
    assert transactions[1].date == "2026-01-03"
    assert "previous row's date" in (transactions[1].review_reason or "")
    assert all("currency not printed" in (t.review_reason or "") for t in transactions)
    assert any(d["reason"] == "rows_not_converted" for d in statement.diagnostics)


def test_rows_in_a_break_are_flagged() -> None:
    rows = [
        StatementRow(
            row_index=0,
            date="2026-01-03",
            description_raw="A",
            amount=-200.0,
            balance_after=800.0,
        ),
        StatementRow(
            row_index=1,
            date="2026-01-04",
            description_raw="B",
            amount=-150.0,
            balance_after=700.0,
        ),
    ]
    statement = check_balance(
        _statement(rows, account_type="deposit", opening_balance=1000.0)
    )
    transactions, _ = statement_transactions(statement)
    assert [t.needs_review for t in transactions] == [False, True]


# ── Nodes and graph ───────────────────────────────────────────────────────────


def test_extract_and_check_nodes_turn_pdfs_into_transactions(tmp_path: Path) -> None:
    pdf = str(build_statement_pdf(tmp_path / "d.pdf", deposit_layout()))
    with patch("agents.reconciliator.nodes.ChatOpenAI"):
        extract = make_extract_statements_node(CONFIG)
        check = make_check_statements_node(CONFIG)
    extracted = extract({"source_files": [pdf, str(tmp_path / "other.xlsx")]})
    assert [s.balance_status for s in extracted["statements"]] == ["ok"]
    receipt = {
        "id": "r1",
        "date": "2026-01-02",
        "amount_original": -5.0,
        "currency": "UYU",
        "merchant": "CAFE",
        "account": "Receipt",
        "source_file": "r.png",
    }
    checked = check(
        {
            "transactions": [receipt],
            "statements": extracted["statements"],
            "source_files": [pdf],
        }
    )
    merchants = [t.merchant for t in checked["transactions"]]
    assert merchants[0] == "CAFE"
    assert merchants[1:] == [
        "PAGO ALQUILER APTO 302 ENERO",
        "TRANSFERENCIA RECIBIDA ITAU",
        "UTE FACTURA",
        "COMPRA DISCO",
        "RETIRO CAJERO",
    ]
    assert "SALDO ANTERIOR" not in merchants


def test_normalize_drops_balance_rows_with_the_v16_reader() -> None:
    import json
    from unittest.mock import MagicMock

    doc = RawDocument(source_file="a.xlsx", file_type="xlsx", content="| x |")
    answer = [
        {
            "date": "2026-01-01",
            "amount_original": 15000,
            "currency": "UYU",
            "merchant": "SALDO ANTERIOR",
            "account": "A",
        },
        {
            "date": "2026-01-03",
            "amount_original": -100,
            "currency": "UYU",
            "merchant": "PAGO SALDO TARJETA",
            "account": "A",
        },
    ]
    with patch("agents.reconciliator.nodes.ChatOpenAI") as llm:
        llm.return_value.invoke.return_value = MagicMock(content=json.dumps(answer))
        result = make_normalize_node(CONFIG)({"raw_documents": [doc]})
    assert [t.merchant for t in result["transactions"]] == ["PAGO SALDO TARJETA"]


def test_graph_routes_pdfs_by_extractor() -> None:
    with (
        patch("agents.reconciliator.nodes.ChatOpenAI"),
        patch("agents.reconciliator.nodes.ChatAnthropic"),
    ):
        ladder = make_graph(CONFIG).get_graph()
        legacy = make_graph(
            ReconciliationConfig(statement_extractor="legacy")
        ).get_graph()
    edges = {(e.source, e.target) for e in ladder.edges}
    assert {
        ("prepare_ingest", "extract_statements"),
        ("extract_statements", "normalize"),
        ("normalize", "check_statements"),
    } <= edges
    assert "extract_statements" in {
        n for n in legacy.nodes
    }  # present, but routed to skip_statements
