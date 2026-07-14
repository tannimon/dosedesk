// Proves the JS engine reproduces the Python engine's golden vectors EXACTLY.
// Run: node engine/tests/parity.test.js   (from repo root)
const fs = require("fs");
const path = require("path");
const D = require("../dosedesk_engine.js");

const vectorsPath = path.join(__dirname, "..", "..", "provenance", "parity_vectors.json");
const { vectors } = JSON.parse(fs.readFileSync(vectorsPath, "utf8"));

const DISPATCH = {
  solid_liquid_dose: D.solidLiquidDose,
  infusion_rate: D.infusionRate,
  dose_from_rate: D.doseFromRate,
  reconstitution: D.reconstitution,
  drip_rate: D.dripRate,
  percent_to_mg_ml: D.percentToMgMl,
  ratio_to_mg_ml: D.ratioToMgMl,
  crcl_cockcroft_gault: D.crclCockcroftGault,
};

let pass = 0, fail = 0;
for (const v of vectors) {
  const fn = DISPATCH[v.calc];
  const r = fn.apply(null, v.args);
  const exactOk = r.exact_value === v.exact_value;              // strict rational-string match
  const resultOk = Math.abs(r.result_value - v.result_value) < 1e-9;
  const ok = exactOk && resultOk;
  ok ? pass++ : fail++;
  console.log(
    `  ${ok ? "PASS" : "FAIL"}  ${v.calc.padEnd(22)} exact ${r.exact_value} (py ${v.exact_value}) | result ${r.result_value} (py ${v.result_value})`
  );
}

// Also confirm the guardrails ported: lowercase-u and hedge detection.
const uOk = D.checkErrorProne("Give 5u regular insulin").length > 0;
const uCleanOk = D.checkErrorProne("give 5 units insulin").length === 0;
const hedgeOk = D.checkUncertainInputs("probably around 70 kg").length > 0;
const plausOk = D.plausibilityAssess("dopamine", "mcg/kg/min", "160/9").level === "NORMAL"; // 800/45 = 17.78
[["guardrail: lowercase 5u flagged", uOk], ["guardrail: 'units' not false-flagged", uCleanOk],
 ["guardrail: hedge flagged", hedgeOk], ["plausibility: dopamine 17.78 -> NORMAL", plausOk]]
  .forEach(([n, c]) => { c ? pass++ : fail++; console.log(`  ${c ? "PASS" : "FAIL"}  ${n}`); });

console.log(`\n==== JS<->PY parity: ${pass} passed, ${fail} failed ====`);
process.exit(fail ? 1 : 0);
