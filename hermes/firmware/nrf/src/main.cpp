#include <Arduino.h>
#include <Wire.h>

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_SGP30.h>
#include <Adafruit_SHT31.h>

#include "hermes_protocol.h"

#define ENABLE_USB_EXPORT 1

static const uint8_t OLED_ADDR_ENV = 0x3C;
static const uint8_t OLED_ADDR_ESP = 0x3D;
static const uint8_t SCREEN_WIDTH = 128;
static const uint8_t SCREEN_HEIGHT = 64;

static const int STATUS_LED_PIN = D1;
static const int PIN_BTN = D0;

static const uint32_t BTN_DEBOUNCE_MS = 25;
static const uint32_t BTN_DOUBLE_WINDOW_MS = 350;
static const uint32_t BTN_LONG_PRESS_MS = 800;
static const uint32_t FOCUS_MODE_DURATION_MS = 5 * 60 * 1000;

static const uint32_t LED_FAST_PERIOD_MS = 200;
static const uint32_t LED_SLOW_PERIOD_MS = 1000;
static const uint32_t LED_DOUBLE_PERIOD_MS = 2000;

static const uint32_t DISPLAY_INTERVAL_MS = 500;
static const uint32_t DISPLAY_FOCUS_INTERVAL_MS = 1000;

static Adafruit_SSD1306 displayEnv(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
static Adafruit_SSD1306 displayEsp(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
static Adafruit_SHT31 sht31 = Adafruit_SHT31();
static Adafruit_SGP30 sgp;

struct EspTelemetry {
  uint32_t up = 0;
  uint32_t n = 0;
  int rssi = RSSI_NOT_CONNECTED;
  uint32_t heap = 0;
  uint32_t psram = 0;
  float ct = NAN;
  float light = NAN;
  float scene = NAN;
};

static EspTelemetry espTelemetry;

static bool shtOk = false;
static bool sgpOk = false;
static float shtTempC = NAN;
static float shtHumidity = NAN;
static uint16_t sgpTvoc = 0;
static uint16_t sgpEco2 = 0;

static const int HIST_N = 120;
static float histTemp[HIST_N];
static float histRh[HIST_N];
static float histEco2[HIST_N];
static float histTvoc[HIST_N];
static float histLight[HIST_N];
static float histScene[HIST_N];
static int histIndex = 0;
static int histCount = 0;

enum DisplayMode {
  MODE_DEFAULT = 0,
  MODE_GRAPHS,
  MODE_LINK_DEBUG,
  MODE_ENV_BIG
};

static DisplayMode displayMode = MODE_DEFAULT;
static bool focusMode = false;
static uint32_t focusModeUntilMs = 0;

static char lastSensLine[160] = "SENS,<none>";
static uint32_t linesOk = 0;

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
static uint32_t lastSampleMs = 0;

static uint32_t lastParseFailSeen = 0;
static uint32_t parseErrorUntilMs = 0;
static uint32_t lastLedMs = 0;
static uint32_t refreshFlashUntilMs = 0;

static bool btnRawState = true;
static bool btnStableState = true;
static uint32_t btnLastChangeMs = 0;
static uint32_t btnPressStartMs = 0;
static bool btnLongHandled = false;
static bool shortPressPending = false;
static uint32_t shortPressMs = 0;

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
  if (strcmp(key, KEY_N) == 0) {
    espTelemetry.n = static_cast<uint32_t>(strtoul(value, nullptr, 10));
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
  if (strcmp(key, KEY_LIGHT) == 0) {
    espTelemetry.light = strtof(value, nullptr);
    return true;
  }
  if (strcmp(key, KEY_SCENE) == 0) {
    espTelemetry.scene = strtof(value, nullptr);
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

static void storeSensLine(const char *line) {
  strncpy(lastSensLine, line, sizeof(lastSensLine) - 1);
  lastSensLine[sizeof(lastSensLine) - 1] = '\0';
}

static uint32_t getAgeMs(uint32_t now) {
  if (lastLineMs == 0) {
    return 0xFFFFFFFF;
  }
  return now - lastLineMs;
}

static void drawCornerFlags(Adafruit_SSD1306 &display, uint32_t now) {
  if (!focusMode && now >= refreshFlashUntilMs) {
    return;
  }

  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(114, 0);
  if (focusMode) {
    display.print('F');
  }
  if (now < refreshFlashUntilMs) {
    display.print('R');
  }
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
        if (strncmp(rxBuffer, SENS_PREFIX, strlen(SENS_PREFIX)) == 0) {
          storeSensLine(rxBuffer);
          if (parseTelemetryLine(rxBuffer)) {
            lastLineMs = millis();
            linesOk++;
          } else {
            parseFail++;
          }
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

static void formatFloat(char *buffer, size_t size, float value, int precision) {
  if (isnan(value)) {
    snprintf(buffer, size, "nan");
    return;
  }
  char format[8];
  snprintf(format, sizeof(format), "%%.%df", precision);
  snprintf(buffer, size, format, value);
}

static void exportUsbLine(uint32_t now) {
#if ENABLE_USB_EXPORT
  char tBuf[16];
  char rhBuf[16];
  char ctBuf[16];
  char lightBuf[16];
  char sceneBuf[16];

  formatFloat(tBuf, sizeof(tBuf), shtTempC, 1);
  formatFloat(rhBuf, sizeof(rhBuf), shtHumidity, 1);
  formatFloat(ctBuf, sizeof(ctBuf), espTelemetry.ct, 2);
  formatFloat(lightBuf, sizeof(lightBuf), espTelemetry.light, 2);
  formatFloat(sceneBuf, sizeof(sceneBuf), espTelemetry.scene, 2);

  const uint32_t ageMs = getAgeMs(now);
  char line[320];
  snprintf(
      line,
      sizeof(line),
      "LOG,t=%s,rh=%s,eco2=%u,tvoc=%u,n=%lu,rssi=%d,heap=%lu,psram=%lu,ct=%s,light=%s,scene=%s,bps=%lu,age=%lu,pf=%lu\n",
      tBuf,
      rhBuf,
      static_cast<unsigned>(sgpEco2),
      static_cast<unsigned>(sgpTvoc),
      static_cast<unsigned long>(espTelemetry.n),
      espTelemetry.rssi,
      static_cast<unsigned long>(espTelemetry.heap),
      static_cast<unsigned long>(espTelemetry.psram),
      ctBuf,
      lightBuf,
      sceneBuf,
      static_cast<unsigned long>(bps),
      static_cast<unsigned long>(ageMs),
      static_cast<unsigned long>(parseFail));
  Serial.print(line);
#else
  (void)now;
#endif
}

static void pushSample(float tC, float rh, float eco2, float tvoc, float light, float scene) {
  histTemp[histIndex] = tC;
  histRh[histIndex] = rh;
  histEco2[histIndex] = eco2;
  histTvoc[histIndex] = tvoc;
  histLight[histIndex] = light;
  histScene[histIndex] = scene;

  histIndex = (histIndex + 1) % HIST_N;
  if (histCount < HIST_N) {
    histCount++;
  }
}

static void updateHistory(uint32_t now) {
  if (now - lastSampleMs < 1000) {
    return;
  }
  lastSampleMs = now;
  pushSample(
      shtTempC,
      shtHumidity,
      static_cast<float>(sgpEco2),
      static_cast<float>(sgpTvoc),
      espTelemetry.light,
      espTelemetry.scene);
  exportUsbLine(now);
}

static void initHistory() {
  for (int i = 0; i < HIST_N; i++) {
    histTemp[i] = NAN;
    histRh[i] = NAN;
    histEco2[i] = NAN;
    histTvoc[i] = NAN;
    histLight[i] = NAN;
    histScene[i] = NAN;
  }
}

static bool hasValidSeries(const float *series, int count) {
  for (int i = 0; i < count; i++) {
    if (!isnan(series[i])) {
      return true;
    }
  }
  return false;
}

static void drawSparkline(
    int x,
    int y,
    int w,
    int h,
    const float *series,
    int count,
    int head,
    Adafruit_SSD1306 &display) {
  if (count <= 1 || w <= 1 || h <= 1) {
    return;
  }

  bool hasValue = false;
  float minVal = 0.0f;
  float maxVal = 0.0f;
  for (int i = 0; i < count; i++) {
    const int idx = (head + i) % HIST_N;
    const float v = series[idx];
    if (isnan(v)) {
      continue;
    }
    if (!hasValue) {
      minVal = v;
      maxVal = v;
      hasValue = true;
    } else {
      if (v < minVal) {
        minVal = v;
      }
      if (v > maxVal) {
        maxVal = v;
      }
    }
  }

  if (!hasValue) {
    return;
  }

  if (fabs(maxVal - minVal) < 0.0001f) {
    maxVal += 1.0f;
    minVal -= 1.0f;
  }

  int prevX = -1;
  int prevY = -1;
  for (int i = 0; i < count; i++) {
    const int idx = (head + i) % HIST_N;
    const float v = series[idx];
    if (isnan(v)) {
      prevX = -1;
      prevY = -1;
      continue;
    }

    const int xPos = x + ((count > 1) ? (i * (w - 1)) / (count - 1) : 0);
    float norm = (v - minVal) / (maxVal - minVal);
    if (norm < 0.0f) {
      norm = 0.0f;
    } else if (norm > 1.0f) {
      norm = 1.0f;
    }
    const int yPos = y + (h - 1) - static_cast<int>(lroundf(norm * (h - 1)));

    if (prevX >= 0) {
      display.drawLine(prevX, prevY, xPos, yPos, SSD1306_WHITE);
    }
    prevX = xPos;
    prevY = yPos;
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

  drawCornerFlags(displayEnv, millis());

  displayEnv.display();
}

static void drawEspDisplay(uint32_t now) {
  displayEsp.clearDisplay();
  displayEsp.setTextSize(1);
  displayEsp.setTextColor(SSD1306_WHITE);

  const uint32_t ageMs = getAgeMs(now);

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

  drawCornerFlags(displayEsp, now);

  displayEsp.display();
}

static void drawLinkStatsDisplay(uint32_t now) {
  displayEsp.clearDisplay();
  displayEsp.setTextSize(1);
  displayEsp.setTextColor(SSD1306_WHITE);

  const uint32_t ageMs = getAgeMs(now);

  displayEsp.setCursor(0, 0);
  displayEsp.print("LINK");

  displayEsp.setCursor(0, 8);
  displayEsp.print("bps ");
  displayEsp.print(bps);

  displayEsp.setCursor(0, 16);
  displayEsp.print("age ");
  displayEsp.print(ageMs);

  displayEsp.setCursor(0, 24);
  displayEsp.print("pf ");
  displayEsp.print(parseFail);

  displayEsp.setCursor(0, 32);
  displayEsp.print("ok ");
  displayEsp.print(linesOk);

  drawCornerFlags(displayEsp, now);

  displayEsp.display();
}

static void drawLinkDebugDisplay() {
  displayEnv.clearDisplay();
  displayEnv.setTextSize(1);
  displayEnv.setTextColor(SSD1306_WHITE);

  const size_t len = strlen(lastSensLine);
  size_t idx = 0;
  for (uint8_t row = 0; row < 7 && idx < len; row++) {
    displayEnv.setCursor(0, row * 8);
    for (uint8_t col = 0; col < 21 && idx < len; col++) {
      displayEnv.write(lastSensLine[idx++]);
    }
  }

  drawCornerFlags(displayEnv, millis());

  displayEnv.display();
}

static void drawEnvBigDisplay() {
  displayEnv.clearDisplay();
  displayEnv.setTextSize(2);
  displayEnv.setTextColor(SSD1306_WHITE);

  displayEnv.setCursor(0, 0);
  displayEnv.print("T ");
  if (isnan(shtTempC)) {
    displayEnv.print("nan");
  } else {
    displayEnv.print(shtTempC, 1);
  }

  displayEnv.setCursor(0, 16);
  displayEnv.print("RH ");
  if (isnan(shtHumidity)) {
    displayEnv.print("nan");
  } else {
    displayEnv.print(shtHumidity, 1);
  }

  drawCornerFlags(displayEnv, millis());

  displayEnv.display();
}

static void drawEnvBigLeftDisplay() {
  displayEsp.clearDisplay();
  displayEsp.setTextSize(2);
  displayEsp.setTextColor(SSD1306_WHITE);

  displayEsp.setCursor(0, 0);
  displayEsp.print("CO2 ");
  displayEsp.print(sgpEco2);

  displayEsp.setCursor(0, 16);
  displayEsp.print("TVOC ");
  displayEsp.print(sgpTvoc);

  drawCornerFlags(displayEsp, millis());

  displayEsp.display();
}

static void drawGraphsDisplay(uint32_t now) {
  displayEnv.clearDisplay();
  displayEnv.setTextSize(1);
  displayEnv.setTextColor(SSD1306_WHITE);

  displayEnv.setCursor(0, 0);
  displayEnv.print("T ");
  if (isnan(shtTempC)) {
    displayEnv.print("nan");
  } else {
    displayEnv.print(shtTempC, 1);
  }
  displayEnv.print(" RH ");
  if (isnan(shtHumidity)) {
    displayEnv.print("nan");
  } else {
    displayEnv.print(shtHumidity, 1);
  }

  displayEnv.setCursor(0, 8);
  displayEnv.print("L ");
  if (isnan(espTelemetry.light)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(espTelemetry.light, 2);
  }
  displayEnv.print(" S ");
  if (isnan(espTelemetry.scene)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(espTelemetry.scene, 2);
  }

  drawSparkline(0, 16, SCREEN_WIDTH, 24, histTemp, histCount, histIndex, displayEnv);
  drawSparkline(0, 40, SCREEN_WIDTH, 24, histRh, histCount, histIndex, displayEnv);

  drawCornerFlags(displayEnv, now);
  displayEnv.display();

  displayEsp.clearDisplay();
  displayEsp.setTextSize(1);
  displayEsp.setTextColor(SSD1306_WHITE);

  displayEsp.setCursor(0, 0);
  displayEsp.print("eCO2 ");
  displayEsp.print(sgpEco2);

  displayEsp.setCursor(0, 8);
  displayEsp.print("TVOC ");
  displayEsp.print(sgpTvoc);

  drawSparkline(0, 16, SCREEN_WIDTH, 24, histEco2, histCount, histIndex, displayEsp);
  drawSparkline(0, 40, SCREEN_WIDTH, 24, histTvoc, histCount, histIndex, displayEsp);

  drawCornerFlags(displayEsp, now);
  displayEsp.display();
}

static void renderDisplays(uint32_t now) {
  switch (displayMode) {
    case MODE_GRAPHS:
      drawGraphsDisplay(now);
      break;
    case MODE_LINK_DEBUG:
      drawLinkDebugDisplay();
      drawLinkStatsDisplay(now);
      break;
    case MODE_ENV_BIG:
      drawEnvBigDisplay();
      drawEnvBigLeftDisplay();
      break;
    case MODE_DEFAULT:
    default:
      drawEnvDisplay();
      drawEspDisplay(now);
      break;
  }
}

static void refreshNow(uint32_t now) {
  refreshFlashUntilMs = now + 500;
  lastDisplayMs = now;
  renderDisplays(now);
  Serial.println("BTN: refresh now");
}

static void handleShortPress(uint32_t now) {
  switch (displayMode) {
    case MODE_DEFAULT:
      displayMode = MODE_GRAPHS;
      break;
    case MODE_GRAPHS:
      displayMode = MODE_LINK_DEBUG;
      break;
    case MODE_LINK_DEBUG:
      displayMode = MODE_ENV_BIG;
      break;
    case MODE_ENV_BIG:
    default:
      displayMode = MODE_DEFAULT;
      break;
  }
  lastDisplayMs = now;
  renderDisplays(now);
}

static void handleDoublePress(uint32_t now) {
  refreshNow(now);
}

static void handleLongPress(uint32_t now) {
  focusMode = !focusMode;
  if (focusMode) {
    focusModeUntilMs = now + FOCUS_MODE_DURATION_MS;
    Serial.println("BTN: focus ON");
  } else {
    focusModeUntilMs = 0;
    Serial.println("BTN: focus OFF");
  }
}

static void updateButton(uint32_t now) {
  const bool rawLevel = digitalRead(PIN_BTN);
  if (rawLevel != btnRawState) {
    btnRawState = rawLevel;
    btnLastChangeMs = now;
  }

  if ((now - btnLastChangeMs) >= BTN_DEBOUNCE_MS && rawLevel != btnStableState) {
    btnStableState = rawLevel;
    if (!btnStableState) {
      btnPressStartMs = now;
      btnLongHandled = false;
    } else {
      if (!btnLongHandled) {
        if (shortPressPending && (now - shortPressMs) <= BTN_DOUBLE_WINDOW_MS) {
          shortPressPending = false;
          handleDoublePress(now);
        } else {
          shortPressPending = true;
          shortPressMs = now;
        }
      }
    }
  }

  if (!btnLongHandled && !btnStableState && (now - btnPressStartMs) >= BTN_LONG_PRESS_MS) {
    btnLongHandled = true;
    shortPressPending = false;
    handleLongPress(now);
  }

  if (shortPressPending && (now - shortPressMs) > BTN_DOUBLE_WINDOW_MS) {
    shortPressPending = false;
    handleShortPress(now);
  }
}

static void updateLed(uint32_t now) {
  if (now - lastLedMs < 50) {
    return;
  }
  lastLedMs = now;

  if (parseFail != lastParseFailSeen) {
    lastParseFailSeen = parseFail;
    parseErrorUntilMs = now + LED_DOUBLE_PERIOD_MS;
  }

  const bool parseErrorActive = now < parseErrorUntilMs;
  const uint32_t ageMs = getAgeMs(now);
  const bool hasLink = (linesOk > 0) && (ageMs <= 1500);

  bool ledOn = false;
  if (parseErrorActive) {
    const uint32_t phase = now % LED_DOUBLE_PERIOD_MS;
    ledOn = (phase < 100) || (phase >= 200 && phase < 300);
  } else if (!hasLink) {
    ledOn = (now % LED_FAST_PERIOD_MS) < (LED_FAST_PERIOD_MS / 2);
  } else if (espTelemetry.rssi == RSSI_NOT_CONNECTED) {
    ledOn = (now % LED_SLOW_PERIOD_MS) < (LED_SLOW_PERIOD_MS / 2);
  } else {
    ledOn = true;
  }

  digitalWrite(STATUS_LED_PIN, ledOn ? HIGH : LOW);
}

static void updateDisplays(uint32_t now) {
  const uint32_t interval = focusMode ? DISPLAY_FOCUS_INTERVAL_MS : DISPLAY_INTERVAL_MS;
  if (now - lastDisplayMs < interval) {
    return;
  }
  lastDisplayMs = now;
  renderDisplays(now);
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
  Serial1.setPins(D7, D6);
  Serial1.begin(UART_BAUD);
  Wire.begin();

  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);
  pinMode(PIN_BTN, INPUT_PULLUP);
  btnRawState = digitalRead(PIN_BTN);
  btnStableState = btnRawState;
  btnLastChangeMs = millis();

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

  initHistory();

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
  updateHistory(now);
  updateButton(now);
  updateLed(now);
  if (focusMode && now >= focusModeUntilMs) {
    focusMode = false;
    focusModeUntilMs = 0;
  }
  updateDisplays(now);
  heartbeat(now);
}
