#ifndef HERMES_PROTOCOL_H
#define HERMES_PROTOCOL_H

static const uint32_t UART_BAUD = 115200;

static const char SENS_PREFIX[] = "SENS,";

static const char KEY_UP[] = "up";
static const char KEY_RSSI[] = "rssi";
static const char KEY_HEAP[] = "heap";
static const char KEY_PSRAM[] = "psram";
static const char KEY_CT[] = "ct";
static const char KEY_LIGHT[] = "light";
static const char KEY_SCENE[] = "scene";
static const char KEY_MIC[] = "mic";
static const char KEY_MICPK[] = "micpk";
static const char KEY_MICNF[] = "micnf";
static const char KEY_N[] = "n";

static const int RSSI_NOT_CONNECTED = 999;

#endif  // HERMES_PROTOCOL_H
