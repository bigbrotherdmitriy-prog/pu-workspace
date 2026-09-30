import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.api.ai_secretary import _message_payload, inbox
from app.models.ai_secretary import Message
from app.models.document import Document
from app.models.external_resource import ExternalResourceLink
from app.models.governance import Risk
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.task_completion_suggestion import TaskCompletionSuggestion
from app.models.v54_authority import AuthorityState
from app.core.v54_authority import PILOT_SCOPE


def test_batched_inbox_preserves_payload_and_query_count_for_full_page(db_session, user_factory):
    db = db_session
    user = user_factory()
    org = Organization(name="Read performance")
    db.add(org); db.flush()
    project = Project(name="Selected", organization_id=org.id)
    other = Project(name="Other", organization_id=org.id)
    db.add_all([project, other]); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    now = datetime.now(timezone.utc)
    db.add(AuthorityState(organization_id=org.id, project_id=project.id,
        principal_kind="user", principal_id=str(user.id), scope=PILOT_SCOPE,
        membership_role="owner", permissions=["context.confirm"], state="active",
        authority_epoch=1, record_version=1, valid_until=now + timedelta(hours=1),
        updated_at=now, updated_by_user_id=user.id))
    imported = Document(project_id=project.id, external_id="attachment", name="Attachment")
    db.add(imported); db.flush()
    rows = []
    for i in range(200):
        row = Message(organization_id=org.id, project_id=project.id, created_by_user_id=user.id,
            source_type="email", source_external_id=f"message-{i}", source_name=f"Message {i}",
            content="Prepare a report", summary="Summary", context_evidence="Selected",
            context_confirmed=True, context_confidence=.95, context_version=1,
            attachments_json=json.dumps([{"document_external_id": "attachment"}]),
            context_confirmed_by_user_id=user.id, context_confirmed_by_user_at=now,
            context_confirmed_context_version=1, context_confirmed_authority_epoch=1)
        if i == 1:
            row.context_confirmed_authority_epoch = 2
        if i == 2:
            row.context_confirmed_context_version = 2
        db.add(row); db.flush()
        task = Task(project_id=project.id, message_id=row.id, assignee_user_id=user.id,
            created_by_user_id=user.id, title=f"Task {i}", source_file_id=f"message:{row.id}",
            source_file_name="Message", source_excerpt="Report", source_excerpt_hash=f"t{i}",
            confidence=.95, google_calendar_event_id=f"legacy-calendar-{i}")
        draft = ResponseDraft(project_id=project.id, reviewer_user_id=user.id, message_id=row.id,
            subject="Report", body="Draft", source_file_id=f"message:{row.id}", source_file_name="Message",
            source_excerpt="Report", source_excerpt_hash=f"d{i}", confidence=.95)
        risk = Risk(project_id=project.id, owner_user_id=user.id, source_id=f"message:{row.id}",
            source_type="email", source_name="Message", source_excerpt="Report", source_hash=f"r{i}",
            title="Risk", description="Check report", confidence=.9)
        db.add_all([task, draft, risk]); db.flush()
        db.add(TaskCompletionSuggestion(project_id=project.id, message_id=row.id, task_id=task.id,
            confidence=.8, evidence="Report done"))
        db.add(ExternalResourceLink(project_id=project.id, entity_type="task", entity_id=task.id,
            provider="google_workspace", resource_type="task", external_id=f"external-{i}",
            sync_status="deleted" if i == 3 else "synced"))
        rows.append(row)
    # A stale relation to a task in another project must not leak into this page.
    db.add(Task(project_id=other.id, message_id=rows[0].id, assignee_user_id=user.id,
        created_by_user_id=user.id, title="Wrong project", source_file_id="foreign",
        source_file_name="Other", source_excerpt="Other", source_excerpt_hash="foreign", confidence=.8))
    db.commit()
    user_id, project_id = user.id, project.id
    db.expunge_all()
    user = db.get(type(user), user_id)
    queries = []
    def count_query(_conn, _cursor, statement, *_args):
        queries.append(statement)
    event.listen(db.get_bind(), "before_cursor_execute", count_query)
    try:
        result = inbox(project_id, db, user)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", count_query)
    assert result["count"] == 200
    assert len(queries) <= 13
    for payload in result["messages"]:
        row = db.get(Message, payload["id"])
        # The deliberately stale cross-project task is filtered in the optimized reader.
        expected = _message_payload(db, row, actor=user)
        expected["tasks"] = [item for item in expected["tasks"] if item["title"] != "Wrong project"]
        assert payload == expected
    states = {row["id"]: row["auto_context_confirmation_state"] for row in result["messages"]}
    assert "confirmed_current" in states.values()
    assert "stale_context" in states.values()
    assert "stale_authority" in states.values()

