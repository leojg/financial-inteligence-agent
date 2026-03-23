"""Reconciliation service — compiles and invokes the reconciliation agent."""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from agents.reconciliator.configuration import DEFAULT_CONFIG
from agents.reconciliator.graph import make_graph
from agents.reconciliator.state import initial_state
from services.schemas import RunResult, RunStatus
from shared.db import get_checkpointer

logger = logging.getLogger(__name__)


# Two compiled variants: one with interrupts (interactive), one without (auto-approve).
_graph_interactive: Any = None
_graph_auto_approve: Any = None
_checkpointer: Any = None


def _get_checkpointer() -> Any:
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = get_checkpointer()
    return _checkpointer


def _get_graph(auto_approve: bool = True) -> Any:
    global _graph_interactive, _graph_auto_approve
    checkpointer = _get_checkpointer()

    if auto_approve:
        if _graph_auto_approve is None:
            _graph_auto_approve = make_graph(
                DEFAULT_CONFIG, checkpointer=checkpointer, interrupt_before=None
            )
        return _graph_auto_approve
    else:
        if _graph_interactive is None:
            _graph_interactive = make_graph(DEFAULT_CONFIG, checkpointer=checkpointer)
        return _graph_interactive


def run(
    file_paths: list[str] | None = None,
    files: list[tuple[str, bytes]] | None = None,
    auto_approve: bool = True,
) -> RunResult:
    """Invoke the reconciliation graph on the given files.

    Args:
        file_paths: Paths to statement/receipt files on disk.
        files: List of (filename, content) tuples to ingest. Written to a temp
            directory internally; the graph receives paths and never knows the difference.
        auto_approve: When True, skip human-in-the-loop interrupts.

    Returns:
        RunResult with run_id, status, and summary when complete.

    Raises:
        ValueError: If neither file_paths nor files is provided, or if both are provided.
    """
    if bool(file_paths) == bool(files):
        raise ValueError("Exactly one of file_paths or files must be provided")

    tmpdir_path: str | None = None
    try:
        if files:
            tmpdir_path = tempfile.mkdtemp(prefix="finance_reconcile_")
            paths: list[str] = []
            for filename, content in files:
                dest = Path(tmpdir_path) / filename
                dest.write_bytes(content)
                paths.append(str(dest.resolve()))
            source_paths = paths
        else:
            source_paths = file_paths or []

        graph = _get_graph(auto_approve)
        thread_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        state = initial_state(source_paths)
        run_id = state["run_id"]
        try:
            graph.invoke(state, config=config)
            snapshot = graph.get_state(config)
            values = getattr(snapshot, "values", {}) or {}
            next_nodes = list(getattr(snapshot, "next", ()) or ())
            if next_nodes:
                return RunResult(
                    run_id=run_id,
                    thread_id=thread_id,
                    status=RunStatus.AWAITING_REVIEW,
                    interrupt_at=next_nodes[0],
                )
            transactions = values.get("transactions", [])
            suspicious = [
                t if isinstance(t, dict) else t.model_dump()
                for t in transactions
                if (
                    t.get("suspicious")
                    if isinstance(t, dict)
                    else getattr(t, "suspicious", False)
                )
            ]
            duplicates = [
                t
                for t in transactions
                if (
                    t.get("duplicate_of")
                    if isinstance(t, dict)
                    else getattr(t, "duplicate_of", None)
                )
            ]
            return RunResult(
                run_id=run_id,
                thread_id=thread_id,
                status=RunStatus.COMPLETED,
                total_transactions=len(transactions),
                total_duplicates=len(duplicates),
                total_suspicious=len(suspicious),
                flagged_transactions=suspicious if suspicious else None,
                report=values.get("report"),
            )
        except Exception as e:
            logger.exception("Reconciliation failed for run_id=%s", run_id)
            return RunResult(
                run_id=run_id,
                thread_id=thread_id,
                status=RunStatus.FAILED,
                report=str(e),
            )
    finally:
        if tmpdir_path is not None:
            shutil.rmtree(tmpdir_path, ignore_errors=True)


def get_status(run_id: str, thread_id: str) -> RunResult:
    """Check the status of a reconciliation run.

    Uses the thread_id to look up the graph checkpoint state.
    """
    graph = _get_graph(auto_approve=False)
    config = {"configurable": {"thread_id": thread_id}}

    try:
        snapshot = graph.get_state(config)
        values = getattr(snapshot, "values", {}) or {}
        next_nodes = list(getattr(snapshot, "next", ()) or ())

        if next_nodes:
            return RunResult(
                run_id=run_id,
                thread_id=thread_id,
                status=RunStatus.AWAITING_REVIEW,
                interrupt_at=next_nodes[0],
            )

        transactions = values.get("transactions", [])
        if not transactions and not values.get("report"):
            return RunResult(
                run_id=run_id,
                thread_id=thread_id,
                status=RunStatus.RUNNING,
            )

        suspicious = [
            t if isinstance(t, dict) else t.model_dump()
            for t in transactions
            if (
                t.get("suspicious")
                if isinstance(t, dict)
                else getattr(t, "suspicious", False)
            )
        ]
        duplicates = [
            t
            for t in transactions
            if (
                t.get("duplicate_of")
                if isinstance(t, dict)
                else getattr(t, "duplicate_of", None)
            )
        ]

        return RunResult(
            run_id=run_id,
            thread_id=thread_id,
            status=RunStatus.COMPLETED,
            total_transactions=len(transactions),
            total_duplicates=len(duplicates),
            total_suspicious=len(suspicious),
            flagged_transactions=suspicious if suspicious else None,
            report=values.get("report"),
        )

    except Exception as e:
        logger.exception("Failed to get status for run_id=%s", run_id)
        return RunResult(
            run_id=run_id,
            thread_id=thread_id,
            status=RunStatus.FAILED,
            report=str(e),
        )
