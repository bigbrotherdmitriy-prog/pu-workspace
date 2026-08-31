from types import SimpleNamespace

from app.api.organizations_contracts import _contract_document_score


def test_contract_filename_outranks_permit_that_only_mentions_contract_in_text():
    contract = SimpleNamespace(
        number="ГК-08-19425",
        counterparty="ФКУ Налог-Сервис",
        title="Модернизация системы бесперебойного электропитания",
    )
    actual = SimpleNamespace(name="№ ГК-08-19425 от 29 января 2026.docx")
    permit = SimpleNamespace(name="от 04.02.2024 пропуск на вынос мусора.pdf")
    repeated_context = "Договор ГК-08-19425. ФКУ Налог-Сервис. Модернизация системы бесперебойного электропитания."

    actual_score, actual_reasons = _contract_document_score(contract, actual, "")
    permit_score, permit_reasons = _contract_document_score(contract, permit, repeated_context)

    assert actual_score >= 70
    assert actual_score > permit_score
    assert "номер договора указан в имени файла" in actual_reasons
    assert "имя указывает на связанный документ, а не договор" in permit_reasons
