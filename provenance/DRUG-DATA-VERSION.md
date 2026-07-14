# Drug-data version log

DoseDesk bundles a **versioned snapshot** of dosing-relevant public data rather than
making live calls, so it runs offline, deterministically, and auditably. Every
label-derived value a user sees should be stamped with the `data_version` it came from.
When FDA labeling changes, re-pull, diff, bump the version, and log it here.

This mirrors the spec-version-diff workflow: a running history so the evolution of the
underlying data is itself traceable.

| data_version | date | scope | notes |
|--------------|------|-------|-------|
| 2026.07-seed | 2026-07 | Seed set: acetaminophen, heparin, insulin, potassium chloride | Initial hand-verified high-alert ceilings in `provenance/safety_bounds.json`. Not comprehensive — proof of the provenance architecture, not a finished formulary. |

## Update procedure (per release)

1. Pull the target drug's current SPL from DailyMed / openFDA (both CC0 / public).
2. Extract the dosage-and-administration and max-dose language.
3. Encode only structured, factual ceilings into `safety_bounds.json` with `source_version`.
4. Diff against the prior snapshot; note any changed ceilings in the table above.
5. Bump `_meta.data_version`, re-run `python -m engine.tests.test_engine` (must stay green).
