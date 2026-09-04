"""Synthetic, read-only tests for the v5.4 fragment reader."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import traceback

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register all metadata
from app.database import Base
from app.core.v54_interfaces import RequestScope, Resolution
from app.core.v54_permissions import SourceEvidenceError
from app.models.v54_pilot import (
    ConnectionIdentity, Evidence, EvidenceAssessment, MailConnection,
    SourceCurrent, SourceReference, SourceVersion,
)
from app.source_evidence.facade import SourceEvidenceFacade
from app.source_evidence.fragment_reader import (
    FragmentLimits, FragmentStorePayload, read_fragment,
)
from test_v54_source_evidence_pilot import P, R, policy, prepared, scope
from v54_pilot_fixture import NOW, uid


TEXT = b"Synthetic evidence fragment only."


class ResolverDouble:
    def __init__(self, **changes):
        self.changes = changes
        self.calls = 0

    def resolve(self, db, *, scope, pin, operation, lock):
        self.calls += 1
        values = dict(
            pin=pin, actor=scope.actor, project=scope.project, operation=operation,
            acl="allow", version="current", freshness="fresh", availability="available",
            verification="verified", policy_known=True, retention_known=True,
            residency_allowed=True, valid_until=NOW + timedelta(minutes=4),
            authority_epoch=1, binding_epoch=1,
        )
        values.update(self.changes)
        return Resolution(**values)


class StoreDouble:
    def __init__(self, *, fragment=TEXT, **changes):
        self.fragment = fragment
        self.changes = changes
        self.calls = []

    def read(self, request):
        self.calls.append(request)
        values = dict(
            representation_id=request.representation_id,
            evidence_pin=request.evidence_pin,
            source_ref=request.source_ref,
            source_version_pin=request.source_version_pin,
            kind=request.kind,
            media_type=request.media_type,
            fragment=self.fragment,
        )
        values.update(self.changes)
        return FragmentStorePayload(**values)


class ExplodingStore:
    calls = 0

    def read(self, request):
        self.calls += 1
        raise RuntimeError("SECRET excerpt https://provider.example/item?id=42")


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        from v54_pilot_fixture import seed
        seed(session)
        yield session
        session.rollback()
    engine.dispose()


def descriptor(evidence, source, version, **changes):
    value = {
        "schema_version": "v54.fragment.1",
        "representation_id": "rep-current-1",
        "handle": "opaque-fragment-1",
        "evidence_pin": evidence.model_dump(mode="json"),
        "source_ref": source.ref.model_dump(mode="json"),
        "source_version_pin": version.model_dump(mode="json"),
        "kind": "extracted_text",
        "media_type": "text/plain; charset=utf-8",
        "retention_state": "active",
        "expires_at": (NOW + timedelta(minutes=4)).isoformat(),
    }
    value.update(changes)
    return value


def ready(db, *, locator=None, verified=True):
    _, source, version, evidence = prepared(db, review=verified)
    # Representation materialization belongs to a future owner.  SQL setup is
    # used here only to seed the immutable synthetic assertion at insert state.
    db.execute(update(Evidence).where(Evidence.id == evidence.ref.id.value).values(
        representation_ref=descriptor(evidence, source, version),
        locator=locator or {"kind": "whole_object", "reason_code": "synthetic_fixture"},
    ))
    db.expire_all()
    return source, version, evidence


def read(db, evidence, *, resolver=None, store=None, actor_scope=None, limits=None):
    return read_fragment(
        db, scope=actor_scope or scope(), evidence_pin=evidence,
        resolver=resolver or ResolverDouble(), store=store or StoreDouble(),
        clock=lambda: NOW, limits=limits or FragmentLimits(),
    )


@pytest.mark.parametrize("verified", [True, False])
def test_current_verified_and_unverified_are_readable(db, verified):
    source, _, evidence = ready(db, verified=verified)
    result = read(db, evidence)
    assert result.fragment == TEXT.decode()
    assert result.verification == ("verified" if verified else "unverified")
    assert result.effective_status == ("verified" if verified else "unverified")
    assert result.historical is False
    assert result.assessment_record_version == (2 if verified else 1)
    assert result.version_state == "current"
    assert result.freshness == "fresh"
    assert result.availability == "available"
    assert result.valid_until == NOW + timedelta(minutes=4)
    assert result.extractor.model_dump() == {"name": "fixture", "version": "1",
                                             "method": None, "model_provider": None,
                                             "model_id": None, "model_version": None,
                                             "prompt_version": None,
                                             "configuration_digest": None}
    assert result.confidence is None
    assert result.confidence_kind == "unknown"
    assert result.extracted_at == NOW
    assert result.evidence_pin == evidence


def test_effective_status_never_elevates_unverified_resolver_state(db):
    _, _, evidence = ready(db, verified=True)
    result = read(db, evidence, resolver=ResolverDouble(verification="unverified"))
    assert result.verification == "verified"
    assert result.effective_status == "unverified"


@pytest.mark.parametrize("change", [
    {"acl": "deny"}, {"acl": "unknown"}, {"version": "changed"},
    {"version": "unknown"}, {"freshness": "stale"},
    {"availability": "unavailable"}, {"policy_known": False},
    {"retention_known": False}, {"residency_allowed": False},
    {"authority_epoch": None}, {"binding_epoch": None},
    {"valid_until": NOW - timedelta(seconds=1)},
    {"actor": scope(3).actor}, {"project": R("project", 8)},
    {"pin": P("evidence", uid(999))},
])
def test_authoritative_unknown_or_denied_state_fails_before_store(db, change):
    _, _, evidence = ready(db)
    store = StoreDouble()
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, resolver=ResolverDouble(**change), store=store)
    assert store.calls == []


def test_scope_pin_revision_and_foreign_resource_do_not_leak(db):
    _, _, evidence = ready(db)
    foreign_scope = RequestScope.model_validate({
        "project": {"namespace": "pu", "type": "project",
                    "tenant_id": {"kind": "int", "value": "2"},
                    "id": {"kind": "int", "value": "9"}},
        "tenant": {"kind": "int", "value": "2"},
        "actor": {"namespace": "pu", "type": "user",
                  "tenant_id": {"kind": "int", "value": "2"},
                  "id": {"kind": "int", "value": "2"}},
        "correlation_id": uid(998),
    })
    wrong_project = RequestScope.model_validate({
        **scope().model_dump(mode="json"),
        "project": {"namespace": "pu", "type": "project",
                    "tenant_id": {"kind": "int", "value": "1"},
                    "id": {"kind": "int", "value": "8"}},
    })
    cases = [
        (wrong_project, evidence),
        (foreign_scope, evidence),
        (scope(), P("evidence", uid(999))),
    ]
    for caller_scope, pin in cases:
        store = StoreDouble()
        with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
            read(db, pin, actor_scope=caller_scope, store=store)
        assert not store.calls
    db.get(Evidence, evidence.ref.id.value).revision = 2
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence)


@pytest.mark.parametrize("mutation", [
    "source_version_mismatch", "current_changed", "source_stale", "source_unavailable",
    "assessment_stale", "assessment_unavailable", "source_expired", "assessment_expired",
    "identity_revoked", "mailbox_revoked", "binding_changed", "policy_missing",
    "residency_missing", "consistency_unknown",
])
def test_local_chain_gates_fail_before_resolver_and_store(db, mutation):
    source_pin, version_pin, evidence = ready(db)
    source = db.get(SourceReference, source_pin.ref.id.value)
    assessment = db.get(EvidenceAssessment, evidence.ref.id.value)
    identity = db.get(ConnectionIdentity, source.identity_id)
    mailbox = db.query(MailConnection).filter_by(identity_id=source.identity_id, namespace=source.namespace).one()
    if mutation == "source_version_mismatch":
        db.get(Evidence, evidence.ref.id.value).source_version_id = uid(15)
    elif mutation == "current_changed":
        db.get(SourceCurrent, source.id).version_id = uid(15)
    elif mutation == "source_stale": source.freshness = "stale"
    elif mutation == "source_unavailable": source.availability = "access_denied"
    elif mutation == "assessment_stale": assessment.freshness = "stale"
    elif mutation == "assessment_unavailable": assessment.availability = "unavailable"
    elif mutation == "source_expired": source.next_check_at = NOW
    elif mutation == "assessment_expired": assessment.valid_until = NOW
    elif mutation == "identity_revoked": identity.state = "revoked"
    elif mutation == "mailbox_revoked": mailbox.state = "revoked"
    elif mutation == "binding_changed": identity.binding_epoch = 2
    elif mutation == "policy_missing": source.policy_pins = None
    elif mutation == "residency_missing": source.residency = None
    elif mutation == "consistency_unknown": db.get(SourceVersion, version_pin.ref.id.value).consistency = "unknown"
    resolver, store = ResolverDouble(), StoreDouble()
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, resolver=resolver, store=store)
    assert resolver.calls == (1 if mutation == "binding_changed" else 0)
    assert store.calls == []


@pytest.mark.parametrize("bad", [
    None,
    {},
    {"schema_version": "unknown"},
])
def test_malformed_descriptor_fails_closed(db, bad):
    _, _, evidence = ready(db)
    db.get(Evidence, evidence.ref.id.value).representation_ref = bad
    store = StoreDouble()
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, store=store)
    assert not store.calls


@pytest.mark.parametrize("handle", [
    "https://example.test/x", "C:\\secret\\x", "/var/secret", "../secret",
    "signed?token=secret", "provider://opaque", "fragment#part", "a%2Fb",
])
def test_descriptor_handle_is_opaque_not_a_locator(db, handle):
    source, version, evidence = ready(db)
    db.get(Evidence, evidence.ref.id.value).representation_ref = descriptor(
        evidence, source, version, handle=handle)
    store = StoreDouble()
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, store=store)
    assert not store.calls


@pytest.mark.parametrize("state,expires", [
    ("expired", NOW + timedelta(minutes=1)),
    ("purged", NOW + timedelta(minutes=1)),
    ("active", NOW),
])
def test_expired_or_purged_representation_is_unavailable(db, state, expires):
    source, version, evidence = ready(db)
    db.get(Evidence, evidence.ref.id.value).representation_ref = descriptor(
        evidence, source, version, retention_state=state, expires_at=expires.isoformat())
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence)


def locator_cases(source_id):
    return [
        ("file", {"kind": "page_bbox", "page": 2, "coordinate_space": "representation",
                  "units": "pixels", "box": [10.0, 20.0, 30.0, 40.0], "extent": [100.0, 100.0],
                  "representation_id": "rep-current-1", "precise_navigation": True}),
        ("file", {"kind": "page", "page": 3}),
        ("file", {"kind": "section_clause", "section_path": ["Section 2"],
                  "clause_label": "2.1", "anchor": None}),
        ("file", {"kind": "sheet_cell", "sheet_key": "sheet-1", "sheet_name": "Plan",
                  "range_a1": "Plan!B2:C4", "value_kind": "displayed_value"}),
        ("message", {"kind": "message", "message_external_id": "synthetic-pilot-message",
                     "part": "body", "char_range": [0, 9]}),
        ("attachment", {"kind": "attachment", "message_external_id": "synthetic-mail-1",
                        "attachment_external_id": "synthetic-pilot-message",
                        "attachment_source_reference_id": {
                            "namespace": "pu", "type": "source",
                            "tenant_id": {"kind": "int", "value": "1"},
                            "id": {"kind": "uuid", "value": source_id}}}),
        ("record", {"kind": "record", "record_key": "record-1", "field_path": ["amount", "net"]}),
        ("message", {"kind": "whole_object", "reason_code": "granularity_unavailable"}),
    ]


@pytest.mark.parametrize("index", range(8))
def test_all_locator_variants(index, db):
    source_pin, _, evidence = ready(db)
    source = db.get(SourceReference, source_pin.ref.id.value)
    evidence_row = db.get(Evidence, evidence.ref.id.value)
    object_kind, locator = locator_cases(source.id)[index]
    if object_kind == "attachment":
        db.execute(update(SourceReference).where(SourceReference.id == source.id).values(
            object_kind=object_kind, parent_source_id=uid(12)))
        db.expire(source)
    else:
        source.object_kind = object_kind
    evidence_row.locator = locator
    assert read(db, evidence).locator.kind == locator["kind"]


@pytest.mark.parametrize("locator", [
    {"kind": "page", "page": 0},
    {"kind": "page_bbox", "page": 1, "coordinate_space": "original", "units": "pixels",
     "box": [90.0, 0.0, 11.0, 1.0], "extent": [100.0, 100.0],
     "representation_id": "rep-current-1", "precise_navigation": True},
    {"kind": "page_bbox", "page": 1, "coordinate_space": "original", "units": "pixels",
     "box": [0.0, 0.0, 1.0, 1.0], "extent": [100.0, 100.0],
     "representation_id": "rep-other", "precise_navigation": False},
    {"kind": "message", "message_external_id": "wrong", "part": "body", "char_range": [1, 1]},
    {"kind": "invented", "page": 1},
])
def test_invalid_locator_or_geometry_never_reaches_store(db, locator):
    source, _, evidence = ready(db, locator=locator)
    db.get(SourceReference, source.ref.id.value).object_kind = "file" if locator["kind"].startswith("page") else "message"
    store = StoreDouble()
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, store=store)
    assert not store.calls


@pytest.mark.parametrize("field,value", [
    ("representation_id", "rep-other"),
    ("evidence_pin", P("evidence", uid(999))),
    ("source_ref", P("source", uid(13), version=1).ref),
    ("source_version_pin", P("source_version", uid(15))),
    ("kind", "quote"),
    ("media_type", "text/markdown"),
])
def test_store_payload_binding_mismatch_is_denied(db, field, value):
    _, _, evidence = ready(db)
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, store=StoreDouble(**{field: value}))


@pytest.mark.parametrize("fragment,limit", [
    (b"", 64),
    (b"\xff", 64),
    (b"a" * 65, 64),
    (b"a\x00b", 64),
])
def test_empty_non_utf8_oversized_or_unsafe_fragment_is_denied(db, fragment, limit):
    _, _, evidence = ready(db)
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, store=StoreDouble(fragment=fragment), limits=FragmentLimits(max_bytes=limit))


def test_store_error_is_sanitized_and_excerpt_absent_from_traceback(db):
    _, _, evidence = ready(db)
    with pytest.raises(SourceEvidenceError) as caught:
        read(db, evidence, store=ExplodingStore())
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert str(caught.value) == "resource_unavailable"
    assert "SECRET" not in rendered and "provider.example" not in rendered


def test_resolver_error_is_sanitized_and_store_is_not_called(db):
    class ExplodingResolver:
        def resolve(self, *args, **kwargs):
            raise RuntimeError("SECRET namespace and provider locator")

    _, _, evidence = ready(db)
    store = StoreDouble()
    with pytest.raises(SourceEvidenceError) as caught:
        read(db, evidence, resolver=ExplodingResolver(), store=store)
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert str(caught.value) == "resource_unavailable"
    assert "SECRET" not in rendered and "provider locator" not in rendered
    assert store.calls == []


@pytest.mark.parametrize("field", ["evidence_pin", "source_ref", "source_version_pin"])
def test_descriptor_binding_mismatch_is_denied_before_store(db, field):
    source, version, evidence = ready(db)
    changes = {
        "evidence_pin": P("evidence", uid(999)).model_dump(mode="json"),
        "source_ref": R("source", uid(13)).model_dump(mode="json"),
        "source_version_pin": P("source_version", uid(15)).model_dump(mode="json"),
    }
    db.get(Evidence, evidence.ref.id.value).representation_ref = descriptor(
        evidence, source, version, **{field: changes[field]})
    store = StoreDouble()
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, store=store)
    assert store.calls == []


@pytest.mark.parametrize("changed", ["message", "attachment", "source"])
def test_attachment_locator_requires_exact_parent_and_source_binding(db, changed):
    source_pin, _, evidence = ready(db)
    source = db.get(SourceReference, source_pin.ref.id.value)
    evidence_row = db.get(Evidence, evidence.ref.id.value)
    source.object_kind = "attachment"
    source.parent_source_id = uid(12)
    locator = locator_cases(source.id)[5][1]
    if changed == "message": locator["message_external_id"] = "wrong-message"
    elif changed == "attachment": locator["attachment_external_id"] = "wrong-attachment"
    else: locator["attachment_source_reference_id"]["id"]["value"] = uid(13)
    evidence_row.locator = locator
    store = StoreDouble()
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, store=store)
    assert store.calls == []


def test_reader_uses_one_lineage_select_and_does_not_mutate_rows_or_session(db):
    _, _, evidence = ready(db)
    row = db.get(Evidence, evidence.ref.id.value)
    before = {column.key: deepcopy(getattr(row, column.key)) for column in Evidence.__table__.columns}
    dirty_before, new_before, deleted_before = set(db.dirty), set(db.new), set(db.deleted)
    statements = []

    @event.listens_for(db.bind, "before_cursor_execute")
    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    read(db, evidence)
    event.remove(db.bind, "before_cursor_execute", capture)
    after = {column.key: deepcopy(getattr(row, column.key)) for column in Evidence.__table__.columns}
    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("SELECT")
    assert before == after
    assert set(db.dirty) == dirty_before and set(db.new) == new_before and set(db.deleted) == deleted_before
    assert db.in_transaction()


def test_attachment_parent_is_loaded_in_the_same_lineage_select(db):
    source_pin, _, evidence = ready(db)
    source = db.get(SourceReference, source_pin.ref.id.value)
    db.execute(update(SourceReference).where(SourceReference.id == source.id).values(
        object_kind="attachment", parent_source_id=uid(12)))
    db.execute(update(Evidence).where(Evidence.id == evidence.ref.id.value).values(
        locator=locator_cases(source.id)[5][1]))
    db.expire_all()
    statements = []

    @event.listens_for(db.bind, "before_cursor_execute")
    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    assert read(db, evidence).locator.kind == "attachment"
    event.remove(db.bind, "before_cursor_execute", capture)
    assert len(statements) == 1


@pytest.mark.parametrize("deadline_owner", ["resolver", "assessment", "source", "descriptor"])
def test_result_valid_until_is_earliest_live_gate(db, deadline_owner):
    source_pin, version, evidence = ready(db)
    source = db.get(SourceReference, source_pin.ref.id.value)
    assessment = db.get(EvidenceAssessment, evidence.ref.id.value)
    evidence_row = db.get(Evidence, evidence.ref.id.value)
    deadlines = {
        "resolver": NOW + timedelta(seconds=31),
        "assessment": NOW + timedelta(seconds=32),
        "source": NOW + timedelta(seconds=33),
        "descriptor": NOW + timedelta(seconds=34),
    }
    assessment.valid_until = deadlines["assessment"]
    source.next_check_at = deadlines["source"]
    evidence_row.representation_ref = descriptor(
        evidence, source_pin, version, expires_at=deadlines["descriptor"].isoformat())
    resolver = ResolverDouble(valid_until=deadlines["resolver"])
    expected = deadlines[deadline_owner]
    if deadline_owner != "resolver":
        resolver.changes["valid_until"] = NOW + timedelta(minutes=5)
    if deadline_owner != "assessment": assessment.valid_until = NOW + timedelta(minutes=5)
    if deadline_owner != "source": source.next_check_at = NOW + timedelta(minutes=5)
    if deadline_owner != "descriptor":
        evidence_row.representation_ref = descriptor(
            evidence, source_pin, version, expires_at=(NOW + timedelta(minutes=5)).isoformat())
    assert read(db, evidence, resolver=resolver).valid_until == expected


@pytest.mark.parametrize("mutation", ["future_extracted", "bad_extractor", "unknown_with_confidence"])
def test_invalid_extraction_provenance_fails_before_store(db, mutation):
    _, _, evidence = ready(db)
    row = db.get(Evidence, evidence.ref.id.value)
    if mutation == "future_extracted": row.extracted_at = NOW + timedelta(seconds=1)
    elif mutation == "bad_extractor": row.extractor = {"name": "fixture", "secret": "not-allowed"}
    else:
        row.confidence = 0.8
        row.confidence_kind = "unknown"
    store = StoreDouble()
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, store=store)
    assert store.calls == []


def test_no_fallback_to_latest_or_another_representation(db):
    source, version, evidence = ready(db)
    row = db.get(Evidence, evidence.ref.id.value)
    row.representation_ref = descriptor(
        evidence, source, version,
        source_version_pin=P("source_version", uid(15)).model_dump(mode="json"),
    )
    store = StoreDouble()
    with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
        read(db, evidence, store=store)
    assert not store.calls


def test_production_facade_fragment_remains_denied(db):
    _, _, evidence = ready(db)
    result = SourceEvidenceFacade(policy(), lambda: NOW).resolve(
        db, scope=scope(), pin=evidence, operation="fragment"
    )
    assert result.acl == "deny"
