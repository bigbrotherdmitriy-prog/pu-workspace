"""Behavioral coverage for MVP-7 monthly automation rules.

docs/acceptance/mvp7-ai-secretary-control/CLOSURE_ACT_RU.md found that only the
date helpers were tested; prepare_rule_run / run_due_rules and the HTTP
pause/run-now controls had no coverage at all. These tests close that gap
against the acceptance list in docs/mvp7-ai-secretary-control.md.
"""
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api import ai_secretary as api
from app.automation_engine import prepare_rule_run, run_due_rules
from app.models.automation_rule import AutomationRule, AutomationRun
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.execution_finance import CashFlowEntry
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task


@pytest.fixture
def world(db_session, user_factory):
    db = db_session
    owner, manager = user_factory(), user_factory()
    org = Organization(name="MVP7 org"); db.add(org); db.flush()
    project = Project(name="Проект регламентов", organization_id=org.id); db.add(project); db.flush()
    db.add_all([
        ProjectMember(project_id=project.id, user_id=owner.id, role="owner"),
        ProjectMember(project_id=project.id, user_id=manager.id, role="manager"),
    ])
    contract = Contract(project_id=project.id, number="Д-7/2026", title="Договор регламента")
    document = Document(project_id=project.id, name="Регламент.pdf", source="local_upload", status="ready")
    db.add_all([contract, document]); db.flush()
    db.commit()
    return db, owner, manager, project, contract, document


def make_rule(db, project, contract=None, document=None, **overrides):
    values = dict(
        project_id=project.id, contract_id=contract.id if contract else None,
        source_document_id=document.id if document else None,
        created_by_user_id=1, name="Ежемесячный отчёт", day_of_month=31,
        recipient_to="client@example.test",
        subject_template="Отчёт {project} за {month}",
        body_template="По договору {contract}: отчёт за {month}, следующий период {next_month}, дата {date}.",
        task_title_template="Подготовить отчёт за {month}",
        active=True, next_run_on=date(2027, 2, 28),
    )
    values.update(overrides)
    rule = AutomationRule(**values); db.add(rule); db.commit()
    return rule


def counts(db):
    return (db.scalar(select(func.count()).select_from(Task)),
            db.scalar(select(func.count()).select_from(ResponseDraft)),
            db.scalar(select(func.count()).select_from(AutomationRun)))


def test_run_creates_one_task_and_one_unsent_draft_linked_to_rule_context(world):
    db, owner, _manager, project, contract, document = world
    rule = make_rule(db, project, contract, document)
    run = prepare_rule_run(db, rule)

    task, draft = db.get(Task, run.task_id), db.get(ResponseDraft, run.response_draft_id)
    assert run.status == "prepared" and run.scheduled_for == date(2027, 2, 28)
    # Templates render project, contract, current and next month.
    assert task.title == "Подготовить отчёт за февраль 2027"
    assert draft.subject == "Отчёт Проект регламентов за февраль 2027"
    assert "Д-7/2026" in draft.body and "март 2027" in draft.body and "28.02.2027" in draft.body
    # Rule stays linked to project, contract and source document.
    assert (rule.project_id, rule.contract_id, rule.source_document_id) == (project.id, contract.id, document.id)
    assert draft.contract_id == contract.id
    # Nothing leaves the system without the user: task is a proposal, draft is unsent.
    assert task.needs_review is True and task.external_action_status == "proposed"
    assert draft.status == "draft"
    # The owner (highest role) is the actor.
    assert task.assignee_user_id == owner.id and draft.reviewer_user_id == owner.id
    # Last-run result is recorded on the rule; next run rolls to the 31st -> last day of March.
    assert rule.last_run_on == date(2027, 2, 28) and rule.next_run_on == date(2027, 3, 31)


def test_same_period_is_idempotent(world):
    db, _owner, _manager, project, contract, document = world
    rule = make_rule(db, project, contract, document)
    first = prepare_rule_run(db, rule, date(2027, 2, 28))
    before = counts(db)
    second = prepare_rule_run(db, rule, date(2027, 2, 28))
    assert second.id == first.id
    assert counts(db) == before == (1, 1, 1)


def test_run_never_creates_a_payment_fact(world):
    db, _owner, _manager, project, contract, document = world
    prepare_rule_run(db, make_rule(db, project, contract, document))
    assert db.scalar(select(func.count()).select_from(CashFlowEntry)) == 0


def test_run_due_rules_skips_paused_and_future_rules_and_isolates_failures(world, user_factory):
    db, _owner, _manager, project, contract, document = world
    due = make_rule(db, project, contract, document, name="due", next_run_on=date(2027, 2, 28))
    paused = make_rule(db, project, contract, document, name="paused", active=False, next_run_on=date(2027, 2, 1))
    future = make_rule(db, project, contract, document, name="future", next_run_on=date(2027, 4, 30))
    # A project with no members has no actor -> this rule must fail without blocking the others.
    lonely = Project(name="Без участников", organization_id=project.organization_id); db.add(lonely); db.commit()
    broken = make_rule(db, lonely, name="broken", next_run_on=date(2027, 2, 1))

    result = run_due_rules(db, today=date(2027, 3, 1))

    assert result == {"due": 2, "prepared": 1, "failed": 1}
    rule_ids = set(db.scalars(select(AutomationRun.rule_id)))
    assert rule_ids == {due.id}
    assert paused.id not in rule_ids and future.id not in rule_ids and broken.id not in rule_ids


def test_http_pause_resume_and_run_now_require_manager_and_never_send(world, user_factory):
    db, owner, manager, project, contract, document = world
    outsider = user_factory()
    created = api.create_automation_rule(api.AutomationRuleCreate(
        project_id=project.id, contract_id=contract.id, source_document_id=document.id,
        name="Отчёт", day_of_month=31, recipient_to="client@example.test",
        subject_template="Отчёт {month}", body_template="Тело {contract}",
        task_title_template="Задача {month}",
    ), db, manager)
    rule_id = created["id"]

    with pytest.raises(HTTPException):
        api.update_automation_rule(rule_id, api.AutomationRuleState(active=False), db, outsider)
    paused = api.update_automation_rule(rule_id, api.AutomationRuleState(active=False), db, manager)
    assert paused["active"] is False
    assert run_due_rules(db, today=date(2099, 1, 1)) == {"due": 0, "prepared": 0, "failed": 0}
    assert api.update_automation_rule(rule_id, api.AutomationRuleState(active=True), db, owner)["active"] is True

    with pytest.raises(HTTPException):
        api.run_automation_rule_now(rule_id, db, outsider)
    result = api.run_automation_rule_now(rule_id, db, manager)
    assert result["sent"] is False
    assert db.get(ResponseDraft, result["response_draft_id"]).status == "draft"
    # Card shows the last run with links to its task and draft.
    card = api.list_automation_rules(project.id, db, manager)["rules"][0]
    assert card["last_run_on"] is not None
    assert card["runs"][0]["task_id"] == result["task_id"]
    assert card["runs"][0]["response_draft_id"] == result["response_draft_id"]
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.entity_type.in_(("automation_rule", "automation_run")))))
    assert {"automation_rule_created", "automation_rule_state_changed", "automation_rule_run_prepared"} <= actions


def test_rule_rejects_foreign_contract_and_document(world, user_factory):
    db, _owner, manager, project, _contract, _document = world
    other = Project(name="Чужой", organization_id=project.organization_id); db.add(other); db.flush()
    foreign_contract = Contract(project_id=other.id, number="X", title="X")
    foreign_doc = Document(project_id=other.id, name="x.pdf", source="local_upload", status="ready")
    db.add_all([foreign_contract, foreign_doc]); db.commit()
    base = dict(project_id=project.id, name="r", day_of_month=5, recipient_to="a@example.test",
                subject_template="s", body_template="b", task_title_template="t")
    with pytest.raises(HTTPException):
        api.create_automation_rule(api.AutomationRuleCreate(**base, contract_id=foreign_contract.id), db, manager)
    with pytest.raises(HTTPException):
        api.create_automation_rule(api.AutomationRuleCreate(**base, source_document_id=foreign_doc.id), db, manager)


def test_scheduler_derives_short_month_dates_and_never_repeats_a_period(world):
    db, _owner, _manager, project, contract, document = world
    rule = make_rule(db, project, contract, document, next_run_on=date(2027, 1, 31))

    assert run_due_rules(db, today=date(2027, 2, 1))["prepared"] == 1
    db.refresh(rule)
    # 31st anchor: February date is computed by the engine, not seeded by the test.
    assert (rule.last_run_on, rule.next_run_on) == (date(2027, 1, 31), date(2027, 2, 28))

    assert run_due_rules(db, today=date(2027, 3, 1))["prepared"] == 1
    db.refresh(rule)
    assert (rule.last_run_on, rule.next_run_on) == (date(2027, 2, 28), date(2027, 3, 31))

    before = counts(db)
    assert run_due_rules(db, today=date(2027, 3, 1))["prepared"] == 0
    assert counts(db) == before == (2, 2, 2)
    periods = sorted(db.scalars(select(AutomationRun.scheduled_for).where(AutomationRun.rule_id == rule.id)))
    assert periods == [date(2027, 1, 31), date(2027, 2, 28)]
