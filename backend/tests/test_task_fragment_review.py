"""Synthetic Russian fragments: review signals, not a benchmark of PDF OCR."""
from datetime import date

import pytest

from app.organizer_engine.content import extract_text_result
from app.task_engine import extract_task_candidates


@pytest.mark.parametrize("source", [
    "Поку обязан при ий товар лично или через у го пред: теля.",
    "Исполнитель обязан передать подписан ый акт и готов ые документы заказчику",
    "Подрядчик должен предоставить рабоч ую схему и согласован ый план поставки",
    "Поставщик обязан передать готов ые изделия и подписан ую накладную заказчику",
])
@pytest.mark.parametrize("deadline", ["", " до 15.09.2026"])
def test_distinct_orphan_endings_lower_review_score_without_rewriting(source, deadline):
    source = source.rstrip(".") + deadline
    candidate, = extract_task_candidates(source)
    assert candidate.confidence == 0.45
    assert candidate.excerpt == source
    assert candidate.title == source[:240]
    assert any("отдельно стоящие окончания" in reason for reason in candidate.review_reasons)
    assert candidate.due_date == (date(2026, 9, 15) if deadline else None)


@pytest.mark.parametrize("source", [
    "Покупатель обязан принять оплаченный товар лично или через уполномоченного представителя.",
    "Исполнитель обязан при приёмке передать их ей и отнести её копию в архив",
    "Поставщик должен поставить болт М8х20, лист 12Х18Н10Т и кабель ВВГнг-LS 3х2,5",
    "Заказчик обязан проверить ГО и ИЙ по данным спецификации оборудования",
    "Поставщик обязан передать изделие AB-ый и модуль CD/ые согласно спецификации",
    "Поставщик обязан передать изделие xый20 и модуль 3ые согласно спецификации",
    "Исполнитель должен описать правила игры го для новой учебной программы",
    "Исполнитель должен описать игру го и повторить правила игры го для участников",
    "Исполнитель должен проверить окончания «ый» и «ые» в учебном тексте",
    'Исполнитель должен проверить окончания "ую" и "ий" в учебном тексте',
    "Исполнитель должен проверить окончания -ый и -ую в учебном тексте",
    "Исполнитель должен проверить коды ый: один и ую: два в учебной таблице",
    "Исполнитель должен подготовить план поставок за 2 ч и по 5 шт на объект",
    "Подрядчик обязан подготовить акт № 2 по СП 70.13330.2012 и ГОСТ 123-45",
])
def test_clean_prose_codes_abbreviations_and_single_ambiguous_fragments_are_not_penalized(source):
    candidate, = extract_task_candidates(source)
    assert candidate.review_reasons == ()
    assert candidate.confidence == 0.82
    assert candidate.excerpt == source


def test_fragment_review_is_local_to_candidate_not_the_entire_document():
    damaged = "Исполнитель обязан передать подписан ый акт и готов ые документы заказчику"
    clean = "Покупатель обязан принять оплаченный товар лично или через представителя"
    candidates = extract_task_candidates(damaged + ". " + clean)
    assert [candidate.confidence for candidate in candidates] == [0.45, 0.82]
    assert candidates[1].review_reasons == ()


def test_synthetic_ocr_output_reaches_fragment_review_without_text_repair(monkeypatch):
    source = "Подрядчик должен предоставить рабоч ую схему и согласован ый план поставки"
    monkeypatch.setattr("app.organizer_engine.content._ocr_text", lambda *args: source)
    extracted = extract_text_result(b"synthetic image placeholder", "image/png", "synthetic.png")
    candidate, = extract_task_candidates(extracted.text)
    assert extracted.method == "ocr"
    assert candidate.confidence == 0.45
    assert candidate.excerpt == source


def test_no_signal_is_not_a_claim_that_ocr_is_correct():
    # All pieces can look like valid words even though the sentence is nonsense.
    source = "Покупатель обязан лично через товар представитель принять оплаченный"
    candidate, = extract_task_candidates(source)
    assert candidate.excerpt == source
    assert candidate.review_reasons == ()
    assert candidate.confidence == 0.82
