#include <Arduino.h>
#include <Wire.h>

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_SGP30.h>
#include <Adafruit_SHT31.h>

#include "hermes_protocol.h"

static const uint8_t OLED_ADDR_ENV = 0x3C;
static const uint8_t OLED_ADDR_ESP = 0x3D;
static const uint8_t SCREEN_WIDTH = 128;
static const uint8_t SCREEN_HEIGHT = 64;

static Adafruit_SSD1306 displayEnv(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
static Adafruit_SSD1306 displayEsp(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
static Adafruit_SHT31 sht31 = Adafruit_SHT31();
static Adafruit_SGP30 sgp;

struct EspTelemetry {
  uint32_t up = 0;
  int rssi = RSSI_NOT_CONNECTED;
  uint32_t heap = 0;
  uint32_t psram = 0;
  float ct = NAN;
};

static EspTelemetry espTelemetry;

static bool shtOk = false;
static bool sgpOk = false;
static float shtTempC = NAN;
static float shtHumidity = NAN;
static uint16_t sgpTvoc = 0;
static uint16_t sgpEco2 = 0;

static char rxBuffer[160];
static size_t rxLen = 0;

static uint32_t lastLineMs = 0;
static uint32_t parseFail = 0;
static uint32_t byteCount = 0;
static uint32_t lastBpsMs = 0;
static uint32_t bps = 0;

static uint32_t lastSensorMs = 0;
static uint32_t lastDisplayMs = 0;
static uint32_t lastHeartbeatMs = 0;

static bool parseKeyValue(char *pair) {
  char *equals = strchr(pair, '=');
  if (!equals) {
    return false;
  }

  *equals = '\0';
  const char *key = pair;
  const char *value = equals + 1;

  if (strcmp(key, KEY_UP) == 0) {
    espTelemetry.up = static_cast<uint32_t>(strtoul(value, nullptr, 10));
    return true;
  }
  if (strcmp(key, KEY_RSSI) == 0) {
    espTelemetry.rssi = static_cast<int>(strtol(value, nullptr, 10));
    return true;
  }
  if (strcmp(key, KEY_HEAP) == 0) {
    espTelemetry.heap = static_cast<uint32_t>(strtoul(value, nullptr, 10));
    return true;
  }
  if (strcmp(key, KEY_PSRAM) == 0) {
    espTelemetry.psram = static_cast<uint32_t>(strtoul(value, nullptr, 10));
    return true;
  }
  if (strcmp(key, KEY_CT) == 0) {
    espTelemetry.ct = strtof(value, nullptr);
    return true;
  }

  return false;
}

static bool parseTelemetryLine(char *line) {
  const size_t prefixLen = strlen(SENS_PREFIX);
  if (strncmp(line, SENS_PREFIX, prefixLen) != 0) {
    return false;
  }

  char *payload = line + prefixLen;
  char *savePtr = nullptr;
  char *token = strtok_r(payload, ",", &savePtr);
  bool malformed = false;
  bool sawToken = false;
  while (token) {
    sawToken = true;
    if (!strchr(token, '=')) {
      malformed = true;
      break;
    }

    parseKeyValue(token);
    token = strtok_r(nullptr, ",", &savePtr);
  }

  if (malformed || !sawToken) {
    return false;
  }
  return true;
}

static void handleSerial1() {
  while (Serial1.available() > 0) {
    const char c = static_cast<char>(Serial1.read());
    byteCount++;

    if (c == '\r') {
      continue;
    }

    if (c == '\n') {
      rxBuffer[rxLen] = '\0';
      if (rxLen > 0) {
        if (parseTelemetryLine(rxBuffer)) {
          lastLineMs = millis();
        } else {
          parseFail++;
        }
      }
      rxLen = 0;
      continue;
    }

    if (rxLen + 1 < sizeof(rxBuffer)) {
      rxBuffer[rxLen++] = c;
    } else {
      rxLen = 0;
      parseFail++;
    }
  }
}

static void updateBps(uint32_t now) {
  if (lastBpsMs == 0) {
    lastBpsMs = now;
  }
  const uint32_t elapsed = now - lastBpsMs;
  if (elapsed >= 1000) {
    bps = (elapsed > 0) ? (byteCount * 1000 / elapsed) : 0;
    byteCount = 0;
    lastBpsMs = now;
  }
}

static void readSensors(uint32_t now) {
  if (now - lastSensorMs < 1000) {
    return;
  }
  lastSensorMs = now;

  if (shtOk) {
    shtTempC = sht31.readTemperature();
    shtHumidity = sht31.readHumidity();
  }

  if (sgpOk) {
    if (sgp.IAQmeasure()) {
      sgpTvoc = sgp.TVOC;
      sgpEco2 = sgp.eCO2;
    }
  }
}

static void drawEnvDisplay() {
  displayEnv.clearDisplay();
  displayEnv.setTextSize(1);
  displayEnv.setTextColor(SSD1306_WHITE);

  displayEnv.setCursor(0, 0);
  displayEnv.print("ENV");

  displayEnv.setCursor(0, 8);
  displayEnv.print("T ");
  if (isnan(shtTempC)) {
    displayEnv.print("nan");
  } else {
    displayEnv.print(shtTempC, 1);
  }
  displayEnv.print("C H ");
  if (isnan(shtHumidity)) {
    displayEnv.print("nan");
  } else {
    displayEnv.print(shtHumidity, 1);
  }

  displayEnv.setCursor(0, 16);
  displayEnv.print("eCO2 ");
  displayEnv.print(sgpEco2);

  displayEnv.setCursor(0, 24);
  displayEnv.print("TVOC ");
  displayEnv.print(sgpTvoc);

  displayEnv.setCursor(0, 32);
  displayEnv.print("SHT ");
  displayEnv.print(shtOk ? "ok" : "err");

  displayEnv.setCursor(0, 40);
  displayEnv.print("SGP ");
  displayEnv.print(sgpOk ? "ok" : "err");

  displayEnv.display();
}

static void drawEspDisplay(uint32_t now) {
  displayEsp.clearDisplay();
  displayEsp.setTextSize(1);
  displayEsp.setTextColor(SSD1306_WHITE);

  const uint32_t ageMs = (lastLineMs == 0) ? 0 : (now - lastLineMs);

  displayEsp.setCursor(0, 0);
  displayEsp.print("ESP up ");
  displayEsp.print(espTelemetry.up);

  displayEsp.setCursor(0, 8);
  displayEsp.print("rssi ");
  displayEsp.print(espTelemetry.rssi);

  displayEsp.setCursor(0, 16);
  displayEsp.print("heap ");
  displayEsp.print(espTelemetry.heap);

  displayEsp.setCursor(0, 24);
  displayEsp.print("psram ");
  displayEsp.print(espTelemetry.psram);

  displayEsp.setCursor(0, 32);
  displayEsp.print("ct ");
  if (isnan(espTelemetry.ct)) {
    displayEsp.print("nan");
  } else {
    displayEsp.print(espTelemetry.ct, 1);
  }

  displayEsp.setCursor(0, 40);
  displayEsp.print("bps ");
  displayEsp.print(bps);

  displayEsp.setCursor(0, 48);
  displayEsp.print("age ");
  displayEsp.print(ageMs);
  displayEsp.print(" pf ");
  displayEsp.print(parseFail);

  displayEsp.display();
}

static void updateDisplays(uint32_t now) {
  if (now - lastDisplayMs < 500) {
    return;
  }
  lastDisplayMs = now;
  drawEnvDisplay();
  drawEspDisplay(now);
}

static void heartbeat(uint32_t now) {
  if (now - lastHeartbeatMs < 2000) {
    return;
  }
  lastHeartbeatMs = now;
  Serial.print("HB bps=");
  Serial.print(bps);
  Serial.print(" up=");
  Serial.print(espTelemetry.up);
  Serial.print(" rssi=");
  Serial.print(espTelemetry.rssi);
  Serial.print(" ct=");
  if (isnan(espTelemetry.ct)) {
    Serial.println("nan");
  } else {
    Serial.println(espTelemetry.ct, 1);
  }
}

void setup() {
  Serial.begin(UART_BAUD);
  Serial1.begin(UART_BAUD);
  Wire.begin();

  if (!displayEnv.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR_ENV)) {
    Serial.println("ENV OLED init failed");
  }
  if (!displayEsp.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR_ESP)) {
    Serial.println("ESP OLED init failed");
  }

  shtOk = sht31.begin(0x44);
  if (!shtOk) {
    shtOk = sht31.begin(0x45);
  }

  sgpOk = sgp.begin();

  displayEnv.clearDisplay();
  displayEsp.clearDisplay();
  displayEnv.display();
  displayEsp.display();
}

void loop() {
  const uint32_t now = millis();
  handleSerial1();
  updateBps(now);
  readSensors(now);
  updateDisplays(now);
  heartbeat(now);
}
