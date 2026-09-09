import pytest

from app.core.integration_types import StorageObject
from app.organizer_engine.drive import DriveClient, UnsafeDriveMutation


def item(identifier, name, parent, *, folder=False, key=None, source=None):
    properties = {}
    if key: properties["puManagedCopyKey"] = key
    if source: properties["puManagedSourceId"] = source
    return StorageObject(identifier, name,
        "application/vnd.google-apps.folder" if folder else "application/pdf", parent,
        object_type="folder" if folder else "file", provider="google_drive",
        provider_metadata={"app_properties": properties})


def test_managed_copy_retry_reconciles_existing_children_without_duplicate():
    key = "managed-" + "a" * 32
    client = object.__new__(DriveClient)
    children = {
        "parent": [item("copy", "Copy", "parent", folder=True, key=key)],
        "copy": [item("existing", "A.pdf", "copy", key=key, source="source-a")],
    }
    client.list_children = lambda parent: list(children.get(parent, []))
    client.create_folder = lambda *a, **k: pytest.fail("duplicate root")
    copied = []
    client.copy_file = lambda source, parent, **kwargs: copied.append(source) or "new"
    source_items = [item("source-a", "A.pdf", "source"), item("source-b", "B.pdf", "source")]
    result = client.copy_folder_tree("source", "parent", "Source", source_items, idempotency_key=key)
    assert result.copy_root_id == "copy" and copied == ["source-b"]
    assert result.id_map == {"source": "copy", "source-a": "existing", "source-b": "new"}


def test_cleanup_rejects_foreign_descendant_before_trash():
    key = "managed-" + "b" * 32
    client = object.__new__(DriveClient)
    client.get_file_meta = lambda _: item("copy", "Copy", "parent", folder=True, key=key)
    client.walk_tree = lambda *_: [item("foreign", "Foreign.pdf", "copy")]
    client.trash_safe_copy = lambda _: pytest.fail("must not trash mixed subtree")
    with pytest.raises(UnsafeDriveMutation, match="managed_copy_contains_unowned_object"):
        client.trash_managed_copy("copy", key)


def test_cleanup_accepts_exact_owned_subtree_and_is_idempotent_at_provider_boundary():
    key = "managed-" + "c" * 32
    client = object.__new__(DriveClient)
    client.get_file_meta = lambda _: item("copy", "Copy", "parent", folder=True, key=key)
    client.walk_tree = lambda *_: [item("child", "Child.pdf", "copy", key=key)]
    calls = []
    client.trash_safe_copy = calls.append
    client.trash_managed_copy("copy", key)
    client.trash_managed_copy("copy", key)
    assert calls == ["copy", "copy"]
