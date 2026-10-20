from app.rd_volume import (
    parse_main_set_sheets,
    parse_referenced_norms,
    parse_sheet_list,
    parse_volume_code,
    section_of,
    volume_summary,
)


TEXT = (
    "Обозначение Наименование Примечание "
    "Д/ФНС.2024-ЭМ-С Содержание "
    "Д/ФНС.2024-СП Состав проектной документации "
    "Д/ФНС.2024-ЭМ.ТЧ Рабочая документация «Система электроснабжения». Текстовая часть. "
    "Д/ФНС.2024-ЭМ.ГЧ л.1 Существующая схема электроснабжения. "
    "Д/ФНС.2024-ЭМ.ГЧ л.2 Схема электроснабжения после модернизации "
    "Д/ФНС.2024-ЭМ.ГЧ л.32 План кабельных трасс "
    "Д/ФНС.2024-ЭМ.ГЧ л.33 План кабельных лотков "
    "Прилагаемые документы "
    "Д/ФНС.2024-ЭМ.СО Спецификация оборудования, изделий и материалов "
    "Д/ФНС.2024-ЭМ.КЖ Кабельный журнал "
    "Д/ФНС.2024-ЭМ.ДВ Ведомость демонтируемого оборудования "
    "Изм. Кол.уч. Лист № док. Подпись Дата "
    "Д/ФНС.2024-ЭМ-С"
)


def test_volume_code_and_section():
    assert parse_volume_code(TEXT) == "Д/ФНС.2024-ЭМ"
    summary = volume_summary(TEXT)
    assert summary["section"] == "ЭМ"


def test_sheet_list_parses_entries():
    sheets = parse_sheet_list(TEXT)
    target = {
        "code": "Д/ФНС.2024-ЭМ.ГЧ",
        "sheet": 32,
        "title": "План кабельных трасс",
        "kind": "drawing",
    }
    assert target in sheets

    cable_journal = next(s for s in sheets if s["code"] == "Д/ФНС.2024-ЭМ.КЖ")
    assert cable_journal["kind"] == "cable_journal"

    schedule = next(s for s in sheets if s["code"] == "Д/ФНС.2024-ЭМ.СО")
    assert schedule["kind"] == "schedule"

    for entry in sheets:
        assert "Изм." not in entry["title"]


def test_volume_summary_counts():
    summary = volume_summary(TEXT)
    assert summary["drawings"] == 4
    assert summary["has_specification"] is True
    assert summary["has_cable_journal"] is True


def test_section_of_returns_expected_groups():
    assert section_of("Д/ФНС.2024-АР") == "АР"
    assert section_of("X-СКТП") == "ОВиК"


AR_TEXT = (
    "Ведомость основных комплектов рабочих чертежей Обозначение Наименование Примечание "
    "Пояснительная записка Д/ФНС.2024-АР Архитектурные решения "
    "Д/ФНС.2024-ЭМ Система электроснабжение "
    "Ведомость ссылочных и прилагаемых документов Обозначение Наименование Примечание "
    "Ссылочные документы "
    "СНиП 21.01-97* Пожарная безопасность зданий и сооружений "
    "СП 131.13330.2020 Строительная климатология "
    "СП 68.13330.2017 Приемка в эксплуатацию законченных строительством объектов. "
    "Ведомость рабочих чертежей основного комплекта Лист Наименование Примечание "
    "Общие данные Лист №1-11 Д/ФНС.2024-АР Дверные проемы Лист №12 "
    "Инв. № подл. Подпись и дата Взам. инв. № Д/ФНС.2024-АР"
)


def test_main_set_sheets_parsed():
    entries = parse_main_set_sheets(AR_TEXT, "Д/ФНС.2024-АР")
    common = next(e for e in entries if e["sheet"] == 1)
    assert common["sheet_to"] == 11
    assert common["title"] == "Общие данные"
    assert common["kind"] == "text"
    doors = next(e for e in entries if e["sheet"] == 12)
    assert doors["code"] == "Д/ФНС.2024-АР"
    assert doors["sheet_to"] is None
    assert doors["title"] == "Дверные проемы"
    assert doors["kind"] == "drawing"


def test_referenced_norms_parsed():
    norms = parse_referenced_norms(AR_TEXT)
    assert len(norms) == 3
    assert norms[1] == {"code": "СП 131.13330.2020", "title": "Строительная климатология"}


def test_volume_summary_includes_main_set_and_references():
    summary = volume_summary(AR_TEXT)
    assert summary["volume_code"] == "Д/ФНС.2024-АР"
    sheets = summary["sheets"]
    target = {
        "code": "Д/ФНС.2024-АР",
        "sheet": 12,
        "sheet_to": None,
        "title": "Дверные проемы",
        "kind": "drawing",
    }
    assert target in sheets
    common = {
        "code": "Д/ФНС.2024-АР",
        "sheet": 1,
        "sheet_to": 11,
        "title": "Общие данные",
        "kind": "text",
    }
    assert common in sheets
    refs = {r["code"] for r in summary["referenced_volumes"]}
    assert "Д/ФНС.2024-ЭМ" in refs
    assert len(summary["norms"]) == 3


STAMP_TEXT = (
    "П2200175-11-021-РЗ Лист 1 Общие данные "
    "П2200175-11-021-РЗ Лист 2 Схема "
    "ГОСТ 2.104-2006 Правила оформления "
)


def test_volume_code_generic_with_stamps():
    assert parse_volume_code(STAMP_TEXT) == "П2200175-11-021-РЗ"
    assert section_of(parse_volume_code(STAMP_TEXT) or "") == "РЗА"


def test_section_of_new_groups():
    assert section_of("П2200171-11-027-КХ") == "КХ"
    assert section_of("П2200171-11-023-УА") == "АСУ ТП"
    assert section_of("Х-11-ZZZ") == "ZZZ"
