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
