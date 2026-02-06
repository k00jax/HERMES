#include <Arduino.h>
#include <math.h>

#include "esp_camera.h"

#include "hermes_protocol.h"

#define ENABLE_ESP_CMD 1

static const uint8_t UART_RX_PIN = D7;
static const uint8_t UART_TX_PIN = D6;

static const uint32_t CAMERA_INTERVAL_MS = 2000;
static const int SCENE_STRIDE = 4;
static const int SCENE_SAMPLES = (160 / SCENE_STRIDE) * (120 / SCENE_STRIDE);

static uint32_t lastSendMs = 0;
static uint32_t lastCameraMs = 0;
static uint32_t packetCounter = 0;
static bool cameraOk = false;
static float cameraLight = NAN;
static float cameraScene = NAN;
static uint8_t scenePrev[SCENE_SAMPLES];
static bool scenePrevValid = false;

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

static void initCamera() {
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
  config.pin_sccb_sda = 40;
  config.pin_sccb_scl = 39;
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_QQVGA;
  config.pixel_format = PIXFORMAT_GRAYSCALE;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.jpeg_quality = 12;
  config.fb_count = 1;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    config.pixel_format = PIXFORMAT_RGB565;
    err = esp_camera_init(&config);
  }

  if (err == ESP_OK) {
    cameraOk = true;
    Serial.println("Camera init OK");
  } else {
    cameraOk = false;
    Serial.print("Camera init failed: 0x");
    Serial.println(static_cast<unsigned long>(err), HEX);
  }
}

static void sampleCamera(uint32_t now) {
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
  const int rssi = RSSI_NOT_CONNECTED;
  const uint32_t heap = ESP.getFreeHeap();
  const uint32_t psram = ESP.getFreePsram();
  const float tempC = temperatureRead();

  char ctBuffer[16];
  char lightBuffer[16];
  char sceneBuffer[16];
  if (isnan(tempC)) {
    snprintf(ctBuffer, sizeof(ctBuffer), "nan");
  } else {
    snprintf(ctBuffer, sizeof(ctBuffer), "%.2f", tempC);
  }
  formatFloat(lightBuffer, sizeof(lightBuffer), cameraLight, 2);
  formatFloat(sceneBuffer, sizeof(sceneBuffer), cameraScene, 2);

  char line[180];
  snprintf(
      line,
      sizeof(line),
      "%sup=%lu,n=%lu,rssi=%d,heap=%lu,psram=%lu,ct=%s,light=%s,scene=%s\n",
      SENS_PREFIX,
      static_cast<unsigned long>(uptimeSec),
      static_cast<unsigned long>(frame),
      rssi,
      static_cast<unsigned long>(heap),
      static_cast<unsigned long>(psram),
      ctBuffer,
      lightBuffer,
      sceneBuffer);

  Serial1.print(line);
}

void setup() {
  Serial.begin(115200);
  Serial1.setPins(UART_RX_PIN, UART_TX_PIN);
  Serial1.begin(UART_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
  delay(50);
  Serial.println("ESP32 telemetry sender ready");
  initCamera();
}

void loop() {
  const uint32_t now = millis();
  handleSerial1Commands();
  sampleCamera(now);
  if (now - lastSendMs >= 1000) {
    lastSendMs = now;
    sendTelemetryLine();
  }
}
