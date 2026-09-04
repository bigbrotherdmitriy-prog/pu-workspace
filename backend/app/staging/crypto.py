from __future__ import annotations

import base64
import re

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap

from app.staging.contracts import KekRef, KekResolver, StagingIntegrityError, StagingSecurityError

KEK_BYTES = 32
DEK_WRAP_DOMAIN = b"PUW-STAGING-DEK-WRAP-V2\x00"
WRAPPED_DEK_BYTES = 40
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")


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


def _validate_object_id(object_id: str) -> None:
    if not isinstance(object_id, str) or not _OBJECT_ID_RE.fullmatch(object_id):
        raise StagingSecurityError("invalid_opaque_id")


def _wrap_aad(object_id: str, kek: KekRef) -> bytes:
    _validate_object_id(object_id)
    return (
        DEK_WRAP_DOMAIN + object_id.encode("ascii") + b"\x00"
        + kek.reference.encode("ascii") + b"\x00" + kek.version.encode("ascii")
    )


def _derived_wrapping_key(key: bytes, *, object_id: str, kek: KekRef) -> bytes:
    """Separate this use and bind AES-KW to the exact object/key identity."""
    return HKDF(
        algorithm=hashes.SHA256(), length=KEK_BYTES, salt=DEK_WRAP_DOMAIN,
        info=_wrap_aad(object_id, kek),
    ).derive(key)


def decode_wrapped_dek(wrapped: str) -> bytes:
    if not isinstance(wrapped, str) or len(wrapped) != 56:
        raise StagingIntegrityError("wrapped_key_invalid")
    try:
        raw = base64.b64decode(wrapped.encode("ascii"), altchars=b"-_", validate=True)
    except Exception:
        raise StagingIntegrityError("wrapped_key_invalid") from None
    if len(raw) != WRAPPED_DEK_BYTES:
        raise StagingIntegrityError("wrapped_key_invalid")
    return raw


def wrap_dek(dek: bytes, *, object_id: str, kek: KekRef, resolver: KekResolver) -> str:
    if not isinstance(dek, bytes) or len(dek) != KEK_BYTES:
        raise StagingSecurityError("invalid_dek")
    _validate_object_id(object_id)
    key = _resolve(resolver, kek)
    try:
        wrapped = aes_key_wrap(
            _derived_wrapping_key(key, object_id=object_id, kek=kek), dek,
        )
    except Exception:
        raise StagingIntegrityError("key_wrap_failed") from None
    return base64.urlsafe_b64encode(wrapped).decode("ascii")


def unwrap_dek(wrapped: str, *, object_id: str, kek: KekRef, resolver: KekResolver) -> bytes:
    _validate_object_id(object_id)
    raw = decode_wrapped_dek(wrapped)
    key = _resolve(resolver, kek)
    try:
        return aes_key_unwrap(
            _derived_wrapping_key(key, object_id=object_id, kek=kek), raw,
        )
    except Exception:
        raise StagingIntegrityError("wrapped_key_invalid") from None
