"""
Single source for paths and environment settings. Import this before reading os.environ.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

# Hosting: point DATA_DIR at a persistent volume (database, résumés, browser sessions live here)
DATA_DIR = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else REPO_ROOT / "data"
RESUMES_DIR = DATA_DIR / "resumes"
BROWSERS_DIR = DATA_DIR / "browsers"
RESUMES_DIR.mkdir(parents=True, exist_ok=True)
BROWSERS_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
# Tried in order; free-tier quota is per model, so each extra model adds its own daily requests
GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODELS", "gemini-3.8-flash,gemini-3.5-flash,gemini-flash-lite-latest").split(",") if m.strip()]
