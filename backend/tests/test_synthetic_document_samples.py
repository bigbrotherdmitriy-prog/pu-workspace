from io import BytesIO
import zipfile
from xml.etree import ElementTree
from openpyxl import load_workbook
from app.synthetic_document_samples import build_synthetic_samples,MARKER


def test_all_samples_marked_readable_and_print_bounded():
    samples,meta=build_synthetic_samples()
    assert len(samples)==7 and meta['synthetic'] is True and meta['not_for_signing'] is True
    assert meta['fact_qty']=='1.000' and meta['act_period_total']=='10.00'
    for name,data in samples.items():
        assert name.startswith('SYNTHETIC-')
        with zipfile.ZipFile(BytesIO(data)) as z:
            assert z.testzip() is None
            if name.endswith('.docx'):
                xml=z.read('word/document.xml').decode();ElementTree.fromstring(xml)
                assert MARKER in xml and 'w:pgSz' in xml
            else:
                wb=load_workbook(BytesIO(data))
                for ws in wb:
                    assert ws.cell(1,1).value==MARKER and ws.print_area
                    assert ws.page_setup.fitToWidth==1 and ws.page_setup.orientation=='landscape'


def test_ks_samples_use_journal_fact_not_full_estimate_quantity():
    samples,meta=build_synthetic_samples()
    with zipfile.ZipFile(BytesIO(samples['SYNTHETIC-KS2.docx'])) as z:
        xml=z.read('word/document.xml').decode()
        assert 'SYNTHETIC work' in xml and '10,00' in xml
    assert meta['estimate_qty']=='2' and meta['fact_qty']=='1.000'
