import unittest

from app.organizer_engine.drive import DriveClient, UnsafeDriveMutation
from app.organizer_engine.types import DriveFile
from app.integrations.contracts import MutableStorageAdapter, StorageAdapter


class _Request:
    def __init__(self, value): self.value = value
    def execute(self): return self.value


class _Files:
    def __init__(self, metadata):
        self.metadata = metadata
        self.updates = []
        self.creates = []

    def get(self, fileId, fields, **kwargs): return _Request(self.metadata[fileId])
    def update(self, **kwargs):
        self.updates.append(kwargs)
        return _Request({"id": kwargs["fileId"]})
    def list(self, **kwargs):
        parent = kwargs["q"].split("'", 2)[1]
        rows = [value for value in self.metadata.values() if parent in (value.get("parents") or [])]
        return _Request({"files": rows})
    def create(self, body, fields):
        self.creates.append(body)
        file_id = f"created-{len(self.creates)}"
        self.metadata[file_id] = {
            "id": file_id, "name": body["name"], "mimeType": body["mimeType"], "parents": body["parents"],
            "appProperties": body.get("appProperties") or {},
        }
        return _Request({"id": file_id})
    def copy(self, fileId, body, fields):
        self.creates.append(body)
        new_id = f"copied-{len(self.creates)}"
        source = self.metadata[fileId]
        self.metadata[new_id] = {
            "id": new_id, "name": body.get("name") or source["name"], "mimeType": source["mimeType"],
            "parents": body["parents"], "appProperties": body.get("appProperties") or {},
        }
        return _Request({"id": new_id})


class _Service:
    def __init__(self, metadata): self.resource = _Files(metadata)
    def files(self): return self.resource


class DriveBoundaryTests(unittest.TestCase):
    def setUp(self):
        def meta(file_id, name, parent):
            return {"id": file_id, "name": name, "mimeType": "application/pdf", "parents": [parent] if parent else []}
        self.service = _Service({
            "original": meta("original", "original.pdf", "outside"),
            "copy": {"id": "copy", "name": "copy", "mimeType": "application/vnd.google-apps.folder", "parents": ["outside"]},
            "copied-file": meta("copied-file", "copy.pdf", "copy"),
            "outside": {"id": "outside", "name": "outside", "mimeType": "application/vnd.google-apps.folder", "parents": []},
        })
        self.drive = DriveClient(self.service)

    def test_google_drive_client_is_a_storage_adapter(self):
        self.assertIsInstance(self.drive, StorageAdapter)
        self.assertIsInstance(self.drive, MutableStorageAdapter)
        item = self.drive.get_object("copy")
        self.assertTrue(item.is_folder)
        self.assertEqual(item.provider, "google_drive")

    def test_rename_outside_copy_is_blocked_before_api_update(self):
        with self.assertRaises(UnsafeDriveMutation):
            self.drive.rename_file("original", "changed.pdf", "copy")
        self.assertEqual(self.service.resource.updates, [])

    def test_rename_inside_copy_is_allowed(self):
        self.drive.rename_file("copied-file", "changed.pdf", "copy")
        self.assertEqual(len(self.service.resource.updates), 1)

    def test_content_progress_counts_every_item_including_skipped_folders(self):
        items = [
            type("Item", (), {"is_folder": True, "size": None})(),
            type("Item", (), {"is_folder": False, "size": 100_000_000})(),
        ]
        progress = []

        extracted, failed = self.drive.populate_content(
            items,
            on_progress=lambda processed, total: progress.append((processed, total)),
        )

        self.assertEqual((extracted, failed), (0, 0))
        self.assertEqual(progress, [(1, 2), (2, 2)])

    def test_repeated_safe_copy_with_idempotency_key_does_not_duplicate_root(self):
        first = self.drive.copy_folder_tree("copy", "outside", "copy", source_items=[], idempotency_key="same")
        second = self.drive.copy_folder_tree("copy", "outside", "copy", source_items=[], idempotency_key="same")
        self.assertEqual(first.copy_root_id, second.copy_root_id)
        self.assertEqual(len(self.service.resource.creates), 1)

    def test_crash_mid_copy_retry_resumes_instead_of_duplicating_root_or_children(self):
        """Regression guard for the gap found in the storage-adapters preflight (2.4.d):
        a crash between creating the copy root and persisting copy_folder_id must
        not make a retry create a second, orphaned root or re-copy what already
        exists — it must reconcile by ownership marker, not by folder name.
        """
        key = "organizer-session-1"
        # Simulate a prior attempt that crashed after copying "source-a" but
        # before "source-b" and before the caller ever saw a CopyResult.
        self.service.resource.metadata["prior-root"] = {
            "id": "prior-root", "name": "Source (безопасная копия organizer-session-1)",
            "mimeType": "application/vnd.google-apps.folder", "parents": ["outside"],
            "appProperties": {"puManagedCopyKey": key},
        }
        self.service.resource.metadata["prior-file-a"] = {
            "id": "prior-file-a", "name": "a.pdf", "mimeType": "application/pdf",
            "parents": ["prior-root"],
            "appProperties": {"puManagedCopyKey": key, "puManagedSourceId": "source-a"},
        }
        # The originals being copied must themselves resolve through the fake
        # service, exactly like real Drive source files would.
        self.service.resource.metadata["source-a"] = {
            "id": "source-a", "name": "a.pdf", "mimeType": "application/pdf", "parents": ["source-root"],
        }
        self.service.resource.metadata["source-b"] = {
            "id": "source-b", "name": "b.pdf", "mimeType": "application/pdf", "parents": ["source-root"],
        }
        source_items = [
            DriveFile("source-a", "a.pdf", "application/pdf", "source-root"),
            DriveFile("source-b", "b.pdf", "application/pdf", "source-root"),
        ]
        result = self.drive.copy_folder_tree(
            "source-root", "outside", "Source", source_items=source_items, idempotency_key=key,
        )
        self.assertEqual(result.copy_root_id, "prior-root")
        self.assertEqual(result.id_map["source-a"], "prior-file-a")
        self.assertEqual(result.id_map["source-b"], "copied-1")
        # Only the missing child (source-b) was actually created/copied.
        self.assertEqual(len(self.service.resource.creates), 1)
        copied_body = self.service.resource.creates[0]
        self.assertEqual(copied_body.get("appProperties"), {"puManagedCopyKey": key, "puManagedSourceId": "source-b"})


if __name__ == "__main__":
    unittest.main()
