from app.api.history import compare_version_content


def test_compare_version_content_reports_added_removed_and_changed_lines():
    result = compare_version_content(
        "Раздел 1\nСрок: 10 дней\nУдалить строку",
        "Раздел 1\nСрок: 15 дней\nДобавить строку\nНовый пункт",
    )
    assert result["changed_lines"] == 2
    assert result["added_lines"] == 1
    assert result["removed_lines"] == 0
    assert result["unchanged"] is False
    assert any("Срок: 15 дней" in line for line in result["preview"])


def test_compare_version_content_detects_identical_text():
    result = compare_version_content("Без изменений", "Без изменений")
    assert result["unchanged"] is True
    assert result["added_lines"] == 0
    assert result["removed_lines"] == 0
    assert result["changed_lines"] == 0
