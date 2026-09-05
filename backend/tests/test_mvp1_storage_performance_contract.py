from collections import defaultdict

import pytest

from app.core.integration_types import StorageObject
from app.integrations.yandex_disk import YandexDiskStorageAdapter
from app.organizer_engine.drive import DriveClient


def _large_tree(provider: str, folder_count: int = 256, files_per_folder: int = 8):
    root = "disk:/" if provider == "yandex_disk" else "root"
    children: dict[str, list[StorageObject]] = defaultdict(list)
    parent = root
    for number in range(folder_count):
        identifier = (
            f"disk:/customer/project/level-{number}"
            if provider == "yandex_disk"
            else f"folder-{number}"
        )
        folder = StorageObject(
            identifier, f"Level {number}", "inode/directory", parent,
            object_type="folder", provider=provider,
        )
        children[parent].append(folder)
        for file_number in range(files_per_folder):
            children[identifier].append(StorageObject(
                f"{identifier}/file-{file_number}",
                f"Document {number}-{file_number}.pdf",
                "application/pdf",
                identifier,
                size=1024,
                provider=provider,
            ))
        parent = identifier
    return root, children


@pytest.mark.parametrize("adapter_type,provider", [
    (DriveClient, "google_drive"),
    (YandexDiskStorageAdapter, "yandex_disk"),
])
def test_large_tree_scan_has_linear_provider_call_budget(adapter_type, provider):
    """A deterministic performance contract: one provider listing per folder.

    This measures algorithmic/provider-call cost rather than wall-clock time, so
    slow CI hosts cannot create a false performance regression.
    """
    root, children = _large_tree(provider)
    calls: list[str] = []
    adapter = object.__new__(adapter_type)

    def list_children(folder_id):
        calls.append(folder_id)
        return list(children.get(folder_id, ()))

    adapter.list_children = list_children
    items = adapter.walk_tree(root, limit=3_000)

    expected_items = 256 * 9
    assert len(items) == expected_items
    assert len(calls) == 257  # root plus each folder, never once per file
    assert len(calls) <= len(items) + 1
    assert len(set(item.id for item in items)) == expected_items


@pytest.mark.parametrize("adapter_type,provider", [
    (DriveClient, "google_drive"),
    (YandexDiskStorageAdapter, "yandex_disk"),
])
def test_large_tree_scan_enforces_hard_item_limit(adapter_type, provider):
    root, children = _large_tree(provider, folder_count=8, files_per_folder=8)
    adapter = object.__new__(adapter_type)
    adapter.list_children = lambda folder_id: list(children.get(folder_id, ()))

    with pytest.raises(Exception, match=r"more than 20 (items|objects)"):
        adapter.walk_tree(root, limit=20)
