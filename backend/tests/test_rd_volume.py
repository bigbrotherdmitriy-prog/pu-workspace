from app.rd_volume import parse_sheet_list, parse_volume_code, section_of, volume_summary


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
