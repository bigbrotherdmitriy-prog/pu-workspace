from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import MAX_FILES_PER_SCAN, SAFE_COPY_SUFFIX
from .types import DriveFile, FOLDER_MIME


class UnsafeDriveMutation(RuntimeError):
    pass


class CopyLimitExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class CopyResult:
    copy_root_id: str
    copy_root_name: str
    id_map: dict[str, str]
    item_count: int


class DriveClient:
    """Google Drive wrapper with hard COPY-only safety checks.

    The caller provides an authenticated Drive API v3 service. Any mutating
    operation on an existing file must pass copy_root_id and is rejected when
    the file is outside the safe-copy tree.
    """

    def __init__(self, service: Any):
        self.service = service

    @staticmethod
    def _to_file(meta: dict, fallback_parent: str = "") -> DriveFile:
        return DriveFile(
            id=meta["id"],
            name=meta["name"],
            mime_type=meta["mimeType"],
            parent_id=(meta.get("parents") or [fallback_parent or ""])[0],
            md5_checksum=meta.get("md5Checksum"),
            size=int(meta["size"]) if meta.get("size") else None,
            modified_time=meta.get("modifiedTime"),
        )

    def get_file_meta(self, file_id: str) -> DriveFile:
        meta = self.service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,parents,md5Checksum,size,modifiedTime,trashed",
        ).execute()
        if meta.get("trashed"):
            raise ValueError("Google Drive object is in trash")
        return self._to_file(meta)

    def list_children(self, folder_id: str) -> list[DriveFile]:
        out: list[DriveFile] = []
        page_token = None
        while True:
            resp = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id,name,mimeType,parents,md5Checksum,size,modifiedTime)",
                pageSize=1000,
                pageToken=page_token,
            ).execute()
            out.extend(self._to_file(x, folder_id) for x in resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

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

    def create_folder(self, name: str, parent_id: str) -> str:
        created = self.service.files().create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id",
        ).execute()
        return created["id"]

    def copy_file(self, file_id: str, new_parent_id: str, new_name: str | None = None) -> str:
        body: dict[str, Any] = {"parents": [new_parent_id]}
        if new_name:
            body["name"] = new_name
        copied = self.service.files().copy(fileId=file_id, body=body, fields="id").execute()
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

    def copy_folder_tree(self, source_folder_id: str, new_parent_id: str, source_name: str, source_items: list[DriveFile] | None = None) -> CopyResult:
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
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H-%M-%S UTC")
        copy_name = source_name + SAFE_COPY_SUFFIX.format(ts=ts)
        root_copy_id = self.create_folder(copy_name, new_parent_id or "root")
        id_map: dict[str, str] = {source_folder_id: root_copy_id}
        queue: deque[str] = deque([source_folder_id])
        try:
            copied_count = 0
            while queue:
                current_source = queue.popleft()
                current_copy = id_map[current_source]

                for item in children_by_parent.get(current_source, []):
                    copied_count += 1
                    if copied_count % 100 == 0:
                        print(
                            f"[DRIVE COPY] copied={copied_count}/{len(source_items)} "
                            f"folders_pending={len(queue)}",
                            flush=True,
                        )
                    if item.is_folder:
                        new_id = self.create_folder(item.name, current_copy)
                        id_map[item.id] = new_id
                        queue.append(item.id)
                    else:
                        id_map[item.id] = self.copy_file(item.id, current_copy)
        except Exception:
            # We intentionally do not trash/delete the partial copy automatically:
            # retaining evidence is safer. The session is marked failed by service layer.
            raise
        return CopyResult(root_copy_id, copy_name, id_map, len(source_items))
