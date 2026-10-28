from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import pytest

import app.models  # noqa: F401
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdSheet, RdVolume
from app.rd_sheets import build_register, classify_page, parse_stamp


STAMP_TEXT = (
    "ООО \"Проект\"\n"
    "Изм. Кол.уч Лист Подп. Дата\n"
    "Разраб. Иванов\n"
    "Н. контр. Петров\n"
    "Стадия Листов\n"
    "Лист 12\n"
    "Наименование листа: Схема электрическая принципиальная\n"
)


def test_classify_spec():
    assert classify_page("anything", True) == "spec"


def test_classify_blank():
    assert classify_page("", False) == "blank"
    assert classify_page("   \n\t  ", False) == "blank"
    assert classify_page("коротко", False) == "blank"


def test_classify_drawing():
    assert classify_page(STAMP_TEXT, False) == "drawing"


def test_classify_text():
    text = (
        "Настоящий документ содержит общие указания по монтажу "
        "и эксплуатации электрооборудования объекта."
    )
    assert classify_page(text, False) == "text"


def test_parse_stamp_full():
    stamp = (
        "Изм. Кол.уч Лист Подп. Дата\n"
        "Лист 5\n"
        "Наименование листа: Схема электрическая принципиальная\n"
    )
    result = parse_stamp(stamp)
    assert result["sheet_no"] == "5"
    assert result["sheet_title"] == "Схема электрическая принципиальная"


def test_parse_stamp_no_title():
    stamp = "Кол.уч. Лист 7\nПодп.\n"
    result = parse_stamp(stamp)
    assert result["sheet_no"] == "7"
    assert result["sheet_title"] is None


def test_parse_stamp_garbage():
    assert parse_stamp(None) == {"sheet_no": None, "sheet_title": None}
    assert parse_stamp("") == {"sheet_no": None, "sheet_title": None}
    assert parse_stamp("\x00\x01###") == {"sheet_no": None, "sheet_title": None}


def test_build_register_fields():
    pages = ["", STAMP_TEXT, "Схема расположения оборудования на плане"]
    spec_pages = [1]
    sources = ["native", "ocr", "native"]
    reg = build_register(pages, spec_pages, sources)
    assert len(reg) == 3
    assert reg[0]["page_no"] == 1
    assert reg[0]["kind"] == "spec"
    assert reg[0]["text_source"] == "native"
    assert reg[0]["text_len"] == 0
    assert reg[1]["page_no"] == 2
    assert reg[1]["kind"] == "drawing"
    assert reg[1]["sheet_no"] == "12"
    assert reg[1]["text_source"] == "ocr"
    assert reg[1]["text_len"] == len(STAMP_TEXT.strip())
    assert reg[2]["page_no"] == 3
    assert reg[2]["text_source"] == "native"


def test_build_register_short_sources():
    reg = build_register(["a" * 30, "b" * 30], [], ["native"])
    assert reg[1]["text_source"] == "none"


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="RD sheets org")
        db.add(org)
        db.flush()
        project = Project(name="RD sheets project", organization_id=org.id)
        db.add(project)
        db.flush()
        vol = RdVolume(
            project_id=project.id,
            title="Том",
            file_name="vol.pdf",
            provider="google_drive",
            external_id="file-1",
        )
        db.add(vol)
        db.commit()
        yield {"db": db, "project": project, "volume": vol}
    engine.dispose()


def test_rd_sheet_insert_and_unique(world):
    db = world["db"]
    project = world["project"]
    vol = world["volume"]

    sheet = RdSheet(
        volume_id=vol.id,
        project_id=project.id,
        page_no=1,
        sheet_no="1",
        sheet_title="Титул",
        kind="text",
        text_source="native",
        text_len=100,
    )
    db.add(sheet)
    db.commit()
    assert sheet.id is not None

    dup = RdSheet(
        volume_id=vol.id,
        project_id=project.id,
        page_no=1,
        kind="drawing",
        text_source="ocr",
        text_len=0,
    )
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
