from collections import deque
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from googleapiclient.errors import HttpError
from httplib2 import Response
import pytest

from app.integrations.contracts import StorageRateLimited
from app.organizer_engine.drive import DriveClient, FOLDER_MIME, SHORTCUT_MIME


class Request:
    def __init__(self, outcomes):
        self.outcomes = deque(outcomes)

    def execute(self):
        value = self.outcomes.popleft()
        if isinstance(value, Exception):
            raise value
        return value


class Files:
    def __init__(self, *, get_outcomes=None, list_outcomes=None):
        self.get_outcomes = get_outcomes or []
        self.list_outcomes = deque(list_outcomes or [])
        self.get_kwargs = []
        self.list_kwargs = []

    def get(self, **kwargs):
        self.get_kwargs.append(kwargs)
        return Request(self.get_outcomes)

    def list(self, **kwargs):
        self.list_kwargs.append(kwargs)
        return Request([self.list_outcomes.popleft()])


class Service:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


def meta(identifier="shortcut", mime=SHORTCUT_MIME):
    return {
        "id": identifier,
        "name": "Synthetic shortcut",
        "mimeType": mime,
        "parents": ["root"],
        "modifiedTime": "2026-09-09T08:00:00Z",
        "version": "19",
        "webViewLink": "https://drive.google.test/open/shortcut",
        "driveId": "shared-drive",
        "capabilities": {"canDownload": True, "canEdit": False},
        "shortcutDetails": {
            "targetId": "target-folder",
            "targetMimeType": FOLDER_MIME,
            "targetResourceKey": "opaque-key",
        },
    }


def test_shared_drive_and_shortcut_metadata_are_preserved_without_following_target():
    files = Files(list_outcomes=[{"files": [meta()], "nextPageToken": None}])
    client = DriveClient(Service(files), sleep=lambda _: None)
    children = client.list_children("root")
    item = children[0]

    assert files.list_kwargs == [{
        "q": "'root' in parents and trashed=false",
        "fields": files.list_kwargs[0]["fields"],
        "pageSize": 1000,
        "pageToken": None,
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }]
    assert item.provider_revision == "19" and item.provider_metadata["drive_id"] == "shared-drive"
    assert item.shortcut_target_id == "target-folder"
    assert item.shortcut_target_mime_type == FOLDER_MIME
    assert item.is_folder is False


def test_all_pages_are_read_and_read_rate_limit_is_retried_bounded():
    rate = HttpError(Response({"status": "429", "reason": "rate"}), b"{}")
    request = Request([rate, {"id": "file", "name": "A", "mimeType": "text/plain", "parents": ["root"]}])

    class RetryFiles(Files):
        def get(self, **kwargs):
            self.get_kwargs.append(kwargs)
            return request

    sleeps = []
    files = RetryFiles()
    client = DriveClient(Service(files), sleep=sleeps.append)
    assert client.get_file_meta("file").id == "file"
    assert sleeps == [0.1]
    assert files.get_kwargs[0]["supportsAllDrives"] is True

    exhausted = Request([rate, rate, rate])
    with pytest.raises(StorageRateLimited):
        client._execute_read(exhausted)


def test_google_403_rate_limit_reason_is_retried_but_plain_forbidden_is_not():
    limited = HttpError(
        Response({"status": "403", "reason": "forbidden"}),
        b'{"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}',
    )
    request = Request([limited, {"ok": True}])
    sleeps = []
    assert DriveClient(Service(Files()), sleep=sleeps.append)._execute_read(request) == {"ok": True}
    assert sleeps == [0.1]

    forbidden = HttpError(Response({"status": "403", "reason": "forbidden"}), b"{}")
    with pytest.raises(Exception, match="access is denied"):
        DriveClient(Service(Files()), sleep=lambda _: None)._execute_read(Request([forbidden]))


def test_pagination_uses_next_page_token_without_duplicate_or_truncation():
    files = Files(list_outcomes=[
        {"files": [meta("one", "text/plain")], "nextPageToken": "two"},
        {"files": [meta("two", "text/plain")], "nextPageToken": None},
    ])
    result = DriveClient(Service(files), sleep=lambda _: None).list_children("root")
    assert [item.id for item in result] == ["one", "two"]
    assert [call["pageToken"] for call in files.list_kwargs] == [None, "two"]


def test_shortcut_metadata_migration_is_sequential_current_head():
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["a54f001c0a24"]
    assert scripts.get_revision("a54f001c0a24").down_revision == "a54f001c0a23"
