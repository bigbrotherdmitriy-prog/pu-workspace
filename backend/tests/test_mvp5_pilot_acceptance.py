from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.api.tasks as tasks_api
import app.models  # noqa: F401 - register every mapped table before create_all
from app.api.ai_secretary import IncomingMessage, ingest_message
from app.api.dashboard import project_dashboard
from app.api.management import ObligationUpdate, refresh_notifications, update_obligation
from app.api.responses import DraftUpdate, update_draft
from app.api.tasks import ExternalActionApproval, approve_external
from app.database import Base
from app.daily_briefing import build_daily_briefing
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.management import Obligation
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.user import User


def test_pilot_communication_to_action_requires_human_approval(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        organization = Organization(name="Пилотная организация")
        user = User(name="Руководитель проекта", email="pilot@example.test", is_admin=False)
        db.add_all([organization, user])
        db.flush()
        project = Project(name="Модернизация объекта", organization_id=organization.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        contract = Contract(
            project_id=project.id,
            number="ГК-08-194/25",
            title="Модернизация системы",
            counterparty="Налог-Сервис",
            status="active",
        )
        db.add(contract)
        db.commit()

        due_date = date.today() - timedelta(days=1)
        content = (
            "По договору ГК-08-194/25 с Налог-Сервис просим подготовить и направить "
            f"исправленный акт не позднее {due_date.strftime('%d.%m.%Y')}. "
            "Подтвердите срок ответа заказчику."
        )
        result = ingest_message(
            IncomingMessage(
                project_id=project.id,
                source_type="email",
                source_external_id="pilot-email-1",
                source_name="Письмо заказчика",
                source_sender="customer@example.test",
                content=content,
            ),
            db,
            user,
        )

        message = db.get(Message, result["id"])
        task = db.scalar(select(Task).where(Task.message_id == message.id))
        obligation = db.scalar(select(Obligation).where(Obligation.task_id == task.id))
        draft = db.scalar(select(ResponseDraft).where(ResponseDraft.message_id == message.id))

        assert message.contract_id == contract.id
        assert message.context_confirmed is True
        assert task.due_date == due_date
        assert task.external_action_status == "proposed"
        assert task.needs_review is True
        assert obligation.status == "needs_confirmation"
        assert draft.status == "draft"

        update_obligation(obligation.id, ObligationUpdate(status="confirmed"), db, user)
        update_draft(draft.id, DraftUpdate(status="approved"), db, user)

        adapter = SimpleNamespace(provider="pilot_action_adapter")
        monkeypatch.setattr(tasks_api, "configured_action_adapter", lambda _project_id, _db: adapter)
        monkeypatch.setattr(
            tasks_api,
            "publish_actions",
            lambda *_args, **_kwargs: SimpleNamespace(
                task_synced=1,
                task_failed=0,
                calendar_synced=1,
                calendar_failed=0,
            ),
        )
        monkeypatch.setattr(
            tasks_api,
            "external_id_for",
            lambda _db, **kwargs: f"pilot-{kwargs['resource_type']}-1",
        )

        published = approve_external(
            task.id,
            ExternalActionApproval(publish_task=True, publish_calendar=True),
            db,
            user,
        )
        notifications = refresh_notifications(project.id, db, user)
        dashboard = project_dashboard(project.id, db, user)
        briefing = build_daily_briefing(db, project.id, today=date.today())

        assert published["provider"] == "pilot_action_adapter"
        assert published["external_action_status"] == "executed"
        assert db.get(Task, task.id).needs_review is False
        assert notifications["unread"] == 1
        assert notifications["notifications"][0]["kind"] == "overdue"
        assert dashboard["summary"]["overdue_tasks"] == 1
        assert dashboard["summary"]["overdue_obligations"] == 1
        assert briefing["summary"]["overdue_tasks"] == 1
        # The briefing is an action list: a linked obligation and its task
        # are one human decision, while the domain dashboard retains both
        # underlying entity counts.
        assert briefing["summary"]["overdue_obligations"] == 0
        assert len([
            item for item in briefing["attention"]
            if item["kind"] in {"overdue_task", "overdue_obligation"}
        ]) == 1
        assert briefing["attention"][0]["priority"] == "critical"
        assert briefing["external_actions_created"] is False
        assert db.scalar(select(AuditLog).where(AuditLog.action == "external_task_action")) is not None
