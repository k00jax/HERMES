# CO2 Safety Thresholds and Health Effects

HERMES monitors CO2 via the SGP30 sensor reporting eCO2 (equivalent CO2) in parts per million (ppm).

## Normal Outdoor Levels

Outdoor ambient CO2 typically ranges from 400 to 420 ppm. This is the baseline for healthy air. HERMES calibration should establish a local baseline close to this range in well-ventilated or outdoor environments.

## Indoor Comfort Range

Indoor CO2 between 400 and 1000 ppm is considered acceptable for occupied spaces. Levels below 800 ppm indicate good ventilation. Between 800 and 1000 ppm, ventilation is adequate but could be improved.

## Elevated CO2: 1000 to 2000 ppm

At 1000 to 1500 ppm, occupants may begin to notice stuffiness. Cognitive performance studies show measurable decline in decision-making ability starting around 1000 ppm. Drowsiness and reduced concentration are common above 1200 ppm. This range indicates poor ventilation in enclosed spaces and warrants opening windows or doors.

At 1500 to 2000 ppm, discomfort becomes noticeable. Headaches, fatigue, and difficulty concentrating are common. Prolonged exposure at these levels is not recommended. HERMES should flag sustained readings above 1500 ppm as a warning condition.

## High CO2: 2000 to 5000 ppm

Between 2000 and 5000 ppm, air quality is poor. Expect headaches, sleepiness, and poor concentration. Nausea may occur at the higher end. This range typically indicates a sealed or overcrowded space with inadequate ventilation. Immediate ventilation improvement is recommended. HERMES should treat sustained readings above 2000 ppm as an alert condition.

## Dangerous CO2: Above 5000 ppm

5000 ppm is the OSHA permissible exposure limit (PEL) for an 8-hour workday. Above 5000 ppm, oxygen displacement becomes a concern. Symptoms include increased heart rate, rapid breathing, and dizziness.

At 40000 ppm (4%), exposure becomes immediately dangerous to life and health (IDLH). Loss of consciousness can occur within minutes. Death can result from prolonged exposure.

HERMES should treat any reading above 5000 ppm as a critical alarm requiring immediate evacuation or ventilation.

## SGP30 Sensor Limitations

The SGP30 measures equivalent CO2 (eCO2), not true CO2. It infers CO2 levels from hydrogen gas and volatile organic compound concentrations. Accuracy is lower than NDIR-based CO2 sensors. Readings can be influenced by cleaning chemicals, cooking fumes, perfumes, and other volatile organics. The SGP30 requires a 12-hour initial burn-in period and benefits from periodic baseline calibration. HERMES readings should be treated as indicative, not laboratory-grade.

## Correlation with Presence Detection

HERMES can cross-reference CO2 trends with radar presence data. Rising CO2 in a sealed room with detected presence suggests occupancy-driven accumulation. Rising CO2 without detected presence may indicate an external source such as a vehicle exhaust leak, combustion byproduct, or soil gas intrusion and should be flagged differently.
