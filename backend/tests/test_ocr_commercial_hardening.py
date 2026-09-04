from types import SimpleNamespace

from app.organizer_engine.content import (
    OcrToken, PageExtraction, _extract_fields, _extract_table_cells,
    _parse_tsv, _result_confidence, extract_text_result,
)


def corpus():
    """Generate twenty synthetic pages without personal or customer data."""
    templates = [
        ("Договор № ГК-08-194/25 от 29.01.2026. ООО «Генподряд», ИНН 0000000000 и ООО «Монтаж», ИНН 0000000001. Цена 1 250 000 рублей.", {"number": "ГК-08-194/25", "date": "29.01.2026", "parties": {"ооо «генподряд»", "ооо «монтаж»"}, "amount": "1 250 000"}),
        ("Счет № СЧ-1045 от 03.02.2026. ПАО «Поставщик», ИНН 0000000002; сумма 87 450,50 руб.", {"number": "СЧ-1045", "date": "03.02.2026", "parties": {"пао «поставщик»"}, "amount": "87 450,50"}),
        ("Акт № АКТ-77/Б от 18-03-2026. АО «Заказчик», ИНН 0000000003; ООО «Исполнитель», ИНН 0000000004. Итого 999 000 руб.", {"number": "АКТ-77/Б", "date": "18-03-2026", "parties": {"ао «заказчик»", "ооо «исполнитель»"}, "amount": "999 000"}),
        ("Контракт N К-2026/44 от 7/04/2026. ФКУ «Служба», ИНН 0000000005 и ИП Иванов, ИНН 0000000006. Стоимость 42 000 рублей.", {"number": "К-2026/44", "date": "7/04/2026", "parties": {"фку «служба»", "ип иванов"}, "amount": "42 000"}),
        ("Договор номер ПОСТ-55_26 от 11.05.2026. ООО \"Альфа\", ИНН 0000000007; ООО \"Бета\", ИНН 0000000008. 3 400 000,00 ₽.", {"number": "ПОСТ-55_26", "date": "11.05.2026", "parties": {"ооо \"альфа\"", "ооо \"бета\""}, "amount": "3 400 000,00"}),
    ]
    return [
        {"id": f"safe-{repeat}-{index}", "text": text, "expected": expected}
        for repeat in range(4) for index, (text, expected) in enumerate(templates)
    ]


def _values(fields, name):
    return {item.value.casefold().translate(str.maketrans("", "", "«»\"'")) for item in fields.get(name, [])}


def benchmark_metrics():
    counts = {name: {"tp": 0, "fp": 0, "fn": 0} for name in ("number", "date", "party", "amount")}
    completed = 0
    for fixture in corpus():
        page = PageExtraction(1, fixture["text"], .96, "ocr")
        fields = _extract_fields([page])
        _result_confidence([page], fields)
        completed += 1
        expected = fixture["expected"]
        mappings = {
            "number": {expected["number"].casefold()}, "date": {expected["date"].casefold()},
            "party": {value.translate(str.maketrans("", "", "«»\"'")) for value in expected["parties"]},
            "amount": {expected["amount"].casefold()},
        }
        for name, wanted in mappings.items():
            actual = _values(fields, name)
            counts[name]["tp"] += len(actual & wanted)
            counts[name]["fp"] += len(actual - wanted)
            counts[name]["fn"] += len(wanted - actual)
    return {
        "pages": len(corpus()),
        "success_rate": completed / len(corpus()),
        "fields": {
            name: {
                "precision": metric["tp"] / max(1, metric["tp"] + metric["fp"]),
                "recall": metric["tp"] / max(1, metric["tp"] + metric["fn"]),
            }
            for name, metric in counts.items()
        },
    }


def test_tesseract_tsv_preserves_coordinates_and_confidence():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t20\t80\t22\t92.5\tДоговор\n"
        "5\t1\t1\t1\t1\t2\t110\t20\t90\t22\t88.0\t№42\n"
    )
    tokens = _parse_tsv(tsv)
    assert tokens[0].bbox == (10, 20, 80, 22)
    assert tokens[0].confidence == 0.925
    assert tokens[1].line_id == (1, 1, 1)


def test_structured_fields_include_page_excerpt_and_area():
    tokens = [
        OcrToken("Договор", .94, (10, 10, 80, 20), (1, 1, 1)),
        OcrToken("ГК-42/26", .91, (120, 10, 100, 20), (1, 1, 1)),
        OcrToken("29.01.2026", .93, (240, 10, 100, 20), (1, 1, 1)),
    ]
    page = PageExtraction(1, "Договор № ГК-42/26 от 29.01.2026 сумма 125 000 руб.", .92, "ocr", 800, 1200, tokens)
    fields = _extract_fields([page])
    assert fields["number"][0].page == 1
    assert fields["number"][0].bbox is not None
    assert "29.01.2026" in fields["date"][0].excerpt


def test_table_foundation_keeps_row_column_and_cell_boxes():
    page = PageExtraction(1, "Этап Сумма", .9, "ocr", tokens=[
        OcrToken("Этап", .9, (10, 10, 60, 20), (1, 1, 1)),
        OcrToken("Сумма", .9, (300, 10, 80, 20), (1, 1, 1)),
    ])
    cells = _extract_table_cells([page])
    assert [(cell.row, cell.column, cell.text) for cell in cells] == [(1, 1, "Этап"), (1, 2, "Сумма")]
    assert all(cell.bbox[2] > 0 for cell in cells)


def test_low_confidence_ocr_requires_manual_review(monkeypatch):
    page = PageExtraction(1, "Акт № 42 от 01.02.2026", .4, "ocr")
    monkeypatch.setattr("app.organizer_engine.content._ocr_image_page", lambda *_: page)
    result = extract_text_result(b"safe-image", "image/png", "scan.png")
    assert result.needs_review is True
    assert result.confidence < .72
    assert result.metadata()["fields"]["date"][0]["page"] == 1


def test_safe_corpus_processes_every_page_and_reports_field_metrics():
    result = benchmark_metrics()
    assert result["success_rate"] >= .95
    for metric in result["fields"].values():
        assert metric["precision"] >= .8
        assert metric["recall"] >= .8


if __name__ == "__main__":
    import json
    print(json.dumps(benchmark_metrics(), ensure_ascii=False, indent=2))
