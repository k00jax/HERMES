#include <Arduino.h>
#include <math.h>
#include <Wire.h>

#include "esp_camera.h"
#include "cam_pins.h"
#if __has_include(<ESP_I2S.h>)
#include <ESP_I2S.h>
#define HAS_ESP_I2S 1
#else
#define HAS_ESP_I2S 0
#endif
#if !HAS_ESP_I2S && __has_include(<I2S.h>)
#include <I2S.h>
#define HAS_ARDUINO_I2S 1
#else
#define HAS_ARDUINO_I2S 0
#endif
#if !HAS_ESP_I2S && !HAS_ARDUINO_I2S
#include "driver/i2s.h"
#endif

#if __has_include("secrets.h")
#include "secrets.h"
#define HAS_WIFI_SECRETS 1
#else
#define HAS_WIFI_SECRETS 0
#endif

#if HAS_WIFI_SECRETS
#include <WiFi.h>
#include <time.h>
#endif

#include "hermes_protocol.h"

#ifndef CAMERA_PROBE_MODE
#define CAMERA_PROBE_MODE 0
#endif

#ifndef LD2410_UART_BRINGUP_MODE
#define LD2410_UART_BRINGUP_MODE 0
#endif

#ifndef LD2410_DEBUG
#define LD2410_DEBUG 0
#endif

#define ENABLE_ESP_CMD 1
#ifndef ENABLE_CAMERA
#define ENABLE_CAMERA 0
#endif
#ifndef ENABLE_MIC
#define ENABLE_MIC 0
#endif
#define ENABLE_WIFI 1

static const uint8_t UART_RX_PIN = D7;
static const uint8_t UART_TX_PIN = D6;
static const int LD2410_UART_RX_PIN = UART_RX_PIN;
static const uint32_t LD2410_UART_BAUD = 256000;
static const uint8_t LD2410_HEADER[4] = {0xF4, 0xF3, 0xF2, 0xF1};
static const uint8_t LD2410_FOOTER[4] = {0xF8, 0xF7, 0xF6, 0xF5};
static const size_t LD2410_MAX_FRAME = 64;
static const uint32_t LD2410_EMIT_MIN_INTERVAL_MS = 100;
static const uint32_t LD2410_ALIVE_TIMEOUT_MS = 1000;
HardwareSerial RadarSerial(2);

struct Ld2410State {
  bool hasValidFrame = false;
  bool alive = false;
  bool deadAnnounced = false;
  bool aliveAnnouncePending = false;
  bool targetChangedPending = false;
  uint8_t target = 0;
  uint16_t moveCm = 0;
  uint16_t statCm = 0;
  uint16_t detectCm = 0;
  uint8_t moveEn = 0;
  uint8_t statEn = 0;
  uint32_t initTsMs = 0;
  uint32_t frameTsMs = 0;
  uint32_t lastEmitMs = 0;
};

static uint8_t ld2410Frame[LD2410_MAX_FRAME];
static size_t ld2410FrameLen = 0;
static uint8_t ld2410HeaderMatch = 0;
static bool ld2410InFrame = false;
static Ld2410State ld2410State;

static bool endsWithFooter(const uint8_t *buffer, size_t len) {
  if (len < sizeof(LD2410_FOOTER)) {
    return false;
  }
  const size_t start = len - sizeof(LD2410_FOOTER);
  for (size_t index = 0; index < sizeof(LD2410_FOOTER); ++index) {
    if (buffer[start + index] != LD2410_FOOTER[index]) {
      return false;
    }
  }
  return true;
}

static const char *targetStateText(uint8_t state) {
  switch (state) {
    case 0:
      return "none";
    case 1:
      return "moving";
    case 2:
      return "stationary";
    case 3:
      return "both";
    default:
      return "unknown";
  }
}

static void emitRadarStateLine(bool alive, uint32_t now) {
  char line[192];
  snprintf(
      line,
      sizeof(line),
      "RADAR,alive=%d,target=%u,move_cm=%u,stat_cm=%u,detect_cm=%u,move_en=%u,stat_en=%u,frame_ts_ms=%lu,ts=%lu\n",
      alive ? 1 : 0,
      static_cast<unsigned>(ld2410State.target),
      static_cast<unsigned>(ld2410State.moveCm),
      static_cast<unsigned>(ld2410State.statCm),
      static_cast<unsigned>(ld2410State.detectCm),
      static_cast<unsigned>(ld2410State.moveEn),
      static_cast<unsigned>(ld2410State.statEn),
      static_cast<unsigned long>(ld2410State.frameTsMs),
      static_cast<unsigned long>(now));
  Serial1.print(line);
}

static bool parseLd2410Frame(const uint8_t *frame, size_t frameLen) {
  if (frameLen < 10) {
    return false;
  }

  const uint16_t payloadLen = static_cast<uint16_t>(frame[4])
      | (static_cast<uint16_t>(frame[5]) << 8);
  const size_t expectedFrameLen = 4 + 2 + payloadLen + 4;
  if (frameLen != expectedFrameLen) {
#if LD2410_DEBUG
    Serial.print("[ld2410] frame_len_mismatch got=");
    Serial.print(static_cast<unsigned long>(frameLen));
    Serial.print(" expected=");
    Serial.println(static_cast<unsigned long>(expectedFrameLen));
#endif
    return false;
  }

  if (payloadLen < 13) {
#if LD2410_DEBUG
    Serial.print("[ld2410] payload_len=");
    Serial.println(static_cast<unsigned long>(payloadLen));
#endif
    return false;
  }

  const uint8_t *payload = frame + 6;
  const uint8_t reportType = payload[0];
  const uint8_t reportMarker = payload[1];
  if (reportType != 0x02 || reportMarker != 0xAA) {
    return false;
  }

  const uint8_t targetState = payload[2];
  const uint16_t movingDistanceCm = static_cast<uint16_t>(payload[3])
      | (static_cast<uint16_t>(payload[4]) << 8);
  const uint8_t movingEnergy = payload[5];
  const uint16_t stationaryDistanceCm = static_cast<uint16_t>(payload[6])
      | (static_cast<uint16_t>(payload[7]) << 8);
  const uint8_t stationaryEnergy = payload[8];
  const uint16_t detectDistanceCm = static_cast<uint16_t>(payload[9])
      | (static_cast<uint16_t>(payload[10]) << 8);
  const uint16_t trailerWord = static_cast<uint16_t>(payload[11])
      | (static_cast<uint16_t>(payload[12]) << 8);

  const bool targetChanged = (!ld2410State.hasValidFrame || ld2410State.target != targetState);
  ld2410State.target = targetState;
  ld2410State.moveCm = movingDistanceCm;
  ld2410State.statCm = stationaryDistanceCm;
  ld2410State.detectCm = detectDistanceCm;
  ld2410State.moveEn = movingEnergy;
  ld2410State.statEn = stationaryEnergy;
  ld2410State.frameTsMs = millis();
  ld2410State.hasValidFrame = true;
  ld2410State.deadAnnounced = false;
  if (!ld2410State.alive) {
    ld2410State.alive = true;
    ld2410State.aliveAnnouncePending = true;
  }
  if (targetChanged) {
    ld2410State.targetChangedPending = true;
  }

#if LD2410_DEBUG
  Serial.print("[ld2410] frame ");
  for (size_t index = 0; index < frameLen; ++index) {
    Serial.printf("%02X ", frame[index]);
  }
  Serial.println();
  Serial.printf(
      "[ld2410] type=0x%02X marker=0x%02X target=%u(%s) move_cm=%u stat_cm=%u move_en=%u stat_en=%u detect_cm=%u trail=0x%04X\n",
      reportType,
      reportMarker,
      targetState,
      targetStateText(targetState),
      static_cast<unsigned>(movingDistanceCm),
      static_cast<unsigned>(stationaryDistanceCm),
      static_cast<unsigned>(movingEnergy),
      static_cast<unsigned>(stationaryEnergy),
      static_cast<unsigned>(detectDistanceCm),
      static_cast<unsigned>(trailerWord));
  (void)trailerWord;
#endif
  return true;
}

static void processLd2410Byte(uint8_t byteValue) {
  if (!ld2410InFrame) {
    if (byteValue == LD2410_HEADER[ld2410HeaderMatch]) {
      ld2410HeaderMatch++;
      if (ld2410HeaderMatch == sizeof(LD2410_HEADER)) {
        ld2410InFrame = true;
        ld2410FrameLen = sizeof(LD2410_HEADER);
        for (size_t index = 0; index < sizeof(LD2410_HEADER); ++index) {
          ld2410Frame[index] = LD2410_HEADER[index];
        }
        ld2410HeaderMatch = 0;
      }
    } else {
      ld2410HeaderMatch = (byteValue == LD2410_HEADER[0]) ? 1 : 0;
    }
    return;
  }

  if (ld2410FrameLen < LD2410_MAX_FRAME) {
    ld2410Frame[ld2410FrameLen++] = byteValue;
  } else {
    ld2410InFrame = false;
    ld2410FrameLen = 0;
    ld2410HeaderMatch = 0;
#if LD2410_DEBUG
    Serial.println("[ld2410] frame overflow, reset parser");
#endif
    return;
  }

  if (!endsWithFooter(ld2410Frame, ld2410FrameLen)) {
    return;
  }

  parseLd2410Frame(ld2410Frame, ld2410FrameLen);
  ld2410InFrame = false;
  ld2410FrameLen = 0;
  ld2410HeaderMatch = 0;
}

static void ld2410Init() {
  ld2410State = Ld2410State{};
  ld2410State.initTsMs = millis();
  RadarSerial.begin(LD2410_UART_BAUD, SERIAL_8N1, LD2410_UART_RX_PIN, -1);
#if LD2410_DEBUG
  Serial.println("LD2410 parser active");
  Serial.print("Radar UART RX pin = ");
  Serial.println(LD2410_UART_RX_PIN);
  Serial.print("Radar UART baud = ");
  Serial.println(static_cast<unsigned long>(LD2410_UART_BAUD));
#endif
}

static void ld2410Poll() {
  while (RadarSerial.available() > 0) {
    const uint8_t byteValue = static_cast<uint8_t>(RadarSerial.read());
    processLd2410Byte(byteValue);
  }
}

static void ld2410EmitIfNeeded(uint32_t now) {
  if (!ld2410State.hasValidFrame) {
    if (!ld2410State.deadAnnounced && (now - ld2410State.initTsMs) > LD2410_ALIVE_TIMEOUT_MS) {
      emitRadarStateLine(false, now);
      ld2410State.deadAnnounced = true;
      ld2410State.lastEmitMs = now;
    }
    return;
  }

    if (ld2410State.alive && ld2410State.hasValidFrame
      && (now >= ld2410State.frameTsMs)
      && (now - ld2410State.frameTsMs > LD2410_ALIVE_TIMEOUT_MS)) {
    ld2410State.alive = false;
    ld2410State.aliveAnnouncePending = true;
    ld2410State.deadAnnounced = true;
  }

  if (ld2410State.aliveAnnouncePending) {
    emitRadarStateLine(ld2410State.alive, now);
    ld2410State.aliveAnnouncePending = false;
    ld2410State.lastEmitMs = now;
    ld2410State.targetChangedPending = false;
    return;
  }

  if (!ld2410State.alive || !ld2410State.hasValidFrame) {
    return;
  }

  const bool throttledReady = (now - ld2410State.lastEmitMs) >= LD2410_EMIT_MIN_INTERVAL_MS;
  if (!throttledReady && !ld2410State.targetChangedPending) {
    return;
  }

  emitRadarStateLine(true, now);
  ld2410State.lastEmitMs = now;
  ld2410State.targetChangedPending = false;
}

static const uint32_t CAMERA_INTERVAL_MS = 2000;
static const int SCENE_STRIDE = 4;
static const int SCENE_SAMPLES = (160 / SCENE_STRIDE) * (120 / SCENE_STRIDE);

static const uint32_t MIC_SAMPLE_RATE = 16000;
static const size_t MIC_WINDOW_SAMPLES = 512;
static const uint32_t MIC_UPDATE_MS = 100;
static const float MIC_NOISE_ALPHA = 0.01f;
static const int MIC_PDM_CLK_PIN = 42;
static const int MIC_PDM_DATA_PIN = 41;
static const int MIC_I2S_BCK_PIN = 42;
static const int MIC_I2S_WS_PIN = 41;
static const int MIC_I2S_DATA_PIN = 2;

static const uint32_t WIFI_CHECK_MS = 2000;
static const uint32_t WIFI_RETRY_MS = 5000;
static const uint32_t NTP_VALID_AFTER = 1600000000UL;

static uint32_t lastSendMs = 0;
static uint32_t packetCounter = 0;

static int espRssi = RSSI_NOT_CONNECTED;
static uint32_t ntpEpoch = 0;
static uint32_t lastWifiCheckMs = 0;
static uint32_t lastWifiBeginMs = 0;
static bool ntpConfigured = false;
static int wifiStatus = -1;
static uint32_t lastWifiReportMs = 0;
static uint32_t wifiReportSeq = 0;

static bool cameraOk = false;
static int cameraErr = -2;
static int cameraAddr = -1;
static int cameraSda = CAM_PIN_SIOD;
static int cameraScl = CAM_PIN_SIOC;
static float cameraLight = NAN;
static float cameraScene = NAN;
static uint32_t lastCameraMs = 0;
static bool scenePrevValid = false;
static uint8_t scenePrev[SCENE_SAMPLES];

static bool micOk = false;
static int micErr = -2;
static uint32_t lastMicMs = 0;
static float micRms = NAN;
static float micPeak = NAN;
static float micNoiseFloor = NAN;
static float micDelta = NAN;
static int16_t micSamples[MIC_WINDOW_SAMPLES];
#if HAS_ESP_I2S
static I2SClass micI2S;
#endif


static char cmdBuffer[64];
static size_t cmdLen = 0;

static void formatFloat(char *buffer, size_t size, float value, int precision) {
  if (isnan(value)) {
    snprintf(buffer, size, "nan");
    return;
  }
  char format[8];
  snprintf(format, sizeof(format), "%%.%df", precision);
  snprintf(buffer, size, format, value);
}

static void waitForSerial(uint32_t timeoutMs) {
  const uint32_t start = millis();
  while (!Serial && (millis() - start) < timeoutMs) {
    delay(10);
  }
}

static void runCameraProbe() {
  Serial.println("[camera_probe] start");
  Serial.print("[camera_probe] chip.model=");
  Serial.println(ESP.getChipModel());
  Serial.print("[camera_probe] chip.revision=");
  Serial.println(ESP.getChipRevision());
  Serial.print("[camera_probe] chip.cores=");
  Serial.println(ESP.getChipCores());
  Serial.print("[camera_probe] sdk=");
  Serial.println(ESP.getSdkVersion());

  const bool psram_ok = psramFound();
  const uint32_t psram_size = ESP.getPsramSize();
  const uint32_t free_psram = ESP.getFreePsram();
  const uint32_t free_heap = ESP.getFreeHeap();

  Serial.print("[camera_probe] psramFound()=");
  Serial.println(psram_ok ? 1 : 0);
  Serial.print("[camera_probe] ESP.getPsramSize()=");
  Serial.println(static_cast<unsigned long>(psram_size));
  Serial.print("[camera_probe] ESP.getFreePsram()=");
  Serial.println(static_cast<unsigned long>(free_psram));
  Serial.print("[camera_probe] ESP.getFreeHeap()=");
  Serial.println(static_cast<unsigned long>(free_heap));

  Serial.print("[camera_probe] pins.sccb_sda=");
  Serial.println(CAM_PIN_SIOD);
  Serial.print("[camera_probe] pins.sccb_scl=");
  Serial.println(CAM_PIN_SIOC);
  Serial.print("[camera_probe] pins.xclk=");
  Serial.println(CAM_PIN_XCLK);

  const uint32_t probe_xclk_hz = 20000000;
  ledcSetup(LEDC_CHANNEL_0, probe_xclk_hz, 1);
  ledcAttachPin(CAM_PIN_XCLK, LEDC_CHANNEL_0);
  ledcWrite(LEDC_CHANNEL_0, 1);
  Serial.print("[camera_probe] xclk.enabled_hz=");
  Serial.println(static_cast<unsigned long>(probe_xclk_hz));
  delay(30);

  Wire.begin(CAM_PIN_SIOD, CAM_PIN_SIOC);
  Wire.setClock(100000);
  uint8_t i2c_found = 0;
  Serial.println("[camera_probe] i2c.scan.start=0x08..0x77");
  for (uint8_t addr = 0x08; addr <= 0x77; addr++) {
    Wire.beginTransmission(addr);
    const uint8_t rc = Wire.endTransmission();
    if (rc == 0) {
      Serial.print("[camera_probe] i2c.found=0x");
      if (addr < 16) {
        Serial.print('0');
      }
      Serial.println(addr, HEX);
      i2c_found++;
    }
  }
  Serial.print("[camera_probe] i2c.found_count=");
  Serial.println(static_cast<unsigned long>(i2c_found));

  Wire.beginTransmission(CAM_SCCB_ADDR);
  const uint8_t sccb_rc = Wire.endTransmission();
  Serial.print("[camera_probe] sccb.addr=0x");
  if (CAM_SCCB_ADDR < 16) {
    Serial.print('0');
  }
  Serial.println(CAM_SCCB_ADDR, HEX);
  Serial.print("[camera_probe] sccb.endTransmission_rc=");
  Serial.println(static_cast<unsigned long>(sccb_rc));
  if (sccb_rc == 0) {
    Serial.println("[camera_probe] SCCB OK");
  } else {
    Serial.println("[camera_probe] SCCB FAIL");
    Serial.println("[camera_probe] stop: physical seating/orientation or wrong camera pins");
    return;
  }

  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = CAM_PIN_D0;
  config.pin_d1 = CAM_PIN_D1;
  config.pin_d2 = CAM_PIN_D2;
  config.pin_d3 = CAM_PIN_D3;
  config.pin_d4 = CAM_PIN_D4;
  config.pin_d5 = CAM_PIN_D5;
  config.pin_d6 = CAM_PIN_D6;
  config.pin_d7 = CAM_PIN_D7;
  config.pin_xclk = CAM_PIN_XCLK;
  config.pin_pclk = CAM_PIN_PCLK;
  config.pin_vsync = CAM_PIN_VSYNC;
  config.pin_href = CAM_PIN_HREF;
  config.pin_sccb_sda = CAM_PIN_SIOD;
  config.pin_sccb_scl = CAM_PIN_SIOC;
  config.pin_pwdn = CAM_PIN_PWDN;
  config.pin_reset = CAM_PIN_RESET;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_QVGA;
  config.jpeg_quality = 12;
  config.fb_count = 1;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;

  const esp_err_t cam_init_rc = esp_camera_init(&config);
  Serial.print("[camera_probe] esp_camera_init rc=0x");
  Serial.println(static_cast<unsigned long>(cam_init_rc), HEX);
  Serial.print("[camera_probe] esp_camera_init rc_dec=");
  Serial.println(static_cast<long>(cam_init_rc));

  if (cam_init_rc != ESP_OK) {
    Serial.println("[camera_probe] camera init FAILED");
    return;
  }

  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[camera_probe] esp_camera_fb_get returned NULL");
  } else {
    Serial.print("[camera_probe] frame.len=");
    Serial.println(static_cast<unsigned long>(fb->len));
    Serial.print("[camera_probe] frame.width=");
    Serial.println(static_cast<unsigned long>(fb->width));
    Serial.print("[camera_probe] frame.height=");
    Serial.println(static_cast<unsigned long>(fb->height));
    Serial.print("[camera_probe] frame.format=");
    Serial.println(static_cast<unsigned long>(fb->format));
    esp_camera_fb_return(fb);
  }

  const esp_err_t cam_deinit_rc = esp_camera_deinit();
  Serial.print("[camera_probe] esp_camera_deinit rc=0x");
  Serial.println(static_cast<unsigned long>(cam_deinit_rc), HEX);
  Serial.print("[camera_probe] esp_camera_deinit rc_dec=");
  Serial.println(static_cast<long>(cam_deinit_rc));
  Serial.println("[camera_probe] done");
}

static void scanCameraBus() {
#if ENABLE_CAMERA
  struct CameraBus {
    int sda;
    int scl;
  };

  const CameraBus buses[] = {
      {40, 39},
      {5, 6},
      {7, 8},
      {1, 2},
      {2, 1},
      {3, 4},
      {4, 3},
      {8, 9},
      {9, 8},
      {17, 18},
      {18, 17},
      {41, 42},
      {42, 41}
  };

  cameraAddr = -1;
  for (size_t i = 0; i < (sizeof(buses) / sizeof(buses[0])); i++) {
    Wire.begin(buses[i].sda, buses[i].scl);
    Wire.setClock(10000);
    uint8_t found = 0;
    Serial.print("Camera SCCB scan sda=");
    Serial.print(buses[i].sda);
    Serial.print(" scl=");
    Serial.print(buses[i].scl);
    Serial.print(":");
    Serial1.print("CAMSCAN");
    Serial1.print(",sda=");
    Serial1.print(buses[i].sda);
    Serial1.print(",scl=");
    Serial1.print(buses[i].scl);

    for (uint8_t addr = 1; addr < 127; addr++) {
      Wire.beginTransmission(addr);
      if (Wire.endTransmission() == 0) {
        Serial.print(" 0x");
        if (addr < 16) {
          Serial.print('0');
        }
        Serial.print(addr, HEX);
        Serial1.print(',');
        Serial1.print(addr, HEX);
        found++;
        if (cameraAddr < 0) {
          cameraAddr = addr;
          cameraSda = buses[i].sda;
          cameraScl = buses[i].scl;
        }
        delay(2);
      }
    }

    if (found == 0) {
      Serial.print(" none");
      Serial1.print(",none");
    }
    Serial.println();
    Serial1.println();

    if (cameraAddr >= 0) {
      return;
    }
  }
#endif
}

static void initCamera() {
#if ENABLE_CAMERA
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 15;
  config.pin_d1 = 17;
  config.pin_d2 = 18;
  config.pin_d3 = 16;
  config.pin_d4 = 14;
  config.pin_d5 = 12;
  config.pin_d6 = 11;
  config.pin_d7 = 48;
  config.pin_xclk = 10;
  config.pin_pclk = 13;
  config.pin_vsync = 38;
  config.pin_href = 47;
  config.pin_sccb_sda = cameraSda;
  config.pin_sccb_scl = cameraScl;
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_QQVGA;
  config.pixel_format = PIXFORMAT_GRAYSCALE;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.jpeg_quality = 12;
  config.fb_count = 1;

  const int pwdnCandidates[] = { -1, 1 };
  esp_err_t err = ESP_FAIL;
  delay(200);
  for (size_t i = 0; i < (sizeof(pwdnCandidates) / sizeof(pwdnCandidates[0])); i++) {
    config.pin_pwdn = pwdnCandidates[i];
    if (config.pin_pwdn >= 0) {
      pinMode(config.pin_pwdn, OUTPUT);
      digitalWrite(config.pin_pwdn, LOW);
      delay(10);
    }

    err = esp_camera_init(&config);
    if (err != ESP_OK) {
      config.pixel_format = PIXFORMAT_RGB565;
      err = esp_camera_init(&config);
    }

    if (err == ESP_OK) {
      break;
    }

    delay(200);
  }

  cameraErr = static_cast<int>(err);

  if (err == ESP_OK) {
    cameraOk = true;
    Serial.println("Camera init OK");
  } else {
    cameraOk = false;
    Serial.print("Camera init failed: 0x");
    Serial.println(static_cast<unsigned long>(err), HEX);
  }
#endif
}

static void sampleCamera(uint32_t now) {
#if ENABLE_CAMERA
  if (!cameraOk || (now - lastCameraMs) < CAMERA_INTERVAL_MS) {
    return;
  }
  lastCameraMs = now;

  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Camera capture failed");
    return;
  }

  const int width = fb->width;
  const int height = fb->height;
  const int stride = SCENE_STRIDE;
  const int samplesX = width / stride;
  const int samplesY = height / stride;
  const int maxSamples = samplesX * samplesY;

  uint32_t sum = 0;
  uint32_t diffSum = 0;
  int sampleIndex = 0;

  if (fb->format == PIXFORMAT_GRAYSCALE) {
    for (int y = 0; y < height; y += stride) {
      for (int x = 0; x < width; x += stride) {
        const int idx = y * width + x;
        const uint8_t v = fb->buf[idx];
        sum += v;
        if (sampleIndex < SCENE_SAMPLES) {
          if (scenePrevValid) {
            diffSum += static_cast<uint32_t>(abs(static_cast<int>(v) - static_cast<int>(scenePrev[sampleIndex])));
          }
          scenePrev[sampleIndex] = v;
        }
        sampleIndex++;
      }
    }
  } else {
    const uint16_t *pixels = reinterpret_cast<const uint16_t *>(fb->buf);
    for (int y = 0; y < height; y += stride) {
      for (int x = 0; x < width; x += stride) {
        const int idx = y * width + x;
        const uint16_t pix = pixels[idx];
        const uint8_t r = (pix >> 11) & 0x1F;
        const uint8_t g = (pix >> 5) & 0x3F;
        const uint8_t b = pix & 0x1F;
        const uint8_t r8 = static_cast<uint8_t>((r * 255) / 31);
        const uint8_t g8 = static_cast<uint8_t>((g * 255) / 63);
        const uint8_t b8 = static_cast<uint8_t>((b * 255) / 31);
        const uint8_t lum = static_cast<uint8_t>((r8 * 30 + g8 * 59 + b8 * 11) / 100);
        sum += lum;
        if (sampleIndex < SCENE_SAMPLES) {
          if (scenePrevValid) {
            diffSum += static_cast<uint32_t>(abs(static_cast<int>(lum) - static_cast<int>(scenePrev[sampleIndex])));
          }
          scenePrev[sampleIndex] = lum;
        }
        sampleIndex++;
      }
    }
  }

  esp_camera_fb_return(fb);

  const int usedSamples = (sampleIndex < SCENE_SAMPLES) ? sampleIndex : SCENE_SAMPLES;
  if (usedSamples > 0) {
    cameraLight = static_cast<float>(sum) / static_cast<float>(usedSamples) / 255.0f;
    if (scenePrevValid) {
      cameraScene = static_cast<float>(diffSum) / static_cast<float>(usedSamples) / 255.0f;
    } else {
      cameraScene = 0.0f;
      scenePrevValid = true;
    }
  }
#else
  (void)now;
#endif
}

static bool initMic() {
#if ENABLE_MIC
#if HAS_ESP_I2S
  micI2S.setPinsPdmRx(MIC_PDM_CLK_PIN, MIC_PDM_DATA_PIN);
  if (!micI2S.begin(I2S_MODE_PDM_RX, MIC_SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println("PDM I2S init failed");
    micErr = -1;
    return false;
  }
  for (int i = 0; i < 256; i++) {
    (void)micI2S.read();
  }
  micErr = ESP_OK;
  return true;
#elif HAS_ARDUINO_I2S
  I2S.setAllPins(-1, MIC_PDM_CLK_PIN, MIC_PDM_DATA_PIN, -1, -1);
  if (!I2S.begin(PDM_MONO_MODE, MIC_SAMPLE_RATE, 16)) {
    Serial.println("PDM I2S init failed");
    micErr = -1;
    return false;
  }
  for (int i = 0; i < 256; i++) {
    (void)I2S.read();
  }
  micErr = ESP_OK;
  return true;
#else
  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
  config.sample_rate = MIC_SAMPLE_RATE;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = 0;
  config.dma_buf_count = 4;
  config.dma_buf_len = MIC_WINDOW_SAMPLES;
  config.use_apll = false;
  config.tx_desc_auto_clear = false;
  config.fixed_mclk = 0;

  i2s_pin_config_t pinConfig = {};
  pinConfig.bck_io_num = MIC_I2S_BCK_PIN;
  pinConfig.ws_io_num = MIC_I2S_WS_PIN;
  pinConfig.data_out_num = -1;
  pinConfig.data_in_num = MIC_I2S_DATA_PIN;

  esp_err_t err = i2s_driver_install(I2S_NUM_0, &config, 0, nullptr);
  if (err != ESP_OK) {
    micErr = static_cast<int>(err);
    Serial.print("I2S install failed: 0x");
    Serial.println(static_cast<unsigned long>(err), HEX);
    return false;
  }

  err = i2s_set_pin(I2S_NUM_0, &pinConfig);
  if (err != ESP_OK) {
    micErr = static_cast<int>(err);
    Serial.print("I2S set pin failed: 0x");
    Serial.println(static_cast<unsigned long>(err), HEX);
    i2s_driver_uninstall(I2S_NUM_0);
    return false;
  }

  i2s_zero_dma_buffer(I2S_NUM_0);
  micErr = ESP_OK;
  return true;
#endif
#else
  return false;
#endif
}

static void sampleMic(uint32_t now) {
#if ENABLE_MIC
  if (!micOk || (now - lastMicMs) < MIC_UPDATE_MS) {
    return;
  }
  lastMicMs = now;

  size_t bytesRead = 0;
#if HAS_ESP_I2S
  bytesRead = micI2S.read(micSamples, sizeof(micSamples));
  if (bytesRead == 0) {
    return;
  }
#elif HAS_ARDUINO_I2S
  bytesRead = I2S.read(reinterpret_cast<uint8_t *>(micSamples), sizeof(micSamples));
  if (bytesRead == 0) {
    return;
  }
#else
  esp_err_t err = i2s_read(
      I2S_NUM_0,
      micSamples,
      sizeof(micSamples),
      &bytesRead,
      pdMS_TO_TICKS(20));
  if (err != ESP_OK || bytesRead == 0) {
    return;
  }
#endif

  const size_t sampleCount = bytesRead / sizeof(int16_t);
  if (sampleCount == 0) {
    return;
  }

  int32_t peak = 0;
  int64_t sumSquares = 0;
  for (size_t i = 0; i < sampleCount; i++) {
    const int32_t v = micSamples[i];
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

  micRms = rms;
  micPeak = pk;
  if (isnan(micNoiseFloor)) {
    micNoiseFloor = rms;
  } else {
    micNoiseFloor += MIC_NOISE_ALPHA * (rms - micNoiseFloor);
  }
  micDelta = (rms > micNoiseFloor) ? (rms - micNoiseFloor) : 0.0f;
#else
  (void)now;
#endif
}

static void initWifi() {
#if ENABLE_WIFI && HAS_WIFI_SECRETS
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  lastWifiBeginMs = millis();
  lastWifiCheckMs = lastWifiBeginMs;
#endif
}

static void updateWifi(uint32_t now) {
#if ENABLE_WIFI && HAS_WIFI_SECRETS
  if (now - lastWifiCheckMs < WIFI_CHECK_MS) {
    return;
  }
  lastWifiCheckMs = now;

  wifiStatus = WiFi.status();
  if (wifiStatus == WL_CONNECTED) {
    espRssi = WiFi.RSSI();
    if (!ntpConfigured) {
      configTime(0, 0, "pool.ntp.org", "time.nist.gov");
      ntpConfigured = true;
    }
  } else {
    espRssi = RSSI_NOT_CONNECTED;
    if (now - lastWifiBeginMs >= WIFI_RETRY_MS) {
      WiFi.begin(WIFI_SSID, WIFI_PASS);
      lastWifiBeginMs = now;
    }
  }

  const time_t nowSec = time(nullptr);
  if (nowSec > static_cast<time_t>(NTP_VALID_AFTER)) {
    ntpEpoch = static_cast<uint32_t>(nowSec);
  } else {
    ntpEpoch = 0;
  }

  const bool reportDue = (now - lastWifiReportMs) >= 5000;
  if (reportDue) {
    lastWifiReportMs = now;
    const int currentRssi = (wifiStatus == WL_CONNECTED) ? WiFi.RSSI() : RSSI_NOT_CONNECTED;
    IPAddress localIp = WiFi.localIP();
    IPAddress gatewayIp = WiFi.gatewayIP();
    const bool hasIp =
        (wifiStatus == WL_CONNECTED)
        && (localIp[0] != 0 || localIp[1] != 0 || localIp[2] != 0 || localIp[3] != 0);
    const bool hasGw =
        (wifiStatus == WL_CONNECTED)
        && (gatewayIp[0] != 0 || gatewayIp[1] != 0 || gatewayIp[2] != 0 || gatewayIp[3] != 0);
    char ipField[20];
    if (hasIp) {
      snprintf(ipField, sizeof(ipField), "%u.%u.%u.%u", localIp[0], localIp[1], localIp[2], localIp[3]);
    } else {
      snprintf(ipField, sizeof(ipField), "none");
    }
    char netLine[256];
    if (hasGw) {
      snprintf(
          netLine,
          sizeof(netLine),
          "ESP,NET,n=%lu,wifist=%d,rssi=%d,ntp=%lu,camok=%d,camerr=%d,micok=%d,micerr=%d,camaddr=%d,ip=%s,gw=%u.%u.%u.%u\n",
          static_cast<unsigned long>(++wifiReportSeq),
          wifiStatus,
          currentRssi,
          static_cast<unsigned long>(ntpEpoch),
          cameraOk ? 1 : 0,
          cameraErr,
          micOk ? 1 : 0,
          micErr,
          cameraAddr,
          ipField,
          gatewayIp[0],
          gatewayIp[1],
          gatewayIp[2],
          gatewayIp[3]);
    } else {
      snprintf(
          netLine,
          sizeof(netLine),
          "ESP,NET,n=%lu,wifist=%d,rssi=%d,ntp=%lu,camok=%d,camerr=%d,micok=%d,micerr=%d,camaddr=%d,ip=%s\n",
          static_cast<unsigned long>(++wifiReportSeq),
          wifiStatus,
          currentRssi,
          static_cast<unsigned long>(ntpEpoch),
          cameraOk ? 1 : 0,
          cameraErr,
          micOk ? 1 : 0,
          micErr,
          cameraAddr,
          ipField);
    }
    Serial1.print(netLine);
  }
#else
  (void)now;
  espRssi = RSSI_NOT_CONNECTED;
  ntpEpoch = 0;
  wifiStatus = -1;
#endif
}

static void handleSerial1Commands() {
#if ENABLE_ESP_CMD
  while (Serial1.available() > 0) {
    const char c = static_cast<char>(Serial1.read());
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      cmdBuffer[cmdLen] = '\0';
      if (strcmp(cmdBuffer, "CMD,reboot") == 0) {
        Serial.println("ESP CMD reboot");
        delay(20);
        ESP.restart();
      }
      cmdLen = 0;
      continue;
    }
    if (cmdLen + 1 < sizeof(cmdBuffer)) {
      cmdBuffer[cmdLen++] = c;
    } else {
      cmdLen = 0;
    }
  }
#endif
}

static void sendTelemetryLine() {
  const uint32_t uptimeSec = millis() / 1000;
  const uint32_t frame = ++packetCounter;
  const int rssi = espRssi;
  const uint32_t heap = ESP.getFreeHeap();
  const uint32_t psram = ESP.getFreePsram();
  const float tempC = temperatureRead();

  char ctBuffer[16];
  if (isnan(tempC)) {
    snprintf(ctBuffer, sizeof(ctBuffer), "nan");
  } else {
    snprintf(ctBuffer, sizeof(ctBuffer), "%.2f", tempC);
  }
  char line[260];
  snprintf(
      line,
      sizeof(line),
      "%sup=%lu,n=%lu,camok=%d,camerr=%d,micok=%d,micerr=%d,camaddr=%d,wifist=%d,rssi=%d,ntp=%lu,heap=%lu,psram=%lu,ct=%s\n",
      SENS_PREFIX,
      static_cast<unsigned long>(uptimeSec),
      static_cast<unsigned long>(frame),
      cameraOk ? 1 : 0,
      cameraErr,
      micOk ? 1 : 0,
      micErr,
      cameraAddr,
      wifiStatus,
      rssi,
      static_cast<unsigned long>(ntpEpoch),
      static_cast<unsigned long>(heap),
      static_cast<unsigned long>(psram),
      ctBuffer);

  Serial1.print(line);
}

void setup() {
  Serial.begin(115200);
#if LD2410_UART_BRINGUP_MODE
  delay(200);
  waitForSerial(1500);
  Serial.println("LD2410 UART bring-up starting...");
  ld2410Init();
  Serial.println("Waiting for radar bytes...");
  return;
#endif
#if CAMERA_PROBE_MODE
  delay(50);
  waitForSerial(1500);
  runCameraProbe();
  return;
#endif
  Serial1.setPins(UART_RX_PIN, UART_TX_PIN);
  Serial1.begin(UART_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
  delay(50);
  waitForSerial(1500);
  ld2410Init();
  Serial.println("ESP32 telemetry sender ready");
  scanCameraBus();
  initCamera();
  micOk = initMic();
  initWifi();
}

void loop() {
#if LD2410_UART_BRINGUP_MODE
  ld2410Poll();
  ld2410EmitIfNeeded(millis());
  delay(10);
  return;
#endif
#if CAMERA_PROBE_MODE
  delay(1000);
  return;
#endif
  ld2410Poll();
  const uint32_t now = millis();
  ld2410EmitIfNeeded(now);
  handleSerial1Commands();
  sampleCamera(now);
  sampleMic(now);
  updateWifi(now);
  if (now - lastSendMs >= 1000) {
    lastSendMs = now;
    sendTelemetryLine();
  }
}
