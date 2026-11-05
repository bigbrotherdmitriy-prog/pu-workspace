import io
import posixpath
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import equipment_test_act as eta
from app import work_log as wl
from app import work_log_form
from app.database import Base
from app.docx_kit import build_docx, page_break
from app.models.defects import Defect, DefectPhoto
from app.models.estimates import Estimate, KsAct
from app.models.organization_contract import Organization
from app.models.project import Project


def _xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        return zf.read("word/document.xml").decode("utf-8")


def test_equipment_test_act_content_and_validation():
    header = {"act_no": "7", "object_name": "ЦОД Дубна", "date": "2026-10-20", "customer_rep": "Иванов И.И."}
    xml = _xml(eta.render(header, [
        {"name": "Насос Grundfos CR 5", "type_mark": "CR 5-8", "serial": "SN-1", "program": "Пробный пуск 2 ч", "values": "ток 4.1 А", "result": "pass"},
    ]))
    assert "АКТ № 7" in xml and "ЦОД Дубна" in xml and "Насос Grundfos CR 5" in xml
    assert "выдержало испытание" in xml and "может быть принято для комплексного опробования" in xml
    assert "20" in xml and "октября" in xml
    bad = _xml(eta.render({}, [{"name": "Щит", "result": "fail"}]))
    assert "не выдержало испытание" in bad and "подлежат устранению" in bad
    with pytest.raises(ValueError):
        eta.render({}, [])
    with pytest.raises(ValueError):
        eta.render({}, [{"name": "X", "result": "maybe"}])
    with pytest.raises(ValueError):
        eta.render({}, [{"type_mark": "только марка"}])
    # пустая строка формы (только итог по умолчанию) просто пропускается
    xml2 = _xml(eta.render({}, [{"name": "", "result": "pass"}, {"name": "Щит", "result": "pass"}]))
    assert "Щит" in xml2


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Form org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        db.add(p)
        db.flush()
        db.commit()
        yield {"db": db, "p": p}
    engine.dispose()


def test_work_log_form_has_sections_and_entries(world):
    db, pid = world["db"], world["p"].id
    crew = wl.add_crew(db, pid, {"name": "Бригада 1", "foreman": "Петров П.П.", "members": "Сидоров"})
    wl.add_entry(db, pid, 1, {"work_date": "2026-10-20", "work_name": "Прокладка кабеля", "qty": "40", "unit": "м",
                              "location": "Этаж 2", "weather": "+5, дождь", "crew_id": crew["id"], "hours": "8"})
    db.commit()
    xml = _xml(work_log_form.render(db, pid, {"object_name": "ЦОД Дубна", "journal_no": "1"}))
    for needle in ("ОБЩИЙ ЖУРНАЛ РАБОТ", "ЦОД Дубна", "Раздел 1.", "Раздел 3.", "Раздел 4.", "Раздел 5.",
                   "Прокладка кабеля", "20.10.2026", "+5, дождь", "Петров П.П.", "бригада: Бригада 1", "часов: 8.00"):
        assert needle in xml, needle
    assert 'w:orient="landscape"' in xml


def test_api(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import id_forms as api
    from app.core.auth import require_user
    from app.database import get_db

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: world["db"]
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    c = TestClient(app)
    pid = world["p"].id
    r = c.post(f"/projects/{pid}/executive-docs/equipment-test-act.docx", json={"header": {}, "items": [{"name": "Щит", "result": "pass"}]})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert c.post(f"/projects/{pid}/executive-docs/equipment-test-act.docx", json={"header": {}, "items": []}).status_code == 422
    r = c.post(f"/projects/{pid}/work-log/form.docx", json={"header": {"object_name": "О"}})
    assert r.status_code == 200 and r.content[:2] == b"PK"


@pytest.fixture
def weekly_world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Weekly org")
        db.add(org)
        db.flush()
        p1 = Project(name="Проект А", organization_id=org.id)
        p2 = Project(name="Проект Б", organization_id=org.id)
        db.add_all([p1, p2])
        db.flush()
        db.commit()
        yield {"db": db, "p1": p1, "p2": p2}
    engine.dispose()


def _snapshot(db: Session) -> dict:
    return {
        "work_log_entries": db.execute(select(wl.WorkLogEntry.id, wl.WorkLogEntry.work_name)).all(),
        "defects": db.execute(select(Defect.id, Defect.status)).all(),
        "defect_photos": db.execute(select(DefectPhoto.id, DefectPhoto.size_bytes)).all(),
        "ks_acts": db.execute(select(KsAct.id, KsAct.status)).all(),
    }


def test_collect_weekly_report_scoping_and_readonly(weekly_world):
    db = weekly_world["db"]
    p1, p2 = weekly_world["p1"], weekly_world["p2"]

    wl.add_entry(db, p1.id, 1, {"work_date": "2026-10-19", "work_name": "Работа до периода", "unit": "м", "qty": "1"})
    wl.add_entry(db, p1.id, 1, {"work_date": "2026-10-20", "work_name": "Работа в начале", "unit": "м", "qty": "2"})
    wl.add_entry(db, p1.id, 1, {"work_date": "2026-10-26", "work_name": "Работа в конце", "unit": "шт", "qty": "3"})
    wl.add_entry(db, p1.id, 1, {"work_date": "2026-10-27", "work_name": "Работа после периода", "unit": "м", "qty": "4"})
    wl.add_entry(db, p2.id, 1, {"work_date": "2026-10-22", "work_name": "Чужой проект", "unit": "м", "qty": "5"})

    d1 = Defect(project_id=p1.id, number=1, title="Трещина", status="open", responsible="Иванов",
                due_date=date(2026, 11, 1), location="Этаж 1")
    d2 = Defect(project_id=p2.id, number=1, title="Чужой дефект", status="open")
    db.add_all([d1, d2])
    db.flush()
    db.add(DefectPhoto(project_id=p1.id, defect_id=d1.id, filename="a.jpg", mime_type="image/jpeg",
                       size_bytes=3, data=b"\x00\x01\x02"))
    db.add(DefectPhoto(project_id=p2.id, defect_id=d2.id, filename="b.jpg", mime_type="image/jpeg",
                       size_bytes=2, data=b"\xff\xfe"))

    est = Estimate(project_id=p1.id, name="Смета")
    db.add(est)
    db.flush()
    db.add(KsAct(project_id=p1.id, estimate_id=est.id, number="1", period_from=date(2026, 10, 1),
                 period_to=date(2026, 10, 31), status="draft", customer_status="pending",
                 lines_json="[]", header_json="{}"))
    db.commit()

    before = _snapshot(db)
    result = work_log_form.collect_weekly_report(db, p1.id, "2026-10-20", "2026-10-26")
    after = _snapshot(db)

    assert before == after
    assert not db.new and not db.dirty and not db.deleted

    assert result["project_id"] == p1.id
    assert result["project_name"] == "Проект А"
    assert result["period"] == {"date_from": "2026-10-20", "date_to": "2026-10-26"}

    names = [w["work_name"] for w in result["work_facts"]]
    assert "Работа в начале" in names and "Работа в конце" in names
    assert "Работа до периода" not in names and "Работа после периода" not in names
    assert "Чужой проект" not in names

    assert [d["title"] for d in result["defects"]] == ["Трещина"]
    assert result["defects"][0]["status"] == "open"
    assert result["defects"][0]["responsible"] == "Иванов"
    assert result["defects"][0]["due_date"] == "2026-11-01"
    assert result["defects"][0]["location"] == "Этаж 1"

    assert len(result["defect_photos"]) == 1
    photo = result["defect_photos"][0]
    assert photo["filename"] == "a.jpg" and photo["mime_type"] == "image/jpeg"
    assert photo["size_bytes"] == 3 and photo["defect_id"] == d1.id
    assert "data" not in photo
    assert b"\x00\x01\x02" not in repr(result).encode("utf-8")

    assert len(result["ks_acts"]) == 1
    act = result["ks_acts"][0]
    assert act["number"] == "1" and act["status"] == "draft" and act["customer_status"] == "pending"
    assert act["period_from"] == "2026-10-01" and act["period_to"] == "2026-10-31"
    assert "lines_json" not in act and "header_json" not in act and "total_ex_vat" not in act


def test_collect_weekly_report_validation(weekly_world):
    db = weekly_world["db"]
    pid = weekly_world["p1"].id
    with pytest.raises(ValueError):
        work_log_form.collect_weekly_report(db, pid, "2026-10-26", "2026-10-20")
    with pytest.raises(ValueError):
        work_log_form.collect_weekly_report(db, pid, "not-a-date", "2026-10-20")
    with pytest.raises(LookupError):
        work_log_form.collect_weekly_report(db, 999999, "2026-10-20", "2026-10-26")


_CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_CUSTOM_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
_CUSTOM_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
_PKG_RELS = "_rels/.rels"
_DOC_RELS = "word/_rels/document.xml.rels"


def _read(data: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        return zf.read(name).decode("utf-8")


def _names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.namelist()


def _resolve_target(source_part: str, target: str) -> str:
    """Resolve an OPC relationship Target relative to its source part."""
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(source_part)
    return posixpath.normpath(posixpath.join(base, target))


def test_build_docx_old_call_and_positional_landscape_preserved():
    parts = ["<w:p><w:r><w:t>Привет</w:t></w:r></w:p>", page_break()]
    plain = build_docx(parts)
    names = _names(plain)
    assert "docProps/custom.xml" not in names
    assert "word/document.xml" in names
    xml = _read(plain, "word/document.xml")
    assert "Привет" in xml and 'w:type="page"' in xml
    assert 'w:orient="landscape"' not in xml

    land = build_docx(parts, True)
    land_xml = _read(land, "word/document.xml")
    assert 'w:orient="landscape"' in land_xml
    assert "docProps/custom.xml" not in _names(land)

    # positional landscape with keyword metadata still works
    both = build_docx(parts, True, template_id="T1", template_version="1")
    assert 'w:orient="landscape"' in _read(both, "word/document.xml")
    assert "docProps/custom.xml" in _names(both)


def test_build_docx_template_metadata_parses_and_escapes():
    parts = ["<w:p><w:r><w:t>Текст</w:t></w:r></w:p>"]
    data = build_docx(parts, template_id="  PU-ACT-01  ", template_version="  v2 & <x>  ")
    names = _names(data)
    assert "docProps/custom.xml" in names

    custom = _read(data, "docProps/custom.xml")
    root = ET.fromstring(custom)
    assert root.tag == f"{{{_CUSTOM_NS}}}Properties"
    props = {}
    pids = []
    for prop in root.findall(f"{{{_CUSTOM_NS}}}property"):
        pids.append(prop.get("pid"))
        value_el = prop.find(f"{{{_VT_NS}}}lpwstr")
        assert value_el is not None
        props[prop.get("name")] = value_el.text
    assert props["PU.TemplateId"] == "PU-ACT-01"
    assert props["PU.TemplateVersion"] == "v2 & <x>"
    assert len(set(pids)) == len(pids) == 2
    assert "&amp;" in custom and "&lt;x&gt;" in custom

    content_types = _read(data, "[Content_Types].xml")
    ct_root = ET.fromstring(content_types)
    overrides = [o for o in ct_root if o.get("PartName") == "/docProps/custom.xml"]
    assert len(overrides) == 1
    assert overrides[0].get("ContentType") == _CUSTOM_CONTENT_TYPE

    # custom-properties relationship lives in the package rels, not document rels
    pkg_rels = _read(data, _PKG_RELS)
    pkg_root = ET.fromstring(pkg_rels)
    custom_rels = [r for r in pkg_root if r.get("Type") == _CUSTOM_REL_TYPE]
    assert len(custom_rels) == 1
    assert custom_rels[0].get("Target") == "docProps/custom.xml"
    pkg_ids = [r.get("Id") for r in pkg_root]
    assert len(set(pkg_ids)) == len(pkg_ids)

    doc_rels = _read(data, _DOC_RELS)
    doc_root = ET.fromstring(doc_rels)
    assert [r for r in doc_root if r.get("Type") == _CUSTOM_REL_TYPE] == []
    doc_ids = [r.get("Id") for r in doc_root]
    assert len(set(doc_ids)) == len(doc_ids)


def test_build_docx_custom_relationship_target_resolves_in_package():
    parts = ["<w:p><w:r><w:t>Текст</w:t></w:r></w:p>"]
    data = build_docx(parts, template_id="T", template_version="1")
    names = _names(data)

    pkg_root = ET.fromstring(_read(data, _PKG_RELS))
    custom_rels = [r for r in pkg_root if r.get("Type") == _CUSTOM_REL_TYPE]
    assert len(custom_rels) == 1
    target = custom_rels[0].get("Target")
    assert target

    resolved = _resolve_target(_PKG_RELS, target)
    assert resolved == "docProps/custom.xml"
    assert resolved in names


def test_build_docx_document_xml_unchanged_between_versions():
    parts = ["<w:p><w:r><w:t>Один</w:t></w:r></w:p>", page_break()]
    a = build_docx(parts, template_id="T", template_version="1")
    b = build_docx(parts, template_id="T", template_version="2")
    assert _read(a, "word/document.xml") == _read(b, "word/document.xml")
    assert _read(a, "word/document.xml") == _read(build_docx(parts), "word/document.xml")
    assert _read(a, "docProps/custom.xml") != _read(b, "docProps/custom.xml")


def test_build_docx_invalid_template_pairs_rejected():
    parts = ["<w:p><w:r><w:t>X</w:t></w:r></w:p>"]
    with pytest.raises(ValueError):
        build_docx(parts, template_id="T")
    with pytest.raises(ValueError):
        build_docx(parts, template_version="1")
    with pytest.raises(ValueError):
        build_docx(parts, template_id="", template_version="1")
    with pytest.raises(ValueError):
        build_docx(parts, template_id="T", template_version="   ")
    with pytest.raises(ValueError):
        build_docx(parts, template_id=123, template_version="1")
    with pytest.raises(ValueError):
        build_docx(parts, template_id="T", template_version=True)
    with pytest.raises(ValueError):
        build_docx(parts, template_id=None, template_version="1")


def test_build_docx_does_not_mutate_parts():
    parts = ["<w:p><w:r><w:t>A</w:t></w:r></w:p>"]
    original = list(parts)
    build_docx(parts, template_id="T", template_version="1")
    assert parts == original
