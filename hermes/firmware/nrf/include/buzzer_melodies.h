#pragma once

#include <stddef.h>

struct Note {
  int freq_hz;
  int dur_ms;
};

inline constexpr Note MELODY_STARTUP_VAULT_BOOT[] = {
  {880, 150},
  {1046, 150},
  {1318, 180},
  {1567, 300},
  {1318, 150},
  {1046, 200},
  {880, 400},
};
inline constexpr size_t MELODY_STARTUP_VAULT_BOOT_COUNT = sizeof(MELODY_STARTUP_VAULT_BOOT) / sizeof(MELODY_STARTUP_VAULT_BOOT[0]);

inline constexpr Note MELODY_STARTUP_ATOMIC_SUNRISE[] = {
  {659, 200},
  {784, 200},
  {988, 250},
  {1318, 350},
  {1174, 200},
  {988, 200},
  {784, 500},
};
inline constexpr size_t MELODY_STARTUP_ATOMIC_SUNRISE_COUNT = sizeof(MELODY_STARTUP_ATOMIC_SUNRISE) / sizeof(MELODY_STARTUP_ATOMIC_SUNRISE[0]);

inline constexpr Note MELODY_STARTUP_RADIANT_BOOTLOADER[] = {
  {1200, 120},
  {1400, 120},
  {1600, 120},
  {2000, 200},
  {1600, 150},
  {2400, 300},
};
inline constexpr size_t MELODY_STARTUP_RADIANT_BOOTLOADER_COUNT = sizeof(MELODY_STARTUP_RADIANT_BOOTLOADER) / sizeof(MELODY_STARTUP_RADIANT_BOOTLOADER[0]);

inline constexpr Note MELODY_STARTUP_FIELD_UNIT_ONLINE[] = {
  {523, 200},
  {659, 200},
  {784, 200},
  {1046, 400},
};
inline constexpr size_t MELODY_STARTUP_FIELD_UNIT_ONLINE_COUNT = sizeof(MELODY_STARTUP_FIELD_UNIT_ONLINE) / sizeof(MELODY_STARTUP_FIELD_UNIT_ONLINE[0]);

inline constexpr Note MELODY_STARTUP_WASTELAND_RISE[] = {
  {294, 200},
  {440, 200},
  {523, 250},
  {587, 400},
  {523, 200},
  {440, 200},
  {659, 300},
  {587, 500},
};
inline constexpr size_t MELODY_STARTUP_WASTELAND_RISE_COUNT = sizeof(MELODY_STARTUP_WASTELAND_RISE) / sizeof(MELODY_STARTUP_WASTELAND_RISE[0]);

inline constexpr Note MELODY_WARN_RADIATION_SPIKE[] = {
  {2200, 90},
  {2500, 90},
  {2200, 90},
  {2800, 180},
  {0, 70},
  {2800, 180},
};
inline constexpr size_t MELODY_WARN_RADIATION_SPIKE_COUNT = sizeof(MELODY_WARN_RADIATION_SPIKE) / sizeof(MELODY_WARN_RADIATION_SPIKE[0]);

inline constexpr Note MELODY_WARN_SYSTEM_FAULT[] = {
  {420, 260},
  {0, 90},
  {420, 260},
  {0, 90},
  {420, 420},
};
inline constexpr size_t MELODY_WARN_SYSTEM_FAULT_COUNT = sizeof(MELODY_WARN_SYSTEM_FAULT) / sizeof(MELODY_WARN_SYSTEM_FAULT[0]);

inline constexpr Note MELODY_WARN_LOW_POWER[] = {
  {900, 120},
  {0, 120},
  {900, 120},
  {0, 240},
  {700, 220},
};
inline constexpr size_t MELODY_WARN_LOW_POWER_COUNT = sizeof(MELODY_WARN_LOW_POWER) / sizeof(MELODY_WARN_LOW_POWER[0]);

void play_melody(const Note* notes, size_t count, int gap_ms = 30);
void play_warning_chime();
void play_error_chime();
