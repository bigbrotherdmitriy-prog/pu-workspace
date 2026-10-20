from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

from app.acceptance_act_document import (
    ActData,
    ActParty,
    act_text_lines,
    render_act_docx,
    rubles_in_words,
    stage_quantity,
    vat_from_gross,
)


def test_rubles_in_words_main():
    assert rubles_in_words(Decimal("23446972.13")) == (
        "Двадцать три миллиона четыреста сорок шесть тысяч "
        "девятьсот семьдесят два рубля 13 копеек"
    )


def test_rubles_in_words_variants():
    assert rubles_in_words(Decimal("1001.01")) == "Одна тысяча один рубль 01 копейка"
    assert rubles_in_words(Decimal("2000000.00")) == "Два миллиона рублей 00 копеек"
    assert rubles_in_words(Decimal("11.11")) == "Одиннадцать рублей 11 копеек"
    assert rubles_in_words(Decimal("21.22")) == "Двадцать один рубль 22 копейки"
    assert rubles_in_words(Decimal("0")) == "Ноль рублей 00 копеек"


def test_vat_from_gross():
    assert vat_from_gross(Decimal("22000000.00"), Decimal("22")) == Decimal("3967213.11")
    assert vat_from_gross(Decimal("100"), None) is None


def test_stage_quantity():
    assert stage_quantity(Decimal("23446972.13"), Decimal("261692220.86")) == Decimal("0.0895975")


def _sample_data() -> ActData:
    return ActData(
        number="42",
        act_date=date(2026, 2, 17),
        city="Москва",
        contract_number="123/2026",
        contract_date=date(2026, 2, 17),
        customer=ActParty(
            name="ООО «Заказчик»",
            role="Заказчик",
            signer_position="Генерального директора",
            signer_name="Иванова И.И.",
        ),
        contractor=ActParty(
            name="ООО «Подрядчик»",
            role="Подрядчик",
            signer_position="Директора",
            signer_name="Петрова П.П.",
        ),
        object_name="Объект",
        stage_number=2,
        stage_title="Работы по этапу",
        okpd2="41.20.40",
        unit="усл. ед.",
        quantity=Decimal("0.0895975"),
        unit_price=Decimal("261700000.00"),
        amount=Decimal("23446972.13"),
        vat_rate=Decimal("22"),
    )


def test_act_text_lines_structure():
    data = _sample_data()
    lines = act_text_lines(data)
    assert lines["title"] == "Акт сдачи-приемки выполненных работ № 42"
    assert "Договору № 123/2026" in lines["subtitle"]
    assert len(lines["table_header"]) == 6
    assert lines["table_row"][3] == "0,0895975"
    assert "23 446 972,13" in lines["total"]


def test_render_act_docx():
    data = _sample_data()
    blob = render_act_docx(data)

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        assert "word/document.xml" in names
        xml = zf.read("word/document.xml")

    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    texts = [t.text or "" for t in root.iter(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    )]
    joined = " ".join(texts)
    assert "42" in joined
    assert "23 446 972,13" in joined
    assert "Двадцать три миллиона четыреста сорок шесть тысяч" in joined
