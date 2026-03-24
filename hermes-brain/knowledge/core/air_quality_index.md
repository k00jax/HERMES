# Air Quality Index and TVOC Thresholds

HERMES monitors air quality via the SGP30 sensor, which reports both eCO2 (covered in co2_safety.md) and Total Volatile Organic Compounds (TVOC) in parts per billion (ppb). This document covers TVOC interpretation and general air quality assessment.

## What Are Volatile Organic Compounds

VOCs are organic chemicals that evaporate at room temperature. Common sources include paints, solvents, cleaning products, adhesives, building materials, cooking, vehicle exhaust, personal care products, and off-gassing from new furniture or electronics. Some VOCs are harmless at low concentrations while others are toxic even in small amounts.

## TVOC Thresholds

### Good: Below 220 ppb

TVOC below 220 ppb indicates clean air with no significant VOC contamination. This is typical of well-ventilated outdoor environments or indoor spaces with minimal off-gassing sources.

### Acceptable: 220 to 660 ppb

TVOC between 220 and 660 ppb is acceptable for occupied indoor spaces. Minor sources like cooking or cleaning may produce temporary spikes in this range. No health effects expected for most people during normal exposure durations.

### Marginal: 660 to 2200 ppb

TVOC between 660 and 2200 ppb indicates noticeable contamination. Sensitive individuals may experience eye irritation, headaches, or throat discomfort. Sources should be identified and ventilation increased. Common causes include fresh paint, new carpet or furniture, heavy cleaning chemical use, or poor ventilation during cooking. HERMES should flag sustained readings in this range as a warning.

### Poor: 2200 to 5500 ppb

TVOC between 2200 and 5500 ppb indicates significant air quality problems. Most occupants will experience discomfort. Symptoms include headache, nausea, eye and throat irritation, and difficulty concentrating. Ventilation must be improved immediately. Source identification is critical. HERMES should treat sustained readings in this range as an alert.

### Dangerous: Above 5500 ppb

TVOC above 5500 ppb indicates hazardous air quality. Immediate evacuation or maximum ventilation is recommended. Possible causes include chemical spills, fire byproducts, industrial emissions, or confined space with heavy solvent use. Respiratory protection may be warranted. HERMES should treat readings above 5500 ppb as a critical alarm.

## SGP30 TVOC Sensor Characteristics

The SGP30 uses a metal oxide semiconductor to detect a broad range of VOCs. It does not distinguish between individual compounds — it reports a total equivalent concentration. The sensor is sensitive to alcohols, aldehydes, ketones, organic acids, and hydrocarbons. It is less sensitive to methane and carbon monoxide.

The SGP30 requires a 12-hour burn-in period after first power-on. During this time, readings may drift. After burn-in, the sensor benefits from periodic baseline calibration. HERMES firmware should store and restore baseline values across power cycles for best accuracy.

Readings can be temporarily elevated by cooking, cleaning products, perfume, hand sanitizer (ethanol), and similar everyday sources. Short spikes (under 5 minutes) are usually not concerning. Sustained elevation indicates a persistent source or ventilation problem.

## Combining TVOC with Other Sensors

HERMES can provide more meaningful alerts by combining TVOC with other readings. High TVOC with high eCO2 in an occupied space strongly suggests ventilation failure. High TVOC with no detected presence may indicate a chemical source unrelated to occupancy (off-gassing, leak, or external contamination). High TVOC with elevated temperature may indicate accelerated off-gassing from materials, as VOC emission rates increase with heat.

## Indoor Air Quality Standards Reference

The World Health Organization recommends TVOC levels below 300 ppb for long-term indoor exposure. The German Federal Environment Agency recommends investigation when TVOC exceeds 1000 ppb and action when it exceeds 3000 ppb. OSHA does not set a single TVOC limit but regulates individual compounds separately. For field use, HERMES uses the tiered threshold approach described above as a practical approximation.
