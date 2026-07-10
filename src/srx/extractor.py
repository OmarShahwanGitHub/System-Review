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
