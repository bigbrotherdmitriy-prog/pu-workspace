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
