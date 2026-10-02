from decimal import Decimal, ROUND_UP, localcontext

import pytest

from app.structured_import import parse_structured_rows


MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def matrix(values, *, annual="0", title="ФОТ", months=MONTHS, row=12, limit=500):
    content = (
        "\t".join(("Статья", "Годовой итог", *months, "__PU_SOURCE_COORD__:ДДС:1"))
        + "\n"
        + "\t".join((title, annual, *map(str, values), f"__PU_SOURCE_COORD__:ДДС:{row}"))
        + "\n"
    )
    return parse_structured_rows(content, "cash-flow", plan_year=2026, limit=limit)


def allocate(values):
    from app.structured_import import allocate_monthly_amounts

    return allocate_monthly_amounts(values)


def test_fot_largest_remainder_preserves_total_and_shows_every_adjustment():
    result = matrix(["1594090.8333333333"] * 12, annual="19129090")
    assert [row["amount"] for row in result["rows"]] == ["1594090.84"] * 4 + ["1594090.83"] * 8
    assert sum(Decimal(row["amount"]) for row in result["rows"]) == Decimal("19129090.00")
    article = result["articles"][0]
    assert article["monthly_total"] == "19129090.00"
    assert article["annual_difference"] == "0.00"
    assert article["rounding_algorithm"] == "largest_remainder_half_even_v1"
    assert [cell["adjustment"] for cell in article["months"]] == ["0.01"] * 4 + ["0.00"] * 8
    assert all(cell["raw_amount"] == "1594090.8333333333" for cell in article["months"])


def test_ties_use_month_then_source_column_not_input_traversal():
    cells = [(3, 4, Decimal("0.005")), (1, 2, Decimal("0.005")), (2, 3, Decimal("0.005"))]
    assert allocate(cells) == {2: Decimal("0.01"), 3: Decimal("0.01"), 4: Decimal("0.00")}
    assert allocate(list(reversed(cells))) == allocate(cells)


@pytest.mark.parametrize(
    "values, expected",
    [
        (["0.004"] * 3, ["0.01", "0.00", "0.00"]),
        (["0.006"] * 3, ["0.01", "0.01", "0.00"]),
        (["0.005", "0", "0"], ["0.00", "0.00", "0.00"]),
        (["0.015", "0", "0"], ["0.02", "0.00", "0.00"]),
        (["1.01", "0", "2.02"], ["1.01", "0.00", "2.02"]),
        (["0", "0", "0"], ["0.00", "0.00", "0.00"]),
    ],
)
def test_rounding_boundaries_zero_and_both_directions_of_residual(values, expected):
    cells = [(month, month + 1, Decimal(value)) for month, value in enumerate(values, start=1)]
    amounts = allocate(cells)
    assert [str(amounts[column]) for _, column, _ in cells] == expected


def test_rounding_does_not_depend_on_global_decimal_precision_or_rounding():
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_UP
        result = matrix(["1594090.8333333333"] * 12, annual="19129090")
    assert [row["amount"] for row in result["rows"]] == ["1594090.84"] * 4 + ["1594090.83"] * 8


def test_yearly_control_does_not_replace_months_or_spread_14000_difference():
    values = ["0"] * 12
    values[0], values[6] = "2378718", "14000"
    result = matrix(values, annual="2378718", title="Материалы для ЭМ и АСДУ (Городец)")
    assert result["issues"] == ["ДДС!12: годовой итог 2378718.00 не равен сумме месяцев 2392718.00"]
    assert [row["amount"] for row in result["rows"]] == ["2378718.00", "14000.00"]
    assert all(row["importable"] for row in result["rows"])
    article = result["articles"][0]
    assert article["monthly_total"] == "2392718.00"
    assert article["annual_total"] == "2378718.00"
    assert article["annual_difference"] == "-14000.00"
    assert article["annual_coordinate"] == "ДДС!B12"
    assert article["months"][6]["source_coordinate"] == "ДДС!I12"
    assert all(cell["adjustment"] == "0.00" for cell in article["months"])
    assert article["importable"] is True


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "99999999999999999", "1,234.56", "USD 12", "1 23", "1e-999999"])
def test_invalid_month_is_blocking_and_not_silently_dropped(value):
    result = matrix([value, "1", "1"], months=MONTHS[:3])
    assert result["blocking_issues"]
    assert "ДДС!C12" in result["blocking_issues"][0]
    assert result["articles"][0]["importable"] is False
    assert result["rows"] == []


def test_numeric_limit_applies_to_sum_not_just_individual_months():
    result = matrix(["9999999999999999.99", "0.01", "0"], months=MONTHS[:3])
    assert result["blocking_issues"]
    assert result["rows"] == []


def test_exact_numeric_limit_is_preserved():
    result = matrix(["9999999999999999.99", "0", "0"], annual="9999999999999999.99", months=MONTHS[:3])
    assert result["articles"][0]["monthly_total"] == "9999999999999999.99"
    assert result["blocking_issues"] == []


def test_zero_months_do_not_receive_artificial_payments():
    result = matrix(["0", "0.006", "0"], months=MONTHS[:3])
    assert len(result["rows"]) == 1
    assert result["rows"][0]["planned_date"] == "2026-02-28"
    assert result["articles"][0]["months"][0]["amount"] == "0.00"


def test_duplicate_month_header_is_blocking():
    result = matrix(["1", "2", "3", "4"], months=("январь", "февраль", "март", "январь"))
    assert result["blocking_issues"]
    assert result["rows"] == []


def test_truncated_preview_marks_article_incomplete_and_blocks_budget_creation():
    result = matrix(["1"] * 12, annual="12", limit=3)
    assert result["truncated"] is True
    assert result["blocking_issues"]
    assert result["articles"][0]["monthly_total"] == "12.00"
    assert result["articles"][0]["preview_complete"] is False


def test_exact_limit_with_no_omitted_rows_is_not_truncated():
    result = matrix(["1", "2", "3"], annual="6", months=MONTHS[:3], limit=3)
    assert result["truncated"] is False
    assert result["articles"][0]["preview_complete"] is True


def test_comma_and_grouped_spaces_preserve_raw_decimal_precision():
    result = matrix(["1 234,005", "0", "0"], annual="1234.005", months=MONTHS[:3])
    assert result["articles"][0]["months"][0]["raw_amount"] == "1234.005"
    assert result["rows"][0]["amount"] == "1234.00"


def test_invalid_value_in_unlabelled_december_cannot_be_ignored():
    result = matrix(["1"] * 11 + ["NaN"], months=MONTHS[:11] + ("",))
    assert result["blocking_issues"]
    assert "ДДС!N12" in result["blocking_issues"][0]
    assert result["rows"] == []


def test_labelled_annual_column_after_november_is_not_inferred_as_december():
    result = matrix(["1"] * 11 + ["100"], annual="11", months=MONTHS[:11] + ("Годовой итог",))
    assert result["inferred_december"] is False
    assert len(result["rows"]) == 11
    assert result["articles"][0]["monthly_total"] == "11.00"
