from app.organizer_engine.repository import OrganizerRepository


def test_safe_auto_policy_preserves_manual_approval_below_threshold():
    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.committed = False

        def execute(self, statement, params):
            sql = str(statement)
            self.statements.append((sql, params))
            if "count(*)" in sql:
                return Result(0)
            return Result(None)

        def commit(self):
            self.committed = True

    db = FakeSession()
    OrganizerRepository(db).apply_auto_policy(23, 0.9)

    update_statements = [sql for sql, _ in db.statements if "UPDATE organizer_actions" in sql]
    assert update_statements, "auto policy must update organizer_actions"
    sql = update_statements[0]
    assert "user_decision IS NULL" in sql
    assert "user_decision = 'pending'" in sql
    assert "edited_name IS NULL" in sql
    assert "edited_folder IS NULL" in sql
    assert db.committed is True


def test_manual_approval_below_threshold_stays_approved_after_safe_policy():
    """Regression: manual user_decision='approved' with confidence < threshold
    must not be overwritten to 'skipped' by approve-safe."""
    class FakeRow:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeSession:
        def __init__(self, rows):
            self.rows = rows
            self.committed = False

        def execute(self, statement, params):
            sql = str(statement)
            if "UPDATE organizer_actions" in sql:
                for row in self.rows:
                    if row.user_decision == "pending" and row.edited_name is None and row.edited_folder is None:
                        if row.special_case is None and row.confidence >= params["minimum"]:
                            row.user_decision = "approved"
                        else:
                            row.user_decision = "skipped"

            class R:
                def __init__(self, value):
                    self.value = value

                def scalar_one(self):
                    return self.value

            if "count(*)" in sql:
                n = sum(
                    1
                    for r in self.rows
                    if r.user_decision == "approved" and r.edited_name is None and r.edited_folder is None
                )
                return R(n)
            return R(None)

        def commit(self):
            self.committed = True

    rows = [
        FakeRow(user_decision="approved", confidence=0.85, special_case=None, edited_name=None, edited_folder=None),
        FakeRow(user_decision="pending", confidence=0.95, special_case=None, edited_name=None, edited_folder=None),
        FakeRow(user_decision="pending", confidence=0.50, special_case=None, edited_name=None, edited_folder=None),
    ]
    db = FakeSession(rows)
    approved = OrganizerRepository(db).apply_auto_policy(42, 0.9)

    assert rows[0].user_decision == "approved"
    assert rows[1].user_decision == "approved"
    assert rows[2].user_decision == "skipped"
    assert approved == 2
    assert db.committed is True
