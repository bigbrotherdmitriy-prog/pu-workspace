from app.organization_requisites import extract_organization_requisites


def test_extracts_company_and_bank_requisites_from_card():
    profiles = extract_organization_requisites(
        'ООО "ДИСИАЙ СОЛЮШНС"\nИНН 7716888076 КПП 771501001 ОГРН 1187746032572 '
        'р/с 40702810301100013895 к/с 30101810200000000593 БИК 044525593 '
        'Электронная почта office@example.ru'
    )
    assert profiles == [{
        "inn": "7716888076", "legal_name": 'ООО "ДИСИАЙ СОЛЮШНС"',
        "name": 'ООО "ДИСИАЙ СОЛЮШНС"', "kpp": "771501001", "ogrn": "1187746032572",
        "settlement_account": "40702810301100013895",
        "correspondent_account": "30101810200000000593", "bik": "044525593",
        "email": "office@example.ru",
    }]


def test_extracts_every_unique_inn_from_contract():
    profiles = extract_organization_requisites(
        'Заказчик ООО "Альфа", ИНН 7712345678 КПП 771201001. '
        'Подрядчик ООО "Бета", ИНН 7812345678 КПП 781201001.'
    )
    assert {item["inn"] for item in profiles} == {"7712345678", "7812345678"}
