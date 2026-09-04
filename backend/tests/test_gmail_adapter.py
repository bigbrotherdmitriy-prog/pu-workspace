import base64
import inspect
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.ai_secretary import project_candidate
from app.api.gmail import GmailSyncRequest, _apply_automated_filter, _apply_bulk_filter, _attachments, _automated_sender_reason, _backfill_automated_messages_for_user, _bulk_email_reason, _gmail_telegram_notice, _message_text, router, sync_gmail_project
from app.api.google_drive import SCOPES
from app.database import Base
from app.models.ai_secretary import Message
from app.models.task import Task


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


def test_gmail_html_fallback_omits_css_scripts_and_keeps_readable_lines():
    markup = base64.urlsafe_b64encode(
        ("<html><head><style>html{-webkit-text-size-adjust:none} p{margin:0!important}</style>"
         "<script>secret()</script></head><body><p>Добрый день!</p>"
         "<p>Проверьте, что-то не так с картой.<br>Нужен ответ.</p></body></html>").encode()
    ).decode()
    payload = {"mimeType": "text/html", "body": {"data": markup}}
    text = _message_text(payload)
    assert text == "Добрый день!\nПроверьте, что-то не так с картой.\nНужен ответ."
    assert "webkit" not in text
    assert "secret" not in text


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
        "id": 17,
        "summary": "Нужно согласовать акт до 10 сентября.",
        "tasks": [{"id": 1, "title": "Согласовать акт"}],
        "risks": [{"id": 2, "title": "Просрочка согласования"}],
        "drafts": [{"id": 3, "subject": "Re: Нужен акт", "body": "Добрый день! Акт принят на проверку."}],
    })
    assert "Новое письмо" in text
    assert "Нужен акт" in text
    assert "Письмо: #17" in text
    assert "🧠 Анализ:" in text
    assert "Нужно согласовать акт до 10 сентября." in text
    assert "задач 1" in text
    assert "рисков 1" in text
    assert "черновиков 1" in text
    assert "Согласовать акт" in text
    assert "Просрочка согласования" in text
    assert "Черновик ответа (НЕ отправлен)" in text
    assert "Добрый день! Акт принят на проверку." in text
    assert "подтвердите отправку" in text
    assert len(text) <= 4000


def test_gmail_telegram_notice_does_not_claim_reply_when_no_draft_exists():
    text = _gmail_telegram_notice("robot@example.com", "Служебное уведомление", {
        "id": 18, "summary": "Автоматическое письмо.", "tasks": [], "risks": [], "drafts": [],
    })
    assert "Черновик ответа не создан" in text
    assert "Черновик ответа (НЕ отправлен)" not in text


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


def test_machine_sender_suppresses_reply_draft_without_filtering_message():
    assert _automated_sender_reason({"from": 'REG.RU support <noreply@support.reg.ru>'})
    assert _automated_sender_reason({"from": 'GitHub <notifications@github.com>'})
    assert _automated_sender_reason({"from": 'System <robot@example.test>'})
    assert _automated_sender_reason({"from": "client@example.ru"}) is None


def test_gmail_notifications_are_collapsed_by_thread_per_sync():
    source = inspect.getsource(sync_gmail_project)
    assert "notified_threads" in source
    assert "thread_key not in notified_threads" in source
    assert "response_suppressed=bool(automated_sender_reason)" in source


def test_filtered_messages_do_not_backfill_drafts_or_notify_telegram():
    source = inspect.getsource(sync_gmail_project)
    assert 'not bulk_reason and not automated_sender_reason and existing.status != "filtered"' in source
    assert "not is_outgoing and not bulk_reason" in source


def test_existing_bulk_message_is_safely_reclassified_on_resync():
    message = SimpleNamespace(status="needs_context_confirmation", summary="Анализируется")

    assert _apply_bulk_filter(message, "Gmail отнёс письмо к категории «Промоакции»") is True
    assert message.status == "filtered"
    assert "Автоматические действия не создавались" in message.summary


def test_bulk_backfill_does_not_override_human_workflow_status():
    message = SimpleNamespace(status="in_progress", summary="Проверяется пользователем")

    assert _apply_bulk_filter(message, "массовая рассылка") is False
    assert message.status == "in_progress"


def test_nonactionable_machine_message_is_removed_from_attention_on_resync():
    message = SimpleNamespace(status="needs_context_confirmation", summary="Анализируется")

    assert _apply_automated_filter(
        message, "адрес отправителя не принимает ответы", has_actions=False,
    ) is True
    assert message.status == "filtered"
    assert "Служебное письмо без действий" in message.summary


def test_actionable_machine_message_remains_for_human_review():
    message = SimpleNamespace(status="needs_context_confirmation", summary="Найдена задача")

    assert _apply_automated_filter(
        message, "адрес отправителя не принимает ответы", has_actions=True,
    ) is False
    assert message.status == "needs_context_confirmation"


def test_backfill_covers_old_gmail_pages_without_overriding_actions_or_human_confirmation():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        rows = [
            Message(
                organization_id=1, project_id=1, created_by_user_id=7,
                source_type="email", source_external_id="old-machine",
                source_name="System notice", source_url="https://mail.google.com/mail/u/0/#all/old-machine",
                source_sender="Notifications <notifications@example.test>", content="Service update",
                summary="Analysis", context_confidence=0.7, context_evidence="Not enough evidence",
                context_confirmed=False, status="needs_context_confirmation",
            ),
            Message(
                organization_id=1, project_id=1, created_by_user_id=7,
                source_type="email", source_external_id="actionable-machine",
                source_name="Build failed", source_url="https://mail.google.com/mail/u/0/#all/actionable-machine",
                source_sender="Notifications <notifications@example.test>", content="Fix the failed build",
                summary="Task found", context_confidence=0.7, context_evidence="Not enough evidence",
                context_confirmed=False, status="needs_context_confirmation",
            ),
            Message(
                organization_id=1, project_id=1, created_by_user_id=7,
                source_type="email", source_external_id="confirmed-machine",
                source_name="Confirmed notice", source_url="https://mail.google.com/mail/u/0/#all/confirmed-machine",
                source_sender="No reply <noreply@example.test>", content="Confirmed by the operator",
                summary="Confirmed", context_confidence=1.0,
                context_evidence="Проект подтверждён пользователем: Проект",
                context_confirmed=True, status="ready",
            ),
        ]
        db.add_all(rows)
        db.flush()
        db.add(Task(
            project_id=1, assignee_user_id=7, created_by_user_id=7,
            message_id=rows[1].id, title="Fix build", status="assigned",
            source_file_id="message:actionable-machine", source_file_name="Build failed",
            source_excerpt="Fix the failed build", source_excerpt_hash="a" * 64,
            confidence=0.8, needs_review=True,
        ))
        db.commit()

        changed = _backfill_automated_messages_for_user(db, SimpleNamespace(id=7))
        db.commit()

        assert changed == 1
        assert rows[0].status == "filtered"
        assert rows[1].status == "needs_context_confirmation"
        assert rows[2].status == "ready"


def test_oauth_callback_returns_to_new_interface():
    source = __import__("inspect").getsource(__import__("app.api.google_drive", fromlist=["google_callback"]).google_callback)
    assert 'url=f"/new/?oauth=connected&project_id={project_id}"' in source
