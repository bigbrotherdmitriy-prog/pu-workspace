"""Google OIDC verification boundary. Tokens and claims are never logged."""
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2 import id_token


class OIDCVerificationError(ValueError):
    pass


def verified_google_subject(raw_id_token: str, audience: str, *, request=None, now=None) -> str:
    if not raw_id_token or not audience:
        raise OIDCVerificationError("identity_verification_failed")
    try:
        claims = id_token.verify_oauth2_token(raw_id_token, request or Request(), audience=audience)
        issuer = claims.get("iss")
        subject = claims.get("sub")
        expiry = claims.get("exp")
        current = int((now or datetime.now(timezone.utc)).timestamp())
        if (issuer not in {"accounts.google.com", "https://accounts.google.com"}
                or not isinstance(subject, str) or not subject or len(subject) > 255
                or subject.strip() != subject or type(expiry) is not int or expiry <= current):
            raise ValueError
    except Exception as exc:
        raise OIDCVerificationError("identity_verification_failed") from exc
    return subject
