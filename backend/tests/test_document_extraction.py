import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import document_extraction as de
from app.api.ai_secretary import IncomingMessage, ingest_message
from app.api.gmail import _bulk_email_reason
from app.core.integration_types import StorageObject
from app.models.ai_policy import ProjectAIPolicy
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task


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


def _fake_ready_provider(payload=None, exc=None):
    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=True)
    if exc is not None:
        provider.extract_fields.side_effect = exc
    else:
        provider.extract_fields.return_value = payload
    return provider


# --- pure helpers -----------------------------------------------------------

def test_looks_actionable_matches_any_of_the_four_detectors():
    assert de._looks_actionable("Исполнитель обязан подготовить акт.")
    assert de._looks_actionable("Прошу подтвердить получение.")
    assert de._looks_actionable("Есть риск срыва поставки.")
    assert de._looks_actionable("Когда будет готово?")
    assert not de._looks_actionable("Обычное описание без ничего примечательного.")
    assert not de._looks_actionable("")
    assert not de._looks_actionable(None)


def test_live_mail_imperative_with_adjacent_deadline_proposes_task(world, monkeypatch):
    db, _user, project = world
    text = "Дмитрий, готовь техзадание. Срок: 25.09.2026."
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert de._looks_actionable(text)
    result = de.extract_for_text(db, project.id, text, "live-mail.txt")

    assert result.extraction_method == "regex"
    assert len(result.obligations) == 1
    assert result.obligations[0].due_date.isoformat() == "2026-09-25"
    assert "готовь техзадание" in result.obligations[0].excerpt


def test_live_mail_materializes_task_and_nonempty_summary(world, monkeypatch):
    db, user, project = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = ingest_message(IncomingMessage(
        project_id=project.id,
        source_type="email",
        source_external_id="synthetic-imperative-deadline",
        source_name="Тестовое письмо",
        content="Дмитрий, готовь техзадание. Срок: 25.09.2026.",
        routing_confidence=0.99,
    ), db, user)

    assert len(result["tasks"]) == 1
    assert "Задач: 1" in result["summary"]
    task = db.get(Task, result["tasks"][0]["id"])
    assert task.due_date.isoformat() == "2026-09-25"
    assert task.external_action_status == "proposed"
    assert task.google_task_id is None


def test_bulk_promotion_with_imperative_is_filtered_before_task_creation(world, monkeypatch):
    db, user, project = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    text = "Сделай ремонт мечты. Срок: 25.09.2026. Евролан Дей приглашает на встречу."
    reason = _bulk_email_reason(
        {"list-unsubscribe": "<https://example.test/unsubscribe>"},
        ["INBOX", "CATEGORY_PROMOTIONS"], "Рекламная рассылка", text,
    )
    assert reason

    result = ingest_message(IncomingMessage(
        project_id=project.id,
        source_type="email",
        source_external_id="synthetic-bulk-promotion",
        source_name="Рекламная рассылка",
        content=text,
        routing_confidence=0.99,
        automation_suppressed=True,
        automation_suppression_reason=reason,
    ), db, user)

    assert result["status"] == "filtered"
    assert result["tasks"] == []


def test_verbatim_requires_exact_case_insensitive_substring():
    text = "Подрядчик Обязан предоставить Акт до 15.09.2026."
    assert de._verbatim("обязан предоставить акт", text) == "обязан предоставить акт"
    assert de._verbatim("предоставить нечто другое", text) is None
    assert de._verbatim(None, text) is None
    assert de._verbatim("", text) is None


def test_parse_iso_date_rejects_garbage():
    assert de._parse_iso_date("2026-09-15").isoformat() == "2026-09-15"
    assert de._parse_iso_date("not a date") is None
    assert de._parse_iso_date(None) is None


def test_parse_amount_handles_numbers_and_garbage():
    assert de._parse_amount(1500.5) == de._parse_amount("1500.5")
    assert de._parse_amount("not a number") is None
    assert de._parse_amount(None) is None


# --- extract_for_text: fallback classification (Вариант А + В) -------------

def test_no_api_key_falls_back_to_regex(world, monkeypatch):
    db, _user, project = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = de.extract_for_text(db, project.id, "Исполнитель обязан подготовить акт до 20.09.2026.", "a.txt")
    assert result.extraction_method == "regex"
    assert result.fallback_reason == "not_configured"
    assert result.obligations[0].due_date.isoformat() == "2026-09-20"
    assert result.obligations[0].assignee_hint is None
    assert result.obligations[0].amount is None


def test_local_only_project_never_calls_the_provider(world, monkeypatch):
    db, user, project = world
    db.add(ProjectAIPolicy(project_id=project.id, mode="local_only", updated_by_user_id=user.id))
    db.commit()
    provider = Mock()
    provider.extract_fields.side_effect = AssertionError("must never be called for a local_only project")
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    result = de.extract_for_text(db, project.id, "Исполнитель обязан подготовить акт.", "a.txt")
    assert result.extraction_method == "regex"
    assert result.fallback_reason == "policy_blocked"
    provider.extract_fields.assert_not_called()


def test_provider_not_ready_falls_back_with_correct_reason(world, monkeypatch):
    db, _user, project = world
    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=False)
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    result = de.extract_for_text(db, project.id, "Исполнитель обязан подготовить акт.", "a.txt")
    assert result.fallback_reason == "not_configured"
    provider.extract_fields.assert_not_called()


def test_provider_exception_falls_back_as_temporarily_unavailable(world, monkeypatch):
    db, _user, project = world
    provider = _fake_ready_provider(exc=RuntimeError("boom"))
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    result = de.extract_for_text(db, project.id, "Исполнитель обязан подготовить акт.", "a.txt")
    assert result.extraction_method == "regex"
    assert result.fallback_reason == "temporarily_unavailable"


def test_invalid_provider_response_falls_back_as_invalid_response(world, monkeypatch):
    db, _user, project = world
    provider = _fake_ready_provider(payload={"unexpected": "shape"})
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    result = de.extract_for_text(db, project.id, "Исполнитель обязан подготовить акт.", "a.txt")
    assert result.fallback_reason == "invalid_response"


def test_prefilter_skips_llm_entirely_for_non_actionable_text(world, monkeypatch):
    db, _user, project = world
    provider = Mock()
    provider.extract_fields.side_effect = AssertionError("prefilter should have skipped the call")
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    result = de.extract_for_text(db, project.id, "Ничего примечательного тут нет вообще.", "a.txt")
    assert result.extraction_method == "empty"
    assert result.obligations == [] and result.risks == [] and result.decisions == []
    provider.extract_fields.assert_not_called()


# --- extract_for_text: successful LLM path, per-field verification --------

def test_llm_success_maps_confidence_and_verifies_each_evidence_quote(world, monkeypatch):
    db, _user, project = world
    text = "Иванов обязан подготовить отчёт. Срок — не позднее 20.09.2026. Сумма договора 150000 руб."
    payload = {
        "obligations": [{
            "title": "Подготовить отчёт",
            "evidence_quote": "Иванов обязан подготовить отчёт",
            "due_date": "2026-09-20",
            "due_date_evidence_quote": "не позднее 20.09.2026",
            "assignee_hint": "Иванов",
            "assignee_evidence_quote": "Иванов обязан",
            "amount": 150000,
            "amount_currency": "RUB",
            "amount_evidence_quote": "Сумма договора 150000 руб",
            "confidence": "high",
        }],
        "response_candidates": [], "risks": [], "decisions": [],
    }
    monkeypatch.setattr(de, "configured_ai_provider", lambda: _fake_ready_provider(payload=payload))
    result = de.extract_for_text(db, project.id, text, "a.txt")
    assert result.extraction_method == "llm"
    obligation = result.obligations[0]
    assert obligation.confidence == 0.92
    assert obligation.due_date.isoformat() == "2026-09-20"
    assert obligation.assignee_hint == "Иванов"
    assert obligation.amount == de._parse_amount(150000)
    assert obligation.amount_currency == "RUB"
    assert obligation.extraction_method == "llm"


def test_llm_field_with_unverifiable_quote_is_dropped_not_the_whole_obligation(world, monkeypatch):
    db, _user, project = world
    text = "Иванов обязан подготовить отчёт."
    payload = {
        "obligations": [{
            "title": "Подготовить отчёт",
            "evidence_quote": "Иванов обязан подготовить отчёт",
            "due_date": "2026-09-20",
            "due_date_evidence_quote": "это не подстрока исходного текста",  # fabricated
            "assignee_hint": None, "assignee_evidence_quote": None,
            "amount": None, "amount_currency": None, "amount_evidence_quote": None,
            "confidence": "medium",
        }],
        "response_candidates": [], "risks": [], "decisions": [],
    }
    monkeypatch.setattr(de, "configured_ai_provider", lambda: _fake_ready_provider(payload=payload))
    result = de.extract_for_text(db, project.id, text, "a.txt")
    obligation = result.obligations[0]
    assert obligation.confidence == 0.75  # kept: the core evidence_quote did verify
    assert obligation.due_date is None  # dropped: its own quote failed verification
    assert obligation.due_date_evidence_quote is None


def test_llm_obligation_with_unverifiable_core_quote_is_dropped_entirely(world, monkeypatch):
    db, _user, project = world
    text = "Иванов обязан подготовить отчёт."
    payload = {
        "obligations": [{
            "title": "Придуманное поручение", "evidence_quote": "текста которого нет в документе",
            "due_date": None, "due_date_evidence_quote": None,
            "assignee_hint": None, "assignee_evidence_quote": None,
            "amount": None, "amount_currency": None, "amount_evidence_quote": None,
            "confidence": "high",
        }],
        "response_candidates": [], "risks": [], "decisions": [],
    }
    monkeypatch.setattr(de, "configured_ai_provider", lambda: _fake_ready_provider(payload=payload))
    result = de.extract_for_text(db, project.id, text, "a.txt")
    assert result.obligations == []


def test_llm_risk_and_decision_and_response_candidates_are_parsed(world, monkeypatch):
    db, _user, project = world
    text = "Есть риск срыва поставки. Требуется решение по варианту B. Прошу подтвердить дату."
    payload = {
        "obligations": [],
        "response_candidates": [{"evidence_quote": "Прошу подтвердить дату", "confidence": "medium"}],
        "risks": [{
            "title": "Риск срыва поставки", "evidence_quote": "риск срыва поставки",
            "kind": "risk", "criticality": "high", "confidence": "high",
        }],
        "decisions": [{"question": "Вариант B", "evidence_quote": "решение по варианту B", "confidence": "medium"}],
    }
    monkeypatch.setattr(de, "configured_ai_provider", lambda: _fake_ready_provider(payload=payload))
    result = de.extract_for_text(db, project.id, text, "a.txt")
    assert result.response_candidates[0].confidence == 0.75
    assert result.risks[0].criticality == "high" and result.risks[0].kind == "risk"
    assert result.decisions[0].question == "Вариант B"


# --- assignee matching -------------------------------------------------------

def test_match_assignee_hint_finds_project_member_by_substring(world):
    db, user, project = world
    assert de.match_assignee_hint(db, project.id, user.name.split()[0]) is not None
    assert de.match_assignee_hint(db, project.id, "Совершенно постороннее имя не из проекта") is None
    assert de.match_assignee_hint(db, project.id, None) is None
    assert de.match_assignee_hint(db, project.id, "") is None


# --- extract_for_files: bounded worker pool ---------------------------------

def test_extract_for_files_caches_per_file_and_does_not_recompute(world, monkeypatch):
    db, _user, project = world
    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=True)
    calls = []

    def fake_extract_fields(text, filename, project_name, member_names):
        calls.append(filename)
        return {"obligations": [], "response_candidates": [], "risks": [], "decisions": []}

    provider.extract_fields.side_effect = fake_extract_fields
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    files = [
        StorageObject(id=f"f{i}", name=f"file{i}.txt", mime_type="text/plain", parent_id="root",
                      content_text=f"Исполнитель должен подготовить документ {i}.")
        for i in range(3)
    ]
    de.extract_for_files(db, files, project.id)
    assert sorted(calls) == ["file0.txt", "file1.txt", "file2.txt"]
    assert all(f.llm_extraction_cache is not None for f in files)

    # A second pass must not re-request already-cached files.
    de.extract_for_files(db, files, project.id)
    assert len(calls) == 3


def test_extract_for_files_respects_concurrency_limit(world, monkeypatch):
    db, _user, project = world
    monkeypatch.setenv("LLM_EXTRACTION_CONCURRENCY", "2")
    in_flight = {"current": 0, "max_seen": 0}
    lock = threading.Lock()

    def fake_extract_fields(text, filename, project_name, member_names):
        with lock:
            in_flight["current"] += 1
            in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
        time.sleep(0.05)
        with lock:
            in_flight["current"] -= 1
        return {"obligations": [], "response_candidates": [], "risks": [], "decisions": []}

    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=True)
    provider.extract_fields.side_effect = fake_extract_fields
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    files = [
        StorageObject(id=f"f{i}", name=f"file{i}.txt", mime_type="text/plain", parent_id="root",
                      content_text=f"Исполнитель должен подготовить документ {i}.")
        for i in range(8)
    ]
    de.extract_for_files(db, files, project.id)
    assert in_flight["max_seen"] <= 2
    assert all(f.llm_extraction_cache is not None for f in files)


def test_extract_for_files_single_file_skips_pool_entirely(world, monkeypatch):
    db, _user, project = world
    provider = _fake_ready_provider(payload={"obligations": [], "response_candidates": [], "risks": [], "decisions": []})
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)
    file = StorageObject(id="f1", name="f1.txt", mime_type="text/plain", parent_id="root",
                         content_text="Исполнитель должен подготовить документ.")
    de.extract_for_files(db, [file], project.id)
    assert file.llm_extraction_cache is not None
    provider.extract_fields.assert_called_once()
