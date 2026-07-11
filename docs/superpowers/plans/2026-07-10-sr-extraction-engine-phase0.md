# SR Extraction Engine — Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python extraction engine + validation benchmark: study PDFs in → provenance-anchored evidence table out, scored field-by-field against the user's gold-standard human extraction.

**Architecture:** A `srx` Python package with an `sr` CLI. Pipeline per study: pdfplumber text layer + native PDF to Claude → two independent extraction passes (structured tool output, every value with quote+page) → local anchor verification (anti-hallucination gate) → reconciliation (agree / adjudicate / human-flag) → per-study JSON checkpoints → SQLite run registry. Benchmark loads the user's gold CSV, classifies every field into an error taxonomy, and renders a self-contained HTML report with click-to-expand provenance and a run-over-run diff.

**Tech Stack:** Python 3.12, `anthropic` SDK, `pdfplumber`, `PyYAML`, SQLite (stdlib), argparse (stdlib). Dev: `pytest`, `fpdf2` (synthetic PDF fixtures). No web framework, no server.

**Spec:** `docs/superpowers/specs/2026-07-10-sr-extraction-engine-design.md` (approved 2026-07-10). Section references like §6.4 point there.

## Global Constraints

Every task's requirements implicitly include all of these (copied from the spec):

- Python 3.12. On this machine use `/usr/bin/python3.12` (default `python3` is a conda 3.13 — do not use it). Venv lives at `~/.venvs/srx` (Linux FS; the repo is on `/mnt/c`, where venvs are slow).
- ~~`temperature=0` on **every** model call.~~ **Amended 2026-07-10 (final review):** no sampling parameters are sent — the current API 400s on non-default temperature for Fable 5/Sonnet 5; reproducibility = prompt-version pinning + benchmark regression.
- Provenance on every value: `{value, unit, quote, page, method, anchor_status, confidence}` — no naked numbers anywhere in stored data.
- Provenance is **never inline** in displayed tables — click-to-expand only (`<details>` in HTML reports).
- Anchor gate: quote fuzzy-matched ≥ 0.90 on claimed page ±1. No match on a text-bearing PDF → value **flagged, never accepted**. Scanned PDFs → `vision-only` anchors → auto-flagged for human verification.
- Pass disagreements are **never averaged**: adjudication pass or human-flag queue.
- Cost: per-run token/cost logging **including prompt-cache tokens (creation 1.25×, read 0.1× input price — amended 2026-07-10)**; configurable cost cap, default **$20/run**, aborting cleanly at a per-study checkpoint boundary.
- Per-study checkpointing: a run **resumes**, never restarts. API errors: retry w/ exponential backoff; a study that exhausts retries is `errored`, not fatal.
- Run store is append-only in spirit: never delete rows; benchmark scores recorded per run so reports can diff run-over-run.
- Benchmark scoring denominator: only accepted values count for agreement; flagged values score as `missed` (flagged notation) — they can never inflate the score.
- Success gate (do not weaken in code or docs): ≥95% agreement on objective numeric fields after adjudication; zero unanchored numeric values accepted.
- All user-facing text says **"systematic review"** — never "system review".
- Study PDFs stay local, sent only to the model API. `projects/` is gitignored so copyrighted PDFs are never committed.
- Commit after every task; end every commit message with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Tests must never hit the network: the Anthropic SDK is always injected/faked in tests.

## File Structure

```
pyproject.toml                  # package metadata, deps, `sr` entry point
.gitignore
README.md                       # Task 14
src/srx/
  __init__.py                   # __version__
  models.py                     # provenance dataclasses + JSON round-trip
  schema.py                     # FieldSpec/Schema, schema.yaml load, CSV→draft
  normalize.py                  # number/unit/category normalization + equality
  ingest.py                     # pdfplumber text layers, scanned detection
  anchor.py                     # quote anchor verification (anti-hallucination)
  claude_client.py              # SDK wrapper: forced tool use, retry, pricing
  extractor.py                  # dual-pass extraction prompts + tool schema
  reconcile.py                  # pass comparison, adjudication, flagging
  runstore.py                   # SQLite run registry
  goldload.py                   # gold.csv + mapping.yaml loader
  bench.py                      # scoring + error taxonomy
  report.py                     # HTML/Markdown reports, run diff
  project.py                    # project dir layout + config.yaml
  pipeline.py                   # extract-run orchestration, checkpoints, cost cap
  cli.py                        # `sr extract|bench|report|schema` (argparse)
tests/
  conftest.py                   # synthetic PDF + schema/gold fixtures
  test_models.py  test_schema.py  test_normalize.py  test_ingest.py
  test_anchor.py  test_claude_client.py  test_extractor.py  test_reconcile.py
  test_runstore.py  test_goldload.py  test_bench.py  test_report.py
  test_pipeline.py  test_cli.py  test_integration.py
```

User project directories (data, not code — gitignored) follow spec §5:
`projects/<name>/{studies/*.pdf, schema.yaml, gold/gold.csv, gold/mapping.yaml, config.yaml, runs/<run_id>/, sr.sqlite}`

---

### Task 1: Scaffold + provenance data models

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/srx/__init__.py`, `src/srx/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces (used by every later task):
  - `srx.models.Candidate(value: Any, unit: str|None, quote: str, page: int, confidence: float)` — one pass's raw output for one field.
  - `srx.models.ExtractedValue(field_id: str, value: Any, unit: str|None, quote: str, page: int, method: str, anchor_status: str, confidence: float|None)` — `method ∈ {"pass-agreed","adjudicated","human"}`, `anchor_status ∈ {"verified","vision-only","failed"}`.
  - `srx.models.FlaggedField(field_id: str, reason: str, candidates: list[Candidate])`.
  - `srx.models.StudyExtraction(study_id: str, scanned: bool, accepted: dict[str, ExtractedValue], flagged: dict[str, FlaggedField], not_found: list[str], cost_usd: float)` with `.to_json() -> str` and `StudyExtraction.from_json(s: str)`.
  - All four have `.to_dict()`/`.from_dict()`.

- [ ] **Step 1: Create environment and package skeleton**

```bash
cd /mnt/c/Users/omars/Documents/System-Review
/usr/bin/python3.12 -m venv ~/.venvs/srx
~/.venvs/srx/bin/pip install --upgrade pip
```

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "srx"
version = "0.1.0"
description = "Systematic review extraction engine"
requires-python = ">=3.12"
dependencies = [
    "anthropic>=0.40",
    "pdfplumber>=0.11",
    "PyYAML>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "fpdf2>=2.7"]

[project.scripts]
sr = "srx.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `.gitignore`:

```
__pycache__/
*.egg-info/
.pytest_cache/
.venv/
projects/
```

Create `src/srx/__init__.py`:

```python
__version__ = "0.1.0"
```

Install editable (note: `cli.py` doesn't exist yet; the `sr` script target may warn on invocation but install succeeds):

```bash
~/.venvs/srx/bin/pip install -e ".[dev]"
```

Expected: `Successfully installed srx-0.1.0 ...`

- [ ] **Step 2: Write the failing test**

`tests/test_models.py`:

```python
from srx.models import Candidate, ExtractedValue, FlaggedField, StudyExtraction


def test_study_extraction_json_round_trip():
    ev = ExtractedValue(
        field_id="n_total", value=124, unit=None,
        quote="A total of 124 patients were randomised",
        page=3, method="pass-agreed", anchor_status="verified", confidence=0.95,
    )
    ff = FlaggedField(
        field_id="mean_age",
        reason="pass-disagreement-unresolved",
        candidates=[
            Candidate(value=61.2, unit="years", quote="mean age 61.2", page=4, confidence=0.8),
            Candidate(value=62.1, unit="years", quote="mean age 62.1", page=4, confidence=0.7),
        ],
    )
    se = StudyExtraction(
        study_id="smith-2019", scanned=False,
        accepted={"n_total": ev}, flagged={"mean_age": ff},
        not_found=["dropout_rate"], cost_usd=0.42,
    )
    back = StudyExtraction.from_json(se.to_json())
    assert back == se
    assert back.accepted["n_total"].quote.startswith("A total")
    assert back.flagged["mean_age"].candidates[1].value == 62.1


def test_candidate_dict_round_trip():
    c = Candidate(value="placebo", unit=None, quote="matched placebo", page=2, confidence=0.9)
    assert Candidate.from_dict(c.to_dict()) == c
```

- [ ] **Step 3: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.models'`

- [ ] **Step 4: Implement `src/srx/models.py`**

```python
"""Provenance data model. Spec §4: every extracted value carries
{value, unit, quote, page, method, anchor_status, confidence} — no naked numbers."""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Candidate:
    value: Any
    unit: Optional[str]
    quote: str
    page: int
    confidence: float

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        return cls(**d)


@dataclass
class ExtractedValue:
    field_id: str
    value: Any
    unit: Optional[str]
    quote: str
    page: int
    method: str          # "pass-agreed" | "adjudicated" | "human"
    anchor_status: str   # "verified" | "vision-only" | "failed"
    confidence: Optional[float]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ExtractedValue":
        return cls(**d)


@dataclass
class FlaggedField:
    field_id: str
    reason: str          # "pass-disagreement-unresolved" | "anchor-failed" | "vision-only" | "presence-uncertain"
    candidates: list[Candidate]

    def to_dict(self) -> dict:
        return {
            "field_id": self.field_id,
            "reason": self.reason,
            "candidates": [c.to_dict() for c in self.candidates],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FlaggedField":
        return cls(
            field_id=d["field_id"],
            reason=d["reason"],
            candidates=[Candidate.from_dict(c) for c in d["candidates"]],
        )


@dataclass
class StudyExtraction:
    study_id: str
    scanned: bool
    accepted: dict[str, ExtractedValue]
    flagged: dict[str, FlaggedField]
    not_found: list[str]
    cost_usd: float

    def to_dict(self) -> dict:
        return {
            "format_version": 1,
            "study_id": self.study_id,
            "scanned": self.scanned,
            "accepted": {k: v.to_dict() for k, v in self.accepted.items()},
            "flagged": {k: v.to_dict() for k, v in self.flagged.items()},
            "not_found": list(self.not_found),
            "cost_usd": self.cost_usd,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StudyExtraction":
        return cls(
            study_id=d["study_id"],
            scanned=d["scanned"],
            accepted={k: ExtractedValue.from_dict(v) for k, v in d["accepted"].items()},
            flagged={k: FlaggedField.from_dict(v) for k, v in d["flagged"].items()},
            not_found=list(d["not_found"]),
            cost_usd=d["cost_usd"],
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "StudyExtraction":
        return cls.from_dict(json.loads(s))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_models.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/srx/__init__.py src/srx/models.py tests/test_models.py
git commit -m "feat: scaffold srx package with provenance data models

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Schema module

**Files:**
- Create: `src/srx/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `srx.schema.FieldSpec(id: str, type: str, description: str = "", unit: str|None = None, accepted_units: list[str] = [], allowed_values: list[str] = [], level: str = "study", tolerance: float|None = None)` — `type ∈ {"numeric","int","categorical","text","bool"}`.
  - `srx.schema.Schema(fields: list[FieldSpec])` with `.by_id -> dict[str, FieldSpec]` (property) and `.hash() -> str` (sha256 hex of canonical JSON).
  - `srx.schema.load_schema(path: pathlib.Path) -> Schema` — raises `srx.schema.SchemaError` on duplicate ids / unknown types / missing `fields` key.
  - `srx.schema.draft_schema_from_csv(csv_path: pathlib.Path, study_id_column: str = "study_id") -> str` — YAML draft text; a column is typed `numeric` when ≥80% of its non-empty cells parse as numbers, else `text`.

- [ ] **Step 1: Write the failing test**

`tests/test_schema.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.schema'`

- [ ] **Step 3: Implement `src/srx/schema.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_schema.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/srx/schema.py tests/test_schema.py
git commit -m "feat: schema.yaml loading, validation, and CSV draft generator

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Normalizer

**Files:**
- Create: `src/srx/normalize.py`
- Test: `tests/test_normalize.py`

**Interfaces:**
- Consumes: `srx.schema.FieldSpec`.
- Produces:
  - `srx.normalize.parse_number(raw: Any) -> float|None` — handles ints/floats passthrough, `"1,234"`, `"12·5"` (middle-dot decimal), `"12.5%"`, `"n=124"`, surrounding whitespace. `None` if unparseable.
  - `srx.normalize.convert_unit(value: float, from_unit: str|None, to_unit: str|None) -> float|None` — case-insensitive; supports g/mg/kg, l/ml/dl, days/weeks, `%`↔`proportion`; identical units pass through; `None` when not convertible.
  - `srx.normalize.canonical_category(raw: str) -> str` — casefold, strip, collapse whitespace.
  - `srx.normalize.decimal_places(raw: str) -> int` — decimals in a raw string number ("12.50" → 2, "12" → 0).
  - `srx.normalize.parse_ci(raw: str) -> tuple[float, float, float]|None` — `(point, low, high)` from CI format variants: `"0.75 (0.60–0.94)"`, `"0.75 (95% CI 0.60 to 0.94)"` (en/em dash, hyphen, comma, "to").
  - `srx.normalize.parse_p(raw: str) -> tuple[str, float]|None` — `("<"|"="|">", value)` from `"p<0.001"`, `"<0.001"`, `"p = 0.03"`.
  - `srx.normalize.values_equal(engine_value, engine_unit, gold_value, gold_unit, spec: FieldSpec, gold_precision: int|None = None, convert_units: bool = True) -> bool` — the single comparison used by reconciler and bench. Numeric/int: parse both, unit-convert engine→gold when needed, round both to `gold_precision` when given, else compare with `spec.tolerance` (absolute) when set, else `math.isclose(rel_tol=1e-9)`. Categorical/text: `canonical_category` equality. Bool: truthy-string map.

- [ ] **Step 1: Write the failing test**

`tests/test_normalize.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.normalize'`

- [ ] **Step 3: Implement `src/srx/normalize.py`**

```python
"""Value normalization and the single equality used by reconciler + benchmark.
Spec §6.5: thousands separators, percent vs proportion, unit conversions,
format variants. Disagreements are detected here, never resolved here."""
from __future__ import annotations

import math
import re
from typing import Any, Optional

from srx.schema import FieldSpec

# unit -> (canonical base, factor to base). Case-insensitive lookup.
UNIT_TABLE: dict[str, tuple[str, float]] = {
    "g": ("g", 1.0), "mg": ("g", 0.001), "kg": ("g", 1000.0), "mcg": ("g", 1e-6),
    "l": ("l", 1.0), "ml": ("l", 0.001), "dl": ("l", 0.1),
    "days": ("days", 1.0), "weeks": ("days", 7.0),
    "%": ("%", 1.0), "proportion": ("%", 100.0),
}

_TRUTHY = {"yes", "true", "y", "1"}
_FALSY = {"no", "false", "n", "0"}

_NUM_RE = re.compile(r"-?\d[\d,]*(?:[.·]\d+)?")


def parse_number(raw: Any) -> Optional[float]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    m = _NUM_RE.search(raw)
    if not m:
        return None
    return float(m.group(0).replace(",", "").replace("·", "."))


def convert_unit(value: float, from_unit: Optional[str], to_unit: Optional[str]) -> Optional[float]:
    f = (from_unit or "").strip().lower()
    t = (to_unit or "").strip().lower()
    if f == t:
        return value
    if f not in UNIT_TABLE or t not in UNIT_TABLE:
        return None
    f_base, f_factor = UNIT_TABLE[f]
    t_base, t_factor = UNIT_TABLE[t]
    if f_base != t_base:
        return None
    return value * f_factor / t_factor


def canonical_category(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw)).strip().casefold()


def decimal_places(raw: str) -> int:
    m = _NUM_RE.search(str(raw))
    if not m:
        return 0
    s = m.group(0).replace("·", ".")
    return len(s.split(".")[1]) if "." in s else 0


_CI_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*\(\s*(?:95\s*%\s*ci[:\s]*)?"
    r"(-?\d+(?:\.\d+)?)\s*(?:–|—|-|,|to)\s*(-?\d+(?:\.\d+)?)\s*\)",
    re.IGNORECASE,
)


def parse_ci(raw: str) -> Optional[tuple[float, float, float]]:
    m = _CI_RE.search(str(raw).replace("·", "."))
    if not m:
        return None
    point, low, high = (float(g) for g in m.groups())
    return (point, low, high)


_P_RE = re.compile(r"^\s*p?\s*(<|=|>|≤|≥)?\s*(\d+(?:\.\d+)?|\.\d+)\s*$", re.IGNORECASE)


def parse_p(raw: str) -> Optional[tuple[str, float]]:
    m = _P_RE.match(str(raw).replace("·", "."))
    if not m:
        return None
    return (m.group(1) or "=", float(m.group(2)))


def _bool_of(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    s = canonical_category(v)
    if s in _TRUTHY:
        return True
    if s in _FALSY:
        return False
    return None


def values_equal(
    engine_value: Any,
    engine_unit: Optional[str],
    gold_value: Any,
    gold_unit: Optional[str],
    spec: FieldSpec,
    gold_precision: Optional[int] = None,
    convert_units: bool = True,
) -> bool:
    if spec.type == "bool":
        e, g = _bool_of(engine_value), _bool_of(gold_value)
        return e is not None and e == g
    if spec.type in ("categorical", "text"):
        if canonical_category(engine_value) == canonical_category(gold_value):
            return True
        if spec.type == "text":
            e_ci, g_ci = parse_ci(str(engine_value)), parse_ci(str(gold_value))
            if e_ci and g_ci:
                return all(math.isclose(a, b, rel_tol=1e-9) for a, b in zip(e_ci, g_ci))
            e_p, g_p = parse_p(str(engine_value)), parse_p(str(gold_value))
            if e_p and g_p:
                return e_p == g_p
        return False
    # numeric / int
    e, g = parse_number(engine_value), parse_number(gold_value)
    if e is None or g is None:
        return False
    if (engine_unit or "") != (gold_unit or ""):
        if not convert_units:
            return False
        converted = convert_unit(e, engine_unit, gold_unit)
        if converted is None:
            return False
        e = converted
    if gold_precision is not None:
        return round(e, gold_precision) == round(g, gold_precision)
    if spec.tolerance is not None:
        return abs(e - g) <= spec.tolerance
    return math.isclose(e, g, rel_tol=1e-9)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_normalize.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/srx/normalize.py tests/test_normalize.py
git commit -m "feat: value normalization and typed equality comparison

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: PDF ingestion + synthetic PDF fixtures

**Files:**
- Create: `src/srx/ingest.py`, `tests/conftest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `srx.ingest.PdfDoc(study_id: str, path: pathlib.Path, pages: list[str], scanned: bool)` — `pages[0]` is page 1's text (`""` for empty/image pages); `scanned` is True when total stripped text < `srx.ingest.SCANNED_MIN_CHARS` (=100).
  - `srx.ingest.load_pdf(path: pathlib.Path) -> PdfDoc` — `study_id` = filename stem.
  - `srx.ingest.load_studies(studies_dir: pathlib.Path) -> list[PdfDoc]` — sorted `*.pdf`.
  - Test fixture helpers in `tests/conftest.py` (used by Tasks 5, 13, 15): `make_pdf(path: Path, pages: list[str]) -> Path` (one page per string, wrapped text) and `make_scanned_pdf(path: Path) -> Path` (one image-like page, no text layer).

- [ ] **Step 1: Write fixture helpers**

`tests/conftest.py`:

```python
"""Shared fixtures. Synthetic PDFs only — tests never depend on copyrighted
study PDFs (spec §9)."""
from pathlib import Path

from fpdf import FPDF


def make_pdf(path: Path, pages: list[str]) -> Path:
    pdf = FPDF()
    pdf.set_font("helvetica", size=11)
    for text in pages:
        pdf.add_page()
        pdf.multi_cell(w=180, text=text)
    pdf.output(str(path))
    return path


def make_scanned_pdf(path: Path) -> Path:
    pdf = FPDF()
    pdf.add_page()
    pdf.rect(20, 20, 150, 200)  # shapes only: no extractable text layer
    pdf.output(str(path))
    return path
```

- [ ] **Step 2: Write the failing test**

`tests/test_ingest.py`:

```python
from conftest import make_pdf, make_scanned_pdf

from srx.ingest import load_pdf, load_studies


def test_load_pdf_text_layer(tmp_path):
    p = make_pdf(
        tmp_path / "smith-2019.pdf",
        [
            "Randomised controlled trial of drug X. " * 5,
            "A total of 124 patients were randomised to intervention or placebo.",
        ],
    )
    doc = load_pdf(p)
    assert doc.study_id == "smith-2019"
    assert len(doc.pages) == 2
    assert "124 patients were randomised" in doc.pages[1]
    assert doc.scanned is False


def test_scanned_detection(tmp_path):
    p = make_scanned_pdf(tmp_path / "old-1987.pdf")
    doc = load_pdf(p)
    assert doc.scanned is True
    assert all(not page.strip() for page in doc.pages)


def test_load_studies_sorted(tmp_path):
    studies = tmp_path / "studies"
    studies.mkdir()
    make_pdf(studies / "b.pdf", ["text " * 40])
    make_pdf(studies / "a.pdf", ["text " * 40])
    (studies / "notes.txt").write_text("ignore me")
    docs = load_studies(studies)
    assert [d.study_id for d in docs] == ["a", "b"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.ingest'`

- [ ] **Step 4: Implement `src/srx/ingest.py`**

```python
"""PDF ingestion (spec §6.1). Each study PDF gets (a) a local per-page text
layer for anchor verification and (b) is later sent natively to the model.
Scanned (no-text-layer) PDFs are marked so the anchor gate can degrade to
vision-only (spec §6.4)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber

SCANNED_MIN_CHARS = 100


@dataclass
class PdfDoc:
    study_id: str
    path: Path
    pages: list[str]
    scanned: bool


def load_pdf(path: Path) -> PdfDoc:
    path = Path(path)
    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    total_chars = sum(len(p.strip()) for p in pages)
    return PdfDoc(
        study_id=path.stem,
        path=path,
        pages=pages,
        scanned=total_chars < SCANNED_MIN_CHARS,
    )


def load_studies(studies_dir: Path) -> list[PdfDoc]:
    return [load_pdf(p) for p in sorted(Path(studies_dir).glob("*.pdf"))]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_ingest.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/srx/ingest.py tests/conftest.py tests/test_ingest.py
git commit -m "feat: PDF ingestion with scanned detection + synthetic PDF fixtures

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Anchor verifier (anti-hallucination gate)

**Files:**
- Create: `src/srx/anchor.py`
- Test: `tests/test_anchor.py`

**Interfaces:**
- Consumes: nothing from other tasks (operates on `list[str]` of page texts).
- Produces:
  - `srx.anchor.AnchorResult(status: str, page: int|None, score: float)` — `status ∈ {"verified","vision-only","failed"}`.
  - `srx.anchor.normalize_text(s: str) -> str` — lowercase, repair hyphenated line breaks (`exam-\nple` → `example`), collapse all whitespace to single spaces.
  - `srx.anchor.find_anchor(quote: str, pages: list[str], claimed_page: int, scanned: bool = False) -> AnchorResult` — spec §6.4: search claimed page ±1 (1-based, clamped); normalized-substring fast path scores 1.0; else sliding-window `difflib.SequenceMatcher` ratio, verified iff ≥ `srx.anchor.SIM_THRESHOLD` (=0.90); scanned docs short-circuit to `vision-only`.

- [ ] **Step 1: Write the failing test**

`tests/test_anchor.py`:

```python
from srx.anchor import SIM_THRESHOLD, find_anchor, normalize_text

PAGES = [
    "Background and methods of the trial. " * 3,
    "A total of 124 patients were ran-\ndomised   to receive either drug X or placebo.",
    "Mean age was 61.2 years (SD 9.4) in the intervention arm.",
]


def test_normalize_text_repairs_hyphenation_and_whitespace():
    assert normalize_text("ran-\ndomised   to") == "randomised to"


def test_exact_quote_verifies():
    r = find_anchor("124 patients were randomised", PAGES, claimed_page=2)
    assert r.status == "verified"
    assert r.page == 2
    assert r.score == 1.0


def test_off_by_one_page_verifies():
    r = find_anchor("Mean age was 61.2 years", PAGES, claimed_page=2)
    assert r.status == "verified"
    assert r.page == 3


def test_near_match_above_threshold_verifies():
    # one-character variant ("randomized" vs "randomised") stays above 0.90
    r = find_anchor("124 patients were randomized to receive", PAGES, claimed_page=2)
    assert r.status == "verified"
    assert r.score >= SIM_THRESHOLD


def test_fabricated_quote_fails():
    r = find_anchor("mortality was reduced by 40 percent", PAGES, claimed_page=2)
    assert r.status == "failed"
    assert r.score < SIM_THRESHOLD


def test_two_pages_away_fails():
    r = find_anchor("Mean age was 61.2 years", PAGES, claimed_page=1)
    assert r.status == "failed"


def test_scanned_short_circuits_to_vision_only():
    r = find_anchor("anything", ["", ""], claimed_page=1, scanned=True)
    assert r.status == "vision-only"
    assert r.page == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_anchor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.anchor'`

- [ ] **Step 3: Implement `src/srx/anchor.py`**

```python
"""Anchor verification (spec §6.4): every value's quote must be found in the
local text layer of the claimed page ±1. No match on a text-bearing PDF →
flagged, never silently accepted. This is the anti-hallucination gate."""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

SIM_THRESHOLD = 0.90


@dataclass
class AnchorResult:
    status: str  # "verified" | "vision-only" | "failed"
    page: Optional[int]
    score: float


def normalize_text(s: str) -> str:
    s = re.sub(r"-\s*\n\s*", "", s)   # repair hyphenated line breaks
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def _best_window_score(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    n = len(needle)
    if len(haystack) <= n:
        return SequenceMatcher(None, needle, haystack).ratio()
    # fine stride keeps windows well-aligned; window length == needle length
    # so a matching prefix isn't ratio-diluted by extra window content
    step = max(1, n // 4)
    best = 0.0
    for start in range(0, len(haystack) - n + 1, step):
        window = haystack[start : start + n]
        best = max(best, SequenceMatcher(None, needle, window).ratio())
        if best >= 0.99:
            break
    return best


def find_anchor(
    quote: str, pages: list[str], claimed_page: int, scanned: bool = False
) -> AnchorResult:
    if scanned:
        return AnchorResult(status="vision-only", page=claimed_page, score=0.0)
    needle = normalize_text(quote)
    candidates = [
        p for p in (claimed_page, claimed_page - 1, claimed_page + 1)
        if 1 <= p <= len(pages)
    ]
    best_page, best_score = None, 0.0
    for p in candidates:
        score = _best_window_score(needle, normalize_text(pages[p - 1]))
        if score > best_score:
            best_page, best_score = p, score
    if best_score >= SIM_THRESHOLD:
        return AnchorResult(status="verified", page=best_page, score=best_score)
    return AnchorResult(status="failed", page=best_page, score=best_score)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_anchor.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/srx/anchor.py tests/test_anchor.py
git commit -m "feat: quote anchor verification with fuzzy page-window matching

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Claude API client (forced tool use, retry, pricing)

**Files:**
- Create: `src/srx/claude_client.py`
- Test: `tests/test_claude_client.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `srx.claude_client.Usage(input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0)` with `.add(other: Usage) -> None` (in-place accumulate).
  - `srx.claude_client.ClaudeError(Exception)` — raised when retries are exhausted or the response has no tool_use block.
  - `srx.claude_client.ClaudeClient(client=None, pricing=None, sleep=time.sleep)` — `client` is an `anthropic.Anthropic` (or fake with `.messages.create`); constructed lazily from `ANTHROPIC_API_KEY` when omitted.
    - `.tool_call(*, model: str, system: str, messages: list[dict], tool_name: str, tool_schema: dict, max_tokens: int = 8192, max_retries: int = 4) -> tuple[dict, Usage]` — forced tool choice, `temperature=0`, exponential backoff (2^attempt seconds) on retryable errors (HTTP 429/500/502/503/529, connection/timeout).
  - `srx.claude_client.pdf_document_block(pdf_bytes: bytes) -> dict` — base64 document content block with `cache_control: {"type": "ephemeral"}` (the same PDF is sent for pass A, pass B, and adjudication — caching pays).
  - `srx.claude_client.DEFAULT_PRICING: dict[str, tuple[float, float]]` — USD per **million** tokens (input, output).

- [ ] **Step 1: Write the failing test**

`tests/test_claude_client.py`:

```python
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from srx.claude_client import ClaudeClient, ClaudeError, Usage, pdf_document_block

TOOL_SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}}


def fake_response(tool_input, in_tok=1000, out_tok=200):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="record", input=tool_input)],
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


class FakeSDK:
    def __init__(self, results):
        self.results = list(results)  # values or exceptions, popped in order
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def rate_limit_error():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=req), body=None
    )


def test_tool_call_returns_input_and_costed_usage():
    sdk = FakeSDK([fake_response({"x": 5})])
    c = ClaudeClient(client=sdk, pricing={"m1": (10.0, 50.0)})
    data, usage = c.tool_call(
        model="m1", system="s", messages=[{"role": "user", "content": "hi"}],
        tool_name="record", tool_schema=TOOL_SCHEMA,
    )
    assert data == {"x": 5}
    assert usage.input_tokens == 1000
    assert usage.cost_usd == pytest.approx(1000 / 1e6 * 10.0 + 200 / 1e6 * 50.0)
    call = sdk.calls[0]
    assert call["temperature"] == 0
    assert call["tool_choice"] == {"type": "tool", "name": "record"}
    assert call["tools"][0]["input_schema"] == TOOL_SCHEMA


def test_retries_then_succeeds():
    sdk = FakeSDK([rate_limit_error(), rate_limit_error(), fake_response({"x": 1})])
    slept = []
    c = ClaudeClient(client=sdk, pricing={}, sleep=slept.append)
    data, usage = c.tool_call(
        model="m?", system="s", messages=[{"role": "user", "content": "hi"}],
        tool_name="record", tool_schema=TOOL_SCHEMA,
    )
    assert data == {"x": 1}
    assert slept == [1, 2]          # 2**0, 2**1
    assert usage.cost_usd == 0.0    # unknown model prices at 0 (warned)


def test_retries_exhausted_raises():
    sdk = FakeSDK([rate_limit_error()] * 4)
    c = ClaudeClient(client=sdk, pricing={}, sleep=lambda s: None)
    with pytest.raises(ClaudeError, match="retries"):
        c.tool_call(
            model="m", system="s", messages=[{"role": "user", "content": "hi"}],
            tool_name="record", tool_schema=TOOL_SCHEMA, max_retries=4,
        )


def test_no_tool_use_block_raises():
    bad = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="chatter")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    c = ClaudeClient(client=FakeSDK([bad]), pricing={})
    with pytest.raises(ClaudeError, match="tool_use"):
        c.tool_call(
            model="m", system="s", messages=[{"role": "user", "content": "hi"}],
            tool_name="record", tool_schema=TOOL_SCHEMA,
        )


def test_usage_add_and_pdf_block():
    u = Usage(1, 2, 0.5)
    u.add(Usage(10, 20, 1.5))
    assert (u.input_tokens, u.output_tokens, u.cost_usd) == (11, 22, 2.0)
    block = pdf_document_block(b"%PDF-fake")
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"
    assert block["cache_control"] == {"type": "ephemeral"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_claude_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.claude_client'`

- [ ] **Step 3: Implement `src/srx/claude_client.py`**

```python
"""Anthropic SDK wrapper (spec §5, §8): forced tool use for structured output,
temperature 0 everywhere, exponential-backoff retry, per-call cost accounting.
Tests inject a fake SDK; nothing here may be network-hit in tests."""
from __future__ import annotations

import base64
import time
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Optional

import anthropic

# USD per million tokens (input, output). Verify against
# https://docs.anthropic.com/en/docs/about-claude/pricing and edit as needed;
# projects can override via config.yaml "pricing:".
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

RETRY_STATUSES = {429, 500, 502, 503, 529}


class ClaudeError(Exception):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd


def pdf_document_block(pdf_bytes: bytes) -> dict:
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
        },
        "cache_control": {"type": "ephemeral"},
    }


class ClaudeClient:
    def __init__(
        self,
        client: Optional[Any] = None,
        pricing: Optional[dict[str, tuple[float, float]]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = client if client is not None else anthropic.Anthropic()
        self._pricing = DEFAULT_PRICING if pricing is None else pricing
        self._sleep = sleep

    def _cost(self, model: str, in_tok: int, out_tok: int) -> float:
        if model not in self._pricing:
            warnings.warn(f"no pricing for model '{model}'; costing 0.0 — edit pricing config")
            return 0.0
        in_price, out_price = self._pricing[model]
        return in_tok / 1e6 * in_price + out_tok / 1e6 * out_price

    def tool_call(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tool_name: str,
        tool_schema: dict,
        max_tokens: int = 8192,
        max_retries: int = 4,
    ) -> tuple[dict, Usage]:
        last_err: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                resp = self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=0,
                    system=system,
                    messages=messages,
                    tools=[{
                        "name": tool_name,
                        "description": "Record the structured result.",
                        "input_schema": tool_schema,
                    }],
                    tool_choice={"type": "tool", "name": tool_name},
                )
            except anthropic.APIStatusError as e:
                if e.status_code not in RETRY_STATUSES:
                    raise ClaudeError(f"API error {e.status_code}: {e}") from e
                last_err = e
                self._sleep(2 ** attempt)
                continue
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                last_err = e
                self._sleep(2 ** attempt)
                continue
            usage = Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
            )
            usage.cost_usd = self._cost(model, usage.input_tokens, usage.output_tokens)
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    return dict(block.input), usage
            raise ClaudeError(f"model '{model}' returned no tool_use block")
        raise ClaudeError(f"retries exhausted after {max_retries} attempts: {last_err}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_claude_client.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/srx/claude_client.py tests/test_claude_client.py
git commit -m "feat: Claude client with forced tool use, retry/backoff, cost accounting

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Dual-pass extractor

**Files:**
- Create: `src/srx/extractor.py`
- Test: `tests/test_extractor.py`

**Interfaces:**
- Consumes: `srx.schema.Schema/FieldSpec`, `srx.models.Candidate`, `srx.claude_client.ClaudeClient.tool_call`, `srx.claude_client.pdf_document_block`, `srx.claude_client.Usage`.
- Produces:
  - `srx.extractor.PROMPT_VERSION: str` (= `"p1"`; bump whenever prompt text changes — it is recorded per run for regression diffing).
  - `srx.extractor.build_tool_schema(schema: Schema) -> dict` — JSON schema forcing one entry per field: `{value (typed, nullable), unit (string|null), quote (string), page (integer ≥1), confidence (0..1)}`; top-level `required` lists every field id, so the model must explicitly return `value: null` for not-reported fields.
  - `srx.extractor.run_pass(client, model: str, pdf_bytes: bytes, study_id: str, schema: Schema, framing: tuple) -> tuple[dict[str, Candidate], Usage]` — fields with `value: null` (or malformed entries) are absent from the returned dict.
  - `srx.extractor.FRAMINGS: list[tuple[str, str, callable]]` — exactly two: `("pass-a", <instruction>, identity order)` and `("pass-b", <instruction>, reversed order)`.
  - `srx.extractor.extract_study(client, model: str, pdf_bytes: bytes, study_id: str, schema: Schema) -> tuple[dict[str, Candidate], dict[str, Candidate], Usage]` — (pass_a, pass_b, summed usage).
  - The user prompt always contains the line `Study file: {study_id}` (traceability; also lets test fakes route responses).

- [ ] **Step 1: Write the failing test**

`tests/test_extractor.py`:

```python
from srx.claude_client import Usage
from srx.extractor import FRAMINGS, build_tool_schema, extract_study, run_pass
from srx.schema import FieldSpec, Schema

SCHEMA = Schema(fields=[
    FieldSpec(id="n_total", type="int", description="Total randomised"),
    FieldSpec(id="blinding", type="categorical",
              allowed_values=["open-label", "double-blind"]),
    FieldSpec(id="mean_age", type="numeric", unit="years"),
])

ENTRY = {"unit": None, "quote": "124 patients were randomised", "page": 2, "confidence": 0.95}


class FakeToolClient:
    """Duck-types ClaudeClient.tool_call. Returns canned tool input, records prompts."""

    def __init__(self, results):
        self.results = list(results)
        self.prompts = []

    def tool_call(self, *, model, system, messages, tool_name, tool_schema, **kw):
        text = " ".join(
            b["text"] for b in messages[0]["content"] if b.get("type") == "text"
        )
        self.prompts.append(text)
        return self.results.pop(0), Usage(1000, 100, 0.01)


def test_build_tool_schema_shapes():
    ts = build_tool_schema(SCHEMA)
    assert set(ts["required"]) == {"n_total", "blinding", "mean_age"}
    n = ts["properties"]["n_total"]
    assert n["properties"]["value"]["type"] == ["integer", "null"]
    assert set(n["required"]) == {"value", "quote", "page", "confidence"}
    assert "open-label" in ts["properties"]["blinding"]["description"]


def test_run_pass_parses_candidates_and_skips_nulls():
    result = {
        "n_total": {"value": 124, **ENTRY},
        "blinding": {"value": None, "unit": None, "quote": "", "page": 1, "confidence": 0.0},
        "mean_age": {"value": 61.2, "unit": "years", "quote": "mean age 61.2 years",
                     "page": 4, "confidence": 0.9},
    }
    client = FakeToolClient([result])
    cands, usage = run_pass(client, "m", b"%PDF", "smith-2019", SCHEMA, FRAMINGS[0])
    assert cands["n_total"].value == 124
    assert cands["n_total"].page == 2
    assert "blinding" not in cands
    assert cands["mean_age"].unit == "years"
    assert usage.input_tokens == 1000
    assert "Study file: smith-2019" in client.prompts[0]


def test_extract_study_runs_two_passes_with_different_framings():
    result = {"n_total": {"value": 124, **ENTRY}}
    client = FakeToolClient([result, result])
    a, b, usage = extract_study(client, "m", b"%PDF", "smith-2019", SCHEMA)
    assert a["n_total"].value == b["n_total"].value == 124
    assert usage.input_tokens == 2000
    assert client.prompts[0] != client.prompts[1]  # framings differ
    # pass-b lists fields in reversed order
    assert client.prompts[1].index("mean_age") < client.prompts[1].index("n_total")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.extractor'`

- [ ] **Step 3: Implement `src/srx/extractor.py`**

```python
"""Dual-pass extraction (spec §6.3): two independent, differently-framed passes
per study — the machine analog of dual human extraction. Every value must come
back with a verbatim quote and page; nulls mean 'not reported'."""
from __future__ import annotations

from srx.claude_client import Usage, pdf_document_block
from srx.models import Candidate
from srx.schema import FieldSpec, Schema

PROMPT_VERSION = "p1"

SYSTEM_EXTRACT = (
    "You are a meticulous data extractor working on a systematic review. "
    "Extract ONLY values explicitly reported in the attached study PDF. "
    "For every field: report the value in the units requested when stated, the "
    "VERBATIM quote (copied exactly, 5-30 words) that contains the value, the "
    "1-based page number of that quote, and your confidence from 0 to 1. "
    "If a value is not reported in the document, return null for that field's "
    "value — never guess, infer, or compute values that are not stated."
)

FRAMINGS = [
    (
        "pass-a",
        "Work through the fields in the order given, reading the full document "
        "before answering. Prefer values from the results section and tables.",
        lambda fields: list(fields),
    ),
    (
        "pass-b",
        "This is an independent re-extraction for quality control. Re-read the "
        "document from scratch and extract each field on its own merits. Be "
        "skeptical: only report values you can point to verbatim in the text.",
        lambda fields: list(reversed(fields)),
    ),
]


def _value_json_type(spec: FieldSpec) -> list[str]:
    return {
        "numeric": ["number", "null"],
        "int": ["integer", "null"],
        "bool": ["boolean", "null"],
    }.get(spec.type, ["string", "null"])


def _field_description(spec: FieldSpec) -> str:
    parts = [spec.description or spec.id]
    if spec.unit:
        parts.append(f"Report in: {spec.unit} (accepted: {', '.join(spec.accepted_units) or spec.unit})")
    if spec.allowed_values:
        parts.append("Allowed values: " + ", ".join(spec.allowed_values))
    return " | ".join(parts)


def build_tool_schema(schema: Schema) -> dict:
    props = {}
    for spec in schema.fields:
        props[spec.id] = {
            "type": "object",
            "description": _field_description(spec),
            "properties": {
                "value": {"type": _value_json_type(spec)},
                "unit": {"type": ["string", "null"]},
                "quote": {"type": "string"},
                "page": {"type": "integer", "minimum": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["value", "quote", "page", "confidence"],
        }
    return {"type": "object", "properties": props, "required": list(props)}


def _field_lines(schema: Schema, order) -> str:
    lines = []
    for spec in order(schema.fields):
        lines.append(f"- {spec.id} ({spec.type}): {_field_description(spec)}")
    return "\n".join(lines)


def run_pass(
    client, model: str, pdf_bytes: bytes, study_id: str, schema: Schema, framing
) -> tuple[dict[str, Candidate], Usage]:
    name, instruction, order = framing
    user_text = (
        f"Study file: {study_id}\n\n{instruction}\n\n"
        f"Extract these fields:\n{_field_lines(schema, order)}\n\n"
        "Record every field with the extraction tool; use value null when not reported."
    )
    messages = [{
        "role": "user",
        "content": [pdf_document_block(pdf_bytes), {"type": "text", "text": user_text}],
    }]
    data, usage = client.tool_call(
        model=model, system=SYSTEM_EXTRACT, messages=messages,
        tool_name="record_extraction", tool_schema=build_tool_schema(schema),
    )
    candidates: dict[str, Candidate] = {}
    for spec in schema.fields:
        entry = data.get(spec.id)
        if not isinstance(entry, dict) or entry.get("value") is None:
            continue
        if not entry.get("quote") or not isinstance(entry.get("page"), int):
            continue  # malformed: treat as absent rather than accept unanchored
        candidates[spec.id] = Candidate(
            value=entry["value"],
            unit=entry.get("unit"),
            quote=entry["quote"],
            page=entry["page"],
            confidence=float(entry.get("confidence", 0.0)),
        )
    return candidates, usage


def extract_study(
    client, model: str, pdf_bytes: bytes, study_id: str, schema: Schema
) -> tuple[dict[str, Candidate], dict[str, Candidate], Usage]:
    total = Usage()
    pass_a, u1 = run_pass(client, model, pdf_bytes, study_id, schema, FRAMINGS[0])
    total.add(u1)
    pass_b, u2 = run_pass(client, model, pdf_bytes, study_id, schema, FRAMINGS[1])
    total.add(u2)
    return pass_a, pass_b, total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_extractor.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/srx/extractor.py tests/test_extractor.py
git commit -m "feat: dual-pass schema-driven extraction with independent framings

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Reconciler (agree / adjudicate / flag)

**Files:**
- Create: `src/srx/reconcile.py`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Consumes: `srx.models.*`, `srx.schema.Schema/FieldSpec`, `srx.normalize.values_equal`, `srx.anchor.find_anchor`, `srx.ingest.PdfDoc`, `client.tool_call`, `srx.claude_client.Usage`.
- Produces:
  - `srx.reconcile.reconcile_study(*, schema: Schema, pdf: PdfDoc, pass_a: dict[str, Candidate], pass_b: dict[str, Candidate], client, model: str) -> tuple[dict[str, ExtractedValue], dict[str, FlaggedField], list[str], Usage]` — `(accepted, flagged, not_found, adjudication_usage)`.
  - Behavior contract (spec §6.5): both passes absent → `not_found`. Both present and `values_equal` → accept as `pass-agreed` with `confidence = min(a, b)` — subject to the anchor gate (try the higher-confidence candidate's quote first, fall back to the other; both fail → flagged `"anchor-failed"`; scanned → flagged `"vision-only"`). Any disagreement (including one-sided null) → one adjudication `tool_call` (tool `adjudicate`, schema `{choice: "a"|"b"|"neither"|"not-reported", rationale: str}`) with both candidates and page excerpts; `a`/`b` → accept that candidate as `adjudicated` (anchor gate still applies); `not-reported` → `not_found`; `neither` → flagged `"pass-disagreement-unresolved"`. Disagreements are never averaged.

- [ ] **Step 1: Write the failing test**

`tests/test_reconcile.py`:

```python
from pathlib import Path

from srx.claude_client import Usage
from srx.ingest import PdfDoc
from srx.models import Candidate
from srx.reconcile import reconcile_study
from srx.schema import FieldSpec, Schema

SCHEMA = Schema(fields=[
    FieldSpec(id="n_total", type="int"),
    FieldSpec(id="mean_age", type="numeric", unit="years"),
])

PAGES = [
    "Methods section text here.",
    "A total of 124 patients were randomised. Mean age was 61.2 years overall.",
]
PDF = PdfDoc(study_id="smith-2019", path=Path("smith-2019.pdf"), pages=PAGES, scanned=False)


def cand(value, quote, page=2, unit=None, conf=0.9):
    return Candidate(value=value, unit=unit, quote=quote, page=page, confidence=conf)


class FakeAdjudicator:
    def __init__(self, choice):
        self.choice = choice
        self.calls = 0

    def tool_call(self, **kwargs):
        self.calls += 1
        return {"choice": self.choice, "rationale": "because"}, Usage(500, 50, 0.005)


def test_agreement_accepts_with_verified_anchor():
    a = {"n_total": cand(124, "124 patients were randomised", conf=0.95)}
    b = {"n_total": cand(124, "total of 124 patients", conf=0.90)}
    adj = FakeAdjudicator("a")
    accepted, flagged, not_found, usage = reconcile_study(
        schema=SCHEMA, pdf=PDF, pass_a=a, pass_b=b, client=adj, model="m")
    ev = accepted["n_total"]
    assert ev.method == "pass-agreed"
    assert ev.anchor_status == "verified"
    assert ev.confidence == 0.90            # min of the two passes
    assert adj.calls == 0                   # no adjudication needed
    assert "mean_age" in not_found
    assert usage.cost_usd == 0.0


def test_agreement_with_fabricated_quotes_is_flagged_never_accepted():
    a = {"n_total": cand(124, "we enrolled one hundred and forty patients")}
    b = {"n_total": cand(124, "patients were recruited from twelve sites")}
    accepted, flagged, _, _ = reconcile_study(
        schema=SCHEMA, pdf=PDF, pass_a=a, pass_b=b, client=FakeAdjudicator("a"), model="m")
    assert "n_total" not in accepted
    assert flagged["n_total"].reason == "anchor-failed"
    assert len(flagged["n_total"].candidates) == 2


def test_disagreement_adjudicated_to_b():
    a = {"mean_age": cand(16.2, "mean age was 61.2 years", unit="years")}
    b = {"mean_age": cand(61.2, "Mean age was 61.2 years", unit="years")}
    adj = FakeAdjudicator("b")
    accepted, flagged, _, usage = reconcile_study(
        schema=SCHEMA, pdf=PDF, pass_a=a, pass_b=b, client=adj, model="m")
    assert adj.calls == 1
    assert accepted["mean_age"].value == 61.2
    assert accepted["mean_age"].method == "adjudicated"
    assert usage.cost_usd > 0


def test_disagreement_neither_flags():
    a = {"mean_age": cand(59.0, "mean age was 61.2 years")}
    b = {"mean_age": cand(61.2, "Mean age was 61.2 years")}
    accepted, flagged, _, _ = reconcile_study(
        schema=SCHEMA, pdf=PDF, pass_a=a, pass_b=b, client=FakeAdjudicator("neither"), model="m")
    assert "mean_age" not in accepted
    assert flagged["mean_age"].reason == "pass-disagreement-unresolved"


def test_one_sided_null_adjudicated_not_reported():
    a = {"mean_age": cand(61.2, "Mean age was 61.2 years")}
    accepted, flagged, not_found, _ = reconcile_study(
        schema=SCHEMA, pdf=PDF, pass_a=a, pass_b={}, client=FakeAdjudicator("not-reported"), model="m")
    assert "mean_age" in not_found
    assert "mean_age" not in accepted and "mean_age" not in flagged


def test_scanned_pdf_flags_everything_vision_only():
    scanned = PdfDoc(study_id="old", path=Path("old.pdf"), pages=["", ""], scanned=True)
    a = {"n_total": cand(124, "124 patients", page=1)}
    b = {"n_total": cand(124, "124 patients", page=1)}
    accepted, flagged, _, _ = reconcile_study(
        schema=SCHEMA, pdf=scanned, pass_a=a, pass_b=b, client=FakeAdjudicator("a"), model="m")
    assert accepted == {}
    assert flagged["n_total"].reason == "vision-only"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.reconcile'`

- [ ] **Step 3: Implement `src/srx/reconcile.py`**

```python
"""Reconciliation (spec §6.5): agree -> accept; disagree -> adjudication pass;
unresolved -> human-flag queue. Values are never averaged, and the anchor gate
(spec §6.4) applies to every acceptance path."""
from __future__ import annotations

from srx.anchor import find_anchor
from srx.claude_client import Usage
from srx.ingest import PdfDoc
from srx.models import Candidate, ExtractedValue, FlaggedField
from srx.normalize import values_equal
from srx.schema import FieldSpec, Schema

ADJ_SYSTEM = (
    "You adjudicate between two independent data extractions from a study in a "
    "systematic review. Choose 'a' or 'b' only when the document excerpts "
    "clearly support that candidate. Choose 'not-reported' when the value is "
    "not actually stated in the document. Choose 'neither' when you cannot "
    "resolve the disagreement from the excerpts."
)

ADJ_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"type": "string", "enum": ["a", "b", "neither", "not-reported"]},
        "rationale": {"type": "string"},
    },
    "required": ["choice", "rationale"],
}


def _excerpt(pdf: PdfDoc, page: int, radius: int = 0, limit: int = 1500) -> str:
    texts = []
    for p in range(max(1, page - radius), min(len(pdf.pages), page + radius) + 1):
        texts.append(f"[page {p}] {pdf.pages[p - 1][:limit]}")
    return "\n".join(texts)


def _describe(label: str, cand: Candidate | None) -> str:
    if cand is None:
        return f"Candidate {label}: NOT REPORTED (this pass found no value)"
    return (
        f"Candidate {label}: value={cand.value!r} unit={cand.unit!r} "
        f"(claimed page {cand.page}, quote: \"{cand.quote}\")"
    )


def _gate(spec: FieldSpec, primary: Candidate, fallback: Candidate | None,
          method: str, confidence: float, pdf: PdfDoc):
    """Apply the anchor gate. Returns ExtractedValue or FlaggedField."""
    candidates = [primary] + ([fallback] if fallback else [])
    for cand in candidates:
        anchor = find_anchor(cand.quote, pdf.pages, cand.page, pdf.scanned)
        if anchor.status == "verified":
            return ExtractedValue(
                field_id=spec.id, value=primary.value, unit=primary.unit,
                quote=cand.quote, page=anchor.page, method=method,
                anchor_status="verified", confidence=confidence,
            )
        if anchor.status == "vision-only":
            return FlaggedField(field_id=spec.id, reason="vision-only", candidates=candidates)
    return FlaggedField(field_id=spec.id, reason="anchor-failed", candidates=candidates)


def reconcile_study(
    *, schema: Schema, pdf: PdfDoc,
    pass_a: dict[str, Candidate], pass_b: dict[str, Candidate],
    client, model: str,
) -> tuple[dict[str, ExtractedValue], dict[str, FlaggedField], list[str], Usage]:
    accepted: dict[str, ExtractedValue] = {}
    flagged: dict[str, FlaggedField] = {}
    not_found: list[str] = []
    usage = Usage()

    for spec in schema.fields:
        a, b = pass_a.get(spec.id), pass_b.get(spec.id)

        if a is None and b is None:
            not_found.append(spec.id)
            continue

        if a is not None and b is not None and values_equal(
            a.value, a.unit, b.value, b.unit, spec
        ):
            primary, fallback = (a, b) if a.confidence >= b.confidence else (b, a)
            result = _gate(spec, primary, fallback, "pass-agreed",
                           min(a.confidence, b.confidence), pdf)
        else:
            # Disagreement (including one-sided null): adjudicate.
            pages = sorted({c.page for c in (a, b) if c is not None})
            excerpts = "\n\n".join(_excerpt(pdf, p) for p in pages)
            user_text = (
                f"Study file: {pdf.study_id}\n"
                f"Field: {spec.id} ({spec.type}) — {spec.description or spec.id}\n\n"
                f"{_describe('a', a)}\n{_describe('b', b)}\n\n"
                f"Document excerpts:\n{excerpts if excerpts else '(no text layer available)'}"
            )
            data, adj_usage = client.tool_call(
                model=model, system=ADJ_SYSTEM,
                messages=[{"role": "user", "content": user_text}],
                tool_name="adjudicate", tool_schema=ADJ_TOOL_SCHEMA,
            )
            usage.add(adj_usage)
            choice = data.get("choice")
            chosen = {"a": a, "b": b}.get(choice)
            if choice == "not-reported" or (choice in ("a", "b") and chosen is None):
                not_found.append(spec.id)
                continue
            if chosen is None:  # "neither" or malformed
                flagged[spec.id] = FlaggedField(
                    field_id=spec.id, reason="pass-disagreement-unresolved",
                    candidates=[c for c in (a, b) if c is not None],
                )
                continue
            result = _gate(spec, chosen, None, "adjudicated", chosen.confidence, pdf)

        if isinstance(result, ExtractedValue):
            accepted[spec.id] = result
        else:
            flagged[spec.id] = result

    return accepted, flagged, not_found, usage
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_reconcile.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the full suite (regression)**

Run: `~/.venvs/srx/bin/pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/srx/reconcile.py tests/test_reconcile.py
git commit -m "feat: pass reconciliation with adjudication and anchor gating

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: SQLite run store

**Files:**
- Create: `src/srx/runstore.py`
- Test: `tests/test_runstore.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `srx.runstore.new_run_id() -> str` — `YYYYMMDD-HHMMSS-<4 hex>` (sortable prefix, unique suffix).
  - `srx.runstore.RunStore(db_path: pathlib.Path)` — creates tables on open; `sqlite3.Row` rows; `.close()`.
    - `.create_run(run_id: str, schema_hash: str, models: dict, prompt_version: str) -> None` (status `"running"`; raises `sqlite3.IntegrityError` on duplicate id)
    - `.finish_run(run_id: str, status: str, cost_usd: float, summary: dict) -> None` — status ∈ `{"done","cost-capped","errored"}`
    - `.record_study(run_id: str, study_id: str, status: str, cost_usd: float, json_path: str) -> None` (INSERT OR REPLACE; status ∈ `{"done","errored"}`)
    - `.study_status(run_id: str, study_id: str) -> str|None`
    - `.record_bench(run_id: str, rows: dict[str, dict]) -> None` — per field id: `{"n": int, "agreement": float|None, "taxonomy": dict[str,int]}` (INSERT OR REPLACE)
    - `.bench_rows(run_id: str) -> dict[str, sqlite3.Row]` (taxonomy JSON in column `taxonomy_json`)
    - `.run_row(run_id: str) -> sqlite3.Row|None`
    - `.latest_run() -> sqlite3.Row|None` (by `created_at`)
    - `.previous_benched_run(before_run_id: str) -> str|None` — most recent earlier run having bench rows (for report diffs)
  - Rows are never deleted (append-only store, spec §6.7); `finish_run`/re-`record_bench` update in place, which is the only mutation.

- [ ] **Step 1: Write the failing test**

`tests/test_runstore.py`:

```python
import json

import pytest

from srx.runstore import RunStore, new_run_id


def test_new_run_id_format_and_uniqueness():
    a, b = new_run_id(), new_run_id()
    assert len(a.split("-")) == 3
    assert a != b


def test_run_lifecycle(tmp_path):
    store = RunStore(tmp_path / "sr.sqlite")
    store.create_run("r1", schema_hash="abc", models={"extract": "m1"}, prompt_version="p1")
    with pytest.raises(Exception):
        store.create_run("r1", schema_hash="abc", models={}, prompt_version="p1")
    store.record_study("r1", "smith-2019", "done", 0.42, "runs/r1/studies/smith-2019.json")
    store.record_study("r1", "jones-2020", "errored", 0.10, "")
    assert store.study_status("r1", "smith-2019") == "done"
    assert store.study_status("r1", "nope") is None
    store.finish_run("r1", "done", 0.52, {"studies": 2})
    row = store.run_row("r1")
    assert row["status"] == "done"
    assert row["cost_usd"] == pytest.approx(0.52)
    assert json.loads(row["models_json"]) == {"extract": "m1"}
    assert store.latest_run()["run_id"] == "r1"
    store.close()


def test_bench_rows_and_previous_benched(tmp_path):
    store = RunStore(tmp_path / "sr.sqlite")
    store.create_run("r1", "h", {}, "p1")
    store.finish_run("r1", "done", 0.0, {})
    store.create_run("r2", "h", {}, "p1")
    store.finish_run("r2", "done", 0.0, {})
    store.record_bench("r1", {"n_total": {"n": 10, "agreement": 0.9, "taxonomy": {"correct": 9}}})
    store.record_bench("r2", {"n_total": {"n": 10, "agreement": 1.0, "taxonomy": {"correct": 10}}})
    rows = store.bench_rows("r1")
    assert rows["n_total"]["agreement"] == pytest.approx(0.9)
    assert json.loads(rows["n_total"]["taxonomy_json"]) == {"correct": 9}
    assert store.previous_benched_run("r2") == "r1"
    assert store.previous_benched_run("r1") is None
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_runstore.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.runstore'`

- [ ] **Step 3: Implement `src/srx/runstore.py`**

```python
"""Append-only run registry (spec §6.7). Every run records inputs hash, models,
prompt version, cost, and benchmark scores so any prompt/model change can be
diffed run-over-run. Rows are never deleted."""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  schema_hash TEXT NOT NULL,
  models_json TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0,
  summary_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS study_runs (
  run_id TEXT NOT NULL,
  study_id TEXT NOT NULL,
  status TEXT NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0,
  json_path TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (run_id, study_id)
);
CREATE TABLE IF NOT EXISTS bench_scores (
  run_id TEXT NOT NULL,
  field_id TEXT NOT NULL,
  n INTEGER NOT NULL,
  agreement REAL,
  taxonomy_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (run_id, field_id)
);
"""


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class RunStore:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def create_run(self, run_id: str, schema_hash: str, models: dict, prompt_version: str) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, created_at, status, schema_hash, models_json, prompt_version)"
            " VALUES (?, ?, 'running', ?, ?, ?)",
            (run_id, datetime.now(timezone.utc).isoformat(), schema_hash,
             json.dumps(models), prompt_version),
        )
        self._conn.commit()

    def finish_run(self, run_id: str, status: str, cost_usd: float, summary: dict) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, cost_usd = ?, summary_json = ? WHERE run_id = ?",
            (status, cost_usd, json.dumps(summary), run_id),
        )
        self._conn.commit()

    def record_study(self, run_id: str, study_id: str, status: str,
                     cost_usd: float, json_path: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO study_runs (run_id, study_id, status, cost_usd, json_path)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, study_id, status, cost_usd, json_path),
        )
        self._conn.commit()

    def study_status(self, run_id: str, study_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT status FROM study_runs WHERE run_id = ? AND study_id = ?",
            (run_id, study_id),
        ).fetchone()
        return row["status"] if row else None

    def record_bench(self, run_id: str, rows: dict[str, dict]) -> None:
        for field_id, r in rows.items():
            self._conn.execute(
                "INSERT OR REPLACE INTO bench_scores (run_id, field_id, n, agreement, taxonomy_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, field_id, r["n"], r["agreement"], json.dumps(r["taxonomy"])),
            )
        self._conn.commit()

    def bench_rows(self, run_id: str) -> dict[str, sqlite3.Row]:
        rows = self._conn.execute(
            "SELECT * FROM bench_scores WHERE run_id = ?", (run_id,)
        ).fetchall()
        return {r["field_id"]: r for r in rows}

    def run_row(self, run_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def latest_run(self) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    def previous_benched_run(self, before_run_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT r.run_id FROM runs r"
            " WHERE r.created_at < (SELECT created_at FROM runs WHERE run_id = ?)"
            "   AND EXISTS (SELECT 1 FROM bench_scores b WHERE b.run_id = r.run_id)"
            " ORDER BY r.created_at DESC LIMIT 1",
            (before_run_id,),
        ).fetchone()
        return row["run_id"] if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_runstore.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/srx/runstore.py tests/test_runstore.py
git commit -m "feat: append-only SQLite run registry with bench score diffing

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Gold-standard loader

**Files:**
- Create: `src/srx/goldload.py`
- Test: `tests/test_goldload.py`

**Interfaces:**
- Consumes: `srx.schema.Schema/FieldSpec`, `srx.normalize.parse_number/decimal_places`.
- Produces:
  - `srx.goldload.GoldValue(raw: str, value: Any, unit: str|None, precision: int|None)` — `value` is `float` for numeric/int fields, stripped `str` otherwise; `precision` = decimals in the raw cell for numeric fields, else `None`; `unit` = mapping override or the spec's canonical unit.
  - `srx.goldload.GoldError(Exception)`.
  - `srx.goldload.load_gold(gold_csv: Path, mapping_yaml: Path, schema: Schema) -> dict[str, dict[str, GoldValue]]` — outer key study_id, inner key field_id. Empty cells → absent. Raises `GoldError` on: missing study-id column, duplicate study ids, mapping to unknown field ids, unparseable numeric cells (message names study, column, and cell). CSV columns not in the mapping are ignored with a `warnings.warn`.
  - `mapping.yaml` format:
    ```yaml
    study_id_column: study_id
    columns:
      "N randomised": n_total
      "Mean age (I)": {field: mean_age, unit: years}
    ```

- [ ] **Step 1: Write the failing test**

`tests/test_goldload.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_goldload.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.goldload'`

- [ ] **Step 3: Implement `src/srx/goldload.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_goldload.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/srx/goldload.py tests/test_goldload.py
git commit -m "feat: gold-standard CSV loader with column mapping and validation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Benchmark scorer + error taxonomy

**Files:**
- Create: `src/srx/bench.py`
- Test: `tests/test_bench.py`

**Interfaces:**
- Consumes: `srx.models.StudyExtraction`, `srx.schema.Schema/FieldSpec`, `srx.goldload.GoldValue`, `srx.normalize.values_equal/canonical_category`, `srx.anchor.normalize_text`, `client.tool_call` (judge only).
- Produces:
  - `srx.bench.TAXONOMY = ("correct","wrong-value","unit-mismatch","missed","spurious-hallucinated","spurious-anchored","gold-suspect")` (spec §7).
  - `srx.bench.FieldScore(study_id: str, field_id: str, taxonomy: str, engine_display: str, gold_display: str, flagged: bool, in_denominator: bool, provenance: dict|None)` with `.to_dict()`/`.from_dict()`.
  - `srx.bench.score_all(extractions: list[StudyExtraction], gold: dict[str, dict[str, GoldValue]], schema: Schema, judge) -> list[FieldScore]` — `judge` is `Callable[[FieldSpec, str, str], bool]`, used only for `text` fields.
  - `srx.bench.exact_judge(spec, engine_str, gold_str) -> bool` — canonical-equality fallback (`--no-judge` mode).
  - `srx.bench.make_judge(client, model: str)` — returns a judge backed by a `tool_call` (tool `judge_equivalence`, schema `{equivalent: bool, rationale: str}`).
  - `srx.bench.BenchSummary(overall_agreement: float|None, numeric_agreement: float|None, per_field: dict[str, dict], taxonomy_counts: dict[str, int], n_scored: int, n_studies: int)` with `.to_dict()`/`.from_dict()`; `per_field[fid] = {"n": int, "agreement": float|None, "taxonomy": dict[str, int]}` (exactly the shape `RunStore.record_bench` takes).
  - `srx.bench.aggregate(scores: list[FieldScore], schema: Schema) -> BenchSummary`.
  - Classification contract (spec §7): gold present + accepted → `correct` when equal (`convert_units=False`, using `gold_precision`); else `unit-mismatch` when equal only with `convert_units=True`; else `gold-suspect` when the engine value's digits appear verbatim in the anchored quote, otherwise `wrong-value`. Gold present + flagged → `missed` with `flagged=True`. Gold present + absent → `missed`. Gold absent + accepted → `spurious-anchored`. Gold absent + flagged `anchor-failed` → `spurious-hallucinated` with `in_denominator=False` (informational). Gold absent otherwise → no score row. Agreement = correct / denominator rows.

- [ ] **Step 1: Write the failing test**

`tests/test_bench.py`:

```python
import pytest

from srx.bench import BenchSummary, aggregate, exact_judge, score_all
from srx.goldload import GoldValue
from srx.models import Candidate, ExtractedValue, FlaggedField, StudyExtraction
from srx.schema import FieldSpec, Schema

SCHEMA = Schema(fields=[
    FieldSpec(id="n_total", type="int"),
    FieldSpec(id="dose", type="numeric", unit="g"),
    FieldSpec(id="mean_age", type="numeric", unit="years"),
    FieldSpec(id="funding", type="text"),
])


def ev(fid, value, unit=None, quote="q", method="pass-agreed"):
    return ExtractedValue(field_id=fid, value=value, unit=unit, quote=quote,
                          page=1, method=method, anchor_status="verified", confidence=0.9)


def gv(raw, value=None, unit=None, precision=None):
    return GoldValue(raw=raw, value=value if value is not None else raw,
                     unit=unit, precision=precision)


def flag(fid, reason):
    return FlaggedField(field_id=fid, reason=reason,
                        candidates=[Candidate(value=1, unit=None, quote="q", page=1, confidence=0.5)])


def study(accepted=None, flagged=None, not_found=None):
    return StudyExtraction(study_id="s1", scanned=False, accepted=accepted or {},
                           flagged=flagged or {}, not_found=not_found or [], cost_usd=0.0)


def by_field(scores):
    return {s.field_id: s for s in scores}


def test_taxonomy_classification():
    extraction = study(
        accepted={
            "n_total": ev("n_total", 124),                                  # correct
            "dose": ev("dose", 1500, unit="mg"),                            # unit-mismatch vs 1.5 g
            "mean_age": ev("mean_age", 61.2, unit="years",
                           quote="mean age was 61.2 years"),                 # gold says 16.2 -> gold-suspect
            "funding": ev("funding", "industry sponsored"),                 # gold absent -> spurious-anchored
        },
    )
    gold = {"s1": {
        "n_total": gv("124", 124.0, precision=0),
        "dose": gv("1.5", 1.5, unit="g", precision=1),
        "mean_age": gv("16.2", 16.2, unit="years", precision=1),
    }}
    s = by_field(score_all([extraction], gold, SCHEMA, exact_judge))
    assert s["n_total"].taxonomy == "correct"
    assert s["dose"].taxonomy == "unit-mismatch"
    assert s["mean_age"].taxonomy == "gold-suspect"      # 61.2 appears in the quote
    assert s["funding"].taxonomy == "spurious-anchored"
    assert all(x.in_denominator for x in s.values())


def test_flagged_scores_as_missed_and_hallucinated_is_informational():
    extraction = study(flagged={
        "n_total": flag("n_total", "pass-disagreement-unresolved"),  # gold present -> missed
        "dose": flag("dose", "anchor-failed"),                       # gold absent -> spurious-hallucinated
    })
    gold = {"s1": {"n_total": gv("124", 124.0, precision=0)}}
    s = by_field(score_all([extraction], gold, SCHEMA, exact_judge))
    assert s["n_total"].taxonomy == "missed" and s["n_total"].flagged
    assert s["dose"].taxonomy == "spurious-hallucinated"
    assert not s["dose"].in_denominator
    assert "mean_age" not in s                            # both null -> no row


def test_wrong_value_when_quote_lacks_engine_digits():
    extraction = study(accepted={"n_total": ev("n_total", 128, quote="patients were randomised")})
    gold = {"s1": {"n_total": gv("124", 124.0, precision=0)}}
    s = by_field(score_all([extraction], gold, SCHEMA, exact_judge))
    assert s["n_total"].taxonomy == "wrong-value"


def test_text_field_uses_judge():
    extraction = study(accepted={"funding": ev("funding", "funded by Pfizer Inc")})
    gold = {"s1": {"funding": gv("Pfizer")}}
    always_yes = lambda spec, e, g: True
    s = by_field(score_all([extraction], gold, SCHEMA, always_yes))
    assert s["funding"].taxonomy == "correct"


def test_aggregate_and_round_trip():
    extraction = study(accepted={"n_total": ev("n_total", 124)},
                       flagged={"mean_age": flag("mean_age", "vision-only")})
    gold = {"s1": {"n_total": gv("124", 124.0, precision=0),
                   "mean_age": gv("61.2", 61.2, precision=1)}}
    scores = score_all([extraction], gold, SCHEMA, exact_judge)
    summary = aggregate(scores, SCHEMA)
    assert summary.overall_agreement == pytest.approx(0.5)   # correct=1 of denominator=2
    assert summary.numeric_agreement == pytest.approx(0.5)
    assert summary.per_field["n_total"]["agreement"] == 1.0
    assert summary.taxonomy_counts["missed"] == 1
    assert BenchSummary.from_dict(summary.to_dict()) == summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_bench.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.bench'`

- [ ] **Step 3: Implement `src/srx/bench.py`**

```python
"""Benchmark scoring (spec §6.6, §7). Only accepted values enter the agreement
denominator; flagged values score as missed — they can never inflate the score.
gold-suspect exists because human gold sheets contain errors too: when the
engine's differing value is visible verbatim in its anchored quote, a human
should adjudicate which side is wrong."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from srx.anchor import normalize_text
from srx.goldload import GoldValue
from srx.models import StudyExtraction
from srx.normalize import canonical_category, parse_number, values_equal
from srx.schema import FieldSpec, Schema

TAXONOMY = (
    "correct", "wrong-value", "unit-mismatch", "missed",
    "spurious-hallucinated", "spurious-anchored", "gold-suspect",
)

Judge = Callable[[FieldSpec, str, str], bool]


def exact_judge(spec: FieldSpec, engine_str: str, gold_str: str) -> bool:
    return canonical_category(engine_str) == canonical_category(gold_str)


JUDGE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "equivalent": {"type": "boolean"},
        "rationale": {"type": "string"},
    },
    "required": ["equivalent", "rationale"],
}


def make_judge(client, model: str) -> Judge:
    def judge(spec: FieldSpec, engine_str: str, gold_str: str) -> bool:
        data, _ = client.tool_call(
            model=model,
            system=(
                "You judge whether two short answers from a systematic review "
                "evidence table are semantically equivalent for the given field. "
                "Different wording of the same fact is equivalent; different "
                "facts are not."
            ),
            messages=[{"role": "user", "content":
                       f"Field: {spec.id} — {spec.description or spec.id}\n"
                       f"Answer 1 (engine): {engine_str}\nAnswer 2 (gold): {gold_str}"}],
            tool_name="judge_equivalence", tool_schema=JUDGE_TOOL_SCHEMA,
        )
        return bool(data.get("equivalent"))
    return judge


@dataclass
class FieldScore:
    study_id: str
    field_id: str
    taxonomy: str
    engine_display: str
    gold_display: str
    flagged: bool
    in_denominator: bool
    provenance: Optional[dict]

    def to_dict(self) -> dict:
        return dict(vars(self))

    @classmethod
    def from_dict(cls, d: dict) -> "FieldScore":
        return cls(**d)


def _digits_in_quote(value: Any, quote: str) -> bool:
    num = parse_number(value)
    if num is None:
        return False
    text = normalize_text(quote)
    as_str = f"{num:g}"
    return as_str in text or (num == int(num) and str(int(num)) in text)


def _display(value: Any, unit: Optional[str]) -> str:
    return f"{value} {unit}".strip() if unit else str(value)


def _classify(spec: FieldSpec, extraction: StudyExtraction,
              gold: Optional[GoldValue], judge: Judge) -> Optional[FieldScore]:
    acc = extraction.accepted.get(spec.id)
    flg = extraction.flagged.get(spec.id)
    base = dict(study_id=extraction.study_id, field_id=spec.id, flagged=False,
                in_denominator=True, provenance=acc.to_dict() if acc else None)

    if gold is not None:
        gold_disp = _display(gold.raw, gold.unit)
        if acc is not None:
            engine_disp = _display(acc.value, acc.unit)
            if spec.type == "text":
                # cheap normalization equality (incl. CI/p-value formats) first;
                # the model judge is only consulted when that fails
                tax = "correct" if (
                    values_equal(acc.value, acc.unit, gold.value, gold.unit, spec)
                    or judge(spec, str(acc.value), str(gold.value))
                ) else "wrong-value"
            elif values_equal(acc.value, acc.unit, gold.value, gold.unit, spec,
                              gold_precision=gold.precision, convert_units=False):
                tax = "correct"
            elif values_equal(acc.value, acc.unit, gold.value, gold.unit, spec,
                              gold_precision=gold.precision, convert_units=True):
                tax = "unit-mismatch"
            elif _digits_in_quote(acc.value, acc.quote):
                tax = "gold-suspect"
            else:
                tax = "wrong-value"
            return FieldScore(**base, taxonomy=tax,
                              engine_display=engine_disp, gold_display=gold_disp)
        if flg is not None:
            return FieldScore(**{**base, "flagged": True}, taxonomy="missed",
                              engine_display=f"FLAGGED ({flg.reason})", gold_display=gold_disp)
        return FieldScore(**base, taxonomy="missed",
                          engine_display="—", gold_display=gold_disp)

    # gold absent
    if acc is not None:
        return FieldScore(**base, taxonomy="spurious-anchored",
                          engine_display=_display(acc.value, acc.unit), gold_display="—")
    if flg is not None and flg.reason == "anchor-failed":
        return FieldScore(**{**base, "flagged": True, "in_denominator": False},
                          taxonomy="spurious-hallucinated",
                          engine_display=f"FLAGGED ({flg.reason})", gold_display="—")
    return None


def score_all(extractions: list[StudyExtraction],
              gold: dict[str, dict[str, GoldValue]],
              schema: Schema, judge: Judge) -> list[FieldScore]:
    scores: list[FieldScore] = []
    for extraction in extractions:
        study_gold = gold.get(extraction.study_id, {})
        for spec in schema.fields:
            score = _classify(spec, extraction, study_gold.get(spec.id), judge)
            if score is not None:
                scores.append(score)
    return scores


@dataclass
class BenchSummary:
    overall_agreement: Optional[float]
    numeric_agreement: Optional[float]
    per_field: dict[str, dict]
    taxonomy_counts: dict[str, int]
    n_scored: int
    n_studies: int

    def to_dict(self) -> dict:
        return dict(vars(self))

    @classmethod
    def from_dict(cls, d: dict) -> "BenchSummary":
        return cls(**d)


def _rate(scores: list[FieldScore]) -> Optional[float]:
    denom = [s for s in scores if s.in_denominator]
    if not denom:
        return None
    return sum(1 for s in denom if s.taxonomy == "correct") / len(denom)


def aggregate(scores: list[FieldScore], schema: Schema) -> BenchSummary:
    numeric_ids = {f.id for f in schema.fields if f.type in ("numeric", "int")}
    per_field: dict[str, dict] = {}
    for spec in schema.fields:
        fs = [s for s in scores if s.field_id == spec.id]
        denom = [s for s in fs if s.in_denominator]
        taxonomy: dict[str, int] = {}
        for s in fs:
            taxonomy[s.taxonomy] = taxonomy.get(s.taxonomy, 0) + 1
        if fs:
            per_field[spec.id] = {"n": len(denom), "agreement": _rate(fs), "taxonomy": taxonomy}
    taxonomy_counts: dict[str, int] = {}
    for s in scores:
        taxonomy_counts[s.taxonomy] = taxonomy_counts.get(s.taxonomy, 0) + 1
    return BenchSummary(
        overall_agreement=_rate(scores),
        numeric_agreement=_rate([s for s in scores if s.field_id in numeric_ids]),
        per_field=per_field,
        taxonomy_counts=taxonomy_counts,
        n_scored=sum(1 for s in scores if s.in_denominator),
        n_studies=len({s.study_id for s in scores}),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_bench.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/srx/bench.py tests/test_bench.py
git commit -m "feat: benchmark scoring with spec error taxonomy and agreement rates

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: HTML/Markdown report with click-to-expand provenance

**Files:**
- Create: `src/srx/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `srx.bench.BenchSummary/FieldScore`.
- Produces:
  - `srx.report.render_html(summary: BenchSummary, scores: list[FieldScore], run_id: str, prev: tuple[str, dict[str, float|None]]|None = None) -> str` — fully self-contained HTML (inline CSS, no external resources, no JS). Cells show clean values only; quote/page/method/confidence/gold live inside `<details>` (spec §4 presentation rule). When `prev = (prev_run_id, {field_id: prev_agreement})` is given, the per-field table shows a Δ column vs that run (spec §6.7 regression diffing). All content passes through `html.escape`.
  - `srx.report.render_markdown(summary: BenchSummary, run_id: str) -> str` — terminal-friendly summary.
  - `srx.report.write_report(run_dir: pathlib.Path, html: str) -> pathlib.Path` — writes `report.html`, returns the path.

- [ ] **Step 1: Write the failing test**

`tests/test_report.py`:

```python
from srx.bench import BenchSummary, FieldScore
from srx.report import render_html, render_markdown, write_report

SUMMARY = BenchSummary(
    overall_agreement=0.75, numeric_agreement=0.8,
    per_field={"n_total": {"n": 4, "agreement": 0.75, "taxonomy": {"correct": 3, "wrong-value": 1}}},
    taxonomy_counts={"correct": 3, "wrong-value": 1}, n_scored=4, n_studies=4,
)

SCORE = FieldScore(
    study_id="smith-2019", field_id="n_total", taxonomy="correct",
    engine_display="124", gold_display="124", flagged=False, in_denominator=True,
    provenance={"quote": 'randomised <script>alert(1)</script>', "page": 3,
                "method": "pass-agreed", "anchor_status": "verified", "confidence": 0.9},
)


def test_html_provenance_is_behind_details_and_escaped():
    html = render_html(SUMMARY, [SCORE], run_id="r1")
    assert "<details" in html
    body, details = html.split("<details", 1)
    assert "randomised" not in body            # provenance never inline (spec §4)
    assert "randomised" in details
    assert "<script>" not in html              # escaped
    assert "&lt;script&gt;" in html
    assert "systematic review" in html.lower()
    assert "r1" in html


def test_html_diff_column_when_prev_given():
    html = render_html(SUMMARY, [SCORE], run_id="r2", prev=("r1", {"n_total": 0.5}))
    assert "Δ" in html and "+25.0%" in html and "r1" in html


def test_markdown_summary():
    md = render_markdown(SUMMARY, run_id="r1")
    assert "75.0%" in md and "n_total" in md


def test_write_report(tmp_path):
    p = write_report(tmp_path, "<p>hi</p>")
    assert p.name == "report.html"
    assert p.read_text(encoding="utf-8") == "<p>hi</p>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.report'`

- [ ] **Step 3: Implement `src/srx/report.py`**

```python
"""Benchmark reports (spec §4, §7). The displayed table is clean values only;
provenance (quote, page, method, confidence) expands on click via <details>.
Self-contained HTML: inline CSS, zero external requests, zero JS."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Optional

from srx.bench import BenchSummary, FieldScore

_COLORS = {
    "correct": "#e6f4ea", "wrong-value": "#fce8e6", "unit-mismatch": "#fef7e0",
    "missed": "#f1f3f4", "spurious-hallucinated": "#fce8e6",
    "spurious-anchored": "#fef7e0", "gold-suspect": "#e8f0fe",
}

_CSS = """
body { font-family: system-ui, sans-serif; margin: 2rem; color: #202124; }
table { border-collapse: collapse; margin: 1rem 0; }
th, td { border: 1px solid #dadce0; padding: 6px 10px; font-size: 14px; vertical-align: top; }
th { background: #f8f9fa; text-align: left; }
.cards { display: flex; gap: 1rem; }
.card { border: 1px solid #dadce0; border-radius: 8px; padding: 1rem; min-width: 10rem; }
.card b { font-size: 22px; display: block; }
details summary { cursor: pointer; }
details div { margin-top: 6px; font-size: 12px; color: #5f6368; }
"""


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _esc(x) -> str:
    return html.escape(str(x))


def _cell(score: FieldScore) -> str:
    color = _COLORS.get(score.taxonomy, "#ffffff")
    label = _esc(score.engine_display)
    inner = [f"<i>{_esc(score.taxonomy)}</i>", f"gold: {_esc(score.gold_display)}"]
    if score.provenance:
        p = score.provenance
        inner += [
            f"quote: “{_esc(p.get('quote', ''))}” (page {_esc(p.get('page', '?'))})",
            f"method: {_esc(p.get('method', ''))} · anchor: {_esc(p.get('anchor_status', ''))}"
            f" · confidence: {_esc(p.get('confidence', ''))}",
        ]
    body = "<br>".join(inner)
    return (f'<td style="background:{color}"><details><summary>{label}</summary>'
            f"<div>{body}</div></details></td>")


def render_html(summary: BenchSummary, scores: list[FieldScore], run_id: str,
                prev: Optional[tuple[str, dict[str, Optional[float]]]] = None) -> str:
    parts = [f"<style>{_CSS}</style>"]
    parts.append(f"<h1>Systematic review extraction benchmark — run {_esc(run_id)}</h1>")
    parts.append(
        '<div class="cards">'
        f'<div class="card">Overall agreement<b>{_pct(summary.overall_agreement)}</b></div>'
        f'<div class="card">Numeric-field agreement<b>{_pct(summary.numeric_agreement)}</b></div>'
        f'<div class="card">Scored fields<b>{summary.n_scored}</b></div>'
        f'<div class="card">Studies<b>{summary.n_studies}</b></div>'
        "</div>"
    )

    # per-field table (with Δ vs previous benched run when available)
    delta_header = f"<th>Δ vs {_esc(prev[0])}</th>" if prev else ""
    rows = []
    for fid, stats in summary.per_field.items():
        delta_cell = ""
        if prev:
            prev_agreement = prev[1].get(fid)
            cur = stats["agreement"]
            if prev_agreement is None or cur is None:
                delta_cell = "<td>n/a</td>"
            else:
                d = (cur - prev_agreement) * 100
                delta_cell = f"<td>{'+' if d >= 0 else ''}{d:.1f}%</td>"
        taxonomy = ", ".join(f"{k}: {v}" for k, v in sorted(stats["taxonomy"].items()))
        rows.append(
            f"<tr><td>{_esc(fid)}</td><td>{stats['n']}</td>"
            f"<td>{_pct(stats['agreement'])}</td>{delta_cell}<td>{_esc(taxonomy)}</td></tr>"
        )
    parts.append(
        "<h2>Per-field agreement</h2><table>"
        f"<tr><th>Field</th><th>n</th><th>Agreement</th>{delta_header}<th>Taxonomy</th></tr>"
        + "".join(rows) + "</table>"
    )

    # study × field matrix — clean values, provenance on click (spec §4)
    study_ids = sorted({s.study_id for s in scores})
    field_ids = list(summary.per_field.keys())
    by_key = {(s.study_id, s.field_id): s for s in scores}
    matrix_rows = []
    for sid in study_ids:
        cells = []
        for fid in field_ids:
            score = by_key.get((sid, fid))
            cells.append(_cell(score) if score else "<td></td>")
        matrix_rows.append(f"<tr><th>{_esc(sid)}</th>{''.join(cells)}</tr>")
    parts.append(
        "<h2>Study × field matrix</h2>"
        "<p>Click a value to see its provenance (quote, page, method, confidence).</p>"
        "<table><tr><th>Study</th>" + "".join(f"<th>{_esc(f)}</th>" for f in field_ids)
        + "</tr>" + "".join(matrix_rows) + "</table>"
    )
    return "".join(parts)


def render_markdown(summary: BenchSummary, run_id: str) -> str:
    lines = [
        f"# Systematic review extraction benchmark — run {run_id}",
        "",
        f"- Overall agreement: {_pct(summary.overall_agreement)}",
        f"- Numeric-field agreement: {_pct(summary.numeric_agreement)}",
        f"- Scored fields: {summary.n_scored} across {summary.n_studies} studies",
        "",
        "| Field | n | Agreement |",
        "|---|---|---|",
    ]
    for fid, stats in summary.per_field.items():
        lines.append(f"| {fid} | {stats['n']} | {_pct(stats['agreement'])} |")
    return "\n".join(lines)


def write_report(run_dir: Path, html_text: str) -> Path:
    path = Path(run_dir) / "report.html"
    path.write_text(html_text, encoding="utf-8")
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_report.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/srx/report.py tests/test_report.py
git commit -m "feat: self-contained benchmark report with click-to-expand provenance

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: Project layout + pipeline orchestration (checkpoints, cost cap, resume)

**Files:**
- Create: `src/srx/project.py`, `src/srx/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything from Tasks 1–9 (`load_studies/load_pdf`, `extract_study`, `reconcile_study`, `RunStore`, `load_schema`, `StudyExtraction`, `Usage`, `ClaudeError`, `PROMPT_VERSION`).
- Produces:
  - `srx.project.ProjectError(Exception)`.
  - `srx.project.Project(root: Path, studies_dir: Path, schema_path: Path, gold_csv: Path, mapping_yaml: Path, runs_dir: Path, db_path: Path, config: dict)` — field paths per spec §5 (`studies/`, `schema.yaml`, `gold/gold.csv`, `gold/mapping.yaml`, `runs/`, `sr.sqlite`).
  - `srx.project.open_project(root: Path) -> Project` — requires `studies/` with ≥1 PDF and `schema.yaml` (raises `ProjectError` otherwise), creates `runs/`, loads optional `config.yaml` into `.config` (keys used later: `extract_model`, `judge_model`, `cost_cap_usd`, `pricing`).
  - `srx.pipeline.RunConfig(extract_model: str = "claude-fable-5", judge_model: str = "claude-sonnet-5", cost_cap_usd: float = 20.0, resume: str|None = None)` and `srx.pipeline.config_from_project(project: Project, **overrides) -> RunConfig` (project config.yaml first, explicit overrides win).
  - `srx.pipeline.run_extraction(project: Project, config: RunConfig, client, store: RunStore) -> str` — returns run_id. Behavior: per-study checkpoint files `runs/<run_id>/studies/<study_id>.json` (skip when present — resume never re-extracts); cost cap checked **before** each study, capping finishes the run cleanly with status `"cost-capped"`; `ClaudeError` on a study records it `"errored"` and continues; at the end writes `runs/<run_id>/extraction.json` (list of study dicts, sorted by study_id) and finishes the run (`"done"` unless capped).
  - `srx.pipeline.load_run_extractions(run_dir: Path) -> list[StudyExtraction]` — reads `extraction.json`.

- [ ] **Step 1: Write the failing test**

`tests/test_pipeline.py`:

```python
import json

from conftest import make_pdf

from srx.claude_client import ClaudeError, Usage
from srx.pipeline import RunConfig, load_run_extractions, run_extraction
from srx.project import open_project
from srx.runstore import RunStore

SCHEMA_YAML = """\
fields:
  - id: n_total
    type: int
    description: Total randomised
"""

PAGE = "A total of 124 patients were randomised to intervention or placebo."


def entry(value, quote):
    return {"value": value, "unit": None, "quote": quote, "page": 1, "confidence": 0.9}


class FakeClient:
    """Routes tool calls by tool_name + 'Study file:' marker in the prompt text."""

    def __init__(self, fail_studies=()):
        self.fail_studies = set(fail_studies)
        self.extract_calls = []

    def tool_call(self, *, model, system, messages, tool_name, tool_schema, **kw):
        content = messages[0]["content"]
        text = content if isinstance(content, str) else " ".join(
            b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text")
        study = text.split("Study file: ")[1].split()[0]
        if tool_name == "record_extraction":
            if study in self.fail_studies:
                raise ClaudeError("boom")
            self.extract_calls.append(study)
            return {"n_total": entry(124, "124 patients were randomised")}, Usage(1000, 100, 1.0)
        if tool_name == "adjudicate":
            return {"choice": "a", "rationale": "r"}, Usage(100, 10, 0.1)
        raise AssertionError(f"unexpected tool {tool_name}")


def make_project(tmp_path, n_studies=2):
    root = tmp_path / "proj"
    (root / "studies").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "schema.yaml").write_text(SCHEMA_YAML)
    names = ["alpha", "beta", "gamma"][:n_studies]
    for name in names:
        make_pdf(root / "studies" / f"{name}.pdf", [PAGE])
    return root


def test_full_run_writes_checkpoints_and_extraction(tmp_path):
    project = open_project(make_project(tmp_path))
    store = RunStore(project.db_path)
    run_id = run_extraction(project, RunConfig(), FakeClient(), store)
    run_dir = project.runs_dir / run_id
    assert (run_dir / "studies" / "alpha.json").exists()
    extractions = load_run_extractions(run_dir)
    assert [e.study_id for e in extractions] == ["alpha", "beta"]
    assert extractions[0].accepted["n_total"].value == 124
    assert extractions[0].accepted["n_total"].anchor_status == "verified"
    row = store.run_row(run_id)
    assert row["status"] == "done"
    assert row["cost_usd"] > 0
    assert store.study_status(run_id, "beta") == "done"


def test_cost_cap_stops_cleanly_and_resume_completes(tmp_path):
    project = open_project(make_project(tmp_path))
    store = RunStore(project.db_path)
    client = FakeClient()
    # each study costs ~2.0 (two passes at 1.0); cap below that stops after study 1
    run_id = run_extraction(project, RunConfig(cost_cap_usd=1.5), client, store)
    assert store.run_row(run_id)["status"] == "cost-capped"
    assert client.extract_calls.count("alpha") == 2      # two passes
    assert "beta" not in client.extract_calls
    # resume with a higher cap: alpha's checkpoint is reused, only beta runs
    run_id2 = run_extraction(
        project, RunConfig(cost_cap_usd=100.0, resume=run_id), client, store)
    assert run_id2 == run_id
    assert client.extract_calls.count("alpha") == 2      # NOT re-extracted
    assert client.extract_calls.count("beta") == 2
    assert store.run_row(run_id)["status"] == "done"
    assert [e.study_id for e in load_run_extractions(project.runs_dir / run_id)] == ["alpha", "beta"]


def test_errored_study_is_recorded_and_run_continues(tmp_path):
    project = open_project(make_project(tmp_path))
    store = RunStore(project.db_path)
    run_id = run_extraction(project, RunConfig(), FakeClient(fail_studies={"alpha"}), store)
    assert store.study_status(run_id, "alpha") == "errored"
    assert store.study_status(run_id, "beta") == "done"
    assert [e.study_id for e in load_run_extractions(project.runs_dir / run_id)] == ["beta"]
    assert store.run_row(run_id)["status"] == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.pipeline'`

- [ ] **Step 3: Implement `src/srx/project.py`**

```python
"""Project directory layout (spec §5). A project is plain files the user owns:
studies/*.pdf stay local and are sent only to the model API."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ProjectError(Exception):
    pass


@dataclass
class Project:
    root: Path
    studies_dir: Path
    schema_path: Path
    gold_csv: Path
    mapping_yaml: Path
    runs_dir: Path
    db_path: Path
    config: dict = field(default_factory=dict)


def open_project(root: Path) -> Project:
    root = Path(root).resolve()
    studies = root / "studies"
    schema_path = root / "schema.yaml"
    if not studies.is_dir() or not list(studies.glob("*.pdf")):
        raise ProjectError(f"{root}: no studies/ directory with PDFs — see README project layout")
    if not schema_path.is_file():
        raise ProjectError(f"{root}: missing schema.yaml (run `sr schema init` to draft one)")
    runs = root / "runs"
    runs.mkdir(exist_ok=True)
    config_path = root / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    return Project(
        root=root, studies_dir=studies, schema_path=schema_path,
        gold_csv=root / "gold" / "gold.csv", mapping_yaml=root / "gold" / "mapping.yaml",
        runs_dir=runs, db_path=root / "sr.sqlite", config=config or {},
    )
```

- [ ] **Step 4: Implement `src/srx/pipeline.py`**

```python
"""Extraction-run orchestration (spec §6, §8): per-study checkpoints, resume
without re-extraction, clean cost-cap abort, per-study error isolation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from srx.claude_client import ClaudeError
from srx.extractor import PROMPT_VERSION, extract_study
from srx.ingest import load_pdf
from srx.models import StudyExtraction
from srx.project import Project, ProjectError
from srx.reconcile import reconcile_study
from srx.runstore import RunStore, new_run_id
from srx.schema import load_schema


@dataclass
class RunConfig:
    extract_model: str = "claude-fable-5"
    judge_model: str = "claude-sonnet-5"
    cost_cap_usd: float = 20.0
    resume: Optional[str] = None


def config_from_project(project: Project, **overrides) -> RunConfig:
    merged = {}
    for key in ("extract_model", "judge_model", "cost_cap_usd"):
        if key in project.config:
            merged[key] = project.config[key]
    merged.update({k: v for k, v in overrides.items() if v is not None})
    return RunConfig(**merged)


def run_extraction(project: Project, config: RunConfig, client, store: RunStore) -> str:
    schema = load_schema(project.schema_path)
    run_id = config.resume or new_run_id()
    run_dir = project.runs_dir / run_id
    ck_dir = run_dir / "studies"
    ck_dir.mkdir(parents=True, exist_ok=True)

    if config.resume:
        if store.run_row(run_id) is None:
            raise ProjectError(f"cannot resume: run '{run_id}' not found")
    else:
        store.create_run(
            run_id, schema_hash=schema.hash(),
            models={"extract": config.extract_model, "judge": config.judge_model},
            prompt_version=PROMPT_VERSION,
        )

    # cost so far (resume case) comes from existing checkpoints
    total_cost = sum(
        StudyExtraction.from_json(p.read_text(encoding="utf-8")).cost_usd
        for p in ck_dir.glob("*.json")
    )
    status = "done"
    for pdf_path in sorted(project.studies_dir.glob("*.pdf")):
        study_id = pdf_path.stem
        ck = ck_dir / f"{study_id}.json"
        if ck.exists():
            continue  # resume never re-extracts (spec §8)
        if total_cost >= config.cost_cap_usd:
            status = "cost-capped"
            print(f"cost cap ${config.cost_cap_usd:.2f} reached — stopping cleanly; "
                  f"resume with: sr extract --resume {run_id}")
            break
        try:
            doc = load_pdf(pdf_path)
            pdf_bytes = pdf_path.read_bytes()
            pass_a, pass_b, usage = extract_study(
                client, config.extract_model, pdf_bytes, study_id, schema)
            accepted, flagged, not_found, adj_usage = reconcile_study(
                schema=schema, pdf=doc, pass_a=pass_a, pass_b=pass_b,
                client=client, model=config.extract_model)
            usage.add(adj_usage)
            extraction = StudyExtraction(
                study_id=study_id, scanned=doc.scanned, accepted=accepted,
                flagged=flagged, not_found=not_found, cost_usd=usage.cost_usd)
            ck.write_text(extraction.to_json(), encoding="utf-8")
            total_cost += usage.cost_usd
            store.record_study(run_id, study_id, "done", usage.cost_usd, str(ck))
        except ClaudeError as e:
            print(f"study {study_id} errored: {e}")
            store.record_study(run_id, study_id, "errored", 0.0, "")

    extractions = sorted(
        (StudyExtraction.from_json(p.read_text(encoding="utf-8")) for p in ck_dir.glob("*.json")),
        key=lambda s: s.study_id,
    )
    (run_dir / "extraction.json").write_text(
        json.dumps([s.to_dict() for s in extractions], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    store.finish_run(run_id, status, total_cost, {"studies": len(extractions)})
    return run_id


def load_run_extractions(run_dir: Path) -> list[StudyExtraction]:
    data = json.loads((Path(run_dir) / "extraction.json").read_text(encoding="utf-8"))
    return [StudyExtraction.from_dict(d) for d in data]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_pipeline.py -v`
Expected: 3 passed

- [ ] **Step 6: Run the full suite, then commit**

Run: `~/.venvs/srx/bin/pytest -q` — Expected: all pass

```bash
git add src/srx/project.py src/srx/pipeline.py tests/test_pipeline.py
git commit -m "feat: project layout and run orchestration with checkpoints and cost cap

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 14: `sr` CLI + README

**Files:**
- Create: `src/srx/cli.py`, `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything (this is the wiring layer): `open_project`, `config_from_project`, `run_extraction`, `load_run_extractions`, `load_gold`, `score_all/aggregate/exact_judge/make_judge`, `render_html/render_markdown/write_report`, `RunStore`, `ClaudeClient`, `draft_schema_from_csv`.
- Produces:
  - `srx.cli.main(argv: list[str]|None = None, client_factory=None) -> int` — argparse CLI registered as the `sr` script. `client_factory() -> ClaudeClient`-compatible object (tests inject fakes; default builds a real `ClaudeClient`). Exit codes: 0 success, 2 configuration/usage errors (message to stderr).
  - Subcommands (all take `--project DIR`, default `.`):
    - `sr extract [--resume RUN_ID] [--cost-cap USD] [--model M] [--judge-model M]` — runs extraction, prints run id, study count, flagged-field count, total cost.
    - `sr bench [--run RUN_ID] [--no-judge]` — defaults to the latest run; loads gold + mapping (friendly `ProjectError` message if missing), scores, records bench rows, writes `scores.json` (`{"summary": ..., "scores": [...]}`) and `report.html` (with Δ vs previous benched run when one exists), prints the markdown summary and report path. `--no-judge` uses `exact_judge` (no API calls).
    - `sr report [--run RUN_ID]` — re-renders `report.html` from `scores.json` without re-scoring; errors (exit 2) if the run has no `scores.json`.
    - `sr schema init [--from CSV]` — drafts `schema.yaml` from the gold CSV headers (default `gold/gold.csv`); refuses to overwrite without `--force`; reminds the user to hand-refine descriptions/types/units.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import json

from conftest import make_pdf

from srx.claude_client import Usage
from srx.cli import main

SCHEMA_YAML = """\
fields:
  - id: n_total
    type: int
    description: Total randomised
"""

PAGE = "A total of 124 patients were randomised to intervention or placebo."


class FakeClient:
    def tool_call(self, *, model, system, messages, tool_name, tool_schema, **kw):
        if tool_name == "record_extraction":
            return {"n_total": {"value": 124, "unit": None,
                                "quote": "124 patients were randomised",
                                "page": 1, "confidence": 0.9}}, Usage(1000, 100, 0.5)
        if tool_name == "adjudicate":
            return {"choice": "a", "rationale": "r"}, Usage(100, 10, 0.05)
        if tool_name == "judge_equivalence":
            return {"equivalent": True, "rationale": "r"}, Usage(100, 10, 0.05)
        raise AssertionError(tool_name)


def make_project(tmp_path):
    root = tmp_path / "proj"
    (root / "studies").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "schema.yaml").write_text(SCHEMA_YAML)
    make_pdf(root / "studies" / "alpha.pdf", [PAGE])
    (root / "gold" / "gold.csv").write_text("study_id,N randomised\nalpha,124\n")
    (root / "gold" / "mapping.yaml").write_text(
        'study_id_column: study_id\ncolumns:\n  "N randomised": n_total\n')
    return root


def test_extract_then_bench_then_report(tmp_path, capsys):
    root = str(make_project(tmp_path))
    factory = lambda: FakeClient()
    assert main(["extract", "--project", root], client_factory=factory) == 0
    out = capsys.readouterr().out
    assert "run " in out.lower()
    assert main(["bench", "--project", root, "--no-judge"], client_factory=factory) == 0
    out = capsys.readouterr().out
    assert "100.0%" in out and "report.html" in out
    assert main(["report", "--project", root], client_factory=factory) == 0


def test_bench_without_gold_errors_cleanly(tmp_path, capsys):
    root = make_project(tmp_path)
    (root / "gold" / "gold.csv").unlink()
    assert main(["extract", "--project", str(root)], client_factory=lambda: FakeClient()) == 0
    capsys.readouterr()
    assert main(["bench", "--project", str(root), "--no-judge"],
                client_factory=lambda: FakeClient()) == 2
    assert "gold" in capsys.readouterr().err.lower()


def test_schema_init(tmp_path, capsys):
    root = make_project(tmp_path)
    (root / "schema.yaml").unlink()
    assert main(["schema", "init", "--project", str(root)]) == 0
    text = (root / "schema.yaml").read_text()
    assert "n_randomised" in text
    # refuses to overwrite
    assert main(["schema", "init", "--project", str(root)]) == 2


def test_missing_project_exits_2(tmp_path, capsys):
    assert main(["extract", "--project", str(tmp_path / "nope")],
                client_factory=lambda: FakeClient()) == 2
    assert "studies" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venvs/srx/bin/pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'srx.cli'` (or import error from the entry point)

- [ ] **Step 3: Implement `src/srx/cli.py`**

```python
"""`sr` CLI (spec §5): extract, bench, report, schema init."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from srx.bench import BenchSummary, FieldScore, aggregate, exact_judge, make_judge, score_all
from srx.claude_client import ClaudeClient
from srx.goldload import GoldError, load_gold
from srx.pipeline import config_from_project, load_run_extractions, run_extraction
from srx.project import ProjectError, open_project
from srx.report import render_html, render_markdown, write_report
from srx.runstore import RunStore
from srx.schema import SchemaError, draft_schema_from_csv, load_schema


def _resolve_run(store: RunStore, run_id: str | None) -> str:
    if run_id:
        if store.run_row(run_id) is None:
            raise ProjectError(f"run '{run_id}' not found")
        return run_id
    row = store.latest_run()
    if row is None:
        raise ProjectError("no runs yet — run `sr extract` first")
    return row["run_id"]


def _cmd_extract(args, client_factory) -> int:
    project = open_project(Path(args.project))
    config = config_from_project(
        project, extract_model=args.model, judge_model=args.judge_model,
        cost_cap_usd=args.cost_cap, resume=args.resume)
    store = RunStore(project.db_path)
    client = client_factory()
    run_id = run_extraction(project, config, client, store)
    extractions = load_run_extractions(project.runs_dir / run_id)
    n_flagged = sum(len(e.flagged) for e in extractions)
    row = store.run_row(run_id)
    print(f"run {run_id}: {len(extractions)} studies extracted "
          f"({n_flagged} flagged fields for human review), "
          f"status {row['status']}, cost ${row['cost_usd']:.2f}")
    store.close()
    return 0


def _cmd_bench(args, client_factory) -> int:
    project = open_project(Path(args.project))
    if not project.gold_csv.is_file() or not project.mapping_yaml.is_file():
        raise ProjectError(
            f"benchmark needs {project.gold_csv} and {project.mapping_yaml} — "
            "export your human extraction sheet to CSV and map its columns")
    schema = load_schema(project.schema_path)
    store = RunStore(project.db_path)
    run_id = _resolve_run(store, args.run)
    run_dir = project.runs_dir / run_id
    extractions = load_run_extractions(run_dir)
    gold = load_gold(project.gold_csv, project.mapping_yaml, schema)
    config = config_from_project(project)
    judge = exact_judge if args.no_judge else make_judge(client_factory(), config.judge_model)
    scores = score_all(extractions, gold, schema, judge)
    summary = aggregate(scores, schema)
    store.record_bench(run_id, summary.per_field)
    (run_dir / "scores.json").write_text(json.dumps({
        "summary": summary.to_dict(),
        "scores": [s.to_dict() for s in scores],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    prev = _previous_agreements(store, run_id)
    path = write_report(run_dir, render_html(summary, scores, run_id, prev))
    print(render_markdown(summary, run_id))
    print(f"\nreport: {path}")
    store.close()
    return 0


def _previous_agreements(store: RunStore, run_id: str):
    prev_id = store.previous_benched_run(run_id)
    if prev_id is None:
        return None
    return prev_id, {fid: row["agreement"] for fid, row in store.bench_rows(prev_id).items()}


def _cmd_report(args, client_factory) -> int:
    project = open_project(Path(args.project))
    store = RunStore(project.db_path)
    run_id = _resolve_run(store, args.run)
    run_dir = project.runs_dir / run_id
    scores_path = run_dir / "scores.json"
    if not scores_path.is_file():
        raise ProjectError(f"run '{run_id}' has no scores.json — run `sr bench` first")
    data = json.loads(scores_path.read_text(encoding="utf-8"))
    summary = BenchSummary.from_dict(data["summary"])
    scores = [FieldScore.from_dict(d) for d in data["scores"]]
    path = write_report(run_dir, render_html(summary, scores, run_id,
                                             _previous_agreements(store, run_id)))
    print(f"report: {path}")
    store.close()
    return 0


def _cmd_schema_init(args, client_factory) -> int:
    root = Path(args.project)
    target = root / "schema.yaml"
    if target.exists() and not args.force:
        raise ProjectError(f"{target} exists — pass --force to overwrite")
    source = Path(args.from_csv) if args.from_csv else root / "gold" / "gold.csv"
    if not source.is_file():
        raise ProjectError(f"CSV not found: {source}")
    target.write_text(draft_schema_from_csv(source), encoding="utf-8")
    print(f"drafted {target} from {source} — now hand-refine types, units, "
          f"allowed values, and descriptions before extracting")
    return 0


def main(argv=None, client_factory=None) -> int:
    client_factory = client_factory or (lambda: ClaudeClient())
    parser = argparse.ArgumentParser(
        prog="sr", description="Systematic review extraction engine")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="run dual-pass extraction over studies/")
    p.add_argument("--project", default=".")
    p.add_argument("--resume", default=None)
    p.add_argument("--cost-cap", type=float, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--judge-model", default=None)
    p.set_defaults(func=_cmd_extract)

    p = sub.add_parser("bench", help="score a run against the gold-standard extraction")
    p.add_argument("--project", default=".")
    p.add_argument("--run", default=None)
    p.add_argument("--no-judge", action="store_true",
                   help="judge text fields by exact match instead of a model call")
    p.set_defaults(func=_cmd_bench)

    p = sub.add_parser("report", help="re-render report.html from scores.json")
    p.add_argument("--project", default=".")
    p.add_argument("--run", default=None)
    p.set_defaults(func=_cmd_report)

    p_schema = sub.add_parser("schema", help="schema utilities")
    schema_sub = p_schema.add_subparsers(dest="schema_command", required=True)
    p = schema_sub.add_parser("init", help="draft schema.yaml from a gold CSV's headers")
    p.add_argument("--project", default=".")
    p.add_argument("--from", dest="from_csv", default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cmd_schema_init)

    args = parser.parse_args(argv)
    try:
        return args.func(args, client_factory)
    except (ProjectError, GoldError, SchemaError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venvs/srx/bin/pytest tests/test_cli.py -v`
Expected: 4 passed

- [ ] **Step 5: Write `README.md`**

```markdown
# System-Review

Engine for automating the **systematic review** extraction + synthesis workflow.
Phase 0 (this repo state): dual-pass AI data extraction with quote/page
provenance on every value, benchmarked against a human gold-standard sheet.
Spec: `docs/superpowers/specs/2026-07-10-sr-extraction-engine-design.md`.

## Setup

```bash
/usr/bin/python3.12 -m venv ~/.venvs/srx
~/.venvs/srx/bin/pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...
alias sr=~/.venvs/srx/bin/sr
```

Run tests (no network, no API key needed): `~/.venvs/srx/bin/pytest -q`

## Project layout

Projects live under `projects/` (gitignored — study PDFs are copyrighted and
stay local; they are sent only to the Anthropic API):

```
projects/my-review/
  studies/            # included-study PDFs; filename stem = study id
  schema.yaml         # evidence-table field definitions
  gold/gold.csv       # your human extraction (benchmark reference)
  gold/mapping.yaml   # gold column -> schema field mapping
  config.yaml         # optional: extract_model, judge_model, cost_cap_usd
```

**Study id convention:** `studies/smith-2019.pdf` ↔ a `gold.csv` row with
`study_id` = `smith-2019`.

## Workflow

```bash
cd projects/my-review
sr schema init            # draft schema.yaml from gold/gold.csv headers
$EDITOR schema.yaml       # refine types, units, allowed values, descriptions
sr extract                # dual-pass extraction (default cost cap: $20/run)
sr bench                  # score vs gold, write runs/<id>/report.html
sr report                 # re-render the latest benched report
sr extract --resume <id>  # continue a cost-capped or interrupted run
```

Every value the engine accepts carries provenance (verbatim quote, page,
method, confidence) — visible in the report by clicking a value. Values whose
quotes cannot be verified in the PDF text are flagged for human review, never
silently accepted, and flagged values score as "missed" in the benchmark.

Pricing for cost logging lives in `srx/claude_client.py` (`DEFAULT_PRICING`) —
verify against current Anthropic pricing and adjust if needed.

## Phase 0 success gate

≥95% agreement on objective numeric fields after adjudication, zero
unanchored numeric values accepted — measured by `sr bench` against your own
previous review. Prompt changes bump `PROMPT_VERSION`; every bench run diffs
against the previous one in the report.
```

- [ ] **Step 6: Verify the installed entry point works, then commit**

Run: `~/.venvs/srx/bin/sr --help`
Expected: usage text listing `extract`, `bench`, `report`, `schema`

Run: `~/.venvs/srx/bin/pytest -q` — Expected: all pass

```bash
git add src/srx/cli.py README.md tests/test_cli.py
git commit -m "feat: sr CLI (extract/bench/report/schema-init) and README

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 15: End-to-end synthetic integration test

**Files:**
- Test: `tests/test_integration.py`

**Interfaces:**
- Consumes: the whole library API (no CLI): `open_project`, `RunConfig`, `run_extraction`, `load_run_extractions`, `load_gold`, `score_all`, `aggregate`, `exact_judge`, `render_html`, `RunStore`.
- Produces: the permanent CI integration suite (spec §9) — synthetic PDFs with planted values, a planted gold mismatch, a scanned study, and the zero-unanchored invariant.

- [ ] **Step 1: Write the integration test**

`tests/test_integration.py`:

```python
"""End-to-end: synthetic project -> extract (fake API) -> bench -> report.
Covers the spec §7 taxonomy on planted data and the success-gate invariant
that no accepted value is ever unanchored."""
import json

from conftest import make_pdf, make_scanned_pdf

from srx.bench import aggregate, exact_judge, score_all
from srx.claude_client import Usage
from srx.goldload import load_gold
from srx.pipeline import RunConfig, load_run_extractions, run_extraction
from srx.project import open_project
from srx.report import render_html
from srx.runstore import RunStore
from srx.schema import load_schema

SCHEMA_YAML = """\
fields:
  - id: n_total
    type: int
    description: Total randomised
  - id: mean_age
    type: numeric
    unit: years
    description: Mean age, intervention arm
  - id: blinding
    type: categorical
    allowed_values: [open-label, double-blind]
    description: Blinding status
"""

# Planted document truth. beta's n_total gold cell (90) deliberately contradicts
# the document (88) -> engine must classify gold-suspect. beta has no gold
# mean_age -> engine's anchored value is spurious-anchored.
DOCS = {
    "alpha": {"n_total": (124, "A total of 124 patients were randomised."),
              "mean_age": (61.2, "Mean age was 61.2 years in the intervention arm."),
              "blinding": ("double-blind", "This was a double-blind placebo controlled trial.")},
    "beta": {"n_total": (88, "We randomised 88 participants across two sites."),
             "mean_age": (54.3, "Mean age was 54.3 years in the intervention arm."),
             "blinding": ("open-label", "The study used an open-label design.")},
}

GOLD_CSV = (
    "study_id,N,Age,Blinding\n"
    "alpha,124,61.2,double-blind\n"
    "beta,90,,open-label\n"
    "gamma,50,48.0,open-label\n"
)

MAPPING = ('study_id_column: study_id\n'
           'columns:\n  "N": n_total\n  "Age": {field: mean_age, unit: years}\n'
           '  "Blinding": blinding\n')


class FakeClient:
    def tool_call(self, *, model, system, messages, tool_name, tool_schema, **kw):
        content = messages[0]["content"]
        text = content if isinstance(content, str) else " ".join(
            b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text")
        study = text.split("Study file: ")[1].split()[0]
        if tool_name == "record_extraction":
            truth = DOCS.get(study, DOCS["alpha"])  # scanned gamma gets alpha's answers
            result = {}
            for fid, (value, quote) in truth.items():
                result[fid] = {"value": value, "unit": "years" if fid == "mean_age" else None,
                               "quote": quote, "page": 1, "confidence": 0.9}
            return result, Usage(1000, 100, 0.25)
        if tool_name == "adjudicate":
            return {"choice": "a", "rationale": "r"}, Usage(100, 10, 0.02)
        raise AssertionError(tool_name)


def build_project(tmp_path):
    root = tmp_path / "proj"
    (root / "studies").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "schema.yaml").write_text(SCHEMA_YAML)
    for sid, truth in DOCS.items():
        page = " ".join(quote for _, quote in truth.values())
        make_pdf(root / "studies" / f"{sid}.pdf", [page])
    make_scanned_pdf(root / "studies" / "gamma.pdf")
    (root / "gold" / "gold.csv").write_text(GOLD_CSV)
    (root / "gold" / "mapping.yaml").write_text(MAPPING)
    return root


def test_end_to_end(tmp_path):
    project = open_project(build_project(tmp_path))
    schema = load_schema(project.schema_path)
    store = RunStore(project.db_path)

    run_id = run_extraction(project, RunConfig(), FakeClient(), store)
    extractions = load_run_extractions(project.runs_dir / run_id)
    assert [e.study_id for e in extractions] == ["alpha", "beta", "gamma"]

    # success-gate invariant: nothing accepted without a verified anchor
    for e in extractions:
        for ev in e.accepted.values():
            assert ev.anchor_status == "verified"

    # scanned study: nothing accepted, everything flagged vision-only
    gamma = extractions[2]
    assert gamma.scanned and gamma.accepted == {}
    assert all(f.reason == "vision-only" for f in gamma.flagged.values())

    gold = load_gold(project.gold_csv, project.mapping_yaml, schema)
    scores = score_all(extractions, gold, schema, exact_judge)
    summary = aggregate(scores, schema)
    tax = {(s.study_id, s.field_id): s.taxonomy for s in scores}

    assert tax[("alpha", "n_total")] == "correct"
    assert tax[("alpha", "mean_age")] == "correct"
    assert tax[("alpha", "blinding")] == "correct"
    assert tax[("beta", "n_total")] == "gold-suspect"      # doc says 88, gold says 90
    assert tax[("beta", "mean_age")] == "spurious-anchored"
    assert tax[("gamma", "n_total")] == "missed"           # flagged -> missed
    assert summary.taxonomy_counts["missed"] >= 3          # gamma's gold fields

    store.record_bench(run_id, summary.per_field)
    html = render_html(summary, scores, run_id,
                       (run_id, {fid: 0.5 for fid in summary.per_field}))
    assert "<details" in html and "Δ" in html

    # scores.json shape used by `sr report`
    payload = {"summary": summary.to_dict(), "scores": [s.to_dict() for s in scores]}
    assert json.loads(json.dumps(payload))["summary"]["n_studies"] == 3
    store.close()
```

- [ ] **Step 2: Run the integration test**

Run: `~/.venvs/srx/bin/pytest tests/test_integration.py -v`
Expected: 1 passed. If classification assertions fail, fix the *component* (bench/reconcile), not the test — the planted truths encode the spec.

- [ ] **Step 3: Run the full suite**

Run: `~/.venvs/srx/bin/pytest -q`
Expected: all tests pass (~40)

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: end-to-end synthetic integration suite with planted taxonomy cases

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Phase 0 exit (after all tasks)

The plan above ends with a fully tested engine on synthetic data. The Phase 0
**gate** (spec §2) then runs on real data, driven by Omar:

1. Create `projects/<review-name>/`, copy the included-study PDFs into `studies/` (filename stems = gold study ids), export the human extraction sheet to `gold/gold.csv`, write `gold/mapping.yaml`.
2. `sr schema init` → hand-refine `schema.yaml` together (types, units, allowed values, per-field descriptions).
3. `sr extract` (watch cost; raise `--cost-cap` deliberately if needed) → `sr bench` → read `report.html`.
4. Iterate: prompt/pipeline changes bump `PROMPT_VERSION` in `extractor.py`; every re-run's report diffs agreement per field against the previous run. `gold-suspect` rows get human adjudication — some will be gold-sheet errors (that's a feature, log them).
5. Gate: ≥95% numeric-field agreement after adjudication, zero unanchored numerics accepted. Then Phase 1 (meta-analysis core) gets planned.

