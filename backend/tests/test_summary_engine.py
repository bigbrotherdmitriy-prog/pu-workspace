from app.summary_engine import brief_summary


def test_summary_contains_grounded_points_dates_and_counts():
    text = "Просим предоставить акт не позднее 30.08.2026. Стоимость работ составляет 100 рублей. Справочная фраза без поручения."
    summary = brief_summary(text, "Запрос.docx", tasks=1, drafts=1, calendar_events=1)
    assert "Запрос.docx" in summary
    assert "30.08.2026" in summary
    assert "Задач: 1" in summary
    assert "Calendar: 1" in summary
    assert "Черновиков ответов: 1" in summary
