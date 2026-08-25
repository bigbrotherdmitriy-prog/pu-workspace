from app.api.tasks import TaskUpdate


def test_omitted_due_date_is_distinct_from_explicit_clear():
    status_only = TaskUpdate(status="in_progress")
    clear_due = TaskUpdate(due_date=None, due_change_reason="Срок отменён заказчиком")
    assert "due_date" not in status_only.model_fields_set
    assert "due_date" in clear_due.model_fields_set


def test_allowed_task_statuses():
    assert TaskUpdate(status="assigned").status == "assigned"
    assert TaskUpdate(status="in_progress").status == "in_progress"
    assert TaskUpdate(status="completed", result_note="Акт загружен").status == "completed"
