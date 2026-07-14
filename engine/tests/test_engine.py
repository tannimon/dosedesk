"""
DoseDesk validation harness.

Two kinds of checks:
  A. GOLDEN cases — numbers computed independently by hand, asserted exactly.
  B. PROPERTY invariants — must hold for ALL inputs (catch bug classes that
     hand-picked examples miss): round-trip, scaling, unit-cancellation.

Run: python -m engine.tests.test_engine   (from the dosedesk/ root)
"""
import sys, os, random
from fractions import Fraction
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from engine import dosedesk_engine as E

PASS = 0
FAIL = 0

def check(name, got, expected):
    global PASS, FAIL
    ok = abs(float(got) - float(expected)) < 1e-9
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got}, expected {expected}")
    PASS += ok; FAIL += (not ok)

def check_true(name, cond):
    global PASS, FAIL
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    PASS += bool(cond); FAIL += (not cond)

print("A. GOLDEN CASES (independently hand-computed)")
# 750 mg ordered, 250 mg/tab -> 3 tabs
check("tablet 750/250", E.solid_liquid_dose(750, 250, "tab", "half_tab").result_value, 3)
# 62 kg, 5 mcg/kg/min, 400 mg/250 mL: 310 mcg/min / 1.6 mg/mL *60 = 11.625 -> 11.6 mL/hr
check("infusion dopamine", E.infusion_rate(62, 5, "mcg/kg/min", 400, "mg", 250).result_value, 11.6)
# reverse of the above at 11.6 mL/hr is ~4.99 mcg/kg/min (rounding drift expected small)
check_true("dose_from_rate sane",
           4.8 < E.dose_from_rate(62, 11.625, 400, "mg", 250).result_value < 5.2)
# 1 g vial + 10 mL -> 100 mg/mL; order 400 mg -> 4 mL
check("reconstitution", E.reconstitution(1, "g", 10, 400, "mg").result_value, 4)
# 1000 mL / 8 hr, 15 gtt/mL -> 31.25 -> 31 gtt/min
check("drip rate", E.drip_rate(1000, 8, "hr", 15).result_value, 31)
# 2% lidocaine -> 20 mg/mL
check("percent 2%", E.percent_to_mg_ml(2).result_value, 20)
# 1:1000 epinephrine -> 1 mg/mL
check("ratio 1:1000", E.ratio_to_mg_ml(1, 1000).result_value, 1)
# CrCl 70yo 70kg Scr 1.0 male: 4900/72 = 68.05.. -> 68
check("crcl", E.crcl_cockcroft_gault(70, 70, 1.0, female=False).result_value, 68)
# BSA 180cm 80kg -> sqrt(4) = 2.00 m^2 (perfect square, exact)
check("bsa perfect square", E.bsa_mosteller(180, 80).result_value, 2.0)

print("\nB. PROPERTY INVARIANTS (random inputs)")
random.seed(1)

# Scaling: doubling ordered dose doubles the tablet count (exact, pre-rounding).
scale_ok = True
for _ in range(500):
    o = random.randint(50, 2000); s = random.choice([25, 50, 100, 250, 500])
    a = Fraction(E.solid_liquid_dose(o, s, "tab", "none").exact_value)
    b = Fraction(E.solid_liquid_dose(o * 2, s, "tab", "none").exact_value)
    scale_ok &= (b == a * 2)
check_true("scaling: 2x dose -> 2x tablets (exact)", scale_ok)

# Round-trip: infusion_rate then dose_from_rate recovers the dose (exact, no rounding).
rt_ok = True
for _ in range(500):
    w = random.randint(3, 150); dose = random.randint(1, 20)
    cm = random.choice([100, 200, 400, 800]); cv = random.choice([50, 100, 250, 500])
    fwd = E.infusion_rate(w, dose, "mcg/kg/min", cm, "mg", cv, rounding="none")
    ml_hr = Fraction(fwd.exact_value)
    back = E.dose_from_rate(w, ml_hr, cm, "mg", cv, "mcg/kg/min", rounding="none")
    rt_ok &= abs(Fraction(back.exact_value) - dose) < Fraction(1, 10**9)
check_true("round-trip: dose -> rate -> dose recovers dose (exact)", rt_ok)

# Unit-cancellation: every calculator returns exactly the unit it promised.
uc = [
    (E.solid_liquid_dose(500, 250, "tab"), "tab"),
    (E.infusion_rate(70, 4, "mcg/kg/min", 400, "mg", 250), "mL/hr"),
    (E.reconstitution(2, "g", 20, 500, "mg"), "mL"),
    (E.drip_rate(500, 4, "hr", 20), "gtt/min"),
]
check_true("unit-cancellation: result units match target", all(r.result_unit == u for r, u in uc))

print("\nC. GUARDRAILS (must fire in code)")
# Error-prone abbreviations
w = E.check_error_prone("give 10 U insulin and .5 mg and 1.0 mg via cc")
check_true("abbrev: flags U", any("unit" in x and "'U'" in x for x in w))
check_true("abbrev: flags naked decimal", any("Naked decimal" in x for x in w))
check_true("abbrev: flags trailing zero", any("Trailing zero" in x for x in w))
check_true("abbrev: flags cc", any("'cc'" in x for x in w))
# Red-team #6: lowercase 'u' for insulin units must be caught (was a bug)
check_true("abbrev: flags lowercase '5u' (insulin)", len(E.check_error_prone("Give 5u regular insulin")) > 0)
check_true("abbrev: does NOT false-flag the word 'insulin'/'units'", len(E.check_error_prone("give 5 units insulin")) == 0)
# Red-team #5: hedged/estimated inputs must be rejected as a code-backed gate
check_true("hedge: flags 'probably around 70 kg'", len(E.check_uncertain_inputs("weight is probably around 70 kg")) > 0)
check_true("hedge: flags '~70 kg'", len(E.check_uncertain_inputs("~70 kg")) > 0)
check_true("hedge: clean exact input passes", len(E.check_uncertain_inputs("weight 72 kg")) == 0)

# Pediatric cap halts
halted = False
try:
    E.enforce_pediatric_cap(computed_mg=1200, adult_max_mg=1000, drug="acetaminophen")
except E.DoseSafetyHalt:
    halted = True
check_true("pediatric cap halts when computed > adult max", halted)

# Bag-runs-dry flag
r = E.infusion_rate(70, 5000, "mcg/kg/min", 1, "mg", 10)  # absurd order
check_true("bag-runs-dry flag fires on implausible order", any("IMPLAUSIBLE" in f for f in r.flags))

# Unit mismatch is refused (can't divide mg by tablets and call it mL)
refused = False
try:
    E.qty(5, "mg") ; E.parse_unit("mg/xyz")
except E.UnitError:
    refused = True
check_true("unknown unit refused (no silent guessing)", refused)

print("\nD. PLAUSIBILITY LAYER (graded, confidence-tagged, fails to UNKNOWN)")
from engine import plausibility as P
# The dopamine example ChatGPT flagged: 800 mcg/min for 45 kg = 17.78 mcg/kg/min.
# A naive range check would cry wolf; ours must NOT — it's inside 2-20.
a1 = P.assess("dopamine", "mcg/kg/min", Fraction(800, 45))
check_true("no false alarm: 17.78 mcg/kg/min dopamine -> NORMAL", a1.level == P.NORMAL)
check_true("NORMAL carries confidence", a1.confidence == "educational-reference")
# Genuinely absurd dose -> HIGH_REVIEW, worded as 'verify' not 'wrong'
a2 = P.assess("dopamine", "mcg/kg/min", 60)
check_true("60 mcg/kg/min dopamine -> HIGH_REVIEW", a2.level == P.HIGH_REVIEW)
check_true("HIGH_REVIEW says verify, not wrong", "wrong" in a2.message and "not a statement" in a2.message)
# Unknown drug -> UNKNOWN (never NORMAL). Silence is not safety.
a3 = P.assess("obscureazole", "mg/kg", 5)
check_true("unknown drug -> UNKNOWN (not NORMAL)", a3.level == P.UNKNOWN)
check_true("UNKNOWN confidence is 'unknown'", a3.confidence == "unknown")
# Unbounded upper (norepi) -> within if above lower
a4 = P.assess("norepinephrine", "mcg/min", 30)
check_true("unbounded upper handled -> NORMAL", a4.level == P.NORMAL)

print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
