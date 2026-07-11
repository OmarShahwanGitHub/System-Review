"""AI-proposed evidence-table schema — the product default when the user has
no template sheet: sample up to three study PDFs and ask the model to design
the extraction fields. The user always reviews/refines before extracting."""
from __future__ import annotations

from pathlib import Path

import yaml

from srx.claude_client import Usage, pdf_document_block
from srx.schema import VALID_LEVELS, VALID_TYPES, _slug, schema_from_yaml_text

MAX_SAMPLE_PDFS = 3

PROPOSE_SYSTEM = (
    "You design the data-extraction evidence table for a systematic review. "
    "From the attached included studies, propose the fields a methodologist "
    "would extract from EVERY study: study design, sample sizes, population "
    "characteristics, interventions/comparators, follow-up, and the outcomes "
    "these papers actually report. Rules: field ids are short snake_case; "
    "prefer numeric/int/categorical over text wherever possible; give units "
    "for numeric fields; for comparative studies create per-arm variants of "
    "arm-specific fields using short, consistent arm slugs; 10-25 fields; "
    "every field must be a per-study fact extractable from a paper, never a "
    "review-level judgment."
)

PROPOSE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string", "enum": sorted(VALID_TYPES)},
                    "description": {"type": "string"},
                    "unit": {"type": ["string", "null"]},
                    "allowed_values": {"type": "array", "items": {"type": "string"}},
                    "level": {"type": "string", "enum": sorted(VALID_LEVELS)},
                },
                "required": ["id", "type", "description"],
            },
        },
    },
    "required": ["fields"],
}


def propose_schema_from_pdfs(client, model: str, studies_dir: Path,
                             review_hint: str = "") -> tuple[str, Usage]:
    """Returns (schema.yaml text, usage). Raises SchemaError if the proposal
    fails validation after sanitization."""
    pdf_paths = sorted(Path(studies_dir).glob("*.pdf"))[:MAX_SAMPLE_PDFS]
    content: list[dict] = [pdf_document_block(p.read_bytes()) for p in pdf_paths]
    hint = f"\nReview context from the user: {review_hint}" if review_hint else ""
    content.append({"type": "text", "text": (
        f"Attached: a sample of {len(pdf_paths)} included studies "
        f"({', '.join(p.stem for p in pdf_paths)}).{hint}\n"
        "Propose the evidence-table schema with the design_schema tool."
    )})
    data, usage = client.tool_call(
        model=model, system=PROPOSE_SYSTEM,
        messages=[{"role": "user", "content": content}],
        tool_name="design_schema", tool_schema=PROPOSE_TOOL_SCHEMA,
    )
    seen: set[str] = set()
    fields: list[dict] = []
    for f in data.get("fields", []):
        fid = _slug(str(f.get("id", "")))
        if not fid or fid == "col" or fid in seen:
            continue
        seen.add(fid)
        entry: dict = {"id": fid, "type": f["type"], "description": f["description"]}
        if f.get("unit"):
            entry["unit"] = f["unit"]
        if f.get("allowed_values"):
            entry["allowed_values"] = [str(v) for v in f["allowed_values"]]
        if f.get("level") and f["level"] != "study":
            entry["level"] = f["level"]
        fields.append(entry)
    text = yaml.safe_dump({"fields": fields}, sort_keys=False, allow_unicode=True)
    schema_from_yaml_text(text, source="<proposed schema>")  # reject bad proposals loudly
    return text, usage
