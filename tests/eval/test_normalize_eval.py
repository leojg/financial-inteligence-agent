"""Normalize-node eval: field metrics vs labeled synthetic data (xlsx/pdf ingest path)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tabulate import tabulate

from agents.reconciliator.configuration import DEFAULT_CONFIG
from agents.reconciliator.nodes import make_normalize_node
from agents.reconciliator.utils.parsers import load_documents
from shared.models import RawDocument, Transaction

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Ingest only loads pdf/xlsx; image statements use the vision pipeline (see v1.4 spec).
_SUPPORTED_FILE_TYPES = frozenset({"xlsx", "pdf"})

_AMOUNT_TOL = 0.01


def _norm_merchant(s: str) -> str:
    return s.strip().casefold()


def _match_key(
    date: str,
    amount: float,
    merchant: str,
) -> tuple[str, float, str]:
    return (date, round(float(amount), 2), _norm_merchant(merchant))


def _key_from_expected(row: dict) -> tuple[str, float, str]:
    return _match_key(row["date"], row["amount"], row["merchant"])


def _key_from_prediction(t: Transaction) -> tuple[str, float, str]:
    return _match_key(t.date, t.amount_original, t.merchant)


def _match_by_date_amount_merchant(
    expected: list[dict],
    predicted: list[Transaction],
) -> tuple[list[tuple[dict, Transaction | None]], list[Transaction]]:
    """Greedy multiset match on (date, amount, merchant); merchant compared case-insensitive."""
    pool = list(predicted)
    pairs: list[tuple[dict, Transaction | None]] = []
    for exp in expected:
        ek = _key_from_expected(exp)
        idx = next((i for i, p in enumerate(pool) if _key_from_prediction(p) == ek), None)
        if idx is None:
            pairs.append((exp, None))
        else:
            pairs.append((exp, pool.pop(idx)))
    return pairs, pool


def _raw_documents_like_ingest(absolute_path: str) -> list[RawDocument]:
    """Mirror `ingest` in nodes.py: load_documents → RawDocument list."""
    documents = load_documents([absolute_path])
    return [
        RawDocument(
            source_file=doc.metadata["source"],
            file_type="xlsx"
            if str(doc.metadata["source"]).endswith((".xlsx", ".xls"))
            else "pdf",
            content=doc.page_content,
            confidence=1.0,
        )
        for doc in documents
    ]


def _currency_match(expected: str, actual: str) -> bool:
    return expected.strip().casefold() == (actual or "").strip().casefold()


def _account_match(expected: str, actual: str) -> bool:
    return expected.strip().casefold() == (actual or "").strip().casefold()


def _amount_match(expected: float, actual: float) -> bool:
    return abs(float(expected) - float(actual)) <= _AMOUNT_TOL


@pytest.mark.eval
def test_normalize_field_metrics(normalization_labels):
    """Run normalize on each labeled xlsx/pdf; print metrics (no fixed accuracy bar yet)."""
    if not normalization_labels:
        pytest.skip("No normalization labels in eval_labels.json — run generate_samples.py")

    rows_out = []
    for entry in normalization_labels:
        ft = entry["file_type"]
        if ft not in _SUPPORTED_FILE_TYPES:
            continue

        basename = entry["source_file"]
        path = DATA_DIR / basename
        if not path.is_file():
            pytest.skip(f"Missing sample file: {path} (run scripts/generate_samples.py)")

        expected = entry["expected_transactions"]
        raw_documents = _raw_documents_like_ingest(str(path.resolve()))
        state = {
            "source_folder": str(DATA_DIR),
            "raw_documents": raw_documents,
            "transactions": [],
            "duplicates": [],
            "suspicious": [],
            "exchange_rates": {},
            "report": None,
        }

        result = make_normalize_node(DEFAULT_CONFIG)(state)
        predicted: list[Transaction] = result.get("transactions") or []

        pairs, extras = _match_by_date_amount_merchant(expected, predicted)
        matched = [(e, p) for e, p in pairs if p is not None]
        missing = sum(1 for e, p in pairs if p is None)

        date_ok = merchant_ok = amount_ok = currency_ok = account_ok = 0
        all_four_core = 0  # date, merchant, amount, currency (per v1.4 spec)
        for exp, pred in matched:
            if pred is None:
                continue
            d_ok = exp["date"] == pred.date
            m_ok = _norm_merchant(exp["merchant"]) == _norm_merchant(pred.merchant)
            a_ok = _amount_match(exp["amount"], pred.amount_original)
            c_ok = _currency_match(exp["currency"], pred.currency)
            ac_ok = _account_match(exp["account"], pred.account)
            date_ok += int(d_ok)
            merchant_ok += int(m_ok)
            amount_ok += int(a_ok)
            currency_ok += int(c_ok)
            account_ok += int(ac_ok)
            if d_ok and m_ok and a_ok and c_ok:
                all_four_core += 1

        n = len(matched)
        label = f"{basename} ({ft})"
        rows_out.append(
            [
                label,
                len(expected),
                len(predicted),
                missing,
                len(extras),
                f"{date_ok}/{n}" if n else "—",
                f"{merchant_ok}/{n}" if n else "—",
                f"{amount_ok}/{n}" if n else "—",
                f"{currency_ok}/{n}" if n else "—",
                f"{account_ok}/{n}" if n else "—",
                f"{all_four_core}/{n}" if n else "—",
            ]
        )

    if not rows_out:
        pytest.skip(
            "No xlsx/pdf normalization entries or no matching files — "
            "generate samples with PDF/XLSX enabled"
        )

    print(
        "\n"
        + tabulate(
            rows_out,
            headers=[
                "document",
                "exp_rows",
                "pred_rows",
                "miss",
                "extra",
                "date",
                "merchant",
                "amount",
                "currency",
                "account",
                "all4",
            ],
            tablefmt="github",
        )
    )
