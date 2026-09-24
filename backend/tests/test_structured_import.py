from app.structured_import import parse_structured_rows


def test_schedule_csv_is_mapped_to_reviewable_source_rows():
    result = parse_structured_rows(
        "Этап;Дата начала;Дата окончания;Плановый процент\nМонтаж;01.09.2026;30.09.2026;100\n",
        "schedule",
    )
    assert result["issues"] == []
    assert result["rows"][0] == {
        "selection_id": 2,
        "source_row": 2, "source_sheet": None, "source_line": 2, "source_coordinate": "строка 2", "source_name": None,
        "title": "Монтаж", "category": "Прочее",
        "planned_start": "2026-09-01", "planned_finish": "2026-09-30",
        "planned_date": None, "amount": None, "counterparty": None, "object_name": None, "note": "Монтаж",
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


def test_dds_detail_columns_preserve_object_category_and_description():
    result = parse_structured_rows(
        "Дата;Объект;Статья;Тип операции;Сумма;Описание операции\n"
        "29.01.2026;Дубна;Оборудование;Расход;14811906,55;Аванс за производство щитов\n",
        "cash-flow",
    )

    assert result["rows"][0]["object_name"] == "Дубна"
    assert result["rows"][0]["category"] == "Оборудование"
    assert result["rows"][0]["title"] == "Аванс за производство щитов"
    assert result["rows"][0]["note"] == "Аванс за производство щитов"


def test_xlsx_coordinate_marker_preserves_real_sheet_and_row():
    content = (
        "Дата;Объект;Статья;Тип операции;Сумма;Описание операции;__PU_SOURCE_COORD__:Детализация:3\n"
        "46051;Дубна;СМР;Расход;2000000;Аванс подрядчику;__PU_SOURCE_COORD__:Детализация:17\n"
    )

    result = parse_structured_rows(content, "cash-flow", source_name="ДДС.xlsx")

    assert result["rows"][0]["source_row"] == 17
    assert result["rows"][0]["source_sheet"] == "Детализация"
    assert result["rows"][0]["source_coordinate"] == "Детализация!17"
    assert result["rows"][0]["source_name"] == "ДДС.xlsx"
    assert result["rows"][0]["category"] == "СМР"
    assert result["rows"][0]["title"] == "Аванс подрядчику"
    assert result["rows"][0]["note"] == "Аванс подрядчику"


def test_dds_finds_detail_header_after_summary_sheets_and_reads_excel_date_serial():
    prefix = "\n".join(f"Сводная строка {index};100" for index in range(30))
    content = prefix + "\nДата;Объект;Статья;Тип операции;Сумма;Описание операции\n46051;Дубна;СМР;Расход;2000000;Аванс подрядчику\n"

    result = parse_structured_rows(content, "cash-flow")

    assert result["issues"] == []
    assert result["rows"][0]["planned_date"] == "2026-01-29"
    assert result["rows"][0]["object_name"] == "Дубна"


def test_structured_import_routes_require_explicit_preview_and_import():
    from app.api.execution_finance import router

    paths = {route.path for route in router.routes}
    assert "/execution/documents/{document_id}/structured-preview" in paths
    assert "/execution/documents/{document_id}/structured-import" in paths


def test_monthly_dds_matrix_uses_month_end_and_infers_blank_december_column():
    content = (
        "\t\tянварь\tфевраль\tмарт\tапрель\tмай\tиюнь\tиюль\tавгуст\tсентябрь\tоктябрь\tноябрь\t\t__PU_SOURCE_COORD__:ДДС:1\n"
        "Платежи в DCI\t1000\t100\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t900\t__PU_SOURCE_COORD__:ДДС:2\n"
        "Этапы Дубна\t1000\t100\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t900\t__PU_SOURCE_COORD__:ДДС:3\n"
        "Городец, кнопка, материалы\t415000\t0\t415000\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t__PU_SOURCE_COORD__:ДДС:4\n"
        "пом.116 SCADA и прогрузка\t1154000\t0\t0\t1154000\t0\t0\t0\t0\t0\t0\t0\t0\t0\t__PU_SOURCE_COORD__:ДДС:11\n"
        "Материалы\t100\t0\t0\t0\t114\t0\t0\t0\t0\t0\t0\t0\t0\t__PU_SOURCE_COORD__:ДДС:12\n"
        "Расходы по месяцам (DCI)\t415000\t0\t415000\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t__PU_SOURCE_COORD__:ДДС:31\n"
        "сумма\t100\t5\t5\t5\t5\t5\t5\t5\t5\t5\t5\t5\t5\t__PU_SOURCE_COORD__:ДДС:35\n"
    )

    result = parse_structured_rows(content, "cash-flow", source_name="для PU ДДС.xlsx", plan_year=2026)

    assert result["layout"] == "monthly_matrix"
    assert result["plan_year"] == 2026
    assert result["inferred_december"] is True
    assert [(row["source_coordinate"], row["title"], row["planned_date"], row["amount"], row["direction"]) for row in result["rows"]] == [
        ("ДДС!C3", "Этапы Дубна", "2026-01-31", "100.00", "inflow"),
        ("ДДС!N3", "Этапы Дубна", "2026-12-31", "900.00", "inflow"),
        ("ДДС!D4", "Городец, кнопка, материалы", "2026-02-28", "415000.00", "outflow"),
        ("ДДС!E11", "пом.116 SCADA и прогрузка", "2026-03-31", "1154000.00", "outflow"),
        ("ДДС!F12", "Материалы", "2026-04-30", "114.00", "outflow"),
    ]
    assert result["issues"] == ["ДДС!12: годовой итог 100.00 не равен сумме месяцев 114.00"]
    assert len({row["selection_id"] for row in result["rows"]}) == 5
    assert not any(row["source_row"] >= 31 for row in result["rows"])
