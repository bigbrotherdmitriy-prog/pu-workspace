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
        _folder("sktp_schemes", "Схемы", "sktp"),
        _folder("ar", "АР", "root"),
        _folder("ar_acts", "Акты", "ar"),
        _file("ar_plan", "план.pdf", "ar"),
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
    assert len(files) == 4
    assert set(f.kind for f in files) >= {"passport", "act", "scheme", "service"}
    act = next(f for f in files if f.name == "Акт ввода 1.docx")
    assert "blank_fields" in (act.issues or [])
    assert "editable_format_not_signed" in (act.issues or [])
    bak = next(f for f in files if f.name == "план.bak")
    assert "service_file" in (bak.issues or [])
    mod1 = next(f for f in files if f.name == "Модуль 1.pdf")
    assert mod1.path == "ЭМ/Паспорта/Модуль 1.pdf"
    assert result["empty_folders"] == ["АР/Акты", "СКТП"]

    # repeat scan without .bak -> IdFile removed
    tree_v2 = [i for i in _tree_v1() if i.id != "proj_bak"]
    storage2 = FakeStorage(tree_v2, contents={"em_act1": (b"fake-docx", "application/msword")})
    ed.scan_source(db, source, storage2)
    db.commit()
    remaining = db.query(IdFile).filter(IdFile.source_id == source.id).all()
    assert len(remaining) == 3
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


def test_detect_contained_kinds():
    text = (
        "АКТ № 1 технической готовности электромонтажных работ ... "
        "ВЕДОМОСТЬ № 4 смонтированного электрооборудования ... "
        "ПРОТОКОЛ № 1 осмотра ... ЖУРНАЛ прокладки кабеля"
    )
    assert ed.detect_contained_kinds(text) == ["act", "journal", "protocol", "statement"]


def test_match_requirements_contained_kinds(world):
    db = world["db"]
    project = world["project"]

    source = IdSource(
        project_id=project.id, object_name="Объект А",
        provider="google_drive", folder_id="root",
    )
    db.add(source)
    db.flush()

    stmt_req = _mk_req(
        db, project.id, title="Ведомость", doc_kind="statement", section="ЭМ",
        required_if_applicable=False, object_name="Объект А",
    )
    _mk_file(
        db, project.id, source.id, external_id="combined1",
        path="ЭМ/Акты ЭМ 2026.pdf", name="Акты ЭМ 2026.pdf", kind="act", section="ЭМ",
        contained_kinds=["act", "statement"],
    )
    db.commit()

    matches = ed.match_requirements([stmt_req], db.query(IdFile).all())
    assert matches[0]["status"] == "present"


def test_match_requirements_orders_distributed_one_to_one(world):
    db = world["db"]
    project = world["project"]

    source = IdSource(
        project_id=project.id, object_name="Объект А",
        provider="google_drive", folder_id="root",
    )
    db.add(source)
    db.flush()

    order1 = _mk_req(
        db, project.id, title="Приказ заказчик", doc_kind="order", section="Общее",
        required_if_applicable=False, object_name="Объект А",
    )
    order2 = _mk_req(
        db, project.id, title="Приказ подрядчик", doc_kind="order", section="Общее",
        required_if_applicable=False, object_name="Объект А",
    )
    _mk_file(
        db, project.id, source.id, external_id="o1",
        path="Общее/Приказ.docx", name="Приказ.docx", kind="order", section="Общее",
    )
    db.commit()

    matches = ed.match_requirements([order1, order2], db.query(IdFile).all())
    assert matches[0]["status"] == "present"
    assert matches[1]["status"] == "missing"

    _mk_file(
        db, project.id, source.id, external_id="o2",
        path="Общее/Приказ2.docx", name="Приказ2.docx", kind="order", section="Общее",
    )
    db.commit()

    matches = ed.match_requirements([order1, order2], db.query(IdFile).all())
    assert matches[0]["status"] == "present"
    assert matches[1]["status"] == "present"


def test_requirements_from_contract_prefers_tz(world, monkeypatch):
    from app.models.document import Document
    from app.models.document_version import DocumentVersion
    from app.models.organization_contract import Contract
    from app.models.contract_document_link import ContractDocumentLink

    db = world["db"]
    project = world["project"]

    monkeypatch.setattr(ed, "extract_id_requirements_ai", lambda text: None)

    contract = Contract(name="Договор", project_id=project.id)
    db.add(contract)
    db.flush()

    big = Document(name="для ДДС.xlsx", project_id=project.id)
    tz = Document(name="Приложение №1 ТЗ.docx", project_id=project.id)
    db.add_all([big, tz])
    db.flush()

    db.add(DocumentVersion(document_id=big.id, version_number=1, content_text="x" * 250_000))
    db.add(DocumentVersion(
        document_id=tz.id, version_number=1,
        content_text=(
            "Перечень исполнительной документации: Журнал работ; "
            "Технический отчет испытаний; Гарантии"
        ),
    ))
    big.current_version = 1
    tz.current_version = 1
    db.add_all([
        ContractDocumentLink(contract_id=contract.id, document_id=big.id),
        ContractDocumentLink(contract_id=contract.id, document_id=tz.id),
    ])
    db.commit()

    ed.requirements_from_contract(
        db, project.id, contract.id, None, ["ЭМ"],
    )
    db.commit()

    reqs = db.query(IdRequirement).filter(
        IdRequirement.project_id == project.id,
        IdRequirement.contract_id == contract.id,
    ).all()
    tz_items = [r for r in reqs if r.source == "tz"]
    norm_items = [r for r in reqs if r.source == "norm"]
    assert len(tz_items) == 2
    assert all(r.required_if_applicable is True for r in norm_items)


def test_scan_source_skips_read_when_unchanged(world, monkeypatch):
    db = world["db"]
    project = world["project"]

    calls = {"n": 0}

    class CountingStorage(FakeStorage):
        def read_bytes(self, obj_id, max_size):
            calls["n"] += 1
            return super().read_bytes(obj_id, max_size)

    monkeypatch.setattr(
        ed, "_extract_text_from_bytes",
        lambda data, mime, name: "Ведомость № 4 смонтированного электрооборудования",
    )

    source = IdSource(
        project_id=project.id, object_name="Объект А",
        provider="google_drive", folder_id="root",
    )
    db.add(source)
    db.commit()

    tree = [
        _folder("root", "Проект", ""),
        _file(
            "doc1", "Акт.docx", "root",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            modified="2026-01-10T12:00:00+00:00", size=1000,
        ),
    ]
    storage = CountingStorage(tree, contents={"doc1": (b"fake-docx", "application/msword")})
    ed.scan_source(db, source, storage)
    db.commit()
    first_calls = calls["n"]
    assert first_calls > 0

    ed.scan_source(db, source, storage)
    db.commit()
    assert calls["n"] == first_calls


def test_scan_source_pdf_contained_kinds(world, monkeypatch):
    db = world["db"]
    project = world["project"]

    monkeypatch.setattr(
        ed, "extract_text",
        lambda data, mime, name: "Ведомость № 4 смонтированного электрооборудования", raising=False,
    )
    monkeypatch.setattr(
        ed, "_extract_text_from_bytes",
        lambda data, mime, name: "Ведомость № 4 смонтированного электрооборудования",
    )

    source = IdSource(
        project_id=project.id, object_name="Объект А",
        provider="google_drive", folder_id="root",
    )
    db.add(source)
    db.commit()

    tree = [
        _folder("root", "Проект", ""),
        _file("pdf1", "Акты ЭМ 2026.pdf", "root"),
    ]
    storage = FakeStorage(tree, contents={"pdf1": (b"fake-pdf", "application/pdf")})
    ed.scan_source(db, source, storage)
    db.commit()

    files = db.query(IdFile).filter(IdFile.source_id == source.id).all()
    f = next((x for x in files if x.name == "Акты ЭМ 2026.pdf"), None)
    assert f is not None
    assert "statement" in (f.contained_kinds or [])
