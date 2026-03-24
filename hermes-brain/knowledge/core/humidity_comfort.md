# Humidity Ranges and Health Impacts

HERMES monitors relative humidity via the SHT sensor, reported as a percentage. Humidity significantly affects thermal comfort, respiratory health, and material preservation.

## Optimal Range: 30 to 60 Percent

Relative humidity between 30 and 60 percent is considered the comfort zone for most people. Within this range, respiratory mucous membranes stay hydrated, skin moisture is balanced, and the risk of both mold growth and static electricity is minimized. Most indoor environments should aim for this range.

## Low Humidity: Below 30 Percent

Below 30 percent relative humidity, air feels dry. Skin cracking, chapped lips, and nosebleeds become more common. Respiratory passages dry out, increasing susceptibility to colds and respiratory infections. Static electricity buildup increases, which can be a nuisance and a hazard around sensitive electronics. Contact lens discomfort increases. Wooden structures and instruments can crack or warp from moisture loss.

Below 20 percent is considered very dry. Prolonged exposure irritates eyes, throat, and nasal passages. In arid or desert environments, HERMES may routinely read in this range — this is expected but worth flagging for health awareness.

## High Humidity: 60 to 80 Percent

Between 60 and 80 percent, air begins to feel muggy. Sweat evaporation slows, reducing the body's primary cooling mechanism. Perceived temperature rises — a room at 28 C and 75 percent humidity feels significantly hotter than 28 C at 40 percent humidity. Mold growth risk increases on surfaces above 60 percent humidity sustained for more than 48 hours. Dust mites thrive above 70 percent humidity.

## Very High Humidity: Above 80 Percent

Above 80 percent relative humidity, thermal regulation through sweating is severely impaired. Heat exhaustion risk rises dramatically during physical exertion even at moderate temperatures. Condensation forms on cool surfaces. Electronics are at risk of moisture damage. Mold growth can begin within 24 to 48 hours on organic surfaces. Food spoilage accelerates.

Above 95 percent humidity indicates near-saturation conditions typical of fog, rain, or tropical environments. HERMES should flag sustained above-80 readings as a humidity warning, especially when combined with elevated temperature.

## Dew Point as a Comfort Indicator

Dew point temperature is often a better indicator of human discomfort than relative humidity alone. A dew point below 10 C feels dry and comfortable. Between 10 and 16 C is comfortable. Between 16 and 20 C begins to feel humid. Above 20 C feels oppressive. Above 24 C is dangerous — the body cannot cool effectively.

HERMES can estimate dew point from temperature and relative humidity using the Magnus formula. This provides a single number that reliably predicts perceived mugginess regardless of actual temperature.

## Humidity and CO2 Correlation

In occupied enclosed spaces, both humidity and CO2 tend to rise together because humans exhale both moisture and CO2. If HERMES detects rising humidity and rising CO2 simultaneously in a sealed environment, this strongly indicates poor ventilation and occupancy-driven air quality degradation. Ventilation should be recommended.

## Humidity and Material Safety

For equipment and gear storage, maintaining 40 to 50 percent humidity is ideal. Below 30 percent, leather dries and cracks. Above 60 percent, metal corrodes faster, paper degrades, and textiles develop mildew. In a field context, HERMES humidity readings can inform decisions about where to store sensitive equipment or provisions.

## Condensation Risk

When the ambient temperature drops close to the dew point (within 2 to 3 degrees C), condensation forms on surfaces. This is relevant for electronics protection, optics fogging, and tent or shelter interior moisture. HERMES can warn of condensation risk by comparing temperature trends with humidity levels.
