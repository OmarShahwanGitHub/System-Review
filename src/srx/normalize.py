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
    if engine_value is None or gold_value is None:
        return False  # absence is handled upstream; None never equals anything
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
