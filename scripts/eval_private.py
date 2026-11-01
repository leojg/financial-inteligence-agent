"""Private statement eval (v1.6 §5): run the statement extractors on your own statements, locally.

Your statements never leave your machine and never enter git: put the PDFs under
``PRIVATE_EVAL_DIR`` (default ``data/private/``, gitignored). Each run uses a
fresh temporary database, so nothing is served from a cache.

What it prints is aggregate only — per statement: balance status, rows, which
diagnostics fired; per labeled file: recall, precision, sign and date errors,
tokens used. Never transaction contents. A JSON with the full statement
summaries (diagnostics included) is written to ``<dir>/results/``.

Usage (from the repo root):
    python scripts/eval_private.py                     # the v1.6 ladder (needs OPENAI_API_KEY)
    python scripts/eval_private.py --extractor rules   # table reader only: no API calls
    python scripts/eval_private.py --extractor legacy  # the pre-v1.6 per-page normalize, measured
    python scripts/eval_private.py --extractor all     # all three side by side
    python scripts/eval_private.py --export-labels     # write labels to correct by hand

Labels (optional, for recall/precision): ``<dir>/labels/<pdf stem>.csv`` with
columns ``date,description,amount,currency`` — ISO date, amount signed with a
dot decimal (negative = money out or a card charge). ``--export-labels`` writes
the extractor's rows in that shape for every PDF without labels; correct them
once and later runs measure against them. Existing label files are never
overwritten. Without labels, the balance check alone says which statements close.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

logger = logging.getLogger("eval_private")

EXTRACTORS = ("ladder", "rules", "legacy")


# ── Labels ────────────────────────────────────────────────────────────────────


def read_labels(path: Path) -> list[dict[str, Any]]:
    """Read a labels CSV: date, description, amount (signed, dot decimal), currency."""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not (row.get("date") and row.get("amount")):
                continue
            rows.append(
                {
                    "date": row["date"].strip(),
                    "description": (row.get("description") or "").strip(),
                    "amount": round(float(row["amount"]), 2),
                    "currency": (row.get("currency") or "").strip().upper() or None,
                }
            )
    return rows


def write_labels(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write extracted rows as a labels CSV to be corrected by hand."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date",
                "description",
                "amount",
                "currency",
                "page",
                "row_index",
                "statement_status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def compare(
    expected: list[dict[str, Any]], extracted: list[dict[str, Any]]
) -> dict[str, Any]:
    """Match extracted rows to labels: exact (date, amount), then sign errors, then date errors."""
    pool = list(extracted)

    def take(
        predicate: Callable[[dict[str, Any], dict[str, Any]], bool], e: dict[str, Any]
    ) -> dict[str, Any] | None:
        for i, row in enumerate(pool):
            if predicate(e, row):
                return pool.pop(i)
        return None

    exact = sign = dated = currency = 0
    left: list[dict[str, Any]] = []
    for e in expected:
        hit = take(
            lambda e, r: r["date"] == e["date"] and r["amount"] == e["amount"], e
        )
        if hit is None:
            left.append(e)
            continue
        exact += 1
        if e["currency"] and hit["currency"] != e["currency"]:
            currency += 1
    still: list[dict[str, Any]] = []
    for e in left:
        if take(lambda e, r: r["date"] == e["date"] and r["amount"] == -e["amount"], e):
            sign += 1
        else:
            still.append(e)
    missed = 0
    for e in still:

        def near(e: dict[str, Any], r: dict[str, Any]) -> bool:
            try:
                gap = abs(
                    (date.fromisoformat(r["date"]) - date.fromisoformat(e["date"])).days
                )
            except ValueError:
                return False
            return r["amount"] == e["amount"] and gap <= 3

        if take(near, e):
            dated += 1
        else:
            missed += 1
    n, m = len(expected), len(extracted)
    return {
        "expected": n,
        "extracted": m,
        "exact": exact,
        "recall": exact / n if n else None,
        "precision": exact / m if m else None,
        "sign_errors": sign,
        "date_errors": dated,
        "currency_errors": currency,
        "missed": missed,
        "extra": len(pool),
    }


# ── Extractors ────────────────────────────────────────────────────────────────


def make_extractors(names: list[str]) -> dict[str, Callable[[str], list[Any]]]:
    """Build the requested extractors: each takes a PDF path and returns checked Statements."""
    if not os.getenv("OPENAI_API_KEY"):
        if any(n != "rules" for n in names):
            raise SystemExit(
                "OPENAI_API_KEY is required for the ladder and legacy extractors (use --extractor rules)"
            )
        os.environ["OPENAI_API_KEY"] = (
            "unused-rules-only"  # the package builds its graph on import
        )

    from langchain_openai import ChatOpenAI

    from agents.reconciliator.configuration import ReconciliationConfig
    from agents.reconciliator.nodes import make_normalize_node
    from agents.reconciliator.statements.build import legacy_statements
    from agents.reconciliator.statements.header import (
        make_header_transcriber,
        read_header_with_fallback,
    )
    from agents.reconciliator.statements.ladder import StatementExtractor
    from agents.reconciliator.statements.llm_reader import make_page_transcriber
    from agents.reconciliator.statements.pdf import load_pdf
    from agents.reconciliator.utils.parsers import load_documents
    from shared.models import RawDocument

    config = ReconciliationConfig(statement_extractor="ladder")
    extractors: dict[str, Callable[[str], list[Any]]] = {}
    if "rules" in names:
        extractors["rules"] = StatementExtractor(config).extract
    llm = None
    if "ladder" in names or "legacy" in names:
        llm = ChatOpenAI(
            model=config.statement_model_name, temperature=config.temperature
        )
    if "ladder" in names:
        extractors["ladder"] = StatementExtractor(
            config,
            header_transcriber=make_header_transcriber(llm),
            page_transcriber=make_page_transcriber(llm),
        ).extract
    if "legacy" in names:
        legacy_config = ReconciliationConfig(statement_extractor="legacy")
        normalize = make_normalize_node(legacy_config)
        header_transcriber = make_header_transcriber(llm)

        def legacy(path: str) -> list[Any]:
            documents = [
                RawDocument(
                    source_file=d.metadata["source"],
                    file_type="pdf",
                    content=d.page_content,
                    confidence=1.0,
                )
                for d in load_documents([path])
            ]
            transactions = normalize({"raw_documents": documents})["transactions"]
            document = load_pdf(path, config.pdf_passwords)
            header = read_header_with_fallback(
                document, header_transcriber, config.statement_local_currency
            )
            statements, _ = legacy_statements(
                path, document.sha256, header, transactions
            )
            return statements

        extractors["legacy"] = legacy
    return extractors


def rows_of(statements: list[Any]) -> list[dict[str, Any]]:
    """Flatten statements into comparable rows."""
    return [
        {
            "date": r.date or "",
            "description": r.description_raw,
            "amount": round(r.amount, 2) if r.amount is not None else None,
            "currency": s.currency,
            "page": r.page,
            "row_index": r.row_index,
            "statement_status": s.balance_status,
        }
        for s in statements
        for r in s.rows
        if r.amount is not None
    ]


# ── Run ───────────────────────────────────────────────────────────────────────


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def main() -> None:
    """Run the private eval."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dir", default=os.getenv("PRIVATE_EVAL_DIR", str(ROOT / "data" / "private"))
    )
    parser.add_argument("--extractor", choices=[*EXTRACTORS, "all"], default="ladder")
    parser.add_argument(
        "--export-labels",
        action="store_true",
        help="write labels/<stem>.csv for PDFs without labels",
    )
    args = parser.parse_args()
    base = Path(args.dir).expanduser().resolve()
    pdfs = sorted(
        p
        for p in base.rglob("*.pdf")
        if not {"results", "labels"} & set(p.relative_to(base).parts)
    )
    if not pdfs:
        raise SystemExit(
            f"No PDFs under {base}. Put your statements there (the folder is gitignored)."
        )

    os.environ["DATABASE_URL"] = "sqlite:///" + str(
        Path(tempfile.mkdtemp(prefix="eval_private_")) / "eval.db"
    )
    from shared.db import run_migrations

    run_migrations()
    # Alembic's fileConfig replaces logging and disables existing loggers: set ours up again.
    logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
    logger.disabled = False
    for noisy in ("httpx", "alembic", "agents", "shared"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    from langchain_core.callbacks import get_usage_metadata_callback
    from tabulate import tabulate

    names = list(EXTRACTORS) if args.extractor == "all" else [args.extractor]
    extractors = make_extractors(names)  # first: it sets up the API key the package needs on import
    from agents.reconciliator.statements.build import summarize

    table: list[list[Any]] = []
    results: dict[str, Any] = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "dir": str(base),
        "files": {},
    }
    totals: dict[str, Counter[str]] = {name: Counter() for name in extractors}
    exported = 0

    for pdf in pdfs:
        label_path = base / "labels" / f"{pdf.stem}.csv"
        labels = read_labels(label_path) if label_path.exists() else None
        results["files"][pdf.name] = {}
        for name, extract in extractors.items():
            started = time.monotonic()
            with get_usage_metadata_callback() as usage:
                try:
                    statements = extract(str(pdf))
                    error = None
                except Exception as e:  # one bad file must not stop the eval
                    statements, error = [], f"{type(e).__name__}: {e}"
            seconds = time.monotonic() - started
            tokens = sum(
                u.get("total_tokens", 0) for u in usage.usage_metadata.values()
            )
            rows = rows_of(statements)
            metrics = compare(labels, rows) if labels is not None else None
            statuses = [s.balance_status or "?" for s in statements]
            reasons = sorted(
                {d.get("reason", "?") for s in statements for d in s.diagnostics}
            )
            totals[name].update(statuses)
            if metrics:
                totals[name].update(
                    {
                        "labeled": 1,
                        "exact": metrics["exact"],
                        "expected": metrics["expected"],
                        "extracted": metrics["extracted"],
                        "sign_errors": metrics["sign_errors"],
                    }
                )
            totals[name].update({"tokens": tokens})
            table.append(
                [
                    pdf.name,
                    name,
                    ",".join(statuses) or "error",
                    len(rows),
                    _pct(metrics["recall"]) if metrics else "—",
                    _pct(metrics["precision"]) if metrics else "—",
                    metrics["sign_errors"] if metrics else "—",
                    metrics["date_errors"] if metrics else "—",
                    f"{seconds:.1f}",
                    tokens,
                    error or ", ".join(reasons),
                ]
            )
            results["files"][pdf.name][name] = {
                "error": error,
                "seconds": round(seconds, 2),
                "tokens": tokens,
                "metrics": metrics,
                "summary": summarize(statements),
                "breaks": [s.breaks for s in statements],
                "diagnostics": [s.diagnostics for s in statements],
            }
            if args.export_labels and labels is None and name == names[0] and rows:
                write_labels(label_path, rows)
                exported += 1

    logger.info(
        "\n%s",
        tabulate(
            table,
            headers=[
                "file",
                "extractor",
                "status",
                "rows",
                "recall",
                "precision",
                "sign err",
                "date err",
                "s",
                "tokens",
                "diagnostics",
            ],
            tablefmt="github",
        ),
    )
    for name, t in totals.items():
        statements = sum(t[s] for s in ("ok", "mismatch", "no_balance", "?"))
        line = f"{name}: {t['ok']}/{statements} statements close ({_pct(t['ok'] / statements if statements else None)})"
        line += f", {t['mismatch']} mismatch, {t['no_balance']} nothing to check"
        if t["labeled"]:
            line += (
                f"; labeled files {t['labeled']}: recall {_pct(t['exact'] / t['expected'] if t['expected'] else None)},"
                f" precision {_pct(t['exact'] / t['extracted'] if t['extracted'] else None)}, sign errors {t['sign_errors']}"
            )
        line += f"; tokens {t['tokens']}"
        logger.info(line)

    out = base / "results" / f"eval-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    logger.info("Full results (stay in the private folder): %s", out)
    if exported:
        logger.info(
            "Wrote %d label files under %s — correct them by hand, then run again.",
            exported,
            base / "labels",
        )


if __name__ == "__main__":
    main()
