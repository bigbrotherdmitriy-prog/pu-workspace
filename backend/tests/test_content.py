import io
import unittest
import zipfile
from unittest.mock import patch

from app.organizer_engine.content import extract_text


class ContentExtractionTests(unittest.TestCase):
    def test_extracts_docx_xml_text(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="urn:w"><w:p><w:t>Договор поставки</w:t></w:p></w:document>',
            )
        self.assertEqual(
            extract_text(buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "x.docx"),
            "Договор поставки",
        )

    def test_extracts_utf8_text(self):
        self.assertEqual(extract_text("Смета объекта".encode(), "text/plain", "x.txt"), "Смета объекта")

    @patch("app.organizer_engine.content._ocr_text", return_value="Распознанный текст сканированного акта")
    def test_uses_local_ocr_for_image(self, ocr):
        self.assertEqual(
            extract_text(b"image", "image/png", "scan.png"),
            "Распознанный текст сканированного акта",
        )
        ocr.assert_called_once()

    @patch("app.organizer_engine.content._ocr_text", return_value="")
    def test_keeps_native_pdf_text_when_ocr_has_no_better_result(self, ocr):
        with patch("pypdf.PdfReader") as reader:
            reader.return_value.pages = [type("Page", (), {"extract_text": lambda self: "Коротко"})()]
            self.assertEqual(extract_text(b"pdf", "application/pdf", "x.pdf"), "Коротко")
        ocr.assert_called_once()


if __name__ == "__main__":
    unittest.main()
