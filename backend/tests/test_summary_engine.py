from app.summary_engine import brief_summary, normalize_document_text


def test_summary_contains_grounded_points_dates_and_counts():
    text = "Просим предоставить акт не позднее 30.08.2026. Стоимость работ составляет 100 рублей. Справочная фраза без поручения."
    summary = brief_summary(text, "Запрос.docx", tasks=1, drafts=1, calendar_events=1)
    assert "Запрос.docx" in summary
    assert "30.08.2026" in summary
    assert "Задач: 1" in summary
    assert "Calendar: 1" in summary
    assert "Черновиков ответов: 1" in summary


def test_act_summary_is_structured_and_repairs_spaced_numbers():
    text = (
        "Акт сдачи-приемки выполненных работ № 1 3 по Государственному контракту "
        "№ ГК-08-194/25 от 2 9 января 2 0 2 6 года. "
        "Общая стоимость работ 125 000 рублей, в том числе НДС 2 2 %."
    )
    summary = brief_summary(text, "Акт.docx", tasks=0, drafts=0, calendar_events=0)
    assert "Документ:" in summary
    assert "Основание:" in summary
    assert "Сумма:" in summary
    assert "2026" in summary
    assert "22%" in summary
    assert "Явных поручений" in summary


def test_spaced_number_normalization_does_not_join_words():
    assert normalize_document_text("Москва 2 0 2 6 г. Акт выполнен") == "Москва 2026 г. Акт выполнен"
