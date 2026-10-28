from __future__ import annotations

from decimal import Decimal

from app.specification import (
    equipment_list,
    extract_specification_ai,
    find_spec_header,
    parse_specification,
    rows_from_layout_text,
    specification_from_text,
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


AI_TEXT = (
    "1.1 Модульный источник бесперебойного питания МОДУЛЬ Е СТМ600 Импульс шт. 6\n"
    "1.2 Щит ЩР-ИБП-ЦК1 шт. 6"
)


class _FakeGemini:
    def __init__(self, response):
        self._response = response
        self.calls: list[tuple] = []

    def deepseek_configured(self) -> bool:
        return True

    def _try_deepseek(self, system, user, schema):
        self.calls.append((system, user, schema))
        return self._response


def test_extract_ai_accepts_valid_and_drops_hallucinated(monkeypatch):
    fake = _FakeGemini(
        {
            "items": [
                {
                    "pos": "1.1",
                    "name": "Модульный источник бесперебойного питания",
                    "type": "МОДУЛЬ Е СТМ600",
                    "maker": "Импульс",
                    "unit": "шт.",
                    "qty": 6,
                    "section": None,
                    "evidence": "1.1 Модульный источник бесперебойного питания МОДУЛЬ Е СТМ600 Импульс шт. 6",
                },
                {
                    "pos": None,
                    "name": "Трансформатор 1000 кВА",
                    "type": None,
                    "maker": None,
                    "unit": "шт.",
                    "qty": 1,
                    "section": None,
                    "evidence": "Трансформатор 1000 кВА шт. 1",
                },
                {
                    "pos": "1.2",
                    "name": "Щит ЩР-ИБП-ЦК1",
                    "type": None,
                    "maker": None,
                    "unit": "шт.",
                    "qty": 6,
                    "section": None,
                    "evidence": "1.2 Щит ЩР-ИБП-ЦК1 шт. 6",
                },
            ]
        }
    )
    monkeypatch.setattr("app.specification.gemini_analysis", fake)

    items = extract_specification_ai(AI_TEXT)
    assert items is not None
    assert len(items) == 2
    names = [it["name"] for it in items]
    assert "Трансформатор 1000 кВА" not in names
    assert items[0]["qty"] == Decimal("6")
    assert items[0]["pos"] == "1.1"

    assert len(fake.calls) == 1


def test_extract_ai_rejects_qty_mismatch(monkeypatch):
    fake = _FakeGemini(
        {
            "items": [
                {
                    "pos": "1.1",
                    "name": "Модульный источник бесперебойного питания",
                    "type": "МОДУЛЬ Е СТМ600",
                    "maker": "Импульс",
                    "unit": "шт.",
                    "qty": 7,
                    "section": None,
                    "evidence": "1.1 Модульный источник бесперебойного питания МОДУЛЬ Е СТМ600 Импульс шт. 6",
                },
                {
                    "pos": "1.2",
                    "name": "Щит ЩР-ИБП-ЦК1",
                    "type": None,
                    "maker": None,
                    "unit": "шт.",
                    "qty": 6,
                    "section": None,
                    "evidence": "1.2 Щит ЩР-ИБП-ЦК1 шт. 6",
                },
            ]
        }
    )
    monkeypatch.setattr("app.specification.gemini_analysis", fake)

    items = extract_specification_ai(AI_TEXT)
    assert items is not None
    assert len(items) == 1
    assert items[0]["pos"] == "1.2"


def test_specification_from_text_falls_back_to_layout(monkeypatch):
    class _NotConfigured:
        def deepseek_configured(self) -> bool:
            return False

        def _try_deepseek(self, system, user, schema):  # pragma: no cover
            raise AssertionError("must not be called")

    monkeypatch.setattr("app.specification.gemini_analysis", _NotConfigured())

    text = (
        "Поз.    Наименование и техническая характеристика    Единица измерения    Кол.\n"
        "1.1    Щит ЩР-ИБП-ЦК1    шт.    6"
    )
    items, method = specification_from_text(text)
    assert method == "layout"
    assert len(items) == 1
    assert items[0]["name"] == "Щит ЩР-ИБП-ЦК1"
    assert items[0]["qty"] == Decimal("6")


def test_accept_ai_item_allows_ellipsis_in_evidence():
    from app.specification import _accept_ai_item, _ai_norm

    chunk = "1.1\nМодульный источник питания Импульс в составе:\n- Силовой шкаф 1шт.\n- Силовой блок 6шт.\nИмпульс шт. 6\n1.2 Щит ЩР 1 шт."
    item = {
        "pos": "1.1",
        "name": "Модульный источник питания Импульс",
        "unit": "шт.",
        "qty": 6,
        "evidence": "1.1 Модульный источник питания Импульс в составе: ... Импульс шт. 6",
    }
    assert _accept_ai_item(item, _ai_norm(chunk)) is not None
    item["evidence"] = "1.1 Выдуманная позиция ... Импульс шт. 6"
    assert _accept_ai_item(item, _ai_norm(chunk)) is None
