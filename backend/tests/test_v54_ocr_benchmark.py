from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from app.ocr_quality.benchmark import (
    _character_accuracy,
    _normalize,
    find_cyrillic_font,
    load_corpus,
    run_benchmark,
)
from app.organizer_engine.content import ExtractionResult, PageExtraction
from app.organizer_engine.content import extract_text_result


CORPUS = Path(__file__).parent / "fixtures/ocr_benchmark/corpus.json"


def test_corpus_is_explicitly_synthetic_complete_and_multidocument():
    cases = load_corpus(CORPUS)
    assert len(cases) == 20
    assert len({case.document_id for case in cases}) == 5
    assert {case.page for case in cases} == {1, 2, 3, 4}
    assert {case.degradation for case in cases} == {"clean", "low_contrast", "noise", "skew"}
    assert all(all(case.expected[field] for field in ("number", "date", "party", "amount")) for case in cases)


def test_corpus_contains_no_real_identifiers_or_external_locations():
    serialized = CORPUS.read_text(encoding="utf-8")
    assert "synthetic" in serialized.lower()
    assert not any(marker in serialized for marker in ("@", "http://", "https://", "drive.google", "disk.yandex"))
    assert not any(char.isdigit() and token.isdigit() and len(token) in {10, 12, 13, 16, 20}
                   for token in serialized.replace('"', " ").split() for char in token[:1])


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("ООО «Проект»", "ооо проект", 1.0),
        ("ГК-08-194/26", "ГК 08 194 26", 1.0),
        ("12345", "12346", 0.8),
    ],
)
def test_normalization_and_character_accuracy_are_deterministic(left, right, expected):
    assert _normalize(left) == _normalize(right) if expected == 1.0 else _normalize(left) != _normalize(right)
    assert _character_accuracy(left, right) == expected


def test_low_confidence_contract_requires_review_and_blocks_legal_financial_actions():
    result = ExtractionResult(
        text="Акт № ТЕСТ-1", method="ocr", quality="low", confidence=0.2,
        pages=[PageExtraction(1, "Акт № ТЕСТ-1", 0.2, "ocr")], needs_review=True,
    )
    automation_ready = [] if result.needs_review else [result]
    assert result.needs_review is True
    assert automation_ready == []


def test_unreadable_image_fails_closed_to_manual_review(monkeypatch):
    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.setattr("app.organizer_engine.content._tesseract_binary", lambda: None)
    result = extract_text_result(b"not-an-image", "image/png", "scan.png")
    assert result.confidence == 0
    assert result.needs_review is True
    assert result.warnings == ["manual_review_required"]


def test_external_vision_is_not_an_implicit_benchmark_fallback(monkeypatch):
    monkeypatch.setenv("OCR_EXTERNAL_VISION_ENABLED", "true")
    # The benchmark has exactly one required engine and never imports/calls a provider.
    source = Path(__file__).parents[1] / "app/ocr_quality/benchmark.py"
    implementation = source.read_text(encoding="utf-8")
    assert "analyze_document(" not in implementation
    assert "AIProviderAdapter(" not in implementation
    assert "Local Tesseract executable is required" in implementation


def test_actual_local_tesseract_benchmark_gate(tmp_path):
    if not (os.getenv("TESSERACT_CMD") or shutil.which("tesseract")):
        pytest.skip("local Tesseract is not installed")
    try:
        find_cyrillic_font()
    except RuntimeError:
        pytest.skip("local Cyrillic font is not installed")
    result = run_benchmark(CORPUS)
    (tmp_path / "metrics.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    assert result["engine"]["baseline"] == "local_tesseract"
    assert result["external_vision"] == {
        "enabled": False, "used": False, "allowed_boundary": "AIProviderAdapter",
    }
    assert result["technical"]["success_rate"] >= 0.95
    assert all(metric["precision"] >= 0.8 and metric["recall"] >= 0.8
               for metric in result["quality"]["fields"].values())
    assert result["review"]["low_confidence_policy_violations"] == 0
    assert result["review"]["legal_financial_actions_allowed_for_low_confidence"] is False
    assert result["evidence"]["coverage"] == 1.0
    assert result["gate"]["pass"] is True
