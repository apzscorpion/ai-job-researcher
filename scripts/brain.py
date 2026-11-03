"""Bridge for the apply-jobs Claude Code skill: read the site agent's next step prompt, send back the answer.

  python scripts/brain.py next            -> "ID <id>" then the prompt, or "NONE" after ~50 s with nothing
  python scripts/brain.py answer <id>     -> reads the JSON answer from stdin

Signs in as the first ADMIN_EMAILS account (or --email) with the server's own key: run it on the app's PC.
API defaults to http://127.0.0.1:8000 (env API).
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.stdout.reconfigure(encoding="utf-8")


def _token(email: str) -> str:
    from sqlmodel import Session, select

    from app.auth import create_token
    from app.db import engine
    from app.models import User

    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
    if not user:
        sys.exit(f"No user {email}")
    return create_token(user.id)


def _call(path: str, token: str, body=None):
    req = urllib.request.Request(os.environ.get("API", "http://127.0.0.1:8000") + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=70) as resp:
        return json.load(resp)


def main() -> None:
    args = sys.argv[1:]
    email = args[args.index("--email") + 1] if "--email" in args else None
    import app.config  # noqa: F401  (loads .env)

    token = _token(email or os.environ.get("ADMIN_EMAILS", "").split(",")[0].strip())
    if args[:1] == ["next"]:
        got = _call("/api/brain/next?wait=50", token)
        print(f"ID {got['id']}\n{got['prompt']}" if got["id"] else "NONE")
    elif args[:1] == ["answer"] and len(args) >= 2:
        try:
            _call(f"/api/brain/answer/{args[1]}", token, {"text": sys.stdin.read()})
            print("OK")
        except urllib.error.HTTPError as exc:
            print(f"LATE ({exc.code}): the step timed out; the app used its own AI for it")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
