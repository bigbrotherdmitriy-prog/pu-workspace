import pytest

from app.organizer_engine.naming import build_standard_name
from app.organizer_engine.classifier import classify
from app.organizer_engine.rule_precedence import (
    RULE_PRECEDENCE_VERSION, ClassificationSignal, evaluate_precedence,
)


def rule(keyword, folder):
    return {"pattern": {"filename_contains": keyword}, "action": {"folder": folder}}


FOLDERS = [
    "01_УПРАВЛЕНИЕ ПРОЕКТОМ", "02_ДОГОВОРЫ И ЮРИДИЧЕСКИЕ",
    "03_ФИНАНСЫ И СМЕТЫ", "04_ПРОЕКТИРОВАНИЕ", "05_ЗАКУПКИ И ПОСТАВКИ",
    "06_ПОДРЯДЧИКИ И КОНТРАГЕНТЫ", "07_ПЕРЕПИСКА И СОГЛАСОВАНИЯ",
]


@pytest.mark.parametrize("winning_index", range(6))
def test_exact_versioned_precedence_is_stable(winning_index):
    signals = [ClassificationSignal(FOLDERS[index + 3], 0.8, str(index), source)
               for index, source in enumerate(("manual", "metadata", "ai"))]
    kwargs = {
        "filename": "contract synthetic.pdf",
        "policy_rules": [rule("contract", FOLDERS[0])],
        "project_rules": [rule("contract", FOLDERS[1])],
        "organization_rules": [rule("contract", FOLDERS[2])],
        "manual": signals[0], "metadata": signals[1], "ai": signals[2],
    }
    ordered = ["policy_rules", "project_rules", "organization_rules", "manual", "metadata", "ai"]
    for key in ordered[:winning_index]:
        kwargs[key] = [] if key.endswith("rules") else None
    decision = evaluate_precedence(**kwargs)
    assert decision.version == RULE_PRECEDENCE_VERSION
    assert decision.source == ("organization" if ordered[winning_index] == "organization_rules"
                               else "project" if ordered[winning_index] == "project_rules"
                               else "policy" if ordered[winning_index] == "policy_rules"
                               else ordered[winning_index])
    assert decision.classification.folder == FOLDERS[winning_index]


def test_invalid_signal_is_ignored_and_low_confidence_ai_requires_review():
    bad = ClassificationSignal("not-a-folder", 1.0, "bad", "manual")
    ai = ClassificationSignal("03_ФИНАНСЫ И СМЕТЫ", 0.2, "uncertain", "ai")
    decision = evaluate_precedence(filename="x", manual=bad, ai=ai)
    assert decision.source == "ai" and decision.classification.is_ambiguous


def test_standard_name_is_idempotent_and_preserves_human_subject():
    first = build_standard_name("Договор № 17.PDF", "02_ДОГОВОРЫ И ЮРИДИЧЕСКИЕ", "Север")
    second = build_standard_name(first, "02_ДОГОВОРЫ И ЮРИДИЧЕСКИЕ", "Север")
    assert first == second
    assert "Договор № 17" in first and first.endswith(".pdf")


def test_production_classifier_uses_source_layer_not_database_order():
    rules = [
        {**rule("contract", FOLDERS[3]), "source": "manual"},
        {**rule("contract", FOLDERS[2]), "source": "organization"},
        {**rule("contract", FOLDERS[1]), "source": "project"},
        {**rule("contract", FOLDERS[0]), "source": "policy"},
    ]
    result = classify("contract synthetic.pdf", confirmed_rules=rules)
    assert result.folder == FOLDERS[0]
    assert "policy" in result.reasoning
