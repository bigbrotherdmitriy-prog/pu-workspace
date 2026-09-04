import pytest
from pydantic import ValidationError

from app.api.responses import DraftUpdate, router


def test_response_draft_update_route_is_registered():
    paths = {route.path for route in router.routes}
    assert "/response-drafts/{draft_id}" in paths


def test_response_draft_status_is_restricted():
    with pytest.raises(ValidationError):
        DraftUpdate(status="sent")


def test_response_draft_accepts_reviewed_body():
    payload = DraftUpdate(status="approved", body="Подтверждённый текст ответа")
    assert payload.status == "approved"
