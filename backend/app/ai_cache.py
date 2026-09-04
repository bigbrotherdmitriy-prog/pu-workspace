from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_cache import AIAnalysisCache


T = TypeVar("T", bound=dict)


def analysis_cache_key(
    *, provider: str, model: str, operation: str, prompt_version: str,
    policy_mode: str, text: str, context: str,
) -> str:
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "operation": operation,
            "prompt_version": prompt_version,
            "policy_mode": policy_mode,
            "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "context_hash": hashlib.sha256(context.encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cached_ai_result(
    db: Session,
    *,
    provider: str,
    model: str,
    operation: str,
    prompt_version: str,
    policy_mode: str,
    text: str,
    context: str,
    compute: Callable[[], T],
) -> tuple[T, bool]:
    """Return a structured AI result without persisting source text or context."""
    key = analysis_cache_key(
        provider=provider, model=model, operation=operation,
        prompt_version=prompt_version, policy_mode=policy_mode,
        text=text, context=context,
    )
    row = db.scalar(select(AIAnalysisCache).where(AIAnalysisCache.cache_key == key))
    if row is not None:
        row.hit_count += 1
        row.last_hit_at = datetime.now(timezone.utc)
        db.flush()
        return dict(row.result_json), True

    result = compute()
    if not isinstance(result, dict):
        raise ValueError("AI provider must return a structured object")
    row = AIAnalysisCache(
        cache_key=key, provider=provider, model=model, operation=operation,
        prompt_version=prompt_version, policy_mode=policy_mode,
        result_json=result,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(AIAnalysisCache).where(AIAnalysisCache.cache_key == key))
        if existing is None:
            raise
        existing.hit_count += 1
        existing.last_hit_at = datetime.now(timezone.utc)
        db.flush()
        return dict(existing.result_json), True
    return result, False
