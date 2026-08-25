import os

from cryptography.fernet import Fernet, InvalidToken


PREFIX = "fernet:v1:"


class TokenEncryptionError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.getenv("TOKEN_ENCRYPTION_KEY", "").encode("ascii")
    if not key:
        raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is invalid") from exc


def encrypt_token(value: str | None) -> str | None:
    if not value:
        return value
    if value.startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_token(value: str | None) -> str | None:
    if not value or not value.startswith(PREFIX):
        # Legacy plaintext is readable so an existing installation can migrate
        # it on the next OAuth refresh/callback without losing access.
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise TokenEncryptionError("Stored Google token cannot be decrypted") from exc
