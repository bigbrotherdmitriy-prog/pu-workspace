from app.organizer_engine.executor import source_metadata_changed
from app.organizer_engine.types import DriveFile


def _current(**changes):
    values = dict(id="f1", name="contract.pdf", mime_type="application/pdf", parent_id="p1", modified_time="2026-08-26T10:00:00Z", md5_checksum="abc")
    values.update(changes)
    return DriveFile(**values)


def _expected(**changes):
    values = dict(source="contract.pdf", current_parent_id="p1", source_modified_at="2026-08-26T10:00:00Z", source_checksum="abc")
    values.update(changes)
    return values


def test_unchanged_source_passes_recheck():
    assert source_metadata_changed(_expected(), _current()) is False


def test_rename_is_a_conflict():
    assert source_metadata_changed(_expected(), _current(name="changed.pdf")) is True


def test_parent_change_is_a_conflict():
    assert source_metadata_changed(_expected(), _current(parent_id="p2")) is True


def test_content_change_is_a_conflict():
    assert source_metadata_changed(_expected(), _current(md5_checksum="def")) is True


def test_drive_timestamp_settling_is_allowed_when_checksum_matches():
    assert source_metadata_changed(
        _expected(), _current(modified_time="2026-08-26T10:00:02Z")
    ) is False


def test_native_file_timestamp_change_remains_a_conflict_without_checksum():
    assert source_metadata_changed(
        _expected(source_checksum=None),
        _current(md5_checksum=None, modified_time="2026-08-26T10:00:02Z"),
    ) is True
