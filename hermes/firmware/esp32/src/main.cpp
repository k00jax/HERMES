#include <Arduino.h>
#include <math.h>
#include <Wire.h>

#include "esp_camera.h"
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

#define ENABLE_ESP_CMD 1
#define ENABLE_CAMERA 0
#define ENABLE_MIC 0
#define ENABLE_WIFI 1

static const uint8_t UART_RX_PIN = D7;
static const uint8_t UART_TX_PIN = D6;

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
  char line[220];
  snprintf(
      line,
      sizeof(line),
      "%sup=%lu,n=%lu,rssi=%d,ntp=%lu,heap=%lu,psram=%lu,ct=%s\n",
      SENS_PREFIX,
      static_cast<unsigned long>(uptimeSec),
      static_cast<unsigned long>(frame),
      rssi,
      static_cast<unsigned long>(ntpEpoch),
      static_cast<unsigned long>(heap),
      static_cast<unsigned long>(psram),
      ctBuffer);

  Serial1.print(line);
}

void setup() {
  Serial.begin(115200);
  Serial1.setPins(UART_RX_PIN, UART_TX_PIN);
  Serial1.begin(UART_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
  delay(50);
  waitForSerial(1500);
  Serial.println("ESP32 telemetry sender ready");
  scanCameraBus();
  initCamera();
  initMic();
  initWifi();
}

void loop() {
  const uint32_t now = millis();
  handleSerial1Commands();
  sampleCamera(now);
  sampleMic(now);
  updateWifi(now);
  if (now - lastSendMs >= 1000) {
    lastSendMs = now;
    sendTelemetryLine();
  }
}
