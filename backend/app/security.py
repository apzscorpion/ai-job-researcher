"""
Request-level protections shared by the routers and services.

rate_limit(key, limit, window_s)  — raise 429 after *limit* hits per *window_s* for *key* (in memory)
check_public_url(url, schemes)    — raise ValueError unless url uses an allowed scheme and resolves only to
                                    public internet addresses (blocks SSRF to localhost, LAN, cloud metadata)
http_url_or_none(url)             — the url if it's plain http(s), else None (blocks javascript: links)
block_private_hook                — httpx request hook: re-checks every request, including redirects
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Iterable, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, status

# shortcut: per-process memory, so limits reset on restart and aren't shared between workers;
# move to Redis if the app ever runs more than one worker.
_hits: Dict[str, Deque[float]] = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limit(key: str, limit: int, window_s: int, message: str = "Too many attempts — try again later") -> None:
    now = time.monotonic()
    with _hits_lock:
        q = _hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=message,
                                headers={"Retry-After": str(int(window_s - (now - q[0])) + 1)})
        q.append(now)


def _public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def check_public_host(host: Optional[str], port: Optional[int] = None) -> None:
    if not host:
        raise ValueError("missing host")
    try:
        infos = socket.getaddrinfo(host, port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"can't resolve {host}") from exc
    if not infos or not all(_public_ip(info[4][0]) for info in infos):
        raise ValueError(f"{host} is not a public internet address")


def check_public_url(url: str, schemes: Iterable[str] = ("https",)) -> None:
    parsed = urlparse(url or "")
    if parsed.scheme not in schemes:
        raise ValueError(f"only {'/'.join(schemes)} links are allowed")
    if parsed.username or parsed.password:
        raise ValueError("links with a username or password aren't allowed")
    check_public_host(parsed.hostname, parsed.port)


def http_url_or_none(url: Optional[str]) -> Optional[str]:
    return url if url and urlparse(url).scheme in ("http", "https") and urlparse(url).netloc else None


async def block_private_hook(request) -> None:
    """httpx event hook: refuse requests (and redirects) that point inside the server's network."""
    import asyncio

    import httpx

    try:
        await asyncio.to_thread(check_public_host, request.url.host, request.url.port)
    except ValueError as exc:
        raise httpx.RequestError(f"blocked: {exc}", request=request) from exc
