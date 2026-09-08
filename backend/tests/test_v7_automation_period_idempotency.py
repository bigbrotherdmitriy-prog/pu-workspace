"""Synthetic monthly-period replay through real SQLAlchemy sessions."""
from datetime import date

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.database import Base
from app.automation_engine import prepare_rule_run, run_due_rules, monthly_date
from app.models.automation_rule import AutomationRule, AutomationRun
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.user import User


def seed(sessions):
    with sessions.begin() as db:
        db.add(Organization(id=1, name="Synthetic period tenant"))
        db.add(User(id=2, name="Synthetic owner", email="period@example.invalid"))
        db.flush()
        db.add(Project(id=4, name="Synthetic period project", organization_id=1))
        db.flush()
        db.add(ProjectMember(project_id=4, user_id=2, role="owner"))


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "period.db"))
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    seed(factory)
    yield factory
    engine.dispose()


def rule_id(sessions, *, scheduled=date(2028, 2, 29), day=31):
    with sessions.begin() as db:
        rule = AutomationRule(project_id=4, created_by_user_id=2,
            name="Synthetic monthly notice", kind="monthly_email", day_of_month=day,
            recipient_to="recipient@example.invalid", subject_template="{month}",
            body_template="Prepare {date}; next {next_month}", task_title_template="Review {month}",
            active=True, next_run_on=scheduled)
        db.add(rule)
        db.flush()
        return rule.id


def run(sessions, rule_id, when):
    with sessions() as db:
        row = prepare_rule_run(db, db.get(AutomationRule, rule_id), when)
        return row.id, row.task_id, row.response_draft_id


def counts(sessions):
    with sessions() as db:
        return tuple(db.scalar(select(func.count()).select_from(model))
            for model in (AutomationRun, Task, ResponseDraft))


@pytest.mark.parametrize("year,month,last_day", [(2027, 2, 28), (2028, 2, 29), (2028, 4, 30)])
@pytest.mark.parametrize("day", [29, 30, 31])
def test_two_manual_days_share_period_and_keep_original_due_semantics(sessions, year, month, last_day, day):
    scheduled = monthly_date(year, month, day)
    assert scheduled == date(year, month, min(day, last_day))
    identifier = rule_id(sessions, scheduled=scheduled, day=day)
    first_date = date(year, month, 5)
    first = run(sessions, identifier, first_date)
    assert run(sessions, identifier, scheduled) == first
    assert counts(sessions) == (1, 1, 1)
    with sessions() as db:
        row = db.get(AutomationRun, first[0])
        task, draft = db.get(Task, first[1]), db.get(ResponseDraft, first[2])
        rule = db.get(AutomationRule, identifier)
        assert row.scheduled_for == date(year, month, 1)
        assert task.due_date == first_date and rule.last_run_on == first_date
        assert rule.next_run_on == monthly_date(year, month + 1, day)
        assert first_date.strftime("%d.%m.%Y") in draft.body
        assert task.needs_review and task.external_action_status == "proposed"
        assert draft.status == "draft" and draft.sent_at is None and draft.sent_external_id is None


@pytest.mark.parametrize("manual_first", [True, False])
def test_manual_and_scheduler_snapshot_share_month(sessions, manual_first):
    identifier = rule_id(sessions)
    first, second = (date(2028, 2, 10), date(2028, 2, 29)) if manual_first else (
        date(2028, 2, 29), date(2028, 2, 10))
    assert run(sessions, identifier, first) == run(sessions, identifier, second)
    with sessions() as db:
        assert run_due_rules(db, date(2028, 2, 29)) == {"due": 0, "prepared": 0, "failed": 0}
    assert counts(sessions) == (1, 1, 1)


@pytest.mark.parametrize("months", [((2028, 2), (2028, 3)), ((2028, 12), (2029, 1))])
def test_adjacent_months_get_distinct_pairs_and_replay(sessions, months):
    identifier = rule_id(sessions)
    dates = [date(year, month, 15) for year, month in months]
    first, second = [run(sessions, identifier, when) for when in dates]
    assert set(first).isdisjoint(set(second))
    assert run(sessions, identifier, dates[0].replace(day=20)) == first
    assert run(sessions, identifier, dates[1].replace(day=21)) == second
    assert counts(sessions) == (2, 2, 2)


def test_legacy_noncanonical_run_is_reused_without_rewrite(sessions):
    identifier = rule_id(sessions)
    first = run(sessions, identifier, date(2028, 2, 20))
    with sessions.begin() as db:
        legacy = db.get(AutomationRun, first[0])
        legacy.scheduled_for = date(2028, 2, 20)
        legacy.status = "legacy_prepared"
    assert run(sessions, identifier, date(2028, 2, 29)) == first
    with sessions() as db:
        legacy = db.get(AutomationRun, first[0])
        assert legacy.scheduled_for == date(2028, 2, 20) and legacy.status == "legacy_prepared"
    assert counts(sessions) == (1, 1, 1)


def test_different_rules_do_not_share_period_identity(sessions):
    first, second = rule_id(sessions), rule_id(sessions)
    assert run(sessions, first, date(2028, 2, 10)) != run(sessions, second, date(2028, 2, 10))
    assert counts(sessions) == (2, 2, 2)


def test_real_due_scheduler_prepares_only_due_month_then_manual_reuses(sessions):
    identifier = rule_id(sessions)
    with sessions() as db:
        assert run_due_rules(db, date(2028, 2, 28))["due"] == 0
        assert run_due_rules(db, date(2028, 2, 29)) == {"due": 1, "prepared": 1, "failed": 0}
        first = db.scalar(select(AutomationRun))
        assert db.get(Task, first.task_id).due_date == date(2028, 2, 29)
        expected = first.id, first.task_id, first.response_draft_id
    assert run(sessions, identifier, date(2028, 2, 10)) == expected
    assert counts(sessions) == (1, 1, 1)
    with sessions() as db:
        assert run_due_rules(db, date(2028, 3, 31))["prepared"] == 1
    assert counts(sessions) == (2, 2, 2)


def test_scheduler_passes_original_due_snapshot_when_rule_changes(sessions, monkeypatch):
    import app.automation_engine as engine
    identifier = rule_id(sessions)
    prepare = engine.prepare_rule_run
    def advanced_elsewhere(db, rule, scheduled_for=None):
        rule.next_run_on = date(2028, 3, 31)
        return prepare(db, rule, scheduled_for)
    monkeypatch.setattr(engine, "prepare_rule_run", advanced_elsewhere)
    with sessions() as db:
        assert engine.run_due_rules(db, date(2028, 2, 29))["prepared"] == 1
        row = db.scalar(select(AutomationRun))
        assert row.scheduled_for == date(2028, 2, 1)
        assert db.get(Task, row.task_id).due_date == date(2028, 2, 29)
        assert db.get(AutomationRule, identifier).next_run_on == date(2028, 3, 31)


def test_existing_legacy_duplicates_are_not_rewritten_or_expanded(sessions):
    identifier = rule_id(sessions)
    first = run(sessions, identifier, date(2028, 2, 10))
    with sessions.begin() as db:
        db.get(AutomationRun, first[0]).scheduled_for = date(2028, 2, 10)
        db.add(AutomationRun(rule_id=identifier, scheduled_for=date(2028, 2, 20),
            status="legacy_incomplete"))
    assert run(sessions, identifier, date(2028, 2, 29)) == first
    assert counts(sessions) == (2, 1, 1)


def test_nonmonthly_kind_keeps_legacy_date_identity(sessions):
    identifier = rule_id(sessions)
    with sessions.begin() as db:
        db.get(AutomationRule, identifier).kind = "legacy_other_kind"
    first = run(sessions, identifier, date(2028, 2, 10))
    second = run(sessions, identifier, date(2028, 2, 11))
    assert first != second
    assert counts(sessions) == (2, 2, 2)


def test_failure_after_pair_flush_rolls_back_then_replays(sessions):
    identifier = rule_id(sessions)
    with sessions() as db:
        def crash_before_run_flush(session, *_args):
            if any(isinstance(row, AutomationRun) for row in session.new):
                raise RuntimeError("synthetic process interruption")
        event.listen(db, "before_flush", crash_before_run_flush)
        with pytest.raises(RuntimeError, match="synthetic process interruption"):
            prepare_rule_run(db, db.get(AutomationRule, identifier), date(2028, 2, 10))
        db.rollback()
    assert counts(sessions) == (0, 0, 0)
    with sessions() as db:
        assert db.get(AutomationRule, identifier).next_run_on == date(2028, 2, 29)
    first = run(sessions, identifier, date(2028, 2, 11))
    assert run(sessions, identifier, date(2028, 2, 29)) == first
    assert counts(sessions) == (1, 1, 1)
