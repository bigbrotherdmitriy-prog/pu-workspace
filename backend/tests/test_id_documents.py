from __future__ import annotations

import io
import zipfile
from datetime import date

from app.id_documents import (
    AosrData,
    Material,
    OrgRequisites,
    Representative,
    ROLE_TITLES,
    aosr_lines,
    aosr_missing,
    render_aosr_docx,
)


def _full_data(number: str = "12") -> AosrData:
    developer = OrgRequisites(
        name="ООО «Застройщик»",
        ogrn="1027700000001",
        inn="7700000001",
        address="г. Москва, ул. Тестовая, 1",
        phone="+7 (495) 000-00-00",
    )
    builder = OrgRequisites(
        name="ООО «Генподрядчик»",
        ogrn="1027700000002",
        inn="7700000002",
        address="г. Москва, ул. Строителей, 2",
        phone="+7 (495) 000-00-01",
    )
    performer_org = OrgRequisites(
        name="ООО «Субподрядчик»",
        ogrn="1027700000003",
        inn="7700000003",
    )
    representatives = [
        Representative(
            role="customer_control",
            position="Инженер строительного контроля",
            full_name="Иванов Иван Иванович",
            order_ref="приказ № 1 от 10.01.2024",
            nrs_id="С-01-000001",
        ),
        Representative(
            role="builder",
            position="Генеральный директор",
            full_name="Петров Пётр Петрович",
            order_ref="приказ № 2 от 11.01.2024",
        ),
        Representative(
            role="builder_control",
            position="Начальник участка",
            full_name="Сидоров Сидор Сидорович",
            order_ref="приказ № 3 от 12.01.2024",
            nrs_id="С-01-000002",
        ),
        Representative(
            role="performer",
            position="Прораб",
            full_name="Кузнецов Кузьма Кузьмич",
            order_ref="приказ № 4 от 13.01.2024",
            organization=performer_org,
        ),
    ]
    return AosrData(
        object_name="Жилой дом, корпус 1",
        object_address="г. Москва, ул. Тестовая, 1",
        number=number,
        act_date=date(2024, 5, 20),
        developer=developer,
        builder=builder,
        designer=None,
        representatives=representatives,
        works="Устройство монолитных фундаментов",
        design_docs="Чертёж 123-АР, ООО «Проект»",
        materials=[
            Material(name="Бетон B25", quality_doc="сертификат № 100 от 01.05.2024"),
            Material(name="Арматура А500С", quality_doc="сертификат № 101 от 02.05.2024"),
        ],
        supporting_docs=["исполнительная схема № 1"],
        work_start=date(2024, 4, 1),
        work_finish=date(2024, 5, 15),
        norms=["СП 45.13330.2017", "ГОСТ 7473-2010"],
        next_works="Устройство стен подвала",
    )


def test_full_data_no_missing() -> None:
    data = _full_data()
    missing = aosr_missing(data)
    assert missing == [], missing


def test_missing_order_and_nrs() -> None:
    data = _full_data()
    for r in data.representatives:
        if r.role == "builder_control":
            r.nrs_id = None
        if r.role == "builder":
            r.order_ref = None
    missing = aosr_missing(data)
    titles = ROLE_TITLES
    expected_order = (
        f"{titles['builder']}: не указан распорядительный документ "
        f"(приказ) о полномочиях"
    )
    expected_nrs = (
        f"{titles['builder_control']}: нет ИНРС (идентификационного номера "
        f"в национальном реестре специалистов)"
    )
    assert expected_order in missing
    assert expected_nrs in missing
    assert len(missing) == 2, missing


def test_many_materials_registry() -> None:
    data = _full_data()
    data.materials = [
        Material(name=f"Материал {i}", quality_doc=f"сертификат № {i}")
        for i in range(1, 8)
    ]
    lines = aosr_lines(data)
    assert "реестр" in lines["p3"].lower()
    registry = lines["materials_registry"]
    assert registry[0] == ["№", "Наименование материала", "Документ о качестве"]
    assert len(registry) == 1 + 7


def test_copies_defaults_to_signers_count() -> None:
    data = _full_data()
    data.copies = None
    assert len(data.representatives) == 4
    lines = aosr_lines(data)
    assert "в 4 экземплярах" in lines["copies"]


def test_render_docx_contents() -> None:
    data = _full_data(number="12")
    data.act_date = None
    payload = render_aosr_docx(data)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        assert "word/document.xml" in zf.namelist()
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "освидетельствования скрытых работ № 12" in xml
    assert "Иванов Иван Иванович" in xml
    assert "________" in xml
