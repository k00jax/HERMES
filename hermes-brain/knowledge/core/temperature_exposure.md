# Temperature Exposure Thresholds and Health Effects

HERMES monitors ambient temperature via the SHT sensor in degrees Celsius. This document provides reference thresholds for interpreting temperature readings in the context of human safety.

## Comfortable Range: 18 to 26 C

The generally accepted comfort range for humans at rest is 18 to 26 degrees Celsius (64 to 79 F). Within this range, the body maintains thermal equilibrium without significant sweating or shivering. Cognitive performance and manual dexterity are optimal in this range.

## Cool Conditions: 10 to 18 C

Between 10 and 18 C, the body begins conserving heat. Peripheral vasoconstriction reduces blood flow to extremities. Manual dexterity begins to decline below 15 C. Layered clothing is recommended. Risk of hypothermia is low for healthy individuals with appropriate clothing, but prolonged sedentary exposure without insulation increases risk.

## Cold Conditions: 0 to 10 C

Between 0 and 10 C, risk of cold injury increases. Fingers and toes are vulnerable to numbness within 30 to 60 minutes of exposure without gloves or insulated footwear. Shivering becomes persistent below 5 C. Core body temperature can begin to drop if clothing is wet or wind chill is significant. Active movement generates body heat and reduces risk.

## Freezing Conditions: Below 0 C

Below 0 C, frostbite risk becomes significant on exposed skin. At minus 10 C with wind, frostbite can occur in 30 minutes on exposed face and hands. At minus 20 C, frostbite can occur in 10 minutes. Hypothermia risk is high without shelter and insulation. Signs of hypothermia include uncontrollable shivering, confusion, slurred speech, and drowsiness. HERMES should flag sustained below-zero readings as a cold exposure warning.

## Warm Conditions: 26 to 35 C

Between 26 and 35 C, the body relies on sweating for cooling. Hydration becomes critical. Physical exertion increases heat load significantly. Heat cramps (muscle cramps from electrolyte loss) can occur during heavy exertion. HERMES should cross-reference temperature with humidity — high humidity impairs sweat evaporation and dramatically increases heat stress risk.

## Hot Conditions: 35 to 40 C

Between 35 and 40 C, heat exhaustion risk is elevated. Symptoms include heavy sweating, weakness, nausea, dizziness, and headache. Core body temperature may rise to 38 to 40 C. Rest in shade, hydration, and cooling are required. Physical exertion should be minimized. When ambient temperature exceeds skin temperature (approximately 35 C), the body gains heat from the environment rather than losing it.

## Extreme Heat: Above 40 C

Above 40 C, heatstroke risk is significant. Heatstroke is a medical emergency where core body temperature exceeds 40 C and the body's cooling mechanisms fail. Symptoms include hot dry skin (sweating may stop), confusion, seizures, and loss of consciousness. Without treatment, heatstroke can be fatal. HERMES should treat sustained readings above 40 C as a critical heat alarm.

## Wet Bulb Temperature

The wet bulb temperature accounts for both heat and humidity. A wet bulb temperature above 35 C is considered the theoretical upper limit of human survivability even at rest in shade with unlimited water. At wet bulb 32 C, sustained outdoor labor becomes dangerous. HERMES can approximate heat stress risk by combining temperature and humidity readings, though a direct wet bulb calculation requires additional modeling.

## Wind Chill

Wind increases convective heat loss from the body. A wind chill equivalent temperature can be significantly lower than the measured air temperature. HERMES does not currently measure wind speed, so temperature readings in windy outdoor conditions should be interpreted conservatively. Adding a wind sensor in future hardware revisions would enable wind chill calculation.

## Correlation with HERMES Sensors

HERMES can combine SHT temperature with humidity to estimate heat index or apparent temperature. Rising temperature with rising humidity indicates increasing heat stress risk even if neither value alone seems dangerous. A room at 32 C and 70 percent humidity is more dangerous than 35 C at 20 percent humidity.
