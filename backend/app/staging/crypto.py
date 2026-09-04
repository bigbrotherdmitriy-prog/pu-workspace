from __future__ import annotations

import base64
import re
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.staging.contracts import KekRef, KekResolver, StagingIntegrityError, StagingSecurityError

KEK_BYTES = 32
NONCE_BYTES = 12
DEK_WRAP_DOMAIN = b"PUW-STAGING-DEK-WRAP-V2\x00"
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_kek_ref(kek: KekRef) -> None:
    if (not isinstance(kek, KekRef) or not _REF_RE.fullmatch(kek.reference)
            or not _VERSION_RE.fullmatch(kek.version)):
        raise StagingSecurityError("invalid_kek_reference")


def _resolve(resolver: KekResolver, kek: KekRef) -> bytes:
    validate_kek_ref(kek)
    try:
        value = resolver.resolve(kek.reference, kek.version)
    except Exception:
        raise StagingIntegrityError("key_unavailable") from None
    if not isinstance(value, bytes) or len(value) != KEK_BYTES:
        raise StagingIntegrityError("key_unavailable")
    return value


def _wrap_aad(object_id: str, kek: KekRef) -> bytes:
    return DEK_WRAP_DOMAIN + object_id.encode("ascii") + b"\x00" + kek.reference.encode("ascii") + b"\x00" + kek.version.encode("ascii")


def wrap_dek(dek: bytes, *, object_id: str, kek: KekRef, resolver: KekResolver) -> str:
    if not isinstance(dek, bytes) or len(dek) != KEK_BYTES:
        raise StagingSecurityError("invalid_dek")
    key = _resolve(resolver, kek)
    nonce = secrets.token_bytes(NONCE_BYTES)
    wrapped = nonce + AESGCM(key).encrypt(nonce, dek, _wrap_aad(object_id, kek))
    return base64.urlsafe_b64encode(wrapped).decode("ascii")


def unwrap_dek(wrapped: str, *, object_id: str, kek: KekRef, resolver: KekResolver) -> bytes:
    if not isinstance(wrapped, str) or not 40 <= len(wrapped) <= 128:
        raise StagingIntegrityError("wrapped_key_invalid")
    key = _resolve(resolver, kek)
    try:
        raw = base64.b64decode(wrapped.encode("ascii"), altchars=b"-_", validate=True)
        if len(raw) != NONCE_BYTES + KEK_BYTES + 16:
            raise ValueError
        return AESGCM(key).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], _wrap_aad(object_id, kek))
    except Exception:
        raise StagingIntegrityError("wrapped_key_invalid") from None
