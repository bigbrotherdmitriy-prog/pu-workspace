"""Real document handler/persistence/cache with only external transports doubled."""
import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.ai_cache import analysis_cache_key
from app.api import telegram
from app.models.ai_cache import AIAnalysisCache
from app.models.ai_policy import ProjectAIPolicy
from app.models.ai_secretary import Message
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.telegram_chat import TelegramChatLink


@pytest.fixture
def ingress(db_session, user_factory, monkeypatch):
    owner = user_factory()
    organization = Organization(name="Synthetic privacy fixture")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic document project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    db_session.add_all([
        ProjectMember(project_id=project.id, user_id=owner.id, role="owner"),
        TelegramChatLink(chat_id=705, project_id=project.id, title="Synthetic", enabled=True),
    ])
    db_session.commit()
    calls, notifications = [], []

    def analyze(text, filename):
        calls.append((text, filename))
        return {"executive_summary": "Synthetic external result"}

    provider = SimpleNamespace(
        provider="synthetic", model="synthetic", health=lambda: SimpleNamespace(ready=True),
        analyze_document=analyze,
    )
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "synthetic-webhook-secret")
    monkeypatch.setattr(telegram, "SessionLocal", sessionmaker(db_session.get_bind(), expire_on_commit=False))
    monkeypatch.setattr(telegram, "configured_ai_provider", lambda: provider)
    monkeypatch.setattr(telegram, "notify_telegram_chat", lambda *args: notifications.append(args))
    monkeypatch.setattr(telegram, "_download_document", lambda item: (
        item["file_name"], "text/plain", item["synthetic_body"].encode("utf8"),
    ))

    def set_policy(mode):
        if mode is not None:
            db_session.add(ProjectAIPolicy(project_id=project.id, mode=mode,
                dlp_enabled=True, prompt_version="v1", updated_by_user_id=owner.id))
            db_session.commit()

    def receive(filename, body, *, message_id=1):
        class Request:
            async def json(self):
                return {"message": {
                    "message_id": message_id, "chat": {"id": 705, "type": "group"},
                    "from": {"id": 1}, "document": {
                        "file_id": f"synthetic-file-{message_id}",
                        "file_unique_id": f"synthetic-unique-{message_id}",
                        "file_name": filename, "synthetic_body": body,
                    },
                }}
        assert asyncio.run(telegram.webhook(Request(), "synthetic-webhook-secret")) == {"ok": True}
        db_session.expire_all()

    return SimpleNamespace(db=db_session, project=project, calls=calls, provider=provider,
                           notifications=notifications, set_policy=set_policy, receive=receive)


def assert_local_source_unchanged(ingress, filename, body):
    document = ingress.db.scalar(select(Document).where(Document.project_id == ingress.project.id))
    message = ingress.db.scalar(select(Message).where(Message.project_id == ingress.project.id))
    version = ingress.db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id))
    assert document.name == message.source_name == filename
    assert message.content == version.content == body


@pytest.mark.parametrize("kind,marker", [
    ("EMAIL", "synthetic.person@example.test"),
    ("PHONE", "+7 999 123-45-67"),
    ("INN", "7700000000"),
])
def test_redacted_document_egress_covers_filename_body_and_cache(ingress, kind, marker):
    ingress.set_policy("redacted")
    filename, body = f"Report {marker} .txt", f"Synthetic contact: {marker}"
    ingress.receive(filename, body)
    token = f"[{kind}_{hashlib.sha256(marker.encode()).hexdigest()[:8]}]"
    safe_body, safe_filename = body.replace(marker, token), filename.replace(marker, token)
    assert ingress.calls == [(safe_body, safe_filename)]
    cache = ingress.db.scalar(select(AIAnalysisCache))
    assert cache.cache_key == analysis_cache_key(provider="synthetic", model="synthetic",
        operation="document_analysis", prompt_version="v1", policy_mode="redacted",
        text=safe_body, context=safe_filename)
    assert_local_source_unchanged(ingress, filename, body)


def test_metadata_only_uses_fixed_context_and_reuses_only_safe_cache(ingress):
    ingress.set_policy("metadata_only")
    body = "Synthetic confidential document body"
    first = "Synthetic secret first name.txt"
    second = "Synthetic secret second name.txt"
    ingress.receive(first, body)
    ingress.receive(second, body, message_id=2)
    assert ingress.calls == [(f"Метаданные: длина текста {len(body)} символов. Содержимое политикой не передаётся.", "document")]
    assert ingress.db.scalar(select(AIAnalysisCache)).hit_count == 1
    assert set(ingress.db.scalars(select(Document.name))) == {first, second}


def test_local_only_document_preserves_local_processing_without_external_call(ingress):
    ingress.set_policy("local_only")
    filename, body = "Local synthetic secret.txt", "Synthetic local content"
    ingress.receive(filename, body)
    assert ingress.calls == []
    assert ingress.db.scalar(select(AIAnalysisCache)) is None
    assert_local_source_unchanged(ingress, filename, body)


@pytest.mark.parametrize("mode", ["external_allowed", None])
def test_existing_external_allow_and_missing_policy_defaults_are_preserved(ingress, mode):
    ingress.set_policy(mode)
    filename, body = "Synthetic permitted original.txt", "Synthetic permitted content"
    ingress.receive(filename, body)
    assert ingress.calls == [(body, filename)]
    assert_local_source_unchanged(ingress, filename, body)


@pytest.mark.parametrize("mode", ["redacted", "metadata_only"])
def test_old_raw_filename_cache_entry_cannot_bypass_prepared_context(ingress, mode):
    ingress.set_policy(mode)
    filename, body = "Report synthetic.person@example.test .txt", "Synthetic document content"
    safe_body = (body if mode == "redacted" else
        f"Метаданные: длина текста {len(body)} символов. Содержимое политикой не передаётся.")
    ingress.db.add(AIAnalysisCache(
        cache_key=analysis_cache_key(provider="synthetic", model="synthetic",
            operation="document_analysis", prompt_version="v1", policy_mode=mode,
            text=safe_body, context=filename),
        provider="synthetic", model="synthetic", operation="document_analysis",
        prompt_version="v1", policy_mode=mode,
        result_json={"executive_summary": "OLD_RAW_FILENAME_CACHE_RESULT"},
    ))
    ingress.db.commit()
    ingress.receive(filename, body)
    assert len(ingress.calls) == 1
    message = ingress.db.scalar(select(Message))
    assert "OLD_RAW_FILENAME_CACHE_RESULT" not in message.summary
    assert_local_source_unchanged(ingress, filename, body)


def test_external_failure_keeps_local_result_without_publishing_raw_error(ingress):
    ingress.set_policy("redacted")
    filename, body = "Synthetic fallback original.txt", "Synthetic local fallback content"
    def failed(text, filename):
        raise RuntimeError("RAW_SYNTHETIC_PROVIDER_ERROR")
    ingress.provider.analyze_document = failed
    ingress.receive(filename, body)
    assert_local_source_unchanged(ingress, filename, body)
    assert ingress.db.scalar(select(AIAnalysisCache)) is None
    assert "RAW_SYNTHETIC_PROVIDER_ERROR" not in str(ingress.notifications)
