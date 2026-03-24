# Radio Communications Reference

This document covers practical radio communication knowledge for field and emergency use, including frequency bands, common protocols, and operational principles.

## Radio Frequency Bands Overview

Radio frequencies are organized into bands. The most relevant for field and emergency use are VHF (Very High Frequency, 30 to 300 MHz) and UHF (Ultra High Frequency, 300 MHz to 3 GHz). VHF signals travel farther in open terrain and can bend slightly over hills. UHF signals penetrate buildings and dense vegetation better but have shorter range. HF (High Frequency, 3 to 30 MHz) can bounce off the ionosphere for long-distance communication but requires larger antennas and more skill.

## FRS and GMRS (United States)

Family Radio Service (FRS) operates on 22 channels in the 462 and 467 MHz UHF band. FRS requires no license. Power is limited to 2 watts on channels 1 through 7 and 15 through 22, and 0.5 watts on channels 8 through 14. Typical range is 0.5 to 2 miles in most terrain.

General Mobile Radio Service (GMRS) shares channels with FRS but allows higher power (up to 50 watts) and repeater use. GMRS requires an FCC license (no exam, fee-based). Range with a handheld is 1 to 5 miles. With a mobile or base station and repeater, range can exceed 30 miles.

FRS channel 1 (462.5625 MHz) is commonly monitored as an informal calling channel. GMRS channel 20 (462.6750 MHz) is designated as the GMRS calling frequency.

## Amateur Radio (Ham)

Amateur radio requires an FCC license obtained by passing an exam. The Technician class license grants access to VHF and UHF bands, which are most useful for local communication. The General class license adds HF privileges for long-distance communication.

Key amateur frequencies for emergency use: 146.520 MHz is the national VHF simplex calling frequency. 446.000 MHz is the national UHF simplex calling frequency. In an emergency where no repeater is available, these are the most likely frequencies to find other operators.

Amateur radio operators often participate in emergency communication networks (ARES and RACES) and can relay messages during disasters when other infrastructure fails.

## CB Radio

Citizens Band radio operates on 40 channels around 27 MHz (HF band). No license is required. Channel 9 is designated for emergencies. Channel 19 is commonly used by truck drivers on highways and is a good general calling channel. CB range is typically 1 to 5 miles with a handheld and 5 to 15 miles with a vehicle-mounted antenna. CB can occasionally propagate hundreds of miles via atmospheric skip, but this is unreliable.

## MURS (Multi-Use Radio Service)

MURS operates on 5 channels in the 151 and 154 MHz VHF band. No license required. Power limited to 2 watts. Range is typically 1 to 4 miles. MURS is less congested than FRS because fewer consumer radios support it. MURS channels are sometimes used for business and ranch operations.

## Marine VHF

Marine VHF operates on channels in the 156 to 174 MHz band. Channel 16 (156.800 MHz) is the international distress and calling frequency. All vessels are required to monitor channel 16. In coastal areas, channel 16 is monitored by the Coast Guard. Marine VHF is restricted to maritime use by regulation but is available in emergencies.

## Emergency Communication Principles

In any emergency radio communication, use plain language rather than codes. State your situation clearly: who you are, where you are, what the emergency is, and what help you need. Repeat critical information (especially location and nature of emergency). Listen before transmitting to avoid interrupting ongoing communications. Keep transmissions brief. Use the word "Mayday" for life-threatening emergencies on maritime and aviation frequencies. Use "SOS" as a universal distress signal.

## Radio Procedures

Standard procedure: listen on the frequency before transmitting. Press the transmit button, pause briefly, then speak clearly at normal volume. Release the transmit button to listen for a response. Identify yourself at the beginning and end of a conversation. On shared frequencies, keep transmissions short to allow others to use the channel.

The NATO phonetic alphabet is used to spell out words clearly: Alpha, Bravo, Charlie, Delta, Echo, Foxtrot, Golf, Hotel, India, Juliet, Kilo, Lima, Mike, November, Oscar, Papa, Quebec, Romeo, Sierra, Tango, Uniform, Victor, Whiskey, X-ray, Yankee, Zulu.

## Signal Propagation Basics

Radio range depends on frequency, power, antenna height, and terrain. Higher antennas dramatically improve range — climbing to higher ground with a handheld radio can double or triple effective range. VHF and UHF are line-of-sight at their core, meaning hills and buildings block signals. Dense vegetation absorbs UHF more than VHF. Water surfaces reflect signals well and can extend range. Urban environments create multipath effects where signals bounce off buildings.

## Power Conservation

In a field scenario with limited battery power, minimize transmission time. Listen more than you transmit. Reduce power output to the minimum needed to maintain contact. Turn the radio off when not actively needed and establish scheduled check-in times instead of monitoring continuously. Carry spare batteries and keep them warm in cold weather (cold reduces battery capacity).

## HERMES Integration Potential

HERMES does not currently include a radio transceiver. Future hardware revisions could integrate a LoRa module for low-power long-range mesh communication between HERMES units. LoRa operates in the 915 MHz ISM band (in the US), requires no license for ISM-band use, and can achieve ranges of 2 to 15 kilometers with low power consumption. This would enable distributed environmental monitoring across a team of HERMES units.
