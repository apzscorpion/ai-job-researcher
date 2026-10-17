"""
Fernet symmetric encryption helpers for storing sensitive values (SMTP password,
API keys) in the database.

Reads FERNET_KEY from the environment.  If the key is absent or invalid a new
one is generated at startup and a warning is printed — this is safe for
development but means previously encrypted values cannot be decrypted after a
restart, so users should set a stable key in production.
"""

from __future__ import annotations

import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key bootstrap
# ---------------------------------------------------------------------------

def _load_or_generate_key() -> Fernet:
    import app.config  # noqa: F401  (loads .env)

    raw = os.environ.get("FERNET_KEY", "")
    if raw:
        try:
            # Accept raw base64url key or URL-safe base64 directly
            key_bytes = raw.encode() if isinstance(raw, str) else raw
            # Validate by constructing Fernet
            return Fernet(key_bytes)
        except Exception:
            logger.warning(
                "FERNET_KEY is set but invalid — generating a temporary key. "
                "Previously encrypted values will not be recoverable."
            )
    key = Fernet.generate_key()
    logger.warning(
        "FERNET_KEY not set — using a temporary key. Saved SMTP/Gemini secrets "
        "will be unreadable after restart; set FERNET_KEY in .env."
    )
    return Fernet(key)


_fernet: Fernet = _load_or_generate_key()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encrypt(plaintext: str) -> str:
    """Encrypt *plaintext* and return a URL-safe base64 token string.

    Returns an empty string if *plaintext* is empty or if encryption fails.
    """
    if not plaintext:
        return ""
    try:
        token: bytes = _fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")
    except Exception:
        logger.exception("Encryption failed")
        return ""


def decrypt(ciphertext: str) -> str:
    """Decrypt a token produced by :func:`encrypt`.

    Returns an empty string if *ciphertext* is empty, invalid, or decryption
    fails for any reason.
    """
    if not ciphertext:
        return ""
    try:
        plaintext: bytes = _fernet.decrypt(ciphertext.encode("utf-8"))
        return plaintext.decode("utf-8")
    except (InvalidToken, Exception):
        logger.warning("Decryption failed (wrong FERNET_KEY?) — re-enter the secret in Settings")
        return ""
