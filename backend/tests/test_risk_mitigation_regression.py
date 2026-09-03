import pytest

from app.governance_engine import extract_governance_candidates


@pytest.mark.parametrize("sentence", [
    "Мера снижения риска — подтвердить наличие учебного аккумулятора до 10.09.2026.",
    "Меры по снижению рисков: проверить наличие запасных комплектующих.",
    "Для минимизации риска необходимо проверить наличие аккумулятора.",
    "1. Мера снижения риска — подтвердить комплектность поставки.",
])
def test_mitigation_action_is_not_a_new_risk(sentence):
    risks, _ = extract_governance_candidates(sentence)
    assert risks == []


@pytest.mark.parametrize("sentence", [
    "Риск № 1: задержка поставки учебного аккумулятора может перенести проверку стенда.",
    "Мера снижения риска не устраняет угрозу аварии на стенде.",
    "Для снижения риска предусмотрен резерв, однако сохраняется риск срыва поставки.",
    "Мера снижения риска может привести к перегрузке оборудования.",
    "Заказчик просрочил согласование рабочей документации.",
    "Снижение риска не достигнуто после проверки резервного оборудования.",
    "Меры снижения риска оказались неэффективными при испытании стенда.",
])
def test_actual_risk_signal_is_preserved(sentence):
    risks, _ = extract_governance_candidates(sentence)
    assert len(risks) == 1
    assert risks[0]["text"] == sentence


def test_synthetic_protocol_has_one_risk_and_one_decision():
    text = (
        "Риск № 1: задержка поставки учебного аккумулятора может перенести проверку стенда.\n"
        "Мера снижения риска — подтвердить наличие учебного аккумулятора до 10.09.2026.\n"
        "Решение на согласование: утвердить дату учебной проверки 12.09.2026."
    )
    risks, decisions = extract_governance_candidates(text)
    assert len(risks) == 1
    assert len(decisions) == 1
    assert risks[0]["text"].startswith("Риск № 1:")


def test_mitigation_can_still_contain_a_decision():
    risks, decisions = extract_governance_candidates(
        "Мера снижения риска: утвердить резервную дату поставки оборудования."
    )
    assert risks == []
    assert len(decisions) == 1
