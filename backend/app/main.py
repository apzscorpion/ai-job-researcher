"""
FastAPI application entry-point for AI Job Researcher.
"""

from __future__ import annotations

import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.config  # noqa: F401  (loads .env, creates data dirs)
from app.db import create_db_and_tables

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    # Create DB tables
    create_db_and_tables()
    logger.info("Database tables created / verified.")

    # Start background scheduler
    from app.services.scheduler import start_scheduler
    start_scheduler()
    logger.info("APScheduler started.")

    yield

    # ---- shutdown ----
    from app.services.scheduler import stop_scheduler
    stop_scheduler()
    logger.info("APScheduler stopped.")


# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Job Researcher",
    description="Automated job discovery, scoring, and application backend.",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from app.auth import get_current_user  # noqa: E402
from app.routers import auth, profile, settings, jobs, applications, notifications  # noqa: E402

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(settings.router)
app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(notifications.router)


# ---------------------------------------------------------------------------
# Health check & Live Logs
# ---------------------------------------------------------------------------

import collections
_LOG_BUFFER: collections.deque = collections.deque(maxlen=300)

class BufferLogHandler(logging.Handler):
    def emit(self, record):
        try:
            entry = {
                "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "level": record.levelname,
                "name": record.name,
                "message": self.format(record),
            }
            _LOG_BUFFER.append(entry)
        except Exception:
            pass

_buffer_handler = BufferLogHandler()
_buffer_handler.setFormatter(logging.Formatter("%(message)s"))
for log_name in ["", "app", "uvicorn", "uvicorn.access", "uvicorn.error"]:
    _l = logging.getLogger(log_name)
    _l.addHandler(_buffer_handler)
    _l.setLevel(logging.INFO)


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/logs", tags=["meta"], dependencies=[Depends(get_current_user)])
def get_logs() -> dict:
    return {"logs": list(_LOG_BUFFER)}

