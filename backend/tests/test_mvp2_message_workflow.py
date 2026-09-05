from types import SimpleNamespace

from sqlalchemy import select

from app.api import ai_secretary as ai
from app.models.ai_secretary import Message
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.task_completion_suggestion import TaskCompletionSuggestion


def _world(db, user_factory, monkeypatch):
    user = user_factory()
    organization = Organization(name="Synthetic organization")
    db.add(organization)
    db.flush()
    project = Project(name="Synthetic project", organization_id=organization.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    db.commit()
    calls = {"tasks": 0, "drafts": 0, "governance": 0}

    def tasks(*_args, **_kwargs):
        calls["tasks"] += 1
        return []

    def drafts(*_args, **_kwargs):
        calls["drafts"] += 1
        return []

    def governance(*_args, **_kwargs):
        calls["governance"] += 1
        return [], []

    monkeypatch.setattr(ai, "create_tasks_from_files", tasks)
    monkeypatch.setattr(ai, "create_response_drafts", drafts)
    monkeypatch.setattr(ai, "create_governance_items", governance)
    monkeypatch.setattr(ai, "brief_summary", lambda *_args: "Synthetic summary")
    monkeypatch.setattr(ai, "configured_action_adapter", lambda *_args: SimpleNamespace(provider="synthetic"))
    return user, project, calls


def test_low_confidence_context_defers_all_message_automation_until_human_confirmation(
    db_session, user_factory, monkeypatch,
):
    user, project, calls = _world(db_session, user_factory, monkeypatch)

    result = ai.ingest_message(
        ai.IncomingMessage(
            project_id=project.id,
            source_type="email",
            source_external_id="synthetic-low-confidence",
            source_name="Synthetic sender",
            source_sender="sender@example.test",
            content="Просим подготовить документ до 15.10.2026.",
            routing_confidence=0.4,
            routing_evidence="Two project candidates",
        ),
        db_session,
        user,
    )

    message = db_session.get(Message, result["id"])
    assert calls == {"tasks": 0, "drafts": 0, "governance": 0}
    assert message.analysis_required is True
    assert result["workflow_state"] == "needs_context_confirmation"

    confirmed = ai.confirm_context(
        message.id,
        ai.ContextConfirmation(project_id=project.id),
        db_session,
        user,
    )
    assert calls == {"tasks": 1, "drafts": 1, "governance": 1}
    assert message.analysis_required is False
    assert confirmed["workflow_state"] == "requires_action"

    ai.confirm_context(
        message.id,
        ai.ContextConfirmation(project_id=project.id),
        db_session,
        user,
    )
    assert calls == {"tasks": 1, "drafts": 1, "governance": 1}


def test_outgoing_message_reports_awaiting_reply_without_completing_a_task(
    db_session, user_factory, monkeypatch,
):
    user, project, _calls = _world(db_session, user_factory, monkeypatch)
    task = Task(
        project_id=project.id,
        assignee_user_id=user.id,
        created_by_user_id=user.id,
        title="Unrelated open task",
        status="assigned",
        source_file_id="synthetic",
        source_file_name="Synthetic",
        source_excerpt="Unrelated",
        source_excerpt_hash="b" * 64,
        confidence=0.9,
    )
    db_session.add(task)
    db_session.commit()

    result = ai.ingest_message(
        ai.IncomingMessage(
            project_id=project.id,
            source_type="email_outgoing",
            source_external_id="synthetic-outgoing",
            source_name="Synthetic outgoing",
            source_sender="recipient@example.test",
            source_thread_id="synthetic-thread",
            content="Направляем информацию для ознакомления.",
            routing_confidence=0.99,
            routing_evidence="Confirmed synthetic route",
        ),
        db_session,
        user,
    )

    assert result["workflow_state"] == "awaiting_reply"
    assert task.status == "assigned"


def test_sent_draft_reports_awaiting_reply_and_completed_message_wins(
    db_session, user_factory, monkeypatch,
):
    user, project, _calls = _world(db_session, user_factory, monkeypatch)
    message = Message(
        organization_id=project.organization_id,
        project_id=project.id,
        created_by_user_id=user.id,
        source_type="email",
        source_external_id="synthetic-incoming",
        source_name="Synthetic incoming",
        source_sender="sender@example.test",
        content="Synthetic content",
        attachments_json="[]",
        summary="Synthetic",
        context_confidence=1,
        context_evidence="Confirmed",
        context_confirmed=True,
        status="ready",
    )
    db_session.add(message)
    db_session.flush()
    db_session.add(ResponseDraft(
        project_id=project.id,
        reviewer_user_id=user.id,
        message_id=message.id,
        subject="Re: Synthetic",
        body="Sent body",
        status="sent",
        source_file_id=f"message:{message.id}",
        source_file_name="Synthetic",
        source_excerpt="Synthetic",
        source_excerpt_hash="c" * 64,
        confidence=0.9,
    ))
    db_session.commit()

    payload = ai._message_payload(db_session, message, actor=user)
    assert payload["workflow_state"] == "awaiting_reply"

    message.status = "completed"
    db_session.commit()
    payload = ai._message_payload(db_session, message, actor=user)
    assert payload["workflow_state"] == "completed"


def test_message_job_payload_contract_keeps_raw_mail_out_of_durable_jobs():
    from app.staging.gmail import enqueue_staged_gmail_attachment

    source = __import__("inspect").getsource(enqueue_staged_gmail_attachment)
    assert '{"staging_id": staging_id}' in source
    for forbidden in ("content", "body", "token", "provider_message_id", "attachment_id"):
        assert forbidden not in source


def test_outgoing_completion_analysis_is_idempotent_before_final_checkpoint(
    db_session, user_factory, monkeypatch,
):
    user, project, _calls = _world(db_session, user_factory, monkeypatch)
    task = Task(
        project_id=project.id, assignee_user_id=user.id, created_by_user_id=user.id,
        title="Prepare synthetic report", description="Prepare synthetic report completed",
        status="assigned", source_file_id="synthetic-task", source_file_name="Synthetic",
        source_excerpt="Prepare synthetic report", source_excerpt_hash="d" * 64,
        confidence=0.9,
    )
    message = Message(
        organization_id=project.organization_id, project_id=project.id,
        created_by_user_id=user.id, source_type="email_outgoing",
        source_external_id="synthetic-replay", source_name="Synthetic outgoing",
        content="Prepare synthetic report completed", attachments_json="[]",
        summary="Pending", context_confidence=1, context_evidence="Confirmed",
        context_confirmed=True, status="ready", analysis_required=True,
    )
    db_session.add_all([task, message])
    db_session.commit()

    first = ai._create_completion_suggestions(db_session, message)
    db_session.flush()
    second = ai._create_completion_suggestions(db_session, message)
    db_session.flush()

    assert len(first) == 1
    assert second == []
    assert len(list(db_session.scalars(select(TaskCompletionSuggestion)))) == 1


def test_confirmed_message_materializes_task_draft_and_risk_once(
    db_session, user_factory, monkeypatch,
):
    user = user_factory()
    organization = Organization(name="Synthetic organization")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    db_session.commit()
    monkeypatch.setattr(ai, "configured_action_adapter", lambda *_args: SimpleNamespace(provider="synthetic"))

    result = ai.ingest_message(
        ai.IncomingMessage(
            project_id=project.id, source_type="email",
            source_external_id="synthetic-full-chain", source_name="Synthetic request",
            source_sender="sender@example.test",
                content=(
                    "Просим подготовить отчёт до 15.10.2026. "
                    "Риск критичной просрочки поставки. Требуется решение согласовать вариант."
                ),
            routing_confidence=0.4, routing_evidence="Synthetic ambiguity",
        ),
        db_session,
        user,
    )
    assert result["tasks"] == [] and result["drafts"] == [] and result["risks"] == []

    confirmed = ai.confirm_context(
        result["id"], ai.ContextConfirmation(project_id=project.id), db_session, user,
    )
    assert len(confirmed["tasks"]) >= 1
    assert len(confirmed["drafts"]) == 1
    assert len(confirmed["risks"]) == 1
    assert confirmed["tasks"][0]["external_action_status"] == "proposed"
    assert confirmed["workflow_state"] == "requires_action"

    replay = ai.confirm_context(
        result["id"], ai.ContextConfirmation(project_id=project.id), db_session, user,
    )
    assert [value["id"] for value in replay["tasks"]] == [value["id"] for value in confirmed["tasks"]]
    assert [value["id"] for value in replay["drafts"]] == [value["id"] for value in confirmed["drafts"]]
