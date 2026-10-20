from __future__ import annotations

from decimal import Decimal

from app.specification import (
    equipment_list,
    find_spec_header,
    parse_specification,
    rows_from_layout_text,
)


ROWS = [
    [
        "Поз.",
        "Наименование и техническая характеристика",
        "Тип, марка, обозначение документа, опросного листа",
        "Код оборудования, изделия, материала",
        "Завод-изготовитель",
        "Единица измерения",
        "Кол.",
        "Масса единицы, кг",
        "Примечание",
    ],
    ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
    ["", "1. Оборудование", "", "", "", "", "", "", ""],
    ["1.1", "Модульный источник бесперебойного питания", "МОДУЛЬ Е СТМ600", "", "Импульс", "шт.", "6", "", ""],
    ["", "в составе: силовой шкаф", "", "", "", "", "", "", ""],
    ["1.2", "Щит ЩР-ИБП-ЦК1", "", "", "", "шт.", "6", "", ""],
    ["", "2. Кабели", "", "", "", "", "", "", ""],
    ["2.1", "Кабель", "КГВВнг(А)-LS 1х185", "", "", "м", "540", "", ""],
]


def test_find_header_and_parse():
    header = find_spec_header(ROWS)
    assert header is not None
    assert header[0] == 0

    items = parse_specification(ROWS)
    assert len(items) == 3

    first = items[0]
    assert first["pos"] == "1.1"
    assert first["name"] == "Модульный источник бесперебойного питания в составе: силовой шкаф"
    assert first["type"] == "МОДУЛЬ Е СТМ600"
    assert first["maker"] == "Импульс"
    assert first["unit"] == "шт."
    assert first["qty"] == Decimal("6")
    assert first["section"] == "1. Оборудование"

    second = items[1]
    assert second["pos"] == "1.2"
    assert second["name"] == "Щит ЩР-ИБП-ЦК1"
    assert second["section"] == "1. Оборудование"

    third = items[2]
    assert third["pos"] == "2.1"
    assert third["section"] == "2. Кабели"
    assert third["qty"] == Decimal("540")
    assert third["unit"] == "м"


def test_equipment_list_filters_cables():
    items = parse_specification(ROWS)
    equip = equipment_list(items)
    assert len(equip) == 2
    assert equip[0]["name"] == "Модульный источник бесперебойного питания в составе: силовой шкаф"
    assert equip[0]["type"] == "МОДУЛЬ Е СТМ600"
    assert equip[0]["maker"] == "Импульс"
    assert equip[0]["qty"] == Decimal("6")
    assert equip[0]["unit"] == "шт."
    assert equip[0]["pos"] == "1.1"

    assert equip[1]["pos"] == "1.2"


def test_rows_from_layout_text():
    text = "1.2    Щит ЩР-ИБП-ЦК1     шт.    6"
    assert rows_from_layout_text(text) == [["1.2", "Щит ЩР-ИБП-ЦК1", "шт.", "6"]]
