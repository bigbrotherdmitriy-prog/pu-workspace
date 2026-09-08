"""Do not acknowledge unsupported planning intent as a successful legacy item."""

from datetime import date

import pytest
from pydantic import ValidationError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.execution_finance import ScheduleItemCreate, get_db, require_user, router


@pytest.mark.parametrize("unsupported,value", [
    ("duration_days", 3), ("is_milestone", True),
    ("predecessor_ids", "12FS+2d"), ("dependencies", [{"predecessor_id": 12}]),
    ("constraint_type", "mso"), ("constraint_date", "2026-09-12"),
    ("parent_id", 12), ("sort_order", 3),
    ("not_before_date", "2026-09-12"), ("expected_graph_revision", 2),
    ("project_id", 99), ("actual_progress", 100),
])
def test_unsupported_schedule_create_fields_are_not_silently_dropped(unsupported, value):
    with pytest.raises(ValidationError) as caught:
        ScheduleItemCreate.model_validate({
            "baseline_id": 1, "expected_baseline_version": 1,
            "title": "Synthetic stage", unsupported: value,
        })
    errors = caught.value.errors(include_input=False, include_url=False)
    assert [(error["loc"], error["type"]) for error in errors] == [
        ((unsupported,), "extra_forbidden"),
    ]


def test_current_browser_schedule_create_payload_is_unchanged():
    payload = {
        "baseline_id": 1, "expected_baseline_version": 1,
        "title": "Synthetic stage", "planned_finish": "2026-09-12",
        "planned_progress": 20,
    }
    parsed = ScheduleItemCreate.model_validate(payload)
    assert parsed.model_dump() == {
        **payload, "planned_finish": date(2026, 9, 12), "planned_start": None,
    }


def test_existing_optional_dates_and_legacy_version_contract_remain_unchanged():
    parsed = ScheduleItemCreate(baseline_id=1, title="Synthetic stage")
    assert parsed.expected_baseline_version is None
    assert parsed.planned_start is None and parsed.planned_finish is None
    assert parsed.planned_progress == 0


def test_http_boundary_rejects_unsupported_intent_before_endpoint_database_access():
    application = FastAPI()
    application.include_router(router)
    # Isolated request-validation test, not production auth acceptance. Any use
    # of either object by the endpoint fails; validation must reject first.
    application.dependency_overrides[get_db] = lambda: object()
    application.dependency_overrides[require_user] = lambda: object()
    with TestClient(application) as client:
        response = client.post("/execution/schedule-items", json={
            "baseline_id": 1, "expected_baseline_version": 1,
            "title": "Synthetic stage", "predecessor_ids": "12FS",
        })
    assert response.status_code == 422
    assert [(item["loc"], item["type"]) for item in response.json()["detail"]] == [
        (["body", "predecessor_ids"], "extra_forbidden"),
    ]


def test_openapi_declares_closed_schedule_create_contract():
    assert ScheduleItemCreate.model_json_schema()["additionalProperties"] is False
