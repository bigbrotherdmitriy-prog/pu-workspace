from app.task_engine import extract_task_candidates


def test_extracts_obligation_with_deadline():
    tasks = extract_task_candidates("Подрядчик обязан предоставить акт не позднее 15.09.2026.")
    assert len(tasks) == 1
    assert tasks[0].due_date.isoformat() == "2026-09-15"
    assert tasks[0].priority == "high"
    assert tasks[0].confidence == 0.90


def test_ignores_plain_descriptive_text():
    assert extract_task_candidates("Настоящий договор состоит из десяти страниц.") == []


def test_extracts_russian_month_deadline_in_current_year():
    tasks = extract_task_candidates("Просьба направить исправленный акт до 28 августа и подтвердить оплату.")
    assert len(tasks) == 1
    assert tasks[0].due_date.month == 8
    assert tasks[0].due_date.day == 28


def test_limits_candidates_per_file():
    text = " ".join(f"Исполнитель должен подготовить документ номер {i}." for i in range(20))
    assert len(extract_task_candidates(text)) == 5
