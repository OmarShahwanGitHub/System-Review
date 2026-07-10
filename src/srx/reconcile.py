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
