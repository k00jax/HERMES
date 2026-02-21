#include "buzzer_melodies.h"

#include <Arduino.h>

void play_melody(const Note* notes, size_t count, int gap_ms) {
  if (!notes || count == 0) {
    return;
  }
  constexpr int BUZZER_PIN = D0;
  pinMode(BUZZER_PIN, OUTPUT);
  for (size_t index = 0; index < count; index++) {
    const int freq = notes[index].freq_hz;
    const int dur = notes[index].dur_ms;
    if (freq > 0 && dur > 0) {
      tone(BUZZER_PIN, static_cast<unsigned int>(freq), static_cast<unsigned long>(dur));
      delay(static_cast<unsigned long>(dur + gap_ms));
    }
  }
  noTone(BUZZER_PIN);
}
