from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import uuid
from contextlib import contextmanager
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
SERVICE_DIRS = {".trash", ".pu-copy-staging"}
RD_VERSION_CONTENT_DIR = ".rd-version-content"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_unix_open_flags() -> tuple[int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if not isinstance(nofollow, int) or not isinstance(nonblock, int) or nofollow == 0 or nonblock == 0:
        raise StorageUnavailable("Platform lacks required O_NOFOLLOW/O_NONBLOCK open flags")
    return nofollow, nonblock


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
    if cleaned in {"", ".", ".."} or cleaned in SERVICE_DIRS:
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


def _tree_bytes(root: Path, *, exclude_trash=False, exclude_temps=False) -> int:
    if root.is_symlink():
        raise StorageAccessDenied("Invalid storage directory")
    if not root.exists():
        return 0
    if not root.is_dir():
        raise StorageAccessDenied("Invalid storage directory")
    total = 0
    def fail(error):
        raise error
    for directory, dirs, files in os.walk(root, onerror=fail, followlinks=False):
        if exclude_trash:
            dirs[:] = [name for name in dirs if name != ".trash"]
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink():
                raise StorageAccessDenied("Symlink in quota accounting")
        for name in files:
            if exclude_temps and name.startswith('.tmp-'):
                continue
            metadata = (Path(directory) / name).stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise StorageAccessDenied("Nonregular storage object")
            total += metadata.st_size
    return total


def _shared_usage(root: Path, pid: int) -> int:
    base = root / RD_VERSION_CONTENT_DIR
    if base.is_symlink():
        raise StorageAccessDenied("Private namespace is a symlink")
    return _tree_bytes(root / str(pid), exclude_trash=True) + _tree_bytes(base / str(pid), exclude_temps=True)


@contextmanager
def _mutable_writer_lock(root: Path, pid: int):
    store = RdVersionContentStore(root, 1)
    store._private_base(create=True)
    nofollow, nonblock = _require_unix_open_flags()
    descriptors = []
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
        descriptors.append(root_fd)
        base_fd = store._open_dir_anchored(root_fd, RD_VERSION_CONTENT_DIR)
        descriptors.append(base_fd)
        lock_fd = os.open(f'.lock-{pid}', os.O_RDWR | os.O_CREAT | nofollow | nonblock, 0o600, dir_fd=base_fd)
        descriptors.append(lock_fd)
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise StorageAccessDenied("Invalid project lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


@contextmanager
def _mutable_parent_fd(root: Path, pid: int, relative_parts: list[str]):
    """Hold each directory inode; never follow mutable parent symlinks."""
    nofollow, nonblock = _require_unix_open_flags()
    descriptors = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock)
        descriptors.append(fd)
        for name in [str(pid), *relative_parts]:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock, dir_fd=fd)
            descriptors.append(fd)
        yield fd
    except FileNotFoundError:
        raise
    except OSError as exc:
        import errno
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise StorageAccessDenied("Mutable path contains a symlink or non-directory") from exc
        raise StorageUnavailable("Cannot access mutable storage safely") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _rename_noreplace(source: str, destination: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
    """Linux atomic no-clobber rename; unsupported platforms fail closed."""
    import ctypes
    import errno
    try:
        call = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        raise StorageUnavailable("Atomic no-replace rename is unavailable") from exc
    call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    call.restype = ctypes.c_int
    if call(src_dir_fd, os.fsencode(source), dst_dir_fd, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise StorageAccessDenied("Destination already exists")
        if error in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise StorageUnavailable("Atomic no-replace rename is unavailable")
        raise OSError(error, os.strerror(error))


def _copy_tree_fds(source_fd: int, destination_fd: int, byte_budget: int) -> list[str]:
    """Copy regular files/directories without resolving mutable pathnames."""
    nofollow, nonblock = _require_unix_open_flags()
    copied = []
    remaining = byte_budget
    def visit(src, dst, prefix, depth):
        nonlocal remaining
        if depth > 64:
            raise StorageQuotaExceeded("Copy directory depth exceeds limit")
        with os.scandir(src) as entries:
            names = []
            for entry in entries:
                if len(names) + len(copied) >= 10_000:
                    raise StorageQuotaExceeded("Copy object count exceeds limit")
                names.append(entry.name)
            names.sort()
        for name in names:
            if len(copied) >= 10_000:
                raise StorageQuotaExceeded("Copy object count exceeds limit")
            info = os.stat(name, dir_fd=src, follow_symlinks=False)
            relative = prefix + name
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock, dir_fd=src)
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise StorageAccessDenied("Copy source directory changed")
                    os.mkdir(name, mode=0o700, dir_fd=dst)
                    target = os.open(name, os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock, dir_fd=dst)
                    try:
                        copied.append(relative)
                        visit(child, target, relative + '/', depth + 1)
                    finally:
                        os.close(target)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                child = os.open(name, os.O_RDONLY | nofollow | nonblock, dir_fd=src)
                try:
                    opened = os.fstat(child)
                    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise StorageAccessDenied("Copy source file changed")
                    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | nonblock, 0o600, dir_fd=dst)
                    try:
                        while True:
                            chunk = os.read(child, min(65536, remaining + 1))
                            if not chunk:
                                break
                            if len(chunk) > remaining:
                                raise StorageQuotaExceeded("Storage project quota exceeded")
                            remaining -= len(chunk)
                            view = memoryview(chunk)
                            while view:
                                written = os.write(target, view)
                                if written <= 0:
                                    raise OSError("Short storage copy write")
                                view = view[written:]
                    finally:
                        os.close(target)
                finally:
                    os.close(child)
                copied.append(relative)
            else:
                raise StorageAccessDenied("Copy source contains a symlink or special file")
    visit(source_fd, destination_fd, '', 0)
    return copied


def _clear_created_tree(directory_fd: int) -> None:
    """Clean only the held newly created tree, never follow replacement symlinks."""
    nofollow, nonblock = _require_unix_open_flags()
    with os.scandir(directory_fd) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock, dir_fd=directory_fd)
            try:
                _clear_created_tree(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


@contextmanager
def _copy_staging(root: Path, pid: int):
    """Keep unpublished trees private; retain unrelated/crash leftovers."""
    import errno
    nofollow, nonblock = _require_unix_open_flags()
    with _mutable_parent_fd(root, pid, []) as project_fd:
        try:
            os.mkdir('.pu-copy-staging', mode=0o700, dir_fd=project_fd)
        except FileExistsError:
            pass
        stage_fd = os.open('.pu-copy-staging', os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock, dir_fd=project_fd)
        try:
            name = uuid.uuid4().hex
            os.mkdir(name, mode=0o700, dir_fd=stage_fd)
            target_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock, dir_fd=stage_fd)
            state = {'fd': target_fd, 'parent_fd': stage_fd, 'name': name, 'published': False}
            try:
                yield state
            finally:
                try:
                    if not state['published']:
                        held = os.fstat(target_fd)
                        current = os.stat(name, dir_fd=stage_fd, follow_symlinks=False)
                        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                            raise StorageAccessDenied("Copy staging identity changed")
                        _clear_created_tree(target_fd)
                        os.rmdir(name, dir_fd=stage_fd)
                finally:
                    os.close(target_fd)
        finally:
            held = os.fstat(stage_fd)
            os.close(stage_fd)
            current = os.stat('.pu-copy-staging', dir_fd=project_fd, follow_symlinks=False)
            if (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino):
                try:
                    os.rmdir('.pu-copy-staging', dir_fd=project_fd)
                except OSError as exc:
                    if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                        raise


COPY_RECEIPT_XATTR = 'user.pu_workspace_copy_v1'
COPY_RECEIPT_MAX_BYTES = 1024


def _path_digest(paths: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(paths), ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _completed_tree_paths(root_fd: int) -> list[str]:
    nofollow, nonblock = _require_unix_open_flags()
    paths = []
    def visit(directory_fd, prefix, depth):
        if depth > 64:
            raise StorageAccessDenied("Completed copy depth exceeds limit")
        with os.scandir(directory_fd) as entries:
            names = []
            for entry in entries:
                if len(names) + len(paths) >= 10_000:
                    raise StorageAccessDenied("Completed copy object count exceeds limit")
                names.append(entry.name)
        for name in sorted(names):
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise StorageAccessDenied("Unsafe completed copy object")
            flags = os.O_RDONLY | nofollow | nonblock
            if stat.S_ISDIR(info.st_mode):
                flags |= os.O_DIRECTORY
            child = os.open(name, flags, dir_fd=directory_fd)
            try:
                held = os.fstat(child)
                if (held.st_dev, held.st_ino) != (info.st_dev, info.st_ino):
                    raise StorageAccessDenied("Completed copy object changed")
                path = prefix + name
                paths.append(path)
                if stat.S_ISDIR(held.st_mode):
                    visit(child, path + '/', depth + 1)
            finally:
                os.close(child)
    visit(root_fd, '', 0)
    return paths


def _mark_completed_copy(fd: int, key_hash: str, request_hash: str, paths: list[str]) -> None:
    info = os.fstat(fd)
    receipt = {'schema': 2, 'key': key_hash, 'request': request_hash,
               'inode': [info.st_dev, info.st_ino], 'path_count': len(paths), 'path_digest': _path_digest(paths)}
    payload = json.dumps(receipt, ensure_ascii=False, separators=(',', ':')).encode()
    if len(payload) > COPY_RECEIPT_MAX_BYTES:
        raise StorageQuotaExceeded("Copy receipt exceeds metadata limit")
    try:
        os.setxattr(fd, COPY_RECEIPT_XATTR, payload)
    except (AttributeError, OSError) as exc:
        raise StorageUnavailable("Persistent copy ownership metadata unavailable") from exc


def _find_completed_copy(parent_fd: int, key_hash: str, request_hash: str, selected_paths: list[str] | None = None):
    nofollow, nonblock = _require_unix_open_flags()
    found = None
    with os.scandir(parent_fd) as entries:
        names = []
        for entry in entries:
            if len(names) >= 10_000:
                raise StorageQuotaExceeded("Copy retry search exceeds object limit")
            if entry.name not in SERVICE_DIRS:
                names.append(entry.name)
    for name in names:
        try:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno in (errno.ENOTDIR, errno.ELOOP, errno.ENOENT):
                continue
            raise
        try:
            try:
                raw = os.getxattr(child, COPY_RECEIPT_XATTR)
            except OSError as exc:
                if exc.errno == errno.ENODATA:
                    continue
                raise StorageUnavailable("Persistent copy ownership metadata unavailable") from exc
            except AttributeError as exc:
                raise StorageUnavailable("Persistent copy ownership metadata unavailable") from exc
            if len(raw) > COPY_RECEIPT_MAX_BYTES:
                raise StorageAccessDenied("Invalid copy ownership metadata")
            try:
                receipt = json.loads(raw)
                if not isinstance(receipt, dict) or type(receipt.get('schema')) is not int or receipt['schema'] != 2:
                    raise ValueError('Invalid receipt schema')
                if receipt.get('key') != key_hash:
                    continue
                info = os.fstat(child)
                if receipt.get('request') != request_hash or receipt.get('inode') != [info.st_dev, info.st_ino]:
                    raise ValueError('Copy key conflicts with request or inode')
                actual_paths = _completed_tree_paths(child)
                if selected_paths is None:
                    paths = actual_paths
                else:
                    if len(set(selected_paths)) != len(selected_paths) or not set(selected_paths).issubset(set(actual_paths)):
                        raise ValueError('Invalid selected copy paths')
                    paths = selected_paths
                if receipt.get('path_count') != len(paths) or receipt.get('path_digest') != _path_digest(paths):
                    raise ValueError('Completed copy path manifest changed')
            except (ValueError, KeyError, TypeError, UnicodeError, OSError) as exc:
                raise StorageAccessDenied("Invalid or conflicting copy ownership metadata") from exc
            if found is not None:
                raise StorageAccessDenied("Ambiguous copy ownership metadata")
            found = (name, paths)
        finally:
            os.close(child)
    return found


def _copy_result(source_id: str, parent_id: str, name: str, paths: list[str]) -> StorageCopyResult:
    copy_id = parent_id.rstrip('/') + '/' + name
    id_map = {source_id: copy_id}
    id_map.update({source_id.rstrip('/') + '/' + path: copy_id + '/' + path for path in paths})
    return StorageCopyResult(copy_id, name, id_map, len(paths))


def _publish_copy_name(stage_parent_fd: int, stage_name: str, parent_fd: int, copy_name: str) -> str:
    candidate, counter = copy_name, 2
    while True:
        try:
            _rename_noreplace(stage_name, candidate, src_dir_fd=stage_parent_fd, dst_dir_fd=parent_fd)
            return candidate
        except StorageAccessDenied:
            if counter > 10_000:
                raise StorageQuotaExceeded("Too many copy name collisions")
            candidate = f"{copy_name} ({counter})"
            counter += 1


def _recover_staged_copy(root: Path, pid: int, parent_fd: int, key_hash: str,
                         request_hash: str, selected_paths: list[str] | None, copy_name: str):
    nofollow, nonblock = _require_unix_open_flags()
    with _mutable_parent_fd(root, pid, []) as project_fd:
        try:
            stage_parent_fd = os.open('.pu-copy-staging', os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock, dir_fd=project_fd)
        except FileNotFoundError:
            return None
        published = False
        try:
            held = os.fstat(stage_parent_fd)
            completed = _find_completed_copy(stage_parent_fd, key_hash, request_hash, selected_paths)
            if completed is None:
                return None
            name = _publish_copy_name(stage_parent_fd, completed[0], parent_fd, copy_name)
            published = True
            return name, completed[1]
        finally:
            os.close(stage_parent_fd)
            if published:
                current = os.stat('.pu-copy-staging', dir_fd=project_fd, follow_symlinks=False)
                if (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino):
                    try:
                        os.rmdir('.pu-copy-staging', dir_fd=project_fd)
                    except OSError as exc:
                        if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                            raise


class PuStorageAdapter:
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
            if part == RD_VERSION_CONTENT_DIR:
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
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
            raise ValueError("Invalid storage read limit")
        normalized = self._normalize_id(object_id)
        relative = normalized[len("pu:/"):]
        if not relative:
            raise ValueError("Storage object is a folder")
        self._ensure_project_root()
        parts = relative.split('/')
        with _mutable_parent_fd(self._root_parent, self.project_id, parts[:-1]) as parent_fd:
            nofollow, nonblock = _require_unix_open_flags()
            descriptor = os.open(parts[-1], os.O_RDONLY | nofollow | nonblock, dir_fd=parent_fd)
            try:
                source = os.fdopen(descriptor, 'rb')
            except BaseException:
                os.close(descriptor)
                raise
            with source:
                info = os.fstat(source.fileno())
                if stat.S_ISDIR(info.st_mode):
                    raise ValueError("Storage object is a folder")
                if not stat.S_ISREG(info.st_mode):
                    raise StorageAccessDenied("Storage object is not a regular file")
                if info.st_size > max_bytes:
                    raise ValueError(f"Storage object exceeds {max_bytes} bytes")
                data = source.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError(f"Storage object exceeds {max_bytes} bytes")
        return data, _mime_for(Path(parts[-1]), False)

    def _used_bytes(self) -> int:
        return _shared_usage(self._root_parent, self.project_id)

    def usage(self) -> tuple[int, int]:
        return self._used_bytes(), self.quota_bytes

    def create_folder(self, name: str, parent_id: str | None = None) -> str:
        with _mutable_writer_lock(self._root_parent, self.project_id):
            cleaned = _clean_name(name)
            normalized = self._normalize_id(parent_id or "pu:/")
            self._ensure_project_root()
            relative = normalized[len("pu:/"):]
            parts = relative.split('/') if relative else []
            with _mutable_parent_fd(self._root_parent, self.project_id, parts) as parent_fd:
                try:
                    os.mkdir(cleaned, mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                nofollow, nonblock = _require_unix_open_flags()
                descriptor = os.open(cleaned, os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock, dir_fd=parent_fd)
                os.close(descriptor)
            return normalized.rstrip('/') + '/' + cleaned

    def write_file(
        self,
        parent_id: str | None,
        name: str,
        data: bytes,
        mime: str | None = None,
    ) -> str:
        with _mutable_writer_lock(self._root_parent, self.project_id):
            del mime
            cleaned = _clean_name(name)
            normalized = self._normalize_id(parent_id or "pu:/")
            parent_path = self._resolve(normalized)
            if not parent_path.exists():
                raise FileNotFoundError(normalized)
            if not parent_path.is_dir():
                raise LookupError("Parent is not a folder")
            if self._used_bytes() + len(data) > self.quota_bytes:
                raise StorageQuotaExceeded("Storage project quota exceeded")
            relative = normalized[len("pu:/"):]
            parts = relative.split('/') if relative else []
            with _mutable_parent_fd(self._root_parent, self.project_id, parts) as parent_fd:
                nofollow, nonblock = _require_unix_open_flags()
                candidate = cleaned
                stem, suffix = Path(cleaned).stem, Path(cleaned).suffix
                counter = 2
                while True:
                    try:
                        descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | nonblock, 0o600, dir_fd=parent_fd)
                        break
                    except FileExistsError:
                        candidate = f"{stem} ({counter}){suffix}"
                        counter += 1
                try:
                    try:
                        output = os.fdopen(descriptor, 'wb')
                    except BaseException:
                        os.close(descriptor)
                        raise
                    with output:
                        output.write(data)
                except BaseException:
                    os.unlink(candidate, dir_fd=parent_fd)
                    raise
            return normalized.rstrip('/') + '/' + candidate

    def assert_inside_copy(self, object_id: str, copy_root_id: str) -> None:
        target = self._normalize_id(object_id)
        root = self._normalize_id(copy_root_id)
        if target == root:
            return
        if not target.startswith(root.rstrip("/") + "/"):
            raise StorageAccessDenied("Mutation outside of the PU safe copy is blocked")

    def rename_file(self, object_id: str, new_name: str, copy_root_id: str) -> None:
        with _mutable_writer_lock(self._root_parent, self.project_id):
            self.assert_inside_copy(object_id, copy_root_id)
            cleaned = _clean_name(new_name)
            normalized = self._normalize_id(object_id)
            relative = normalized[len("pu:/"):]
            if not relative:
                raise StorageAccessDenied("Project root cannot be renamed")
            parts = relative.split('/')
            with _mutable_parent_fd(self._root_parent, self.project_id, parts[:-1]) as parent_fd:
                source = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISLNK(source.st_mode):
                    raise StorageAccessDenied("Source symlink cannot be renamed")
                if cleaned == parts[-1]:
                    return
                try:
                    os.stat(cleaned, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise StorageAccessDenied("Destination already exists")
                _rename_noreplace(parts[-1], cleaned, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)

    def move_file(
        self,
        object_id: str,
        new_parent_id: str,
        old_parent_id: str,
        copy_root_id: str,
    ) -> None:
        with _mutable_writer_lock(self._root_parent, self.project_id):
            del old_parent_id
            self.assert_inside_copy(object_id, copy_root_id)
            self.assert_inside_copy(new_parent_id.rstrip('/') + '/placeholder', copy_root_id)
            source_id = self._normalize_id(object_id)
            destination_id = self._normalize_id(new_parent_id)
            relative = source_id[len('pu:/'):]
            if not relative:
                raise StorageAccessDenied("Project root cannot be moved")
            parts = relative.split('/')
            destination = destination_id[len('pu:/'):]
            destination_parts = destination.split('/') if destination else []
            with _mutable_parent_fd(self._root_parent, self.project_id, parts[:-1]) as source_fd:
                source = os.stat(parts[-1], dir_fd=source_fd, follow_symlinks=False)
                if stat.S_ISLNK(source.st_mode):
                    raise StorageAccessDenied("Source symlink cannot be moved")
                with _mutable_parent_fd(self._root_parent, self.project_id, destination_parts) as destination_fd:
                    if parts[:-1] == destination_parts:
                        return
                    _rename_noreplace(parts[-1], parts[-1], src_dir_fd=source_fd, dst_dir_fd=destination_fd)

    def trash_safe_copy(self, copy_root_id: str) -> None:
        with _mutable_writer_lock(self._root_parent, self.project_id):
            normalized = self._normalize_id(copy_root_id)
            relative = normalized[len('pu:/'):]
            if not relative:
                raise StorageAccessDenied("Project root cannot be trashed")
            parts = relative.split('/')
            with _mutable_parent_fd(self._root_parent, self.project_id, parts[:-1]) as source_fd:
                source = os.stat(parts[-1], dir_fd=source_fd, follow_symlinks=False)
                if stat.S_ISLNK(source.st_mode):
                    raise StorageAccessDenied("Source symlink cannot be trashed")
                with _mutable_parent_fd(self._root_parent, self.project_id, []) as project_fd:
                    try:
                        os.mkdir('.trash', mode=0o700, dir_fd=project_fd)
                    except FileExistsError:
                        pass
                    nofollow, nonblock = _require_unix_open_flags()
                    trash_fd = os.open('.trash', os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock, dir_fd=project_fd)
                    try:
                        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                        target = f"{timestamp}-{uuid.uuid4().hex[:8]}"
                        _rename_noreplace(parts[-1], target, src_dir_fd=source_fd, dst_dir_fd=trash_fd)
                    finally:
                        os.close(trash_fd)

    def copy_folder_tree(
        self,
        source_folder_id: str,
        new_parent_id: str,
        source_name: str,
        source_items: list[StorageObject] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> StorageCopyResult:
        with _mutable_writer_lock(self._root_parent, self.project_id):
            source_id = self._normalize_id(source_folder_id)
            parent_id = self._normalize_id(new_parent_id)
            if parent_id == source_id or parent_id.startswith(source_id.rstrip('/') + '/'):
                raise StorageAccessDenied("Cannot copy a folder into itself")
            source_parts = source_id[len('pu:/'):].split('/') if source_id != 'pu:/' else []
            parent_parts = parent_id[len('pu:/'):].split('/') if parent_id != 'pu:/' else []
            key = idempotency_key or hashlib.sha256(f"{self.project_id}:{source_id}".encode()).hexdigest()[:12]
            base_name = source_name or (source_parts[-1] if source_parts else 'copy')
            copy_name = _clean_name(f"{base_name} — PU safe copy {key}")
            key_hash = hashlib.sha256(str(idempotency_key).encode()).hexdigest() if idempotency_key else None
            request_hash = hashlib.sha256(json.dumps([source_id, parent_id, base_name,
                sorted(item.id for item in source_items) if source_items is not None else None],
                ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            with _mutable_parent_fd(self._root_parent, self.project_id, parent_parts) as parent_fd:
                if key_hash:
                    source_prefix = source_id.rstrip('/') + '/'
                    selected_paths = None
                    if source_items is not None:
                        if any(not item.id.startswith(source_prefix) for item in source_items):
                            raise StorageAccessDenied("Copy metadata does not match source tree")
                        selected_paths = [item.id[len(source_prefix):] for item in source_items]
                    completed = _find_completed_copy(parent_fd, key_hash, request_hash, selected_paths)
                    if completed is not None:
                        return _copy_result(source_id, parent_id, completed[0], completed[1])
                    staged = _recover_staged_copy(self._root_parent, self.project_id, parent_fd,
                                                  key_hash, request_hash, selected_paths, copy_name)
                    if staged is not None:
                        return _copy_result(source_id, parent_id, staged[0], staged[1])
                available = self.quota_bytes - self._used_bytes()
                if available < 0:
                    raise StorageQuotaExceeded("Storage project quota exceeded")
                with _mutable_parent_fd(self._root_parent, self.project_id, source_parts) as source_fd:
                    with _copy_staging(self._root_parent, self.project_id) as stage:
                        paths = _copy_tree_fds(source_fd, stage['fd'], available)
                        source_prefix = source_id.rstrip('/') + '/'
                        if source_items is not None:
                            selected = []
                            for item in source_items:
                                if not item.id.startswith(source_prefix) or item.id[len(source_prefix):] not in paths:
                                    raise StorageAccessDenied("Copy metadata does not match source tree")
                                selected.append(item.id[len(source_prefix):])
                            paths = selected
                        if key_hash:
                            _mark_completed_copy(stage['fd'], key_hash, request_hash, paths)
                        candidate = _publish_copy_name(stage['parent_fd'], stage['name'], parent_fd, copy_name)
                        stage['published'] = True
            return _copy_result(source_id, parent_id, candidate, paths)


class RdVersionContentStore:
    """Bounded content-addressed primitive; not wired into any caller.

    Private namespace: ``root/.rd-version-content/{project_id}/{sha256}``.
    Shared quota/lock with PuStorageAdapter and WRITE-side parent anchoring
    remain explicit future blockers.
    """

    def __init__(self, root: Path, quota_bytes: int) -> None:
        if root is None or quota_bytes is None:
            raise StorageUnavailable("RdVersionContentStore requires explicit root and quota")
        self._root = Path(root)
        self.quota_bytes = int(quota_bytes)
        if self.quota_bytes <= 0:
            raise StorageUnavailable("RdVersionContentStore quota must be positive")

    @staticmethod
    def _validate_project_id(project_id: int) -> int:
        if not isinstance(project_id, int) or isinstance(project_id, bool) or project_id <= 0:
            raise StorageAccessDenied("Invalid project id")
        return project_id

    def _private_base(self, *, create: bool) -> Path:
        base = self._root / RD_VERSION_CONTENT_DIR
        try:
            st = os.lstat(base)
        except FileNotFoundError:
            if not create:
                raise
            try:
                base.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                pass
            except OSError as exc:
                raise StorageUnavailable("Cannot prepare private content namespace") from exc
            try:
                st = os.lstat(base)
            except OSError as exc:
                raise StorageUnavailable("Cannot prepare private content namespace") from exc
        except OSError as exc:
            raise StorageUnavailable("Cannot prepare private content namespace") from exc
        if stat.S_ISLNK(st.st_mode):
            raise StorageAccessDenied("Private content namespace is a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise StorageUnavailable("Private content namespace is not a directory")
        return base

    def _project_dir(self, project_id: int, *, create: bool) -> Path:
        pid = self._validate_project_id(project_id)
        base = self._private_base(create=create)
        project_dir = base / str(pid)
        try:
            st = os.lstat(project_dir)
        except FileNotFoundError:
            if not create:
                raise
            try:
                project_dir.mkdir(exist_ok=False)
            except FileExistsError:
                pass
            except OSError as exc:
                raise StorageUnavailable("Cannot prepare private project directory") from exc
            try:
                st = os.lstat(project_dir)
            except OSError as exc:
                raise StorageUnavailable("Cannot prepare private project directory") from exc
        except OSError as exc:
            raise StorageUnavailable("Cannot prepare private project directory") from exc
        if stat.S_ISLNK(st.st_mode):
            raise StorageAccessDenied("Private project directory is a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise StorageUnavailable("Private project directory is not a directory")
        return project_dir

    def _lock_path(self, project_id: int) -> Path:
        pid = self._validate_project_id(project_id)
        base = self._private_base(create=True)
        return base / f".lock-{pid}"

    def _acquire_project_lock(self, project_id: int):
        lock_path = self._lock_path(project_id)
        nofollow, nonblock = _require_unix_open_flags()
        flags = os.O_RDWR | os.O_CREAT | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise StorageUnavailable("Cannot open private content lock") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise StorageAccessDenied("Private content lock is not a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            os.close(fd)
            raise StorageUnavailable("Cannot acquire private content lock") from exc
        except BaseException:
            os.close(fd)
            raise
        return os.fdopen(fd, "rb", closefd=True)

    @staticmethod
    def _open_regular_nofollow(path: Path) -> int:
        nofollow, nonblock = _require_unix_open_flags()
        flags = os.O_RDONLY | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EMLINK}:
                raise StorageAccessDenied("Content blob is a symlink") from exc
            raise StorageUnavailable("Cannot open content blob") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise StorageAccessDenied("Content blob is not a regular file")
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _read_blob_bounded(self, path: Path, digest: str, max_bytes: int) -> bytes:
        fd = self._open_regular_nofollow(path)
        try:
            st = os.fstat(fd)
            if st.st_size > max_bytes:
                raise StorageQuotaExceeded("Content blob exceeds read budget")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        except OSError as exc:
            raise StorageUnavailable("Cannot read content blob") from exc
        finally:
            os.close(fd)
        if len(data) > max_bytes:
            raise StorageQuotaExceeded("Content blob exceeds read budget")
        if hashlib.sha256(data).hexdigest() != digest:
            raise StorageUnavailable("Content blob checksum mismatch")
        return data

    @staticmethod
    def _open_dir_anchored(parent_fd: int, name: str) -> int:
        """Open child directory relative to ``parent_fd`` without following symlinks."""
        nofollow, _nonblock = _require_unix_open_flags()
        flags = os.O_RDONLY | os.O_DIRECTORY | nofollow | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            raise
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EMLINK, errno.ENOTDIR}:
                raise StorageAccessDenied("Private content path component is a symlink") from exc
            raise StorageUnavailable("Cannot open private content directory") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode):
                raise StorageAccessDenied("Private content path component is not a directory")
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _read_blob_anchored(self, project_fd: int, digest: str, max_bytes: int) -> bytes:
        """Open digest relative to held project fd; never follows swapped parents."""
        nofollow, nonblock = _require_unix_open_flags()
        flags = os.O_RDONLY | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(digest, flags, dir_fd=project_fd)
        except FileNotFoundError:
            raise
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EMLINK}:
                raise StorageAccessDenied("Content blob is a symlink") from exc
            raise StorageUnavailable("Cannot open content blob") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise StorageAccessDenied("Content blob is not a regular file")
            if st.st_size > max_bytes:
                raise StorageQuotaExceeded("Content blob exceeds read budget")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        except OSError as exc:
            raise StorageUnavailable("Cannot read content blob") from exc
        finally:
            os.close(fd)
        if len(data) > max_bytes:
            raise StorageQuotaExceeded("Content blob exceeds read budget")
        if hashlib.sha256(data).hexdigest() != digest:
            raise StorageUnavailable("Content blob checksum mismatch")
        return data

    def _used_bytes(self, project_id: int) -> int:
        return _shared_usage(self._root, project_id)

    def put(self, project_id: int, content: bytes) -> str:
        if not isinstance(content, (bytes, bytearray)):
            raise StorageAccessDenied("Content must be bytes")
        data = bytes(content)
        digest = hashlib.sha256(data).hexdigest()
        pid = self._validate_project_id(project_id)
        nofollow, nonblock = _require_unix_open_flags()
        descriptors = []
        temp_name = None
        temp_created = False
        project_fd = None
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            root_fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
            descriptors.append(root_fd)
            try:
                os.mkdir(RD_VERSION_CONTENT_DIR, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            base_fd = self._open_dir_anchored(root_fd, RD_VERSION_CONTENT_DIR)
            descriptors.append(base_fd)
            try:
                os.mkdir(str(pid), mode=0o700, dir_fd=base_fd)
            except FileExistsError:
                pass
            project_fd = self._open_dir_anchored(base_fd, str(pid))
            descriptors.append(project_fd)
            lock_fd = os.open(f'.lock-{pid}', os.O_RDWR | os.O_CREAT | nofollow | nonblock, 0o600, dir_fd=base_fd)
            descriptors.append(lock_fd)
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                raise StorageAccessDenied("Private content lock is not a regular file")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                self._read_blob_anchored(project_fd, digest, len(data))
                return digest
            except FileNotFoundError:
                pass
            except StorageQuotaExceeded as exc:
                raise StorageUnavailable("Existing content blob is corrupt") from exc
            # Existing quota accounting is not shared with mutable storage yet.
            if self._used_bytes(pid) + len(data) > self.quota_bytes:
                raise StorageQuotaExceeded("Private content quota exceeded")
            temp_name = '.tmp-' + uuid.uuid4().hex
            fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | nonblock, 0o600, dir_fd=project_fd)
            temp_created = True
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError(errno.EIO, 'short write')
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(temp_name, digest, src_dir_fd=project_fd, dst_dir_fd=project_fd, follow_symlinks=False)
            except FileExistsError:
                self._read_blob_anchored(project_fd, digest, len(data))
            return digest
        except OSError as exc:
            raise StorageUnavailable("Cannot publish content blob") from exc
        finally:
            try:
                if temp_created:
                    os.unlink(temp_name, dir_fd=project_fd)
            finally:
                for fd in reversed(descriptors):
                    os.close(fd)

    def read(self, project_id: int, digest: str, max_bytes: int) -> bytes:
        if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
            raise StorageAccessDenied("Invalid content digest")
        if not isinstance(max_bytes, int) or max_bytes < 0:
            raise StorageAccessDenied("Invalid read budget")
        pid = self._validate_project_id(project_id)
        nofollow, _nonblock = _require_unix_open_flags()
        root_fd = -1
        base_fd = -1
        project_fd = -1
        try:
            try:
                root_fd = os.open(
                    self._root,
                    os.O_RDONLY | os.O_DIRECTORY | nofollow | getattr(os, "O_CLOEXEC", 0),
                )
            except FileNotFoundError:
                raise FileNotFoundError("Content blob not found")
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.EMLINK, errno.ENOTDIR}:
                    raise StorageAccessDenied("Private content root is a symlink") from exc
                raise StorageUnavailable("Cannot open private content root") from exc
            try:
                base_fd = self._open_dir_anchored(root_fd, RD_VERSION_CONTENT_DIR)
            except FileNotFoundError:
                raise FileNotFoundError("Content blob not found")
            try:
                project_fd = self._open_dir_anchored(base_fd, str(pid))
            except FileNotFoundError:
                raise FileNotFoundError("Content blob not found")
            return self._read_blob_anchored(project_fd, digest, max_bytes)
        finally:
            for fd in (project_fd, base_fd, root_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
