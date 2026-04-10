"""LLM duplicate detection precision/recall eval against labeled synthetic data."""

from collections import defaultdict

import pytest
from tabulate import tabulate

from agents.reconciliator.configuration import DEFAULT_CONFIG
from agents.reconciliator.nodes import make_detect_duplicates_node

from .helpers import make_transaction

# Floors for hardened labels (alias, fuzzy_amount, false_positive_bait, temporal).
# Raise after you document stable baselines for the production model.
RECALL_THRESHOLD = 0.55
PRECISION_THRESHOLD = 0.45


@pytest.mark.eval
def test_duplicate_precision_recall(duplicate_pairs_labels, non_duplicate_pairs_labels):
    """Recall/precision vs labels; prints aggregate and per-tier breakdown (v1.4)."""
    txn_map: dict[tuple, str] = {}
    _pool: dict[str, object] = {}

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

    labeled_dup: list[tuple[str, str, str]] = []
    for pair in duplicate_pairs_labels:
        id_a = get_or_create_id(pair["transaction_a"])
        id_b = get_or_create_id(pair["transaction_b"])
        tier = pair.get("tier") or "unknown"
        labeled_dup.append((id_a, id_b, tier))

    labeled_non_dup: list[tuple[str, str, str]] = []
    for pair in non_duplicate_pairs_labels:
        id_a = get_or_create_id(pair["transaction_a"])
        id_b = get_or_create_id(pair["transaction_b"])
        tier = pair.get("tier") or "unknown"
        labeled_non_dup.append((id_a, id_b, tier))

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

    true_pos = sum(1 for a, b, _ in labeled_dup if same_cluster(a, b))
    false_neg = len(labeled_dup) - true_pos
    false_pos = sum(1 for a, b, _ in labeled_non_dup if same_cluster(a, b))

    recall = true_pos / len(labeled_dup) if labeled_dup else 1.0
    denom = true_pos + false_pos
    precision = true_pos / denom if denom > 0 else 1.0

    dup_tier_tp: defaultdict[str, int] = defaultdict(int)
    dup_tier_n: defaultdict[str, int] = defaultdict(int)
    for id_a, id_b, tier in labeled_dup:
        dup_tier_n[tier] += 1
        if same_cluster(id_a, id_b):
            dup_tier_tp[tier] += 1

    nondup_tier_fp: defaultdict[str, int] = defaultdict(int)
    nondup_tier_n: defaultdict[str, int] = defaultdict(int)
    for id_a, id_b, tier in labeled_non_dup:
        nondup_tier_n[tier] += 1
        if same_cluster(id_a, id_b):
            nondup_tier_fp[tier] += 1

    summary = [
        ["Labeled duplicate pairs", len(labeled_dup)],
        ["Labeled non-duplicate pairs", len(labeled_non_dup)],
        ["True positives", true_pos],
        ["False negatives (missed dups)", false_neg],
        ["False positives (wrong dups)", false_pos],
        ["Recall", f"{recall:.1%}"],
        ["Precision", f"{precision:.1%}"],
    ]

    dup_tier_rows = [
        [tier, dup_tier_tp[tier], dup_tier_n[tier], f"{dup_tier_tp[tier] / dup_tier_n[tier]:.1%}"]
        if dup_tier_n[tier]
        else [tier, dup_tier_tp[tier], dup_tier_n[tier], "—"]
        for tier in sorted(dup_tier_n.keys())
    ]

    nondup_tier_rows = [
        [
            tier,
            nondup_tier_fp[tier],
            nondup_tier_n[tier],
            f"{nondup_tier_fp[tier] / nondup_tier_n[tier]:.1%}" if nondup_tier_n[tier] else "—",
        ]
        for tier in sorted(nondup_tier_n.keys())
    ]

    print("\n" + tabulate(summary, tablefmt="github"))
    print("\nDuplicate recall by tier (labeled dup → same cluster)")
    print(tabulate(dup_tier_rows, headers=["tier", "tp", "n", "recall"], tablefmt="github"))
    print("\nFalse-positive rate by tier (labeled non-dup → wrongly same cluster)")
    print(
        tabulate(
            nondup_tier_rows,
            headers=["tier", "fp", "n", "fp_rate"],
            tablefmt="github",
        )
    )

    assert recall >= RECALL_THRESHOLD, (
        "Duplicate recall below threshold.\n" + tabulate(summary, tablefmt="simple")
    )
    assert precision >= PRECISION_THRESHOLD, (
        "Duplicate precision below threshold.\n" + tabulate(summary, tablefmt="simple")
    )
