#include <Arduino.h>
#include <math.h>

#include "hermes_protocol.h"

static const uint8_t UART_RX_PIN = 44;
static const uint8_t UART_TX_PIN = 43;

static uint32_t lastSendMs = 0;

static void sendTelemetryLine() {
  const uint32_t uptimeSec = millis() / 1000;
  const int rssi = RSSI_NOT_CONNECTED;
  const uint32_t heap = ESP.getFreeHeap();
  const uint32_t psram = ESP.getFreePsram();
  const float tempC = temperatureRead();

  char ctBuffer[16];
  if (isnan(tempC)) {
    snprintf(ctBuffer, sizeof(ctBuffer), "nan");
  } else {
    snprintf(ctBuffer, sizeof(ctBuffer), "%.2f", tempC);
  }

  char line[128];
  snprintf(
      line,
      sizeof(line),
      "%sup=%lu,rssi=%d,heap=%lu,psram=%lu,ct=%s\n",
      FRAME_PREFIX,
      static_cast<unsigned long>(uptimeSec),
      rssi,
      static_cast<unsigned long>(heap),
      static_cast<unsigned long>(psram),
      ctBuffer);

  Serial1.print(line);
}

void setup() {
  Serial.begin(UART_BAUD);
  Serial1.begin(UART_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
  delay(50);
  Serial.println("ESP32 telemetry sender ready");
}

void loop() {
  const uint32_t now = millis();
  if (now - lastSendMs >= 1000) {
    lastSendMs = now;
    sendTelemetryLine();
  }
}
