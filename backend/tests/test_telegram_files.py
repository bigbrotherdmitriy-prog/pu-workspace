import io
import zipfile
from app.api.telegram import _empty_extraction_message, _incoming_file, _public_download_error
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


def test_empty_photo_extraction_reports_local_ocr_result_honestly():
    without_caption = _empty_extraction_message("image/jpeg", False)
    with_caption = _empty_extraction_message("image/jpeg", True)

    assert "Локальный OCR не смог распознать" in without_caption
    assert "не подключено" not in without_caption
    assert "Анализирую текст из подписи" in with_caption


def test_empty_scanned_document_suggests_a_better_source():
    message = _empty_extraction_message("application/pdf", False)

    assert "локальный OCR не дал результата" in message
    assert "исходный PDF/DOCX" in message
