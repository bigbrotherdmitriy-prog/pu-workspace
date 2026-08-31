from app.organizer import _audit, router


def test_safe_bulk_approval_route_is_registered():
    paths = {route.path for route in router.routes}
    assert "/organizer/proposals/{proposal_id}/approve-safe" in paths
    assert "/organizer/proposals/{proposal_id}/apply-source-one" in paths
    assert "/organizer/proposals/{proposal_id}/apply-source-approved" in paths
    assert "/organizer/proposals/{proposal_id}/standardize-copy" in paths


def test_bulk_source_apply_requires_backup_and_explicit_confirmation():
    source = __import__("inspect").getsource(
        next(route.endpoint for route in router.routes if route.path.endswith("apply-source-approved"))
    )
    assert 'payload.confirmation != "APPLY_APPROVED_TO_SOURCE"' in source
    assert "safe copy before changing the working folder" in source
    assert '"owner"' in source


def test_organizer_audit_records_actor_identity():
    class FakeSession:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, row):
            self.added.append(row)

        def commit(self):
            self.committed = True

    db = FakeSession()
    _audit(db, "proposal_decided", "organizer_proposal", 7, "Approved", user_id=42)

    assert db.committed is True
    assert len(db.added) == 1
    assert db.added[0].details == "user=42; Approved"
