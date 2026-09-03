"""Adaptive OCR policy tests with synthetic text and fake subprocesses only."""
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

from app.organizer_engine.content import _ocr_pdf_pages, _tesseract


PRIMARY_FRAGMENTED = (
    "Поставщик передает товар покупателю по договору. "
    "Поку обязан принять оплаченный ый товар лично или через у го пред теля. "
    "Передача товара производится после оплаты и проверки документов."
)
FALLBACK_CLEAN = (
    "Поставщик передает товар покупателю по договору. "
    "Покупатель обязан принять оплаченный товар лично или через представителя. "
    "Передача товара производится после оплаты и проверки документов."
)
COMMON_PATCHES = (
    patch("app.organizer_engine.content.OCR_PSM", 1),
    patch("app.organizer_engine.content.OCR_FALLBACK_PSM", 6),
    patch("app.organizer_engine.content.OCR_ADAPTIVE_FALLBACK", True),
    patch("app.organizer_engine.content.shutil.which", return_value="/fake/tesseract"),
)


def _psm_calls(run):
    return [item.args[0][item.args[0].index("--psm") + 1] for item in run.call_args_list]


def _numbered_primary() -> str:
    rows = " ".join(f"Строка {index}: {1000 + index}" for index in range(1, 11))
    return (
        "Счет 951 от 22.01.2026. Поку обязан принять оплаченный ый товар через у го пред теля. "
        "Сумма 147360 рублей. ИНН 7716888076. " + rows +
        " Передача производится после проверки договора, оплаты счета и документов."
    )


def _clean_numbered_fallback() -> str:
    return _numbered_primary().replace(
        "Поку обязан принять оплаченный ый товар через у го пред теля",
        "Покупатель обязан принять оплаченный товар через уполномоченного представителя",
    )


def test_psm1_intact_does_not_start_psm6():
    intact = FALLBACK_CLEAN + " Документ составлен без разрывов и содержит достаточно связного текста."
    with COMMON_PATCHES[0], COMMON_PATCHES[1], COMMON_PATCHES[2], COMMON_PATCHES[3], patch(
        "app.organizer_engine.content.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=intact),
    ) as run:
        assert _tesseract(Path("synthetic.jpg"), 30) == intact
        assert _psm_calls(run) == ["1"]


def test_psm6_is_accepted_when_it_eliminates_breaks_without_losing_evidence():
    with COMMON_PATCHES[0], COMMON_PATCHES[1], COMMON_PATCHES[2], COMMON_PATCHES[3], patch(
        "app.organizer_engine.content.subprocess.run",
        side_effect=[SimpleNamespace(returncode=0, stdout=PRIMARY_FRAGMENTED), SimpleNamespace(returncode=0, stdout=FALLBACK_CLEAN)],
    ) as run:
        assert _tesseract(Path("synthetic.jpg"), 30) == FALLBACK_CLEAN
        assert _psm_calls(run) == ["1", "6"]


@pytest.mark.parametrize(
    "old,new",
    [("147360", "сумма-не-распознана"), ("22.01.2026", "дата-не-распознана"), ("7716888076", "инн-не-распознан")],
    ids=["amount", "date", "inn"],
)
def test_psm6_losing_amount_date_or_inn_is_rejected(old, new):
    primary = _numbered_primary()
    fallback = _clean_numbered_fallback().replace(old, new)
    with COMMON_PATCHES[0], COMMON_PATCHES[1], COMMON_PATCHES[2], COMMON_PATCHES[3], patch(
        "app.organizer_engine.content.subprocess.run",
        side_effect=[SimpleNamespace(returncode=0, stdout=primary), SimpleNamespace(returncode=0, stdout=fallback)],
    ):
        assert _tesseract(Path("synthetic-invoice.jpg"), 30) == primary


def test_psm6_reordering_table_numbers_is_rejected():
    primary = _numbered_primary()
    fallback = _clean_numbered_fallback().replace(
        "Строка 4: 1004 Строка 5: 1005",
        "Строка 4: 1005 Строка 5: 1004",
    )
    with COMMON_PATCHES[0], COMMON_PATCHES[1], COMMON_PATCHES[2], COMMON_PATCHES[3], patch(
        "app.organizer_engine.content.subprocess.run",
        side_effect=[SimpleNamespace(returncode=0, stdout=primary), SimpleNamespace(returncode=0, stdout=fallback)],
    ):
        assert _tesseract(Path("synthetic-table.jpg"), 30) == primary


@pytest.mark.parametrize("still_damaged", [
    FALLBACK_CLEAN.replace("Покупатель обязан", "Покупатель обязан ый"),
    FALLBACK_CLEAN.replace("оплаченный товар", "оплаченный тов�р"),
], ids=["remaining-fragment", "replacement-character"])
def test_both_results_damaged_keeps_primary(still_damaged):
    with COMMON_PATCHES[0], COMMON_PATCHES[1], COMMON_PATCHES[2], COMMON_PATCHES[3], patch(
        "app.organizer_engine.content.subprocess.run",
        side_effect=[SimpleNamespace(returncode=0, stdout=PRIMARY_FRAGMENTED), SimpleNamespace(returncode=0, stdout=still_damaged)],
    ):
        assert _tesseract(Path("synthetic.jpg"), 30) == PRIMARY_FRAGMENTED


def test_fallback_timeout_keeps_primary():
    with COMMON_PATCHES[0], COMMON_PATCHES[1], COMMON_PATCHES[2], COMMON_PATCHES[3], patch(
        "app.organizer_engine.content.subprocess.run",
        side_effect=[SimpleNamespace(returncode=0, stdout=PRIMARY_FRAGMENTED), subprocess.TimeoutExpired("tesseract", 5)],
    ):
        assert _tesseract(Path("synthetic.jpg"), 30) == PRIMARY_FRAGMENTED


def test_setting_disables_fallback_even_for_fragmented_primary():
    with COMMON_PATCHES[0], COMMON_PATCHES[1], patch(
        "app.organizer_engine.content.OCR_ADAPTIVE_FALLBACK", False
    ), COMMON_PATCHES[3], patch(
        "app.organizer_engine.content.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=PRIMARY_FRAGMENTED),
    ) as run:
        assert _tesseract(Path("synthetic.jpg"), 30) == PRIMARY_FRAGMENTED
        assert _psm_calls(run) == ["1"]


def test_pdf_fallback_is_enabled_for_at_most_first_two_eligible_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.setattr("app.organizer_engine.content.OCR_FALLBACK_MAX_PAGES", 2)
    monkeypatch.setattr("app.organizer_engine.content.OCR_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr("app.organizer_engine.content.shutil.which", lambda name: f"/fake/{name}")
    seen: list[tuple[int, bool]] = []

    def render(command, **_kwargs):
        Path(command[-1]).with_suffix(".jpg").write_bytes(b"synthetic")
        return SimpleNamespace(returncode=0)

    def fake_tesseract(path, _timeout, *, allow_fallback=True):
        seen.append((int(path.stem.rsplit("-", 1)[-1]), allow_fallback))
        return f"page {path.stem}"

    monkeypatch.setattr("app.organizer_engine.content.subprocess.run", render)
    monkeypatch.setattr("app.organizer_engine.content._tesseract", fake_tesseract)
    monkeypatch.setattr("app.organizer_engine.content.tempfile.TemporaryDirectory", lambda **_kwargs: _Directory(tmp_path))

    assert set(_ocr_pdf_pages(b"synthetic pdf bytes", {1, 2, 3, 4})) == {1, 2, 3, 4}
    assert seen == [(1, True), (2, True), (3, False), (4, False)]


class _Directory:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return str(self.path)

    def __exit__(self, *_args):
        return False


def test_primary_and_fallback_share_one_deadline():
    time_values = iter([100.0, 106.25])
    with COMMON_PATCHES[0], COMMON_PATCHES[1], COMMON_PATCHES[2], COMMON_PATCHES[3], patch(
        "app.organizer_engine.content.time.monotonic", side_effect=lambda: next(time_values)
    ), patch(
        "app.organizer_engine.content._run_tesseract",
        side_effect=[PRIMARY_FRAGMENTED, FALLBACK_CLEAN],
    ) as run:
        assert _tesseract(Path("synthetic.jpg"), 10) == FALLBACK_CLEAN
        assert run.call_args_list == [
            call(Path("synthetic.jpg"), 1, 10),
            call(Path("synthetic.jpg"), 6, pytest.approx(3.75)),
        ]


def test_fallback_is_not_started_when_primary_exhausts_shared_deadline():
    time_values = iter([100.0, 109.25])
    with COMMON_PATCHES[0], COMMON_PATCHES[1], COMMON_PATCHES[2], COMMON_PATCHES[3], patch(
        "app.organizer_engine.content.time.monotonic", side_effect=lambda: next(time_values)
    ), patch(
        "app.organizer_engine.content._run_tesseract", return_value=PRIMARY_FRAGMENTED
    ) as run:
        assert _tesseract(Path("synthetic.jpg"), 10) == PRIMARY_FRAGMENTED
        run.assert_called_once_with(Path("synthetic.jpg"), 1, 10)
