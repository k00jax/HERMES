#pragma once

#include <stddef.h>

struct Note {
  int freq_hz;
  int dur_ms;
};

constexpr Note MELODY_STARTUP_VAULT_BOOT[] = {
  {880, 150},
  {1046, 150},
  {1318, 180},
  {1567, 300},
  {1318, 150},
  {1046, 200},
  {880, 400},
};
constexpr size_t MELODY_STARTUP_VAULT_BOOT_COUNT = sizeof(MELODY_STARTUP_VAULT_BOOT) / sizeof(MELODY_STARTUP_VAULT_BOOT[0]);

constexpr Note MELODY_STARTUP_ATOMIC_SUNRISE[] = {
  {659, 200},
  {784, 200},
  {988, 250},
  {1318, 350},
  {1174, 200},
  {988, 200},
  {784, 500},
};
constexpr size_t MELODY_STARTUP_ATOMIC_SUNRISE_COUNT = sizeof(MELODY_STARTUP_ATOMIC_SUNRISE) / sizeof(MELODY_STARTUP_ATOMIC_SUNRISE[0]);

constexpr Note MELODY_STARTUP_RADIANT_BOOTLOADER[] = {
  {1200, 120},
  {1400, 120},
  {1600, 120},
  {2000, 200},
  {1600, 150},
  {2400, 300},
};
constexpr size_t MELODY_STARTUP_RADIANT_BOOTLOADER_COUNT = sizeof(MELODY_STARTUP_RADIANT_BOOTLOADER) / sizeof(MELODY_STARTUP_RADIANT_BOOTLOADER[0]);

constexpr Note MELODY_STARTUP_FIELD_UNIT_ONLINE[] = {
  {523, 200},
  {659, 200},
  {784, 200},
  {1046, 400},
};
constexpr size_t MELODY_STARTUP_FIELD_UNIT_ONLINE_COUNT = sizeof(MELODY_STARTUP_FIELD_UNIT_ONLINE) / sizeof(MELODY_STARTUP_FIELD_UNIT_ONLINE[0]);

constexpr Note MELODY_STARTUP_WASTELAND_RISE[] = {
  {294, 200},
  {440, 200},
  {523, 250},
  {587, 400},
  {523, 200},
  {440, 200},
  {659, 300},
  {587, 500},
};
constexpr size_t MELODY_STARTUP_WASTELAND_RISE_COUNT = sizeof(MELODY_STARTUP_WASTELAND_RISE) / sizeof(MELODY_STARTUP_WASTELAND_RISE[0]);

constexpr Note MELODY_WARN_RADIATION_SPIKE[] = {
  {2200, 90},
  {2500, 90},
  {2200, 90},
  {2800, 180},
  {0, 70},
  {2800, 180},
};
constexpr size_t MELODY_WARN_RADIATION_SPIKE_COUNT = sizeof(MELODY_WARN_RADIATION_SPIKE) / sizeof(MELODY_WARN_RADIATION_SPIKE[0]);

constexpr Note MELODY_WARN_SYSTEM_FAULT[] = {
  {420, 260},
  {0, 90},
  {420, 260},
  {0, 90},
  {420, 420},
};
constexpr size_t MELODY_WARN_SYSTEM_FAULT_COUNT = sizeof(MELODY_WARN_SYSTEM_FAULT) / sizeof(MELODY_WARN_SYSTEM_FAULT[0]);

constexpr Note MELODY_WARN_LOW_POWER[] = {
  {900, 120},
  {0, 120},
  {900, 120},
  {0, 240},
  {700, 220},
};
constexpr size_t MELODY_WARN_LOW_POWER_COUNT = sizeof(MELODY_WARN_LOW_POWER) / sizeof(MELODY_WARN_LOW_POWER[0]);

constexpr Note MELODY_BEEP_SINGLE_HIGH[] = {
  {2000, 120},
};
constexpr size_t MELODY_BEEP_SINGLE_HIGH_COUNT = sizeof(MELODY_BEEP_SINGLE_HIGH) / sizeof(MELODY_BEEP_SINGLE_HIGH[0]);

constexpr Note MELODY_BEEP_SINGLE_LOW[] = {
  {900, 140},
};
constexpr size_t MELODY_BEEP_SINGLE_LOW_COUNT = sizeof(MELODY_BEEP_SINGLE_LOW) / sizeof(MELODY_BEEP_SINGLE_LOW[0]);

constexpr Note MELODY_BEEP_DOUBLE[] = {
  {1600, 100},
  {0, 80},
  {1600, 100},
};
constexpr size_t MELODY_BEEP_DOUBLE_COUNT = sizeof(MELODY_BEEP_DOUBLE) / sizeof(MELODY_BEEP_DOUBLE[0]);

void play_melody(const Note* notes, size_t count, int gap_ms = 30);
void play_warning_chime();
void play_error_chime();
