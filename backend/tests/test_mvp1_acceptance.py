from types import SimpleNamespace

from app.organizer_engine.executor import OrganizerExecutor
from app.organizer_engine.types import DriveFile


class AcceptanceRepo:
    def __init__(self):
        self.db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
        self.status = "ready_to_apply_to_copy"
        self.rolled_back = set()
        self.ops = []

    def proposal(self, _):
        return {"id": 1, "status": self.status, "copy_folder_id": "copy", "source_folder_id": "source",
                "session_id": 1, "originals_modified": False}

    def proposal_items(self, _):
        return [{"id": 1, "file_id": "file", "source": "old.pdf", "current_parent_id": "copy",
                 "source_modified_at": "2026-08-28T10:00:00Z", "source_checksum": "abc",
                 "proposed_name": "new.pdf", "edited_name": None, "target_folder": "03_ФИНАНСЫ И СМЕТЫ",
                 "edited_folder": None, "user_decision": "approved", "special_case": None, "confidence": 0.95}]

    def log_operation(self, proposal_id, session_id, file_id, op_type, before, after):
        self.ops.append({"id": len(self.ops) + 1, "proposal_id": proposal_id, "file_id": file_id, "op_type": op_type,
                         "before_json": before, "after_json": after, "rolled_back_at": None})

    def operations(self, *_args): return list(reversed(self.ops))
    def mark_applied(self, _): self.status = "applied"
    def mark_rolled_back(self, op_id):
        self.rolled_back.add(op_id)
        next(op for op in self.ops if op["id"] == op_id)["rolled_back_at"] = "now"
    def mark_rollback_result(self, _, complete): self.status = "rolled_back" if complete else "rollback_partial"
    def mark_source_conflicts(self, *_): raise AssertionError("unexpected conflict")


class AcceptanceDrive:
    def __init__(self):
        self.files = {
            "copy": DriveFile("copy", "copy", "application/vnd.google-apps.folder", "root", object_type="folder"),
            "file": DriveFile("file", "old.pdf", "application/pdf", "copy", "abc", modified_time="2026-08-28T10:00:00Z"),
        }

    def list_children(self, parent): return [value for value in self.files.values() if value.parent_id == parent]
    def create_folder(self, name, parent):
        key = f"folder-{len(self.files)}"; self.files[key] = DriveFile(key, name, "application/vnd.google-apps.folder", parent, object_type="folder"); return key
    def get_file_meta(self, file_id): return self.files[file_id]
    def assert_inside_copy(self, file_id, root): assert root in {"copy", "source"} and file_id == "file"
    def rename_file(self, file_id, name, _root): self.files[file_id].name = name
    def move_file(self, file_id, parent, _old, _root): self.files[file_id].parent_id = parent


def test_mvp1_apply_is_idempotent_and_rollback_restores_original():
    repo, drive = AcceptanceRepo(), AcceptanceDrive()
    executor = OrganizerExecutor(repo, drive)
    result = executor.apply(1)
    assert result["renamed"] == 1 and result["moved"] == 1
    assert drive.files["file"].name == "new.pdf"
    assert executor.apply(1)["already_applied"] == 1
    rollback = executor.rollback(1)
    assert rollback["errors"] == 0
    assert drive.files["file"].name == "old.pdf"
    assert drive.files["file"].parent_id == "copy"
