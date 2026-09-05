from datetime import date, time

import pytest

from app.api.management import (
    DigestEnqueueRequest,
    EvidenceProposalConfirm,
    EvidenceProposalCreate,
    MeetingCreate,
    MeetingUpdate,
    ObligationUpdate,
    router,
)


def test_mvp3_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/management/obligations" in paths
    assert "/management/meetings" in paths
    assert "/management/notifications/refresh" in paths
    assert "/management/notifications/{notification_id}/read" in paths
    assert "/management/v2/meetings/{meeting_id}/proposals" in paths
    assert "/management/v2/messages/{message_id}/proposals" in paths
    assert "/management/v2/proposals/{entity_type}/{entity_id}/confirm" in paths
    assert "/management/v2/digests" in paths


def test_meeting_and_obligation_contracts():
    meeting = MeetingCreate(project_id=1, title="Планёрка", agenda="Проверить сроки")
    assert meeting.contract_id is None
    assert MeetingUpdate(minutes="Подрядчик должен направить акт до 28 августа.").status == "completed"
    assert ObligationUpdate(status="confirmed").result_note is None


def test_evidence_proposal_and_digest_contracts_are_fail_closed():
    proposal = EvidenceProposalCreate(project_id=4, candidates=[{
        "kind": "task", "title": "Передать акт", "owner_user_id": 2,
        "evidence_pins": [{"ref": {"kind": "evidence", "id": {"value": "00000000-0000-0000-0000-000000000001"},
                                      "tenant": {"value": "1"}}}],
        "due_date": date(2026, 9, 12),
    }])
    assert proposal.candidates[0].kind == "task"
    assert EvidenceProposalConfirm(project_id=4, expected_version=1).create_internal_task is False
    digest = DigestEnqueueRequest(
        project_id=4, timezone="Europe/Moscow", quiet_start=time(20), quiet_end=time(8),
        channel="in_app", local_date=date(2026, 9, 5),
    )
    assert digest.channel == "in_app"
    with pytest.raises(ValueError):
        DigestEnqueueRequest(
            project_id=4, timezone="Europe/Moscow", quiet_start=time(20), quiet_end=time(8),
            channel="email", local_date=date(2026, 9, 5),
        )
