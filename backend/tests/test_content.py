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

    def test_extracts_xlsx_and_preserves_rows_and_columns(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "xl/sharedStrings.xml",
                '<sst xmlns="urn:x"><si><t>Этап</t></si><si><t>Сумма</t></si><si><t>Монтаж</t></si></sst>',
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                '<worksheet xmlns="urn:x"><sheetData>'
                '<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
                '<row><c t="s"><v>2</v></c><c><v>1250000</v></c></row>'
                '</sheetData></worksheet>',
            )
        self.assertEqual(
            extract_text(
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "ГПР.xlsx",
            ),
            "Этап\tСумма\nМонтаж\t1250000",
        )

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
