# Validation dashboard

DoseDesk's credibility rests on proof, not assertion. Current state:

| Layer | What it proves | Result |
|-------|----------------|--------|
| Golden cases (Python) | Independently hand-computed answers for all 9 calculators | pass |
| Property invariants (Python) | Scaling, dose→rate→dose round-trip, unit-cancellation hold for random inputs | pass |
| Guardrails (Python) | Error-prone notation (incl. lowercase `u`), hedge-value rejection, pediatric cap halt, bag-runs-dry, unknown-unit refusal | pass |
| Plausibility (Python) | Graded + confidence-tagged; NORMAL/HIGH_REVIEW/UNKNOWN; no false alarm on in-range vasopressor; defaults to UNKNOWN | pass |
| **Python suite total** | `python -m engine.tests.test_engine` | **31 / 31** |
| JS↔Python parity | The browser engine reproduces the Python engine of record exactly, including exact rational strings (`93/8`, `125/4`, `1225/18`) | pass |
| **Parity suite total** | `node engine/tests/parity.test.js` | **12 / 12** |
| Browser self-audit | `web/dosedesk.html` re-runs the golden vectors on load and stamps parity before any result is trusted | 8 / 8 on load |
| Cold-run behavioral | Skill contract under adversarial human input (`docs/COLD-RUN-TESTS.md`) | run at install |

## Release history

| version | date | notes |
|---------|------|-------|
| 0.1.1 | 2026-07 | Calculator/tutor split, `/dose` router, read-back, provenance schema + plausibility framework, error-prone + hedge guardrails, browser demo with self-audited parity, portable `${CLAUDE_PLUGIN_ROOT}` paths. |
| 0.1.0 | 2026-07 | Deterministic engine, exact rational arithmetic, unit algebra, audit records, guardrails. |

## Regenerate / re-verify

```bash
python -m engine.tests.test_engine     # 31/31
node engine/tests/parity.test.js       # 12/12
python -m engine.gen_parity_vectors    # refresh golden vectors from Python of record
python web/build.py                    # re-inline engine + vectors into the browser demo
```
