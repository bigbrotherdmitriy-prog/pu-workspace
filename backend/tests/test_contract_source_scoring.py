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


def test_exact_contract_filename_outranks_operational_forms_referencing_it():
    contract = SimpleNamespace(
        number="Б-УЗП130-02-2026",
        counterparty='ООО "Булат"',
        title="Модернизация системы бесперебойного электропитания Т3",
    )
    source = SimpleNamespace(name="Б-УЗП130-02-2026.pdf")
    operational_form = SimpleNamespace(name="ОС-15 №6 от 31.07.2026.pdf")
    repeated_context = (
        "ОС-15. Основание: договор Б-УЗП130-02-2026. ООО Булат. "
        "Модернизация системы бесперебойного электропитания Т3. "
        "Заказчик и подрядчик подтверждают стоимость выполненных работ."
    )

    source_score, source_reasons = _contract_document_score(contract, source, "")
    form_score, form_reasons = _contract_document_score(contract, operational_form, repeated_context)

    assert source_score == 100
    assert source_score > form_score
    assert "номер договора указан в имени файла" in source_reasons
    assert "имя указывает на связанный документ, а не договор" in form_reasons


def test_legal_structure_and_subject_meaning_strengthen_generic_scan():
    contract = SimpleNamespace(
        number="Б-УЗП130-02-2026",
        counterparty='ООО "Булат"',
        title="Модернизация системы бесперебойного электропитания Т3",
    )
    scan = SimpleNamespace(name="scan-2026-02-17.pdf")
    content = (
        "ООО Булат, именуемое Подрядчик, и Заказчик заключили настоящий договор. "
        "Предмет договора: модернизация системы бесперебойного электропитания Т3. "
        "Права и обязанности сторон. Цена договора. Срок действия. Реквизиты сторон."
    )

    score, reasons = _contract_document_score(contract, scan, content)

    assert score >= 60
    assert "структура текста соответствует договору" in reasons
    assert any(reason.startswith("предмет договора упоминается") for reason in reasons)
