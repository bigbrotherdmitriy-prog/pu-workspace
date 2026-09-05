from __future__ import annotations

import argparse
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from app.organizer_engine.content import (
    OCR_REVIEW_CONFIDENCE,
    PageExtraction,
    _page_from_tokens,
    _parse_tsv,
    _preprocess_image,
    extract_text_result,
)


FIELD_NAMES = ("number", "date", "party", "amount")
ALLOWED_DEGRADATIONS = {"clean", "low_contrast", "noise", "skew"}


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    document_id: str
    page: int
    lines: tuple[str, ...]
    expected: dict[str, tuple[str, ...]]
    degradation: str


def load_corpus(path: Path) -> list[BenchmarkCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("synthetic") is not True or raw.get("schema_version") != 1:
        raise ValueError("OCR benchmark corpus must be explicitly synthetic and schema version 1")
    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for item in raw.get("cases", []):
        case_id = str(item["id"])
        if case_id in seen:
            raise ValueError("Duplicate OCR benchmark case id")
        seen.add(case_id)
        degradation = str(item["degradation"])
        if degradation not in ALLOWED_DEGRADATIONS:
            raise ValueError("Unsupported OCR benchmark degradation")
        expected = {
            field: tuple(str(value) for value in item["expected"].get(field, []))
            for field in FIELD_NAMES
        }
        cases.append(BenchmarkCase(
            case_id=case_id,
            document_id=str(item["document_id"]),
            page=int(item["page"]),
            lines=tuple(str(line) for line in item["lines"]),
            expected=expected,
            degradation=degradation,
        ))
    if len(cases) < 20:
        raise ValueError("OCR benchmark requires at least 20 synthetic pages")
    return cases


def find_cyrillic_font(explicit: str | None = None) -> Path:
    candidates = [
        explicit,
        os.getenv("OCR_BENCHMARK_FONT"),
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("A local Cyrillic TrueType font is required for the synthetic OCR benchmark")


def _render_case(case: BenchmarkCase, font_path: Path) -> Image.Image:
    image = Image.new("L", (1240, 1754), 255)
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(str(font_path), 47)
    body_font = ImageFont.truetype(str(font_path), 39)
    draw.text((90, 80), "СИНТЕТИЧЕСКИЙ ДОКУМЕНТ", font=title_font, fill=15)
    y = 190
    for line in case.lines:
        draw.text((90, y), line, font=body_font, fill=20)
        y += 72
    draw.line((90, y + 20, 1150, y + 20), fill=45, width=2)
    draw.text((90, y + 55), "Тестовые данные. Не является реальным документом.", font=body_font, fill=40)

    if case.degradation == "low_contrast":
        image = ImageEnhance.Contrast(image).enhance(0.34)
    elif case.degradation == "noise":
        rng = random.Random(case.case_id)
        pixels = image.load()
        for _ in range(18_000):
            x, y = rng.randrange(image.width), rng.randrange(image.height)
            pixels[x, y] = rng.randrange(125, 245)
    elif case.degradation == "skew":
        angle = -2.0 if case.page % 2 else 2.0
        image = image.rotate(angle, expand=False, fillcolor=255)
    return image


def _tesseract_command() -> str:
    explicit = os.getenv("TESSERACT_CMD")
    command = explicit if explicit and Path(explicit).is_file() else shutil.which("tesseract")
    if not command:
        raise RuntimeError("Local Tesseract executable is required")
    return command


def _run_tsv(command: str, image_path: Path, page: int) -> PageExtraction:
    started = time.perf_counter()
    result = subprocess.run(
        [command, str(image_path), "stdout", "-l", "rus+eng", "--psm", "1",
         "-c", "preserve_interword_spaces=1", "tsv"],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Local Tesseract returned a non-zero status")
    tsv = result.stdout.decode("utf-8", errors="replace")
    with Image.open(image_path) as image:
        width, height = image.size
    extraction = _page_from_tokens(page, _parse_tsv(tsv), width, height, [])
    extraction.preprocessing.append(f"elapsed_ms:{(time.perf_counter() - started) * 1000:.3f}")
    return extraction


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", value.casefold())


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def _character_accuracy(expected: str, actual: str) -> float:
    normalized_expected = _normalize(expected)
    normalized_actual = _normalize(actual)
    if not normalized_expected:
        return 1.0 if not normalized_actual else 0.0
    return max(0.0, 1.0 - _edit_distance(normalized_expected, normalized_actual) / len(normalized_expected))


def _field_sets(fields: dict[str, list[Any]]) -> dict[str, set[str]]:
    return {
        name: {_normalize(evidence.value) for evidence in fields.get(name, [])}
        for name in FIELD_NAMES
    }


def _public_evidence_is_located(evidence: Any, pages: list[PageExtraction]) -> bool:
    # Each corpus page is submitted as a separate image: its public page is 1,
    # not the page ordinal of the synthetic multi-page corpus document.
    if type(evidence.page) is not int or evidence.page != 1:
        return False
    matching = [page for page in pages if page.page == evidence.page]
    bbox = evidence.bbox
    if len(matching) != 1 or not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
        return False
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in bbox):
        return False
    x, y, width, height = bbox
    page = matching[0]
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
           for value in (page.width, page.height)):
        return False
    return (x >= 0 and y >= 0 and width > 0 and height > 0
            and x + width <= page.width and y + height <= page.height)


def _metric(counter: dict[str, int]) -> dict[str, float | int]:
    tp, fp, fn = counter["tp"], counter["fp"], counter["fn"]
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        **counter,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
    }


def run_benchmark(corpus_path: Path, *, font_path: str | None = None) -> dict[str, Any]:
    cases = load_corpus(corpus_path)
    command = _tesseract_command()
    font = find_cyrillic_font(font_path)
    version = subprocess.run([command, "--version"], capture_output=True, check=False).stdout
    version_line = version.decode("utf-8", errors="replace").splitlines()[0]
    counters = {name: {"tp": 0, "fp": 0, "fn": 0} for name in FIELD_NAMES}
    technical_successes = 0
    recognized_pages = 0
    review_pages = 0
    low_confidence_policy_violations = 0
    evidence_total = 0
    evidence_with_coordinates = 0
    before_accuracy: list[float] = []
    after_accuracy: list[float] = []
    before_confidence: list[float] = []
    after_confidence: list[float] = []
    elapsed_ms: list[float] = []
    benchmark_elapsed_ms: list[float] = []
    failures: list[dict[str, str]] = []

    with tempfile.TemporaryDirectory(prefix="puw-ocr-benchmark-") as temp:
        temp_dir = Path(temp)
        for case in cases:
            expected_fields = {
                name: {_normalize(value) for value in case.expected[name]}
                for name in FIELD_NAMES
            }
            # Failed pages must contribute false negatives, not disappear from recall.
            for name, wanted in expected_fields.items():
                counters[name]["fn"] += len(wanted)
            started = time.perf_counter()
            source = temp_dir / f"{case.case_id}.png"
            processed = temp_dir / f"{case.case_id}-processed.png"
            try:
                image = _render_case(case, font)
                image.save(source, format="PNG")
                raw_page = _run_tsv(command, source, case.page)
                _, _, actions = _preprocess_image(source, processed, 120)
                processed_page = _run_tsv(command, processed, case.page)
                processed_page.preprocessing = actions
                expected_text = " ".join((
                    "СИНТЕТИЧЕСКИЙ ДОКУМЕНТ",
                    *case.lines,
                    "Тестовые данные. Не является реальным документом.",
                ))
                before_accuracy.append(_character_accuracy(expected_text, raw_page.text))
                after_accuracy.append(_character_accuracy(expected_text, processed_page.text))
                before_confidence.append(raw_page.confidence)
                after_confidence.append(processed_page.confidence)

                # Exercise the public local-only extraction facade as the acceptance result.
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                extraction_started = time.perf_counter()
                result = extract_text_result(buffer.getvalue(), "image/png", f"{case.case_id}.png")
                elapsed_ms.append((time.perf_counter() - extraction_started) * 1000)
                fields = result.fields
                actual = _field_sets(fields)
                locations = [_public_evidence_is_located(evidence, result.pages)
                             for values in fields.values() for evidence in values]
                # Validate the whole public result before crediting a successful page.
                succeeded = int(result.method == "ocr" and result.ocr_pages == 1)
                recognized = int(bool(result.text.strip()))
                reviewed = int(result.needs_review)
                violation = int(result.confidence < OCR_REVIEW_CONFIDENCE and not result.needs_review)
                for name in FIELD_NAMES:
                    wanted = expected_fields[name]
                    counters[name]["tp"] += len(actual[name] & wanted)
                    counters[name]["fp"] += len(actual[name] - wanted)
                    counters[name]["fn"] -= len(actual[name] & wanted)
                evidence_total += len(locations)
                evidence_with_coordinates += sum(locations)
                technical_successes += succeeded
                recognized_pages += recognized
                review_pages += reviewed
                low_confidence_policy_violations += violation
            except Exception as exc:
                failures.append({"case_id": case.case_id, "reason": exc.__class__.__name__})
            benchmark_elapsed_ms.append((time.perf_counter() - started) * 1000)

    pages = len(cases)
    field_metrics = {name: _metric(counter) for name, counter in counters.items()}
    return {
        "schema_version": 1,
        "corpus": {"synthetic": True, "pages": pages, "documents": len({c.document_id for c in cases})},
        "engine": {"baseline": "local_tesseract", "version": version_line, "languages": ["rus", "eng"]},
        "external_vision": {
            "enabled": False,
            "used": False,
            "allowed_boundary": "AIProviderAdapter",
        },
        "technical": {
            "success_pages": technical_successes,
            "success_rate": round(technical_successes / pages, 4),
            "recognized_pages": recognized_pages,
            "failures": failures,
            "mean_ms_per_page": round(mean(elapsed_ms), 3) if elapsed_ms else 0.0,
            "p95_ms_per_page": round(sorted(elapsed_ms)[max(0, int(len(elapsed_ms) * .95) - 1)], 3)
            if elapsed_ms else 0.0,
            "mean_benchmark_ms_per_page": round(mean(benchmark_elapsed_ms), 3),
        },
        "quality": {
            "before_preprocessing": {
                "mean_character_accuracy": round(mean(before_accuracy), 4) if before_accuracy else 0.0,
                "mean_confidence": round(mean(before_confidence), 4) if before_confidence else 0.0,
            },
            "after_preprocessing": {
                "mean_character_accuracy": round(mean(after_accuracy), 4) if after_accuracy else 0.0,
                "mean_confidence": round(mean(after_confidence), 4) if after_confidence else 0.0,
            },
            "fields": field_metrics,
        },
        "review": {
            "pages": review_pages,
            "rate": round(review_pages / pages, 4),
            "threshold": OCR_REVIEW_CONFIDENCE,
            "low_confidence_policy_violations": low_confidence_policy_violations,
            "legal_financial_actions_allowed_for_low_confidence": False,
        },
        "evidence": {
            "extracted_fields": evidence_total,
            "with_page_and_coordinates": evidence_with_coordinates,
            "coverage": round(evidence_with_coordinates / evidence_total, 4) if evidence_total else 0.0,
        },
        "gate": {
            "technical_success_target": 0.95,
            "field_precision_recall_target": 0.8,
            "pass": technical_successes / pages >= .95
                    and recognized_pages / pages >= .95
                    and low_confidence_policy_violations == 0
                    and evidence_total > 0
                    and evidence_with_coordinates == evidence_total
                    and all(metric["precision"] >= .8 and metric["recall"] >= .8
                            for metric in field_metrics.values()),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local synthetic PU Workspace OCR benchmark")
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--font")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_benchmark(args.corpus, font_path=args.font)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if result["gate"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
