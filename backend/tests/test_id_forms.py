import posixpath
from xml.etree import ElementTree as ET
from app.docx_kit import build_docx, page_break
import io
import zipfile
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import equipment_test_act as eta
from app import work_log as wl
from app import work_log_form
from app.database import Base
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
    """Resolve an OPC relationship Target relative to its source part.

    The base directory is the directory of the *source part* itself. For the
    package-level relationships part ``_rels/.rels`` the source part is the
    package root, so relative targets resolve against the package root (not
    against the ``_rels`` directory). For a part such as
    ``word/_rels/document.xml.rels`` the source part is ``word/document.xml``
    and relative targets resolve against ``word``.
    """
    if target.startswith("/"):
        return target.lstrip("/")
    if source_part == "_rels/.rels":
        base = ""
    else:
        # strip the trailing ".rels" and the "_rels/" segment to get the
        # source part, then take its directory
        rels_dir = posixpath.dirname(source_part)
        source_dir = posixpath.dirname(rels_dir)
        base = source_dir
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

# --- Equipment act template metadata (renderer) tests ---

_EQUIPMENT_SYNTHETIC_OBJECT = "СИНТЕТИЧЕСКИЙ ОБРАЗЕЦ — НЕ ДЛЯ ПОДПИСАНИЯ"


def _equipment_template_header(**extra) -> dict:
    header = {"object_name": _EQUIPMENT_SYNTHETIC_OBJECT}
    header.update(extra)
    return header


def _equipment_template_items() -> list[dict]:
    return [{"name": "SYNTHETIC", "result": "pass"}]


def _equipment_template_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        return zf.namelist()


def _equipment_template_read(data: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        return zf.read(name).decode("utf-8")


def _equipment_template_custom_props(data: bytes) -> dict:
    custom = _equipment_template_read(data, "docProps/custom.xml")
    root = ET.fromstring(custom)
    assert root.tag == f"{{{_CUSTOM_NS}}}Properties"
    props = {}
    for prop in root.findall(f"{{{_CUSTOM_NS}}}property"):
        value_el = prop.find(f"{{{_VT_NS}}}lpwstr")
        assert value_el is not None
        props[prop.get("name")] = value_el.text
    return props


def _equipment_template_assert_package_consistent(data: bytes) -> None:
    names = _equipment_template_names(data)
    assert "word/document.xml" in names
    assert "docProps/custom.xml" in names
    ET.fromstring(_equipment_template_read(data, "word/document.xml"))
    ET.fromstring(_equipment_template_read(data, "[Content_Types].xml"))
    ET.fromstring(_equipment_template_read(data, "_rels/.rels"))
    ET.fromstring(_equipment_template_read(data, "word/_rels/document.xml.rels"))
    ct_root = ET.fromstring(_equipment_template_read(data, "[Content_Types].xml"))
    overrides = [o for o in ct_root if o.get("PartName") == "/docProps/custom.xml"]
    assert len(overrides) == 1
    assert overrides[0].get("ContentType") == _CUSTOM_CONTENT_TYPE
    rel_root = ET.fromstring(_equipment_template_read(data, "_rels/.rels"))
    custom_rels = [r for r in rel_root if r.get("Type") == _CUSTOM_REL_TYPE]
    assert len(custom_rels) == 1
    assert custom_rels[0].get("Target") == "docProps/custom.xml"
    ids = [r.get("Id") for r in rel_root]
    assert len(set(ids)) == len(ids)


def test_equipment_template_omitted_and_none_use_legacy_builder(monkeypatch):
    calls = []

    def spy(parts, *args, **kwargs):
        calls.append((list(parts), args, dict(kwargs)))
        return build_docx(parts, *args, **kwargs)

    monkeypatch.setattr(eta, "build_docx", spy)
    header = _equipment_template_header()
    items = _equipment_template_items()

    omitted = eta.render(header, items)
    assert len(calls) == 1
    parts, args, kwargs = calls[0]
    assert args == () and kwargs == {}
    assert isinstance(parts, list) and parts
    assert "docProps/custom.xml" not in _equipment_template_names(omitted)

    calls.clear()
    both_none = eta.render(header, items, template_id=None, template_version=None)
    assert len(calls) == 1
    parts, args, kwargs = calls[0]
    assert args == () and kwargs == {}
    assert "docProps/custom.xml" not in _equipment_template_names(both_none)

    assert _equipment_template_read(omitted, "word/document.xml") == _equipment_template_read(
        both_none, "word/document.xml"
    )


def test_equipment_template_explicit_metadata_forwarded_and_parsed(monkeypatch):
    calls = []

    def spy(parts, *args, **kwargs):
        calls.append((list(parts), args, dict(kwargs)))
        return build_docx(parts, *args, **kwargs)

    monkeypatch.setattr(eta, "build_docx", spy)
    header = _equipment_template_header()
    items = _equipment_template_items()

    data = eta.render(header, items, template_id="  PU-EQ-01  ", template_version="  v1 & <x>  ")
    assert len(calls) == 1
    _, args, kwargs = calls[0]
    assert args == ()
    assert kwargs == {"template_id": "  PU-EQ-01  ", "template_version": "  v1 & <x>  "}

    _equipment_template_assert_package_consistent(data)
    props = _equipment_template_custom_props(data)
    assert props["PU.TemplateId"] == "PU-EQ-01"
    assert props["PU.TemplateVersion"] == "v1 & <x>"
    custom = _equipment_template_read(data, "docProps/custom.xml")
    assert "&amp;" in custom and "&lt;x&gt;" in custom


def test_equipment_template_document_xml_unchanged_with_metadata():
    header = _equipment_template_header()
    items = _equipment_template_items()
    plain = eta.render(header, items)
    with_meta = eta.render(header, items, template_id="PU-EQ-01", template_version="1")
    assert "docProps/custom.xml" not in _equipment_template_names(plain)
    assert "docProps/custom.xml" in _equipment_template_names(with_meta)
    assert _equipment_template_read(plain, "word/document.xml") == _equipment_template_read(
        with_meta, "word/document.xml"
    )


def test_equipment_template_header_keys_alone_do_not_enable_metadata():
    header = _equipment_template_header(template_id="PU-EQ-01", template_version="1")
    data = eta.render(header, _equipment_template_items())
    assert "docProps/custom.xml" not in _equipment_template_names(data)


def test_equipment_template_does_not_mutate_inputs():
    import copy

    header = _equipment_template_header(act_no="1")
    items = _equipment_template_items()
    header_before = copy.deepcopy(header)
    items_before = copy.deepcopy(items)

    eta.render(header, items, template_id="PU-EQ-01", template_version="1")
    assert header == header_before and items == items_before

    with pytest.raises(ValueError):
        eta.render(header, items, template_id="PU-EQ-01")
    assert header == header_before and items == items_before


@pytest.mark.parametrize(
    "template_id,template_version",
    [
        ("PU-EQ-01", None),
        (None, "1"),
        ("", "1"),
        ("PU-EQ-01", ""),
        ("   ", "1"),
        ("PU-EQ-01", "   "),
        (True, "1"),
        ("PU-EQ-01", 1),
        ("PU-EQ-01\x00", "1"),
        ("PU-EQ-01", "1\x1f"),
    ],
)
def test_equipment_template_invalid_pairs_rejected(template_id, template_version):
    with pytest.raises(ValueError):
        eta.render(
            _equipment_template_header(),
            _equipment_template_items(),
            template_id=template_id,
            template_version=template_version,
        )


# --- Work log form template metadata (renderer) tests ---

_WORKLOG_SYNTHETIC_OBJECT = "СИНТЕТИЧЕСКИЙ ОБЪЕКТ — НЕ ДЛЯ ПОДПИСАНИЯ"


def _worklog_template_header(**extra) -> dict:
    header = {"object_name": _WORKLOG_SYNTHETIC_OBJECT, "journal_no": "1"}
    header.update(extra)
    return header


def _worklog_template_custom_props(data: bytes) -> dict:
    custom = _read(data, "docProps/custom.xml")
    root = ET.fromstring(custom)
    assert root.tag == f"{{{_CUSTOM_NS}}}Properties"
    props = {}
    for prop in root.findall(f"{{{_CUSTOM_NS}}}property"):
        value_el = prop.find(f"{{{_VT_NS}}}lpwstr")
        assert value_el is not None
        props[prop.get("name")] = value_el.text
    return props


def _worklog_template_assert_package_consistent(data: bytes) -> None:
    names = _names(data)
    assert "word/document.xml" in names
    assert "docProps/custom.xml" in names
    ET.fromstring(_read(data, "word/document.xml"))
    ET.fromstring(_read(data, "[Content_Types].xml"))
    ET.fromstring(_read(data, _PKG_RELS))
    ET.fromstring(_read(data, _DOC_RELS))
    ct_root = ET.fromstring(_read(data, "[Content_Types].xml"))
    overrides = [o for o in ct_root if o.get("PartName") == "/docProps/custom.xml"]
    assert len(overrides) == 1
    assert overrides[0].get("ContentType") == _CUSTOM_CONTENT_TYPE
    # relationship lookup must use the package rels, not document rels
    pkg_root = ET.fromstring(_read(data, _PKG_RELS))
    custom_rels = [r for r in pkg_root if r.get("Type") == _CUSTOM_REL_TYPE]
    assert len(custom_rels) == 1
    target = custom_rels[0].get("Target")
    assert target
    resolved = _resolve_target(_PKG_RELS, target)
    assert resolved == "docProps/custom.xml"
    assert resolved in names
    ids = [r.get("Id") for r in pkg_root]
    assert len(set(ids)) == len(ids)
    doc_root = ET.fromstring(_read(data, _DOC_RELS))
    assert [r for r in doc_root if r.get("Type") == _CUSTOM_REL_TYPE] == []


def test_worklog_template_legacy_positional_dates_use_legacy_builder(world):
    db, pid = world["db"], world["p"].id
    header = _worklog_template_header()
    legacy = work_log_form.render(db, pid, header, date(2026, 10, 1), date(2026, 10, 31))
    assert "docProps/custom.xml" not in _names(legacy)
    assert "word/document.xml" in _names(legacy)
    assert 'w:orient="landscape"' in _read(legacy, "word/document.xml")


def test_worklog_template_explicit_both_none_uses_legacy_builder(world):
    db, pid = world["db"], world["p"].id
    header = _worklog_template_header()
    omitted = work_log_form.render(db, pid, header)
    both_none = work_log_form.render(db, pid, header, template_id=None, template_version=None)
    assert "docProps/custom.xml" not in _names(omitted)
    assert "docProps/custom.xml" not in _names(both_none)
    assert _read(omitted, "word/document.xml") == _read(both_none, "word/document.xml")


def test_worklog_template_explicit_metadata_parsed_and_package_consistent(world):
    db, pid = world["db"], world["p"].id
    header = _worklog_template_header()
    data = work_log_form.render(
        db, pid, header, template_id="  PU-WL-01  ", template_version="  v1 & <x>  "
    )
    _worklog_template_assert_package_consistent(data)
    props = _worklog_template_custom_props(data)
    assert props["PU.TemplateId"] == "PU-WL-01"
    assert props["PU.TemplateVersion"] == "v1 & <x>"
    custom = _read(data, "docProps/custom.xml")
    assert "&amp;" in custom and "&lt;x&gt;" in custom
    assert 'w:orient="landscape"' in _read(data, "word/document.xml")


def test_worklog_template_document_xml_unchanged_with_metadata(world):
    db, pid = world["db"], world["p"].id
    header = _worklog_template_header()
    plain = work_log_form.render(db, pid, header)
    with_meta = work_log_form.render(db, pid, header, template_id="PU-WL-01", template_version="1")
    assert "docProps/custom.xml" not in _names(plain)
    assert "docProps/custom.xml" in _names(with_meta)
    assert _read(plain, "word/document.xml") == _read(with_meta, "word/document.xml")
    assert 'w:orient="landscape"' in _read(with_meta, "word/document.xml")


def test_worklog_template_document_xml_unchanged_between_versions(world):
    db, pid = world["db"], world["p"].id
    header = _worklog_template_header()
    a = work_log_form.render(db, pid, header, template_id="PU-WL-01", template_version="1")
    b = work_log_form.render(db, pid, header, template_id="PU-WL-01", template_version="2")
    assert _read(a, "word/document.xml") == _read(b, "word/document.xml")
    assert _read(a, "docProps/custom.xml") != _read(b, "docProps/custom.xml")


@pytest.mark.parametrize(
    "template_id,template_version",
    [
        ("PU-WL-01", None),
        (None, "1"),
        ("", "1"),
        ("PU-WL-01", ""),
        ("   ", "1"),
        ("PU-WL-01", "   "),
        ("PU-WL-01\x00", "1"),
        ("PU-WL-01", "1\x1f"),
    ],
)
def test_worklog_template_invalid_pairs_rejected(world, template_id, template_version):
    db, pid = world["db"], world["p"].id
    with pytest.raises(ValueError):
        work_log_form.render(
            db, pid, _worklog_template_header(), template_id=template_id, template_version=template_version
        )
