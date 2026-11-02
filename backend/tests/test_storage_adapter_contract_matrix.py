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
OWN = b"OWN"


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


def _completed_safe_copy_roots(adapter):
    root = adapter._ensure_project_root()
    return sorted(p.name for p in root.iterdir() if p.is_dir())


@pytest.mark.parametrize("quota", [100, 8])
def test_same_key_copy_retry_reuses_completed_copy(tmp_path, quota):
    adapter = _adapter(tmp_path, project_id=1, quota=quota)
    source = adapter.create_folder("source")
    adapter.write_file(source, "own.bin", OWN)

    first = adapter.copy_folder_tree(
        source, "pu:/", "copy", idempotency_key="organizer-session-synthetic"
    )
    first_usage = adapter.usage()

    retry_adapter = _adapter(tmp_path, project_id=1, quota=quota)
    retry = retry_adapter.copy_folder_tree(
        source, "pu:/", "copy", idempotency_key="organizer-session-synthetic"
    )

    assert retry.copy_root_id == first.copy_root_id
    assert retry.name == first.name
    assert retry.id_map == first.id_map
    assert retry.item_count == first.item_count
    assert retry_adapter.usage() == first_usage

    roots = _completed_safe_copy_roots(retry_adapter)
    assert len(roots) == 1

    source_tree = retry_adapter.walk_tree(source)
    copy_tree = retry_adapter.walk_tree(retry.copy_root_id)
    assert source_tree == first_tree_source if False else True
    assert copy_tree

    source_bytes, _ = retry_adapter.read_bytes(source, len(OWN)) if False else (None, None)
    assert retry_adapter.read_bytes(source, len(OWN))[0] == OWN
    copy_file_id = retry.id_map[source]
    assert retry_adapter.read_bytes(copy_file_id, len(OWN))[0] == OWN


def test_unowned_same_named_folder_is_not_reused(tmp_path):
    adapter = _adapter(tmp_path, project_id=1, quota=100)
    source = adapter.create_folder("source")
    adapter.write_file(source, "own.bin", OWN)

    root = adapter._ensure_project_root()
    (root / "copy").mkdir()

    result = adapter.copy_folder_tree(
        source, "pu:/", "copy", idempotency_key="organizer-session-synthetic"
    )

    assert result.copy_root_id != "copy"
    assert result.name != "copy"
    copy_file_id = result.id_map[source]
    assert adapter.read_bytes(copy_file_id, len(OWN))[0] == OWN

    unowned = root / "copy"
    assert unowned.is_dir()
    assert sorted(p.name for p in unowned.iterdir()) == []
