"""Reconciliation API routes."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Query, UploadFile

from services.reconciliation import get_status, run
from services.schemas import RunResult, RunStatus

router = APIRouter(tags=["reconciliation"])

# Track thread_id by run_id so GET /runs/{run_id} can look up the checkpoint.
# In-memory for now — acceptable for single-process showcase.
# v2.0: persist this mapping in the DB.
_run_threads: dict[str, str] = {}


@router.post("/", response_model=RunResult)
async def reconcile(
    files: list[UploadFile] = File(...),
    auto_approve: bool = Query(True, description="Skip human-in-the-loop interrupts"),
) -> RunResult:
    """Upload statement/receipt files and run the reconciliation pipeline.

    Files are saved to a temp directory and passed to the reconciliation graph.
    When auto_approve=True (default), both review interrupts are skipped.
    """
    # Save uploaded files to a temp directory
    upload_dir = Path(tempfile.mkdtemp(prefix="finance_api_uploads_"))
    file_paths: list[str] = []

    try:
        for f in files:
            if not f.filename:
                continue
            dest = upload_dir / f.filename
            with dest.open("wb") as buf:
                content = await f.read()
                buf.write(content)
            file_paths.append(str(dest.resolve()))

        if not file_paths:
            return RunResult(
                run_id="",
                thread_id="",
                status=RunStatus.FAILED,
                report="No valid files uploaded.",
            )

        result = run(file_paths=file_paths, auto_approve=auto_approve)

        # Cache the thread_id so GET /runs/{run_id} can find the checkpoint
        _run_threads[result.run_id] = result.thread_id

        return result

    finally:
        # Clean up temp files after processing
        shutil.rmtree(upload_dir, ignore_errors=True)


@router.get("/runs/{run_id}", response_model=RunResult)
async def get_run(run_id: str) -> RunResult:
    """Check the status and results of a reconciliation run."""
    thread_id = _run_threads.get(run_id)
    if not thread_id:
        return RunResult(
            run_id=run_id,
            thread_id="",
            status=RunStatus.FAILED,
            report=f"Unknown run_id: {run_id}. It may have been lost on server restart.",
        )

    return get_status(run_id=run_id, thread_id=thread_id)
