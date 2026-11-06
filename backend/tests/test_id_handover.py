import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import id_forms as api
from app.core.auth import require_user
from app.database import Base, get_db
from app.models.executive_docs import IdParticipant, IdRequirement
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdSpecItem, RdVolume


def _text(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="ООО Монтаж")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        db.add(p)
        db.flush()
        v = RdVolume(project_id=p.id, object_name="ДУБНА", section="ЭМ", title="ЭМ", file_name="em.pdf", provider="pu_storage", external_id="1")
        db.add(v)
        db.flush()
        db.add(RdSpecItem(volume_id=v.id, project_id=p.id, name="Щит ЩР-1", type_mark="ЩРн-24", unit="шт", status="confirmed"))
        db.add(RdSpecItem(volume_id=v.id, project_id=p.id, name="Лишнее", status="rejected"))
        db.add(IdRequirement(project_id=p.id, object_name="ДУБНА", title="Журнал прокладки кабелей", doc_kind="journal", source="kit"))
        db.add(IdParticipant(project_id=p.id, object_name="ДУБНА", role="customer_control", full_name="Петров П.П.", position="инженер"))
        db.commit()
        yield {"db": db, "p": p}
    engine.dispose()


def _client(world):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: world["db"]
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    return TestClient(app)


def test_defaults_come_from_project(world):
    h = _client(world).get(f"/projects/{world['p'].id}/executive-docs/handover/defaults", params={"object_name": "ДУБНА"}).json()
    assert h["contractor"] == "ООО Монтаж" and h["customer_rep"] == "Петров П.П., инженер" and h["object_name"] == "ДУБНА"


def test_equipment_register_from_rd(world):
    c = _client(world)
    pid = world["p"].id
    r = c.post(f"/projects/{pid}/executive-docs/equipment-register.docx", json={"object_name": "ДУБНА", "header": {"object_name": "ДУБНА"}})
    assert r.status_code == 200
    xml = _text(r.content)
    assert "ЩРн-24" in xml and "Лишнее" not in xml
    assert c.post(f"/projects/{pid}/executive-docs/equipment-register.xlsx", json={"object_name": "ДУБНА"}).status_code == 200
    assert c.post(f"/projects/{pid}/executive-docs/equipment-register.pdf", json={}).status_code == 404


def test_id_register_lists_kit_with_status(world):
    c = _client(world)
    r = c.post(f"/projects/{world['p'].id}/executive-docs/id-register.docx", json={"object_name": "ДУБНА"})
    assert r.status_code == 200
    xml = _text(r.content)
    assert "Журнал прокладки кабелей" in xml and "нет" in xml
    assert c.post(f"/projects/{world['p'].id}/executive-docs/id-register.docx", json={}).status_code == 422


def test_trial_and_commissioning_acts(world):
    c = _client(world)
    pid = world["p"].id
    r = c.post(f"/projects/{pid}/executive-docs/trial-act.docx", json={
        "header": {"act_no": "7", "start": "01.11.2026 09:00", "end": "04.11.2026 09:00", "hours": "72"},
        "items": [{"name": "Электроснабжение", "mode": "нагрузка 60 %", "result": "pass"}]})
    assert r.status_code == 200 and "72" in _text(r.content) and "Электроснабжение" in _text(r.content)
    assert c.post(f"/projects/{pid}/executive-docs/trial-act.docx", json={"items": []}).status_code == 422
    r = c.post(f"/projects/{pid}/executive-docs/commissioning-act.docx", json={"header": {"scope": "ЭМ"}})
    assert r.status_code == 200 and "ввода в эксплуатацию" in _text(r.content)


def _add_scope_data(db, p):
    v2 = RdVolume(project_id=p.id, object_name="ВТОРОЙ", section="ЭМ", title="ЭМ-2", file_name="em2.pdf", provider="pu_storage", external_id="2")
    db.add(v2)
    db.flush()
    db.add(RdSpecItem(volume_id=v2.id, project_id=p.id, name="Кабель КВ-2", type_mark="ВВГ", unit="м", status="confirmed"))
    db.add(RdSpecItem(volume_id=v2.id, project_id=p.id, name="Отклонено-2", status="rejected"))
    org2 = Organization(name="ООО Чужой")
    db.add(org2)
    db.flush()
    p2 = Project(name="P2", organization_id=org2.id)
    db.add(p2)
    db.flush()
    v3 = RdVolume(project_id=p2.id, object_name="ДУБНА", section="ЭМ", title="ЭМ-3", file_name="em3.pdf", provider="pu_storage", external_id="3")
    db.add(v3)
    db.flush()
    db.add(RdSpecItem(volume_id=v3.id, project_id=p2.id, name="Чужой Щит", type_mark="ЧУЖ", unit="шт", status="confirmed"))
    db.commit()
    return p2


def test_service_object_scope_known_and_second_isolated(world):
    from app.id_handover import equipment_rows

    db, p = world["db"], world["p"]
    _add_scope_data(db, p)
    first = equipment_rows(db, p.id, "ДУБНА")
    second = equipment_rows(db, p.id, "ВТОРОЙ")
    assert [r["name"] for r in first] == ["Щит ЩР-1"]
    assert [r["name"] for r in second] == ["Кабель КВ-2"]


def test_service_object_scope_unknown_returns_empty(world):
    from app.id_handover import equipment_rows

    db, p = world["db"], world["p"]
    _add_scope_data(db, p)
    assert equipment_rows(db, p.id, "SYNTHETIC-NO-SUCH-OBJECT") == []


def test_service_object_scope_projectwide_for_none_and_empty(world):
    from app.id_handover import equipment_rows

    db, p = world["db"], world["p"]
    _add_scope_data(db, p)
    for scope in (None, ""):
        names = [r["name"] for r in equipment_rows(db, p.id, scope)]
        assert names == ["Щит ЩР-1", "Кабель КВ-2"]
        assert "Лишнее" not in names and "Отклонено-2" not in names and "Чужой Щит" not in names


def test_service_object_scope_reads_do_not_mutate(world):
    from app.id_handover import equipment_rows

    db, p = world["db"], world["p"]
    _add_scope_data(db, p)
    before = (db.query(RdVolume).count(), db.query(RdSpecItem).count())
    equipment_rows(db, p.id, "ДУБНА")
    equipment_rows(db, p.id, "SYNTHETIC-NO-SUCH-OBJECT")
    equipment_rows(db, p.id, None)
    equipment_rows(db, p.id, "")
    after = (db.query(RdVolume).count(), db.query(RdSpecItem).count())
    assert before == after


# ---------- XLSX literal text regression (no DB, no HTTP) ----------

_HARMFUL_STRINGS = ["=1+1", "+text", "-text", "@text", "Unicode & <tag> \"quotes\" 'apos'", "line1\nline2", "#REF!"]


def _load_xlsx(data: bytes):
    from openpyxl import load_workbook

    return load_workbook(io.BytesIO(data), data_only=False)


def _equipment_row(**overrides) -> dict:
    row = {
        "name": "Щит ЩР-1", "type_mark": "ЩРн-24", "code": "C-1", "manufacturer": "Завод",
        "unit": "шт", "qty": "2", "section": "ЭМ", "volume": "ЭМ-1",
    }
    row.update(overrides)
    return row


def _register_row(**overrides) -> dict:
    row = {
        "title": "Журнал прокладки кабелей", "kind": "Журнал", "section": "ЭМ",
        "status": "есть", "required": True, "files": "journal.pdf",
    }
    row.update(overrides)
    return row


def _render(kind: str, header: dict, rows: list[dict]) -> bytes:
    from app.id_handover import render_equipment_register_xlsx, render_register_xlsx

    if kind == "equipment":
        return render_equipment_register_xlsx(header, rows)
    return render_register_xlsx(header, rows)


def _make_row(export_kind: str, **overrides) -> dict:
    return _equipment_row(**overrides) if export_kind == "equipment" else _register_row(**overrides)


def _text_columns(kind: str) -> list[str]:
    if kind == "equipment":
        return ["name", "type_mark", "code", "manufacturer", "unit", "qty", "section", "volume"]
    return ["title", "kind", "section", "status", "files"]


def _column_index(kind: str, key: str) -> int:
    if kind == "equipment":
        order = ["name", "type_mark", "code", "manufacturer", "unit", "qty", "section", "volume"]
        return 2 + order.index(key)
    order = ["title", "kind", "section", "status", "required", "files"]
    return 2 + order.index(key)


def _assert_no_formula_cells(data: bytes) -> None:
    wb = _load_xlsx(data)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert cell.data_type != "f", f"formula cell at {cell.coordinate}: {cell.value!r}"
    xml = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode()
    assert "<f" not in xml


def _assert_zip_crc(data: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None


@pytest.mark.parametrize("kind,column", [
    ("equipment", "name"),
    ("equipment", "type_mark"),
    ("equipment", "code"),
    ("equipment", "manufacturer"),
    ("equipment", "unit"),
    ("equipment", "qty"),
    ("equipment", "section"),
    ("equipment", "volume"),
    ("register", "title"),
    ("register", "kind"),
    ("register", "section"),
    ("register", "status"),
    ("register", "files"),
])
@pytest.mark.parametrize("harmful", _HARMFUL_STRINGS)
def test_xlsx_literal_text_equipment_columns(kind, column, harmful):
    header = {"object_name": "ДУБНА"}
    row = _make_row(kind, **{column: harmful})
    data = _render(kind, header, [row])
    _assert_zip_crc(data)
    _assert_no_formula_cells(data)
    ws = _load_xlsx(data).worksheets[0]
    cell = ws.cell(row=3, column=_column_index(kind, column))
    assert cell.value == harmful
    assert cell.data_type == "s"


@pytest.mark.parametrize("kind", ["equipment", "register"])
@pytest.mark.parametrize("harmful", _HARMFUL_STRINGS)
def test_xlsx_literal_text_all_columns_filled(kind, harmful):
    header = {"object_name": harmful}
    row = _make_row(kind, **{key: harmful for key in _text_columns(kind)})
    data = _render(kind, header, [row])
    _assert_zip_crc(data)
    _assert_no_formula_cells(data)
    ws = _load_xlsx(data).worksheets[0]
    for key in _text_columns(kind):
        cell = ws.cell(row=3, column=_column_index(kind, key))
        assert cell.value == harmful, key
        assert cell.data_type == "s", key


@pytest.mark.parametrize("kind", ["equipment", "register"])
def test_xlsx_literal_text_no_apostrophe_or_strip(kind):
    header = {"object_name": "ДУБНА"}
    row = _make_row(kind, **{key: "  =1+1  " for key in _text_columns(kind)})
    data = _render(kind, header, [row])
    ws = _load_xlsx(data).worksheets[0]
    for key in _text_columns(kind):
        cell = ws.cell(row=3, column=_column_index(kind, key))
        assert cell.value == "  =1+1  ", key
        assert cell.data_type == "s", key
        assert not str(cell.value).startswith("'")


@pytest.mark.parametrize("kind", ["equipment", "register"])
def test_xlsx_numeric_and_optional_control(kind):
    header = {"object_name": "ДУБНА"}
    if kind == "equipment":
        row = _equipment_row(qty=2)
        data = _render(kind, header, [row])
        ws = _load_xlsx(data).worksheets[0]
        assert ws.cell(row=3, column=1).value == 1
        assert ws.cell(row=3, column=1).data_type == "n"
        qty_cell = ws.cell(row=3, column=_column_index(kind, "qty"))
        assert qty_cell.value == 2
        assert qty_cell.data_type == "n"
    else:
        row = _register_row(required=True)
        data = _render(kind, header, [row])
        ws = _load_xlsx(data).worksheets[0]
        assert ws.cell(row=3, column=1).value == 1
        assert ws.cell(row=3, column=1).data_type == "n"
        assert ws.cell(row=3, column=_column_index(kind, "required")).value == "да"


@pytest.mark.parametrize("kind", ["equipment", "register"])
def test_xlsx_text_quantity_string_control(kind):
    header = {"object_name": "ДУБНА"}
    if kind == "equipment":
        row = _equipment_row(qty="2")
        data = _render(kind, header, [row])
        ws = _load_xlsx(data).worksheets[0]
        qty_cell = ws.cell(row=3, column=_column_index(kind, "qty"))
        assert qty_cell.value == "2"
        assert qty_cell.data_type == "s"
    else:
        row = _register_row(required=False)
        data = _render(kind, header, [row])
        ws = _load_xlsx(data).worksheets[0]
        assert ws.cell(row=3, column=_column_index(kind, "required")).value == "нет"


@pytest.mark.parametrize("kind", ["equipment", "register"])
@pytest.mark.parametrize("blank", [None, ""])
def test_xlsx_none_and_blank_legacy_semantics(kind, blank):
    header = {"object_name": "ДУБНА"}
    row = _make_row(kind, **{key: blank for key in _text_columns(kind)})
    data = _render(kind, header, [row])
    ws = _load_xlsx(data).worksheets[0]
    for key in _text_columns(kind):
        cell = ws.cell(row=3, column=_column_index(kind, key))
        assert cell.value is None, key


@pytest.mark.parametrize("kind", ["equipment", "register"])
def test_xlsx_title_sheetnames_widths_unchanged(kind):
    header = {"object_name": "ДУБНА"}
    row = _make_row(kind)
    data = _render(kind, header, [row])
    wb = _load_xlsx(data)
    ws = wb.worksheets[0]
    if kind == "equipment":
        assert ws.title == "Ведомость оборудования"
        expected = {"A": 5, "B": 50, "C": 28, "D": 18, "E": 24, "F": 7, "G": 10, "H": 10, "I": 24}
    else:
        assert ws.title == "Реестр ИД"
        expected = {"A": 5, "B": 60, "C": 18, "D": 14, "E": 12, "F": 14, "G": 40}
    for col, width in expected.items():
        assert ws.column_dimensions[col].width == width, col


@pytest.mark.parametrize("kind", ["equipment", "register"])
def test_xlsx_input_header_and_rows_deepcopies_unchanged(kind):
    import copy

    header = {"object_name": "=1+1", "customer": "@text"}
    row = _make_row(kind, **{key: "=1+1" for key in _text_columns(kind)})
    header_before = copy.deepcopy(header)
    rows_before = copy.deepcopy([row])
    _render(kind, header, [row])
    assert header == header_before
    assert [row] == rows_before


@pytest.mark.parametrize("kind", ["equipment", "register"])
def test_xlsx_empty_rows_raises(kind):
    with pytest.raises(ValueError):
        _render(kind, {"object_name": "ДУБНА"}, [])


def test_docx_compat_qty_string_literal_names():
    from app.id_handover import render_equipment_register

    sample = "=1+1"
    rows = [_equipment_row(name=sample, qty="2")]
    data = render_equipment_register({"object_name": "ДУБНА"}, rows)
    xml = _text(data)
    assert sample in xml
    assert "2" in xml
