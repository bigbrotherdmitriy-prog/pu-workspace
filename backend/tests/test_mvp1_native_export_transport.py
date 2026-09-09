from hashlib import sha256
from io import BytesIO

import pytest

from app.integrations.contracts import StorageUnavailable
from app.organizer_engine.drive import DriveClient, GOOGLE_NATIVE_EXPORTS


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class ExportRequest:
    def __init__(self, content):
        self.content = content

    def next_chunk(self, num_retries=0):
        return None, True


class Files:
    def __init__(self, versions, content=b"synthetic native export"):
        self.versions = iter(versions)
        self.content = content
        self.exports = []

    def get(self, **kwargs):
        version = next(self.versions)
        return Request({"id": "native", "name": "Plan", "mimeType": kwargs.pop("test_mime", None)
            or "application/vnd.google-apps.spreadsheet", "parents": ["root"], "version": version})

    def export_media(self, **kwargs):
        self.exports.append(kwargs)
        return ExportRequest(self.content)


class Service:
    def __init__(self, files): self._files = files
    def files(self): return self._files


class Downloader:
    def __init__(self, buffer, request, chunksize):
        self.buffer, self.request = buffer, request

    def next_chunk(self):
        self.buffer.write(self.request.content)
        return None, True


@pytest.mark.parametrize(("original", "export"), [
    ("application/vnd.google-apps.document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("application/vnd.google-apps.document", "application/pdf"),
    ("application/vnd.google-apps.spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("application/vnd.google-apps.spreadsheet", "text/csv"),
    ("application/vnd.google-apps.presentation", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ("application/vnd.google-apps.presentation", "application/pdf"),
])
def test_native_export_matrix_is_complete_and_version_pinned(monkeypatch, original, export):
    content = b"synthetic export"
    files = Files(["17", "17"], content)
    files.get = lambda **kwargs: Request({"id": "native", "name": "Native", "mimeType": original,
        "parents": ["root"], "version": next(files.versions)})
    monkeypatch.setattr("app.organizer_engine.drive.MediaIoBaseDownload", Downloader)
    result = DriveClient(Service(files), sleep=lambda _: None).read_native_export_exact(
        "native", revision="17", mime_type=export, max_bytes=1024)
    assert export in GOOGLE_NATIVE_EXPORTS[original]
    assert result.content == content and result.sha256 == sha256(content).hexdigest()
    assert result.original_revision == "17" and result.export_mime_type == export
    assert files.exports == [{"fileId": "native", "mimeType": export}]


def test_native_export_rejects_changed_source_and_never_returns_bytes(monkeypatch):
    files = Files(["17", "18"], b"not returned")
    monkeypatch.setattr("app.organizer_engine.drive.MediaIoBaseDownload", Downloader)
    with pytest.raises(StorageUnavailable, match="native_export_source_conflict"):
        DriveClient(Service(files), sleep=lambda _: None).read_native_export_exact(
            "native", revision="17",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            max_bytes=1024)


def test_native_export_rejects_unsupported_pair_before_export():
    files = Files(["17"])
    with pytest.raises(StorageUnavailable, match="native_export_source_conflict"):
        DriveClient(Service(files), sleep=lambda _: None).read_native_export_exact(
            "native", revision="17", mime_type="application/pdf", max_bytes=1024)
    assert files.exports == []
