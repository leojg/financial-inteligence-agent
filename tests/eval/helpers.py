"""Shared helpers for eval tests."""

import uuid

from shared.models import Transaction


def make_transaction(entry: dict, txn_id: str | None = None) -> Transaction:
    """Build a Transaction from an eval-labels entry dict."""
    return Transaction(
        id=txn_id or str(uuid.uuid4()),
        date=entry["date"],
        amount_original=entry["amount"],
        amount_base=entry["amount"],
        currency=entry["currency"],
        merchant=entry["merchant"],
        account=entry["account"],
        source_file=f"data/eval/{entry['account'].replace(' ', '_')}.xlsx",
    )
