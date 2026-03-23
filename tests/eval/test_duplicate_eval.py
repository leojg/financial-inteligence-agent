"""LLM duplicate detection precision/recall eval against labeled synthetic data."""

import pytest
from tabulate import tabulate

from agents.reconciliator.configuration import DEFAULT_CONFIG
from agents.reconciliator.nodes import make_detect_duplicates_node

from .helpers import make_transaction

RECALL_THRESHOLD = 0.90
PRECISION_THRESHOLD = 0.85


@pytest.mark.eval
def test_duplicate_precision_recall(duplicate_pairs_labels, non_duplicate_pairs_labels):
    """Assert duplicate detection recall >= 90% and precision >= 85%."""
    # Build a deduplicated transaction pool from all labeled pairs
    txn_map: dict[tuple, str] = {}  # canonical key → transaction id

    def get_or_create_id(entry: dict) -> str:
        key = (
            entry["date"],
            entry["merchant"],
            entry["amount"],
            entry["currency"],
            entry["account"],
        )
        if key not in txn_map:
            t = make_transaction(entry)
            txn_map[key] = t.id
            _pool[t.id] = t
        return txn_map[key]

    _pool: dict[str, object] = {}

    labeled_dup_pairs: list[tuple[str, str]] = []
    labeled_non_dup_pairs: list[tuple[str, str]] = []

    for pair in duplicate_pairs_labels:
        id_a = get_or_create_id(pair["transaction_a"])
        id_b = get_or_create_id(pair["transaction_b"])
        labeled_dup_pairs.append((id_a, id_b))

    for pair in non_duplicate_pairs_labels:
        id_a = get_or_create_id(pair["transaction_a"])
        id_b = get_or_create_id(pair["transaction_b"])
        labeled_non_dup_pairs.append((id_a, id_b))

    state = {
        "source_folder": "data",
        "raw_documents": [],
        "transactions": list(_pool.values()),
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None,
    }

    result = make_detect_duplicates_node(DEFAULT_CONFIG)(state)

    # Build duplicate clusters via union-find so transitive duplicates are handled.
    # e.g. if A→B and A→C, then B and C are in the same cluster even though B→C
    # was never emitted directly.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: str, y: str) -> None:
        parent[find(x)] = find(y)

    for t in result["transactions"]:
        if getattr(t, "duplicate_of", None):
            union(t.id, t.duplicate_of)  # type: ignore[arg-type]

    def same_cluster(a: str, b: str) -> bool:
        return find(a) == find(b)

    # Metrics
    true_pos = sum(1 for a, b in labeled_dup_pairs if same_cluster(a, b))
    false_neg = len(labeled_dup_pairs) - true_pos
    false_pos = sum(1 for a, b in labeled_non_dup_pairs if same_cluster(a, b))

    recall = true_pos / len(labeled_dup_pairs) if labeled_dup_pairs else 1.0
    denom = true_pos + false_pos
    precision = true_pos / denom if denom > 0 else 1.0

    table = [
        ["Labeled duplicate pairs", len(labeled_dup_pairs)],
        ["Labeled non-duplicate pairs", len(labeled_non_dup_pairs)],
        ["True positives", true_pos],
        ["False negatives (missed dups)", false_neg],
        ["False positives (wrong dups)", false_pos],
        ["Recall", f"{recall:.1%}"],
        ["Precision", f"{precision:.1%}"],
    ]

    assert recall >= RECALL_THRESHOLD, "Duplicate recall below threshold.\n" + tabulate(
        table, tablefmt="simple"
    )
    assert precision >= PRECISION_THRESHOLD, (
        "Duplicate precision below threshold.\n" + tabulate(table, tablefmt="simple")
    )
