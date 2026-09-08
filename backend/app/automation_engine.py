from __future__ import annotations

import calendar
import hashlib
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.automation_rule import AutomationRule, AutomationRun
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.user import User


MONTHS_RU = (
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def monthly_date(year: int, month: int, day_of_month: int) -> date:
    return date(year, month, min(day_of_month, calendar.monthrange(year, month)[1]))


def next_monthly_date(day_of_month: int, after: date) -> date:
    candidate = monthly_date(after.year, after.month, day_of_month)
    if candidate >= after:
        return candidate
    year, month = (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
    return monthly_date(year, month, day_of_month)


def following_monthly_date(day_of_month: int, current: date) -> date:
    year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
    return monthly_date(year, month, day_of_month)


def _actor(db: Session, project_id: int) -> User | None:
    role_order = {"owner": 0, "manager": 1, "editor": 2, "member": 3}
    rows = db.execute(
        select(ProjectMember, User).join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
    ).all()
    return min(rows, key=lambda pair: (role_order.get(pair[0].role, 9), pair[1].id))[1] if rows else None


def _render(template: str, project: Project, contract: Contract | None, scheduled_for: date) -> str:
    next_year, next_month = (
        (scheduled_for.year + 1, 1) if scheduled_for.month == 12
        else (scheduled_for.year, scheduled_for.month + 1)
    )
    values = {
        "project": project.name,
        "contract": contract.number if contract else "без договора",
        "month": f"{MONTHS_RU[scheduled_for.month]} {scheduled_for.year}",
        "next_month": f"{MONTHS_RU[next_month]} {next_year}",
        "date": scheduled_for.strftime("%d.%m.%Y"),
    }
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result


def prepare_rule_run(db: Session, rule: AutomationRule, scheduled_for: date | None = None) -> AutomationRun:
    # Capture the requested occurrence before refreshing a possibly stale rule.
    # The scheduled date still controls rendering, due dates and next_run_on.
    run_date = scheduled_for or rule.next_run_on
    # Serialize upgraded PostgreSQL callers, including reuse of legacy runs
    # whose scheduled_for was an arbitrary manual day rather than a period key.
    rule = db.scalar(select(AutomationRule).where(AutomationRule.id == rule.id)
        .with_for_update().execution_options(populate_existing=True))
    if rule is None:
        raise ValueError("Automation rule no longer exists")
    period_key = run_date.replace(day=1) if rule.kind == "monthly_email" else run_date
    lookup = select(AutomationRun).where(AutomationRun.rule_id == rule.id)
    if rule.kind == "monthly_email":
        # Include old noncanonical rows without rewriting their identity or
        # creating a second pair. Existing duplicates require separate review.
        period_end = monthly_date(run_date.year, run_date.month, 31)
        lookup = lookup.where(AutomationRun.scheduled_for >= period_key,
                              AutomationRun.scheduled_for <= period_end)
    else:
        lookup = lookup.where(AutomationRun.scheduled_for == run_date)
    existing = db.scalar(lookup.order_by(AutomationRun.scheduled_for, AutomationRun.id).limit(1))
    if existing:
        return existing
    project = db.get(Project, rule.project_id)
    actor = _actor(db, rule.project_id)
    if project is None or actor is None:
        raise ValueError("Automation rule has no project actor")
    contract = db.get(Contract, rule.contract_id) if rule.contract_id else None
    subject = _render(rule.subject_template, project, contract, run_date)
    body = _render(rule.body_template, project, contract, run_date)
    task_title = _render(rule.task_title_template, project, contract, run_date)
    source_id = f"automation:{rule.id}:{period_key.isoformat()}"
    digest = hashlib.sha256(source_id.encode()).hexdigest()
    task = Task(
        project_id=rule.project_id, assignee_user_id=actor.id, created_by_user_id=actor.id,
        title=task_title, description=body, due_date=run_date, priority="normal",
        source_type="automation_rule", source_file_id=source_id,
        source_file_name=rule.name, source_excerpt=body, source_excerpt_hash=digest,
        confidence=1.0, needs_review=True, external_action_status="proposed",
    )
    draft = ResponseDraft(
        project_id=rule.project_id, reviewer_user_id=actor.id,
        subject=subject, body=body, recipient_to=rule.recipient_to, status="draft",
        source_file_id=source_id, source_file_name=rule.name,
        source_excerpt=body, source_excerpt_hash=digest, confidence=1.0,
    )
    db.add_all([task, draft]); db.flush()
    run = AutomationRun(
        rule_id=rule.id, scheduled_for=period_key,
        task_id=task.id, response_draft_id=draft.id, status="prepared",
    )
    db.add(run)
    rule.last_run_on = run_date
    rule.next_run_on = following_monthly_date(rule.day_of_month, run_date)
    db.commit(); db.refresh(run)
    return run


def run_due_rules(db: Session, today: date | None = None) -> dict[str, int]:
    current = today or date.today()
    # Keep the due-date snapshot even if another scheduler advances a cached
    # rule while prepare_rule_run waits for its row lock.
    rules = list(db.execute(select(AutomationRule, AutomationRule.next_run_on).where(
        AutomationRule.active.is_(True), AutomationRule.next_run_on <= current,
    ).order_by(AutomationRule.next_run_on, AutomationRule.id)))
    prepared = failed = 0
    for rule, scheduled_for in rules:
        try:
            prepare_rule_run(db, rule, scheduled_for)
            prepared += 1
        except Exception:
            db.rollback()
            failed += 1
    return {"due": len(rules), "prepared": prepared, "failed": failed}
