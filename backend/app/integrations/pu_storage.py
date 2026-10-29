from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

from app.core.integration_types import StorageObject
from app.integrations.contracts import (
    AdapterHealth,
    StorageAccessDenied,
    StorageCopyResult,
    StorageQuotaExceeded,
    StorageUnavailable,
)

FOLDER_MIME = "inode/directory"
DEFAULT_QUOTA_MB = 2048
SERVICE_DIRS = {".trash"}


def _default_root() -> Path:
    raw = os.environ.get("PU_STORAGE_ROOT")
    if raw:
        return Path(raw).expanduser()
    volume = os.environ.get("PU_LOCAL_UPLOAD_STAGING_ROOT")
    if volume:
        return Path(volume) / "pu-storage"
    return Path("/data/pu-storage")


def _default_quota_bytes() -> int:
    raw = os.environ.get("PU_STORAGE_QUOTA_MB")
    try:
        mb = int(raw) if raw is not None else DEFAULT_QUOTA_MB
    except ValueError:
        mb = DEFAULT_QUOTA_MB
    if mb <= 0:
        mb = DEFAULT_QUOTA_MB
    return mb * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_name(name: str) -> str:
    if not name:
        raise StorageAccessDenied("Empty name is not allowed")
    cleaned_chars: list[str] = []
    for ch in str(name):
        code = ord(ch)
        if ch in {"/", "\\"}:
            continue
        if code < 32:
            continue
        cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars).strip()
    if cleaned in {"", ".", ".."}:
        raise StorageAccessDenied("Invalid name")
    if "\x00" in cleaned:
        raise StorageAccessDenied("Invalid name")
    return cleaned


def _mime_for(path: Path, is_dir: bool) -> str:
    if is_dir:
        return FOLDER_MIME
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _md5_of(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PuStorageAdapter:
    """Filesystem-backed storage provider for projects without external cloud."""

    provider = "pu_storage"

    def __init__(
        self,
        project_id: int,
        root: Path | None = None,
        quota_bytes: int | None = None,
    ) -> None:
        self.project_id = int(project_id)
        base = Path(root) if root is not None else _default_root()
        self._root_parent = base
        self._project_root = (base / str(self.project_id))
        self.quota_bytes = int(quota_bytes) if quota_bytes is not None else _default_quota_bytes()

    # ------------------------------------------------------------------ paths
    def _ensure_project_root(self) -> Path:
        try:
            self._project_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageUnavailable("Cannot create project storage directory") from exc
        resolved = self._project_root.resolve()
        parent_resolved = self._root_parent.resolve() if self._root_parent.exists() else self._root_parent
        try:
            resolved.relative_to(parent_resolved)
        except ValueError:
            # A symlinked project root pointing outside the storage parent.
            raise StorageAccessDenied("Project storage root is outside of PU storage")
        return resolved

    def _normalize_id(self, object_id: str | None) -> str:
        raw = (object_id or "pu:/").strip()
        if not raw.startswith("pu:/"):
            raise StorageAccessDenied("Unknown PU storage locator")
        if "\x00" in raw:
            raise StorageAccessDenied("Null byte in locator")
        path_part = raw[len("pu:/"):]
        parts = PurePosixPath(path_part).parts if path_part else ()
        cleaned: list[str] = []
        for part in parts:
            if part in {"", "."}:
                continue
            if part == "..":
                raise StorageAccessDenied("Parent navigation is not allowed")
            if "/" in part or "\\" in part:
                raise StorageAccessDenied("Invalid path segment")
            if part.startswith(".") and part in SERVICE_DIRS:
                raise StorageAccessDenied("Service directory is hidden")
            cleaned.append(part)
        if not cleaned:
            return "pu:/"
        return "pu:/" + "/".join(cleaned)

    def _resolve(self, object_id: str | None) -> Path:
        normalized = self._normalize_id(object_id)
        project_root = self._ensure_project_root()
        rel = normalized[len("pu:/"):]
        candidate = project_root / rel if rel else project_root
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise StorageUnavailable("Cannot resolve path") from exc
        try:
            resolved.relative_to(project_root)
        except ValueError:
            raise StorageAccessDenied("Path escapes project root")
        return resolved

    def _id_of(self, path: Path) -> str:
        project_root = self._ensure_project_root()
        try:
            rel = path.resolve().relative_to(project_root)
        except ValueError:
            raise StorageAccessDenied("Path escapes project root")
        if not rel.parts:
            return "pu:/"
        return "pu:/" + "/".join(rel.parts)

    def _parent_id(self, path: Path) -> str:
        project_root = self._ensure_project_root()
        try:
            rel = path.resolve().relative_to(project_root)
        except ValueError:
            raise StorageAccessDenied("Path escapes project root")
        if not rel.parts or len(rel.parts) == 1:
            return "pu:/"
        return "pu:/" + "/".join(rel.parts[:-1])

    def _to_object(self, path: Path) -> StorageObject:
        resolved = path.resolve()
        is_dir = resolved.is_dir()
        stat = resolved.stat()
        mime = _mime_for(resolved, is_dir)
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return StorageObject(
            id=self._id_of(resolved),
            name=resolved.name,
            mime_type=mime,
            parent_id=self._parent_id(resolved),
            md5_checksum=None if is_dir else _md5_of(resolved),
            size=None if is_dir else stat.st_size,
            modified_time=modified,
            object_type="folder" if is_dir else "file",
            provider="pu_storage",
            parent_ids=(self._parent_id(resolved),),
            provider_revision=str(stat.st_mtime_ns),
            source_path=str(resolved),
            availability="available",
            acl_state="private",
            provider_metadata=None,
        )

    # ------------------------------------------------------------- provider
    def health(self) -> AdapterHealth:
        try:
            root = self._ensure_project_root()
            return AdapterHealth(True, str(root))
        except Exception as exc:  # noqa: BLE001
            return AdapterHealth(False, str(exc))

    def get_object(self, object_id: str) -> StorageObject:
        path = self._resolve(object_id)
        if not path.exists():
            raise FileNotFoundError(object_id)
        return self._to_object(path)

    def get_file_meta(self, object_id: str) -> StorageObject:
        return self.get_object(object_id)

    def list_children(self, folder_id: str) -> list[StorageObject]:
        path = self._resolve(folder_id)
        if not path.exists():
            raise FileNotFoundError(folder_id)
        if not path.is_dir():
            raise LookupError("Not a folder")
        entries: list[StorageObject] = []
        for child in path.iterdir():
            if child.name in SERVICE_DIRS:
                continue
            try:
                entries.append(self._to_object(child))
            except OSError:
                continue
        entries.sort(key=lambda obj: (0 if obj.is_folder else 1, obj.name.lower()))
        return entries

    def walk_tree(self, root_folder_id: str, limit: int = 10_000) -> list[StorageObject]:
        normalized = self._normalize_id(root_folder_id)
        queue: deque[str] = deque([normalized])
        collected: list[StorageObject] = []
        while queue:
            current = queue.popleft()
            for item in self.list_children(current):
                collected.append(item)
                if len(collected) > limit:
                    raise StorageQuotaExceeded(f"Folder contains more than {limit} objects")
                if item.is_folder:
                    queue.append(item.id)
        return collected

    def read_bytes(self, object_id: str, max_bytes: int) -> tuple[bytes, str]:
        path = self._resolve(object_id)
        if not path.exists():
            raise FileNotFoundError(object_id)
        if path.is_dir():
            raise ValueError("Storage object is a folder")
        size = path.stat().st_size
        if size > max_bytes:
            raise ValueError(f"Storage object exceeds {max_bytes} bytes")
        return path.read_bytes(), _mime_for(path, False)

    def _used_bytes(self) -> int:
        project_root = self._ensure_project_root()
        total = 0
        for root, dirs, files in os.walk(project_root):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if d not in SERVICE_DIRS]
            for name in files:
                try:
                    total += (root_path / name).stat().st_size
                except OSError:
                    continue
        return total

    def usage(self) -> tuple[int, int]:
        return self._used_bytes(), self.quota_bytes

    def create_folder(self, name: str, parent_id: str | None = None) -> str:
        cleaned = _clean_name(name)
        parent_path = self._resolve(parent_id or "pu:/")
        if not parent_path.exists():
            raise FileNotFoundError(parent_id or "pu:/")
        if not parent_path.is_dir():
            raise LookupError("Parent is not a folder")
        target = parent_path / cleaned
        try:
            target.mkdir(exist_ok=True)
        except OSError as exc:
            raise StorageUnavailable("Cannot create folder") from exc
        self._resolve(self._id_of(target))  # validates containment
        return self._id_of(target)

    def write_file(
        self,
        parent_id: str | None,
        name: str,
        data: bytes,
        mime: str | None = None,
    ) -> str:
        del mime  # mime derived from filename
        cleaned = _clean_name(name)
        parent_path = self._resolve(parent_id or "pu:/")
        if not parent_path.exists():
            raise FileNotFoundError(parent_id or "pu:/")
        if not parent_path.is_dir():
            raise LookupError("Parent is not a folder")
        incoming = len(data)
        used = self._used_bytes()
        if used + incoming > self.quota_bytes:
            raise StorageQuotaExceeded("Storage project quota exceeded")
        candidate = parent_path / cleaned
        if candidate.exists():
            stem = candidate.stem
            suffix = candidate.suffix
            counter = 2
            while True:
                alt = parent_path / f"{stem} ({counter}){suffix}"
                if not alt.exists():
                    candidate = alt
                    break
                counter += 1
        try:
            candidate.write_bytes(data)
        except OSError as exc:
            raise StorageUnavailable("Cannot write file") from exc
        self._resolve(self._id_of(candidate))
        return self._id_of(candidate)

    def assert_inside_copy(self, object_id: str, copy_root_id: str) -> None:
        target = self._normalize_id(object_id)
        root = self._normalize_id(copy_root_id)
        if target == root:
            return
        if not target.startswith(root.rstrip("/") + "/"):
            raise StorageAccessDenied("Mutation outside of the PU safe copy is blocked")

    def rename_file(self, object_id: str, new_name: str, copy_root_id: str) -> None:
        self.assert_inside_copy(object_id, copy_root_id)
        cleaned = _clean_name(new_name)
        source = self._resolve(object_id)
        if not source.exists():
            raise FileNotFoundError(object_id)
        destination = source.parent / cleaned
        if destination.exists() and destination != source:
            raise StorageAccessDenied("Destination already exists")
        try:
            source.rename(destination)
        except OSError as exc:
            raise StorageUnavailable("Cannot rename file") from exc

    def move_file(
        self,
        object_id: str,
        new_parent_id: str,
        old_parent_id: str,
        copy_root_id: str,
    ) -> None:
        del old_parent_id
        self.assert_inside_copy(object_id, copy_root_id)
        source = self._resolve(object_id)
        if not source.exists():
            raise FileNotFoundError(object_id)
        new_parent = self._resolve(new_parent_id)
        self.assert_inside_copy(new_parent_id.rstrip("/") + "/placeholder", copy_root_id)
        if not new_parent.is_dir():
            raise LookupError("Destination is not a folder")
        destination = new_parent / source.name
        if destination.exists() and destination != source:
            raise StorageAccessDenied("Destination already exists")
        try:
            shutil.move(str(source), str(destination))
        except OSError as exc:
            raise StorageUnavailable("Cannot move file") from exc

    def trash_safe_copy(self, copy_root_id: str) -> None:
        source = self._resolve(copy_root_id)
        if not source.exists():
            raise FileNotFoundError(copy_root_id)
        if source == self._ensure_project_root():
            raise StorageAccessDenied("Project root cannot be trashed")
        trash_dir = self._ensure_project_root() / ".trash"
        try:
            trash_dir.mkdir(exist_ok=True)
        except OSError as exc:
            raise StorageUnavailable("Cannot create trash directory") from exc
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = trash_dir / f"{timestamp}-{uuid.uuid4().hex[:8]}"
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            raise StorageUnavailable("Cannot move folder to trash") from exc

    def copy_folder_tree(
        self,
        source_folder_id: str,
        new_parent_id: str,
        source_name: str,
        source_items: list[StorageObject] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> StorageCopyResult:
        source = self._resolve(source_folder_id)
        if not source.exists() or not source.is_dir():
            raise FileNotFoundError(source_folder_id)
        parent = self._resolve(new_parent_id)
        if not parent.is_dir():
            raise LookupError("Destination is not a folder")
        key = idempotency_key or hashlib.sha256(
            f"{self.project_id}:{self._normalize_id(source_folder_id)}".encode("utf-8")
        ).hexdigest()[:12]
        base_name = source_name or source.name or "copy"
        copy_name = f"{base_name} — PU safe copy {key}"
        candidate = parent / copy_name
        counter = 2
        while candidate.exists():
            candidate = parent / f"{copy_name} ({counter})"
            counter += 1
        try:
            shutil.copytree(source, candidate, symlinks=False)
        except OSError as exc:
            raise StorageUnavailable("Cannot copy folder tree") from exc
        copy_root_id = self._id_of(candidate)
        id_map: dict[str, str] = {self._normalize_id(source_folder_id): copy_root_id}
        items = source_items if source_items is not None else self.walk_tree(self._normalize_id(source_folder_id))
        for item in items:
            rel = item.id[len(self._normalize_id(source_folder_id)):].lstrip("/")
            if not rel:
                continue
            id_map[item.id] = f"{copy_root_id.rstrip('/')}/{rel}"
        return StorageCopyResult(copy_root_id, candidate.name, id_map, len(items))
