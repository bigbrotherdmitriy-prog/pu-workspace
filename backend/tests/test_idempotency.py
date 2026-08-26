from types import SimpleNamespace

from app.organizer_engine.executor import OrganizerExecutor
from app.organizer_engine.repository import OrganizerRepository


class AppliedRepo:
    def proposal(self, _proposal_id):
        return {"status": "applied"}


def test_repeated_apply_is_a_successful_noop():
    result = OrganizerExecutor(AppliedRepo(), drive=None).apply(7)
    assert result == {"renamed": 0, "moved": 0, "skipped": 0, "errors": 0, "already_applied": 1}


class CaptureDb:
    def __init__(self):
        self.params = None

    def execute(self, _statement, params):
        self.params = params
        return SimpleNamespace(scalar_one=lambda: 12)


def test_operation_idempotency_key_is_stable():
    first = CaptureDb()
    second = CaptureDb()
    OrganizerRepository(first).log_operation(3, 4, "file-5", "move", {"parent": "a"}, {"parent": "b"})
    OrganizerRepository(second).log_operation(3, 4, "file-5", "move", {"parent": "a"}, {"parent": "b"})
    assert first.params["idempotency_key"] == second.params["idempotency_key"]
    assert len(first.params["idempotency_key"]) == 64
