"""LLM suspicious-activity flagging eval against labeled synthetic data (v1.4 §2.4)."""

import pytest
from tabulate import tabulate

from agents.reconciliator.configuration import DEFAULT_CONFIG
from agents.reconciliator.nodes import make_flag_suspicious_node

from .helpers import make_categorized_transaction

# Baseline floors — tune after measuring the production model on this set.
RECALL_THRESHOLD = 0.35
PRECISION_THRESHOLD = 0.35


def _txn_matches_spec(t, spec: dict) -> bool:
    return (
        t.date == spec["date"]
        and t.merchant == spec["merchant"]
        and abs(float(t.amount_original) - float(spec["amount"])) < 0.01
        and t.account == spec["account"]
    )


def _assign_ids_for_specs(
    pool: list,
    specs: list[dict],
    consumed: set[str],
) -> list[str]:
    """Match label rows to pool transactions in order (supports identical rapid-fire rows)."""
    ids: list[str] = []
    for spec in specs:
        for t in pool:
            if t.id in consumed:
                continue
            if _txn_matches_spec(t, spec):
                ids.append(t.id)
                consumed.add(t.id)
                break
    return ids


def _resolve_suspicious_eval_ids(categorization_labels, suspicious_labels):
    """Map eval_labels suspicious section to concrete transaction ids (no LLM)."""
    pool = [make_categorized_transaction(row) for row in categorization_labels]
    consumed: set[str] = set()

    should_flag_specs = suspicious_labels.get("should_flag") or []
    labeled_flag_ids: list[str] = []
    pattern_ids: dict[str, list[str]] = {}
    for block in should_flag_specs:
        pattern = block.get("pattern") or "unknown"
        txs = block.get("transactions") or []
        ids = _assign_ids_for_specs(pool, txs, consumed)
        pattern_ids[pattern] = ids
        labeled_flag_ids.extend(ids)

    should_not = suspicious_labels.get("should_not_flag") or []
    labeled_safe_ids: list[str] = []
    for row in should_not:
        spec = {k: v for k, v in row.items() if k != "note"}
        ids = _assign_ids_for_specs(pool, [spec], consumed)
        if ids:
            labeled_safe_ids.append(ids[0])

    return {
        "pool": pool,
        "labeled_flag_ids": labeled_flag_ids,
        "pattern_ids": pattern_ids,
        "labeled_safe_ids": labeled_safe_ids,
    }


@pytest.mark.eval
def test_suspicious_label_resolution_matches_eval_cardinality(
    categorization_labels,
    suspicious_labels,
):
    """Sanity: labels line up with categorization rows (incl. five rapid-fire ids)."""
    r = _resolve_suspicious_eval_ids(categorization_labels, suspicious_labels)
    assert len(r["labeled_flag_ids"]) == 7
    assert len(r["labeled_safe_ids"]) == 4
    assert len(r["pattern_ids"].get("outlier_amount") or []) == 1
    assert len(r["pattern_ids"].get("rapid_fire") or []) == 5
    assert len(r["pattern_ids"].get("round_number") or []) == 1


@pytest.mark.eval
def test_suspicious_precision_recall(categorization_labels, suspicious_labels):
    """Recall on should_flag txns; precision on should_not_flag; per-pattern floor (one LLM run)."""
    r = _resolve_suspicious_eval_ids(categorization_labels, suspicious_labels)
    pool = r["pool"]
    labeled_flag_ids = r["labeled_flag_ids"]
    pattern_ids = r["pattern_ids"]
    labeled_safe_ids = r["labeled_safe_ids"]

    state = {
        "source_folder": "data",
        "raw_documents": [],
        "transactions": pool,
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None,
    }

    result = make_flag_suspicious_node(DEFAULT_CONFIG)(state)
    flagged_ids = {t.id for t in result["suspicious"]}

    tp = sum(1 for i in labeled_flag_ids if i in flagged_ids)
    fn = len(labeled_flag_ids) - tp
    fp = sum(1 for i in labeled_safe_ids if i in flagged_ids)
    tn = len(labeled_safe_ids) - fp

    recall = tp / len(labeled_flag_ids) if labeled_flag_ids else 1.0
    safe_precision = tn / len(labeled_safe_ids) if labeled_safe_ids else 1.0

    pattern_recall_rows = []
    for pattern, ids in sorted(pattern_ids.items()):
        n = len(ids)
        hit = sum(1 for i in ids if i in flagged_ids) if n else 0
        rate = f"{hit / n:.1%}" if n else "—"
        pattern_recall_rows.append([pattern, hit, n, rate])

    summary = [
        ["Labeled should-flag txns", len(labeled_flag_ids)],
        ["Labeled should-not-flag txns", len(labeled_safe_ids)],
        ["True positives (flagged as expected)", tp],
        ["False negatives (missed flags)", fn],
        ["False positives (safe but flagged)", fp],
        ["True negatives (safe, not flagged)", tn],
        ["Recall (should_flag)", f"{recall:.1%}"],
        ["Precision (should_not_flag: safe & not flagged)", f"{safe_precision:.1%}"],
    ]

    print("\n" + tabulate(summary, tablefmt="github"))
    print("\nRecall by pattern (labeled should-flag → flagged)")
    print(
        tabulate(
            pattern_recall_rows,
            headers=["pattern", "hit", "n", "recall"],
            tablefmt="github",
        )
    )

    assert recall >= RECALL_THRESHOLD, (
        "Suspicious recall below threshold.\n" + tabulate(summary, tablefmt="simple")
    )
    assert safe_precision >= PRECISION_THRESHOLD, (
        "Suspicious safe precision below threshold.\n" + tabulate(summary, tablefmt="simple")
    )
