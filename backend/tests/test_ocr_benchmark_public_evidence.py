"""The acceptance gate must score the public extraction, not a private OCR pass."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from app.ocr_quality import benchmark as target
from app.organizer_engine.content import ExtractionResult, FieldEvidence, PageExtraction


@pytest.fixture
def benchmark(monkeypatch):
    fields = {
        name: [FieldEvidence(name, name, 1, .99, "synthetic", (1, 1, 5, 5))]
        for name in target.FIELD_NAMES
    }
    case = target.BenchmarkCase("synthetic-1", "synthetic-doc", 1, ("synthetic",),
                                {name: (name,) for name in target.FIELD_NAMES}, "clean")
    page = PageExtraction(1, "synthetic", .99, "ocr", width=32, height=32)
    result = ExtractionResult("synthetic", "ocr", "high", ocr_pages=1,
                              confidence=.99, pages=[page], fields=fields)
    monkeypatch.setattr(target, "load_corpus", lambda _: [case])
    monkeypatch.setattr(target, "_tesseract_command", lambda: "synthetic-command")
    monkeypatch.setattr(target, "find_cyrillic_font", lambda _: Path("synthetic-font"))
    monkeypatch.setattr(target.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"synthetic-engine\n"))
    monkeypatch.setattr(target, "_render_case", lambda *a: Image.new("L", (32, 32), 255))
    monkeypatch.setattr(target, "_run_tsv", lambda *a: page)
    monkeypatch.setattr(target, "_preprocess_image", lambda *a: (None, None, []))
    # Private preprocessed extraction looks perfect even when the public facade fails.
    monkeypatch.setattr(target, "_extract_fields", lambda *a: fields, raising=False)
    monkeypatch.setattr(target, "extract_text_result", lambda *a: result)
    return result


def test_public_result_without_evidence_cannot_pass(benchmark):
    benchmark.fields = {}
    report = target.run_benchmark(Path("synthetic"))
    assert report["gate"]["pass"] is False
    assert report["evidence"]["extracted_fields"] == 0
    assert all(metric["recall"] == 0 for metric in report["quality"]["fields"].values())


@pytest.mark.parametrize("page,bbox", [(2, (1, 1, 5, 5)), (1, (-1, 1, 5, 5)),
                                        (1, (30, 30, 5, 5)), (1, (1, 1, float("nan"), 5))])
def test_public_out_of_page_evidence_cannot_pass(benchmark, page, bbox):
    benchmark.fields = {name: [replace(values[0], page=page, bbox=bbox)]
                        for name, values in benchmark.fields.items()}
    report = target.run_benchmark(Path("synthetic"))
    assert report["gate"]["pass"] is False
    assert report["evidence"]["coverage"] == 0


def test_failed_page_counts_all_expected_fields_as_missed(benchmark, monkeypatch):
    def failed(*args):
        raise RuntimeError("synthetic private text must not appear")
    monkeypatch.setattr(target, "extract_text_result", failed)
    report = target.run_benchmark(Path("synthetic"))
    assert report["gate"]["pass"] is False
    assert all(metric["fn"] == 1 for metric in report["quality"]["fields"].values())
    assert "synthetic private text" not in str(report)


def test_public_valid_evidence_is_scored(benchmark):
    report = target.run_benchmark(Path("synthetic"))
    assert report["gate"]["pass"] is True
    assert report["evidence"]["coverage"] == 1
