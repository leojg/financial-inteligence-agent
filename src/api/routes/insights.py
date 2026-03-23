"""Insights API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.insights import get_latest, run
from services.schemas import InsightsResponse

router = APIRouter(tags=["insights"])


@router.post("/", response_model=InsightsResponse)
async def insights(
    date_from: str | None = None,
    date_to: str | None = None,
    accounts: list[str] | None = None,
    force_recompute: bool = False,
) -> InsightsResponse:
    """Run the insights pipeline and return the result."""
    return run(
        date_from=date_from,
        date_to=date_to,
        accounts=accounts,
        force_recompute=force_recompute,
    )


@router.get("/latest", response_model=InsightsResponse)
async def get_latest_insights(
    accounts: list[str] | None = None,
) -> InsightsResponse:
    """Return the latest cached insights without re-running the pipeline."""
    result = get_latest(accounts=accounts)
    if result is None:
        raise HTTPException(
            status_code=404, detail="No cached insights found. Run the pipeline first."
        )

    return result
