import pytest
import yaml

from srx.schema import Schema, SchemaError, draft_schema_from_csv, load_schema

GOOD = """\
fields:
  - id: n_total
    type: int
    description: Total number randomised
  - id: mean_age_int
    type: numeric
    unit: years
    accepted_units: [years, months]
  - id: blinding
    type: categorical
    allowed_values: [open-label, single-blind, double-blind]
"""


def test_load_schema(tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text(GOOD)
    s = load_schema(p)
    assert [f.id for f in s.fields] == ["n_total", "mean_age_int", "blinding"]
    assert s.by_id["mean_age_int"].unit == "years"
    assert s.by_id["blinding"].allowed_values == ["open-label", "single-blind", "double-blind"]
    assert s.by_id["n_total"].level == "study"
    assert len(s.hash()) == 64
    assert s.hash() == load_schema(p).hash()


def test_duplicate_ids_rejected(tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text("fields:\n  - {id: a, type: int}\n  - {id: a, type: text}\n")
    with pytest.raises(SchemaError, match="duplicate"):
        load_schema(p)


def test_unknown_type_rejected(tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text("fields:\n  - {id: a, type: floaty}\n")
    with pytest.raises(SchemaError, match="floaty"):
        load_schema(p)


def test_draft_from_csv(tmp_path):
    p = tmp_path / "gold.csv"
    p.write_text(
        "study_id,N randomised,Country\n"
        "smith-2019,124,UK\n"
        "jones-2020,88,Canada\n"
    )
    draft = yaml.safe_load(draft_schema_from_csv(p))
    ids = {f["id"]: f for f in draft["fields"]}
    assert "study_id" not in ids
    assert ids["n_randomised"]["type"] == "numeric"
    assert ids["country"]["type"] == "text"


def test_missing_fields_key_rejected(tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text("nothing: here\n")
    with pytest.raises(SchemaError, match="fields"):
        load_schema(p)


def test_null_fields_value_rejected(tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text("fields:\n")
    with pytest.raises(SchemaError, match="fields"):
        load_schema(p)


def test_missing_id_or_type_rejected(tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text("fields:\n  - {id: a}\n")
    with pytest.raises(SchemaError, match="needs at least"):
        load_schema(p)


def test_invalid_level_rejected(tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text("fields:\n  - {id: a, type: int, level: cohort}\n")
    with pytest.raises(SchemaError, match="level"):
        load_schema(p)


def test_draft_numeric_threshold_boundary(tmp_path):
    p = tmp_path / "gold.csv"
    p.write_text(
        "study_id,mostly_num,mostly_text\n"
        "s1,1,1\ns2,2,x\ns3,3,y\ns4,4,z\ns5,x,2\n"
    )
    draft = yaml.safe_load(draft_schema_from_csv(p))
    ids = {f["id"]: f for f in draft["fields"]}
    assert ids["mostly_num"]["type"] == "numeric"   # 4/5 = 80%, at the >= threshold
    assert ids["mostly_text"]["type"] == "text"     # 2/5 = 40%


def test_draft_reads_utf8_bom(tmp_path):
    p = tmp_path / "gold.csv"
    p.write_bytes("﻿study_id,N\ns1,5\n".encode("utf-8"))
    draft = yaml.safe_load(draft_schema_from_csv(p))
    assert draft["fields"][0]["id"] == "n"
