from app.organizer import _audit, resources_router, router
from app.organizer_engine.repository import OrganizerRepository


def test_safe_bulk_approval_route_is_registered():
    paths = {route.path for route in router.routes}
    assert "/organizer/proposals/{proposal_id}/approve-safe" in paths
    assert "/organizer/proposals/{proposal_id}/confirm-selected" in paths
    assert "/organizer/proposals/{proposal_id}/apply-source-one" in paths
    assert "/organizer/proposals/{proposal_id}/apply-source-approved" in paths
    assert "/organizer/proposals/{proposal_id}/standardize-copy" in paths


def _endpoint(routes, path, method):
    for route in routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"no route registered for {method} {path}")


def test_resources_router_exposes_exactly_the_change_batches_aliases():
    """docs/audits/mvp1-main-integration-app-composition-preflight.md 3.5:
    prefix-less aliases for the canonical /organizer/... proposal/rule routes,
    under the external "change-batches" naming — additive only.
    """
    paths = {route.path for route in resources_router.routes}
    assert paths == {
        "/proposals", "/change-batches",
        "/proposals/{proposal_id}", "/change-batches/{proposal_id}",
        "/change-batches/{proposal_id}/apply",
        "/rollbacks/{proposal_id}", "/change-batches/{proposal_id}/rollback",
        "/rules",
    }


def test_resources_router_aliases_are_the_same_handlers_as_organizer_router():
    """Each alias is the identical Python function object as its canonical
    /organizer/... route (decorator stacking, not a re-implementation) — so
    the two can never drift in behaviour.
    """
    pairs = [
        ("GET", "/proposals", "/organizer/proposals"),
        ("GET", "/change-batches", "/organizer/proposals"),
        ("GET", "/proposals/{proposal_id}", "/organizer/proposals/{proposal_id}"),
        ("GET", "/change-batches/{proposal_id}", "/organizer/proposals/{proposal_id}"),
        ("POST", "/change-batches/{proposal_id}/apply", "/organizer/proposals/{proposal_id}/apply"),
        ("POST", "/rollbacks/{proposal_id}", "/organizer/proposals/{proposal_id}/rollback"),
        ("POST", "/change-batches/{proposal_id}/rollback", "/organizer/proposals/{proposal_id}/rollback"),
        ("POST", "/rules", "/organizer/rules"),
    ]
    for method, alias_path, canonical_path in pairs:
        assert _endpoint(resources_router.routes, alias_path, method) is _endpoint(router.routes, canonical_path, method)


def test_manual_confirmation_skips_untouched_rows_instead_of_approving_them():
    source = __import__("inspect").getsource(
        next(route.endpoint for route in router.routes if route.path.endswith("confirm-selected"))
    )
    repository_source = __import__("inspect").getsource(
        __import__("app.organizer_engine.repository", fromlist=["OrganizerRepository"]).OrganizerRepository.confirm_selected
    )
    assert '"manager"' in source
    assert "Select at least one action" in source
    assert "user_decision='skipped'" in repository_source
    assert "user_decision IN ('approved','edited')" in repository_source


def test_repository_manual_confirmation_is_fail_closed_for_untouched_rows():
    class Result:
        def scalar_one(self):
            return 2

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.committed = False

        def execute(self, statement, params):
            self.statements.append((str(statement), params))
            return Result()

        def commit(self):
            self.committed = True

    db = FakeSession()
    selected = OrganizerRepository(db).confirm_selected(17)

    assert selected == 2
    assert db.committed is True
    sql = "\n".join(statement for statement, _ in db.statements)
    assert "user_decision IN ('approved','edited')" in sql
    assert "user_decision='skipped'" in sql
    assert "status='approved'" in sql


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
