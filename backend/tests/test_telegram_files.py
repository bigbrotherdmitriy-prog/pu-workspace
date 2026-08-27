import io
import zipfile
from app.api.telegram import _incoming_file, _public_download_error
from app.organizer_engine.content import extract_text


def test_telegram_docx_text_can_be_extracted():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:document xmlns:w='x'><w:p><w:t>Просим предоставить акт.</w:t></w:p></w:document>")
    assert extract_text(buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "request.docx") == "Просим предоставить акт."


def test_largest_telegram_photo_is_treated_as_a_file():
    item = _incoming_file({"message_id": 15, "photo": [{"file_id": "small"}, {"file_id": "large"}]})
    assert item["file_id"] == "large"
    assert item["mime_type"] == "image/jpeg"
    assert item["file_name"] == "telegram-photo-15.jpg"


def test_download_errors_do_not_expose_internal_urls():
    message = _public_download_error(RuntimeError("http://172.19.0.1:18080/file/secret"))
    assert "172.19.0.1" not in message
    assert "secret" not in message
