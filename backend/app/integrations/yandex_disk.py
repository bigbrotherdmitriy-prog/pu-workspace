from __future__ import annotations

from collections import deque
import hashlib
from pathlib import PurePosixPath
from typing import Any

import httpx

from app.core.integration_types import StorageObject
from app.integrations.contracts import (
    AdapterHealth,
    StorageAccessDenied,
    StorageCredentialsExpired,
    StorageQuotaExceeded,
    StorageRateLimited,
    StorageUnavailable,
    StorageCopyResult,
)
from app.organizer_engine.content import extract_text


YANDEX_DISK_API = "https://cloud-api.yandex.net/v1/disk"
FOLDER_MIME = "inode/directory"


class YandexDiskStorageAdapter:
    """Yandex Disk implementation of the provider-neutral storage boundary."""

    provider = "yandex_disk"

    def __init__(self, access_token: str, *, client: httpx.Client | None = None):
        self._token = access_token
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True)
        self._current_paths: dict[str, str] = {}

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"OAuth {self._token}"}

    @staticmethod
    def normalize_locator(locator: str | None) -> str:
        value = (locator or "disk:/").strip()
        if value in {"root", "/", "disk:"}:
            return "disk:/"
        return value if value.startswith(("disk:/", "app:/")) else f"disk:/{value.lstrip('/')}"

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            response = self.client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise StorageUnavailable("Yandex Disk is unavailable") from exc
        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in {401}:
            raise StorageCredentialsExpired("Yandex credentials expired or were revoked")
        if response.status_code in {403}:
            raise StorageAccessDenied("Access to Yandex Disk object is denied")
        if response.status_code == 429:
            raise StorageRateLimited("Yandex Disk rate limit exceeded")
        if response.status_code in {413, 507}:
            raise StorageQuotaExceeded("Yandex Disk quota exceeded")
        if response.status_code >= 500:
            raise StorageUnavailable("Yandex Disk is temporarily unavailable")
        response.raise_for_status()

    @staticmethod
    def _to_object(meta: dict[str, Any], fallback_parent: str = "") -> StorageObject:
        path = meta.get("path") or meta.get("resource_id") or ""
        namespace = "app:" if path.startswith("app:/") else "disk:"
        parent = str(PurePosixPath(path.removeprefix(namespace))).rsplit("/", 1)[0] or "/"
        parent_locator = fallback_parent or f"{namespace}{parent}"
        return StorageObject(
            id=path,
            name=meta.get("name") or PurePosixPath(path).name,
            mime_type=FOLDER_MIME if meta.get("type") == "dir" else (meta.get("mime_type") or "application/octet-stream"),
            parent_id=parent_locator,
            md5_checksum=meta.get("md5"),
            size=meta.get("size"),
            modified_time=meta.get("modified"),
            object_type="folder" if meta.get("type") == "dir" else "file",
            provider="yandex_disk",
        )

    def health(self) -> AdapterHealth:
        try:
            data = self._request("GET", YANDEX_DISK_API, params={"fields": "user.login"}).json()
            login = ((data.get("user") or {}).get("login") or "connected")
            return AdapterHealth(True, login)
        except Exception as exc:
            return AdapterHealth(False, str(exc))

    def user_info(self) -> dict[str, Any]:
        return self._request("GET", YANDEX_DISK_API, params={"fields": "user,total_space,used_space"}).json()

    def get_object(self, object_id: str) -> StorageObject:
        path = self.normalize_locator(self._current_paths.get(object_id, object_id))
        meta = self._request("GET", f"{YANDEX_DISK_API}/resources", params={"path": path}).json()
        item = self._to_object(meta)
        if object_id != path:
            item.id = object_id
        return item

    def get_file_meta(self, object_id: str) -> StorageObject:
        return self.get_object(object_id)

    def list_children(self, folder_id: str) -> list[StorageObject]:
        path = self.normalize_locator(folder_id)
        result: list[StorageObject] = []
        offset = 0
        page_size = 100
        while True:
            payload = self._request(
                "GET", f"{YANDEX_DISK_API}/resources",
                params={"path": path, "limit": page_size, "offset": offset, "fields": "_embedded.items,_embedded.total"},
            ).json()
            embedded = payload.get("_embedded") or {}
            items = embedded.get("items") or []
            result.extend(self._to_object(item, path) for item in items)
            offset += len(items)
            if not items or offset >= int(embedded.get("total") or offset):
                return result

    def walk_tree(self, root_folder_id: str, limit: int = 10_000) -> list[StorageObject]:
        result: list[StorageObject] = []
        queue: deque[str] = deque([self.normalize_locator(root_folder_id)])
        while queue:
            current = queue.popleft()
            for item in self.list_children(current):
                result.append(item)
                if len(result) > limit:
                    raise StorageQuotaExceeded(f"Folder contains more than {limit} objects")
                if item.is_folder:
                    queue.append(item.id)
        return result

    def read_bytes(self, object_id: str, max_bytes: int) -> tuple[bytes, str]:
        item = self.get_object(object_id)
        if item.is_folder:
            raise ValueError("Storage object is a folder")
        if item.size is not None and item.size > max_bytes:
            raise ValueError(f"Storage object exceeds {max_bytes} bytes")
        href = self._request(
            "GET", f"{YANDEX_DISK_API}/resources/download", params={"path": self.normalize_locator(self._current_paths.get(object_id, object_id))}
        ).json()["href"]
        response = self._request("GET", href)
        if len(response.content) > max_bytes:
            raise ValueError(f"Storage object exceeds {max_bytes} bytes")
        return response.content, item.mime_type

    def populate_content(self, items: list[StorageObject], on_progress=None) -> tuple[int, int]:
        extracted = failed = 0
        total = len(items)
        for processed, item in enumerate(items, start=1):
            if not item.is_folder:
                try:
                    raw, mime = self.read_bytes(item.id, 4 * 1024 * 1024)
                    item.content_text = extract_text(raw, mime, item.name)
                    extracted += int(bool(item.content_text))
                except Exception:
                    failed += 1
            if on_progress:
                on_progress(processed, total)
        return extracted, failed

    def create_folder(self, name: str, parent_id: str | None = None) -> str:
        path = f"{self.normalize_locator(parent_id).rstrip('/')}/{name}" if parent_id is not None else name
        normalized = self.normalize_locator(path)
        response = self.client.request("PUT", f"{YANDEX_DISK_API}/resources", headers=self._headers, params={"path": normalized})
        if response.status_code == 409:  # idempotent: folder already exists
            return normalized
        self._raise_for_status(response)
        return normalized

    def copy_folder_tree(
        self,
        source_folder_id: str,
        new_parent_id: str,
        source_name: str,
        source_items: list[StorageObject] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> StorageCopyResult:
        """Create a deterministic safe copy; original paths are never mutated."""
        source = self.normalize_locator(source_folder_id)
        parent = self.normalize_locator(new_parent_id)
        key = idempotency_key or hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
        copy_root = f"{parent.rstrip('/')}/{source_name} — PU safe copy {key}"
        self.create_folder(copy_root)
        items = source_items if source_items is not None else self.walk_tree(source)
        id_map: dict[str, str] = {source: copy_root}
        for item in sorted(items, key=lambda obj: (0 if obj.is_folder else 1, obj.id.count("/"))):
            relative = item.id.removeprefix(source).lstrip("/")
            destination = f"{copy_root.rstrip('/')}/{relative}"
            if item.is_folder:
                self.create_folder(destination)
            else:
                response = self.client.request(
                    "POST", f"{YANDEX_DISK_API}/resources/copy", headers=self._headers,
                    params={"from": item.id, "path": destination, "overwrite": "false"},
                )
                if response.status_code != 409:
                    self._raise_for_status(response)
            id_map[item.id] = destination
        return StorageCopyResult(copy_root, PurePosixPath(copy_root).name, id_map, len(items))

    @staticmethod
    def _assert_inside_copy(path: str, copy_root_id: str) -> None:
        if not path.startswith(copy_root_id.rstrip("/") + "/"):
            raise StorageAccessDenied("Mutation outside the Yandex safe copy is blocked")

    def assert_inside_copy(self, path: str, copy_root_id: str) -> None:
        self._assert_inside_copy(self._current_paths.get(path, path), copy_root_id)

    def rename_file(self, file_id: str, new_name: str, copy_root_id: str) -> None:
        current = self._current_paths.get(file_id, file_id)
        self._assert_inside_copy(current, copy_root_id)
        destination = f"{current.rsplit('/', 1)[0]}/{new_name}"
        self._request("POST", f"{YANDEX_DISK_API}/resources/move", params={"from": current, "path": destination, "overwrite": "false"})
        self._current_paths[file_id] = destination

    def move_file(self, file_id: str, new_parent_id: str, old_parent_id: str, copy_root_id: str) -> None:
        current = self._current_paths.get(file_id, file_id)
        self._assert_inside_copy(current, copy_root_id)
        self._assert_inside_copy(new_parent_id.rstrip("/") + "/placeholder", copy_root_id)
        destination = f"{new_parent_id.rstrip('/')}/{PurePosixPath(current).name}"
        self._request("POST", f"{YANDEX_DISK_API}/resources/move", params={"from": current, "path": destination, "overwrite": "false"})
        self._current_paths[file_id] = destination

    def trash_safe_copy(self, copy_root_id: str) -> None:
        self._request("DELETE", f"{YANDEX_DISK_API}/resources", params={"path": copy_root_id, "permanently": "false"})
