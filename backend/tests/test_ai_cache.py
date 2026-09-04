from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.ai_cache import analysis_cache_key, cached_ai_result
from app.database import Base
from app.models.ai_cache import AIAnalysisCache


def test_cache_key_changes_with_policy_prompt_model_or_content():
    base = dict(provider="test", model="m1", operation="document", prompt_version="v1",
                policy_mode="redacted", text="content", context="file.pdf")
    first = analysis_cache_key(**base)
    assert first == analysis_cache_key(**base)
    for field, value in (("model", "m2"), ("prompt_version", "v2"),
                         ("policy_mode", "metadata_only"), ("text", "changed")):
        assert first != analysis_cache_key(**{**base, field: value})


def test_cache_stores_only_hash_and_structured_result():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls = []
    with Session(engine) as db:
        args = dict(
            provider="test", model="m1", operation="document", prompt_version="v1",
            policy_mode="redacted", text="private source text", context="secret filename.pdf",
        )
        first, first_hit = cached_ai_result(db, **args, compute=lambda: calls.append(1) or {"summary": "safe"})
        second, second_hit = cached_ai_result(db, **args, compute=lambda: calls.append(2) or {"summary": "wrong"})

        assert first == second == {"summary": "safe"}
        assert first_hit is False and second_hit is True
        assert calls == [1]
        row = db.query(AIAnalysisCache).one()
        serialized = str(row.__dict__)
        assert "private source text" not in serialized
        assert "secret filename.pdf" not in serialized
        assert row.hit_count == 1
