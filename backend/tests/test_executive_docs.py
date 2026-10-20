import pytest

from app import executive_docs as ed


def test_classify_passport():
    r = ed.classify_file("1. ЭМ/4.Паспорта ИБП", "Модуль Е 800 60022.pdf")
    assert r["kind"] == "passport"
    assert r["section"] == "ЭМ"


def test_classify_certificate_name():
    assert ed.classify_file("Сертификаты", "NE09_W00113.25.pdf")["kind"] == "certificate"
    assert ed.classify_file("", "RU.31621.04.OS05.C00290.pdf")["kind"] == "certificate"


def test_classify_service():
    r = ed.classify_file("Проект", "Планы трасс.bak")
    assert r["kind"] == "service"
    assert "service_file" in r["issues"]


def test_classify_act_and_protocol():
    assert ed.classify_file("Акты", "Акт ввода ИБА А1.1.docx")["kind"] == "act"
    assert ed.classify_file("", "Протокол ИБП К10.pdf")["kind"] == "protocol"


def test_classify_drawing_source_section():
    r = ed.classify_file("СКТП", "07_Аксонометрии.dwg")
    assert r["kind"] == "drawing_source"
    assert r["section"] == "ОВиК"


def test_classify_draft_and_toir():
    r = ed.classify_file("", "Реестр Общий 2026 НЕ ГОТОВ.xlsx")
    assert r["kind"] == "register"
    assert "draft_name" in r["issues"]
    assert ed.classify_file("", "Для ТОИР 2026.xlsx")["kind"] == "toir"


def test_classify_arm_not_ar():
    assert ed.classify_file("", "АРМ оператора.pdf")["section"] != "АР"


def test_draft_issues():
    result = set(ed.draft_issues("Дата «__» ________ 20__ ____ ____ ____ ____", None, "a.docx"))
    assert result == {"editable_format_not_signed", "blank_fields"}


def test_duplicate_names():
    paths = [
        "Акты/Акт № 9 Городец (6-этап).docx",
        "Акты/Акт_№9_Городец_6-этап.docx",
        "Акты/Акт 10.docx",
    ]
    groups = ed.duplicate_names(paths)
    assert len(groups) == 1
    name, group_paths = groups[0]
    assert len(group_paths) == 2
    assert set(group_paths) == {paths[0], paths[1]}


def test_parse_id_requirements():
    text = (
        "Договор ... Перечень исполнительной документации "
        "Журнал производства монтажных и пусконаладочных работ; "
        "Акты освидетельствования скрытых работ – 3 экземпляра (при необходимости); "
        "Исполнительные схемы на бумажном и электронном носителях – 3 экземпляра (при необходимости); "
        "Паспорта на изделия и оборудование, заверенные подписью и печатью, сертификаты; "
        "Ведомость смонтированных приборов и оборудования; "
        "Технический отчет испытаний электроустановки; "
        "Гарантийные обязательства Гарантийный срок 24 месяца"
    )
    items = ed.parse_id_requirements(text)
    assert len(items) == 6
    assert items[1]["copies"] == 3
    assert items[1]["required_if_applicable"] is True
    assert items[1]["doc_kind"] == "aosr"
    assert items[5]["doc_kind"] == "report"
    assert all("Гарант" not in it["title"] for it in items)


def test_build_requirements_norm_fallback(monkeypatch):
    monkeypatch.setattr(ed, "extract_id_requirements_ai", lambda text: None)
    items = ed.build_requirements("Договор без перечня", ["ЭМ"])
    assert any(it["doc_kind"] == "report" and it["source"] == "norm" for it in items)
    assert any(it["doc_kind"] == "journal" for it in items)


def test_serial_numbers_and_designations_are_not_duplicates():
    from app.executive_docs import duplicate_names

    paths = [
        "Паспорта/Модуль Е 800 70022.pdf", "Паспорта/Модуль Е 800 70012.pdf",
        "Акты/Акт ввода А1.1.docx", "Акты/Акт ввода А1.2.docx",
        "Акты/Протокол К10.pdf", "Акты/Протокол К10 (1).pdf",
    ]
    assert duplicate_names(paths) == [("протокол к10", ["Акты/Протокол К10.pdf", "Акты/Протокол К10 (1).pdf"])]


def test_statement_photo_and_executive_scheme_kinds():
    from app.executive_docs import classify_file

    assert classify_file("", "Ведомость смонтированных приборов и оборудования")["kind"] == "statement"
    assert classify_file("", "Материалы фотофиксации до и после")["kind"] == "photo"
    assert classify_file("", "Исполнительные схемы (принципиальные схемы щитов, кабельные журналы)")["kind"] == "scheme"
    assert classify_file("СКТП", "02_Титульный_лист.docx")["kind"] == "cover"


def test_normative_items_are_recommended_when_tz_has_its_own_list(monkeypatch):
    from app import executive_docs

    monkeypatch.setattr(executive_docs, "extract_id_requirements_ai", lambda _text: None)
    text = "Перечень исполнительной документации Журнал производства работ; Технический отчет испытаний; Гарантийные обязательства"
    items = executive_docs.build_requirements(text, ["ЭМ"])
    assert all(i["required_if_applicable"] for i in items if i["source"] == "norm" and i["doc_kind"] != "order")
    assert all(not i["required_if_applicable"] for i in items if i["source"] == "tz")


def test_classify_order():
    assert ed.classify_file("Общее", "Приказ о назначении ответственного ООО Ромашка.pdf")["kind"] == "order"


def test_build_requirements_always_required_orders(monkeypatch):
    from app import executive_docs

    monkeypatch.setattr(executive_docs, "extract_id_requirements_ai", lambda _text: None)
    text = "Перечень исполнительной документации Журнал производства работ; Технический отчет испытаний; Гарантийные обязательства"
    items = executive_docs.build_requirements(text, ["ЭМ"])
    orders = [i for i in items if i["doc_kind"] == "order"]
    assert len(orders) == 2
    assert all(i["required_if_applicable"] is False for i in orders)
