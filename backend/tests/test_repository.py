from types import SimpleNamespace
from app.organizer_engine.repository import OrganizerRepository
from sqlalchemy.sql.dml import Update


class FakeDb:
    def __init__(self, rowcount):
        self.rowcount = rowcount
        self.committed = False

    def execute(self, statement, *_args, **_kwargs):
        self.statement = statement
        return SimpleNamespace(rowcount=self.rowcount)

    def commit(self):
        self.committed = True


def test_mark_prepared_reports_successful_transition():
    db = FakeDb(1)
    assert OrganizerRepository(db).mark_prepared(5) is True
    assert db.committed


def test_mark_prepared_reports_rejected_transition():
    assert OrganizerRepository(FakeDb(0)).mark_prepared(5) is False


def test_update_session_uses_sqlalchemy_update_and_allowlist():
    db = FakeDb(1)
    OrganizerRepository(db).update_session(7, status="ready", progress=80, malicious="ignored")
    assert isinstance(db.statement, Update)
    compiled = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "UPDATE organizer_sessions" in compiled
    assert "malicious" not in compiled
    assert db.committed


def test_requeue_incomplete_sessions_resets_stale_runtime_status():
    db = FakeDb(2)
    OrganizerRepository(db).requeue_incomplete_sessions([7, 8])
    assert isinstance(db.statement, Update)
    compiled = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "UPDATE organizer_sessions" in compiled
    assert "processed_item_count=0" in compiled.replace(" ", "")
    assert db.committed
