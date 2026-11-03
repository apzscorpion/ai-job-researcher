"""
Claude Code as the site agent's brain: while the user's Claude Code session polls (the apply-jobs skill),
the agent's step prompts wait here for its answer instead of going to the AI keys / local model.

connected(user_id) -> bool                 a session polled in the last minute and a half
ask(user_id, prompt, timeout) -> str        "" when nobody answered in time (the caller falls back)
next_prompt(user_id, wait) -> (id, prompt)  oldest unanswered prompt, or None after `wait` seconds
answer(user_id, prompt_id, text) -> bool
"""

import asyncio
import itertools
import time
from typing import Dict, Optional, Tuple

_POLL_FRESH_S = 90
_ids = itertools.count(1)
_pending: Dict[int, Dict[str, Tuple[str, asyncio.Future]]] = {}  # user_id -> {id: (prompt, answer)}
_handed_out: Dict[int, set] = {}  # ids already given to the session (one reader per prompt)
_last_poll: Dict[int, float] = {}


def connected(user_id: int) -> bool:
    return time.monotonic() - _last_poll.get(user_id, -1e9) < _POLL_FRESH_S


async def ask(user_id: int, prompt: str, timeout: float = 180.0) -> str:
    prompt_id = str(next(_ids))
    fut = asyncio.get_running_loop().create_future()
    _pending.setdefault(user_id, {})[prompt_id] = (prompt, fut)
    try:
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        return ""
    finally:
        _pending.get(user_id, {}).pop(prompt_id, None)
        _handed_out.get(user_id, set()).discard(prompt_id)


async def next_prompt(user_id: int, wait: float = 50.0) -> Optional[Tuple[str, str]]:
    deadline = time.monotonic() + wait
    while True:
        _last_poll[user_id] = time.monotonic()
        given = _handed_out.setdefault(user_id, set())
        for prompt_id, (prompt, _fut) in list(_pending.get(user_id, {}).items()):
            if prompt_id not in given:
                given.add(prompt_id)
                return prompt_id, prompt
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(1.0)


def answer(user_id: int, prompt_id: str, text: str) -> bool:
    _prompt, fut = _pending.get(user_id, {}).get(prompt_id, (None, None))
    if fut is None or fut.done():
        return False
    fut.set_result(text)
    return True
