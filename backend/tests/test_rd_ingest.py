import io

import pytest
from pypdf import PdfWriter

from app.rd_ingest import (
    find_spec_pages,
    spec_pages_label,
    spec_text,
    split_pages,
)


def test_find_spec_pages_basic_block():
    pages = [
        "Титульный лист",
        "Содержание тома. Прилагаемые документы: спецификация оборудования, изделий и материалов",
        "Спецификация оборудования, изделий и материалов\nПоз. Наименование\n1.1 Щит управления",
        "1.1 Щит управления\n1.2 Шкаф\n2.1 Кабель",
        "1.3 Клеммник\n1.4 Реле\n1.5 Лампа",
        "Кабельный журнал\n1.1 Кабель ВВГ",
    ]
    assert find_spec_pages(pages) == [3, 4, 5]


def test_find_spec_pages_one_empty_page_does_not_break():
    pages = [
        "Спецификация оборудования, изделий и материалов\nПоз. Наименование",
        "1.1 Щит\n1.2 Шкаф\n2.1 Кабель",
        "",
        "3.1 Клеммник\n3.2 Реле\n3.3 Лампа",
        "Кабельный журнал",
    ]
    assert find_spec_pages(pages) == [1, 2, 4]


def test_find_spec_pages_two_empty_pages_break():
    pages = [
        "Спецификация оборудования, изделий и материалов\nПоз. Наименование",
        "1.1 Щит\n1.2 Шкаф\n2.1 Кабель",
        "",
        "",
        "3.1 Клеммник\n3.2 Реле\n3.3 Лампа",
    ]
    assert find_spec_pages(pages) == [1, 2]


def test_find_spec_pages_none():
    pages = [
        "Титул",
        "Содержание: прилагаемые документы",
        "Кабельный журнал\n1.1 Кабель",
    ]
    assert find_spec_pages(pages) == []


def test_spec_pages_label():
    assert spec_pages_label([54, 55, 56, 57, 58, 70, 71]) == "54-58,70-71"
    assert spec_pages_label([]) == ""
    assert spec_pages_label([5]) == "5"


def test_spec_text():
    pages = ["a", "b", "c", "d"]
    assert spec_text(pages, [2, 4]) == "b\nd"


def test_split_pages_not_pdf():
    with pytest.raises(ValueError):
        split_pages(b"not a pdf")


def test_split_pages_minimal_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    pages = split_pages(buf.getvalue())
    assert isinstance(pages, list)
    assert len(pages) == 1


def test_find_spec_pages_table_without_title_phrase():
    # Лист «Общие данные» (АР): таблица спецификации без заголовка «Спецификация оборудования, изделий и материалов».
    table = (
        "Поз. Наименование и техническая характеристика Тип, марка, обозначение документа, опросного листа "
        "Код оборудования, изделия, материала Завод-изготовитель Единица измерения Кол. Масса единицы, кг Примечание\n"
        "1.1 Дверь противопожарная двухстворчатая с окном ДП-2 (2400х1500) EI60 НПО Пульс шт. 2\n"
    )
    pages = ["Титульный лист", table, "Ведомость"]
    assert find_spec_pages(pages) == [2]


def test_find_spec_pages_plain_header_without_title_is_not_a_spec():
    pages = ["Поз. Наименование Количество\n1 Что-то 2"]
    assert find_spec_pages(pages) == []
