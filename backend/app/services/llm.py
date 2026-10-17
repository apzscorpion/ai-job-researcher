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

from app.config import GEMINI_MODELS

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
    payload: Dict[str, Any] = {"model": model, "prompt": text}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{url}/api/embeddings", json=payload)
            resp.raise_for_status()
            data = resp.json()
            embedding = data.get("embedding", [])
            return [float(v) for v in embedding]
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


def _slot(api_key: str, model: str) -> str:
    return f"{api_key[-6:]}:{model}"


def _usable(api_key: str, models: List[str]) -> List[str]:
    now = time.monotonic()
    return [m for m in models if _blocked.get(_slot(api_key, m), 0.0) <= now]


def _block(api_key: str, model: str, seconds: float, why: str) -> None:
    _blocked[_slot(api_key, model)] = time.monotonic() + seconds
    logger.warning("AI %s (key …%s) %s — skipping it for %.1f h", model, api_key[-4:], why, seconds / 3600)


def providers_for(settings: Any) -> List[Dict[str, str]]:
    """Decrypted AI providers in the order they are tried: the main Gemini key, then the extra ones."""
    from app import crypto
    from app.models import json_decode

    found: List[Dict[str, str]] = []
    if settings.gemini_key_enc:
        found.append({"kind": "gemini", "name": "Gemini", "key": crypto.decrypt(settings.gemini_key_enc)})
    if settings.ai_providers_enc:
        found += [p for p in json_decode(crypto.decrypt(settings.ai_providers_enc), []) if p.get("key")]
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


async def _gemini_generate(api_key: str, contents: str, config=None) -> str:
    """Try each usable Gemini model for this key in turn; "" when none replied."""
    if not api_key:
        return ""
    from google import genai  # type: ignore
    from google.genai import errors  # type: ignore

    client = genai.Client(api_key=api_key)
    for model in _usable(api_key, GEMINI_MODELS):
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


async def gemini_search(query: str, api_key: str) -> str:
    """Use Gemini with Google Search grounding to answer a query; "" on failure."""
    from google.genai import types  # type: ignore

    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())],
                                         response_modalities=["TEXT"])
    return await _gemini_generate(api_key, query, config)


async def generate(prompt: str, providers: List[Dict[str, str]], ollama_model: str, ollama_url: str,
                   json_mode: bool = False, timeout: float = 300.0) -> Tuple[str, str]:
    """Try each cloud provider in order, then local Ollama. Returns (text, name of the AI that answered)."""
    for p in providers:
        if p["kind"] == "gemini":
            from google.genai import types  # type: ignore

            config = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
            text = await _gemini_generate(p["key"], prompt, config)
        else:
            text = await _openai_chat(p, prompt, json_mode)
        if text:
            return text, p.get("name") or p["kind"]
    text = await chat([{"role": "user", "content": prompt}], ollama_model, ollama_url, json_mode, timeout)
    return text, "local model"
