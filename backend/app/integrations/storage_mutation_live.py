"""Capability-gated provider wrappers for exact storage mutations.

The existing Google Drive and Yandex Disk adapters intentionally are not
adapted here: their current mutation methods do not expose an atomic provider
revision precondition.  A live integration may use these wrappers only after
its client implements :class:`ConditionalMutationClient` completely.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Literal, Protocol, runtime_checkable

from app.core.integration_types import StorageObject
from app.integrations.contracts import AdapterHealth, StorageAccessDenied
from app.organizer_engine.storage_mutations import MutationOutcomeUnknown


ProviderName = Literal["google_drive", "yandex_disk"]


class ExactPreconditionUnavailable(RuntimeError):
    """The provider client cannot prove an atomic exact-revision mutation."""


class ConditionalMutationTimeout(TimeoutError):
    """Timeout with an optional proof that no provider effect was initiated."""

    def __init__(self, *, effect_definitely_absent: bool = False):
        super().__init__("provider_mutation_timeout")
        self.effect_definitely_absent = effect_definitely_absent


@dataclass(frozen=True, slots=True)
class ProviderObjectState:
    object_id: str
    name: str
    parent_id: str
    revision: str
    ancestor_ids: tuple[str, ...]
    mime_type: str = "application/octet-stream"
    object_type: str = "file"


@runtime_checkable
class ConditionalMutationClient(Protocol):
    """Provider client contract required before live mutation activation."""

    provider: ProviderName
    supports_exact_mutation_preconditions: bool

    def get_exact_state(self, object_id: str) -> ProviderObjectState: ...

    def rename_if_revision(
        self,
        object_id: str,
        new_name: str,
        *,
        expected_revision: str,
        operation_key: str,
    ) -> ProviderObjectState: ...

    def move_if_revision(
        self,
        object_id: str,
        new_parent_id: str,
        *,
        expected_parent_id: str,
        expected_revision: str,
        operation_key: str,
    ) -> ProviderObjectState: ...


def _operation_key(
    provider: str,
    operation: str,
    object_id: str,
    expected_revision: str,
    target: str,
) -> str:
    material = "\x1f".join((provider, operation, object_id, expected_revision, target))
    return "pu-storage:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


class ExactMutationAdapter:
    """Versioned mutable adapter that never performs an unguarded retry.

    ``enabled`` defaults to false.  Even when explicitly enabled, the injected
    client must advertise and implement exact conditional mutations.  Provider
    timeouts are reconciled by reading exact state; ambiguous results become
    ``MutationOutcomeUnknown``.
    """

    provider: ProviderName

    def __init__(
        self,
        client: ConditionalMutationClient,
        *,
        provider: ProviderName,
        enabled: bool = False,
    ):
        self.client = client
        self.provider = provider
        self.enabled = enabled
        self._states: dict[str, ProviderObjectState] = {}

    def _require_capability(self) -> None:
        if not self.enabled:
            raise ExactPreconditionUnavailable("storage_mutation_disabled")
        if getattr(self.client, "provider", None) != self.provider:
            raise ExactPreconditionUnavailable("provider_mismatch")
        if not getattr(self.client, "supports_exact_mutation_preconditions", False):
            raise ExactPreconditionUnavailable("exact_provider_precondition_unavailable")
        for method in ("get_exact_state", "rename_if_revision", "move_if_revision"):
            if not callable(getattr(self.client, method, None)):
                raise ExactPreconditionUnavailable("exact_provider_precondition_unavailable")

    def _read(self, object_id: str) -> ProviderObjectState:
        self._require_capability()
        state = self.client.get_exact_state(object_id)
        if (
            not isinstance(state, ProviderObjectState)
            or state.object_id != object_id
            or not state.revision
            or not state.parent_id
            or not state.name
        ):
            raise ExactPreconditionUnavailable("invalid_exact_provider_state")
        return state

    def health(self) -> AdapterHealth:
        try:
            self._require_capability()
        except ExactPreconditionUnavailable:
            return AdapterHealth(False, "exact storage mutation unavailable")
        return AdapterHealth(True, "exact conditional storage mutation ready")

    def object_revision(self, object_id: str) -> str:
        state = self._read(object_id)
        self._states[object_id] = state
        return state.revision

    def get_file_meta(self, object_id: str) -> StorageObject:
        state = self._read(object_id)
        pinned = self._states.get(object_id)
        if pinned is not None and pinned.revision != state.revision:
            raise ExactPreconditionUnavailable("provider_revision_changed_during_preflight")
        self._states[object_id] = state
        return StorageObject(
            id=state.object_id,
            name=state.name,
            mime_type=state.mime_type,
            parent_id=state.parent_id,
            object_type=state.object_type,
            provider=self.provider,
        )

    def get_object(self, object_id: str) -> StorageObject:
        return self.get_file_meta(object_id)

    def assert_inside_copy(self, object_id: str, copy_root_id: str) -> None:
        state = self._read(object_id)
        if state.object_id != copy_root_id and copy_root_id not in state.ancestor_ids:
            raise StorageAccessDenied("storage_mutation_outside_safe_copy")
        pinned = self._states.get(object_id)
        if pinned is not None and pinned.revision != state.revision:
            raise ExactPreconditionUnavailable("provider_revision_changed_during_preflight")
        self._states[object_id] = state

    @staticmethod
    def _rename_applied(state: ProviderObjectState, before: ProviderObjectState, target: str) -> bool:
        return state.name == target and state.parent_id == before.parent_id

    @staticmethod
    def _move_applied(state: ProviderObjectState, before: ProviderObjectState, target: str) -> bool:
        return state.parent_id == target and state.name == before.name

    def _reconcile_timeout(
        self,
        *,
        before: ProviderObjectState,
        target: str,
        operation: Literal["rename", "move"],
        definitely_absent: bool,
    ) -> ProviderObjectState:
        current = self._read(before.object_id)
        applied = (
            self._rename_applied(current, before, target)
            if operation == "rename"
            else self._move_applied(current, before, target)
        )
        if applied:
            return current
        unchanged = (
            current.revision == before.revision
            and current.name == before.name
            and current.parent_id == before.parent_id
        )
        if unchanged and definitely_absent:
            raise RuntimeError("provider_effect_definitely_absent")
        raise MutationOutcomeUnknown("provider_effect_requires_reconciliation")

    def rename_file(self, object_id: str, new_name: str, copy_root_id: str) -> None:
        self.assert_inside_copy(object_id, copy_root_id)
        before = self._states[object_id]
        key = _operation_key(self.provider, "rename", object_id, before.revision, new_name)
        try:
            after = self.client.rename_if_revision(
                object_id,
                new_name,
                expected_revision=before.revision,
                operation_key=key,
            )
        except ConditionalMutationTimeout as exc:
            after = self._reconcile_timeout(
                before=before,
                target=new_name,
                operation="rename",
                definitely_absent=exc.effect_definitely_absent,
            )
        except TimeoutError:
            after = self._reconcile_timeout(
                before=before,
                target=new_name,
                operation="rename",
                definitely_absent=False,
            )
        if not self._rename_applied(after, before, new_name) or not after.revision:
            raise MutationOutcomeUnknown("provider_effect_requires_reconciliation")
        self._states[object_id] = after

    def move_file(
        self,
        object_id: str,
        new_parent_id: str,
        old_parent_id: str,
        copy_root_id: str,
    ) -> None:
        self.assert_inside_copy(object_id, copy_root_id)
        self.assert_inside_copy(new_parent_id, copy_root_id)
        before = self._states[object_id]
        if before.parent_id != old_parent_id:
            raise ExactPreconditionUnavailable("provider_parent_precondition_changed")
        key = _operation_key(self.provider, "move", object_id, before.revision, new_parent_id)
        try:
            after = self.client.move_if_revision(
                object_id,
                new_parent_id,
                expected_parent_id=old_parent_id,
                expected_revision=before.revision,
                operation_key=key,
            )
        except ConditionalMutationTimeout as exc:
            after = self._reconcile_timeout(
                before=before,
                target=new_parent_id,
                operation="move",
                definitely_absent=exc.effect_definitely_absent,
            )
        except TimeoutError:
            after = self._reconcile_timeout(
                before=before,
                target=new_parent_id,
                operation="move",
                definitely_absent=False,
            )
        if not self._move_applied(after, before, new_parent_id) or not after.revision:
            raise MutationOutcomeUnknown("provider_effect_requires_reconciliation")
        self._states[object_id] = after

    # The coordinator does not use the remaining StorageAdapter operations.
    # They are delegated only when an injected client deliberately implements
    # them, keeping this wrapper free of provider/OAuth construction.
    def __getattr__(self, name: str) -> Any:
        if name in {
            "list_children", "walk_tree", "read_bytes", "copy_folder_tree",
            "create_folder", "trash_safe_copy",
        }:
            method = getattr(self.client, name, None)
            if callable(method):
                return method
            raise ExactPreconditionUnavailable("unsupported_storage_operation")
        raise AttributeError(name)


class GoogleDriveExactMutationAdapter(ExactMutationAdapter):
    def __init__(self, client: ConditionalMutationClient, *, enabled: bool = False):
        super().__init__(client, provider="google_drive", enabled=enabled)


class YandexDiskExactMutationAdapter(ExactMutationAdapter):
    def __init__(self, client: ConditionalMutationClient, *, enabled: bool = False):
        super().__init__(client, provider="yandex_disk", enabled=enabled)
