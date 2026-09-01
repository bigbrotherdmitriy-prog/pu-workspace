import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.integrations.contracts import (
    MutableStorageAdapter, StorageAccessDenied, StorageCredentialsExpired, StorageQuotaExceeded, StorageRateLimited, StorageUnavailable,
)
from app.integrations.yandex_disk import YANDEX_DISK_API, YandexDiskStorageAdapter


class FakeDisk:
    def __init__(self):
        self.resources = {
            "disk:/": {"path": "disk:/", "name": "Disk", "type": "dir"},
            "disk:/Root": {"path": "disk:/Root", "name": "Root", "type": "dir"},
            "disk:/Root/Nested": {"path": "disk:/Root/Nested", "name": "Nested", "type": "dir"},
            "disk:/Root/a.txt": {"path": "disk:/Root/a.txt", "name": "a.txt", "type": "file", "mime_type": "text/plain", "size": 5, "md5": "x"},
            "disk:/Root/Nested/b.pdf": {"path": "disk:/Root/Nested/b.pdf", "name": "b.pdf", "type": "file", "mime_type": "application/pdf", "size": 3},
        }
        self.copies = []
        self.error = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.error:
            return httpx.Response(self.error, json={"error": "forced"})
        path = request.url.params.get("path")
        if str(request.url) == YANDEX_DISK_API or request.url.path.endswith("/v1/disk"):
            return httpx.Response(200, json={"user": {"login": "tester"}})
        if request.url.path.endswith("/download"):
            return httpx.Response(200, json={"href": "https://download.test/file"})
        if request.url.host == "download.test":
            return httpx.Response(200, content=b"hello")
        if request.url.path.endswith("/copy"):
            marker = (request.url.params.get("from"), path)
            if marker in self.copies:
                return httpx.Response(409, json={"error": "DiskResourceAlreadyExistsError"})
            self.copies.append(marker)
            return httpx.Response(201, json={"href": "operation"})
        if request.method == "PUT":
            if path in self.resources:
                return httpx.Response(409, json={"error": "DiskResourceAlreadyExistsError"})
            self.resources[path] = {"path": path, "name": path.rsplit("/", 1)[-1], "type": "dir"}
            return httpx.Response(201, json={"href": "operation"})
        if request.url.path.endswith("/resources"):
            if path not in self.resources:
                return httpx.Response(404, json={"error": "DiskNotFoundError"})
            item = dict(self.resources[path])
            if item["type"] == "dir" and "limit" in request.url.params:
                children = [value for key, value in self.resources.items() if key != path and key.rsplit("/", 1)[0] == path.rstrip("/")]
                offset = int(request.url.params.get("offset", 0)); limit = int(request.url.params.get("limit", 100))
                item["_embedded"] = {"items": children[offset:offset + limit], "total": len(children)}
            return httpx.Response(200, json=item)
        return httpx.Response(200, json={})


@pytest.fixture
def fake_adapter():
    disk = FakeDisk()
    client = httpx.Client(transport=httpx.MockTransport(disk.handler))
    yield disk, YandexDiskStorageAdapter("secret-not-logged", client=client)
    client.close()


def test_health_metadata_lists_nested_tree_and_download(fake_adapter):
    _, adapter = fake_adapter
    assert isinstance(adapter, MutableStorageAdapter)
    assert adapter.health().ready
    assert adapter.get_object("disk:/Root").is_folder
    assert {item.name for item in adapter.list_children("disk:/Root")} == {"Nested", "a.txt"}
    assert {item.name for item in adapter.walk_tree("disk:/Root")} == {"Nested", "a.txt", "b.pdf"}
    assert adapter.read_bytes("disk:/Root/a.txt", 10) == (b"hello", "text/plain")


def test_folder_listing_follows_pagination(fake_adapter):
    disk, adapter = fake_adapter
    for number in range(105):
        path = f"disk:/Page/file-{number}.txt"
        disk.resources[path] = {"path": path, "name": f"file-{number}.txt", "type": "file", "mime_type": "text/plain", "size": 1}
    disk.resources["disk:/Page"] = {"path": "disk:/Page", "name": "Page", "type": "dir"}
    assert len(adapter.list_children("disk:/Page")) == 105


def test_safe_copy_and_repeated_sync_are_idempotent(fake_adapter):
    disk, adapter = fake_adapter
    first = adapter.copy_folder_tree("disk:/Root", "disk:/", "Root", idempotency_key="same")
    second = adapter.copy_folder_tree("disk:/Root", "disk:/", "Root", idempotency_key="same")
    assert first.copy_root_id == second.copy_root_id
    assert first.item_count == second.item_count == 3
    assert len(set(disk.copies)) == 2


@pytest.mark.parametrize("status,error", [
    (401, StorageCredentialsExpired), (403, StorageAccessDenied), (429, StorageRateLimited),
    (507, StorageQuotaExceeded), (503, StorageUnavailable),
])
def test_provider_errors_are_normalized(fake_adapter, status, error):
    disk, adapter = fake_adapter
    disk.error = status
    with pytest.raises(error):
        adapter.get_object("disk:/Root")


def test_access_token_is_never_present_in_error(fake_adapter):
    disk, adapter = fake_adapter
    disk.error = 503
    with pytest.raises(StorageUnavailable) as caught:
        adapter.get_object("disk:/Root")
    assert "secret-not-logged" not in str(caught.value)
