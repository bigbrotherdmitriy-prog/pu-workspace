import io
import zipfile
from app.organizer_engine.content import extract_text


def test_telegram_docx_text_can_be_extracted():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:document xmlns:w='x'><w:p><w:t>Просим предоставить акт.</w:t></w:p></w:document>")
    assert extract_text(buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "request.docx") == "Просим предоставить акт."
