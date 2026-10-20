from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.core.integration_types import StorageObject
from app.models.executive_docs import IdFile, IdRequirement, IdSource
from app.models.organization_contract import Organization
from app.models.project import Project
from app import executive_docs as ed


class FakeStorage:
    def __init__(self, items, contents=None):
        self.items = list(items)
        self.contents = contents or {}

    def walk_tree(self, root):
        return [i for i in self.items if i.id != root or True]

    def read_bytes(self, obj_id, max_size):
        data = self.contents.get(obj_id, b"")
        if isinstance(data, tuple):
            return data
        return data, "application/octet-stream"


def _folder(id_, name, parent_id):
    return StorageObject(
        id=id_, name=name, mime_type="application/vnd.google-apps.folder",
        parent_id=parent_id, object_type="folder",
    )


def _file(id_, name, parent_id, size=1000, mime="application/pdf", modified=None):
    return StorageObject(
        id=id_, name=name, mime_type=mime, parent_id=parent_id,
        size=size, modified_time=modified, object_type="file",
    )


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="ID org")
        db.add(org)
        db.flush()
        project = Project(name="ID project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.commit()
        yield {"db": db, "project": project}
    engine.dispose()


def _tree_v1():
    return [
        _folder("root", "Проект", ""),
        _folder("em", "ЭМ", "root"),
        _folder("em_passports", "Паспорта", "em"),
        _file("em_passports_mod1", "Модуль 1.pdf", "em_passports"),
        _folder("em_acts", "Акты", "em"),
        _file(
            "em_act1", "Акт ввода 1.docx", "em_acts",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            modified="2026-01-10T12:00:00+00:00",
        ),
        _folder("sktp", "СКТП", "root"),
        _folder("sktp_acts", "Акты", "sktp"),
        _folder("proj", "Проект2", "root"),
        _file("proj_bak", "план.bak", "proj", mime="application/octet-stream"),
    ]


def test_scan_source_tree_and_files(world, monkeypatch):
    db = world["db"]
    project = world["project"]

    monkeypatch.setattr(
        ed, "_extract_text_from_bytes",
        lambda data, mime, name: "Дата «__» ____ ____ ____ ____ ____",
    )

    source = IdSource(
        project_id=project.id, object_name="Объект А",
        provider="google_drive", folder_id="root",
    )
    db.add(source)
    db.commit()

    storage = FakeStorage(_tree_v1(), contents={"em_act1": (b"fake-docx", "application/msword")})
    result = ed.scan_source(db, source, storage)
    db.commit()

    files = db.query(IdFile).filter(IdFile.source_id == source.id).all()
    assert len(files) == 3
    assert set(f.kind for f in files) >= {"passport", "act", "service"}
    act = next(f for f in files if f.name == "Акт ввода 1.docx")
    assert "blank_fields" in (act.issues or [])
    assert "editable_format_not_signed" in (act.issues or [])
    bak = next(f for f in files if f.name == "план.bak")
    assert "service_file" in (bak.issues or [])
    assert result["empty_folders"] == ["СКТП/Акты"]

    # repeat scan without .bak -> IdFile removed
    tree_v2 = [i for i in _tree_v1() if i.id != "proj_bak"]
    storage2 = FakeStorage(tree_v2, contents={"em_act1": (b"fake-docx", "application/msword")})
    ed.scan_source(db, source, storage2)
    db.commit()
    remaining = db.query(IdFile).filter(IdFile.source_id == source.id).all()
    assert len(remaining) == 2
    assert all(f.name != "план.bak" for f in remaining)


def _mk_req(db, project_id, **kwargs):
    req = IdRequirement(
        project_id=project_id,
        source="norm",
        **{**{"section": None, "doc_kind": "other", "title": "t", "required_if_applicable": False}, **kwargs},
    )
    db.add(req)
    db.flush()
    return req


def _mk_file(db, project_id, source_id, **kwargs):
    f = IdFile(
        project_id=project_id,
        provider="google_drive",
        external_id=kwargs.pop("external_id", "x"),
        source_id=source_id,
        **{**{"path": "p", "name": "n", "kind": "other", "issues": [], "object_name": "Объект А"}, **kwargs},
    )
    db.add(f)
    db.flush()
    return f


def test_requirements_and_object_report(world):
    db = world["db"]
    project = world["project"]

    source = IdSource(
        project_id=project.id, object_name="Объект А",
        provider="google_drive", folder_id="root",
    )
    db.add(source)
    db.flush()

    passport_req = _mk_req(
        db, project.id, title="Паспорт ЭМ", doc_kind="passport", section="ЭМ",
        required_if_applicable=False, object_name="Объект А",
    )
    report_req = _mk_req(
        db, project.id, title="Отчёт ЭМ", doc_kind="report", section="ЭМ",
        required_if_applicable=False, object_name="Объект А",
    )
    journal_req = _mk_req(
        db, project.id, title="Журнал", doc_kind="journal", section="Общее",
        required_if_applicable=True, object_name="Объект А",
    )
    _mk_file(
        db, project.id, source.id, external_id="f1",
        path="ЭМ/Паспорта/Модуль 1.pdf", name="Модуль 1.pdf", kind="passport", section="ЭМ",
    )
    db.commit()

    report = ed.object_report(db, project.id, "Объект А")
    assert report["required_total"] == 2
    assert report["percent"] == 50
    assert report["missing"] == [report_req.title]
    assert journal_req.title in report["recommended_missing"]


def test_draft_status_for_act(world):
    db = world["db"]
    project = world["project"]

    source = IdSource(
        project_id=project.id, object_name="Объект А",
        provider="google_drive", folder_id="root",
    )
    db.add(source)
    db.flush()

    act_req = _mk_req(
        db, project.id, title="Акт ЭМ", doc_kind="act", section="ЭМ",
        required_if_applicable=False, object_name="Объект А",
    )
    _mk_file(
        db, project.id, source.id, external_id="a1",
        path="ЭМ/Акты/Акт.docx", name="Акт.docx", kind="act", section="ЭМ",
        issues=["blank_fields"],
    )
    db.commit()

    matches = ed.match_requirements([act_req], db.query(IdFile).all())
    assert matches[0]["status"] == "draft"
