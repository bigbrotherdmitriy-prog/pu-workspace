import os

from cryptography.fernet import Fernet, InvalidToken


PREFIX = "fernet:v1:"


class TokenEncryptionError(RuntimeError):
    pass


def _fernet_from_key(key: str, *, setting: str) -> Fernet:
    encoded = key.encode("ascii")
    try:
        return Fernet(encoded)
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise TokenEncryptionError(f"{setting} is invalid") from exc


def _fernet() -> Fernet:
    key = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is not configured")
    return _fernet_from_key(key, setting="TOKEN_ENCRYPTION_KEY")


def _decrypt_fernets() -> list[Fernet]:
    fernets = [_fernet()]
    previous = os.getenv("TOKEN_ENCRYPTION_PREVIOUS_KEYS", "")
    for index, key in enumerate(previous.split(","), start=1):
        key = key.strip()
        if key:
            fernets.append(
                _fernet_from_key(key, setting=f"TOKEN_ENCRYPTION_PREVIOUS_KEYS item {index}")
            )
    return fernets


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
    encrypted = value[len(PREFIX):].encode("ascii")
    for fernet in _decrypt_fernets():
        try:
            return fernet.decrypt(encrypted).decode("utf-8")
        except InvalidToken:
            continue
    raise TokenEncryptionError("Stored integration token cannot be decrypted")


def rotate_token(value: str | None) -> str | None:
    """Re-encrypt a stored token with the active key without exposing plaintext."""
    if not value:
        return value
    plaintext = decrypt_token(value)
    if plaintext is None:
        return None
    return PREFIX + _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
