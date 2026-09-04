from app.structured_import import parse_structured_rows


def test_schedule_csv_is_mapped_to_reviewable_source_rows():
    result = parse_structured_rows(
        "Этап;Дата начала;Дата окончания;Плановый процент\nМонтаж;01.09.2026;30.09.2026;100\n",
        "schedule",
    )
    assert result["issues"] == []
    assert result["rows"][0] == {
        "source_row": 2, "title": "Монтаж", "category": "Прочее",
        "planned_start": "2026-09-01", "planned_finish": "2026-09-30",
        "planned_date": None, "amount": None, "counterparty": None,
        "direction": None, "progress": 100.0, "issues": [], "importable": True,
        "excerpt": "Монтаж | 01.09.2026 | 30.09.2026 | 100",
    }


def test_budget_and_cash_flow_require_amount_and_date():
    budget = parse_structured_rows("Статья;Описание;Сумма\nМатериалы;Кабель;125 400,50\n", "budget")
    cash = parse_structured_rows("Назначение платежа;Дата платежа;Сумма;Операция\nАванс;05.09.2026;50000;Расход\n", "cash-flow")
    invalid = parse_structured_rows("Описание;Сумма\nПоставка;неизвестно\n", "budget")

    assert budget["rows"][0]["importable"] is True
    assert budget["rows"][0]["amount"] == "125400.50"
    assert cash["rows"][0]["planned_date"] == "2026-09-05"
    assert cash["rows"][0]["direction"] == "outflow"
    assert invalid["rows"][0]["importable"] is False


def test_structured_import_routes_require_explicit_preview_and_import():
    from app.api.execution_finance import router

    paths = {route.path for route in router.routes}
    assert "/execution/documents/{document_id}/structured-preview" in paths
    assert "/execution/documents/{document_id}/structured-import" in paths
