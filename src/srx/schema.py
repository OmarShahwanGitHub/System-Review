"""Evidence-table field definitions (spec §6.2). schema.yaml is the contract
between extraction prompts, reconciliation, gold loading, and benchmarking."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

VALID_TYPES = {"numeric", "int", "categorical", "text", "bool"}
VALID_LEVELS = {"study", "arm", "outcome"}


class SchemaError(Exception):
    pass


@dataclass
class FieldSpec:
    id: str
    type: str
    description: str = ""
    unit: Optional[str] = None
    accepted_units: list[str] = field(default_factory=list)
    allowed_values: list[str] = field(default_factory=list)
    level: str = "study"
    tolerance: Optional[float] = None


@dataclass
class Schema:
    fields: list[FieldSpec]

    @property
    def by_id(self) -> dict[str, FieldSpec]:
        return {f.id: f for f in self.fields}

    def hash(self) -> str:
        canonical = json.dumps(
            [vars(f) for f in self.fields], sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_schema(path: Path) -> Schema:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "fields" not in raw:
        raise SchemaError(f"{path}: expected a top-level 'fields' list")
    if not isinstance(raw["fields"], list) or not raw["fields"]:
        raise SchemaError(f"{path}: 'fields' must be a non-empty list")
    specs: list[FieldSpec] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw["fields"]):
        if not isinstance(entry, dict) or "id" not in entry or "type" not in entry:
            raise SchemaError(f"{path}: field #{i} needs at least 'id' and 'type'")
        if entry["type"] not in VALID_TYPES:
            raise SchemaError(
                f"{path}: field '{entry['id']}' has unknown type '{entry['type']}' "
                f"(valid: {sorted(VALID_TYPES)})"
            )
        if entry.get("level", "study") not in VALID_LEVELS:
            raise SchemaError(f"{path}: field '{entry['id']}' has invalid level")
        if entry["id"] in seen:
            raise SchemaError(f"{path}: duplicate field id '{entry['id']}'")
        seen.add(entry["id"])
        known = {k: v for k, v in entry.items() if k in FieldSpec.__dataclass_fields__}
        specs.append(FieldSpec(**known))
    return Schema(fields=specs)


def _slug(header: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", header.strip().lower())
    return s.strip("_") or "col"


def _is_number(cell: str) -> bool:
    return re.fullmatch(r"-?[\d,]+(\.\d+)?%?", cell.strip()) is not None


def draft_schema_from_csv(csv_path: Path, study_id_column: str = "study_id") -> str:
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SchemaError(f"{csv_path}: no data rows")
    fields = []
    for header in rows[0].keys():
        if header == study_id_column:
            continue
        cells = [r[header].strip() for r in rows if (r[header] or "").strip()]
        numeric = cells and sum(_is_number(c) for c in cells) / len(cells) >= 0.8
        fields.append(
            {
                "id": _slug(header),
                "type": "numeric" if numeric else "text",
                "description": f"REFINE ME — drafted from column '{header}'",
            }
        )
    return yaml.safe_dump({"fields": fields}, sort_keys=False, allow_unicode=True)
