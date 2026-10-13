"""Yearless deadlines ("до 16.10") as colleagues write them in letters.

13.10.2026: five real letters to project 17 had deadlines like "до 16.10";
tasks were created without a due date, so the AUTO pilot (which needs one)
skipped every letter.
"""
from datetime import date

import pytest

from app import document_extraction as extraction
from app.task_engine import extract_explicit_due_date, extract_task_candidates

TODAY = date(2026, 10, 13)


@pytest.mark.parametrize("sentence, expected", [
    ("Прошу до 16.10 связаться с эксплуатацией.", date(2026, 10, 16)),
    ("Нужно согласовать график монтажа до 20.10, иначе не успеем.", date(2026, 10, 20)),
    ("Подготовить ИД не позднее 05.11.", date(2026, 11, 5)),
    ("Прошу к 01.10 прислать акты.", date(2026, 10, 1)),          # 12 days overdue stays this year
    ("Прошу до 15.01 закрыть этап.", date(2027, 1, 15)),           # next occurrence
    ("Срок до 16.10.2026 включительно.", date(2026, 10, 16)),      # full dates unchanged
])
def test_yearless_deadline_is_its_next_occurrence(sentence, expected):
    assert extract_explicit_due_date(sentence, today=TODAY) == expected


@pytest.mark.parametrize("sentence", [
    "Проложить кабель до 2.5 м от щита.",          # measurement, one-digit "month"
    "Нагрузка до 10.50 кВт.",                       # decimal, impossible month
    "Согласно письму от 16.10 подрядчик приступил.",  # reference date, no deadline marker
    "Допуск до 31.02 невозможен.",                  # impossible date
])
def test_numbers_that_are_not_deadlines_are_ignored(sentence):
    assert extract_explicit_due_date(sentence, today=TODAY) is None


def test_regex_extraction_sets_due_date_from_a_letter():
    text = "Дмитрий, добрый день! Прошу до 17.10 подготовить обновлённую версию ПМИ."
    candidates = extract_task_candidates(text)
    assert candidates and candidates[0].due_date is not None
    assert (candidates[0].due_date.month, candidates[0].due_date.day) == (10, 17)


def test_llm_obligation_without_due_date_reads_it_from_the_verified_quote():
    text = "Прошу до 18.10 организовать устранение силами субподрядчика и прислать фотофиксацию."
    [item] = extraction._obligations_from_llm([{
        "title": "Организовать устранение дефектов",
        "evidence_quote": "Прошу до 18.10 организовать устранение силами субподрядчика",
        "confidence": "high",
    }], text)
    assert (item.due_date.month, item.due_date.day) == (10, 18)
    assert item.due_date_evidence_quote == "Прошу до 18.10 организовать устранение силами субподрядчика"


def test_llm_obligation_without_any_deadline_stays_without_due_date():
    text = "Прошу организовать устранение силами субподрядчика."
    [item] = extraction._obligations_from_llm([{
        "title": "Организовать устранение", "evidence_quote": "Прошу организовать устранение силами субподрядчика",
        "confidence": "high",
    }], text)
    assert item.due_date is None and item.due_date_evidence_quote is None
