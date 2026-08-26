from app.document_engine import _parse_time


def test_parse_drive_modified_time():
    value = _parse_time("2026-08-25T10:20:30Z")
    assert value is not None
    assert value.year == 2026
    assert value.tzinfo is not None


def test_invalid_modified_time_is_safe():
    assert _parse_time("not-a-date") is None
