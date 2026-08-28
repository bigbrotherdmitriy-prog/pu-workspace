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


def test_completion_evidence_is_optional_and_explicitly_clearable():
    without_evidence = TaskUpdate(status="completed", result_note="Работа принята")
    with_evidence = TaskUpdate(status="completed", result_note="Работа принята", completion_document_id=42)
    clear_evidence = TaskUpdate(completion_document_id=None)
    assert "completion_document_id" not in without_evidence.model_fields_set
    assert with_evidence.completion_document_id == 42
    assert "completion_document_id" in clear_evidence.model_fields_set
