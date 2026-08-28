import unittest

from app.organizer_engine.drive import DriveClient, UnsafeDriveMutation
from app.integrations.contracts import StorageAdapter


class _Request:
    def __init__(self, value): self.value = value
    def execute(self): return self.value


class _Files:
    def __init__(self, metadata):
        self.metadata = metadata
        self.updates = []

    def get(self, fileId, fields): return _Request(self.metadata[fileId])
    def update(self, **kwargs):
        self.updates.append(kwargs)
        return _Request({"id": kwargs["fileId"]})


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


if __name__ == "__main__":
    unittest.main()
