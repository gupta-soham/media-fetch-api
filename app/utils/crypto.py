"""
Cryptographic utilities used by various extractors.
Includes cipher-related helpers for YouTube and other platforms.
"""

import base64
import hashlib
import hmac
import os


def md5_hash(data: str | bytes) -> str:
    """Compute MD5 hash."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.md5(data).hexdigest()


def sha1_hash(data: str | bytes) -> str:
    """Compute SHA-1 hash."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha1(data).hexdigest()


def sha256_hash(data: str | bytes) -> str:
    """Compute SHA-256 hash."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hmac_sha256(key: str | bytes, message: str | bytes) -> str:
    """Compute HMAC-SHA256."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(message, str):
        message = message.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def base64_encode(data: str | bytes) -> str:
    """Base64 encode."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(data).decode("utf-8")


def base64_decode(data: str) -> bytes:
    """Base64 decode with padding fix."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.b64decode(data)


def base64url_encode(data: bytes) -> str:
    """URL-safe base64 encode."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def base64url_decode(data: str) -> bytes:
    """URL-safe base64 decode."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


def random_bytes(n: int) -> bytes:
    """Generate n random bytes."""
    return os.urandom(n)


def random_hex(n: int) -> str:
    """Generate a random hex string of length n."""
    return os.urandom(n // 2 + 1).hex()[:n]


def xor_bytes(a: bytes, b: bytes) -> bytes:
    """XOR two byte strings."""
    return bytes(x ^ y for x, y in zip(a, b))
