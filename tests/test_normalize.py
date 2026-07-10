from srx.normalize import (
    canonical_category,
    convert_unit,
    decimal_places,
    parse_ci,
    parse_number,
    parse_p,
    values_equal,
)
from srx.schema import FieldSpec

NUM = FieldSpec(id="x", type="numeric")
INT = FieldSpec(id="n", type="int")
CAT = FieldSpec(id="c", type="categorical")
BOOL = FieldSpec(id="b", type="bool")


def test_parse_number():
    assert parse_number("1,234") == 1234.0
    assert parse_number("12·5") == 12.5
    assert parse_number("12.5%") == 12.5
    assert parse_number("n=124") == 124.0
    assert parse_number(88) == 88.0
    assert parse_number("not reported") is None


def test_convert_unit():
    assert convert_unit(1500.0, "mg", "g") == 1.5
    assert convert_unit(2.0, "weeks", "days") == 14.0
    assert convert_unit(0.12, "proportion", "%") == 12.0
    assert convert_unit(12.0, "%", "proportion") == 0.12
    assert convert_unit(5.0, "mg", "days") is None
    assert convert_unit(5.0, "MG", "g") == 0.005


def test_decimal_places():
    assert decimal_places("12.50") == 2
    assert decimal_places("12") == 0
    assert decimal_places("1,234.5") == 1


def test_values_equal_numeric():
    assert values_equal("1,234", None, 1234, None, NUM)
    assert values_equal(1500, "mg", 1.5, "g", NUM)
    assert not values_equal(1500, "mg", 1.5, "g", NUM, convert_units=False)
    # gold reported to 1 decimal: engine 61.24 matches gold 61.2
    assert values_equal(61.24, None, 61.2, None, NUM, gold_precision=1)
    assert not values_equal(61.24, None, 61.2, None, NUM)
    # tolerance override on the spec
    tol = FieldSpec(id="x", type="numeric", tolerance=0.5)
    assert values_equal(61.4, None, 61.2, None, tol)


def test_values_equal_categorical_and_bool():
    assert values_equal("Double-Blind ", None, "double-blind", None, CAT)
    assert not values_equal("open-label", None, "double-blind", None, CAT)
    assert values_equal("Yes", None, True, None, BOOL)
    assert values_equal(False, None, "no", None, BOOL)


def test_values_equal_unparseable_numeric_is_not_equal():
    assert not values_equal("unclear", None, 5, None, NUM)


def test_parse_ci_and_p_variants():
    assert parse_ci("0.75 (0.60–0.94)") == (0.75, 0.60, 0.94)
    assert parse_ci("0.75 (95% CI 0.60 to 0.94)") == (0.75, 0.60, 0.94)
    assert parse_ci("no effect seen") is None
    assert parse_p("p<0.001") == ("<", 0.001)
    assert parse_p("<0.001") == ("<", 0.001)
    assert parse_p("p = 0.03") == ("=", 0.03)
    assert parse_p("not significant") is None


def test_values_equal_text_ci_and_p_formats():
    txt = FieldSpec(id="or_ci", type="text")
    assert values_equal("0.75 (95% CI 0.60 to 0.94)", None, "0.75 (0.60–0.94)", None, txt)
    assert not values_equal("0.80 (0.61–0.94)", None, "0.75 (0.60–0.94)", None, txt)
    assert values_equal("p < 0.001", None, "<0.001", None, txt)
