import pytest

from srx.goldload import GoldError, load_gold
from srx.schema import FieldSpec, Schema

SCHEMA = Schema(fields=[
    FieldSpec(id="n_total", type="int"),
    FieldSpec(id="mean_age", type="numeric", unit="years"),
    FieldSpec(id="country", type="text"),
])

MAPPING = """\
study_id_column: study_id
columns:
  "N randomised": n_total
  "Mean age (I)": {field: mean_age, unit: years}
  "Country": country
"""


def write(tmp_path, csv_text, mapping_text=MAPPING):
    gold = tmp_path / "gold.csv"
    gold.write_text(csv_text, encoding="utf-8")
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text(mapping_text, encoding="utf-8")
    return gold, mapping


def test_load_gold_happy_path(tmp_path):
    gold, mapping = write(
        tmp_path,
        "study_id,N randomised,Mean age (I),Country\n"
        'smith-2019,124,61.2,UK\n'
        "jones-2020,88,,Canada\n",
    )
    g = load_gold(gold, mapping, SCHEMA)
    assert g["smith-2019"]["n_total"].value == 124.0
    assert g["smith-2019"]["mean_age"].precision == 1
    assert g["smith-2019"]["mean_age"].unit == "years"
    assert g["smith-2019"]["country"].value == "UK"
    assert "mean_age" not in g["jones-2020"]          # empty cell -> absent


def test_duplicate_study_rejected(tmp_path):
    gold, mapping = write(
        tmp_path,
        "study_id,N randomised,Mean age (I),Country\n"
        "smith-2019,1,2,UK\nsmith-2019,3,4,UK\n",
    )
    with pytest.raises(GoldError, match="duplicate"):
        load_gold(gold, mapping, SCHEMA)


def test_unknown_field_in_mapping_rejected(tmp_path):
    gold, mapping = write(
        tmp_path,
        "study_id,X\nsmith-2019,1\n",
        "study_id_column: study_id\ncolumns:\n  \"X\": no_such_field\n",
    )
    with pytest.raises(GoldError, match="no_such_field"):
        load_gold(gold, mapping, SCHEMA)


def test_unparseable_numeric_cell_rejected(tmp_path):
    gold, mapping = write(
        tmp_path,
        "study_id,N randomised,Mean age (I),Country\n"
        "smith-2019,unclear,61.2,UK\n",
    )
    with pytest.raises(GoldError, match="N randomised"):
        load_gold(gold, mapping, SCHEMA)


def test_unmapped_column_warns_but_loads(tmp_path):
    gold, mapping = write(
        tmp_path,
        "study_id,N randomised,Mean age (I),Country,Notes\n"
        "smith-2019,124,61.2,UK,check later\n",
    )
    with pytest.warns(UserWarning, match="Notes"):
        g = load_gold(gold, mapping, SCHEMA)
    assert g["smith-2019"]["n_total"].value == 124.0


def test_missing_study_id_column_rejected(tmp_path):
    gold, mapping = write(tmp_path, "id,N randomised\ns1,5\n")
    with pytest.raises(GoldError, match="study_id"):
        load_gold(gold, mapping, SCHEMA)


def test_empty_study_id_rejected(tmp_path):
    gold, mapping = write(
        tmp_path,
        "study_id,N randomised,Mean age (I),Country\n"
        " ,124,61.2,UK\n",
    )
    with pytest.raises(GoldError, match="empty study id"):
        load_gold(gold, mapping, SCHEMA)


def test_unit_override_beats_spec_unit(tmp_path):
    gold, mapping = write(
        tmp_path,
        "study_id,Mean age (I)\ns1,732\n",
        'study_id_column: study_id\ncolumns:\n  "Mean age (I)": {field: mean_age, unit: months}\n',
    )
    g = load_gold(gold, mapping, SCHEMA)
    assert g["s1"]["mean_age"].unit == "months"   # override wins over spec's "years"


def test_default_study_id_column(tmp_path):
    gold, mapping = write(
        tmp_path,
        "study_id,Country\ns1,UK\n",
        'columns:\n  "Country": country\n',
    )
    assert load_gold(gold, mapping, SCHEMA)["s1"]["country"].value == "UK"


def test_mapping_dict_missing_field_key_rejected(tmp_path):
    gold, mapping = write(
        tmp_path,
        "study_id,Country\ns1,UK\n",
        'study_id_column: study_id\ncolumns:\n  "Country": {unit: years}\n',
    )
    with pytest.raises(GoldError, match="field"):
        load_gold(gold, mapping, SCHEMA)
