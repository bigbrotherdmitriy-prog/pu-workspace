"""Google Drive conditional mutation port for MVP-1.

The provider version and HTTP ETag are sealed into one opaque revision token.
Mutations use If-Match and are never retried.  A missing ETag or 412 response is
fail-closed as ``conflict_source_changed``.
"""
from __future__ import annotations

import base64
import json

from googleapiclient.errors import HttpError

from app.integrations.contracts import StorageUnavailable
from app.integrations.storage_mutation_live import (
    ExactPreconditionUnavailable, ProviderObjectState,
)


def _pack(version: str, etag: str) -> str:
    raw = json.dumps({"v": version, "e": etag}, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unpack(token: str) -> tuple[str, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if set(data) != {"v", "e"} or not all(isinstance(data[key], str) and data[key] for key in data):
            raise ValueError
        return data["v"], data["e"]
    except Exception:
        raise ExactPreconditionUnavailable("invalid_provider_revision") from None


class GoogleDriveConditionalMutationClient:
    provider = "google_drive"
    supports_exact_mutation_preconditions = True
    _FIELDS = "id,name,mimeType,parents,version,trashed"

    def __init__(self, service, *, max_ancestor_depth: int = 100):
        self.service = service
        self.max_ancestor_depth = max_ancestor_depth

    @staticmethod
    def _execute(request):
        try:
            payload = request.execute()
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0) or 0)
            if status in {409, 412}:
                raise ExactPreconditionUnavailable("conflict_source_changed") from None
            raise StorageUnavailable("google_drive_mutation_unavailable") from None
        return payload, getattr(request, "resp", None)

    @staticmethod
    def _etag(response) -> str:
        etag = None if response is None else response.get("etag") or response.get("ETag")
        if not isinstance(etag, str) or not etag:
            raise ExactPreconditionUnavailable("exact_provider_etag_unavailable")
        return etag

    def _metadata(self, object_id: str) -> tuple[dict, str]:
        request = self.service.files().get(
            fileId=object_id, fields=self._FIELDS, supportsAllDrives=True,
        )
        payload, response = self._execute(request)
        if payload.get("trashed") or not payload.get("id") or not payload.get("version"):
            raise ExactPreconditionUnavailable("conflict_source_changed")
        return payload, self._etag(response)

    def get_exact_state(self, object_id: str) -> ProviderObjectState:
        meta, etag = self._metadata(object_id)
        parents = meta.get("parents") or []
        parent_id = str(parents[0]) if parents else "root"
        ancestors: list[str] = []
        seen = {object_id}
        current = parent_id
        while current and current != "root":
            if current in seen or len(ancestors) >= self.max_ancestor_depth:
                raise ExactPreconditionUnavailable("invalid_provider_ancestry")
            seen.add(current); ancestors.append(current)
            parent, _ = self._metadata(current)
            values = parent.get("parents") or []
            current = str(values[0]) if values else "root"
        return ProviderObjectState(
            object_id=str(meta["id"]), name=str(meta["name"]), parent_id=parent_id,
            revision=_pack(str(meta["version"]), etag), ancestor_ids=tuple(ancestors),
            mime_type=str(meta.get("mimeType") or "application/octet-stream"),
            object_type="folder" if meta.get("mimeType") == "application/vnd.google-apps.folder" else "file",
        )

    def _update(self, object_id: str, *, expected_revision: str, body: dict,
                add_parents: str | None = None, remove_parents: str | None = None) -> ProviderObjectState:
        expected_version, etag = _unpack(expected_revision)
        current, current_etag = self._metadata(object_id)
        if str(current["version"]) != expected_version or current_etag != etag:
            raise ExactPreconditionUnavailable("conflict_source_changed")
        kwargs = dict(fileId=object_id, body=body, fields=self._FIELDS, supportsAllDrives=True)
        if add_parents is not None: kwargs["addParents"] = add_parents
        if remove_parents is not None: kwargs["removeParents"] = remove_parents
        request = self.service.files().update(**kwargs)
        request.headers["If-Match"] = etag
        self._execute(request)
        return self.get_exact_state(object_id)

    def rename_if_revision(self, object_id: str, new_name: str, *, expected_revision: str,
                           operation_key: str) -> ProviderObjectState:
        del operation_key
        return self._update(object_id, expected_revision=expected_revision, body={"name": new_name})

    def move_if_revision(self, object_id: str, new_parent_id: str, *, expected_parent_id: str,
                         expected_revision: str, operation_key: str) -> ProviderObjectState:
        del operation_key
        return self._update(object_id, expected_revision=expected_revision, body={},
                            add_parents=new_parent_id, remove_parents=expected_parent_id)
