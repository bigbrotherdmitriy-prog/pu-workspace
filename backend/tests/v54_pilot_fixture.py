"""Synthetic records shared by follow-up contract tests. Never seed a real DB."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from app.core.v54_dto import canonical_hash
from app.core.v54_interfaces import RequestScope
from app.core.v54_refs import ObjectRef
from app.models.ai_secretary import Message
from app.models.organization_contract import Organization, Contract
from app.models.project import Project
from app.models.user import User
from app.models.v54_pilot import (
    ConnectionIdentity, MailConnection, SourceReference, SourceVersion, SourceCurrent,
    Evidence, EvidenceAssessment, DeadlineClaim, ActionPolicy, PilotAction,
    ActionRevision, ActionApproval, PendingDispatch,
)

NOW = datetime(2026, 9, 3, 9, tzinfo=timezone.utc)
DOC_FIXTURE = Path(__file__).resolve().parents[2] / "docs/architecture/v54/integration/pilot.json"


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def ref(kind, value, tenant=1):
    int_types = {"organization", "user", "project", "contract", "message", "task", "response_draft", "background_job"}
    return {"namespace": "pu", "type": kind, "tenant_id": {"kind": "int", "value": str(tenant)},
            "id": {"kind": "int" if kind in int_types else "uuid", "value": str(value)}}


def pin(kind, value, version=1, tenant=1, version_kind="revision"):
    return {"ref": ref(kind, value, tenant), "version_kind": version_kind, "value": version}


def scope():
    return RequestScope(actor=ref("user", 2), tenant={"kind": "int", "value": "1"},
                        project=ref("project", 4), correlation_id="synthetic-foundation")


def envelopes():
    d = json.loads(DOC_FIXTURE.read_text(encoding="utf8"))
    assert d["synthetic_only"] is True
    return [deepcopy(r["envelope"]) for r in d["records"] if "envelope" in r]


def seed(db):
    """Caller owns this transaction. No commit, network, provider or execution."""
    # The historical organization migration creates id=1 on an otherwise empty
    # database. Reuse that bootstrap row so the synthetic fixture exercises the
    # real migration path without rewriting or duplicating baseline data.
    if db.get(Organization, 1) is None:
        db.add(Organization(id=1, name="Synthetic tenant"))
    db.add_all([Organization(id=2, name="Other synthetic tenant"),
                User(id=2, name="Requester", email="requester@example.test", is_admin=False),
                User(id=3, name="Reviewer", email="reviewer@example.test", is_admin=False)])
    db.flush()
    db.add_all([Project(id=4, name="Synthetic Alpha", organization_id=1),
                Project(id=9, name="Other synthetic project", organization_id=2)])
    db.flush()
    db.add(Contract(id=5, project_id=4, number="TEST-A-2026", title="Synthetic contract"))
    db.flush()
    msg = Message(id=6, organization_id=1, project_id=4, contract_id=5, created_by_user_id=2,
                  source_type="synthetic", source_external_id="synthetic-mail-1", source_name="Synthetic",
                  content="Synthetic only", summary="", context_evidence="", attachments_json="[]")
    db.add(msg)
    db.flush()
    identity = ConnectionIdentity(id=uid(10), organization_id=1, provider="synthetic",
                                  account_key="synthetic-account", state="verified",
                                  verified_at=NOW, credential_generation=1)
    db.add(identity)
    db.flush()
    db.add(MailConnection(id=uid(11), organization_id=1, identity_id=identity.id,
                          namespace="synthetic-mailbox", state="active"))
    db.flush()
    for number, kind in [(12, "message"), (13, "attachment")]:
        source = SourceReference(id=uid(number), organization_id=1, origin_project_id=4,
                                 identity_id=identity.id, namespace="synthetic-mailbox",
                                 external_id="synthetic-mail-1" if kind == "message" else "synthetic-attachment-1",
                                 external_id_kind="stable_id",
                                 object_kind=kind, canonical_locator={"kind": "opaque_id", "value": f"synthetic-{number}",
                                                                    "normalization_version": "1"})
        if kind == "attachment":
            source.parent_source_id = uid(12)
        db.add(source)
        db.flush()
    db.flush()
    for number, source in [(14, 12), (15, 13)]:
        db.add(SourceVersion(id=uid(number), organization_id=1, source_id=uid(source),
                             observation_key=f"synthetic-observation-{number}", provider_revision="fixture-v1",
                             consistency="revision_bound", locator_at_observation={"kind": "opaque_id", "value": f"synthetic-{source}"},
                             integrity=[], observed_at=NOW))
    db.flush()
    db.add_all([SourceCurrent(source_id=uid(12), organization_id=1, version_id=uid(14)),
                SourceCurrent(source_id=uid(13), organization_id=1, version_id=uid(15))])
    db.add(Evidence(id=uid(16), organization_id=1, source_id=uid(13), source_version_id=uid(15),
                    locator={"kind": "whole_object", "reason_code": "synthetic_fixture"},
                    extractor={"name": "fixture", "version": "1"}, confidence=None, extracted_at=NOW))
    db.flush()
    # Verification and freshness are explicitly synthetic test facts, not defaults.
    db.add(EvidenceAssessment(evidence_id=uid(16), organization_id=1, verification="verified",
                              freshness="fresh", availability="available", checked_at=NOW,
                              valid_until=NOW + timedelta(minutes=5), reviewed_by=3, reviewed_at=NOW))
    from datetime import date
    db.add(DeadlineClaim(id=uid(17), revision=1, organization_id=1, message_id=6,
                         due_date=date(2026, 9, 10), timezone="Europe/Moscow",
                         evidence_pins=[pin("evidence", uid(16))], provenance={"kind": "synthetic"},
                         verification="confirmed", reviewed_by=3, reviewed_at=NOW))
    msg.mail_connection_id = uid(11)
    msg.provider_message_id = "synthetic-mail-1"
    msg.source_reference_id = uid(12)
    db.flush()
    envelope = envelopes()[0]
    policy_record = next(r for r in json.loads(DOC_FIXTURE.read_text(encoding="utf8"))["records"]
                         if r["ref"]["type"] == "policy")
    db.add(ActionPolicy(id=uid(22), revision=1, organization_id=1, policy_hash=envelope["policy_sha256"],
                        mode="CONFIRM", scope_ref=ref("project", 4), rules=policy_record,
                        valid_until=NOW + timedelta(minutes=5)))
    db.add(PilotAction(id=uid(20), organization_id=1, project_id=4, message_id=6,
                       claim_id=uid(17), action_type="task.internal.create"))
    db.flush()
    db.add(ActionRevision(action_id=uid(20), revision=1, organization_id=1, claim_id=uid(17),
                          claim_revision=1, policy_id=uid(22), policy_revision=1, envelope=envelope,
                          envelope_hash=canonical_hash(envelope), command_key=envelope["idempotency_key"],
                          requested_by=2, created_at=NOW))
    db.flush()
    db.add(ActionApproval(id=uid(23), organization_id=1, action_id=uid(20), revision=1,
                          envelope_hash=canonical_hash(envelope), command_key="synthetic-approve-1",
                          approver_id=3, authority_epoch=1, state="GRANTED", granted_at=NOW,
                          expires_at=NOW + timedelta(minutes=5)))
    db.flush()
    db.add(PendingDispatch(action_id=uid(20), organization_id=1, revision=1, approval_id=uid(23),
                           envelope_hash=canonical_hash(envelope)))
    db.flush()
    return {"message": msg, "identity": identity, "envelope": envelope}
