from collections import deque

import pytest

from app.integrations.google_storage_mutation import GoogleDriveConditionalMutationClient
from app.integrations.storage_mutation_live import ExactPreconditionUnavailable


class Response(dict):
    status = 200


class Request:
    def __init__(self, payload, etag):
        self.payload = payload
        self.resp = Response(etag=etag)
        self.headers = {}

    def execute(self): return self.payload


class Files:
    def __init__(self, reads):
        self.reads = deque(reads); self.updates = []

    def get(self, **kwargs):
        payload, etag = self.reads.popleft()
        return Request(payload, etag)

    def update(self, **kwargs):
        request = Request(kwargs["body"], '"after"')
        self.updates.append((kwargs, request))
        return request


class Service:
    def __init__(self, files): self._files = files
    def files(self): return self._files


def meta(name="Before", version="7", parent="safe-root", object_id="item"):
    return {"id": object_id, "name": name, "mimeType": "application/pdf",
            "parents": [parent], "version": version, "trashed": False}


def root():
    return {"id": "safe-root", "name": "Safe", "mimeType": "application/vnd.google-apps.folder",
            "parents": [], "version": "2", "trashed": False}


def test_google_conditional_rename_uses_exact_etag_and_returns_new_revision():
    files = Files([(meta(), '"before"'), (root(), '"root"'),
                   (meta(), '"before"'), (meta("After", "8"), '"after"'), (root(), '"root"')])
    client = GoogleDriveConditionalMutationClient(Service(files))
    before = client.get_exact_state("item")
    after = client.rename_if_revision("item", "After", expected_revision=before.revision, operation_key="opaque")
    kwargs, request = files.updates[0]
    assert request.headers == {"If-Match": '"before"'}
    assert kwargs["supportsAllDrives"] is True and kwargs["body"] == {"name": "After"}
    assert after.name == "After" and after.revision != before.revision


def test_changed_metadata_is_rejected_before_mutation():
    files = Files([(meta(), '"before"'), (root(), '"root"'), (meta(version="8"), '"changed"')])
    client = GoogleDriveConditionalMutationClient(Service(files))
    before = client.get_exact_state("item")
    with pytest.raises(ExactPreconditionUnavailable, match="conflict_source_changed"):
        client.rename_if_revision("item", "After", expected_revision=before.revision, operation_key="opaque")
    assert files.updates == []


def test_missing_etag_fails_closed():
    with pytest.raises(ExactPreconditionUnavailable, match="exact_provider_etag_unavailable"):
        GoogleDriveConditionalMutationClient(Service(Files([(meta(), "")]))).get_exact_state("item")


def test_ancestry_cycle_fails_closed():
    files = Files([(meta(parent="loop"), '"item"'),
                   (meta(parent="loop", object_id="loop"), '"loop"')])
    with pytest.raises(ExactPreconditionUnavailable, match="invalid_provider_ancestry"):
        GoogleDriveConditionalMutationClient(Service(files)).get_exact_state("item")
