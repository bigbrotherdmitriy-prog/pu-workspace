"""Offline regression for safe PostgreSQL-test thread diagnostics, not PG proof."""
from sqlalchemy.exc import OperationalError

import test_v54_authority_postgres as harness
import pytest


def test_aware_seed_timestamp_requires_distinct_mutation_clock():
    from sqlalchemy.orm.attributes import set_committed_value
    from app.models.v54_authority import AuthorityState, _authority_epoch_guard
    from v54_pilot_fixture import NOW
    def changed_row(clock):
        row = AuthorityState()
        # Reproduce PostgreSQL's timezone-aware committed attribute history,
        # not SQLite's naive datetime conversion and not PostgreSQL I/O itself.
        for field,value in dict(organization_id=1,project_id=4,principal_kind="user",principal_id="3",
            scope="v54.synthetic.confirm",membership_role="manager",permissions=["action.approve"],state="active",
            authority_epoch=1,record_version=1,updated_at=NOW).items():
            set_committed_value(row,field,value)
        row.membership_role="viewer"; row.permissions=["metadata"]; row.state="revoked"
        row.authority_epoch=2; row.record_version=2; row.updated_at=clock
        return row
    with pytest.raises(ValueError,match="^authority_epoch_required$"):
        _authority_epoch_guard(None,None,changed_row(NOW))
    _authority_epoch_guard(None,None,changed_row(harness.AUTHORITY_MUTATION_NOW))


def test_thread_database_error_is_captured_without_message_sql_or_parameters():
    secret = "synthetic-secret-never-print"
    failures = {}
    def fail():
        raise OperationalError("SELECT synthetic_document", {"dsn": secret}, RuntimeError(secret))
    harness._guarded_authority_thread("revoke", fail, failures)
    assert failures == {"revoke": "OperationalError"}
    safe = harness._authority_diagnostics({"revoke": "change", "dispatch": "waiting"}, failures, [])
    assert secret not in repr(safe) and "SELECT" not in repr(safe) and "dsn" not in repr(safe)


def test_unknown_exception_type_and_unknown_phase_are_not_exposed():
    failures = {}
    exception_type = type("synthetic_private_exception_name", (Exception,), {})
    def fail(): raise exception_type("synthetic-document-content")
    harness._guarded_authority_thread("dispatch", fail, failures)
    safe = harness._authority_diagnostics({"dispatch": "synthetic-secret-phase"}, failures, ["allowed"])
    assert failures == {"dispatch": "UnexpectedError"}
    assert "synthetic" not in repr(safe)
    assert safe["outcomes"] == ["allowed"]  # a real authorization failure stays visible


def test_success_does_not_invent_error_and_expected_deny_is_not_failure():
    failures, outcomes = {}, []
    harness._guarded_authority_thread("dispatch", lambda: outcomes.append("denied"), failures)
    safe = harness._authority_diagnostics({"dispatch":"denied","revoke":"committed"}, failures, outcomes)
    assert safe["errors"] == {} and safe["outcomes"] == ["denied"]


def test_probe_success_is_silent_and_failures_are_exact_fixed_shape():
    assert harness._authority_failure_probe({}, {}, ["denied", "revoked"]) is None
    for phases, errors, outcomes, alive in [
        ({"revoke":"change"},{"revoke":"OperationalError"},[],False),
        ({"dispatch":"require"},{"dispatch":"IntegrityError"},["revoked"],False),
        ({"revoke":"synthetic-private-phase"},{"revoke":"synthetic-private-type"},[],False),
        ({},{},["allowed","revoked"],False),
        ({},{},[],True),
    ]:
        probe=harness._authority_failure_probe(phases,errors,outcomes,threads_alive=alive)
        assert set(probe)=={"probe","status","phase","error_code"}
        assert probe["probe"]=="authority_concurrency" and probe["status"]=="FAIL"
        assert probe["phase"] in harness._AUTHORITY_PROBE_PHASES
        assert probe["error_code"] in harness._AUTHORITY_PROBE_ERRORS
        assert "synthetic" not in repr(probe)
