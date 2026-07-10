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
