"""Schedule preview/import must reject malformed supplied fields before writing."""

import csv
import io
import json
from contextlib import contextmanager
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select

from app.api.execution_finance import StructuredImportRequest, structured_import, structured_preview
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.structured_import import parse_structured_rows


def _table(*rows, headers=("Этап", "Дата начала", "Дата окончания", "Прогресс")):
    content = io.StringIO()
    writer = csv.writer(content, delimiter=";")
    writer.writerow(headers)
    writer.writerows(rows)
    return content.getvalue()


def _row(title="Монтаж", start="", finish="", progress=""):
    return parse_structured_rows(_table((title, start, finish, progress)), "schedule")["rows"][0]


@pytest.mark.parametrize("field", ["start", "finish"])
@pytest.mark.parametrize("value", ["31.02.2026", "2026-13-01", "tomorrow", "2026-09-01 extra"])
def test_nonblank_invalid_date_is_a_row_error(field, value):
    row = _row(**{field: value})
    assert row["importable"] is False
    assert row["issues"]
    assert value in row["excerpt"]


def test_reversed_date_range_is_not_importable():
    row = _row(start="02.09.2026", finish="01.09.2026")
    assert row["importable"] is False
    assert row["issues"]


@pytest.mark.parametrize("value", [
    "unknown", "50oops", "oops50", "5 0", "1e1", "1.2.3", "1,2,3", "--5",
    "NaN", "Infinity", "-Infinity", "50%%", "-1", "100.01", "101", "9" * 400,
])
def test_invalid_progress_is_rejected_not_stripped_defaulted_or_clamped(value):
    row = _row(progress=value)
    assert row["importable"] is False
    assert row["issues"]
    json.dumps(row, allow_nan=False)


@pytest.mark.parametrize("title", ["", " ", "X", "X" * 501])
def test_title_matches_existing_schedule_creation_field_constraints(title):
    row = _row(title=title, start="01.09.2026")
    assert row["importable"] is False
    assert row["issues"]


def test_missing_title_header_cannot_produce_importable_rows():
    result = parse_structured_rows(_table(("01.09.2026", "50"), headers=("Начало", "Прогресс")), "schedule")
    assert result["issues"]
    assert result["rows"] and all(not row["importable"] for row in result["rows"])


@pytest.mark.parametrize("value, expected", [
    ("", 0), ("  ", 0), ("0", 0), ("100", 100), ("25.5", 25.5),
    ("25,5", 25.5), ("25,5 %", 25.5), ("+50", 50), ("50%", 50),
    (".5", .5), ("50.", 50), ("100.000", 100), ("-0", 0),
])
def test_valid_progress_and_optional_blank_dates_remain_compatible(value, expected):
    row = _row(progress=value)
    assert row["importable"] is True
    assert row["issues"] == []
    assert row["planned_start"] is row["planned_finish"] is None
    assert row["progress"] == expected


@pytest.mark.parametrize("start, finish, expected_start, expected_finish", [
    ("2026-09-01", "01.09.2026", "2026-09-01", "2026-09-01"),
    ("01/09/2026", "02-09-2026", "2026-09-01", "2026-09-02"),
    ("", "01.09.2026", None, "2026-09-01"),
    ("01.09.2026", "", "2026-09-01", None),
])
def test_valid_same_day_and_partial_dates_remain_compatible(start, finish, expected_start, expected_finish):
    row = _row(start=start, finish=finish)
    assert row["importable"] is True
    assert (row["planned_start"], row["planned_finish"]) == (expected_start, expected_finish)


def test_title_only_table_remains_a_valid_undated_draft():
    result = parse_structured_rows("Этап\nМонтаж\n", "schedule")
    assert result["issues"] == []
    assert result["rows"][0]["importable"] is True
    assert result["rows"][0]["progress"] == 0


@pytest.mark.parametrize("title", ["AB", "X" * 500])
def test_valid_title_length_boundaries(title):
    assert _row(title=title)["importable"] is True


def _world(db, user_factory, content):
    user = user_factory()
    organization = Organization(name="Synthetic schedule organization")
    db.add(organization)
    db.flush()
    project = Project(name="Synthetic schedule validation", organization_id=organization.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    document = Document(project_id=project.id, name="synthetic-schedule.csv", source="local_upload")
    baseline = ScheduleBaseline(project_id=project.id, created_by_user_id=user.id,
                                name="Draft schedule", version=1, status="draft")
    db.add_all([document, baseline])
    db.flush()
    version = DocumentVersion(document_id=document.id, version_number=1, content=content)
    db.add(version)
    db.commit()
    return user, project, document, version, baseline


@contextmanager
def _writes(db):
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", observe)


@pytest.mark.parametrize("invalid", [
    ("Монтаж", "31.02.2026", "", ""),
    ("Монтаж", "02.09.2026", "01.09.2026", ""),
    ("Монтаж", "", "", "50oops"),
    ("Монтаж", "", "", "101"),
    ("", "01.09.2026", "", ""),
])
def test_real_preview_and_mixed_selection_reject_before_any_write(db_session, user_factory, invalid):
    content = _table(("Valid stage", "", "", "50"), invalid)
    user, project, document, version, baseline = _world(db_session, user_factory, content)
    with _writes(db_session) as writes:
        preview = structured_preview(document.id, project.id, "schedule", db_session, user)
        assert preview["originals_changed"] is False
        assert preview["requires_confirmation"] is True
        assert preview["rows"][0]["importable"] is True
        assert preview["rows"][1]["importable"] is False
        assert writes == []
        with pytest.raises(HTTPException) as error:
            structured_import(document.id, StructuredImportRequest(
                project_id=project.id, kind="schedule", baseline_id=baseline.id,
                expected_baseline_version=1, source_rows=[2, 3],
            ), db_session, user)
        assert error.value.status_code == 422
        assert writes == []
    assert list(db_session.scalars(select(ScheduleItem))) == []
    assert list(db_session.scalars(select(AuditLog))) == []
    assert version.content == content
    assert document.name == "synthetic-schedule.csv"
    assert (baseline.status, baseline.version) == ("draft", 1)


def test_valid_real_import_and_replay_preserve_exact_dates_and_progress(db_session, user_factory):
    content = _table(("Монтаж", "01.09.2026", "02.09.2026", "25,5 %"), ("Undated stage", "", "", ""))
    user, project, document, version, baseline = _world(db_session, user_factory, content)
    payload = StructuredImportRequest(project_id=project.id, kind="schedule", baseline_id=baseline.id,
                                      expected_baseline_version=1, source_rows=[2, 3])
    with _writes(db_session) as writes:
        preview = structured_preview(document.id, project.id, "schedule", db_session, user)
        assert all(row["importable"] for row in preview["rows"])
        assert writes == []
    structured_import(document.id, payload, db_session, user)
    structured_import(document.id, payload, db_session, user)
    rows = list(db_session.scalars(select(ScheduleItem).order_by(ScheduleItem.id)))
    assert len(rows) == 2
    assert (rows[0].planned_start, rows[0].planned_finish, rows[0].planned_progress) == (
        date(2026, 9, 1), date(2026, 9, 2), 25.5,
    )
    assert (rows[1].planned_start, rows[1].planned_finish, rows[1].planned_progress) == (None, None, 0)
    assert version.content == content
    assert document.name == "synthetic-schedule.csv"


def test_wbs_document_preview_is_read_only_and_requires_confirmation(db_session, user_factory):
    content = "WBS;Наименование;Тип;Длительность\n1;Подготовка;раздел;\n1.1;Монтаж;работа;3\n"
    user, project, document, version, _baseline = _world(db_session, user_factory, content)
    with _writes(db_session) as writes:
        result = structured_preview(document.id, project.id, "schedule-wbs", db_session, user)
    assert writes == []
    assert result["requires_confirmation"] is True
    assert result["commit_allowed"] is False
    assert result["originals_changed"] is False
    assert [row["kind"] for row in result["rows"]] == ["summary", "work"]
    assert result["rows"][1]["parent_ref"] == result["rows"][0]["client_ref"]
    assert version.content == content and document.name == "synthetic-schedule.csv"


def test_unselected_invalid_row_does_not_block_valid_selected_draft_row(db_session, user_factory):
    content = _table(("Valid stage", "", "", "50"), ("Invalid stage", "bad date", "", ""))
    user, project, document, version, baseline = _world(db_session, user_factory, content)
    result = structured_import(document.id, StructuredImportRequest(
        project_id=project.id, kind="schedule", baseline_id=baseline.id,
        expected_baseline_version=1, source_rows=[2],
    ), db_session, user)
    assert result["created"] == 1
    rows = list(db_session.scalars(select(ScheduleItem)))
    assert len(rows) == 1 and rows[0].title == "Valid stage"
    assert version.content == content
