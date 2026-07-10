"""Gold-standard loader (spec §6.6): the user's human extraction sheet becomes
the benchmark reference. gold.csv columns map to schema fields via mapping.yaml.
Study ids must match PDF filename stems (documented in README)."""
from __future__ import annotations

import csv
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from srx.normalize import decimal_places, parse_number
from srx.schema import Schema


class GoldError(Exception):
    pass


@dataclass
class GoldValue:
    raw: str
    value: Any
    unit: Optional[str]
    precision: Optional[int]


def load_gold(gold_csv: Path, mapping_yaml: Path, schema: Schema) -> dict[str, dict[str, GoldValue]]:
    mapping = yaml.safe_load(Path(mapping_yaml).read_text(encoding="utf-8"))
    id_col = mapping.get("study_id_column", "study_id")
    columns: dict[str, dict] = {}
    for col, target in (mapping.get("columns") or {}).items():
        entry = {"field": target, "unit": None} if isinstance(target, str) else dict(target)
        if entry["field"] not in schema.by_id:
            raise GoldError(f"{mapping_yaml}: column '{col}' maps to unknown field '{entry['field']}'")
        columns[col] = entry

    with open(gold_csv, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise GoldError(f"{gold_csv}: no data rows")
    if id_col not in rows[0]:
        raise GoldError(f"{gold_csv}: missing study id column '{id_col}'")

    for col in rows[0]:
        if col != id_col and col not in columns:
            warnings.warn(f"gold column '{col}' is not mapped and will be ignored")

    gold: dict[str, dict[str, GoldValue]] = {}
    for row in rows:
        study_id = (row[id_col] or "").strip()
        if not study_id:
            raise GoldError(f"{gold_csv}: row with empty study id")
        if study_id in gold:
            raise GoldError(f"{gold_csv}: duplicate study id '{study_id}'")
        fields: dict[str, GoldValue] = {}
        for col, entry in columns.items():
            raw = (row.get(col) or "").strip()
            if not raw:
                continue
            spec = schema.by_id[entry["field"]]
            if spec.type in ("numeric", "int"):
                value = parse_number(raw)
                if value is None:
                    raise GoldError(
                        f"{gold_csv}: study '{study_id}', column '{col}': "
                        f"cannot parse numeric cell '{raw}'"
                    )
                precision: Optional[int] = decimal_places(raw)
            else:
                value, precision = raw, None
            fields[entry["field"]] = GoldValue(
                raw=raw, value=value, unit=entry.get("unit") or spec.unit, precision=precision
            )
        gold[study_id] = fields
    return gold
