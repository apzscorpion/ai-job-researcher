# AI Job Researcher

A local, multi-user web app that finds jobs, scores them against your resume, and applies automatically via LinkedIn Easy Apply, Naukri, and direct recruiter email.

## Stack

- **Backend**: FastAPI · SQLite (SQLModel) · APScheduler · Playwright
- **Frontend**: Vite + React + React Router · Plain CSS
- **LLM**: Ollama (`qwen2.5:7b` + `nomic-embed-text`) · Gemini (optional, web search grounding)

## Quick Start

### 1. Prerequisites

- Python 3.12
- Node 22
- [Ollama](https://ollama.ai) running locally

```powershell
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

### 2. Backend

```powershell
cd D:\Repos\ai-job-researcher
python -m venv .venv
.venv\Scripts\Activate.ps1

pip install fastapi uvicorn[standard] sqlmodel "passlib[bcrypt]" pyjwt cryptography python-multipart pypdf python-docx httpx beautifulsoup4 python-jobspy google-genai apscheduler playwright pytest pytest-asyncio numpy

playwright install chromium
```

Copy and configure environment:
```powershell
Copy-Item .env.example .env
# Edit .env — generate SECRET_KEY and FERNET_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run the backend:
```powershell
cd backend
uvicorn app.main:app --reload
```

### 3. Frontend

```powershell
cd D:\Repos\ai-job-researcher\frontend
npm install
npm run dev
```

App opens at **http://localhost:5173**

### 4. Tests

```powershell
cd D:\Repos\ai-job-researcher\backend
pytest tests/ -v
```

| Feature | Status |
|---------|--------|
| Multi-user auth (JWT) | ✅ v1 |
| Resume upload + Ollama parsing | ✅ v1 |
| Job search (JobSpy + Infopark/Technopark + Gemini) | ✅ v1 |
| LLM scoring with relaxation toggles | ✅ v1 |
| Email apply (SMTP, LLM-drafted) | ✅ v1 |
| LinkedIn Easy Apply (Playwright) | ✅ v1 |
| Naukri Apply (Playwright) | ✅ v1 |
| Human pacing (random gaps, daily cap) | ✅ v1 |
| SMTP test & Gemini key storage (encrypted) | ✅ v1 |
| Applications page with filters | ✅ v1 |
| Manual status update | ✅ v1 |

## Roadmap (v2 to v4)

### v2 — Close the loop (replies + quality)
- **IMAP reply reader**: Connect to Gmail/IMAP via app password. LLM classifies replies (interview / rejection / question / other), updates application status automatically, and highlights interviews on the dashboard.
- **Follow-up emails**: Automatic, polite follow-up after $N$ days without response (opt-in, daily-capped).
- **Per-job resume tailoring**: LLM customizes summary and bullet points per job; generates a per-application PDF without altering the master resume.
- **Recruiter email discovery**: Hunter.io / Apollo integration + heuristic pattern guessing (`first.last@company.com`) + MX check.
- **Digest notifications**: Daily digest email or browser notifications of applications, replies, and interviews.
- **Dashboard analytics**: Trend graphs (applies per day, reply rate by platform, score vs reply rate).

### v3 — Reach + smarter matching
- **External ATS auto-apply**: Playwright + LLM vision/DOM agent for Greenhouse, Lever, Workday, and custom career portals.
- **Additional job sources**: Wellfound (AngelList), Instahyre, Cutshort, Foundit, and company career page watchlists.
- **Outcome-driven learning**: Adaptive logistic regression scoring model trained on individual user outcomes (interview callbacks vs rejections).
- **Interview preparation**: Auto-generate targeted interview questions and company research briefs for interview-flagged positions.
- **Company intelligence**: Glassdoor / AmbitionBox sentiment and rating synthesis cached per company via Gemini.

### v4 — Hardening & production deployment
- **Containerization**: Full Docker Compose stack (FastAPI backend, Vite/Nginx frontend, local Ollama or cloud LLM proxy, PostgreSQL).
- **Distributed workers**: Decouple scheduler into Celery / RQ worker queue with Redis for scaled, fault-tolerant background execution.
- **PWA & mobile interface**: Responsive progressive web app for phone status tracking and manual approval triage.
- **Audit & Compliance**: Activity log exports, rate-limit governance, and credential health monitoring.

## Risks

- LinkedIn/Naukri automation breaks their ToS and can trigger account restrictions. Pacing lowers risk but doesn't eliminate it. Selectors change frequently — `browser_apply.py` will need upkeep.
- Automatic email sends go out unseen. The score threshold and daily cap are your only guards.
- Local 7B model quality is moderate. Enable Gemini scoring in Settings for better results.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | JWT signing secret (≥32 chars) |
| `FERNET_KEY` | Fernet encryption key for SMTP password and Gemini key |
| `OLLAMA_URL` | Ollama base URL (default: http://localhost:11434) |
| `OLLAMA_MODEL` | Chat model (default: qwen2.5:7b) |
| `EMBED_MODEL` | Embedding model (default: nomic-embed-text) |


## Releasing publicly

- Fill `SECRET_KEY` and `FERNET_KEY` in `.env` (the app won't start without them), set `ALLOWED_ORIGINS`
  to your site's address and `ADMIN_EMAILS` to the operator's login. Leave `API_DOCS=0`.
- Build the frontend (`npm run build`) and serve `frontend/dist` plus `/api` (proxied to uvicorn on
  127.0.0.1:8000) from one HTTPS host, e.g. Caddy or nginx. Send the same Content-Security-Policy as
  `vite.config.js` as a header, plus `frame-ancestors 'none'`.
- Run uvicorn with a single worker (rate limits and browser locks live in memory) and
  `--proxy-headers --forwarded-allow-ips 127.0.0.1` so login limits see real client IPs.
- "Connect" on Settings → Job sites opens a browser window **on the server**, so job-site logins only
  work when the server is the user's own computer.

## Running the public site from this PC

The website is hosted on Vercel (project `ai-job-researcher-app`, https://ai-job-researcher-app.vercel.app);
the backend runs on this PC and is reached through ngrok at https://equation-diligent-quaintly.ngrok-free.dev.

- Start both with `powershell -ExecutionPolicy Bypass -File scripts\start-public.ps1` and keep the window open.
- Redeploy the website after frontend changes: `cd frontend; npx vercel deploy --prod`.
- The PC must stay on and awake; every user's searches and applications go out from its internet connection.
