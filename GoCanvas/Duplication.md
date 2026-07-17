Yes — a large share of the 183 forms are effectively the **same template rebuilt per client**. My field-level similarity analysis (Jaccard on field labels) surfaced ~56 high-overlap pairs that collapse into a handful of families. The biggest wins:

## A. Cross-client environmental-compliance stack (highest-value merges)
These are near-identical forms duplicated per client — prime candidates for **one config/reference-data-driven form each** (the `ClientModules` pattern already supports this):

| Template | Duplicates found | Similarity |
|---|---|---|
| **SPCC Inspection** | Apex SPCC, UPS SPCC, R+L "SPCC – Inspection Log", Amazon SPCC, Knight-Swift SPCC Monthly | 0.91 (48 shared fields) Apex↔R+L |
| **SW Routine Inspection** | Apex SW Routine (+NEW), UPS SW Routine, Amazon SW Routine, ODFL, QT SWPPP | 0.43–0.44 (57–66 shared) |
| **SW Visual Assessment** | Apex Quarterly Visual, R+L SW Visual, UPS SW Visual, Amazon Rain-Event | 0.84 (49 shared) Apex↔R+L |
| **SW Asset Inspection** | Apex SW Asset, Bridgestone SW, RealTerm Baseline Audit, Knight-Swift SW | 0.90 (80 shared) RealTerm↔KS |
| **SW Work Completion** | Apex SW WC, Unified Port SW WC, QT WC (+ Pavement Maintenance shares scaffolding) | 0.50 (13 shared) |
| **DMP Audit / General Checklist** | Apex DMP Audit, UPS DMP Audit, General Survey, UPS General Checklist | 0.63 (27 shared) |

## B. Within-client duplicate families
- **Knight-Swift compliance suite (7 forms)** — SWPPP Annual, SPCC Monthly, SWPPP Monthly/Quarterly, Facility Inspection, Fuel Island, Haz Waste, Non-SW Discharge all share one scaffold; **SWPPP Annual ↔ SPCC Monthly are 1.00 identical (22/22 fields)**.
- **Lewis incident family (4)** — Incident Report, Incident-LPS, Incident/Near-Miss/LHR, MVA Report → one incident form with a type selector.
- **Lewis site audits (3)** — Site Audit, Site Audit-LPS, Site Safety Audit (0.86).
- **Lewis JSA (2)** — JSA-New ↔ JSA-Updated (0.80).
- **Apex Photo Logs (3)** — NEW/Phase I/Large Photos are **1.00 identical**; differ only in image size settings → one form.
- **Lift Station Maintenance (2)** — Kite Realty ↔ Apex CSW (0.64, 104 shared fields).
- **Tailgate Safety Meeting (2)** — Apex ↔ generic, near-duplicate.

## Recommendation
The environmental stack (Section A) is the strongest opportunity: instead of ~5 client copies of each inspection, keep **one master form per inspection type** and drive client-specific questions, SCM/asset lists, and email routing from reference data (`ClientModules`, per-client `*_Questions`/`ActionItems` datasets) — which Apex already does partially. That alone could consolidate **25–30 forms into ~6 templates**.

⚠️ **Caveat:** a few clustered pairs are false positives from shared header/photo/certification scaffolding (e.g. "QT Work Completion ↔ Wawa Emergency Response") — those overlap only in the Facility Info block, not the payload, so don't merge those.

Want me to write this up as a consolidation plan in the report, or draft the merged schema for one family (e.g. the SPCC template)?