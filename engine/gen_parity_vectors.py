"""Emit golden parity vectors from the PYTHON engine of record.

The JS port (browser) must reproduce exact_value and result_value for every vector.
Regenerate whenever the engine changes: python engine/gen_parity_vectors.py
"""
import json, os
from engine import dosedesk_engine as E

# calc name -> (python callable, args matching the JS positional signature)
CASES = [
    ("solid_liquid_dose", E.solid_liquid_dose, [750, 250, "tab", "half_tab"]),
    ("infusion_rate",     E.infusion_rate,     [62, 5, "mcg/kg/min", 400, "mg", 250]),
    ("dose_from_rate",    E.dose_from_rate,    [62, 11.625, 400, "mg", 250, "mcg/kg/min", "none"]),
    ("reconstitution",    E.reconstitution,    [1, "g", 10, 400, "mg"]),
    ("drip_rate",         E.drip_rate,         [1000, 8, "hr", 15]),
    ("percent_to_mg_ml",  E.percent_to_mg_ml,  [2]),
    ("ratio_to_mg_ml",    E.ratio_to_mg_ml,    [1, 1000]),
    ("crcl_cockcroft_gault", E.crcl_cockcroft_gault, [70, 70, 1.0, False]),
]

vectors = []
for name, fn, args in CASES:
    r = fn(*args)
    vectors.append({
        "calc": name,
        "args": args,
        "exact_value": r.exact_value,      # exact rational string — the strict parity key
        "result_value": r.result_value,    # rounded display value
        "result_unit": r.result_unit,
    })

out = {
    "_meta": {
        "generated_from": "python engine of record",
        "engine_version": E.ENGINE_VERSION,
        "note": "The browser JS engine must reproduce every exact_value and result_value below. Parity is proven, not asserted.",
    },
    "vectors": vectors,
}

path = os.path.join(os.path.dirname(__file__), "..", "provenance", "parity_vectors.json")
with open(path, "w") as f:
    json.dump(out, f, indent=2)
print(f"wrote {len(vectors)} vectors to provenance/parity_vectors.json")
for v in vectors:
    print(f"  {v['calc']:22} exact={v['exact_value']:>10}  result={v['result_value']} {v['result_unit']}")
