# Data sources & copyright posture

DoseDesk is built to be copyright-clean by construction. Two separate things matter:
the **math** and the **drug data**.

## The math is free

Formulas, methods, and unit conversions are not subject to copyright. Everything the
engine computes — Desired/Have × Quantity, gtt/min, C1V1 = C2V2, percent/ratio
strength, Cockcroft-Gault, Mosteller BSA — is implemented from first principles in
`engine/dosedesk_engine.py`. No formula was copied from a proprietary source.

## Drug data: public-domain wells only

| Source | What it gives | Rights |
|--------|---------------|--------|
| **openFDA** drug label API | FDA Structured Product Labeling (indications, dosage & administration, boxed warnings, max-dose language) | Public domain, dedicated **CC0 1.0**. Use is generally unrestricted. |
| **DailyMed** (NLM) | Official, versioned SPL package inserts, downloadable XML with full version history | Public U.S. government resource. |
| **RxNorm** (NLM) | Drug name normalization, brand↔generic, ingredient relationships | Public domain within the U.S. |

Two honest constraints we design around:

1. **openFDA explicitly says not to rely on it to make decisions.** So every
   label-derived value surfaces *with its source and version*, for clinician review —
   never as a silent black-box gate.
2. **SPL dosage sections are free text, not structured max-dose fields.** You cannot
   reliably machine-extract a numeric ceiling across ~60,000 labels. DoseDesk therefore
   keeps a *small, hand-verified* structured ceiling table for high-alert drugs
   (`provenance/safety_bounds.json`; numeric ceilings are facts, not copyrightable) and, for
   the long tail, retrieves and quotes the relevant label section for a human to judge.

## Do NOT use

Lexicomp, Micromedex, Epocrates, UpToDate, Davis's Drug Guide, or nursing-textbook
problem sets and monographs are proprietary. Do not embed, scrape, or reproduce their
dosing tables, mnemonics, or curated safe-dose ranges. The high-alert *concept* and the
error-prone-abbreviation *categories* are common clinical knowledge sourced from many
places (ISMP originated the concept); DoseDesk builds its own categorization rather than
copying any published list verbatim.
