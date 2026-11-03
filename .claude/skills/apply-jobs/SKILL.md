---
name: apply-jobs
description: Be the brain of the app's job-application agent - answer its form-filling steps so the app applies to queued jobs. Use when the user says "apply jobs", "/apply-jobs", or asks Claude Code to drive the applying.
---

# Apply jobs (you are the agent's brain)

The app (backend on port 8000, auto-apply on) opens each job page in its own browser. For every step it
sends a prompt: the job, the candidate, the page's visible text and elements, and the exact JSON reply
format. You answer that JSON; the app does the clicking and typing. Nothing else is needed from you.

## Loop

1. Run: `.venv/Scripts/python.exe scripts/brain.py next` (from the repo root; it waits up to ~50 s).
2. Output `NONE`: nothing waiting. Run `next` again. After 10 `NONE` in a row, stop and tell the user
   the queue is idle (daily cap reached, outside apply hours, or auto-apply is off).
3. Output `ID <id>` + a prompt: decide the step exactly as the prompt asks, then send ONLY the JSON:
   ```
   .venv/Scripts/python.exe scripts/brain.py answer <id> <<'EOF'
   {"status": "...", "reason": "...", "actions": [...]}
   EOF
   ```
4. Go back to 1. Keep going until the user stops you.

## Rules (keep tokens low)

- No commentary between steps, no reading files, no summaries. One `next`, one `answer`, repeat.
- Follow the prompt's own rules and JSON format; they are the source of truth.
- Never invent experience, degrees or employers. Use the candidate data in the prompt.
- Captcha, login/OTP or payment: reply `"status": "need_human"`; don't try to get around it.
- `LATE` after an answer means the step timed out and the app moved on; just continue the loop.
- Every ~25 steps, print one line: how many steps answered.
