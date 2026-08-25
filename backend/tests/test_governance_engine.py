from app.governance_engine import extract_governance_candidates


def test_extracts_risk_deviation_and_decision_from_content():
    text = (
        "Существует существенный риск срыва поставки оборудования до 30 августа. "
        "Заказчик просрочил согласование рабочей документации. "
        "Необходимо решить, какой вариант графика следует утвердить."
    )
    risks, decisions = extract_governance_candidates(text)
    assert len(risks) == 2
    assert risks[0]["criticality"] == "high"
    assert risks[1]["kind"] == "deviation"
    assert len(decisions) == 1
    assert "утвердить" in decisions[0]["text"]


def test_ignores_unrelated_content():
    risks, decisions = extract_governance_candidates("Обычное информационное сообщение без поручений.")
    assert risks == []
    assert decisions == []
