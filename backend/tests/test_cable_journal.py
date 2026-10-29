"""Тесты универсального разбора кабельного журнала."""

from decimal import Decimal

from app.cable_journal import (
    find_header,
    parse_cable_journal,
    rows_from_tsv,
    summary,
)


TSV_TEXT = (
    "Сумма по полю Общая длина, м\tНазвания столбцов\t__PU_SOURCE_COORD__:Сводка:1\n"
    "\tНаименование монтажной единицы\tМаркировка кабеля\tЗаводская маркировка\t"
    "Кол-во и сечение жил\tКол-во занятых жил\tМаркировка цепей\tОткуда\tКуда\t"
    "Общая длина, м\tВ мет. каб. лотках (ОПУ)\tВ ж/б каб. лотках\t"
    "__PU_SOURCE_COORD__:КЖ:1\n"
    "1\tПитание ЗДЗ 10 кВ\tED-100\tКВВГЭнг(А)-LS\t4х2,5\t2\t+ED.1, -ED.1\t"
    "ОПУ, шкаф №146Р\tЗРУ 10 кВ. Шкаф питания ЗДЗ №1\t80\t51\t23\t"
    "__PU_SOURCE_COORD__:КЖ:2\n"
    "2\tПитание ЗДЗ 10 кВ\tED-101\tКВВГЭнг(А)-LS\t4х2,5\t2\t+ED.2\t"
    "ОПУ\tЗРУ\t81\t51\t\t"
    "__PU_SOURCE_COORD__:КЖ:3\n"
    "\tОбщий итог\t\t\t\t\t\t\t\t161\t\t\t"
    "__PU_SOURCE_COORD__:КЖ:4"
)


def test_rows_from_tsv_strips_source_coord():
    rows = rows_from_tsv(TSV_TEXT)
    assert rows, "должны получить строки"
    for row in rows:
        for cell in row:
            assert not cell.startswith("__PU_SOURCE_COORD__")
    # Первая строка начинается с текста «Сумма по полю …»
    assert rows[0][0].startswith("Сумма по полю")
    # Ведущая пустая ячейка во второй строке сохраняется
    assert rows[1][0] == ""


def test_parse_cable_journal_basic():
    rows = rows_from_tsv(TSV_TEXT)
    cables = parse_cable_journal(rows)
    assert len(cables) == 2

    c1 = cables[0]
    assert c1["cable_id"] == "ED-100"
    assert c1["brand"] == "КВВГЭнг(А)-LS"
    assert c1["cores"] == "4х2,5"
    assert c1["from"].startswith("ОПУ")
    assert c1["length"] == Decimal("80")
    assert c1["laying"] == {
        "В мет. каб. лотках (ОПУ)": Decimal("51"),
        "В ж/б каб. лотках": Decimal("23"),
    }

    c2 = cables[1]
    assert c2["cable_id"] == "ED-101"
    assert c2["length"] == Decimal("81")

    s = summary(cables)
    assert s["count"] == 2
    assert s["total_length"] == Decimal("161")


def test_two_row_header_with_design_and_laid():
    rows = [
        [
            "Обозначение кабеля, провода",
            "Трасса",
            "",
            "Кабель, провод",
            "",
            "",
            "",
            "",
            "",
        ],
        ["", "Начало", "Конец", "По проекту", "", "", "Проложен", "", ""],
        [
            "",
            "",
            "",
            "Марка",
            "Количество, число и сечение жил",
            "Длина, м",
            "Марка",
            "Количество, число и сечение жил",
            "Длина, м",
        ],
        [
            "РУ-0,4кВ А7 - ИБП-ЦК5 Осн",
            "РУ-0,4кВ А7",
            "ИБП-ЦК5 Осн",
            "КГВВнг(А)-LS",
            "10(1х185)",
            "50",
            "",
            "",
            "",
        ],
    ]

    header = find_header(rows)
    assert header is not None

    cables = parse_cable_journal(rows)
    assert len(cables) == 1
    cable = cables[0]
    assert cable["from"] == "РУ-0,4кВ А7"
    assert cable["to"] == "ИБП-ЦК5 Осн"
    assert cable["brand"] == "КГВВнг(А)-LS"
    assert cable["cores"] == "10(1х185)"
    assert cable["length"] == Decimal("50")
    assert cable["laid_length"] is None
