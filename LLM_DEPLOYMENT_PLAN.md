---

# LLM_DEPLOYMENT_PLAN.md

Version: 2.0
Status: Modular Enhancement Strategy
Last Updated: 2026-02-21

---

## 1. PRINCIPLE

The LLM layer is an enhancement, not a dependency.

HERMES must:

* Function fully offline.
* Log data without inference.
* Provide usable analytics without AI.
* Generate reports deterministically.

AI must add value, not stability risk.

---

## 2. CURRENT SYSTEM STATE

As of v2:

* Radar detection operational.
* Environmental sensing operational.
* Analytics implemented.
* Reports implemented.
* Calibration implemented.
* History + CSV export implemented.
* Field Mode implemented.

All of this runs without LLM involvement.

This is correct.

---

## 3. WHERE LLM FITS

LLM integration should sit at Tier 3:

Sensor Data → SQLite → Structured API → LLM Interface → Augmented Output

Never:

Sensor → LLM → Core Logic

Inference must never sit in the control loop.

---

## 4. VALID LLM USE CASES

### A. Report Narrative Augmentation

Add optional narrative section to generated reports:

Example:

* “Presence peaks occurred between 6pm–9pm.”
* “ECO2 levels correlated with prolonged stationary presence.”

LLM consumes:

* Structured summary stats
* Bounded time window
* Aggregated values only

LLM must not access raw unbounded database.

---

### B. Event Anomaly Detection

Possible future enhancement:

* Detect unusual radar behavior.
* Detect abnormal ECO2 spikes.
* Detect calibration drift over time.

Must operate on pre-aggregated summaries.

---

### C. Field Mode Summary

Optional:

* Generate short daily summary:

  * “Device recorded 4 hours of occupancy.”
  * “Two ECO2 spikes exceeded 1500 ppm.”

Generated once per day.
Not continuous inference.

---

### D. Query-Based Insights

User enters:
“What happened between 3pm and 6pm yesterday?”

LLM queries structured API, not raw DB.

---

## 5. DEPLOYMENT OPTIONS

### Option 1 – Local LLM (Preferred Long-Term)

Host lightweight model on Odroid:

Constraints:

* Memory limits
* Model size < 7B parameters likely required
* No GPU acceleration

Use case:

* Short summary generation
* Trend analysis

Must measure RAM and CPU impact first.

---

### Option 2 – Cloud LLM (Optional Enhancement)

Allowed only if:

* Explicitly enabled
* User opt-in
* Offline fallback preserved

Never required for base functionality.

---

## 6. API BOUNDARY RULE

All LLM interaction must occur via:

/api/summary
/api/anomaly
/api/report_narrative

These endpoints:

* Accept bounded inputs
* Never stream raw logs
* Never expose entire database
* Enforce max time windows

---

## 7. SECURITY CONSTRAINTS

LLM must:

* Never write to core sensor tables.
* Never modify calibration values.
* Never alter settings.
* Only produce derived outputs.

LLM output must be labeled as generated insight.

---

## 8. PERFORMANCE CONSTRAINTS

LLM inference must:

* Not block ingest loop.
* Not block dashboard.
* Run async or background.
* Time out gracefully.

Failure mode:
If LLM fails, system continues normally.

---

## 9. FUTURE EXPANSION

Potential AI enhancements:

* Drift detection in radar baseline over months.
* Behavioral clustering of occupancy.
* Air quality trend prediction.
* Self-calibration recommendations.

All optional.

---

## 10. META RULE

AI must not increase fragility.

If removing the LLM breaks the device, architecture is wrong.

---

End of document.

---