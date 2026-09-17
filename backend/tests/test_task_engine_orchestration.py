"""create_tasks_from_files end-to-end with the LLM path: amount/assignee_hint/
extraction_method persistence, assignee-hint matching, and the fallback note."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.integration_types import StorageObject
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.task_engine import create_tasks_from_files
from app import document_extraction as de


@pytest.fixture
def world(db_session, user_factory):
    owner = user_factory(name="Пётр Петров")
    member = user_factory(name="Анна Иванова")
    org = Organization(name="Synthetic Org")
    db_session.add(org)
    db_session.flush()
    project = Project(name="Synthetic Project", organization_id=org.id)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))
    db_session.add(ProjectMember(project_id=project.id, user_id=member.id, role="editor"))
    db_session.commit()
    return db_session, owner, member, project


def _fake_provider(payload):
    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=True)
    provider.extract_fields.return_value = payload
    return provider


def _obligation(**overrides):
    base = {
        "title": "Подготовить отчёт", "evidence_quote": "Иванова обязана подготовить отчёт",
        "due_date": "2026-09-20", "due_date_evidence_quote": "не позднее 20.09.2026",
        "assignee_hint": None, "assignee_evidence_quote": None,
        "amount": None, "amount_currency": None, "amount_evidence_quote": None,
        "confidence": "high",
    }
    base.update(overrides)
    return base


def test_llm_task_persists_amount_and_extraction_method(world, monkeypatch):
    db, _owner, _member, project = world
    text = "Иванова обязана подготовить отчёт не позднее 20.09.2026. Сумма договора 250000 руб."
    payload = {
        "obligations": [_obligation(
            amount=250000, amount_currency="RUB", amount_evidence_quote="Сумма договора 250000 руб",
        )],
        "response_candidates": [], "risks": [], "decisions": [],
    }
    monkeypatch.setattr(de, "configured_ai_provider", lambda: _fake_provider(payload))
    file = StorageObject(id="f1", name="doc.txt", mime_type="text/plain", parent_id="root", content_text=text)
    tasks = create_tasks_from_files(db, project.id, None, [file])
    assert len(tasks) == 1
    task = tasks[0]
    assert task.extraction_method == "llm"
    assert task.amount == Decimal("250000")
    assert task.amount_currency == "RUB"
    assert task.due_date.isoformat() == "2026-09-20"


def test_assignee_hint_is_matched_to_a_real_project_member(world, monkeypatch):
    db, owner, member, project = world
    text = "Иванова обязана подготовить отчёт."
    payload = {
        "obligations": [_obligation(
            assignee_hint="Иванова", assignee_evidence_quote="Иванова обязана",
        )],
        "response_candidates": [], "risks": [], "decisions": [],
    }
    monkeypatch.setattr(de, "configured_ai_provider", lambda: _fake_provider(payload))
    file = StorageObject(id="f1", name="doc.txt", mime_type="text/plain", parent_id="root", content_text=text)
    task, = create_tasks_from_files(db, project.id, None, [file])
    assert task.assignee_user_id == member.id  # matched real member, not the role-based default (owner)
    assert task.assignee_hint == "Иванова"
    assert "не найден среди участников" not in task.description


def test_unmatched_assignee_hint_falls_back_to_role_default_and_is_noted(world, monkeypatch):
    db, owner, _member, project = world
    text = "Сидоров обязан подготовить отчёт."
    payload = {
        "obligations": [_obligation(
            title="Подготовить отчёт", evidence_quote="Сидоров обязан подготовить отчёт",
            assignee_hint="Сидоров", assignee_evidence_quote="Сидоров обязан",
        )],
        "response_candidates": [], "risks": [], "decisions": [],
    }
    monkeypatch.setattr(de, "configured_ai_provider", lambda: _fake_provider(payload))
    file = StorageObject(id="f1", name="doc.txt", mime_type="text/plain", parent_id="root", content_text=text)
    task, = create_tasks_from_files(db, project.id, None, [file])
    assert task.assignee_user_id == owner.id  # role-based fallback, no member named Сидоров
    assert task.assignee_hint == "Сидоров"
    assert "Сидоров" in task.description and "не найден среди участников" in task.description


def test_llm_failure_notes_the_reason_and_leaves_amount_and_assignee_empty(world, monkeypatch):
    db, owner, _member, project = world
    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=False)
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    file = StorageObject(id="f1", name="doc.txt", mime_type="text/plain", parent_id="root",
                         content_text="Исполнитель обязан подготовить акт до 20.09.2026.")
    task, = create_tasks_from_files(db, project.id, None, [file])
    assert task.extraction_method == "regex"
    assert task.amount is None
    assert task.assignee_hint is None
    assert task.assignee_user_id == owner.id
    assert "AI не настроен" in task.description
    assert "ответственный и сумма не извлечены" in task.description
