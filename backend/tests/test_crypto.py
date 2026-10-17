"""
Tests for app.crypto — encrypt/decrypt round-trip and edge cases.
"""

import os
import pytest

# Ensure a stable Fernet key is set so the module uses it
os.environ.setdefault("FERNET_KEY", "")  # Force generation at import time

from app.crypto import decrypt, encrypt  # noqa: E402


class TestEncryptDecrypt:
    def test_roundtrip(self):
        """Encrypting and then decrypting should return the original plaintext."""
        plain = "super-secret-password"
        ciphertext = encrypt(plain)
        assert ciphertext != plain, "Ciphertext should differ from plaintext"
        assert decrypt(ciphertext) == plain

    def test_roundtrip_unicode(self):
        """Unicode strings should survive the round-trip."""
        plain = "パスワード123!@#"
        assert decrypt(encrypt(plain)) == plain

    def test_empty_encrypt(self):
        """Encrypting an empty string should return an empty string."""
        assert encrypt("") == ""

    def test_empty_decrypt(self):
        """Decrypting an empty string should return an empty string."""
        assert decrypt("") == ""

    def test_invalid_ciphertext(self):
        """A non-base64 / corrupted string should return an empty string."""
        assert decrypt("not-valid-base64!") == ""

    def test_invalid_ciphertext_base64_garbage(self):
        """Properly padded base64 that is not a valid Fernet token returns ''."""
        import base64
        garbage = base64.urlsafe_b64encode(b"this-is-not-a-valid-fernet-token").decode()
        assert decrypt(garbage) == ""

    def test_different_encryptions_differ(self):
        """Each encryption call should produce a different ciphertext (due to random IV)."""
        plain = "hello"
        c1 = encrypt(plain)
        c2 = encrypt(plain)
        # Fernet uses a random IV so ciphertexts should not be identical
        assert c1 != c2

    def test_long_plaintext(self):
        """Long strings should round-trip correctly."""
        plain = "A" * 10_000
        assert decrypt(encrypt(plain)) == plain
