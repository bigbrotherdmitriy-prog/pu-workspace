import base64

import inspect

from app.api.ai_secretary import project_candidate
from app.api.gmail import GmailSyncRequest, _attachments, _bulk_email_reason, _gmail_telegram_notice, _message_text, router, sync_gmail_project
from app.api.google_drive import SCOPES


def test_gmail_routes_and_scopes_are_explicit():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}/gmail/sync" in paths
    assert "/ai-secretary/inbox/{message_id}/attachments/{attachment_index}/import" in paths
    assert "/response-drafts/{draft_id}/send-gmail" in paths
    assert "https://www.googleapis.com/auth/gmail.readonly" in SCOPES
    assert "https://www.googleapis.com/auth/gmail.send" in SCOPES


def test_gmail_payload_prefers_plain_text():
    plain = base64.urlsafe_b64encode("Просим направить акт до 30.08.2026".encode()).decode()
    markup = base64.urlsafe_b64encode("<b>Другой текст</b>".encode()).decode()
    payload = {"parts": [
        {"mimeType": "text/html", "body": {"data": markup}},
        {"mimeType": "text/plain", "body": {"data": plain}},
    ]}
    assert _message_text(payload) == "Просим направить акт до 30.08.2026"


def test_gmail_attachment_metadata_is_extracted_without_file_transfer():
    payload = {"parts": [{"filename": "Акт.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "secret", "size": 2048}}]}
    assert _attachments(payload, "message-1") == [{"name": "Акт.pdf", "mime_type": "application/pdf", "size": 2048,
                                                    "attachment_id": "secret", "document_external_id": "gmail:message-1:secret"}]


def test_gmail_sync_is_bounded():
    request = GmailSyncRequest()
    assert request.query == "newer_than:7d"
    assert request.max_results == 25


def test_gmail_sync_routes_new_messages_through_semantic_project_matching():
    source = inspect.getsource(sync_gmail_project)
    assert "project_candidate(" in source
    assert "project_id=target_project_id" in source


def test_gmail_sync_backfills_safe_reply_for_older_messages():
    source = inspect.getsource(sync_gmail_project)
    assert "ensure_response=True" in source
    assert "ResponseDraft.message_id == existing.id" in source


def test_project_candidate_is_available_for_cross_project_routing():
    assert callable(project_candidate)


def test_gmail_telegram_notice_is_concise_and_actionable():
    text = _gmail_telegram_notice("Заказчик <client@example.com>", "Нужен акт", {
        "tasks": [{"id": 1}], "risks": [{"id": 2}], "drafts": [{"id": 3}],
    })
    assert "Новое письмо" in text
    assert "Нужен акт" in text
    assert "задач 1" in text
    assert "рисков 1" in text
    assert "черновиков 1" in text


def test_marketing_email_is_suppressed_by_strong_provider_evidence():
    headers = {
        "from": '"Деловые Линии" <info@marketing.dellin.ru>',
        "list-unsubscribe": "<https://marketing.dellin.ru/unsubscribe>",
    }
    reason = _bulk_email_reason(
        headers, ["INBOX", "CATEGORY_PROMOTIONS"],
        "Надежная доставка для регулярных перевозок", "Подробности предложения",
    )
    assert reason


def test_business_offer_is_not_suppressed_from_words_alone():
    reason = _bulk_email_reason(
        {"from": "supplier@example.ru"}, ["INBOX"],
        "Коммерческое предложение по доставке", "Просим согласовать стоимость и срок",
    )
    assert reason is None


def test_filtered_messages_do_not_backfill_drafts_or_notify_telegram():
    source = inspect.getsource(sync_gmail_project)
    assert 'not bulk_reason and existing.status != "filtered"' in source
    assert "not is_outgoing and not bulk_reason" in source


def test_oauth_callback_returns_to_new_interface():
    source = __import__("inspect").getsource(__import__("app.api.google_drive", fromlist=["google_callback"]).google_callback)
    assert 'url=f"/new/?oauth=connected&project_id={project_id}"' in source
