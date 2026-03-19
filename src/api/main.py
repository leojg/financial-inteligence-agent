"""Finance Intelligence Agent — API."""
 
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.routes.chat import router as chat_router
from api.routes.insights import router as insights_router
from api.routes.reconciliation import router as reconciliation_router
from shared.db import get_session, run_migrations

logger = logging.getLogger(__name__)
 
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run migrations on startup, cleanup on shutdown."""
    logger.info("Running database migrations...")
    run_migrations()
    logger.info("API ready.")
    yield

app = FastAPI(
    title="Finance Intelligence Agent",
    version="1.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in v2.0 when auth is added
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(reconciliation_router, prefix="/reconciliation")
app.include_router(insights_router, prefix="/insights")
app.include_router(chat_router, prefix="/chat")

@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check with DB connectivity verification."""
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {e}"
 
    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "db": db_status,
    }