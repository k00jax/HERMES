#include <Arduino.h>
#include <Wire.h>
#include <math.h>

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_SGP30.h>
#include <Adafruit_SHT31.h>
#include <PDM.h>

#include "hermes_protocol.h"

#define ENABLE_USB_EXPORT 1
#define ENABLE_ESP_CMD 1

static const uint8_t OLED_ADDR_ENV = 0x3C;
static const uint8_t OLED_ADDR_ESP = 0x3D;
static const uint8_t SCREEN_WIDTH = 128;
static const uint8_t SCREEN_HEIGHT = 64;

static const int STATUS_LED_PIN = D1;
static const int DEBUG_LED_PIN = D3;
static const int PIN_BTN = D0;
static const int PIN_SW = D2;

static const uint32_t BTN_DEBOUNCE_MS = 25;
static const uint32_t BTN_DOUBLE_WINDOW_MS = 350;
static const uint32_t BTN_LONG_PRESS_MS = 800;
static const uint32_t FOCUS_MODE_DURATION_MS = 5 * 60 * 1000;

static const uint32_t LED_FAST_PERIOD_MS = 200;
static const uint32_t LED_SLOW_PERIOD_MS = 1000;
static const uint32_t LED_DOUBLE_PERIOD_MS = 2000;
static const float MIC_SPIKE_DELTA = 0.10f;
static const float MIC_SUSTAIN_DELTA = 0.05f;
static const uint32_t MIC_SUSTAIN_MS = 3000;
static const uint32_t MIC_SPIKE_COOLDOWN_MS = 1500;
static const uint32_t MIC_SUSTAIN_COOLDOWN_MS = 5000;
static const int ROC_WINDOW_SAMPLES = 60;
static const float BASELINE_ALPHA = 0.01f;
static const uint32_t AIR_EVENT_COOLDOWN_MS = 20000;
static const uint32_t LIGHT_EVENT_COOLDOWN_MS = 20000;
static const uint32_t NOISE_EVENT_COOLDOWN_MS = 20000;
static const uint32_t DISPLAY_FOCUS_INTERVAL_MS = 1200;
static const uint32_t PDM_SAMPLE_RATE = 16000;
static const uint32_t PDM_UPDATE_MS = 100;
static const float MIC_NOISE_ALPHA = 0.01f;
static const size_t PDM_BUFFER_SAMPLES = 256;
static const int TIMEZONE_OFFSET_MIN = -360;
static const char FW_STRING[] = "nrf";
static const char BUILD_STRING[] = __DATE__;

static const int HIST_N = 120;

enum UiStack {
  UI_DEBUG = 0,
  UI_USER
};

static const int DEBUG_PAGE_COUNT = 3;
static const int USER_PAGE_COUNT = 5;
static const uint32_t DISPLAY_DEBUG_INTERVAL_MS = 400;
static const uint32_t DISPLAY_USER_INTERVAL_MS = 800;

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
  float mic = NAN;
  float micpk = NAN;
  float micnf = NAN;
  uint32_t ntp = 0;
  int camok = 0;
  int camerr = 0;
  int micok = 0;
  int micerr = 0;
  int wifist = 0;
  int camaddr = -1;
};

static EspTelemetry espTelemetry;

static bool shtOk = false;
static bool sgpOk = false;
static float shtTempC = NAN;
static float shtHumidity = NAN;
static uint16_t sgpTvoc = 0;
static uint16_t sgpEco2 = 0;

static float histTemp[HIST_N];
static float histRh[HIST_N];
static float histEco2[HIST_N];
static float histTvoc[HIST_N];
static float histLight[HIST_N];
static float histScene[HIST_N];
static float histMic[HIST_N];
static int histIndex = 0;
static int histCount = 0;

struct RocRing {
  uint32_t ms[ROC_WINDOW_SAMPLES];
  float v[ROC_WINDOW_SAMPLES];
  int index = 0;
  int count = 0;
};

static RocRing rocTempRing;
static RocRing rocRhRing;
static RocRing rocEco2Ring;
static RocRing rocTvocRing;
static RocRing rocLightRing;
static RocRing rocMicRing;

static float rocTemp = NAN;
static float rocRh = NAN;
static float rocEco2 = NAN;
static float rocTvoc = NAN;
static float rocLight = NAN;
static float rocMic = NAN;

static float baseTemp = NAN;
static float baseRh = NAN;
static float baseEco2 = NAN;
static float baseTvoc = NAN;
static float baseLight = NAN;
static float baseMic = NAN;

static float deltaTemp = NAN;
static float deltaRh = NAN;
static float deltaEco2 = NAN;
static float deltaTvoc = NAN;
static float deltaLight = NAN;
static float deltaMic = NAN;

static bool localMicOk = false;
static int localMicErr = 0;
static float localMicRms = NAN;
static float localMicPeak = NAN;
static float localMicNoise = NAN;
static bool localMicPrimed = false;
static int16_t pdmSamples[PDM_BUFFER_SAMPLES];
static volatile size_t pdmBytesReady = 0;
static volatile bool pdmReady = false;
static uint32_t lastPdmUpdateMs = 0;

static UiStack uiStack = UI_USER;
static int debugPageIndex = 0;
static int userPageIndex = 0;
static bool focusMode = false;
static uint32_t focusModeUntilMs = 0;
static bool debugMode = false;
static bool usbExportEnabled = false;

static char lastSensLine[320] = "SENS,<none>";
static uint32_t linesOk = 0;

static char rxBuffer[320];
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
static bool swState = true;
static uint32_t swLastChangeMs = 0;

static uint32_t lastMicEventLineMs = 0;
static uint32_t micSustainStartMs = 0;
static uint32_t micSpikeCooldownUntilMs = 0;
static uint32_t micSustainCooldownUntilMs = 0;
static uint8_t airRisingCount = 0;
static uint8_t airFallingCount = 0;
static uint32_t airRisingCooldownUntilMs = 0;
static uint32_t airFallingCooldownUntilMs = 0;
static uint32_t lightDropCooldownUntilMs = 0;
static uint32_t noiseSpikeCooldownUntilMs = 0;

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
  if (strcmp(key, KEY_MIC) == 0) {
    espTelemetry.mic = strtof(value, nullptr);
    return true;
  }
  if (strcmp(key, KEY_MICPK) == 0) {
    espTelemetry.micpk = strtof(value, nullptr);
    return true;
  }
  if (strcmp(key, KEY_MICNF) == 0) {
    espTelemetry.micnf = strtof(value, nullptr);
    return true;
  }
  if (strcmp(key, KEY_NTP) == 0) {
    espTelemetry.ntp = static_cast<uint32_t>(strtoul(value, nullptr, 10));
    return true;
  }
  if (strcmp(key, KEY_CAMOK) == 0) {
    espTelemetry.camok = static_cast<int>(strtol(value, nullptr, 10));
    return true;
  }
  if (strcmp(key, KEY_CAMERR) == 0) {
    espTelemetry.camerr = static_cast<int>(strtol(value, nullptr, 10));
    return true;
  }
  if (strcmp(key, KEY_MICOK) == 0) {
    espTelemetry.micok = static_cast<int>(strtol(value, nullptr, 10));
    return true;
  }
  if (strcmp(key, KEY_MICERR) == 0) {
    espTelemetry.micerr = static_cast<int>(strtol(value, nullptr, 10));
    return true;
  }
  if (strcmp(key, KEY_WIFIST) == 0) {
    espTelemetry.wifist = static_cast<int>(strtol(value, nullptr, 10));
    return true;
  }
  if (strcmp(key, KEY_CAMADDR) == 0) {
    espTelemetry.camaddr = static_cast<int>(strtol(value, nullptr, 10));
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

static void onPdmData() {
  const int bytes = PDM.available();
  if (bytes <= 0) {
    return;
  }
  const size_t readBytes = (bytes > static_cast<int>(sizeof(pdmSamples)))
      ? sizeof(pdmSamples)
      : static_cast<size_t>(bytes);
  PDM.read(pdmSamples, readBytes);
  pdmBytesReady = readBytes;
  pdmReady = true;
}

static void initLocalMic() {
  PDM.onReceive(onPdmData);
  if (!PDM.begin(1, PDM_SAMPLE_RATE)) {
    Serial.println("PDM init failed");
    localMicOk = false;
    localMicErr = 1;
    return;
  }
  localMicOk = true;
  localMicErr = 0;
  localMicRms = NAN;
  localMicPeak = NAN;
  localMicNoise = NAN;
  localMicPrimed = false;
}

static void updateLocalMic(uint32_t now) {
  if (!localMicOk || (now - lastPdmUpdateMs) < PDM_UPDATE_MS) {
    return;
  }
  if (!pdmReady) {
    return;
  }
  pdmReady = false;
  lastPdmUpdateMs = now;

  const size_t sampleCount = pdmBytesReady / sizeof(int16_t);
  if (sampleCount == 0) {
    return;
  }

  int32_t peak = 0;
  int64_t sumSquares = 0;
  for (size_t i = 0; i < sampleCount; i++) {
    const int32_t v = pdmSamples[i];
    const int32_t av = abs(v);
    if (av > peak) {
      peak = av;
    }
    sumSquares += static_cast<int64_t>(v) * static_cast<int64_t>(v);
  }

  const float meanSquare = static_cast<float>(sumSquares) / static_cast<float>(sampleCount);
  float rms = sqrtf(meanSquare) / 32768.0f;
  float pk = static_cast<float>(peak) / 32768.0f;
  if (rms < 0.0f) {
    rms = 0.0f;
  } else if (rms > 1.0f) {
    rms = 1.0f;
  }
  if (pk < 0.0f) {
    pk = 0.0f;
  } else if (pk > 1.0f) {
    pk = 1.0f;
  }

  localMicRms = rms;
  localMicPeak = pk;
  if (isnan(localMicNoise)) {
    localMicNoise = rms;
  } else {
    localMicNoise += MIC_NOISE_ALPHA * (rms - localMicNoise);
  }

  if (!localMicPrimed) {
    localMicPrimed = true;
    Serial.println("PDM ok");
  }
}

static bool isValid(float value) {
  return (value == value) && (fabsf(value) < 3.4e38f);
}

static float computeRoc(float current, float past, float dtMin) {
  if (!isValid(current) || !isValid(past) || dtMin <= 0.0f) {
    return NAN;
  }
  return (current - past) / dtMin;
}

static void updateBaseline(float current, float *baseline) {
  if (!isValid(current)) {
    return;
  }
  if (!isValid(*baseline)) {
    *baseline = current;
  } else {
    *baseline = (*baseline * (1.0f - BASELINE_ALPHA)) + (current * BASELINE_ALPHA);
  }
}

static float computeDelta(float current, float baseline) {
  if (!isValid(current) || !isValid(baseline)) {
    return NAN;
  }
  return current - baseline;
}

static void resetRocRing(RocRing *ring) {
  for (int i = 0; i < ROC_WINDOW_SAMPLES; i++) {
    ring->ms[i] = 0;
    ring->v[i] = NAN;
  }
  ring->index = 0;
  ring->count = 0;
}

static float computeRingRoc(const RocRing *ring, float current, uint32_t now) {
  if (!isValid(current) || ring->count < ROC_WINDOW_SAMPLES || ring->ms[ring->index] == 0) {
    return NAN;
  }
  const float dtMin = static_cast<float>(now - ring->ms[ring->index]) / 60000.0f;
  return computeRoc(current, ring->v[ring->index], dtMin);
}

static void updateRocRing(RocRing *ring, uint32_t now, float current, float *rocOut) {
  if (!isValid(current)) {
    *rocOut = NAN;
    resetRocRing(ring);
    return;
  }

  *rocOut = computeRingRoc(ring, current, now);
  ring->ms[ring->index] = now;
  ring->v[ring->index] = current;
  ring->index = (ring->index + 1) % ROC_WINDOW_SAMPLES;
  if (ring->count < ROC_WINDOW_SAMPLES) {
    ring->count++;
  }
}

static void updateRocRingOptional(RocRing *ring, uint32_t now, float current, float *rocOut) {
  if (!isValid(current)) {
    *rocOut = NAN;
    return;
  }

  *rocOut = computeRingRoc(ring, current, now);
  ring->ms[ring->index] = now;
  ring->v[ring->index] = current;
  ring->index = (ring->index + 1) % ROC_WINDOW_SAMPLES;
  if (ring->count < ROC_WINDOW_SAMPLES) {
    ring->count++;
  }
}

static void exportUsbLine(uint32_t now) {
#if ENABLE_USB_EXPORT
  if (!usbExportEnabled) {
    return;
  }
  char tBuf[16];
  char rhBuf[16];
  char ctBuf[16];
  char lightBuf[16];
  char sceneBuf[16];
  char micBuf[16];
  char micPkBuf[16];
  char micNfBuf[16];
  char rtBuf[16];
  char rrhBuf[16];
  char rco2Buf[16];
  char rtvBuf[16];
  char rliBuf[16];
  char rmicBuf[16];
  char btBuf[16];
  char brhBuf[16];
  char bco2Buf[16];
  char btvBuf[16];
  char bliBuf[16];
  char bmicBuf[16];
  char dtBuf[16];
  char drhBuf[16];
  char dco2Buf[16];
  char dtvBuf[16];
  char dliBuf[16];
  char dmicBuf[16];

  formatFloat(tBuf, sizeof(tBuf), shtTempC, 1);
  formatFloat(rhBuf, sizeof(rhBuf), shtHumidity, 1);
  formatFloat(ctBuf, sizeof(ctBuf), espTelemetry.ct, 2);
  formatFloat(lightBuf, sizeof(lightBuf), espTelemetry.light, 2);
  formatFloat(sceneBuf, sizeof(sceneBuf), espTelemetry.scene, 2);
  formatFloat(micBuf, sizeof(micBuf), localMicRms, 3);
  formatFloat(micPkBuf, sizeof(micPkBuf), localMicPeak, 3);
  formatFloat(micNfBuf, sizeof(micNfBuf), localMicNoise, 3);
  formatFloat(rtBuf, sizeof(rtBuf), rocTemp, 2);
  formatFloat(rrhBuf, sizeof(rrhBuf), rocRh, 2);
  formatFloat(rco2Buf, sizeof(rco2Buf), rocEco2, 2);
  formatFloat(rtvBuf, sizeof(rtvBuf), rocTvoc, 2);
  formatFloat(rliBuf, sizeof(rliBuf), rocLight, 2);
  formatFloat(rmicBuf, sizeof(rmicBuf), rocMic, 2);
  formatFloat(btBuf, sizeof(btBuf), baseTemp, 2);
  formatFloat(brhBuf, sizeof(brhBuf), baseRh, 2);
  formatFloat(bco2Buf, sizeof(bco2Buf), baseEco2, 2);
  formatFloat(btvBuf, sizeof(btvBuf), baseTvoc, 2);
  formatFloat(bliBuf, sizeof(bliBuf), baseLight, 2);
  formatFloat(bmicBuf, sizeof(bmicBuf), baseMic, 2);
  formatFloat(dtBuf, sizeof(dtBuf), deltaTemp, 2);
  formatFloat(drhBuf, sizeof(drhBuf), deltaRh, 2);
  formatFloat(dco2Buf, sizeof(dco2Buf), deltaEco2, 2);
  formatFloat(dtvBuf, sizeof(dtvBuf), deltaTvoc, 2);
  formatFloat(dliBuf, sizeof(dliBuf), deltaLight, 2);
  formatFloat(dmicBuf, sizeof(dmicBuf), deltaMic, 2);

  const uint32_t ageMs = getAgeMs(now);
  char line[780];
  snprintf(
      line,
      sizeof(line),
      "LOG,t=%s,rh=%s,eco2=%u,tvoc=%u,n=%lu,rssi=%d,heap=%lu,psram=%lu,ct=%s,light=%s,scene=%s,mic=%s,micpk=%s,micnf=%s,bps=%lu,age=%lu,pf=%lu,rt=%s,rrh=%s,rco2=%s,rtv=%s,rli=%s,rmic=%s,bt=%s,brh=%s,bco2=%s,btv=%s,bli=%s,bmic=%s,dt=%s,drh=%s,dco2=%s,dtv=%s,dli=%s,dmic=%s,camok=%d,camerr=%d,micok=%d,micerr=%d,wifist=%d,camaddr=%d\n",
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
      micBuf,
      micPkBuf,
      micNfBuf,
      static_cast<unsigned long>(bps),
      static_cast<unsigned long>(ageMs),
      static_cast<unsigned long>(parseFail),
      rtBuf,
      rrhBuf,
      rco2Buf,
      rtvBuf,
      rliBuf,
      rmicBuf,
      btBuf,
      brhBuf,
      bco2Buf,
      btvBuf,
      bliBuf,
      bmicBuf,
      dtBuf,
      drhBuf,
      dco2Buf,
      dtvBuf,
      dliBuf,
      dmicBuf,
      espTelemetry.camok,
      espTelemetry.camerr,
      localMicOk ? 1 : 0,
      localMicErr,
      espTelemetry.wifist,
      espTelemetry.camaddr);
  Serial.print(line);
#else
  (void)now;
#endif
}

static void updateDerivedEvents(uint32_t now);

static void pushSample(float tC, float rh, float eco2, float tvoc, float light, float scene, float mic) {
  histTemp[histIndex] = tC;
  histRh[histIndex] = rh;
  histEco2[histIndex] = eco2;
  histTvoc[histIndex] = tvoc;
  histLight[histIndex] = light;
  histScene[histIndex] = scene;
  histMic[histIndex] = mic;

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

  const float t = shtTempC;
  const float rh = shtHumidity;
  const float eco2 = static_cast<float>(sgpEco2);
  const float tvoc = static_cast<float>(sgpTvoc);
  const float light = espTelemetry.light;
  const float mic = localMicRms;

  updateRocRing(&rocTempRing, now, t, &rocTemp);
  updateRocRing(&rocRhRing, now, rh, &rocRh);
  updateRocRing(&rocEco2Ring, now, eco2, &rocEco2);
  updateRocRing(&rocTvocRing, now, tvoc, &rocTvoc);
  updateRocRingOptional(&rocLightRing, now, light, &rocLight);
  updateRocRingOptional(&rocMicRing, now, mic, &rocMic);

  updateBaseline(t, &baseTemp);
  updateBaseline(rh, &baseRh);
  updateBaseline(eco2, &baseEco2);
  updateBaseline(tvoc, &baseTvoc);
  updateBaseline(light, &baseLight);
  updateBaseline(mic, &baseMic);

  deltaTemp = computeDelta(t, baseTemp);
  deltaRh = computeDelta(rh, baseRh);
  deltaEco2 = computeDelta(eco2, baseEco2);
  deltaTvoc = computeDelta(tvoc, baseTvoc);
  deltaLight = computeDelta(light, baseLight);
  deltaMic = computeDelta(mic, baseMic);

  updateDerivedEvents(now);
  pushSample(t, rh, eco2, tvoc, light, espTelemetry.scene, mic);

  exportUsbLine(now);
}

static void updateDerivedEvents(uint32_t now) {
  if (isnan(rocEco2)) {
    airRisingCount = 0;
    airFallingCount = 0;
  } else {
    if (rocEco2 > 30.0f) {
      airRisingCount++;
    } else {
      airRisingCount = 0;
    }

    if (rocEco2 < -30.0f) {
      airFallingCount++;
    } else {
      airFallingCount = 0;
    }
  }

  if (airRisingCount >= 5 && now >= airRisingCooldownUntilMs) {
    Serial.println("EVT,air_rising");
    airRisingCooldownUntilMs = now + AIR_EVENT_COOLDOWN_MS;
    airRisingCount = 0;
  }
  if (airFallingCount >= 5 && now >= airFallingCooldownUntilMs) {
    Serial.println("EVT,air_falling");
    airFallingCooldownUntilMs = now + AIR_EVENT_COOLDOWN_MS;
    airFallingCount = 0;
  }

  if (!isnan(deltaLight) && !isnan(rocLight)) {
    if (deltaLight < -0.10f && fabs(rocLight) > 0.05f && now >= lightDropCooldownUntilMs) {
      Serial.println("EVT,light_drop");
      lightDropCooldownUntilMs = now + LIGHT_EVENT_COOLDOWN_MS;
    }
  }

  if (!isnan(deltaMic) && deltaMic > 0.10f && now >= noiseSpikeCooldownUntilMs) {
    Serial.println("EVT,noise_spike");
    noiseSpikeCooldownUntilMs = now + NOISE_EVENT_COOLDOWN_MS;
  }
}

static void updateMicEvents(uint32_t now) {
  if (lastLineMs == 0 || lastLineMs == lastMicEventLineMs) {
    return;
  }
  lastMicEventLineMs = lastLineMs;

  if (isnan(localMicRms) || isnan(localMicNoise)) {
    micSustainStartMs = 0;
    return;
  }

    const float delta = (localMicRms > localMicNoise)
      ? (localMicRms - localMicNoise)
      : 0.0f;

  if (delta > MIC_SPIKE_DELTA && now >= micSpikeCooldownUntilMs) {
    Serial.println("EVT,snd_spike");
    micSpikeCooldownUntilMs = now + MIC_SPIKE_COOLDOWN_MS;
  }

  if (delta > MIC_SUSTAIN_DELTA) {
    if (micSustainStartMs == 0) {
      micSustainStartMs = now;
    }
    if ((now - micSustainStartMs) >= MIC_SUSTAIN_MS && now >= micSustainCooldownUntilMs) {
      Serial.println("EVT,snd_sustain");
      micSustainCooldownUntilMs = now + MIC_SUSTAIN_COOLDOWN_MS;
      micSustainStartMs = now;
    }
  } else {
    micSustainStartMs = 0;
  }
}

static void initHistory() {
  for (int i = 0; i < HIST_N; i++) {
    histTemp[i] = NAN;
    histRh[i] = NAN;
    histEco2[i] = NAN;
    histTvoc[i] = NAN;
    histLight[i] = NAN;
    histScene[i] = NAN;
    histMic[i] = NAN;
  }

  resetRocRing(&rocTempRing);
  resetRocRing(&rocRhRing);
  resetRocRing(&rocEco2Ring);
  resetRocRing(&rocTvocRing);
  resetRocRing(&rocLightRing);
  resetRocRing(&rocMicRing);

  rocTemp = NAN;
  rocRh = NAN;
  rocEco2 = NAN;
  rocTvoc = NAN;
  rocLight = NAN;
  rocMic = NAN;

  baseTemp = NAN;
  baseRh = NAN;
  baseEco2 = NAN;
  baseTvoc = NAN;
  baseLight = NAN;
  baseMic = NAN;

  deltaTemp = NAN;
  deltaRh = NAN;
  deltaEco2 = NAN;
  deltaTvoc = NAN;
  deltaLight = NAN;
  deltaMic = NAN;

  airRisingCount = 0;
  airFallingCount = 0;
  airRisingCooldownUntilMs = 0;
  airFallingCooldownUntilMs = 0;
  lightDropCooldownUntilMs = 0;
  noiseSpikeCooldownUntilMs = 0;
}

static void renderDisplays(uint32_t now);

static void initDisplays() {
  bool envOk = false;
  bool espOk = false;
  for (int attempt = 0; attempt < 3 && (!envOk || !espOk); attempt++) {
    envOk = displayEnv.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR_ENV);
    espOk = displayEsp.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR_ESP);
    delay(10);
  }
  if (!envOk) {
    Serial.println("ENV OLED init failed");
  }
  if (!espOk) {
    Serial.println("ESP OLED init failed");
  }
  displayEnv.clearDisplay();
  displayEsp.clearDisplay();
  displayEnv.display();
  displayEsp.display();
}

static void softResetState(uint32_t now) {
  rxLen = 0;
  byteCount = 0;
  bps = 0;
  lastBpsMs = now;
  lastLineMs = 0;
  lastParseFailSeen = parseFail;
  parseErrorUntilMs = 0;
  linesOk = 0;
  strncpy(lastSensLine, "SENS,<none>", sizeof(lastSensLine) - 1);
  lastSensLine[sizeof(lastSensLine) - 1] = '\0';
  histIndex = 0;
  histCount = 0;
  initHistory();
  initDisplays();
  lastDisplayMs = 0;
  renderDisplays(now);
  Serial.println("EVT,sw=toggle,action=soft_reset");

#if ENABLE_ESP_CMD
  Serial1.print("CMD,reboot\n");
#endif
}

static void updateSwitch(uint32_t now) {
  const bool rawState = digitalRead(PIN_SW);
  if (rawState != swState && (now - swLastChangeMs) > 50) {
    swState = rawState;
    swLastChangeMs = now;
    debugMode = !swState;
    usbExportEnabled = debugMode;
    uiStack = debugMode ? UI_DEBUG : UI_USER;
    softResetState(now);
  }
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

static float getSmoothedValue(const float *series) {
  if (histCount == 0) {
    return NAN;
  }
  float sum = 0.0f;
  int count = 0;
  for (int i = 0; i < 3 && i < histCount; i++) {
    const int idx = (histIndex - 1 - i + HIST_N) % HIST_N;
    const float v = series[idx];
    if (isnan(v)) {
      continue;
    }
    sum += v;
    count++;
  }
  return (count > 0) ? (sum / static_cast<float>(count)) : NAN;
}

static bool getLocalEpoch(uint32_t now, int64_t *epochOut) {
  if (espTelemetry.ntp == 0 || lastLineMs == 0) {
    return false;
  }
  const uint32_t elapsedMs = now - lastLineMs;
  const int64_t epoch = static_cast<int64_t>(espTelemetry.ntp) + (elapsedMs / 1000) + (TIMEZONE_OFFSET_MIN * 60);
  *epochOut = epoch;
  return true;
}

static bool getLocalTime(uint32_t now, int *hour, int *minute, int *second) {
  int64_t epoch = 0;
  if (!getLocalEpoch(now, &epoch)) {
    return false;
  }
  int64_t seconds = epoch % 86400;
  if (seconds < 0) {
    seconds += 86400;
  }
  *hour = static_cast<int>(seconds / 3600);
  seconds %= 3600;
  *minute = static_cast<int>(seconds / 60);
  *second = static_cast<int>(seconds % 60);
  return true;
}

static bool getLocalDate(uint32_t now, int *year, int *month, int *day, int *weekday) {
  int64_t epoch = 0;
  if (!getLocalEpoch(now, &epoch)) {
    return false;
  }
  int64_t days = epoch / 86400;
  int64_t rem = epoch % 86400;
  if (rem < 0) {
    rem += 86400;
    days -= 1;
  }

  int w = static_cast<int>((days + 4) % 7);
  if (w < 0) {
    w += 7;
  }
  *weekday = w;

  int64_t z = days + 719468;
  const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  const unsigned doe = static_cast<unsigned>(z - era * 146097);
  const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  int y = static_cast<int>(yoe) + static_cast<int>(era * 400);
  const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  const unsigned mp = (5 * doy + 2) / 153;
  const unsigned d = doy - (153 * mp + 2) / 5 + 1;
  unsigned m = mp + (mp < 10 ? 3 : -9);
  y += (m <= 2);

  *year = y;
  *month = static_cast<int>(m);
  *day = static_cast<int>(d);
  return true;
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

[[maybe_unused]] static void drawEnvDisplay() {
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

  displayEnv.setCursor(0, 48);
  displayEnv.print("MIC ");
  if (isnan(localMicRms)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(localMicRms, 2);
  }
  displayEnv.print(" NF ");
  if (isnan(localMicNoise)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(localMicNoise, 2);
  }

  drawCornerFlags(displayEnv, millis());

  displayEnv.display();
}

[[maybe_unused]] static void drawEspDisplay(uint32_t now) {
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

  displayEsp.setCursor(0, 40);
  displayEsp.print("ntp ");
  displayEsp.print(espTelemetry.ntp);

  drawCornerFlags(displayEsp, now);

  displayEsp.display();
}

[[maybe_unused]] static void drawLinkDebugDisplay() {
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

static void drawDebugLinkPage(uint32_t now) {
  displayEnv.clearDisplay();
  displayEnv.setTextSize(1);
  displayEnv.setTextColor(SSD1306_WHITE);

  displayEnv.setCursor(0, 0);
  displayEnv.print("ESP");

  displayEnv.setCursor(0, 8);
  displayEnv.print("up ");
  displayEnv.print(espTelemetry.up);

  displayEnv.setCursor(0, 16);
  displayEnv.print("rssi ");
  displayEnv.print(espTelemetry.rssi);

  displayEnv.setCursor(0, 24);
  displayEnv.print("heap ");
  displayEnv.print(espTelemetry.heap);

  displayEnv.setCursor(0, 32);
  displayEnv.print("psram ");
  displayEnv.print(espTelemetry.psram);

  displayEnv.setCursor(0, 40);
  displayEnv.print("ct ");
  if (isnan(espTelemetry.ct)) {
    displayEnv.print("nan");
  } else {
    displayEnv.print(espTelemetry.ct, 1);
  }

  drawCornerFlags(displayEnv, now);
  displayEnv.display();

  drawLinkStatsDisplay(now);
}

static void drawDebugSensorsPage(uint32_t now) {
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
  displayEnv.print(" RH ");
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

  drawCornerFlags(displayEnv, now);
  displayEnv.display();

  displayEsp.clearDisplay();
  displayEsp.setTextSize(1);
  displayEsp.setTextColor(SSD1306_WHITE);

  displayEsp.setCursor(0, 0);
  displayEsp.print("ESP");

  displayEsp.setCursor(0, 8);
  displayEsp.print("light ");
  if (isnan(espTelemetry.light)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(espTelemetry.light, 2);
  }

  displayEsp.setCursor(0, 16);
  displayEsp.print("scene ");
  if (isnan(espTelemetry.scene)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(espTelemetry.scene, 2);
  }

  displayEsp.setCursor(0, 24);
  displayEsp.print("mic ");
  if (isnan(localMicRms)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(localMicRms, 2);
  }

  displayEsp.setCursor(0, 32);
  displayEsp.print("micpk ");
  if (isnan(localMicPeak)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(localMicPeak, 2);
  }

  displayEsp.setCursor(0, 40);
  displayEsp.print("micnf ");
  if (isnan(localMicNoise)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(localMicNoise, 2);
  }

  drawCornerFlags(displayEsp, now);
  displayEsp.display();
}

static void drawDebugDerivedPage(uint32_t now) {
  displayEnv.clearDisplay();
  displayEnv.setTextSize(1);
  displayEnv.setTextColor(SSD1306_WHITE);

  displayEnv.setCursor(0, 0);
  displayEnv.print("MIC ");
  if (isnan(localMicRms)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(localMicRms, 2);
  }
  displayEnv.print(" NF ");
  if (isnan(localMicNoise)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(localMicNoise, 2);
  }

  displayEnv.setCursor(0, 8);
  displayEnv.print("rt ");
  if (isnan(rocTemp)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(rocTemp, 2);
  }

  displayEnv.setCursor(0, 16);
  displayEnv.print("rrh ");
  if (isnan(rocRh)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(rocRh, 2);
  }

  displayEnv.setCursor(0, 24);
  displayEnv.print("rco2 ");
  if (isnan(rocEco2)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(rocEco2, 1);
  }

  displayEnv.setCursor(0, 32);
  displayEnv.print("rtv ");
  if (isnan(rocTvoc)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(rocTvoc, 1);
  }

  displayEnv.setCursor(0, 40);
  displayEnv.print("rli ");
  if (isnan(rocLight)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(rocLight, 2);
  }

  displayEnv.setCursor(0, 48);
  displayEnv.print("rmic ");
  if (isnan(rocMic)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(rocMic, 2);
  }

  drawCornerFlags(displayEnv, now);
  displayEnv.display();

  displayEsp.clearDisplay();
  displayEsp.setTextSize(1);
  displayEsp.setTextColor(SSD1306_WHITE);

  displayEsp.setCursor(0, 0);
  displayEsp.print("BASE");

  displayEsp.setCursor(0, 8);
  displayEsp.print("MIC ");
  if (isnan(localMicRms)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(localMicRms, 2);
  }
  displayEsp.print(" NF ");
  if (isnan(localMicNoise)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(localMicNoise, 2);
  }

  displayEsp.setCursor(0, 16);
  displayEsp.print("brh ");
  if (isnan(baseRh)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(baseRh, 2);
  }

  displayEsp.setCursor(0, 24);
  displayEsp.print("bco2 ");
  if (isnan(baseEco2)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(baseEco2, 1);
  }

  displayEsp.setCursor(0, 32);
  displayEsp.print("btv ");
  if (isnan(baseTvoc)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(baseTvoc, 1);
  }

  displayEsp.setCursor(0, 40);
  displayEsp.print("DEL dco2 ");
  if (isnan(deltaEco2)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(deltaEco2, 1);
  }

  displayEsp.setCursor(0, 48);
  displayEsp.print("dmic ");
  if (isnan(deltaMic)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(deltaMic, 2);
  }

  drawCornerFlags(displayEsp, now);
  displayEsp.display();
}

static void renderDebugPage(uint32_t now, int pageIndex) {
  const int idx = (pageIndex < 0) ? 0 : (pageIndex % DEBUG_PAGE_COUNT);
  switch (idx) {
    case 1:
      drawDebugSensorsPage(now);
      break;
    case 2:
      drawDebugDerivedPage(now);
      break;
    case 0:
    default:
      drawDebugLinkPage(now);
      break;
  }
}

static void drawUserOverviewPage(uint32_t now) {
  displayEnv.clearDisplay();
  displayEnv.setTextColor(SSD1306_WHITE);

  const float tSmooth = getSmoothedValue(histTemp);
  const float rhSmooth = getSmoothedValue(histRh);

  displayEnv.setTextSize(2);
  displayEnv.setCursor(0, 0);
  if (isnan(tSmooth)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(tSmooth, 1);
  }

  displayEnv.setTextSize(1);
  displayEnv.setCursor(0, 24);
  displayEnv.print("RH ");
  if (isnan(rhSmooth)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(rhSmooth, 1);
  }

  drawCornerFlags(displayEnv, now);
  displayEnv.display();

  displayEsp.clearDisplay();
  displayEsp.setTextColor(SSD1306_WHITE);

  const float eco2Smooth = getSmoothedValue(histEco2);

  displayEsp.setTextSize(2);
  displayEsp.setCursor(0, 0);
  if (isnan(eco2Smooth)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(static_cast<int>(lroundf(eco2Smooth)));
  }

  displayEsp.setTextSize(1);
  displayEsp.setCursor(0, 24);
  displayEsp.print("TVOC ");
  displayEsp.print(sgpTvoc);

  displayEsp.setCursor(0, 32);
  displayEsp.print("rCO2 ");
  if (isnan(rocEco2)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(rocEco2, 1);
  }

  drawCornerFlags(displayEsp, now);
  displayEsp.display();
}

static void drawUserAirTrendsPage(uint32_t now) {
  displayEnv.clearDisplay();
  displayEnv.setTextSize(1);
  displayEnv.setTextColor(SSD1306_WHITE);
  displayEnv.setCursor(0, 0);
  displayEnv.print("CO2");
  drawSparkline(0, 16, SCREEN_WIDTH, 48, histEco2, histCount, histIndex, displayEnv);
  drawCornerFlags(displayEnv, now);
  displayEnv.display();

  displayEsp.clearDisplay();
  displayEsp.setTextSize(1);
  displayEsp.setTextColor(SSD1306_WHITE);
  displayEsp.setCursor(0, 0);
  displayEsp.print("TVOC");
  drawSparkline(0, 16, SCREEN_WIDTH, 48, histTvoc, histCount, histIndex, displayEsp);
  drawCornerFlags(displayEsp, now);
  displayEsp.display();
}

static void drawUserEnvTrendsPage(uint32_t now) {
  displayEnv.clearDisplay();
  displayEnv.setTextSize(1);
  displayEnv.setTextColor(SSD1306_WHITE);
  displayEnv.setCursor(0, 0);
  displayEnv.print("TEMP / RH");
  drawSparkline(0, 16, SCREEN_WIDTH, 20, histTemp, histCount, histIndex, displayEnv);
  drawSparkline(0, 40, SCREEN_WIDTH, 20, histRh, histCount, histIndex, displayEnv);
  drawCornerFlags(displayEnv, now);
  displayEnv.display();

  displayEsp.clearDisplay();
  displayEsp.setTextSize(1);
  displayEsp.setTextColor(SSD1306_WHITE);
  displayEsp.setCursor(0, 0);
  displayEsp.print("LIGHT");
  drawSparkline(0, 16, SCREEN_WIDTH, 48, histLight, histCount, histIndex, displayEsp);
  drawCornerFlags(displayEsp, now);
  displayEsp.display();
}

static void drawUserSoundPage(uint32_t now) {
  displayEnv.clearDisplay();
  displayEnv.setTextSize(1);
  displayEnv.setTextColor(SSD1306_WHITE);
  displayEnv.setCursor(0, 0);
  displayEnv.print("MIC");
  drawSparkline(0, 16, SCREEN_WIDTH, 48, histMic, histCount, histIndex, displayEnv);
  drawCornerFlags(displayEnv, now);
  displayEnv.display();

  displayEsp.clearDisplay();
  displayEsp.setTextSize(1);
  displayEsp.setTextColor(SSD1306_WHITE);
  displayEsp.setCursor(0, 0);
  displayEsp.print("SCENE");
  drawSparkline(0, 16, SCREEN_WIDTH, 48, histScene, histCount, histIndex, displayEsp);
  drawCornerFlags(displayEsp, now);
  displayEsp.display();
}

static void drawUserTimePage(uint32_t now) {
  displayEsp.clearDisplay();
  displayEsp.setTextColor(SSD1306_WHITE);
  displayEsp.setTextSize(1);
  displayEsp.setCursor(0, 0);
  int hour = 0;
  int minute = 0;
  int second = 0;
  int year = 0;
  int month = 0;
  int day = 0;
  int weekday = 0;
  const char *weekdayNames[] = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"};
  char headerBuffer[32];
  if (!getLocalTime(now, &hour, &minute, &second) || !getLocalDate(now, &year, &month, &day, &weekday)) {
    snprintf(headerBuffer, sizeof(headerBuffer), "---");
  } else {
    snprintf(headerBuffer, sizeof(headerBuffer), "%s", weekdayNames[weekday]);
  }
  const int headerWidth = static_cast<int>(strlen(headerBuffer)) * 6;
  displayEsp.setCursor((SCREEN_WIDTH - headerWidth) / 2, 0);
  displayEsp.print(headerBuffer);

  displayEsp.setTextSize(2);
  const int timeCharWidth = 6 * 2;
  const int timeWidth = 8 * timeCharWidth;
  const int timeX = (SCREEN_WIDTH - timeWidth) / 2;
  displayEsp.setCursor(timeX, 20);
  if (!getLocalTime(now, &hour, &minute, &second)) {
    displayEsp.print("--:--:--");
  } else {
    char timeBuffer[16];
    snprintf(timeBuffer, sizeof(timeBuffer), "%02d:%02d:%02d", hour, minute, second);
    displayEsp.print(timeBuffer);
  }

  displayEsp.setTextSize(1);
  char dateBuffer[16];
  if (!getLocalDate(now, &year, &month, &day, &weekday)) {
    snprintf(dateBuffer, sizeof(dateBuffer), "--/--/--");
  } else {
    const int year2 = year % 100;
    snprintf(dateBuffer, sizeof(dateBuffer), "%02d/%02d/%02d", month, day, year2);
  }
  const int dateWidth = static_cast<int>(strlen(dateBuffer)) * 6;
  displayEsp.setCursor((SCREEN_WIDTH - dateWidth) / 2, 48);
  displayEsp.print(dateBuffer);

  drawCornerFlags(displayEsp, now);
  displayEsp.display();

  displayEnv.clearDisplay();
  displayEnv.setTextColor(SSD1306_WHITE);
  const int columnWidth = SCREEN_WIDTH / 2;
  const int labelY = 0;
  const int valueY = 30;

  const char *tempLabel = "Temp";
  const char *humidLabel = "Humid";
  const int tempLabelWidth = static_cast<int>(strlen(tempLabel)) * 12;
  const int humidLabelWidth = static_cast<int>(strlen(humidLabel)) * 12;

  displayEnv.setTextSize(2);
  displayEnv.setCursor((columnWidth - tempLabelWidth) / 2, labelY);
  displayEnv.print(tempLabel);
  displayEnv.setCursor(columnWidth + (columnWidth - humidLabelWidth) / 2, labelY);
  displayEnv.print(humidLabel);

  displayEnv.setTextSize(2);
  if (isnan(shtTempC)) {
    const char *tempValue = "--";
    const int tempValueWidth = static_cast<int>(strlen(tempValue)) * 12;
    displayEnv.setCursor((columnWidth - tempValueWidth) / 2, valueY);
    displayEnv.print(tempValue);
  } else {
    char tempValue[8];
    snprintf(tempValue, sizeof(tempValue), "%.1fC", shtTempC);
    const int tempValueWidth = static_cast<int>(strlen(tempValue)) * 12;
    displayEnv.setCursor((columnWidth - tempValueWidth) / 2, valueY);
    displayEnv.print(tempValue);
  }

  if (isnan(shtHumidity)) {
    const char *humValue = "--";
    const int humValueWidth = static_cast<int>(strlen(humValue)) * 12;
    displayEnv.setCursor(columnWidth + (columnWidth - humValueWidth) / 2, valueY);
    displayEnv.print(humValue);
  } else {
    char humValue[8];
    snprintf(humValue, sizeof(humValue), "%.1f%%", shtHumidity);
    const int humValueWidth = static_cast<int>(strlen(humValue)) * 12;
    displayEnv.setCursor(columnWidth + (columnWidth - humValueWidth) / 2, valueY);
    displayEnv.print(humValue);
  }

  drawCornerFlags(displayEnv, now);
  displayEnv.display();
}

static void renderUserPage(uint32_t now, int pageIndex) {
  const int idx = (pageIndex < 0) ? 0 : (pageIndex % USER_PAGE_COUNT);
  switch (idx) {
    case 1:
      drawUserAirTrendsPage(now);
      break;
    case 2:
      drawUserEnvTrendsPage(now);
      break;
    case 3:
      drawUserSoundPage(now);
      break;
    case 4:
      drawUserTimePage(now);
      break;
    case 0:
    default:
      drawUserOverviewPage(now);
      break;
  }
}

[[maybe_unused]] static void drawEnvBigDisplay() {
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

[[maybe_unused]] static void drawEnvBigLeftDisplay() {
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

[[maybe_unused]] static void drawGraphsDisplay(uint32_t now) {
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
  displayEnv.print("rT ");
  if (isnan(rocTemp)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(rocTemp, 2);
  }
  displayEnv.print(" rC ");
  if (isnan(rocEco2)) {
    displayEnv.print("--");
  } else {
    displayEnv.print(rocEco2, 1);
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
  displayEsp.print("MIC ");
  if (isnan(localMicRms)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(localMicRms, 2);
  }
  displayEsp.print(" NF ");
  if (isnan(localMicNoise)) {
    displayEsp.print("--");
  } else {
    displayEsp.print(localMicNoise, 2);
  }

  drawSparkline(0, 16, SCREEN_WIDTH, 24, histEco2, histCount, histIndex, displayEsp);
  drawSparkline(0, 40, SCREEN_WIDTH, 24, histMic, histCount, histIndex, displayEsp);

  drawCornerFlags(displayEsp, now);
  displayEsp.display();
}

static void renderDisplays(uint32_t now) {
  if (uiStack == UI_DEBUG) {
    renderDebugPage(now, debugPageIndex);
  } else {
    renderUserPage(now, userPageIndex);
  }
}

static void refreshNow(uint32_t now) {
  refreshFlashUntilMs = now + 500;
  lastDisplayMs = now;
  renderDisplays(now);
  Serial.println("BTN: refresh now");
}

static void handleShortPress(uint32_t now) {
  if (uiStack == UI_DEBUG) {
    debugPageIndex = (debugPageIndex + 1) % DEBUG_PAGE_COUNT;
  } else {
    userPageIndex = (userPageIndex + 1) % USER_PAGE_COUNT;
  }
  lastDisplayMs = now;
  renderDisplays(now);
}

static void handleDoublePress(uint32_t now) {
  if (uiStack == UI_DEBUG) {
    refreshNow(now);
  } else {
    Serial.println("EVT,mark");
  }
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
  digitalWrite(DEBUG_LED_PIN, usbExportEnabled ? HIGH : LOW);
}

static void updateDisplays(uint32_t now) {
  const uint32_t interval = focusMode
      ? DISPLAY_FOCUS_INTERVAL_MS
      : (uiStack == UI_USER ? DISPLAY_USER_INTERVAL_MS : DISPLAY_DEBUG_INTERVAL_MS);
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
  if (!debugMode) {
    return;
  }
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
  delay(1500);
  Serial.print("PROTO,ver=1,device=nrf52840,fw=");
  Serial.print(FW_STRING);
  Serial.print(",build=");
  Serial.println(BUILD_STRING);
  Serial.println("HB,boot");
  Serial.flush();
  Serial1.setPins(D7, D6);
  Serial1.begin(UART_BAUD);
  Wire.begin();

  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);
  pinMode(DEBUG_LED_PIN, OUTPUT);
  digitalWrite(DEBUG_LED_PIN, LOW);
  pinMode(PIN_BTN, INPUT_PULLUP);
  pinMode(PIN_SW, INPUT_PULLUP);
  btnRawState = digitalRead(PIN_BTN);
  btnStableState = btnRawState;
  btnLastChangeMs = millis();
  swState = digitalRead(PIN_SW);
  swLastChangeMs = millis();
  debugMode = !swState;
  usbExportEnabled = debugMode;
  uiStack = debugMode ? UI_DEBUG : UI_USER;

  shtOk = sht31.begin(0x44);
  if (!shtOk) {
    shtOk = sht31.begin(0x45);
  }

  sgpOk = sgp.begin();

  initLocalMic();

  initHistory();
  initDisplays();
}

void loop() {
  const uint32_t now = millis();
  static uint32_t lastHB = 0;
  static uint32_t tick = 0;
  if (now - lastHB >= 1000) {
    lastHB = now;
    tick++;
    const uint32_t uptime_s = now / 1000;
    Serial.print("HB,tick=");
    Serial.print(tick);
    Serial.print(",uptime_s=");
    Serial.println(uptime_s);
  }
  handleSerial1();
  updateBps(now);
  readSensors(now);
  updateLocalMic(now);
  updateHistory(now);
  updateMicEvents(now);
  updateSwitch(now);
  uiStack = debugMode ? UI_DEBUG : UI_USER;
  updateButton(now);
  updateLed(now);
  if (focusMode && now >= focusModeUntilMs) {
    focusMode = false;
    focusModeUntilMs = 0;
  }
  updateDisplays(now);
  heartbeat(now);
}
