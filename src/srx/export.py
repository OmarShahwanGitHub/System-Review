"""Evidence-table exports (spec §4 export rule): a clean spreadsheet the user
can open directly, plus a provenance annex — provenance never inline in the
main sheet. Flagged fields render as a [FLAGGED: reason] marker in the main
sheet; their candidate values appear only in the annex."""
from __future__ import annotations

import csv
from pathlib import Path

from srx.models import StudyExtraction
from srx.schema import Schema

_ANNEX_HEADER = ["study_id", "field_id", "status", "value", "unit", "quote",
                 "page", "method", "anchor_status", "confidence", "flag_reason"]


def write_evidence_csv(extractions: list[StudyExtraction], schema: Schema,
                       out_dir: Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    field_ids = [f.id for f in schema.fields]

    evidence_path = out_dir / "evidence.csv"
    with open(evidence_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["study_id"] + field_ids)
        for e in extractions:
            row: list = [e.study_id]
            for fid in field_ids:
                if fid in e.accepted:
                    row.append(e.accepted[fid].value)
                elif fid in e.flagged:
                    row.append(f"[FLAGGED: {e.flagged[fid].reason}]")
                else:
                    row.append("")
            writer.writerow(row)

    annex_path = out_dir / "provenance.csv"
    with open(annex_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_ANNEX_HEADER)
        for e in extractions:
            for fid in field_ids:
                if fid in e.accepted:
                    v = e.accepted[fid]
                    writer.writerow([e.study_id, fid, "accepted", v.value,
                                     v.unit or "", v.quote, v.page, v.method,
                                     v.anchor_status, v.confidence, ""])
                elif fid in e.flagged:
                    f = e.flagged[fid]
                    for cand in f.candidates:
                        writer.writerow([e.study_id, fid, "flagged", cand.value,
                                         cand.unit or "", cand.quote, cand.page,
                                         "", "", cand.confidence, f.reason])
                else:
                    writer.writerow([e.study_id, fid, "not-reported",
                                     "", "", "", "", "", "", "", ""])
    return evidence_path, annex_path
