"""
DoseDesk plausibility layer (v0.1.1 framework; ranges expand in v0.2).

This is a SEPARATE concern from the deterministic math engine. It never changes a
calculation and never asserts a dose is "wrong." It compares a computed value against
a sourced reference range and returns a graded, confidence-tagged prompt to verify.

Design rules:
  - Absence of a matching range -> UNKNOWN (never NORMAL). Silence is not safety.
  - Confidence always travels with the assessment. A warning without confidence is noise.
  - The wording is "verify," never "wrong": the tool prompts a double-check, it does
    not overrule the order or the clinician.
  - REVIEW (soft boundary) is reserved for records that carry explicit caution
    thresholds; we do NOT invent a near-boundary heuristic, because a mis-tuned one
    causes alert fatigue — the exact failure this layer exists to avoid.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from fractions import Fraction
from typing import Optional
import json, os

_RANGES_PATH = os.path.join(os.path.dirname(__file__), "..", "provenance", "plausibility_ranges.json")

# Assessment levels
NORMAL = "NORMAL"            # within a sourced range
REVIEW = "REVIEW"           # near an explicit soft boundary (record-defined only)
HIGH_REVIEW = "HIGH_REVIEW"  # outside a sourced range
UNKNOWN = "UNKNOWN"         # no sourced range available -> default


@dataclass
class Plausibility:
    level: str
    message: str
    confidence: str
    matched_range_id: Optional[str]
    source: Optional[str]
    source_version: Optional[str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _load_ranges(path: str = _RANGES_PATH) -> list:
    with open(path) as f:
        return json.load(f).get("ranges", [])


def assess(drug: str, parameter: str, value, population: str = "adult",
           ranges_path: str = _RANGES_PATH) -> Plausibility:
    """Grade a computed value against sourced reference ranges. Defaults to UNKNOWN."""
    drug_l = (drug or "").strip().lower()
    ranges = _load_ranges(ranges_path)
    match = next((r for r in ranges
                  if r["drug"].lower() == drug_l
                  and r["parameter"] == parameter
                  and r["population"] in (population, "all")), None)

    if match is None:
        return Plausibility(
            level=UNKNOWN,
            message=(f"No sourced reference range on file for {drug} ({parameter}, {population}). "
                     "Plausibility not assessed — verify against institutional policy and the label."),
            confidence="unknown", matched_range_id=None, source=None, source_version=None)

    v = Fraction(str(value))
    lo = Fraction(match["lower"]) if match["lower"] != "" else None
    hi = Fraction(match["upper"]) if match["upper"] != "" else None
    within = (lo is None or v >= lo) and (hi is None or v <= hi)

    if within:
        lvl, msg = NORMAL, (f"{value} {parameter} is within the {match['range_type'].replace('_',' ')} "
                            f"of {match['lower'] or '-inf'}-{match['upper'] or 'inf'} for {population}. "
                            "Still confirm patient-specific factors.")
    else:
        lvl, msg = HIGH_REVIEW, (f"{value} {parameter} is OUTSIDE the referenced "
                                 f"{match['lower'] or '-inf'}-{match['upper'] or 'inf'} for {population}. "
                                 "This is a prompt to VERIFY concentration, weight, and intended order — "
                                 "not a statement that the order is wrong.")
    return Plausibility(level=lvl, message=msg, confidence=match["confidence"],
                        matched_range_id=match["id"], source=match["source"],
                        source_version=match["source_version"])


if __name__ == "__main__":
    # dopamine 800 mcg/min for 45 kg == 17.78 mcg/kg/min -> should be NORMAL, not a false alarm
    print(assess("dopamine", "mcg/kg/min", Fraction(800, 45)).to_json())
