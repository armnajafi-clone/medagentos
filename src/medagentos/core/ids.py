"""Identifier and digest helpers.

Identifiers are prefixed ULept-style strings: a type prefix, a timestamp
component and a random component. The prefix makes an id self-describing in a
trace or a log line, and the timestamp component makes ids sort roughly in
creation order, which matters when reading a trace by eye.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32, no ambiguous chars


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        chars.append(_ALPHABET[remainder])
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    """Return a fresh sortable identifier such as ``run_01j9x2k4m8_7fq3zt``."""
    if not prefix:
        raise ValueError("identifier prefix must not be empty")
    timestamp = _encode(int(time.time() * 1000), 10)
    randomness = _encode(secrets.randbits(30), 6)
    return f"{prefix}_{timestamp}_{randomness}"


def digest_bytes(payload: bytes) -> str:
    """Return the content digest used for artifact addressing."""
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def digest_json(payload: Any) -> str:
    """Return a stable digest of any JSON-serialisable value.

    Keys are sorted so that two equal payloads always produce the same digest;
    reproducibility measurement (D-09) depends on this being order-independent.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return digest_bytes(encoded.encode("utf-8"))
