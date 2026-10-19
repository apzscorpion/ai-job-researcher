"""
FastAPI application entry-point for AI Job Researcher.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
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

_SHOW_DOCS = os.environ.get("API_DOCS", "").lower() in ("1", "true", "yes")  # off in production
app = FastAPI(
    title="AI Job Researcher",
    description="Automated job discovery, scoring, and application backend.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _SHOW_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _SHOW_DOCS else None,
)


_MAX_BODY = 1024 * 1024  # 1 MB; résumé uploads get 6 MB (5 MB file + form overhead)


@app.middleware("http")
async def security_headers(request, call_next):
    from fastapi.responses import JSONResponse

    limit = 6 * _MAX_BODY if request.url.path == "/api/profile/resume" else _MAX_BODY
    if int(request.headers.get("content-length") or 0) > limit:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")  # API answers hold personal data
    if not (_SHOW_DOCS and request.url.path == "/docs"):
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    # Production: set ALLOWED_ORIGINS=https://your-domain in .env (comma-separated)
    allow_origins=[o.strip() for o in os.environ.get(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",") if o.strip()],
    allow_credentials=False,  # auth is a bearer header, not cookies
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
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
for log_name in ["", "uvicorn", "uvicorn.access", "uvicorn.error"]:  # "app.*" reaches "" by propagation
    _l = logging.getLogger(log_name)
    _l.addHandler(_buffer_handler)
    _l.setLevel(logging.INFO)


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


_ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


@app.get("/api/logs", tags=["meta"])
def get_logs(current_user=Depends(get_current_user)) -> dict:
    # Server logs mix every user's activity: only the operator (ADMIN_EMAILS in .env) may read them
    if current_user.email.lower() not in _ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Server logs are only available to the app's admin")
    return {"logs": list(_LOG_BUFFER)}

