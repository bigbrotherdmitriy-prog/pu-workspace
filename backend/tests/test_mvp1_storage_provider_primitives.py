from __future__ import annotations

import httpx
import pytest

from app.integrations.storage_mutation_live import (
    ExactPreconditionUnavailable,
    GoogleDriveExactMutationAdapter,
    YandexDiskExactMutationAdapter,
)
from app.integrations.yandex_disk import YandexDiskStorageAdapter
from app.organizer_engine.drive import DriveClient


class _BombGoogleFiles:
    def get(self, **_kwargs):
        raise AssertionError("Google HTTP request must not be constructed")

    def update(self, **_kwargs):
        raise AssertionError("Google HTTP request must not be constructed")


class _BombGoogleService:
    def __init__(self):
        self.files_calls = 0

    def files(self):
        self.files_calls += 1
        return _BombGoogleFiles()


@pytest.mark.parametrize("enabled", [False, True])
def test_google_v3_client_is_denied_before_http_without_atomic_precondition(enabled):
    service = _BombGoogleService()
    client = DriveClient(service)
    wrapper = GoogleDriveExactMutationAdapter(client, enabled=enabled)

    assert client.supports_exact_mutation_preconditions is False
    assert client.exact_mutation_blocker == "drive_v3_update_has_no_exact_revision_precondition"
    assert wrapper.health().ready is False
    with pytest.raises(ExactPreconditionUnavailable):
        wrapper.object_revision("opaque-google-id")
    with pytest.raises(ExactPreconditionUnavailable):
        wrapper.rename_file("opaque-google-id", "new.pdf", "opaque-copy-root")
    assert service.files_calls == 0


@pytest.mark.parametrize("enabled", [False, True])
def test_yandex_rest_client_is_denied_before_http_without_atomic_precondition(enabled):
    requests: list[httpx.Request] = []

    def forbidden_transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("Yandex HTTP request must not be sent")

    http_client = httpx.Client(transport=httpx.MockTransport(forbidden_transport))
    client = YandexDiskStorageAdapter("synthetic-token", client=http_client)
    wrapper = YandexDiskExactMutationAdapter(client, enabled=enabled)

    assert client.supports_exact_mutation_preconditions is False
    assert client.exact_mutation_blocker == "yandex_move_has_no_exact_revision_precondition"
    assert wrapper.health().ready is False
    with pytest.raises(ExactPreconditionUnavailable):
        wrapper.object_revision("disk:/copy/file.pdf")
    with pytest.raises(ExactPreconditionUnavailable):
        wrapper.move_file("disk:/copy/file.pdf", "disk:/copy/nested", "disk:/copy", "disk:/copy")
    assert requests == []
    http_client.close()


def test_readable_provider_version_is_not_claimed_as_atomic_write_precondition():
    """A version in metadata alone must never advertise mutation capability."""
    drive = DriveClient(_BombGoogleService())
    yandex = YandexDiskStorageAdapter.__new__(YandexDiskStorageAdapter)
    assert drive.supports_exact_mutation_preconditions is False
    assert yandex.supports_exact_mutation_preconditions is False
