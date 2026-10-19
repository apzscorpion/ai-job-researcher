# Backend for Railway (or any Docker host). Playwright's image ships Chromium + its system libraries;
# the tag must match playwright== in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.47.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

# Applying runs real (non-headless) browser windows, which LinkedIn/Naukri tolerate; Xvfb gives them a screen
RUN apt-get update && apt-get install -y --no-install-recommends xvfb && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt
COPY app ./app

# One worker: rate limits, browser locks and the scheduler live in this process.
# Mount a volume at /data so the database, résumés and browser sessions survive redeploys.
CMD xvfb-run -a --server-args="-screen 0 1366x900x24" \
    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips="*"
