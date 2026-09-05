"""Synthetic-only durable runtime for exact storage mutations.

No runtime is installed automatically.  A test/acceptance composition must
inject a session factory and an adapter explicitly marked as synthetic.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.organizer import OrganizerOperation, OrganizerProposal
from app.organizer_engine.storage_mutation_repository import StorageMutationResolver
from app.organizer_engine.storage_mutations import (
    MutationCommand,
    MutationConflict,
    MutationReceipt,
    StorageBindingPin,
    StorageMutation,
    VersionedMutableStorageAdapter,
    command_hash,
    execute_mutation,
    rollback_mutation,
)


def _ledger_key(kind: str, command_key: str) -> str:
    return f"storage:{kind}:{sha256(command_key.encode()).hexdigest()}"


def _operation(data: dict) -> StorageMutation:
    return StorageMutation(**data)


def _pin(data: dict) -> StorageBindingPin:
    return StorageBindingPin(**data)


class DurableMutationLedger:
    """Append-only attempt/receipt adapter over the existing operation table."""

    def __init__(self, db: Session, payload: dict, command: MutationCommand):
        self.db = db
        self.payload = dict(payload)
        self.command = command
        self.proposal = db.scalar(select(OrganizerProposal).where(
            OrganizerProposal.id == int(payload["proposal_id"]),
            OrganizerProposal.project_id == int(payload["project_id"]),
        ))
        if self.proposal is None:
            raise MutationConflict("resource_unavailable")

    def current_binding(self, project_id: int) -> StorageBindingPin:
        if project_id != self.command.pin.project_id:
            raise MutationConflict("resource_unavailable")
        # This is deliberately resolved again immediately before provider I/O.
        return StorageMutationResolver(self.db).resolve(self.payload).pin

    def current_record_version(self, project_id: int) -> int:
        if project_id != self.command.pin.project_id:
            raise MutationConflict("resource_unavailable")
        count = self.db.scalar(select(func.count(OrganizerOperation.id)).where(
            OrganizerOperation.proposal_id == self.proposal.id,
            OrganizerOperation.op_type == "storage_mutation_receipt",
        )) or 0
        return int(count) + 1

    def attempt_exists(self, command_key: str) -> bool:
        return self.db.scalar(select(OrganizerOperation.id).where(
            OrganizerOperation.idempotency_key == _ledger_key("attempt", command_key)
        )) is not None

    def append_attempt(self, command: MutationCommand) -> int:
        row = OrganizerOperation(
            proposal_id=self.proposal.id,
            session_id=self.proposal.session_id,
            file_id="opaque:storage-mutation",
            op_type="storage_mutation_attempt",
            before_json={"command_key": command.command_key, "payload_hash": command_hash(command)},
            after_json={"state": "started", "provider": command.pin.provider},
            idempotency_key=_ledger_key("attempt", command.command_key),
        )
        self.db.add(row)
        self.db.flush()
        return int(row.id)

    def get(self, command_key: str) -> MutationReceipt | None:
        row = self.db.scalar(select(OrganizerOperation).where(
            OrganizerOperation.idempotency_key == _ledger_key("receipt", command_key)
        ))
        if row is None:
            return None
        data = row.after_json
        return MutationReceipt(
            command_key=str(data["command_key"]), payload_hash=str(data["payload_hash"]),
            pin=_pin(data["pin"]), resulting_record_version=int(data["resulting_record_version"]),
            outcome=data["outcome"],
            applied_operations=tuple(_operation(item) for item in data.get("operations", [])),
        )

    def append(self, receipt: MutationReceipt) -> None:
        if self.get(receipt.command_key) is not None:
            raise MutationConflict("immutable_receipt_exists")
        row = OrganizerOperation(
            proposal_id=self.proposal.id,
            session_id=self.proposal.session_id,
            file_id="opaque:storage-mutation",
            op_type="storage_mutation_receipt",
            before_json={"attempt_key": _ledger_key("attempt", receipt.command_key)},
            after_json={
                "command_key": receipt.command_key,
                "payload_hash": receipt.payload_hash,
                "pin": asdict(receipt.pin),
                "resulting_record_version": receipt.resulting_record_version,
                "outcome": receipt.outcome,
                "operations": [asdict(item) for item in receipt.applied_operations],
            },
            idempotency_key=_ledger_key("receipt", receipt.command_key),
        )
        self.db.add(row)
        self.db.flush()

    def latest_applied(self) -> MutationReceipt | None:
        rows = self.db.scalars(select(OrganizerOperation).where(
            OrganizerOperation.proposal_id == self.proposal.id,
            OrganizerOperation.op_type == "storage_mutation_receipt",
        ).order_by(OrganizerOperation.id.desc())).all()
        for row in rows:
            if row.after_json.get("outcome") == "applied":
                return self.get(str(row.after_json["command_key"]))
        return None

    def receipt_id(self, command_key: str) -> int:
        value = self.db.scalar(select(OrganizerOperation.id).where(
            OrganizerOperation.idempotency_key == _ledger_key("receipt", command_key)
        ))
        if value is None:
            raise MutationConflict("receipt_unavailable")
        return int(value)


def _state(adapter: VersionedMutableStorageAdapter, command: MutationCommand) -> str:
    states: list[str] = []
    for item in command.operations:
        adapter.assert_inside_copy(item.object_id, command.pin.folder_id)
        current = adapter.get_file_meta(item.object_id)
        before = current.parent_id == item.old_parent_id and current.name == item.old_name
        after_parent = item.old_parent_id if item.kind == "rename" else item.new_parent_id
        after_name = item.new_name if item.kind == "rename" else item.old_name
        after = current.parent_id == after_parent and current.name == after_name
        states.append("before" if before else "after" if after else "other")
    if states and all(value == "after" for value in states):
        return "after"
    if states and all(value == "before" for value in states):
        return "before"
    return "unknown"


class SyntheticStorageMutationRuntime:
    """Crash-safe runtime; live provider adapters are always rejected."""

    def __init__(self, session_factory: Callable[[], Session],
                 adapter_factory: Callable[[StorageBindingPin], VersionedMutableStorageAdapter], *,
                 enabled: bool = False):
        self.session_factory = session_factory
        self.adapter_factory = adapter_factory
        self.enabled = enabled

    @staticmethod
    def _require_synthetic(adapter: VersionedMutableStorageAdapter) -> None:
        if getattr(adapter, "synthetic_storage_adapter", False) is not True:
            raise MutationConflict("live_storage_mutation_disabled")

    def execute(self, **payload) -> dict:
        if self.enabled is not True:
            raise MutationConflict("storage_mutation_runtime_disabled")
        with self.session_factory() as db:
            command = StorageMutationResolver(db).resolve(payload)
            ledger = DurableMutationLedger(db, payload, command)
            adapter = self.adapter_factory(command.pin)
            self._require_synthetic(adapter)

            prior = ledger.get(command.command_key)
            if prior is not None:
                if prior.payload_hash != command_hash(command):
                    raise MutationConflict("idempotency_key_conflict")
                return {"receipt_id": ledger.receipt_id(command.command_key), "outcome": prior.outcome,
                        "resulting_record_version": prior.resulting_record_version}

            if ledger.attempt_exists(command.command_key):
                state = _state(adapter, command)
                outcome = "applied" if state == "after" else "unknown"
                receipt = MutationReceipt(command.command_key, command_hash(command), command.pin,
                                          command.expected_record_version + 1, outcome,
                                          command.operations if state == "after" else ())
                ledger.append(receipt)
                db.commit()
                return {"receipt_id": ledger.receipt_id(command.command_key), "outcome": outcome,
                        "resulting_record_version": receipt.resulting_record_version}

            ledger.append_attempt(command)
            db.commit()  # crash fence: attempt is durable before any provider effect

            # Re-resolve after the fence. execute_mutation performs one final binding,
            # CAS and provider-state check immediately before the effect.
            command = StorageMutationResolver(db).resolve(payload)
            ledger = DurableMutationLedger(db, payload, command)
            if payload["operation"] == "rollback":
                original = ledger.latest_applied()
                if original is None:
                    raise MutationConflict("rollback_receipt_unavailable")
                receipt = rollback_mutation(original, rollback_key=command.command_key,
                                            expected_record_version=command.expected_record_version,
                                            adapter=adapter, store=ledger)
            else:
                receipt = execute_mutation(command, adapter=adapter, store=ledger)
            db.commit()
            return {"receipt_id": ledger.receipt_id(receipt.command_key), "outcome": receipt.outcome,
                    "resulting_record_version": receipt.resulting_record_version}
