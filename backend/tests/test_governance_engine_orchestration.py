"""create_governance_items end-to-end with the LLM path, plus the three-engine
single-LLM-call guarantee extended to governance."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.integration_types import StorageObject
from app.governance_engine import create_governance_items
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
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


def test_llm_risk_and_decision_are_persisted_with_mapped_confidence(world, monkeypatch):
    db, _user, project = world
    text = "Есть риск срыва поставки. Требуется решение по варианту B."
    payload = {
        "obligations": [], "response_candidates": [],
        "risks": [{
            "title": "Риск срыва поставки", "evidence_quote": "Есть риск срыва поставки",
            "kind": "risk", "criticality": "high", "confidence": "high",
        }],
        "decisions": [{"question": "Вариант B", "evidence_quote": "Требуется решение по варианту B", "confidence": "medium"}],
    }
    monkeypatch.setattr(de, "configured_ai_provider", lambda: _fake_provider(payload))
    file = StorageObject(id="f1", name="doc.txt", mime_type="text/plain", parent_id="root", content_text=text)
    risks, decisions = create_governance_items(db, project.id, [file])
    assert len(risks) == 1 and risks[0].criticality == "high" and risks[0].confidence == 0.92
    assert len(decisions) == 1 and decisions[0].question == "Вариант B" and decisions[0].confidence == 0.75


def test_repeated_call_does_not_duplicate_governance_items(world, monkeypatch):
    db, _user, project = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    file = StorageObject(id="f1", name="doc.txt", mime_type="text/plain", parent_id="root",
                         content_text="Есть риск срыва поставки по договору.")
    first_risks, _ = create_governance_items(db, project.id, [file])
    assert len(first_risks) == 1
    second_risks, _ = create_governance_items(db, project.id, [file])
    assert second_risks == []


def test_all_three_engines_share_one_llm_call_per_file(world, monkeypatch):
    from app.task_engine import create_tasks_from_files
    from app.response_engine import create_response_drafts

    db, _user, project = world
    calls = []
    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=True)

    def fake_extract_fields(text, filename, project_name, member_names):
        calls.append(filename)
        return {
            "obligations": [{
                "title": "Подготовить акт", "evidence_quote": "Исполнитель обязан подготовить акт",
                "due_date": None, "due_date_evidence_quote": None,
                "assignee_hint": None, "assignee_evidence_quote": None,
                "amount": None, "amount_currency": None, "amount_evidence_quote": None,
                "confidence": "high",
            }],
            "response_candidates": [{"evidence_quote": "Просим подтвердить получение", "confidence": "medium"}],
            "risks": [{
                "title": "Риск задержки", "evidence_quote": "Есть риск задержки поставки",
                "kind": "risk", "criticality": "medium", "confidence": "medium",
            }],
            "decisions": [],
        }

    provider.extract_fields.side_effect = fake_extract_fields
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    file = StorageObject(id="f1", name="doc.txt", mime_type="text/plain", parent_id="root", content_text=(
        "Исполнитель обязан подготовить акт. Просим подтвердить получение. Есть риск задержки поставки."
    ))
    tasks = create_tasks_from_files(db, project.id, None, [file])
    drafts = create_response_drafts(db, project.id, None, [file])
    risks, decisions = create_governance_items(db, project.id, [file])
    assert len(tasks) == 1 and len(drafts) == 1 and len(risks) == 1 and decisions == []
    assert calls == ["doc.txt"]  # one call feeds all three engines
