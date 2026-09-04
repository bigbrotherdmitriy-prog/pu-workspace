from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.ai_secretary import BulkContextConfirmation, IncomingMessage, ingest_message, router as secretary_router
from app.api.tasks import router as task_router
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


def test_mvp2_inbox_routes_are_registered():
    paths = {route.path for route in secretary_router.routes}
    assert "/ai-secretary/inbox" in paths
    assert "/ai-secretary/inbox/{message_id}/confirm-context" in paths
    assert "/ai-secretary/inbox/confirm-context-bulk" in paths
    assert "/ai-secretary/inbox/{message_id}/status" in paths


def test_external_action_requires_explicit_route():
    paths = {route.path for route in task_router.routes}
    assert "/tasks/{task_id}/approve-external" in paths


def test_ai_secretary_uses_configured_action_provider_for_external_resources():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "api" / "ai_secretary.py").read_text(encoding="utf-8")
    payload_source = source[source.index("def _message_payload"):]
    assert "configured_action_adapter(row.project_id, db).provider" in payload_source
    assert "provider=action_provider" in payload_source
    assert '{"provider": action_provider' in payload_source
    assert 'provider="google_workspace"' not in payload_source


def test_incoming_message_defaults_to_manual_source():
    payload = IncomingMessage(project_id=1, source_name="Письмо", content="Просим подготовить ответ до 30.08.2026.")
    assert payload.source_type == "manual"
    assert payload.source_external_id is None


def test_machine_message_filter_has_explainable_reason_field():
    payload = IncomingMessage(
        project_id=1,
        source_type="email",
        source_name="Служебное уведомление",
        content="Вход выполнен",
        response_suppressed=True,
        response_suppression_reason="адрес отправителя не принимает ответы",
    )

    assert payload.response_suppression_reason == "адрес отправителя не принимает ответы"


def test_nonactionable_machine_message_is_filtered_but_retained():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        organization = Organization(name="Тестовая организация")
        user = User(name="Оператор", email="operator@example.test")
        db.add_all([organization, user])
        db.flush()
        project = Project(name="Тестовый проект", organization_id=organization.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        db.commit()

        result = ingest_message(IncomingMessage(
            project_id=project.id,
            source_type="email",
            source_external_id="machine-1",
            source_name="GitHub — verification code",
            source_sender="noreply@example.test",
            content="Your verification code is 123456.",
            response_suppressed=True,
            response_suppression_reason="адрес отправителя не принимает ответы",
        ), db, user)

        assert result["status"] == "filtered"
        assert result["tasks"] == []
        assert result["drafts"] == []
        assert result["risks"] == []
        assert "Служебное письмо без действий" in result["summary"]


def test_bulk_context_confirmation_dedicated_payload():
    payload = BulkContextConfirmation(message_ids=[3, 4], project_id=2, contract_id=7)
    assert payload.message_ids == [3, 4]
    assert payload.project_id == 2
    assert payload.contract_id == 7
