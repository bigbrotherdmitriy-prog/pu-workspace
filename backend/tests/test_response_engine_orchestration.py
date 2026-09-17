"""create_response_drafts end-to-end: LLM path, regex fallback, ensure_response,
and cache-sharing with the other two engines via document_extraction."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.integration_types import StorageObject
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.response_engine import create_response_drafts
from app import document_extraction as de


@pytest.fixture
def world(db_session, user_factory):
    user = user_factory()
    org = Organization(name="Synthetic Org")
    db_session.add(org)
    db_session.flush()
    project = Project(name="Synthetic Project", organization_id=org.id)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    db_session.commit()
    return db_session, user, project


def _fake_provider(payload):
    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=True)
    provider.extract_fields.return_value = payload
    return provider


def test_llm_response_candidate_gets_the_same_template_body_as_regex(world, monkeypatch):
    db, _user, project = world
    text = "Прошу подтвердить получение оплаты по акту."
    monkeypatch.setattr(de, "configured_ai_provider", lambda: _fake_provider({
        "obligations": [], "risks": [], "decisions": [],
        "response_candidates": [{"evidence_quote": "Прошу подтвердить получение оплаты по акту", "confidence": "high"}],
    }))
    file = StorageObject(id="f1", name="letter.txt", mime_type="text/plain", parent_id="root", content_text=text)
    drafts = create_response_drafts(db, project.id, None, [file])
    assert len(drafts) == 1
    assert "Прошу подтвердить получение оплаты по акту" in drafts[0].body
    assert drafts[0].confidence == 0.92
    assert "Ответ на запрос из документа" in drafts[0].subject


def test_no_api_key_falls_back_to_regex_response_extraction(world, monkeypatch):
    db, _user, project = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    file = StorageObject(id="f1", name="letter.txt", mime_type="text/plain", parent_id="root",
                         content_text="Просим предоставить акт выполненных работ.")
    drafts = create_response_drafts(db, project.id, None, [file])
    assert len(drafts) == 1
    assert drafts[0].confidence == 0.80


def test_ensure_response_bonus_applies_regardless_of_extraction_path(world, monkeypatch):
    db, _user, project = world
    monkeypatch.setattr(de, "configured_ai_provider", lambda: _fake_provider({
        "obligations": [], "risks": [], "decisions": [], "response_candidates": [],
    }))
    file = StorageObject(id="f1", name="letter.txt", mime_type="text/plain", parent_id="root",
                         content_text="Направляем подписанный акт выполненных работ во вложении.")
    drafts = create_response_drafts(db, project.id, None, [file], ensure_response=True)
    assert len(drafts) == 1
    assert drafts[0].confidence == 0.55
    assert "Информация получена" in drafts[0].body


def test_repeated_call_does_not_duplicate_drafts(world, monkeypatch):
    db, _user, project = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    file = StorageObject(id="f1", name="letter.txt", mime_type="text/plain", parent_id="root",
                         content_text="Просим предоставить акт выполненных работ.")
    first = create_response_drafts(db, project.id, None, [file])
    assert len(first) == 1
    second = create_response_drafts(db, project.id, None, [file])
    assert second == []
    assert len(db_session_all_drafts(db)) == 1


def db_session_all_drafts(db):
    from sqlalchemy import select
    return list(db.scalars(select(ResponseDraft)))


def test_task_engine_and_response_engine_share_one_llm_call_per_file(world, monkeypatch):
    """The whole point of the combined-call design: calling create_tasks_from_files
    then create_response_drafts on the same file list must hit the provider once."""
    from app.task_engine import create_tasks_from_files

    db, _user, project = world
    calls = []
    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=True)

    def fake_extract_fields(text, filename, project_name, member_names):
        calls.append(filename)
        return {
            "obligations": [{
                "title": "Подготовить акт", "evidence_quote": "Иванов обязан подготовить акт",
                "due_date": None, "due_date_evidence_quote": None,
                "assignee_hint": None, "assignee_evidence_quote": None,
                "amount": None, "amount_currency": None, "amount_evidence_quote": None,
                "confidence": "high",
            }],
            "response_candidates": [{"evidence_quote": "Просим подтвердить получение", "confidence": "medium"}],
            "risks": [], "decisions": [],
        }

    provider.extract_fields.side_effect = fake_extract_fields
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    file = StorageObject(id="f1", name="letter.txt", mime_type="text/plain", parent_id="root",
                         content_text="Иванов обязан подготовить акт. Просим подтвердить получение.")
    tasks = create_tasks_from_files(db, project.id, None, [file])
    drafts = create_response_drafts(db, project.id, None, [file])
    assert len(tasks) == 1
    assert len(drafts) == 1
    assert calls == ["letter.txt"]  # exactly one LLM call for both engines combined
