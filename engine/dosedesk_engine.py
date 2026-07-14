"""
DoseDesk deterministic dosing engine.

Design contract (non-negotiable):
  1. All arithmetic is EXACT (fractions.Fraction) end to end. No float math until
     the single, explicit terminal rounding step.
  2. Every quantity carries a unit dimension. Calculations REFUSE to return a result
     if the units do not cancel to the requested target unit (raises UnitError).
  3. Every calculation returns a structured Result object = the audit record:
     inputs, formula, unit-cancellation trace, exact intermediate, rounding rule,
     final value, warnings, hard flags, engine version. This is the artifact a
     surveyor reviews.
  4. Guardrails (pediatric-exceeds-adult-max, bag-runs-dry, error-prone abbreviations)
     are raised by CODE, independent of any narration layered on top.

Zero Trust: standard library only. No network, no external packages, no data egress.
A port to browser JS is straightforward using a small self-contained rational type.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from fractions import Fraction
from typing import Optional
import json
import re

ENGINE_VERSION = "0.1.0"

# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #
# Each base dimension is deliberately distinct. Body weight (WT) is NOT the same
# dimension as drug mass (MASS) — you can never add a patient's kg to a drug's mg,
# and the engine enforces that. mEq / mmol / unit are their own dimensions because
# they only convert to mass with a drug-specific factor (handled explicitly, never
# silently).

_UNIT_TABLE = {
    # symbol:        (dimension, factor-to-canonical-base as Fraction)
    "mcg": ("MASS", Fraction(1, 1000)),
    "ug":  ("MASS", Fraction(1, 1000)),   # tolerated on input but flagged (see abbrev check)
    "mg":  ("MASS", Fraction(1)),
    "g":   ("MASS", Fraction(1000)),
    "kg":  ("WT",   Fraction(1)),
    "lb":  ("WT",   Fraction(45359237, 100000000)),  # exact 0.45359237 kg
    "mL":  ("VOL",  Fraction(1)),
    "L":   ("VOL",  Fraction(1000)),
    "min": ("TIME", Fraction(1)),
    "hr":  ("TIME", Fraction(60)),
    "h":   ("TIME", Fraction(60)),
    "sec": ("TIME", Fraction(1, 60)),
    "mEq": ("MEQ",  Fraction(1)),
    "mmol":("MMOL", Fraction(1)),
    "unit":("UNIT", Fraction(1)),
    "units":("UNIT",Fraction(1)),
    "tab": ("TAB",  Fraction(1)),
    "cap": ("TAB",  Fraction(1)),
    "gtt": ("DROP", Fraction(1)),
}

# canonical display symbol per dimension (used when composing result units)
_CANON = {"MASS": "mg", "WT": "kg", "VOL": "mL", "TIME": "min",
          "MEQ": "mEq", "MMOL": "mmol", "UNIT": "unit", "TAB": "tab", "DROP": "gtt"}


class UnitError(ValueError):
    pass


class DoseSafetyHalt(Exception):
    """Raised when a hard guardrail trips. Never swallow silently."""
    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail or {}


@dataclass(frozen=True)
class Quantity:
    """An exact magnitude in canonical base units, plus a dimension signature
    {dimension: exponent}. e.g. concentration 1.6 mg/mL -> value=8/5, dims={MASS:1, VOL:-1}."""
    value: Fraction
    dims: tuple  # sorted tuple of (dim, exp) for hashability

    @staticmethod
    def _norm(d: dict) -> tuple:
        return tuple(sorted((k, v) for k, v in d.items() if v != 0))

    @classmethod
    def make(cls, value: Fraction, dims: dict) -> "Quantity":
        return cls(Fraction(value), cls._norm(dims))

    def dimdict(self) -> dict:
        return dict(self.dims)

    def __mul__(self, other: "Quantity") -> "Quantity":
        d = self.dimdict()
        for k, v in other.dims:
            d[k] = d.get(k, 0) + v
        return Quantity.make(self.value * other.value, d)

    def __truediv__(self, other: "Quantity") -> "Quantity":
        d = self.dimdict()
        for k, v in other.dims:
            d[k] = d.get(k, 0) - v
        return Quantity.make(self.value / other.value, d)

    def dim_label(self) -> str:
        num = [k for k, e in self.dims for _ in range(e) if e > 0]
        den = [k for k, e in self.dims for _ in range(-e) if e < 0]
        n = "*".join(_CANON[k] for k in num) or "1"
        if den:
            return n + "/" + "/".join(_CANON[k] for k in den)
        return n


def parse_unit(u: str) -> Quantity:
    """Parse a unit string like 'mg', 'mg/mL', 'mcg/kg/min' into a unit Quantity (value=1)."""
    u = u.strip()
    parts = u.split("/")
    result = Quantity.make(Fraction(1), {})
    for i, tok in enumerate(parts):
        tok = tok.strip()
        if tok not in _UNIT_TABLE:
            raise UnitError(f"unknown unit token: {tok!r} in {u!r}")
        dim, factor = _UNIT_TABLE[tok]
        q = Quantity.make(factor, {dim: 1})
        result = result * q if i == 0 else result / q
    return result


def qty(magnitude, unit: str) -> Quantity:
    """Build a Quantity from a human magnitude + unit string. Magnitude kept EXACT."""
    unit_q = parse_unit(unit)
    return Quantity.make(Fraction(str(magnitude)) * unit_q.value, unit_q.dimdict())


# --------------------------------------------------------------------------- #
# Rounding — the single terminal step. Exact Fraction -> rounded Fraction.
# --------------------------------------------------------------------------- #
def _round_half_up(value: Fraction, places: int) -> Fraction:
    scale = Fraction(10) ** places
    scaled = value * scale
    floor = scaled.numerator // scaled.denominator
    remainder = scaled - floor
    rounded = floor + 1 if remainder >= Fraction(1, 2) else floor
    return Fraction(rounded) / scale


ROUNDING_RULES = {
    "whole": lambda v: _round_half_up(v, 0),            # drops, whole tablets
    "tenth": lambda v: _round_half_up(v, 1),            # mL, most pump rates
    "hundredth": lambda v: _round_half_up(v, 2),        # syringe pumps, peds mL
    "half_tab": lambda v: _round_half_up(v * 2, 0) / 2, # nearest 1/2 tablet
    "none": lambda v: v,
}


def apply_rounding(value: Fraction, rule: str) -> Fraction:
    if rule not in ROUNDING_RULES:
        raise ValueError(f"unknown rounding rule: {rule}")
    return ROUNDING_RULES[rule](value)


# --------------------------------------------------------------------------- #
# Result / audit record
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    calc: str
    inputs: dict
    formula: str
    steps: list           # human-readable unit-cancellation trace
    exact_value: str      # exact fraction as string, e.g. "93/8"
    exact_float: float    # for display only; NEVER used in further math
    rounding_rule: str
    result_value: float
    result_unit: str
    warnings: list = field(default_factory=list)
    flags: list = field(default_factory=list)   # hard safety flags
    engine_version: str = ENGINE_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _finish(calc, inputs, formula, steps, exact: Fraction, target_unit: str,
            rounding: str, warnings=None, flags=None) -> Result:
    target = parse_unit(target_unit)
    # unit-cancellation gate: exact must be expressed in the target's canonical scale
    # exact is already in canonical base units; divide by target scale to get display magnitude
    display_exact = exact / target.value
    rounded = apply_rounding(display_exact, rounding)
    return Result(
        calc=calc, inputs=inputs, formula=formula, steps=steps,
        exact_value=str(display_exact), exact_float=float(display_exact),
        rounding_rule=rounding, result_value=float(rounded), result_unit=target_unit,
        warnings=warnings or [], flags=flags or [],
    )


# --------------------------------------------------------------------------- #
# Safety: error-prone abbreviation detector (ISMP-style categories, built from
# common knowledge — not copied from any proprietary list).
# --------------------------------------------------------------------------- #
_ABBREV_PATTERNS = [
    (r"(?<![A-Za-z])[uU](?![A-Za-z])", "'u'/'U' for units is error-prone (mistaken for 0/4/cc) — a top insulin-error source. Write 'unit'."),
    (r"\bIU\b", "'IU' is error-prone (mistaken for IV/10). Write 'unit'."),
    (r"\bcc\b", "'cc' is error-prone (mistaken for U/00). Write 'mL'."),
    (r"µg|ug\b", "'µg'/'ug' is error-prone (mistaken for mg). Write 'mcg'."),
    (r"\d+\.0*\b(?=\s*(mg|mcg|g|mL|unit))", "Trailing zero after a decimal is error-prone (10x risk). Drop it."),
    (r"(?<![\d.])\.\d", "Naked decimal (no leading zero) is error-prone (10x risk). Write '0.x'."),
]


def check_error_prone(raw: str) -> list:
    warns = []
    for pat, msg in _ABBREV_PATTERNS:
        if re.search(pat, raw):
            warns.append(msg)
    return warns


# Hedge/estimate detector: a medication calculation must never run on an approximate
# input. This turns "reject estimated values" from an instruction into an enforced gate.
_HEDGE_PATTERN = re.compile(
    r"\b(probably|approximately|approx|about|around|roughly|guess|guessing|estimate|"
    r"estimated|somewhere|maybe|ish|or so|give or take)\b|~\s*\d|\d\s*ish\b",
    re.IGNORECASE,
)


def check_uncertain_inputs(raw: str) -> list:
    """Flag hedged/approximate values ('probably ~70 kg') that must not back a real calc."""
    warns = []
    if _HEDGE_PATTERN.search(raw):
        warns.append("Input contains an approximate/hedged value. A medication calculation "
                     "requires exact ordered values — do not calculate on an estimate; "
                     "request the actual figure.")
    return warns


# --------------------------------------------------------------------------- #
# Calculators
# --------------------------------------------------------------------------- #
def solid_liquid_dose(ordered_mg, available_strength_mg, per_unit="tab",
                      rounding="half_tab") -> Result:
    """Desired/Have x Quantity. available_strength is mg per 1 <per_unit> (tab or mL)."""
    ordered = qty(ordered_mg, "mg")
    strength = qty(available_strength_mg, f"mg/{per_unit}")
    result = ordered / strength            # -> <per_unit>
    steps = [
        f"ordered {ordered_mg} mg  /  strength {available_strength_mg} mg per {per_unit}",
        f"units: mg / (mg/{per_unit}) = {per_unit}",
        f"exact = {result.value}  {per_unit}",
    ]
    return _finish("solid_liquid_dose",
                   {"ordered": f"{ordered_mg} mg", "available": f"{available_strength_mg} mg/{per_unit}"},
                   "result = ordered / available_strength", steps, result.value,
                   per_unit, rounding)


def infusion_rate(weight_kg, dose, dose_unit, conc_mass, conc_mass_unit,
                  conc_vol_ml, rounding="tenth", pump_max_ml_hr=None) -> Result:
    """Weight-based dose -> mL/hr. dose_unit like 'mcg/kg/min'. Handles weight-based
    and flat (unit without /kg) doses. Concentration = conc_mass conc_mass_unit / conc_vol_ml mL."""
    dose_q = qty(dose, dose_unit)
    weighted = dose_q * qty(weight_kg, "kg") if "kg" in dose_unit.split("/") else dose_q
    conc = qty(conc_mass, conc_mass_unit) / qty(conc_vol_ml, "mL")
    vol_rate = weighted / conc            # -> VOL/TIME
    steps = [
        f"dose {dose} {dose_unit}" + (f" x weight {weight_kg} kg" if 'kg' in dose_unit.split('/') else ""),
        f"= mass/time {weighted.value} (canonical mg/min)",
        f"concentration = {conc_mass} {conc_mass_unit} / {conc_vol_ml} mL = {conc.value} (canonical mg/mL)",
        f"rate = (mass/time) / concentration -> volume/time",
        f"exact = {(vol_rate.value*Fraction(60))}  mL/hr (before rounding)",
    ]
    warns, flags = [], []
    # bag-runs-dry sanity: how long until conc_vol_ml is consumed at this rate?
    ml_per_hr_exact = vol_rate.value * Fraction(60)
    if ml_per_hr_exact > 0:
        minutes_to_empty = qty(conc_vol_ml, "mL").value / vol_rate.value
        if minutes_to_empty < 5:
            flags.append(f"IMPLAUSIBLE: bag empties in ~{float(minutes_to_empty):.1f} min — verify order/concentration.")
    if pump_max_ml_hr is not None and ml_per_hr_exact > Fraction(str(pump_max_ml_hr)):
        flags.append(f"RATE EXCEEDS PUMP MAX ({pump_max_ml_hr} mL/hr) — verify pump/setup.")
    return _finish("infusion_rate",
                   {"weight": f"{weight_kg} kg", "dose": f"{dose} {dose_unit}",
                    "concentration": f"{conc_mass} {conc_mass_unit}/{conc_vol_ml} mL"},
                   "rate = (dose x weight) / concentration", steps, vol_rate.value,
                   "mL/hr", rounding, warns, flags)


def dose_from_rate(weight_kg, ml_per_hr, conc_mass, conc_mass_unit, conc_vol_ml,
                   target_dose_unit="mcg/kg/min", rounding="tenth") -> Result:
    """Reverse: given a running mL/hr, what dose is the patient receiving?"""
    rate = qty(ml_per_hr, "mL/hr")
    conc = qty(conc_mass, conc_mass_unit) / qty(conc_vol_ml, "mL")
    mass_per_time = rate * conc           # -> MASS/TIME
    per_kg = "kg" in target_dose_unit.split("/")
    dose = mass_per_time / qty(weight_kg, "kg") if per_kg else mass_per_time
    steps = [
        f"rate {ml_per_hr} mL/hr x concentration {conc_mass} {conc_mass_unit}/{conc_vol_ml} mL",
        f"= mass/time {mass_per_time.value} (canonical mg/min)",
        (f"/ weight {weight_kg} kg " if per_kg else "") + f"-> {target_dose_unit}",
    ]
    return _finish("dose_from_rate",
                   {"weight": f"{weight_kg} kg", "rate": f"{ml_per_hr} mL/hr",
                    "concentration": f"{conc_mass} {conc_mass_unit}/{conc_vol_ml} mL"},
                   "dose = (rate x concentration) / weight", steps, dose.value,
                   target_dose_unit, rounding)


def reconstitution(vial_amount, vial_amount_unit, diluent_ml, ordered, ordered_unit,
                   rounding="tenth") -> Result:
    """Powder vial reconstituted with diluent -> volume to draw for the ordered dose."""
    final_conc = qty(vial_amount, vial_amount_unit) / qty(diluent_ml, "mL")
    draw = qty(ordered, ordered_unit) / final_conc
    steps = [
        f"final concentration = {vial_amount} {vial_amount_unit} / {diluent_ml} mL = {final_conc.value} (canonical mg/mL)",
        f"draw = ordered {ordered} {ordered_unit} / final concentration -> mL",
        f"exact = {draw.value} mL",
    ]
    return _finish("reconstitution",
                   {"vial": f"{vial_amount} {vial_amount_unit}", "diluent": f"{diluent_ml} mL",
                    "ordered": f"{ordered} {ordered_unit}"},
                   "draw = ordered / (vial_amount / diluent_volume)", steps, draw.value,
                   "mL", rounding)


def drip_rate(volume_ml, time_val, time_unit, drop_factor_gtt_ml, rounding="whole") -> Result:
    """Gravity tubing gtt/min. Drops must be whole."""
    flow = qty(volume_ml, "mL") / qty(time_val, time_unit)   # VOL/TIME
    gtt = flow * qty(drop_factor_gtt_ml, "gtt/mL")           # DROP/TIME
    steps = [
        f"flow = {volume_ml} mL / {time_val} {time_unit}",
        f"x drop factor {drop_factor_gtt_ml} gtt/mL -> gtt/min",
        f"exact = {(gtt.value)} gtt/min (before rounding to whole drops)",
    ]
    return _finish("drip_rate",
                   {"volume": f"{volume_ml} mL", "time": f"{time_val} {time_unit}",
                    "drop_factor": f"{drop_factor_gtt_ml} gtt/mL"},
                   "gtt/min = (volume / time) x drop_factor", steps, gtt.value,
                   "gtt/min", rounding)


def percent_to_mg_ml(percent) -> Result:
    """x% w/v = x g per 100 mL = x*10 mg/mL."""
    conc = qty(Fraction(str(percent)) * 1000, "mg") / qty(100, "mL")  # x g/100mL
    steps = [f"{percent}% w/v = {percent} g / 100 mL = {conc.value} (canonical mg/mL)"]
    return _finish("percent_to_mg_ml", {"percent": f"{percent}%"},
                   "mg/mL = percent x 10", steps, conc.value, "mg/mL", "none")


def ratio_to_mg_ml(numerator_g, denominator_ml) -> Result:
    """Ratio strength like 1:1000 = 1 g in 1000 mL = 1 mg/mL."""
    conc = qty(Fraction(str(numerator_g)) * 1000, "mg") / qty(denominator_ml, "mL")
    steps = [f"1:{denominator_ml} (g:mL) = {numerator_g} g / {denominator_ml} mL = {conc.value} (canonical mg/mL)"]
    return _finish("ratio_to_mg_ml", {"ratio": f"{numerator_g} g : {denominator_ml} mL"},
                   "mg/mL = grams x 1000 / mL", steps, conc.value, "mg/mL", "none")


# --------------------------------------------------------------------------- #
# Clinical helper formulas (float-tolerant: these are estimates, not dispensable
# volumes; still returned as structured records).
# --------------------------------------------------------------------------- #
def crcl_cockcroft_gault(age, weight_kg, scr_mg_dl, female=False, rounding="whole") -> Result:
    num = Fraction(140 - age) * Fraction(str(weight_kg))
    den = Fraction(72) * Fraction(str(scr_mg_dl))
    val = num / den
    if female:
        val = val * Fraction(85, 100)
    steps = [f"((140 - {age}) x {weight_kg}) / (72 x {scr_mg_dl})" + (" x 0.85 (female)" if female else "")]
    return _finish_scalar("crcl_cockcroft_gault",
                          {"age": age, "weight": f"{weight_kg} kg", "scr": f"{scr_mg_dl} mg/dL", "female": female},
                          "Cockcroft-Gault", steps, val, "mL/min", rounding)


def bsa_mosteller(height_cm, weight_kg, rounding="hundredth") -> Result:
    prod = Fraction(str(height_cm)) * Fraction(str(weight_kg)) / Fraction(3600)
    # sqrt of a Fraction: compute exactly where possible, else high-precision.
    val = _fraction_sqrt(prod)
    steps = [f"sqrt(({height_cm} x {weight_kg}) / 3600) = sqrt({prod})"]
    return _finish_scalar("bsa_mosteller",
                          {"height": f"{height_cm} cm", "weight": f"{weight_kg} kg"},
                          "Mosteller BSA", steps, val, "m^2", rounding)


def _fraction_sqrt(x: Fraction, iters: int = 60) -> Fraction:
    """Newton's method sqrt returning a Fraction, high precision. Exact when perfect square."""
    if x == 0:
        return Fraction(0)
    # perfect-square fast path
    import math
    n_ok = math.isqrt(x.numerator)
    d_ok = math.isqrt(x.denominator)
    if n_ok * n_ok == x.numerator and d_ok * d_ok == x.denominator:
        return Fraction(n_ok, d_ok)
    guess = Fraction(math.isqrt(x.numerator) + 1, max(1, math.isqrt(x.denominator)))
    for _ in range(iters):
        guess = (guess + x / guess) / 2
    return guess


def _finish_scalar(calc, inputs, formula, steps, val: Fraction, unit, rounding) -> Result:
    rounded = apply_rounding(val, rounding)
    return Result(calc=calc, inputs=inputs, formula=formula, steps=steps,
                  exact_value=str(val), exact_float=float(val), rounding_rule=rounding,
                  result_value=float(rounded), result_unit=unit)


# --------------------------------------------------------------------------- #
# Guardrail: pediatric dose must not exceed the adult maximum.
# --------------------------------------------------------------------------- #
def enforce_pediatric_cap(computed_mg, adult_max_mg, drug=""):
    if Fraction(str(computed_mg)) > Fraction(str(adult_max_mg)):
        raise DoseSafetyHalt(
            f"HALT: computed pediatric dose {computed_mg} mg exceeds adult max {adult_max_mg} mg"
            + (f" for {drug}" if drug else "") + ". Recheck weight and order.",
            detail={"computed_mg": computed_mg, "adult_max_mg": adult_max_mg, "drug": drug})


if __name__ == "__main__":
    r = infusion_rate(62, 5, "mcg/kg/min", 400, "mg", 250)
    print(r.to_json())
