import base64
import pytest
from app.api.local_upload import LocalFile, decode_local_file


def test_local_file_base64_is_decoded():
    item = LocalFile(path="Проект/письмо.txt", mime_type="text/plain", content_base64=base64.b64encode("текст".encode()).decode())
    assert decode_local_file(item) == "текст".encode()


def test_invalid_local_file_is_rejected():
    with pytest.raises(ValueError):
        decode_local_file(LocalFile(path="bad.txt", content_base64="%%%"))
