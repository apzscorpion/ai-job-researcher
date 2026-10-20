"""
LLM integration helpers.

Ollama:
    chat(messages, model, url) -> str
    embed(text, model, url) -> list[float]

Cloud AI:
    providers_for(settings) -> list[provider]   — decrypted Gemini / OpenAI-compatible keys
    gemini_search(query, api_key) -> str        — Gemini with Google Search grounding

generate(prompt, providers, ollama_model, ollama_url) -> (text, ai_name)
    — each cloud provider in order (skipping ones out of quota), local Ollama last
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import GEMINI_LITE_MODELS, GEMINI_MODELS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ollama helpers
# ---------------------------------------------------------------------------

async def chat(messages: List[Dict[str, str]], model: str, url: str, json_mode: bool = False,
               timeout: float = 300.0) -> str:
    """Send a chat request to the Ollama API.

    Returns the assistant message content, or an empty string on failure.
    """
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,  # qwen3-style models otherwise spend minutes "thinking"
    }
    if json_mode:
        payload["format"] = "json"
    try:
        # CPU-only Ollama can take minutes per scoring prompt
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "")
    except httpx.HTTPError as exc:
        logger.warning("ollama.chat HTTP error: %s", exc)
        return ""
    except Exception:
        logger.exception("ollama.chat unexpected error")
        return ""


async def embed(text: str, model: str, url: str) -> List[float]:
    """Get a text embedding vector from the Ollama embeddings API.

    Returns an empty list on failure.
    """
    if not text.strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # /api/embed trims text longer than the model's context (some job posts are), instead of failing
            resp = await client.post(f"{url}/api/embed", json={"model": model, "input": text, "truncate": True})
            if resp.status_code == 404:  # older Ollama: the previous endpoint, with the text shortened by hand
                resp = await client.post(f"{url}/api/embeddings", json={"model": model, "prompt": text[:2000]})
                resp.raise_for_status()
                return [float(v) for v in resp.json().get("embedding", [])]
            resp.raise_for_status()
            embeddings = resp.json().get("embeddings") or [[]]
            return [float(v) for v in embeddings[0]]
    except httpx.HTTPError as exc:
        logger.warning("ollama.embed HTTP error: %s", exc)
        return []
    except Exception:
        logger.exception("ollama.embed unexpected error")
        return []

# ---------------------------------------------------------------------------
# Cloud providers (Gemini + OpenAI-compatible APIs)
# ---------------------------------------------------------------------------

# "<key suffix>:<model>" -> monotonic time until which it is skipped (free tiers have daily quotas)
_blocked: Dict[str, float] = {}
_block_why: Dict[str, str] = {}


def _slot(api_key: str, model: str) -> str:
    return f"{api_key[-6:]}:{model}"


def _usable(api_key: str, models: List[str]) -> List[str]:
    now = time.monotonic()
    return [m for m in models if _blocked.get(_slot(api_key, m), 0.0) <= now]


def _block(api_key: str, model: str, seconds: float, why: str) -> None:
    _blocked[_slot(api_key, model)] = time.monotonic() + seconds
    _block_why[_slot(api_key, model)] = why
    logger.warning("AI %s (key …%s) %s — skipping it for %.1f h", model, api_key[-4:], why, seconds / 3600)


_HOST_NAMES = {"openrouter.ai": "OpenRouter", "groq.com": "Groq", "openai.com": "OpenAI", "mistral.ai": "Mistral",
               "deepseek.com": "DeepSeek", "together.xyz": "Together", "fireworks.ai": "Fireworks"}


def _provider_name(p: Dict[str, str]) -> str:
    """What the provider is ("Gemini", "OpenRouter · gemma"), not the user's label for the key."""
    if p.get("kind") == "gemini":
        return "Gemini"
    host = (p.get("base_url") or "").split("/")[2] if "//" in (p.get("base_url") or "") else ""
    name = next((v for k, v in _HOST_NAMES.items() if host.endswith(k)), host or "AI")
    model = (p.get("model") or "").split("/")[-1].split(":")[0].split("-")[0]
    return f"{name} · {model}" if model else name


def quota_status(settings: Any) -> List[Dict[str, Any]]:
    """Per AI provider: which models are skipped right now, why, and for how many more minutes."""
    now = time.monotonic()
    out = []
    for p in providers_for(settings):
        models = GEMINI_MODELS + GEMINI_LITE_MODELS if p.get("kind") == "gemini" else [p.get("model", "")]
        blocked = [{"model": m, "why": _block_why.get(_slot(p["key"], m), ""),
                    "minutes": int((_blocked[_slot(p["key"], m)] - now) // 60) + 1}
                   for m in models if _blocked.get(_slot(p["key"], m), 0.0) > now]
        out.append({"name": _provider_name(p), "label": p.get("name") or "", "models": len(models),
                    "blocked": blocked, "out": len(blocked) == len(models)})
    return out


AI_MODES = ("full_ai", "semi_ai", "local_plus_ai", "local_only")


def ai_mode(settings: Any) -> str:
    """How much of the work goes to cloud AI keys vs. models running on this computer (Settings → AI mode)."""
    mode = getattr(settings, "ai_mode", None)
    if mode in AI_MODES:
        return mode
    return "full_ai" if getattr(settings, "use_gemini_scoring", False) else "semi_ai"


def local_first(settings: Any) -> bool:
    """Quick text tasks (form answers, résumé reports) try the local model before the cloud keys."""
    return ai_mode(settings) == "local_plus_ai"


def providers_for(settings: Any) -> List[Dict[str, str]]:
    """Decrypted AI providers in the order they are tried: the main Gemini key, then the extra ones.

    Empty in "only local models" mode, so nothing is ever sent to a cloud AI.
    """
    if ai_mode(settings) == "local_only":
        return []
    from app import crypto
    from app.models import json_decode

    found: List[Dict[str, str]] = []
    if settings.gemini_key_enc:
        found.append({"kind": "gemini", "name": "Gemini", "key": crypto.decrypt(settings.gemini_key_enc)})
    if settings.ai_providers_enc:
        found += [p for p in json_decode(crypto.decrypt(settings.ai_providers_enc), [])
                  if p.get("key") and p.get("active", True)]  # switched-off keys are never used
    return [p for p in found if p.get("key")]


async def _generate(client, **kwargs):
    """generate_content with short retries: Gemini often returns 503 "high demand" for a few seconds."""
    from google.genai import errors  # type: ignore

    for delay in (2, 6):
        try:
            return await asyncio.to_thread(client.models.generate_content, **kwargs)
        except errors.ServerError as exc:
            logger.info("gemini %s busy (%s), retrying in %ss", kwargs.get("model"), exc.code, delay)
            await asyncio.sleep(delay)
    return await asyncio.to_thread(client.models.generate_content, **kwargs)


async def _gemini_generate(api_key: str, contents: str, config=None, models: Optional[List[str]] = None) -> str:
    """Try each usable Gemini model for this key in turn; "" when none replied."""
    if not api_key:
        return ""
    from google import genai  # type: ignore
    from google.genai import errors  # type: ignore

    client = genai.Client(api_key=api_key)
    for model in _usable(api_key, models or GEMINI_MODELS + GEMINI_LITE_MODELS):
        try:
            response = await _generate(client, model=model, contents=contents, config=config)
            if response.text:
                return response.text
        except errors.ClientError as exc:
            if exc.code == 429:
                delay = re.search(r"'retryDelay': '(\d+)s'", str(exc))
                _block(api_key, model, int(delay.group(1)) if delay else 3600, "quota used up")
            elif exc.code in (401, 403):
                _block(api_key, model, 86400, "rejected the key")
            elif exc.code == 404:
                _block(api_key, model, 86400, "not found (retired?) — check GEMINI_MODELS")
            else:
                logger.warning("gemini %s failed: %s", model, exc)
        except errors.ServerError as exc:
            logger.warning("gemini %s still busy (%s), trying the next model", model, exc.code)
        except Exception:
            logger.exception("gemini %s failed", model)
    return ""


async def _openai_chat(p: Dict[str, str], prompt: str, json_mode: bool) -> str:
    """OpenAI-compatible /chat/completions (OpenAI, Groq, OpenRouter, Mistral, DeepSeek, ...)."""
    key, model = p["key"], p.get("model") or ""
    if not p.get("base_url") or not model or not _usable(key, [model]):
        return ""
    payload: Dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        from app.security import block_private_hook

        # re-checked on every call: DNS can change after the URL was saved
        async with httpx.AsyncClient(timeout=180.0, event_hooks={"request": [block_private_hook]}) as client:
            resp = await client.post(f"{p['base_url'].rstrip('/')}/chat/completions", json=payload,
                                     headers={"Authorization": f"Bearer {key}"})
        if resp.status_code == 429:
            try:
                wait = float(resp.headers.get("retry-after") or 0)
            except ValueError:
                wait = 0
            _block(key, model, wait or 3600, "rate-limited")
            return ""
        if resp.status_code in (401, 403):
            _block(key, model, 86400, "rejected the key")
            return ""
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or ""
    except Exception as exc:
        logger.warning("AI %s (%s) failed: %s", p.get("name") or model, p.get("base_url"), exc)
        return ""


_TEST_PROMPT = "Reply with exactly: OK"


def _explain(code: int, message: str) -> str:
    """The provider's error in plain words, keeping its own message."""
    what = {400: "bad request — often an invalid API key or model name", 401: "the key was rejected (invalid or revoked)",
            402: "payment required — no credits left on this account", 403: "the key isn't allowed to use this model",
            404: "model not found — check the model name", 429: "quota or rate limit used up",
            500: "provider error", 502: "provider unavailable", 503: "provider overloaded — try again shortly"}
    return f"{what.get(code, 'error')} (HTTP {code}): {message.strip()[:300]}"


async def test_provider(p: Dict[str, str]) -> List[Dict[str, Any]]:
    """One tiny live request per model of *p*; what the provider really answered (Settings → Test).

    A success also clears an earlier quota block, so the status header reflects it.
    """
    results: List[Dict[str, Any]] = []
    if p.get("kind") == "gemini":
        from google import genai  # type: ignore
        from google.genai import errors  # type: ignore

        client = genai.Client(api_key=p["key"])
        # the two strongest and two lite models: a test shouldn't burn a request on all of them
        for model in GEMINI_MODELS[:2] + GEMINI_LITE_MODELS[:2]:
            start = time.monotonic()
            try:
                resp = await asyncio.wait_for(asyncio.to_thread(
                    client.models.generate_content, model=model, contents=_TEST_PROMPT), timeout=60)
                ms = int((time.monotonic() - start) * 1000)
                usage = getattr(resp, "usage_metadata", None)
                results.append({"model": model, "ok": True, "status": 200, "ms": ms, "reply": (resp.text or "").strip()[:200],
                                "tokens": getattr(usage, "total_token_count", None)})
                _blocked.pop(_slot(p["key"], model), None)
            except errors.APIError as exc:
                detail = _explain(exc.code or 0, getattr(exc, "message", "") or str(exc))
                retry = re.search(r"retryDelay': '(\d+)s'", str(exc))
                metric = re.search(r"Quota exceeded for metric: ([\w./-]+)", str(exc))
                if metric:
                    detail += f" [quota: {metric.group(1)}]"
                if "free_tier" in str(exc):
                    detail += (" — this key is on Google's FREE tier. A Gemini Pro / Google One subscription "
                               "doesn't cover the API: turn on billing for this key's project in AI Studio "
                               "(Get API key → Set up billing) to lift the limit")
                if retry:
                    s = int(retry.group(1))
                    detail += f" — resets in {s // 3600} h {s % 3600 // 60} min" if s >= 3600 else f" — retry in {s} s"
                if exc.code == 429:
                    _block(p["key"], model, int(retry.group(1)) if retry else 3600, "quota used up")
                results.append({"model": model, "ok": False, "status": exc.code, "error": detail})
            except asyncio.TimeoutError:
                results.append({"model": model, "ok": False, "status": None, "error": "no answer within 60 s"})
            except Exception as exc:
                results.append({"model": model, "ok": False, "status": None, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        return results

    from app.security import block_private_hook

    model = p.get("model") or ""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60.0, event_hooks={"request": [block_private_hook]}) as client:
            resp = await client.post(f"{p.get('base_url', '').rstrip('/')}/chat/completions",
                                     json={"model": model, "messages": [{"role": "user", "content": _TEST_PROMPT}]},
                                     headers={"Authorization": f"Bearer {p['key']}"})
        ms = int((time.monotonic() - start) * 1000)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code == 200 and body.get("choices"):
            _blocked.pop(_slot(p["key"], model), None)
            return [{"model": body.get("model") or model, "ok": True, "status": 200, "ms": ms,
                     "reply": (body["choices"][0]["message"].get("content") or "").strip()[:200],
                     "tokens": (body.get("usage") or {}).get("total_tokens")}]
        err = body.get("error")
        message = (err.get("message") if isinstance(err, dict) else err) or resp.text[:300]
        raw = ((err.get("metadata") or {}).get("raw") if isinstance(err, dict) else None)
        if raw:  # OpenRouter puts the upstream provider's real reason here
            message = f"{message} — {str(raw)[:240]}"
        detail = _explain(resp.status_code, str(message))
        if resp.status_code == 429:
            wait = resp.headers.get("retry-after")
            detail += f" — retry in {wait} s" if wait else ""
            _block(p["key"], model, float(wait) if wait and wait.isdigit() else 3600, "rate-limited")
        return [{"model": model, "ok": False, "status": resp.status_code, "error": detail}]
    except httpx.HTTPError as exc:
        return [{"model": model, "ok": False, "status": None, "error": f"couldn't reach the provider: {str(exc)[:300]}"}]


async def key_info(p: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """What the provider itself says about a key, where it says anything (OpenRouter only so far).

    Gemini / OpenAI-style keys have no endpoint naming the account behind them.
    """
    if "openrouter.ai" not in (p.get("base_url") or ""):
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {p['key']}"})
        data = resp.json().get("data") or {}
    except (httpx.HTTPError, ValueError):
        return None
    if not data:
        return None
    return {"label": data.get("label"), "free_tier": data.get("is_free_tier"),
            "usage_usd": data.get("usage"), "limit_usd": data.get("limit"),
            "limit_remaining_usd": data.get("limit_remaining")}


async def gemini_search(query: str, api_key: str) -> str:
    """Use Gemini with Google Search grounding to answer a query; "" on failure."""
    from google.genai import types  # type: ignore

    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())],
                                         response_modalities=["TEXT"])
    return await _gemini_generate(api_key, query, config)


async def generate(prompt: str, providers: List[Dict[str, str]], ollama_model: str, ollama_url: str,
                   json_mode: bool = False, timeout: float = 300.0, light: bool = False,
                   prefer_local: bool = False) -> Tuple[str, str]:
    """Try each cloud provider in order, then local Ollama. Returns (text, name of the AI that answered).

    light=True (scoring, form answers): Gemini's Flash-Lite models first, keeping the stronger Flash quota
    for cover letters, emails and the site agent.
    """
    gemini_models = GEMINI_LITE_MODELS + GEMINI_MODELS if light else GEMINI_MODELS + GEMINI_LITE_MODELS
    if prefer_local and providers:  # "local models + AI key": this computer first, the cloud as backup
        text = await chat([{"role": "user", "content": prompt}], ollama_model, ollama_url, json_mode, timeout)
        if text:
            return text, "local model"
    for p in providers:
        if p["kind"] == "gemini":
            from google.genai import types  # type: ignore

            config = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
            text = await _gemini_generate(p["key"], prompt, config, gemini_models)
        else:
            text = await _openai_chat(p, prompt, json_mode)
        if text:
            return text, p.get("name") or p["kind"]
    text = await chat([{"role": "user", "content": prompt}], ollama_model, ollama_url, json_mode, timeout)
    return text, "local model"
