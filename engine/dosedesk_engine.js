/*
 * DoseDesk portable engine (JavaScript port of dosedesk_engine.py).
 *
 * Exact arithmetic via BigInt rationals — NO floating point in the math path, NO
 * external library, NO network. Same contract as the Python engine of record:
 * units must cancel, every calc returns a structured audit record, guardrails fire
 * in code. This file is the single JS source; dosedesk.html inlines it verbatim.
 *
 * Because a browser cannot run the Python engine, this is necessarily a second
 * implementation. Its correctness is not asserted — it is PROVEN against golden
 * vectors emitted by the Python engine (see parity_vectors.json / the self-test).
 */
(function (root) {
  "use strict";
  const ENGINE_VERSION = "0.1.0";

  // ---- BigInt rational ----------------------------------------------------
  function bgcd(a, b) { a = a < 0n ? -a : a; b = b < 0n ? -b : b; while (b) { [a, b] = [b, a % b]; } return a; }
  class Frac {
    constructor(n, d = 1n) {
      n = BigInt(n); d = BigInt(d);
      if (d === 0n) throw new Error("zero denominator");
      if (d < 0n) { n = -n; d = -d; }
      const g = bgcd(n, d) || 1n;
      this.n = n / g; this.d = d / g;
    }
    static from(x) {
      if (x instanceof Frac) return x;
      const s = String(x).trim();
      if (s.includes("/")) { const [a, b] = s.split("/"); return new Frac(BigInt(a), BigInt(b)); }
      if (s.includes(".") || s.includes("e") || s.includes("E")) {
        // exact decimal parse (no float): "1.6" -> 16/10
        const neg = s.startsWith("-"); const t = neg ? s.slice(1) : s;
        const [intp, frac = ""] = t.split(".");
        const denom = 10n ** BigInt(frac.length);
        const num = BigInt((intp || "0") + frac);
        return new Frac(neg ? -num : num, denom);
      }
      return new Frac(BigInt(s), 1n);
    }
    mul(o) { o = Frac.from(o); return new Frac(this.n * o.n, this.d * o.d); }
    div(o) { o = Frac.from(o); return new Frac(this.n * o.d, this.d * o.n); }
    add(o) { o = Frac.from(o); return new Frac(this.n * o.d + o.n * this.d, this.d * o.d); }
    sub(o) { o = Frac.from(o); return new Frac(this.n * o.d - o.n * this.d, this.d * o.d); }
    cmp(o) { o = Frac.from(o); const l = this.n * o.d, r = o.n * this.d; return l < r ? -1 : l > r ? 1 : 0; }
    toString() { return this.d === 1n ? this.n.toString() : `${this.n}/${this.d}`; }
    toNumber() { return Number(this.n) / Number(this.d); } // DISPLAY ONLY
  }

  // ---- units --------------------------------------------------------------
  const F = (n, d) => new Frac(n, d);
  const U = {
    mcg: ["MASS", F(1n, 1000n)], ug: ["MASS", F(1n, 1000n)], mg: ["MASS", F(1n)], g: ["MASS", F(1000n)],
    kg: ["WT", F(1n)], lb: ["WT", F(45359237n, 100000000n)],
    mL: ["VOL", F(1n)], L: ["VOL", F(1000n)],
    min: ["TIME", F(1n)], hr: ["TIME", F(60n)], h: ["TIME", F(60n)], sec: ["TIME", F(1n, 60n)],
    mEq: ["MEQ", F(1n)], mmol: ["MMOL", F(1n)], unit: ["UNIT", F(1n)], units: ["UNIT", F(1n)],
    tab: ["TAB", F(1n)], cap: ["TAB", F(1n)], gtt: ["DROP", F(1n)],
  };
  class Qty {
    constructor(value, dims) { this.value = Frac.from(value); this.dims = dims || {}; }
    static norm(d) { const o = {}; for (const k in d) if (d[k] !== 0) o[k] = d[k]; return o; }
    mul(o) { const d = Object.assign({}, this.dims); for (const k in o.dims) d[k] = (d[k] || 0) + o.dims[k]; return new Qty(this.value.mul(o.value), Qty.norm(d)); }
    div(o) { const d = Object.assign({}, this.dims); for (const k in o.dims) d[k] = (d[k] || 0) - o.dims[k]; return new Qty(this.value.div(o.value), Qty.norm(d)); }
  }
  function parseUnit(u) {
    const parts = String(u).split("/");
    let r = new Qty(F(1n), {});
    parts.forEach((tokRaw, i) => {
      const tok = tokRaw.trim();
      if (!(tok in U)) throw new Error(`unknown unit token: ${tok} in ${u}`);
      const [dim, factor] = U[tok];
      const q = new Qty(factor, { [dim]: 1 });
      r = i === 0 ? r.mul(q) : r.div(q);
    });
    return r;
  }
  function qty(mag, unit) { const uq = parseUnit(unit); return new Qty(Frac.from(mag).mul(uq.value), uq.dims); }

  // ---- rounding (single terminal step) ------------------------------------
  function roundHalfUp(value, places) {
    const scale = new Frac(10n ** BigInt(places), 1n);
    const scaled = value.mul(scale);
    // floor for non-negative
    let floor = scaled.n / scaled.d; // BigInt truncation toward zero (values are >= 0 here)
    const rem = scaled.sub(new Frac(floor, 1n));
    if (rem.cmp(F(1n, 2n)) >= 0) floor = floor + 1n;
    return new Frac(floor, 1n).div(scale);
  }
  const ROUNDING = {
    whole: (v) => roundHalfUp(v, 0),
    tenth: (v) => roundHalfUp(v, 1),
    hundredth: (v) => roundHalfUp(v, 2),
    half_tab: (v) => roundHalfUp(v.mul(2), 0).div(2),
    none: (v) => v,
  };
  function applyRounding(v, rule) { if (!(rule in ROUNDING)) throw new Error("unknown rounding rule: " + rule); return ROUNDING[rule](v); }

  function result(calc, inputs, formula, steps, exact, targetUnit, rounding, warnings, flags) {
    const target = parseUnit(targetUnit);
    const displayExact = exact.div(target.value);
    const rounded = applyRounding(displayExact, rounding);
    return {
      calc, inputs, formula, steps,
      exact_value: displayExact.toString(), exact_float: displayExact.toNumber(),
      rounding_rule: rounding, result_value: rounded.toNumber(), result_unit: targetUnit,
      warnings: warnings || [], flags: flags || [], engine_version: ENGINE_VERSION,
    };
  }
  function resultScalar(calc, inputs, formula, steps, val, unit, rounding) {
    const rounded = applyRounding(val, rounding);
    return { calc, inputs, formula, steps, exact_value: val.toString(), exact_float: val.toNumber(),
      rounding_rule: rounding, result_value: rounded.toNumber(), result_unit: unit, warnings: [], flags: [], engine_version: ENGINE_VERSION };
  }

  // ---- safety detectors ---------------------------------------------------
  const ABBREV = [
    [/(?<![A-Za-z])[uU](?![A-Za-z])/, "'u'/'U' for units is error-prone (mistaken for 0/4/cc) — a top insulin-error source. Write 'unit'."],
    [/\bIU\b/, "'IU' is error-prone (mistaken for IV/10). Write 'unit'."],
    [/\bcc\b/, "'cc' is error-prone (mistaken for U/00). Write 'mL'."],
    [/µg|ug\b/, "'µg'/'ug' is error-prone (mistaken for mg). Write 'mcg'."],
    [/\d+\.0*\b(?=\s*(mg|mcg|g|mL|unit))/, "Trailing zero after a decimal is error-prone (10x risk). Drop it."],
    [/(?<![\d.])\.\d/, "Naked decimal (no leading zero) is error-prone (10x risk). Write '0.x'."],
  ];
  function checkErrorProne(raw) { const w = []; for (const [p, m] of ABBREV) if (p.test(raw)) w.push(m); return w; }
  const HEDGE = /\b(probably|approximately|approx|about|around|roughly|guess|guessing|estimate|estimated|somewhere|maybe|ish|or so|give or take)\b|~\s*\d|\d\s*ish\b/i;
  function checkUncertainInputs(raw) {
    return HEDGE.test(raw) ? ["Input contains an approximate/hedged value. A medication calculation requires exact ordered values — do not calculate on an estimate; request the actual figure."] : [];
  }

  // ---- calculators (mirror python) ----------------------------------------
  function solidLiquidDose(orderedMg, availMg, perUnit = "tab", rounding = "half_tab") {
    const ordered = qty(orderedMg, "mg"), strength = qty(availMg, `mg/${perUnit}`);
    const r = ordered.div(strength);
    const steps = [`ordered ${orderedMg} mg / strength ${availMg} mg per ${perUnit}`, `units: mg / (mg/${perUnit}) = ${perUnit}`, `exact = ${r.value} ${perUnit}`];
    return result("solid_liquid_dose", { ordered: `${orderedMg} mg`, available: `${availMg} mg/${perUnit}` }, "result = ordered / available_strength", steps, r.value, perUnit, rounding);
  }
  function infusionRate(weightKg, dose, doseUnit, concMass, concMassUnit, concVolMl, rounding = "tenth", pumpMaxMlHr = null) {
    const doseQ = qty(dose, doseUnit);
    const perKg = doseUnit.split("/").includes("kg");
    const weighted = perKg ? doseQ.mul(qty(weightKg, "kg")) : doseQ;
    const conc = qty(concMass, concMassUnit).div(qty(concVolMl, "mL"));
    const volRate = weighted.div(conc);
    const mlPerHr = volRate.value.mul(60);
    const flags = [];
    if (mlPerHr.cmp(F(0n)) > 0) {
      const minutesToEmpty = qty(concVolMl, "mL").value.div(volRate.value);
      if (minutesToEmpty.cmp(F(5n)) < 0) flags.push(`IMPLAUSIBLE: bag empties in ~${minutesToEmpty.toNumber().toFixed(1)} min — verify order/concentration.`);
    }
    if (pumpMaxMlHr !== null && mlPerHr.cmp(Frac.from(pumpMaxMlHr)) > 0) flags.push(`RATE EXCEEDS PUMP MAX (${pumpMaxMlHr} mL/hr) — verify pump/setup.`);
    const steps = [`dose ${dose} ${doseUnit}${perKg ? ` x weight ${weightKg} kg` : ""}`, `concentration = ${concMass} ${concMassUnit} / ${concVolMl} mL = ${conc.value} (canonical mg/mL)`, `rate = (mass/time) / concentration -> volume/time`, `exact = ${mlPerHr} mL/hr (before rounding)`];
    return result("infusion_rate", { weight: `${weightKg} kg`, dose: `${dose} ${doseUnit}`, concentration: `${concMass} ${concMassUnit}/${concVolMl} mL` }, "rate = (dose x weight) / concentration", steps, volRate.value, "mL/hr", rounding, [], flags);
  }
  function doseFromRate(weightKg, mlPerHr, concMass, concMassUnit, concVolMl, targetDoseUnit = "mcg/kg/min", rounding = "tenth") {
    const rate = qty(mlPerHr, "mL/hr");
    const conc = qty(concMass, concMassUnit).div(qty(concVolMl, "mL"));
    const massPerTime = rate.mul(conc);
    const perKg = targetDoseUnit.split("/").includes("kg");
    const dose = perKg ? massPerTime.div(qty(weightKg, "kg")) : massPerTime;
    const steps = [`rate ${mlPerHr} mL/hr x concentration ${concMass} ${concMassUnit}/${concVolMl} mL`, `${perKg ? `/ weight ${weightKg} kg ` : ""}-> ${targetDoseUnit}`];
    return result("dose_from_rate", { weight: `${weightKg} kg`, rate: `${mlPerHr} mL/hr`, concentration: `${concMass} ${concMassUnit}/${concVolMl} mL` }, "dose = (rate x concentration) / weight", steps, dose.value, targetDoseUnit, rounding);
  }
  function reconstitution(vialAmt, vialUnit, diluentMl, ordered, orderedUnit, rounding = "tenth") {
    const finalConc = qty(vialAmt, vialUnit).div(qty(diluentMl, "mL"));
    const draw = qty(ordered, orderedUnit).div(finalConc);
    const steps = [`final concentration = ${vialAmt} ${vialUnit} / ${diluentMl} mL = ${finalConc.value} (canonical mg/mL)`, `draw = ordered ${ordered} ${orderedUnit} / final concentration -> mL`, `exact = ${draw.value} mL`];
    return result("reconstitution", { vial: `${vialAmt} ${vialUnit}`, diluent: `${diluentMl} mL`, ordered: `${ordered} ${orderedUnit}` }, "draw = ordered / (vial_amount / diluent_volume)", steps, draw.value, "mL", rounding);
  }
  function dripRate(volumeMl, timeVal, timeUnit, dropFactor, rounding = "whole") {
    const flow = qty(volumeMl, "mL").div(qty(timeVal, timeUnit));
    const gtt = flow.mul(qty(dropFactor, "gtt/mL"));
    const steps = [`flow = ${volumeMl} mL / ${timeVal} ${timeUnit}`, `x drop factor ${dropFactor} gtt/mL -> gtt/min`, `exact = ${gtt.value} gtt/min (before rounding to whole drops)`];
    return result("drip_rate", { volume: `${volumeMl} mL`, time: `${timeVal} ${timeUnit}`, drop_factor: `${dropFactor} gtt/mL` }, "gtt/min = (volume / time) x drop_factor", steps, gtt.value, "gtt/min", rounding);
  }
  function percentToMgMl(percent) {
    const conc = qty(Frac.from(percent).mul(1000).toString(), "mg").div(qty(100, "mL"));
    return result("percent_to_mg_ml", { percent: `${percent}%` }, "mg/mL = percent x 10", [`${percent}% w/v = ${percent} g / 100 mL`], conc.value, "mg/mL", "none");
  }
  function ratioToMgMl(numG, denMl) {
    const conc = qty(Frac.from(numG).mul(1000).toString(), "mg").div(qty(denMl, "mL"));
    return result("ratio_to_mg_ml", { ratio: `${numG} g : ${denMl} mL` }, "mg/mL = grams x 1000 / mL", [`1:${denMl} (g:mL) = ${numG} g / ${denMl} mL`], conc.value, "mg/mL", "none");
  }
  function crclCockcroftGault(age, weightKg, scr, female = false, rounding = "whole") {
    let val = Frac.from(140 - age).mul(Frac.from(weightKg)).div(Frac.from(72).mul(Frac.from(scr)));
    if (female) val = val.mul(F(85n, 100n));
    return resultScalar("crcl_cockcroft_gault", { age, weight: `${weightKg} kg`, scr: `${scr} mg/dL`, female }, "Cockcroft-Gault", [`((140 - ${age}) x ${weightKg}) / (72 x ${scr})${female ? " x 0.85 (female)" : ""}`], val, "mL/min", rounding);
  }
  function enforcePediatricCap(computedMg, adultMaxMg, drug = "") {
    if (Frac.from(computedMg).cmp(Frac.from(adultMaxMg)) > 0)
      throw { name: "DoseSafetyHalt", message: `HALT: computed pediatric dose ${computedMg} mg exceeds adult max ${adultMaxMg} mg${drug ? " for " + drug : ""}. Recheck weight and order.` };
  }

  // ---- plausibility (seed ranges inline; mirror provenance json) -----------
  const RANGES = [
    { id: "dopamine.mcg_kg_min.adult", drug: "dopamine", parameter: "mcg/kg/min", range_type: "therapeutic_reference", lower: "2", upper: "20", population: "adult", source: "commonly cited educational reference range", source_version: "2026.07-seed", confidence: "educational-reference" },
    { id: "norepinephrine.mcg_min.adult", drug: "norepinephrine", parameter: "mcg/min", range_type: "usual_starting", lower: "2", upper: "", population: "adult", source: "commonly cited educational reference range", source_version: "2026.07-seed", confidence: "educational-reference" },
  ];
  function plausibilityAssess(drug, parameter, value, population = "adult") {
    const dl = (drug || "").trim().toLowerCase();
    const m = RANGES.find((r) => r.drug.toLowerCase() === dl && r.parameter === parameter && (r.population === population || r.population === "all"));
    if (!m) return { level: "UNKNOWN", message: `No sourced reference range on file for ${drug} (${parameter}, ${population}). Plausibility not assessed — verify against institutional policy and the label.`, confidence: "unknown", matched_range_id: null, source: null, source_version: null };
    const v = Frac.from(value);
    const lo = m.lower !== "" ? Frac.from(m.lower) : null, hi = m.upper !== "" ? Frac.from(m.upper) : null;
    const within = (lo === null || v.cmp(lo) >= 0) && (hi === null || v.cmp(hi) <= 0);
    const level = within ? "NORMAL" : "HIGH_REVIEW";
    const msg = within
      ? `${value} ${parameter} is within the ${m.range_type.replace(/_/g, " ")} of ${m.lower || "-inf"}-${m.upper || "inf"} for ${population}. Still confirm patient-specific factors.`
      : `${value} ${parameter} is OUTSIDE the referenced ${m.lower || "-inf"}-${m.upper || "inf"} for ${population}. This is a prompt to VERIFY concentration, weight, and intended order — not a statement that the order is wrong.`;
    return { level, message: msg, confidence: m.confidence, matched_range_id: m.id, source: m.source, source_version: m.source_version };
  }

  const api = {
    ENGINE_VERSION, Frac, qty, parseUnit, applyRounding,
    solidLiquidDose, infusionRate, doseFromRate, reconstitution, dripRate,
    percentToMgMl, ratioToMgMl, crclCockcroftGault, enforcePediatricCap,
    checkErrorProne, checkUncertainInputs, plausibilityAssess,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.DoseDesk = api;
})(typeof window !== "undefined" ? window : this);
