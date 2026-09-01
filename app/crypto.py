"""Ed25519 canonical signing (BUILD_SPEC §6).

This module implements **our** scheme only: Ed25519 over a canonical JSON
encoding, used for mandates and quotes. Razorpay's HMAC-SHA256 schemes — the
checkout response signature and the webhook signature — live in
`app/platform/` and are a different thing entirely. Never conflate them.

The exact bytes that were signed are stored alongside every signature
(`quotes.signing_payload`, `mandates.signing_payload`) and returned by the API,
so a verifier never has to reconstruct the canonical form by guesswork.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def canonical_json(obj: dict) -> bytes:
    """The one canonicalisation. Sorted keys, no spaces, UTF-8."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sign(seed_hex: str, payload: dict) -> str:
    """Sign `canonical_json(payload)` with the Ed25519 seed. Returns hex."""
    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    return private.sign(canonical_json(payload)).hex()


def verify(public_key_hex: str, payload: dict, signature_hex: str) -> bool:
    """Verify a signature. **Returns False on any exception. Never raises.**

    A malformed signature, a bad key, a payload that will not serialise — all
    are False. A verifier that throws is a verifier that can be turned into a
    denial of service, or worse into an unhandled path that is mistaken for
    success.
    """
    try:
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public.verify(bytes.fromhex(signature_hex), canonical_json(payload))
        return True
    except Exception:
        return False


def public_key_hex(seed_hex: str) -> str:
    """The hex-encoded Ed25519 public key for a signing seed."""
    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    return private.public_key().public_bytes_raw().hex()


def signature_fingerprint(signature_hex: str | None) -> dict[str, Any] | None:
    """First 16 hex characters plus the length (§13.2).

    A full signature never enters `audit_events`; this is what goes instead.
    Enough to correlate a log line with an artifact, not enough to replay one.
    """
    if not signature_hex:
        return None
    return {"prefix": signature_hex[:16], "length": len(signature_hex)}
