"""Synthetic obligations only; no customer documents or external OCR calls."""
from datetime import date
import hashlib

import pytest
from sqlalchemy import select

from app.core.integration_types import StorageObject
from app.models.management import Obligation
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.organizer_engine.content import extract_text_result
from app.task_engine import create_tasks_from_files, extract_task_candidates


DAMAGED = [
    ("Подрядчик обязан предоставить ак�т выполненных ра�бот", "замены"),
    ("Подрядчик обязан предоставить акт \x00 выполненных работ", "служебные"),
    ("Подрядчик обязан предоставить акт \ue001 выполненных работ", "служебные"),
    ("Подрядчик обязан предоставить " + "документы".encode().decode("cp1251"), "кодировки"),
    ("Подрядчик обязан предоставить " + "документы".encode().decode("latin1"), "кодировки"),
    ("Подрядчик обязан предоставить д о к у м е н т ы", "отдельные буквы"),
    ("Подрядчик обязан предоставить аааааааакт выполненных работ", "повтор"),
    ("Подрядчик обязан предоcтавить дoкументы заказчику", "подмены"),
    ("Подрядчик обязан пред0ставить д0кументы заказчику", "подмены"),
    ("Подрядчик обязан предоставить акт ####~~~~ выполненных работ", "символов"),
]


@pytest.mark.parametrize("text,reason", DAMAGED)
@pytest.mark.parametrize("deadline", ["", " до 15.09.2026"])
def test_damaged_candidate_is_retained_but_requires_explained_review(text, reason, deadline):
    source = text + deadline
    candidates = extract_task_candidates(source)
    assert len(candidates) == 1
    task = candidates[0]
    assert task.confidence <= 0.45
    assert any(reason in item for item in task.review_reasons)
    assert task.excerpt == source
    assert task.title == source[:240]
    assert task.due_date == (date(2026, 9, 15) if deadline else None)


@pytest.mark.parametrize("source", [
    "Подрядчик обязан предоставить акт выполненных работ",
    "Исполнитель должен подготовить ИД и согласовать ГПР с ООО ТЕСТ",
    "Необходимо предоставить КС-2 и КС-3 по СП 70.13330.2012",
    "Поставщик обязан поставить артикул AB12-РС34 и изделие М8х20-6g",
    "Поставщик обязан поставить XPS-30 ПВХ-110 DN50 PN16 12Х18Н10Т",
    "Необходимо согласовать API v2 и ISO 9001 с отделом QA",
    "Необходимо поставить кабель ВВГнг-LS 3х2,5 длиной 120 м",
    "Подрядчик обязан предоставить 1С:ERP файл DWG/DXF и ГПР-2026",
    "Исполнитель должен согласовать марки A B C D E F для изделий",
    "Необходимо проверить eДокумент и eПодпись перед передачей",
    "Необходимо поставить 1000000 изделий и 222222 шайб М12",
    "Подрядчик обязан предоставить XML по UTF-8 и акт № 12/АБ-2026",
    "Подрядчик обязан предоставить акт ООО РСУ и паспорт АСУТП",
    "Необходимо поставить артикулы аб12вг и дг34еж согласно заказу",
    "Необходимо поставить изделия км2пр3 и абв1гд2 по спецификации",
])
def test_technical_and_normal_obligations_are_not_treated_as_corrupt(source):
    candidate, = extract_task_candidates(source)
    assert candidate.confidence == 0.82
    assert candidate.review_reasons == ()
    assert candidate.excerpt == source


@pytest.mark.parametrize("deadline", ["15.09.2026", "15/09/2026", "15 сентября 2026"])
def test_dates_do_not_look_like_corrupt_alphanumeric_words(deadline):
    candidate, = extract_task_candidates(f"Подрядчик обязан предоставить КС-2 до {deadline}")
    assert candidate.due_date == date(2026, 9, 15)
    assert candidate.confidence == 0.90
    assert candidate.review_reasons == ()


def test_damage_is_local_and_not_a_document_wide_penalty():
    candidates = extract_task_candidates(
        "Подрядчик обязан предоставить ак�т. Исполнитель должен подготовить нормальный документ."
    )
    assert len(candidates) == 2
    assert candidates[0].confidence <= 0.45
    assert candidates[1].confidence == 0.82
    assert candidates[1].review_reasons == ()


def test_single_mixed_script_name_is_not_sufficient_evidence():
    candidate, = extract_task_candidates("Необходимо предоставить паспорт изделия альфaмодуль")
    assert candidate.review_reasons == ()
    assert candidate.confidence == 0.82


def test_invalid_calendar_date_is_not_repaired_or_given_a_date_bonus():
    source = "Подрядчик обязан предоставить КС-2 до 31.02.2026"
    candidate, = extract_task_candidates(source)
    assert candidate.due_date is None
    assert candidate.excerpt == source
    assert candidate.confidence == 0.82


def test_native_decoding_damage_reaches_task_review():
    source = "Подрядчик обязан предоставить акт ".encode() + b"\xff\xfe" + " до 15.09.2026".encode()
    extracted = extract_text_result(source, "text/plain", "synthetic.txt")
    candidate, = extract_task_candidates(extracted.text)
    assert "\ufffd" in candidate.excerpt
    assert candidate.excerpt == extracted.text
    assert candidate.confidence <= 0.45


def test_ocr_damage_reaches_task_review_without_repair(monkeypatch):
    source = "Подрядчик обязан предоставить ак�т до 15.09.2026"
    monkeypatch.setattr("app.organizer_engine.content._ocr_text", lambda *args: source)
    extracted = extract_text_result(b"synthetic image", "image/png", "synthetic.png")
    assert extracted.method == "ocr"
    candidate, = extract_task_candidates(extracted.text)
    assert candidate.excerpt == source
    assert candidate.confidence <= 0.45


def test_persisted_review_retains_evidence_and_resync_does_not_touch_existing_tasks(db_session, user_factory):
    user = user_factory()
    organization = Organization(name="Synthetic company")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    db_session.commit()
    source = "Подрядчик обязан предоставить ак�т до 15.09.2026"
    file = StorageObject(id="synthetic:file-1", name="synthetic.txt", mime_type="text/plain", parent_id="synthetic", content_text=source)
    task, = create_tasks_from_files(db_session, project.id, None, [file])
    assert task.confidence <= 0.45
    assert "замены" in task.description
    assert "не вероятность" in task.description
    assert "ручная проверка" in task.description
    assert task.needs_review is True
    assert task.external_action_status == "proposed"
    assert task.assignee_user_id == user.id  # Existing default, not a new assignment policy.
    assert task.source_file_id == file.id and task.source_file_name == file.name
    assert task.source_excerpt == source
    assert task.source_excerpt_hash == hashlib.sha256(source.casefold().encode()).hexdigest()
    obligation = db_session.scalar(select(Obligation).where(Obligation.task_id == task.id))
    assert obligation.source_excerpt == source and obligation.source_id == file.id
    assert obligation.confidence == task.confidence
    assert obligation.status == "needs_confirmation"

    # Emulate a pre-existing, manually reviewed record: rescanning must not migrate it.
    task.confidence = 0.82
    task.description = "Ранее проверено человеком"
    task.needs_review = False
    db_session.commit()
    assert create_tasks_from_files(db_session, project.id, None, [file]) == []
    db_session.refresh(task)
    assert (task.confidence, task.description, task.needs_review) == (0.82, "Ранее проверено человеком", False)
    assert len(db_session.scalars(select(Task)).all()) == 1
