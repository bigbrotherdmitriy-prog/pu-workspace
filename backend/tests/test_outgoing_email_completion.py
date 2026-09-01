from pathlib import Path

from app.api.ai_secretary import _completion_candidate_score, router


ROOT = Path(__file__).resolve().parents[1]


class FakeTask:
    title = "Направить заказчику исправленный акт"
    description = "Отправить акт после исправления замечаний"


def test_outgoing_result_matches_open_task_without_auto_completion():
    confidence, evidence = _completion_candidate_score(
        FakeTask(),
        "Добрый день. Исправленный акт направили заказчику, замечания устранены.",
    )
    assert confidence >= 0.45
    assert "признак результата" in evidence


def test_outgoing_completion_requires_explicit_review_endpoint():
    paths = {route.path for route in router.routes}
    assert "/ai-secretary/inbox/{message_id}/completion-suggestions/{suggestion_id}" in paths
    source = (ROOT / "app/api/ai_secretary.py").read_text(encoding="utf-8")
    assert 'if payload.status == "confirmed"' in source
    assert 'status="proposed"' in source


def test_gmail_distinguishes_sent_mail_and_does_not_send_incoming_alert():
    source = (ROOT / "app/api/gmail.py").read_text(encoding="utf-8")
    assert '"SENT" in set(item.get("labelIds") or [])' in source
    assert 'source_type = "email_outgoing" if is_outgoing else "email"' in source
    assert "if not is_outgoing and not bulk_reason:" in source


def test_schema_revision_tracks_latest_migration():
    schema = (ROOT / "app/schema.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/versions/e31a7b8c9d02_add_contract_document_links.py").read_text(encoding="utf-8")
    assert 'CURRENT_SCHEMA_REVISION = "a31c7d8e9f20"' in schema
    assert 'down_revision = "d20f6a7b8c91"' in migration
