from app.api.management import MeetingCreate, MeetingUpdate, ObligationUpdate, router


def test_mvp3_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/management/obligations" in paths
    assert "/management/meetings" in paths
    assert "/management/notifications/refresh" in paths
    assert "/management/notifications/{notification_id}/read" in paths


def test_meeting_and_obligation_contracts():
    meeting = MeetingCreate(project_id=1, title="Планёрка", agenda="Проверить сроки")
    assert meeting.contract_id is None
    assert MeetingUpdate(minutes="Подрядчик должен направить акт до 28 августа.").status == "completed"
    assert ObligationUpdate(status="confirmed").result_note is None
