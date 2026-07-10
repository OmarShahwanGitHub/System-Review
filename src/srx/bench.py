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
