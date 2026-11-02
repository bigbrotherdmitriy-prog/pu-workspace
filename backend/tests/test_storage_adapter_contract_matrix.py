from app.integrations.contracts import StorageAdapter
from app.integrations.yandex_disk import YandexDiskStorageAdapter
from app.organizer_engine.drive import DriveClient


def test_google_and_yandex_implement_the_same_core_contract():
    required = {"health", "get_object", "list_children", "walk_tree", "read_bytes", "copy_folder_tree"}
    mutable = {"get_file_meta", "create_folder", "assert_inside_copy", "rename_file", "move_file", "trash_safe_copy"}
    for adapter_type in (DriveClient, YandexDiskStorageAdapter):
        assert required.issubset(set(dir(adapter_type))), adapter_type.__name__
        assert mutable.issubset(set(dir(adapter_type))), adapter_type.__name__


def test_core_contract_has_no_provider_specific_method_names():
    names = set(dir(StorageAdapter))
    assert not any("google" in name or "yandex" in name for name in names)


import pytest

from app.integrations.contracts import StorageQuotaExceeded
from app.integrations.pu_storage import PuStorageAdapter, RdVersionContentStore


SIX = b"abcdef"
TWO = b"ab"


def _adapter(tmp_path, project_id=1, quota=10):
    return PuStorageAdapter(project_id=project_id, root=tmp_path, quota_bytes=quota)


def _store(tmp_path, quota=10):
    return RdVersionContentStore(root=tmp_path, quota_bytes=quota)


def _mutable_files(adapter):
    root = adapter._ensure_project_root()
    return sorted(p.name for p in root.iterdir() if p.is_file())


def test_cas_then_mutable_write_rejects_and_preserves_cas(tmp_path):
    adapter = _adapter(tmp_path)
    store = _store(tmp_path)
    digest = store.put(1, SIX)
    before = store.read(1, digest, len(SIX))
    with pytest.raises(StorageQuotaExceeded):
        adapter.write_file(None, "mutable.bin", SIX)
    assert store.read(1, digest, len(SIX)) == before
    assert _mutable_files(adapter) == []


def test_mutable_then_cas_rejects_and_preserves_mutable(tmp_path):
    adapter = _adapter(tmp_path)
    store = _store(tmp_path)
    file_id = adapter.write_file(None, "mutable.bin", SIX)
    before, _ = adapter.read_bytes(file_id, len(SIX))
    with pytest.raises(StorageQuotaExceeded):
        store.put(1, SIX)
    after, _ = adapter.read_bytes(file_id, len(SIX))
    assert after == before
    project_dir = store._project_dir(1, create=False)
    assert not any(p.name.startswith(".tmp-") for p in project_dir.iterdir())


def test_copy_folder_tree_rejects_under_quota_and_leaves_source(tmp_path):
    adapter = _adapter(tmp_path)
    folder_id = adapter.create_folder("src")
    adapter.write_file(folder_id, "a.bin", SIX)
    before = adapter.walk_tree(folder_id)
    with pytest.raises(StorageQuotaExceeded):
        adapter.copy_folder_tree(folder_id, "pu:/", "src")
    assert adapter.walk_tree(folder_id) == before
    root = adapter._ensure_project_root()
    assert sorted(p.name for p in root.iterdir()) == ["src"]


def test_project_budgets_are_independent(tmp_path):
    a1 = _adapter(tmp_path, project_id=1, quota=10)
    a2 = _adapter(tmp_path, project_id=2, quota=10)
    a1.write_file(None, "one.bin", TWO)
    a2.write_file(None, "two.bin", TWO)
    assert a1.usage() == (2, 10)
    assert a2.usage() == (2, 10)


def test_cas_dedup_does_not_double_usage_and_usage_includes_retained_cas(tmp_path):
    adapter = _adapter(tmp_path)
    store = _store(tmp_path)
    store.put(1, SIX)
    store.put(1, SIX)
    assert store._used_bytes(1) == len(SIX)
    used, quota = adapter.usage()
    assert quota == 10
    assert used == len(SIX)


def test_copy_partial_tree_is_not_public_before_completion(tmp_path, monkeypatch):
    """Visibility invariant: an in-progress copy must not be publicly listable.

    Scope: this test only checks publication visibility of a partially written
    copy tree. It does not assert fsync/crash durability, nor source-snapshot
    semantics. A post-error cleanup assertion alone would not prove atomic
    publication, so the visibility observation is made at the copy boundary.
    """
    import shutil

    adapter = _adapter(tmp_path, project_id=1, quota=1000)
    source_id = adapter.write_file(None, "own.bin", b"OWN")
    destination_id = adapter.create_folder("dest")

    observations = {"hook_called": False, "children": None, "partial_readable": None}
    original_copytree = shutil.copytree

    def _hooked_copytree(src, dst, *args, **kwargs):
        observations["hook_called"] = True
        # Perform real filesystem writes on tmp_path to simulate a partial copy.
        import os

        os.makedirs(dst, exist_ok=True)
        partial_path = os.path.join(dst, "partial.bin")
        with open(partial_path, "wb") as handle:
            handle.write(b"PARTIAL")
        # Observe what ordinary adapter listing exposes mid-copy.
        observations["children"] = adapter.list_children(destination_id)
        try:
            with open(partial_path, "rb") as handle:
                observations["partial_readable"] = handle.read()
        except OSError:
            observations["partial_readable"] = None
        raise OSError("simulated copy interruption")

    monkeypatch.setattr(shutil, "copytree", _hooked_copytree)
    try:
        with pytest.raises(OSError):
            adapter.copy_folder_tree(source_id, "pu:/", "dest")
    finally:
        monkeypatch.setattr(shutil, "copytree", original_copytree)

    # The hook must actually have been triggered at the real copy boundary.
    assert observations["hook_called"] is True

    # Desired invariant: no final copy entry and no readable partial payload
    # is exposed through ordinary adapter listing before completion.
    children = observations["children"]
    assert children is not None
    names = []
    for child in children:
        if isinstance(child, dict):
            names.append(child.get("name"))
        else:
            names.append(getattr(child, "name", child))
    assert "partial.bin" not in names
    assert observations["partial_readable"] is None

    # Source preservation is asserted separately from the visibility invariant.
    source_bytes, _ = adapter.read_bytes(source_id, len(b"OWN"))
    assert source_bytes == b"OWN"
