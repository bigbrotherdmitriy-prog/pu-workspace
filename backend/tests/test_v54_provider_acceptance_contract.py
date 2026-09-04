"""Contract tests for the isolated v5.4 synthetic provider harness.

These tests prove only the harness contract.  They do not import or exercise
the PU Workspace runtime composition or a live provider.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from support.v54_fake_provider import (
    ContextRevision,
    ContractError,
    EffectKind,
    Fault,
    MailboxIdentity,
    Mode,
    Outcome,
    Reversibility,
    Risk,
    SealedAction,
    StrictFakeProvider,
    SyntheticCommunicationActionHarness,
    payload_digest,
)


MAILBOX_A = MailboxIdentity("fake-provider", "account-a", "mailbox-a", 1)
MAILBOX_B = MailboxIdentity("fake-provider", "account-b", "mailbox-b", 1)


def _setup() -> tuple[StrictFakeProvider, SyntheticCommunicationActionHarness]:
    provider = StrictFakeProvider()
    provider.register(MAILBOX_A)
    provider.register(MAILBOX_B)
    harness = SyntheticCommunicationActionHarness(
        provider,
        {MAILBOX_A.key: "project-a", MAILBOX_B.key: "project-b"},
    )
    return provider, harness


def _action(
    *,
    action_id: str = "action-opaque-1",
    revision: int = 1,
    command_key: str = "command-opaque-1",
    payload_hash: str | None = None,
    mailbox: MailboxIdentity = MAILBOX_A,
    project_id: str = "project-a",
    mode: Mode = Mode.CONFIRM,
    risk: Risk = Risk.LOW,
    effect_kind: EffectKind = EffectKind.INTERNAL_TASK,
    reversibility: Reversibility = Reversibility.REVERSIBLE,
    capability_version: int = 1,
    authority_epoch: int = 1,
    corrects_action_id: str | None = None,
) -> SealedAction:
    return SealedAction(
        action_id=action_id,
        revision=revision,
        mode=mode,
        risk=risk,
        effect_kind=effect_kind,
        reversibility=reversibility,
        mailbox=mailbox,
        project_id=project_id,
        context_revision=1,
        evidence_pins=("evidence-opaque-1@v1", "attachment-opaque-1@v1"),
        payload_hash=payload_hash or payload_digest("synthetic fixture payload"),
        command_key=command_key,
        capability_version=capability_version,
        authority_epoch=authority_epoch,
        corrects_action_id=corrects_action_id,
    )


def _assert_error(code: str, call) -> None:
    with pytest.raises(ContractError) as caught:
        call()
    assert caught.value.code == code
    assert str(caught.value) == code


def test_a_message_attachment_evidence_context_to_approved_internal_task() -> None:
    provider, harness = _setup()
    context = ContextRevision(
        mailbox_key=MAILBOX_A.key,
        message_id="message-opaque-1",
        revision=1,
        project_id="project-a",
        contract_id="contract-opaque-1",
        evidence_pins=("evidence-opaque-1@v1", "attachment-opaque-1@v1"),
    )
    harness.record_context(context)
    action = _action()
    approval = harness.approve(action, "approval-opaque-1")

    receipt = harness.execute(action, approval)

    assert receipt.outcome is Outcome.APPLIED
    assert receipt.action_id == action.action_id
    assert provider.counters == {"dispatch": 1, "lookup": 0, "effects": 1}
    assert harness.context_history(MAILBOX_A.key, context.message_id) == (context,)


def test_b_repeat_delivery_and_command_do_not_duplicate_effect() -> None:
    provider, harness = _setup()
    action = _action()
    approval = harness.approve(action, "approval-opaque-1")

    first = harness.execute(action, approval)
    second = harness.execute(action, approval)

    assert second == first
    assert provider.counters["dispatch"] == 1
    assert provider.counters["effects"] == 1


def test_c_context_correction_preserves_project_and_contract_history() -> None:
    _, harness = _setup()
    original = ContextRevision(MAILBOX_A.key, "message-opaque-1", 1, "project-a", "contract-opaque-1", ("evidence-1@v1",))
    corrected = ContextRevision(MAILBOX_A.key, "message-opaque-1", 2, "project-b", "contract-opaque-2", ("evidence-2@v1",))

    harness.record_context(original)
    harness.correct_context(original, corrected)

    assert harness.context_history(MAILBOX_A.key, original.message_id) == (original, corrected)
    assert [row["event"] for row in harness.audit].count("context_recorded") == 2


def test_d_assist_has_no_effect_auto_denied_and_high_risk_requires_approval() -> None:
    provider, harness = _setup()
    assisted = harness.execute(_action(mode=Mode.ASSIST, effect_kind=EffectKind.EXTERNAL_DRAFT), None)
    assert assisted.outcome is Outcome.ASSISTED
    assert provider.counters["dispatch"] == 0

    _assert_error("auto_denied", lambda: harness.execute(_action(mode=Mode.AUTO), None))
    _assert_error(
        "approval_required",
        lambda: harness.execute(_action(risk=Risk.HIGH, effect_kind=EffectKind.EXTERNAL_SEND), None),
    )
    assert provider.counters["dispatch"] == 0


def test_e_changed_payload_invalidates_approval_and_conflicts_with_bound_command() -> None:
    provider, harness = _setup()
    original = _action()
    original_approval = harness.approve(original, "approval-opaque-1")
    mutated = replace(original, revision=2, payload_hash=payload_digest("different synthetic payload"))

    _assert_error("approval_mismatch", lambda: harness.execute(mutated, original_approval))
    assert provider.counters["dispatch"] == 0

    assert harness.execute(original, original_approval).outcome is Outcome.APPLIED
    mutated_approval = harness.approve(mutated, "approval-opaque-2")
    _assert_error("command_conflict", lambda: harness.execute(mutated, mutated_approval))
    assert provider.counters["effects"] == 1


def test_f_timeout_after_effect_stays_unknown_until_scoped_reconciliation() -> None:
    provider, harness = _setup()
    action = _action(
        effect_kind=EffectKind.EXTERNAL_SEND,
        reversibility=Reversibility.IRREVERSIBLE,
    )
    approval = harness.approve(action, "approval-opaque-1")
    provider.inject_fault(action.mailbox, action.command_key, Fault.TIMEOUT_AFTER_EFFECT)

    unknown = harness.execute(action, approval)
    repeated = harness.execute(action, approval)

    assert unknown.outcome is Outcome.UNKNOWN
    assert repeated == unknown
    assert provider.counters == {"dispatch": 1, "lookup": 0, "effects": 1}
    _assert_error("unknown_outcome", lambda: harness.retry_not_applied(action, approval))

    reconciled = harness.reconcile(action)
    assert reconciled.outcome is Outcome.APPLIED
    assert provider.counters == {"dispatch": 1, "lookup": 1, "effects": 1}


def test_timeout_before_effect_is_persisted_as_retry_safe_and_explicit_retry_applies_once() -> None:
    provider, harness = _setup()
    action = _action(effect_kind=EffectKind.EXTERNAL_DRAFT)
    approval = harness.approve(action, "approval-opaque-1")
    provider.inject_fault(action.mailbox, action.command_key, Fault.TIMEOUT_BEFORE_EFFECT)

    not_applied = harness.execute(action, approval)
    replay = harness.execute(action, approval)
    assert not_applied.outcome is Outcome.NOT_APPLIED
    assert not_applied.retry_safe is True
    assert replay == not_applied
    assert provider.counters == {"dispatch": 1, "lookup": 0, "effects": 0}

    applied = harness.retry_not_applied(action, approval)
    assert applied.outcome is Outcome.APPLIED
    assert provider.counters == {"dispatch": 2, "lookup": 0, "effects": 1}


def test_g_irreversible_send_cannot_rollback_and_follow_up_is_separate_action() -> None:
    provider, harness = _setup()
    sent = _action(
        effect_kind=EffectKind.EXTERNAL_SEND,
        reversibility=Reversibility.IRREVERSIBLE,
    )
    sent_receipt = harness.execute(sent, harness.approve(sent, "approval-opaque-1"))
    assert sent_receipt.outcome is Outcome.APPLIED
    _assert_error("irreversible_action", lambda: harness.mark_rolled_back(sent))

    correction = _action(
        action_id="action-opaque-correction",
        command_key="command-opaque-correction",
        payload_hash=payload_digest("synthetic corrective follow-up"),
        effect_kind=EffectKind.CORRECTIVE_FOLLOW_UP,
        reversibility=Reversibility.IRREVERSIBLE,
        corrects_action_id=sent.action_id,
    )
    _assert_error("approval_required", lambda: harness.execute(correction, None))
    corrected = harness.execute(correction, harness.approve(correction, "approval-opaque-2"))

    assert corrected.outcome is Outcome.APPLIED
    assert corrected.action_id != sent_receipt.action_id
    assert provider.counters["effects"] == 2


def test_compensatable_action_requires_a_new_compensation_action() -> None:
    _, harness = _setup()
    action = _action(effect_kind=EffectKind.EXTERNAL_DRAFT, reversibility=Reversibility.COMPENSATABLE)
    harness.execute(action, harness.approve(action, "approval-opaque-1"))
    _assert_error("compensation_required", lambda: harness.mark_rolled_back(action))


def test_h_same_provider_object_id_is_strictly_scoped_to_exact_mailbox() -> None:
    provider, harness = _setup()
    action_a = _action()
    action_b = _action(
        action_id="action-opaque-2",
        command_key="command-opaque-2",
        mailbox=MAILBOX_B,
        project_id="project-b",
    )
    receipt_a = harness.execute(action_a, harness.approve(action_a, "approval-opaque-1"))
    receipt_b = harness.execute(action_b, harness.approve(action_b, "approval-opaque-2"))

    assert receipt_a.external_id == receipt_b.external_id == "provider-object-1"
    assert receipt_a.mailbox_key != receipt_b.mailbox_key
    assert provider.lookup_external(MAILBOX_A, receipt_a.external_id or "") == receipt_a
    assert provider.lookup_external(MAILBOX_B, receipt_b.external_id or "") == receipt_b

    context_a = ContextRevision(MAILBOX_A.key, "same-thread-opaque", 1, "project-a", "contract-a", ("evidence-a@v1",))
    context_b = ContextRevision(MAILBOX_B.key, "same-thread-opaque", 1, "project-b", "contract-b", ("evidence-b@v1",))
    harness.record_context(context_a)
    harness.record_context(context_b)
    assert harness.context_history(MAILBOX_A.key, "same-thread-opaque") == (context_a,)
    assert harness.context_history(MAILBOX_B.key, "same-thread-opaque") == (context_b,)


def test_scope_authority_capability_and_credential_are_rechecked_before_dispatch() -> None:
    provider, harness = _setup()

    wrong_project = _action(project_id="project-b")
    _assert_error(
        "project_scope_mismatch",
        lambda: harness.execute(wrong_project, harness.approve(wrong_project, "approval-project")),
    )

    stale_capability = _action(action_id="action-cap", command_key="command-cap")
    stale_cap_approval = harness.approve(stale_capability, "approval-cap")
    provider.refresh_capabilities(MAILBOX_A)
    _assert_error("capability_stale", lambda: harness.execute(stale_capability, stale_cap_approval))

    current_capability = replace(stale_capability, action_id="action-cred", command_key="command-cred", capability_version=2)
    stale_credential_approval = harness.approve(current_capability, "approval-cred")
    provider.revoke_credentials(MAILBOX_A)
    _assert_error("credential_stale", lambda: harness.execute(current_capability, stale_credential_approval))

    current_mailbox = replace(MAILBOX_A, credential_generation=2)
    stale_authority = replace(
        current_capability,
        action_id="action-authority",
        command_key="command-authority",
        mailbox=current_mailbox,
    )
    stale_authority_approval = harness.approve(stale_authority, "approval-authority")
    harness.revoke_authority()
    _assert_error("authority_stale", lambda: harness.execute(stale_authority, stale_authority_approval))

    assert provider.counters["dispatch"] == 0


def test_unknown_mailbox_is_denied_before_provider_call() -> None:
    provider, harness = _setup()
    unknown = MailboxIdentity("fake-provider", "account-unknown", "mailbox-unknown", 1)
    action = _action(mailbox=unknown, project_id="project-a")
    approval = harness.approve(action, "approval-opaque-1")
    _assert_error("mailbox_scope_mismatch", lambda: harness.execute(action, approval))
    assert provider.counters["dispatch"] == 0


def test_fake_provider_journal_and_errors_do_not_retain_sensitive_inputs() -> None:
    sensitive_body = "raw-body-marker"
    sensitive_recipient = "recipient-marker@example.test"
    sensitive_token = "token-secret-marker"
    digest = payload_digest("|".join((sensitive_body, sensitive_recipient, sensitive_token)))
    provider, harness = _setup()
    action = _action(payload_hash=digest, project_id="wrong-project")
    approval = harness.approve(action, "approval-opaque-1")
    _assert_error("project_scope_mismatch", lambda: harness.execute(action, approval))

    observable = repr(provider.journal) + repr(harness.audit)
    assert digest not in observable
    for marker in (sensitive_body, sensitive_recipient, sensitive_token):
        assert marker not in observable


def test_reversible_internal_cancel_has_its_own_approval_and_receipt() -> None:
    provider, harness = _setup()
    create = _action()
    harness.execute(create, harness.approve(create, "approval-create"))
    cancel = _action(
        action_id="action-cancel",
        command_key="command-cancel",
        effect_kind=EffectKind.INTERNAL_CANCEL,
        payload_hash=payload_digest("synthetic cancel request"),
    )
    _assert_error("approval_required", lambda: harness.execute(cancel, None))
    receipt = harness.execute(cancel, harness.approve(cancel, "approval-cancel"))

    assert receipt.outcome is Outcome.APPLIED
    assert provider.counters["effects"] == 2
    assert harness.mark_rolled_back(cancel).outcome is Outcome.ROLLED_BACK
