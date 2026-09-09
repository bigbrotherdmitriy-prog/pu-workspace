from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import io
import os
import time
from typing import Any, Callable

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from app.integrations.contracts import (
    AIProviderAdapter, AdapterHealth, StorageAccessDenied, StorageCopyResult, StorageCredentialsExpired,
    StorageRateLimited, StorageUnavailable,
)
from app.ocr_quality.routing import ExtractionPolicy, Mode, route_extraction

from .config import MAX_FILES_PER_SCAN, SAFE_COPY_SUFFIX
from .types import DriveFile, FOLDER_MIME


MAX_CONTENT_BYTES = int(os.getenv("ORGANIZER_MAX_CONTENT_BYTES", str(4 * 1024 * 1024)))
GOOGLE_NATIVE_EXPORTS = {
    "application/vnd.google-apps.document": frozenset({
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/pdf",
    }),
    "application/vnd.google-apps.spreadsheet": frozenset({
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
    }),
    "application/vnd.google-apps.presentation": frozenset({
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/pdf",
    }),
}
# Text-oriented defaults used only by the legacy analyzer. Exact native export
# callers must select an explicit MIME from GOOGLE_NATIVE_EXPORTS.
GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": "application/pdf",
    "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.google-apps.presentation": "application/pdf",
}
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
READ_RETRY_DELAYS = (0.1, 0.25)


class UnsafeDriveMutation(RuntimeError):
    pass


class CopyLimitExceeded(RuntimeError):
    pass


CopyResult = StorageCopyResult


@dataclass(frozen=True, slots=True)
class NativeExportBytes:
    object_id: str
    original_revision: str
    original_mime_type: str
    export_mime_type: str
    content: bytes
    sha256: str
    exported_at: datetime


class DriveClient:
    """Google Drive wrapper with hard COPY-only safety checks.

    The caller provides an authenticated Drive API v3 service. Any mutating
    operation on an existing file must pass copy_root_id and is rejected when
    the file is outside the safe-copy tree.
    """

    provider = "google_drive"
    supports_managed_copy_idempotency = True
    supports_managed_copy_cleanup = True
    supports_exact_native_export = True

    def read_native_export_exact(self, object_id: str, *, revision: str,
                                 mime_type: str, max_bytes: int) -> NativeExportBytes:
        """Export a native file only while its monotonic Drive version is pinned.

        Drive export has no revision argument.  The v3 ``version`` value is
        monotonic, so equal reads immediately before and after the bounded
        export prove that no intervening provider mutation occurred.  Any
        mismatch is rejected; callers never receive unpinned bytes.
        """
        if not object_id or not revision or mime_type not in {
                value for values in GOOGLE_NATIVE_EXPORTS.values() for value in values}:
            raise StorageUnavailable("native_export_request_invalid")
        if type(max_bytes) is not int or not 0 < max_bytes <= 32 * 1024 * 1024:
            raise StorageUnavailable("native_export_request_invalid")
        before = self.get_file_meta(object_id)
        allowed = GOOGLE_NATIVE_EXPORTS.get(before.mime_type)
        if before.provider_revision != revision or not allowed or mime_type not in allowed:
            raise StorageUnavailable("native_export_source_conflict")
        request = self.service.files().export_media(fileId=object_id, mimeType=mime_type)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request, chunksize=min(1024 * 1024, max_bytes + 1))
        done = False
        while not done and buffer.tell() <= max_bytes:
            _, done = downloader.next_chunk()
        content = buffer.getvalue()
        if len(content) > max_bytes:
            raise StorageUnavailable("native_export_too_large")
        after = self.get_file_meta(object_id)
        if (after.provider_revision != revision or after.mime_type != before.mime_type
                or after.id != before.id):
            raise StorageUnavailable("native_export_source_conflict")
        return NativeExportBytes(
            object_id=object_id, original_revision=revision,
            original_mime_type=before.mime_type, export_mime_type=mime_type,
            content=content, sha256=sha256(content).hexdigest(),
            exported_at=datetime.now(timezone.utc),
        )

    # Drive API v3 exposes an output-only monotonically increasing ``version``,
    # but files.update does not accept that value (or a resource ETag) as an
    # atomic mutation precondition.  Read-before-write is not sufficient for
    # rename/move, so the exact-mutation wrapper must reject this client.
    supports_exact_mutation_preconditions = False
    exact_mutation_blocker = "drive_v3_update_has_no_exact_revision_precondition"

    def __init__(self, service: Any, *, sleep: Callable[[float], None] = time.sleep,
                 extraction_mode: Mode = "auto", extraction_policy: ExtractionPolicy | None = None,
                 ai_adapter: AIProviderAdapter | None = None):
        self.service = service
        self._sleep = sleep
        self._extraction_mode = extraction_mode
        self._extraction_policy = extraction_policy or ExtractionPolicy()
        self._ai_adapter = ai_adapter

    def _execute_read(self, request):
        """Retry only side-effect-free provider reads; never replay mutations."""
        for attempt in range(len(READ_RETRY_DELAYS) + 1):
            try:
                return request.execute()
            except HttpError as exc:
                status = int(getattr(exc.resp, "status", 0) or 0)
                content = getattr(exc, "content", b"") or b""
                if isinstance(content, str):
                    content = content.encode("utf-8", errors="ignore")
                rate_limited = status == 429 or (
                    status == 403
                    and any(reason in content for reason in (
                        b"rateLimitExceeded", b"userRateLimitExceeded",
                    ))
                )
                retryable = rate_limited or status >= 500
                if retryable and attempt < len(READ_RETRY_DELAYS):
                    self._sleep(READ_RETRY_DELAYS[attempt])
                    continue
                if status == 401:
                    raise StorageCredentialsExpired("Google Drive credentials expired or were revoked") from exc
                if rate_limited:
                    raise StorageRateLimited("Google Drive rate limit exceeded") from exc
                if status == 403:
                    raise StorageAccessDenied("Google Drive object access is denied") from exc
                raise StorageUnavailable("Google Drive is temporarily unavailable") from exc

    def health(self) -> AdapterHealth:
        return AdapterHealth(ready=self.service is not None, detail="service configured")

    @staticmethod
    def _to_file(meta: dict, fallback_parent: str = "") -> DriveFile:
        parent_ids = tuple(str(value) for value in (meta.get("parents") or ([fallback_parent] if fallback_parent else [])))
        capabilities = meta.get("capabilities") or {}
        acl_state = (
            "write" if capabilities.get("canEdit") is True
            else "read" if capabilities.get("canDownload") is True
            else "unknown"
        )
        shortcut = meta.get("shortcutDetails") or {}
        return DriveFile(
            id=meta["id"],
            name=meta["name"],
            mime_type=meta["mimeType"],
            parent_id=(meta.get("parents") or [fallback_parent or ""])[0],
            md5_checksum=meta.get("md5Checksum"),
            size=int(meta["size"]) if meta.get("size") else None,
            modified_time=meta.get("modifiedTime"),
            object_type=("folder" if meta["mimeType"] == FOLDER_MIME
                         else "shortcut" if meta["mimeType"] == SHORTCUT_MIME else "file"),
            provider="google_drive",
            parent_ids=parent_ids,
            provider_revision=str(meta["version"]) if meta.get("version") is not None else None,
            web_url=meta.get("webViewLink"),
            availability="available",
            acl_state=acl_state,
            provider_metadata={"drive_id": meta.get("driveId"), "resource_key": meta.get("resourceKey"),
                               "app_properties": dict(meta.get("appProperties") or {})},
            shortcut_target_id=shortcut.get("targetId"),
            shortcut_target_mime_type=shortcut.get("targetMimeType"),
            shortcut_target_resource_key=shortcut.get("targetResourceKey"),
        )

    def get_object(self, object_id: str) -> DriveFile:
        return self.get_file_meta(object_id)

    def get_file_meta(self, file_id: str) -> DriveFile:
        request = self.service.files().get(
            fileId=file_id,
            fields=("id,name,mimeType,parents,md5Checksum,size,modifiedTime,version,"
                    "webViewLink,driveId,resourceKey,appProperties,capabilities(canDownload,canEdit),"
                    "shortcutDetails(targetId,targetMimeType,targetResourceKey),trashed"),
            supportsAllDrives=True,
        )
        meta = self._execute_read(request)
        if meta.get("trashed"):
            raise ValueError("Google Drive object is in trash")
        return self._to_file(meta)

    def list_children(self, folder_id: str) -> list[DriveFile]:
        out: list[DriveFile] = []
        page_token = None
        while True:
            request = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields=("nextPageToken, files(id,name,mimeType,parents,md5Checksum,size,modifiedTime,"
                        "version,webViewLink,driveId,resourceKey,appProperties,capabilities(canDownload,canEdit),"
                        "shortcutDetails(targetId,targetMimeType,targetResourceKey))"),
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            resp = self._execute_read(request)
            out.extend(self._to_file(x, folder_id) for x in resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def populate_content(
        self,
        items: list[DriveFile],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> tuple[int, int]:
        extracted = 0
        failed = 0
        total = len(items)
        for processed, item in enumerate(items, start=1):
            if item.is_folder or (item.size is not None and item.size > MAX_CONTENT_BYTES):
                if on_progress:
                    on_progress(processed, total)
                continue
            try:
                export_mime = GOOGLE_EXPORTS.get(item.mime_type)
                request = (
                    self.service.files().export_media(fileId=item.id, mimeType=export_mime)
                    if export_mime
                    else self.service.files().get_media(fileId=item.id)
                )
                buffer = io.BytesIO()
                downloader = MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
                done = False
                while not done and buffer.tell() <= MAX_CONTENT_BYTES:
                    _, done = downloader.next_chunk()
                if buffer.tell() > MAX_CONTENT_BYTES:
                    if on_progress:
                        on_progress(processed, total)
                    continue
                routed = route_extraction(
                    buffer.getvalue(), export_mime or item.mime_type, item.name,
                    mode=self._extraction_mode, policy=self._extraction_policy,
                    adapter=self._ai_adapter,
                )
                item.content_text = routed.result.text
                metadata = dict(item.provider_metadata or {})
                metadata["extraction_method"] = routed.result.method
                if routed.incomplete_reason:
                    metadata["extraction_incomplete_reason"] = routed.incomplete_reason
                item.provider_metadata = metadata
                if item.content_text:
                    extracted += 1
            except Exception:
                failed += 1
            if on_progress:
                on_progress(processed, total)
        return extracted, failed

    def read_bytes(self, object_id: str, max_bytes: int = MAX_CONTENT_BYTES) -> tuple[bytes, str]:
        """Read one object through the storage-adapter boundary without mutating it."""
        item = self.get_file_meta(object_id)
        if item.is_folder:
            raise ValueError("Storage object is a folder")
        if item.size is not None and item.size > max_bytes:
            raise ValueError(f"Storage object exceeds {max_bytes} bytes")
        export_mime = GOOGLE_EXPORTS.get(item.mime_type)
        request = (
            self.service.files().export_media(fileId=item.id, mimeType=export_mime)
            if export_mime else self.service.files().get_media(fileId=item.id)
        )
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
        done = False
        while not done and buffer.tell() <= max_bytes:
            _, done = downloader.next_chunk()
        if buffer.tell() > max_bytes:
            raise ValueError(f"Storage object exceeds {max_bytes} bytes")
        return buffer.getvalue(), export_mime or item.mime_type

    def walk_tree(self, root_folder_id: str, limit: int = MAX_FILES_PER_SCAN) -> list[DriveFile]:
        out: list[DriveFile] = []
        queue: deque[str] = deque([root_folder_id])
        folder_count = 0
        while queue:
            current = queue.popleft()
            folder_count += 1
            if folder_count % 100 == 0:
                print(f"[DRIVE WALK] folders={folder_count} items={len(out)} queue={len(queue)}", flush=True)
            for item in self.list_children(current):
                out.append(item)
                if len(out) > limit:
                    raise CopyLimitExceeded(
                        f"Folder contains more than {limit} items. Safe copy was not started."
                    )
                if item.is_folder:
                    queue.append(item.id)
        return out

    def create_folder(self, name: str, parent_id: str, *, app_properties: dict[str, str] | None = None) -> str:
        body: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        if app_properties:
            body["appProperties"] = app_properties
        created = self.service.files().create(
            body=body,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        return created["id"]

    def copy_file(self, file_id: str, new_parent_id: str, new_name: str | None = None,
                  *, app_properties: dict[str, str] | None = None) -> str:
        body: dict[str, Any] = {"parents": [new_parent_id]}
        if new_name:
            body["name"] = new_name
        if app_properties:
            body["appProperties"] = app_properties
        copied = self.service.files().copy(
            fileId=file_id, body=body, fields="id", supportsAllDrives=True,
        ).execute()
        return copied["id"]

    def _parent_chain_contains(self, file_id: str, ancestor_id: str, max_depth: int = 100) -> bool:
        if file_id == ancestor_id:
            return True
        current = file_id
        visited: set[str] = set()
        for _ in range(max_depth):
            if current in visited:
                return False
            visited.add(current)
            meta = self.get_file_meta(current)
            parent = meta.parent_id
            if not parent:
                return False
            if parent == ancestor_id:
                return True
            current = parent
        return False

    def assert_inside_copy(self, file_id: str, copy_root_id: str) -> None:
        if not self._parent_chain_contains(file_id, copy_root_id):
            raise UnsafeDriveMutation(
                f"Blocked mutation: file {file_id} is outside safe copy {copy_root_id}."
            )

    def rename_file(self, file_id: str, new_name: str, copy_root_id: str) -> None:
        self.assert_inside_copy(file_id, copy_root_id)
        self.service.files().update(fileId=file_id, body={"name": new_name}, fields="id,name").execute()

    def move_file(self, file_id: str, new_parent_id: str, old_parent_id: str, copy_root_id: str) -> None:
        self.assert_inside_copy(file_id, copy_root_id)
        self.assert_inside_copy(new_parent_id, copy_root_id)
        self.service.files().update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=old_parent_id,
            fields="id,parents",
        ).execute()

    def trash_safe_copy(self, copy_root_id: str) -> None:
        """Move an explicitly identified PU safe-copy root to Drive trash."""
        meta = self.get_file_meta(copy_root_id)
        if not meta.is_folder:
            raise UnsafeDriveMutation("Safe copy root must be a folder")
        self.service.files().update(
            fileId=copy_root_id,
            body={"trashed": True},
            fields="id,trashed",
            supportsAllDrives=True,
        ).execute()

    def trash_managed_copy(self, copy_root_id: str, ownership_key: str) -> None:
        """Trash only a complete subtree carrying the same opaque ownership key."""
        if not ownership_key.startswith("managed-") or len(ownership_key) != 40:
            raise UnsafeDriveMutation("managed_copy_ownership_invalid")
        root = self.get_file_meta(copy_root_id)
        marker = (root.provider_metadata.get("app_properties") or {}).get("puManagedCopyKey")
        if not root.is_folder or marker != ownership_key:
            raise UnsafeDriveMutation("managed_copy_ownership_mismatch")
        for item in self.walk_tree(copy_root_id, MAX_FILES_PER_SCAN):
            item_marker = (item.provider_metadata.get("app_properties") or {}).get("puManagedCopyKey")
            if item_marker != ownership_key:
                raise UnsafeDriveMutation("managed_copy_contains_unowned_object")
        self.trash_safe_copy(copy_root_id)

    def copy_folder_tree(self, source_folder_id: str, new_parent_id: str, source_name: str, source_items: list[DriveFile] | None = None, *, idempotency_key: str | None = None) -> CopyResult:
        # Reuse the scan we already performed in organizer.py.
        # This avoids walking the whole Google Drive a second time.
        if source_items is None:
            source_items = self.walk_tree(source_folder_id, MAX_FILES_PER_SCAN)

        if len(source_items) > MAX_FILES_PER_SCAN:
            raise CopyLimitExceeded(
                f"Folder contains more than {MAX_FILES_PER_SCAN} items. Safe copy was not started."
            )

        # Build the source tree from metadata already fetched during the scan.
        # This avoids calling list_children() again for every source folder.
        children_by_parent: dict[str, list[DriveFile]] = {}
        for item in source_items:
            children_by_parent.setdefault(item.parent_id, []).append(item)
        managed = bool(idempotency_key and idempotency_key.startswith("managed-") and len(idempotency_key) == 40)
        ts = idempotency_key or datetime.now(timezone.utc).strftime("%Y-%m-%d %H-%M-%S UTC")
        copy_name = source_name + SAFE_COPY_SUFFIX.format(ts=ts)
        ownership = {"puManagedCopyKey": idempotency_key} if managed else None
        if idempotency_key:
            existing = next((item for item in self.list_children(new_parent_id or "root")
                if item.is_folder and (
                    (item.provider_metadata.get("app_properties") or {}).get("puManagedCopyKey") == idempotency_key
                    if managed else item.name == copy_name)), None)
            if existing:
                root_copy_id = existing.id
            else:
                root_copy_id = self.create_folder(copy_name, new_parent_id or "root", app_properties=ownership)
        else:
            root_copy_id = self.create_folder(copy_name, new_parent_id or "root")
        id_map: dict[str, str] = {source_folder_id: root_copy_id}
        queue: deque[str] = deque([source_folder_id])
        try:
            copied_count = 0
            while queue:
                current_source = queue.popleft()
                current_copy = id_map[current_source]
                existing_by_source = {}
                if managed:
                    existing_by_source = {
                        (child.provider_metadata.get("app_properties") or {}).get("puManagedSourceId"): child
                        for child in self.list_children(current_copy)
                        if (child.provider_metadata.get("app_properties") or {}).get("puManagedCopyKey") == idempotency_key
                    }

                for item in children_by_parent.get(current_source, []):
                    copied_count += 1
                    if copied_count % 100 == 0:
                        print(
                            f"[DRIVE COPY] copied={copied_count}/{len(source_items)} "
                            f"folders_pending={len(queue)}",
                            flush=True,
                        )
                    prior = existing_by_source.get(item.id)
                    if prior is not None:
                        id_map[item.id] = prior.id
                        if item.is_folder:
                            queue.append(item.id)
                        continue
                    child_properties = ({"puManagedCopyKey": idempotency_key,
                                         "puManagedSourceId": item.id} if managed else None)
                    if item.is_folder:
                        new_id = self.create_folder(item.name, current_copy, app_properties=child_properties)
                        id_map[item.id] = new_id
                        queue.append(item.id)
                    else:
                        id_map[item.id] = self.copy_file(item.id, current_copy, app_properties=child_properties)
        except Exception:
            # We intentionally do not trash/delete the partial copy automatically:
            # retaining evidence is safer. The session is marked failed by service layer.
            raise
        return CopyResult(root_copy_id, copy_name, id_map, len(source_items))
