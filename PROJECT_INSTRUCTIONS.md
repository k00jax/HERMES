---

# PROJECT_INSTRUCTIONS.md

Version: 0.2
Status: Product-Oriented Governance
Last Updated: 2026-02-13

---

## PURPOSE OF THIS FILE

This document defines how HERMES should be developed, evaluated, and evolved as:

• An open hardware edge intelligence platform
• A potential commercial device

HERMES must now be treated as a product architecture, not a hobby build.

---

## 1. PRODUCT INTENT

HERMES is:

* Modular
* Offline-first
* Edge-AI capable
* Wearable-ready
* Open architecture

It is not:

* A cloud-dependent gadget
* A novelty sensor rig
* A fragile prototype

Every design choice must consider future public release.

---

## 2. ARCHITECTURAL DISCIPLINE

System layers are mandatory:

Tier 1 – Sensor Layer (MCUs)
Tier 2 – Edge Compute (SBC)
Tier 3 – AI / Memory Layer

No cross-layer leakage without explicit justification.

If a proposal violates separation of concerns:

* Flag it
* Justify it
* Document it

Architecture drift kills products.

---

## 3. CHANGE MANAGEMENT RULE

If any of the following change:

* Hardware selection
* Communication protocol
* Data flow
* AI model strategy
* Power architecture
* Enclosure assumptions

Then:

1. Identify impacted document section.
2. Propose exact modification.
3. Confirm with Kyle.
4. Increment version number.
5. Update Last Updated field.

No undocumented evolution.

---

## 4. PRODUCT READINESS FILTER

Every new feature must pass this filter:

1. Does it increase reliability?
2. Does it increase user value?
3. Does it increase survivability?
4. Does it increase differentiation?

If it only increases complexity, reject it.

---

## 5. FEATURE PROPOSAL FORMAT

All major features must specify:

* Tier impact (1 / 2 / 3)
* Power impact
* Thermal impact
* BOM impact
* Firmware complexity impact
* Manufacturability impact
* Field reliability impact

If those are not considered, the proposal is incomplete.

---

## 6. HARDWARE GOVERNANCE

Before adding hardware:

Ask:

* Can this be implemented in firmware first?
* Does it meaningfully increase signal quality?
* Does it justify BOM cost?
* Does it scale to production?

Prototype-friendly is not the same as production-ready.

---

## 7. POWER IS NON-NEGOTIABLE

Wearable viability is defined by:

* Idle draw
* Peak draw
* Thermal envelope
* Battery life target

Power analysis must precede feature enthusiasm.

---

## 8. LLM GOVERNANCE

LLM is enhancement, not dependency.

Rules:

* System must function without AI.
* AI must degrade gracefully.
* Models must be modular and swappable.
* No permanent cloud requirement.

Commercial viability demands reliability without inference.

---

## 9. OPEN PLATFORM STRATEGY

If open hardware:

* Maintain clear documentation.
* Keep firmware modular.
* Avoid proprietary lock-in.
* Design clean interfaces.

If commercialized:

* Lock down firmware branches.
* Define hardware revision control.
* Establish versioned releases.
* Introduce QA standards.

Both paths require architectural clarity now.

---

## 10. BRAND DISCIPLINE

HERMES is positioned as:

A distributed edge cognition platform.

Not a smartwatch.
Not a tricorder toy.
Not an AI gimmick.

Differentiation comes from:

* Offline capability
* Multi-MCU design
* Context-first logging
* Survivability mindset

---

## 11. FUTURE SESSION RULE

When interacting with this project:

* Treat HERMES_MASTER.md as constitution.
* Treat ROADMAP.md as directional intent.
* Update documentation before major shifts.
* Flag architectural violations.
* Think long-term maintainability.
* Think manufacturing viability.
* Think support burden.

Assume future users are not Kyle.

---

## 12. META PRINCIPLE

Build as if:

1,000 people will replicate this.
100 will modify it.
10 will depend on it.
1 will take it into the wilderness.

Design accordingly.

End of document.

```