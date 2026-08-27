from types import SimpleNamespace

from app.organizer_engine.executor import OrganizerExecutor
from app.organizer_engine.repository import OrganizerRepository
from app.organizer_engine.types import DriveFile


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


class SourceRepo:
    def __init__(self, decision="approved", special_case=None, confidence=0.95, status="waiting_confirmation"):
        self.db = SimpleNamespace(commit=lambda: None)
        self._proposal = {
            "id": 9,
            "status": status,
            "copy_folder_id": "virtual:15",
            "source_folder_id": "root",
            "session_id": 4,
        }
        self._item = {
            "id": 22,
            "file_id": "file-5",
            "source": "old.pdf",
            "current_parent_id": "parent",
            "source_modified_at": "2026-08-26T10:00:00Z",
            "source_checksum": "abc",
            "proposed_name": "new.pdf",
            "edited_name": None,
            "user_decision": decision,
            "special_case": special_case,
            "confidence": confidence,
        }
        self.logged = []
        self.marked = False

    def proposal(self, _proposal_id): return self._proposal
    def proposal_items(self, _proposal_id): return [self._item]
    def operations(self, _proposal_id): return []
    def log_operation(self, *args): self.logged.append(args)
    def mark_source_applied(self, _proposal_id): self.marked = True
    def mark_source_conflicts(self, *_args): raise AssertionError("unexpected conflict")


class SourceDrive:
    def __init__(self): self.renamed = []
    def get_file_meta(self, _file_id): return DriveFile("file-5", "old.pdf", "application/pdf", "parent", "abc", modified_time="2026-08-26T10:00:00Z")
    def list_children(self, _parent_id): return [self.get_file_meta("file-5")]
    def assert_inside_copy(self, file_id, root_id): assert (file_id, root_id) == ("file-5", "root")
    def rename_file(self, file_id, target, root_id): self.renamed.append((file_id, target, root_id))


def test_apply_one_to_source_is_explicit_and_idempotent():
    repo = SourceRepo()
    drive = SourceDrive()
    result = OrganizerExecutor(repo, drive).apply_one_to_source(9, 22)
    assert result == {"renamed": 1, "already_applied": 0}
    assert drive.renamed == [("file-5", "new.pdf", "root")]
    assert repo.logged[0][4] == {"name": "old.pdf", "parent_id": "parent"}
    assert repo.marked is True


def test_special_case_cannot_touch_source_without_manual_edit():
    repo = SourceRepo(special_case="ambiguous", confidence=0.3)
    drive = SourceDrive()
    try:
        OrganizerExecutor(repo, drive).apply_one_to_source(9, 22)
    except ValueError as exc:
        assert "explicitly edited" in str(exc)
    else:
        raise AssertionError("special-case source apply should be blocked")
    assert drive.renamed == []
