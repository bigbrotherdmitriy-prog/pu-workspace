import io
import subprocess
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.organizer_engine.content import _tesseract, extract_text, extract_text_result


class ContentExtractionTests(unittest.TestCase):
    @patch("app.organizer_engine.content.OCR_PSM", 1)
    @patch("app.organizer_engine.content.OCR_FALLBACK_PSM", 6)
    @patch("app.organizer_engine.content.OCR_ADAPTIVE_FALLBACK", True)
    @patch("app.organizer_engine.content.shutil.which", return_value="/usr/bin/tesseract")
    @patch("app.organizer_engine.content.subprocess.run")
    def test_fragmented_auto_layout_uses_single_block_fallback(self, run, _which):
        broken = (
            "Поставщик передает товар покупателю по договору. "
            "Поку обязан принять оплаченный ый товар лично или через у го пред теля. "
            "Передача товара производится после оплаты и проверки документов."
        )
        repaired = (
            "Поставщик передает товар покупателю по договору. "
            "Покупатель обязан принять оплаченный товар лично или через уполномоченного представителя. "
            "Передача товара производится после оплаты и проверки документов."
        )
        run.side_effect = [SimpleNamespace(returncode=0, stdout=broken), SimpleNamespace(returncode=0, stdout=repaired)]

        self.assertEqual(_tesseract(Path("scan.jpg"), 30), repaired)
        self.assertEqual([call.args[0][call.args[0].index("--psm") + 1] for call in run.call_args_list], ["1", "6"])

    @patch("app.organizer_engine.content.OCR_PSM", 1)
    @patch("app.organizer_engine.content.OCR_FALLBACK_PSM", 6)
    @patch("app.organizer_engine.content.OCR_ADAPTIVE_FALLBACK", True)
    @patch("app.organizer_engine.content.shutil.which", return_value="/usr/bin/tesseract")
    @patch("app.organizer_engine.content.subprocess.run")
    def test_intact_ocr_does_not_pay_for_fallback(self, run, _which):
        intact = "Покупатель обязан принять оплаченный товар лично через представителя после проверки документов договора поставки."
        run.return_value = SimpleNamespace(returncode=0, stdout=intact)

        self.assertEqual(_tesseract(Path("scan.jpg"), 30), intact)
        self.assertEqual(run.call_count, 1)

    @patch("app.organizer_engine.content.OCR_PSM", 1)
    @patch("app.organizer_engine.content.OCR_FALLBACK_PSM", 6)
    @patch("app.organizer_engine.content.OCR_ADAPTIVE_FALLBACK", True)
    @patch("app.organizer_engine.content.shutil.which", return_value="/usr/bin/tesseract")
    @patch("app.organizer_engine.content.subprocess.run")
    def test_fallback_cannot_drop_invoice_numbers(self, run, _which):
        broken = (
            "Счет 951 от 22 января 2026. Поку обязан при ый товар через у го пред теля. "
            "Сумма 147360 НДС 26573 ИНН 7716888076 КПП 771501001 и условия поставки товара. "
            "Передача выполняется после проверки договора, оплаты счета и предъявления документов."
        )
        loses_numbers = "Покупатель обязан принять оплаченный товар через уполномоченного представителя."
        run.side_effect = [SimpleNamespace(returncode=0, stdout=broken), SimpleNamespace(returncode=0, stdout=loses_numbers)]

        self.assertEqual(_tesseract(Path("invoice.jpg"), 30), broken)
        self.assertEqual(run.call_count, 2)

    @patch("app.organizer_engine.content.OCR_PSM", 1)
    @patch("app.organizer_engine.content.OCR_FALLBACK_PSM", 6)
    @patch("app.organizer_engine.content.shutil.which", return_value="/usr/bin/tesseract")
    @patch("app.organizer_engine.content.subprocess.run")
    def test_fallback_timeout_keeps_primary_result(self, run, _which):
        broken = (
            "Поставщик передает товар покупателю по договору. "
            "Поку обязан принять оплаченный ый товар лично или через у го пред теля. "
            "Передача товара производится после оплаты и проверки документов."
        )
        run.side_effect = [SimpleNamespace(returncode=0, stdout=broken), subprocess.TimeoutExpired("tesseract", 1)]

        self.assertEqual(_tesseract(Path("scan.jpg"), 30), broken)

    @patch("app.organizer_engine.content.OCR_PSM", 1)
    @patch("app.organizer_engine.content.OCR_FALLBACK_PSM", 6)
    @patch("app.organizer_engine.content.OCR_ADAPTIVE_FALLBACK", False)
    @patch("app.organizer_engine.content.shutil.which", return_value="/usr/bin/tesseract")
    @patch("app.organizer_engine.content.subprocess.run")
    def test_administrator_can_disable_adaptive_fallback(self, run, _which):
        broken = (
            "Поставщик передает товар покупателю по договору. "
            "Поку обязан принять оплаченный ый товар лично или через у го пред теля. "
            "Передача товара производится после оплаты и проверки документов."
        )
        run.return_value = SimpleNamespace(returncode=0, stdout=broken)

        self.assertEqual(_tesseract(Path("scan.jpg"), 30), broken)
        self.assertEqual(run.call_count, 1)

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

    @patch("app.organizer_engine.content._ocr_pdf_pages", return_value={})
    def test_keeps_native_pdf_text_when_ocr_has_no_better_result(self, ocr):
        with patch("pypdf.PdfReader") as reader:
            reader.return_value.pages = [type("Page", (), {"extract_text": lambda self: "Коротко"})()]
            self.assertEqual(extract_text(b"pdf", "application/pdf", "x.pdf"), "Коротко")
        ocr.assert_called_once()

    @patch("app.organizer_engine.content._ocr_pdf_pages", return_value={2: "Распознанный акт выполненных работ"})
    def test_hybrid_pdf_keeps_native_page_and_ocrs_scanned_page(self, ocr):
        with patch("pypdf.PdfReader") as reader:
            reader.return_value.pages = [
                type("Page", (), {"extract_text": lambda self: "Договор поставки с достаточно длинным текстовым слоем"})(),
                type("Page", (), {"extract_text": lambda self: ""})(),
            ]
            result = extract_text_result(b"pdf", "application/pdf", "mixed.pdf")
        self.assertIn("Договор поставки", result.text)
        self.assertIn("Распознанный акт", result.text)
        self.assertEqual(result.method, "hybrid")
        self.assertEqual(result.ocr_pages, 1)
        ocr.assert_called_once_with(b"pdf", {2})

    @patch("app.organizer_engine.content._ocr_pdf_pages", return_value={1: "Счёт на оплату № 42 сумма 125 000 рублей"})
    def test_scanned_pdf_reports_ocr_quality_metadata(self, _ocr):
        with patch("pypdf.PdfReader") as reader:
            reader.return_value.pages = [type("Page", (), {"extract_text": lambda self: ""})()]
            result = extract_text_result(b"pdf", "application/pdf", "invoice.pdf")
        self.assertEqual(result.method, "ocr")
        self.assertEqual(result.total_pages, 1)
        self.assertEqual(result.ocr_pages, 1)
        self.assertIn(result.quality, {"medium", "high"})


if __name__ == "__main__":
    unittest.main()
