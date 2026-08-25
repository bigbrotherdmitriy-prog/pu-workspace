from types import SimpleNamespace
from app.organizer_engine.repository import OrganizerRepository


class FakeDb:
    def __init__(self, rowcount):
        self.rowcount = rowcount
        self.committed = False

    def execute(self, *_args, **_kwargs):
        return SimpleNamespace(rowcount=self.rowcount)

    def commit(self):
        self.committed = True


def test_mark_prepared_reports_successful_transition():
    db = FakeDb(1)
    assert OrganizerRepository(db).mark_prepared(5) is True
    assert db.committed


def test_mark_prepared_reports_rejected_transition():
    assert OrganizerRepository(FakeDb(0)).mark_prepared(5) is False
