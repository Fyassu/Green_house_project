#include <DHTesp.h>
#include <ESP32Servo.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <WiFi.h>
#include <time.h>
#include <PubSubClient.h>

// STATES
enum PlantState {
  GOOD,
  SLOW,
  DECLINE,
  CRITICAL,
  DEAD
};

const char* ssid      = "Wokwi-GUEST";
// Dùng tên miền thay vì IP tĩnh: broker.hivemq.com là load-balancer với nhiều
// IP xoay vòng phía sau, IP tĩnh cũ (3.120.44.48) đã die (test TCP timeout
// khi kiểm thử) — khớp đúng địa chỉ backend/app.py đang dùng.
const char* mqtt_server = "test.mosquitto.org";

WiFiClient espClient;
PubSubClient mqttClient(espClient);




// ── Real-time Timing Configuration ──
const unsigned long SEND_INTERVAL_MS = 3000;  // Gửi lên MQTT mỗi 3 giây
const unsigned long SENSOR_READ_MS   = 200;   // Đọc cảm biến LDR/Soil + cập nhật LCD mỗi 200ms
const unsigned long DHT_READ_MS      = 2000;  // DHT22 cần ít nhất 2 giây (2000ms) để không bị lỗi NaN
const unsigned long MOTION_LIGHT_MS  = 15000; // Đèn bật 15 giây
const unsigned long RECONNECT_INTERVAL_MS = 5000; // Thử kết nối lại MQTT mỗi 5 giây
const unsigned long PRINT_STATUS_MS  = 5000;  // In Serial Monitor mỗi 5 giây (tránh lag Wokwi)

// PIN CONFIG
#define DHT_PIN          15

#define SERVO_PIN        18

#define PUMP_LED_PIN      5
#define FAN_LED_PIN       4
#define ALARM_LED_PIN    27

#define LDR_PIN          34
#define SOIL_PIN         35
#define PIR_PIN          14

#define BUZZER_PIN       23

// OBJECTS
DHTesp dht;
Servo shadeServo;
LiquidCrystal_I2C lcd(0x27, 16, 2);

// STATES
PlantState plantState = GOOD;
PlantState previousState = GOOD;

// SENSOR DATA — volatile vì chia sẻ giữa 2 lõi CPU
volatile float temperature = 0;
volatile float humidity    = 0;
volatile int   soilPercent = 0;
volatile int   lightPercent = 0;
volatile bool  motionDetected = false;

// COMMAND OVERRIDES (-1 = auto, 0/1 = override for fan/pump, 0/45/90 = servo)
volatile int8_t fanCmd   = -1;
volatile int8_t pumpCmd  = -1;
volatile int    servoCmd = -1;
volatile int8_t lightCmd = -1;
volatile bool securityMode = true; // Bật chức năng bảo vệ mặc định

// ACTUATOR STATES — volatile vì chia sẻ giữa 2 lõi CPU
volatile bool fanState = false;
volatile bool pumpState = false;
volatile bool lightStatus = false;

// External shade above the closed greenhouse roof:
// 0° = retracted, 45° = half deployed, 90° = fully deployed.
volatile int shadePosition = 0;

unsigned long motionLightOnTime = 0;
bool motionLightEverTriggered = false;

void sendData()
{
  if (WiFi.status() == WL_CONNECTED && mqttClient.connected())
  {
    String json = "{";
    json += "\"temperature\":"    + String(temperature, 1)        + ",";
    json += "\"humidity\":"       + String(humidity, 1)           + ",";
    json += "\"soil_moisture\":"  + String(soilPercent)           + ",";
    json += "\"light_level\":"    + String(lightPercent)          + ",";
    json += "\"motion_detected\":" + String(motionDetected ? "true" : "false") + ",";
    json += "\"fan_status\":"     + String(fanState    ? "true" : "false") + ",";
    json += "\"pump_status\":"    + String(pumpState   ? "true" : "false") + ",";
    json += "\"servo_angle\":"    + String(shadePosition)                     + ",";
    json += "\"light_status\":"   + String(lightStatus ? "true" : "false");
    json += "}";

    Serial.println("Sending JSON: " + json);
    mqttClient.publish("greenhouse/sensors/data", json.c_str());
  }
}

unsigned long lastSend       = 0UL - SEND_INTERVAL_MS;  // triggers on first loop iteration
unsigned long lastSensorRead = 0;
unsigned long lastDHTRead    = 0UL - DHT_READ_MS;
unsigned long lastReconnectAttempt = 0;
unsigned long lastPrintStatus = 0;

volatile bool motionSendFlag = false;

// JSON PARSERS (dùng cho lệnh điều khiển nhận qua MQTT)
int8_t parseJsonBool(const String& json, const String& key) {
  String search = "\"" + key + "\":";
  int idx = json.indexOf(search);
  if (idx < 0) return -1;
  idx += search.length();
  while (idx < (int)json.length() && json[idx] == ' ') idx++;
  if (json.substring(idx, idx + 4) == "null")  return -1;
  if (json.substring(idx, idx + 4) == "true")  return 1;
  if (json.substring(idx, idx + 5) == "false") return 0;
  return -1;
}

int parseJsonInt(const String& json, const String& key) {
  String search = "\"" + key + "\":";
  int idx = json.indexOf(search);
  if (idx < 0) return -1;
  idx += search.length();
  while (idx < (int)json.length() && json[idx] == ' ') idx++;
  if (json.substring(idx, idx + 4) == "null") return -1;
  int val = 0;
  while (idx < (int)json.length() && isdigit((unsigned char)json[idx])) {
    val = val * 10 + (json[idx] - '0');
    idx++;
  }
  return val;
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String message;
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.println("Received JSON: " + message);

  fanCmd   = parseJsonBool(message, "fan");
  pumpCmd  = parseJsonBool(message, "pump");
  servoCmd = parseJsonInt(message,  "servo");
  lightCmd = parseJsonBool(message, "light");
  
  int8_t sec = parseJsonBool(message, "security");
  if (sec >= 0) {
    securityMode = (sec == 1);
  }
}

void mqttReconnectNonBlocking() {
  if (millis() - lastReconnectAttempt > RECONNECT_INTERVAL_MS) {
    lastReconnectAttempt = millis();
    
    String clientId = "ESP32Client-";
    clientId += String(random(0xffff), HEX);
    
    if (mqttClient.connect(clientId.c_str())) {
      mqttClient.subscribe("greenhouse/commands/control");
    }
  }
}

// BUZZER
void playTone(int freq, int dur) {
  ledcWriteTone(0, freq);
  delay(dur);
}

void stopTone() {
  ledcWriteTone(0, 0);
}

void shadeRetractMelody() {
  playTone(262,150);
  playTone(330,150);
  playTone(392,200);
  stopTone();
}

void shadeDeployMelody() {
  playTone(392,150);
  playTone(330,150);
  playTone(262,200);
  stopTone();
}

void irrigationMelody() {
  playTone(262,150);
  playTone(294,150);
  playTone(330,200);
  stopTone();
}

void warningMelody() {
  for(int i=0;i<3;i++) {
    playTone(1000,100);
    stopTone();
    delay(100);
  }
}

// READ SENSORS
void readSensors() {
  if (millis() - lastDHTRead >= DHT_READ_MS) {
    TempAndHumidity data = dht.getTempAndHumidity();
    if (!isnan(data.temperature)) temperature = data.temperature;
    if (!isnan(data.humidity))    humidity  = data.humidity;
    lastDHTRead = millis();
  }

  int soilRaw  = analogRead(SOIL_PIN);
  int lightRaw = analogRead(LDR_PIN);
  // Khi chân SOIL_PIN (pin 35) chưa nối hoặc ở mức 0V, mặc định ở mức 65% độ ẩm đất nhà kính lý tưởng (60% - 80%)
  soilPercent  = (soilRaw == 0) ? 65 : (int)map(soilRaw, 0, 4095, 0, 100);
  lightPercent = (int)map(lightRaw, 0, 4095, 0, 100);
}

// PLANT HEALTH
PlantState evaluatePlant() {
  if(temperature > 45 || temperature < 5  || soilPercent < 5)                      return DEAD;
  if(temperature > 38 || temperature < 10 || soilPercent < 20 || humidity < 20)    return CRITICAL;
  if(temperature > 34 || temperature < 15 || soilPercent < 35 || humidity < 35)    return DECLINE;
  if(temperature > 30 || temperature < 20 || soilPercent < 50 || humidity < 45)    return SLOW;
  return GOOD;
}

// FAN CONTROL
void controlFan() {
  if (fanCmd >= 0) {
    fanState = (fanCmd == 1);
  } else {
    if (!fanState && temperature > 30) fanState = true;
    if ( fanState && temperature < 28) fanState = false;
  }
  digitalWrite(FAN_LED_PIN, fanState);
}

// PUMP CONTROL
void controlPump() {
  static bool lastPump = false;
  if (pumpCmd >= 0) {
    pumpState = (pumpCmd == 1);
  } else {
    if (!pumpState && soilPercent < 45) pumpState = true;
    if ( pumpState && soilPercent > 65) pumpState = false;
  }
  digitalWrite(PUMP_LED_PIN, pumpState);
  if (pumpState && !lastPump) irrigationMelody();
  lastPump = pumpState;
}

// EXTERNAL SHADE CONTROL — the greenhouse roof itself always stays closed.
void moveShade(int target) {
  if(target == shadePosition) return;
  shadeServo.write(target);
  if(target < shadePosition) shadeRetractMelody();
  else                       shadeDeployMelody();
  shadePosition = target;
}

void controlShade() {
  if (servoCmd >= 0) { moveShade(servoCmd); return; }
  if (temperature > 34 || lightPercent > 85) { moveShade(90); return; }
  if (lightPercent > 65)                     { moveShade(45); return; }
  moveShade(0);
}

// MOTION LIGHT
void controlMotionLight() {
  if (lightCmd >= 0) {
    lightStatus = (lightCmd == 1);
  } else {
    // Chế độ tự động
    if (!securityMode) {
      // Khi TẮT chế độ bảo vệ: phát hiện chuyển động thì tự động bật đèn cảnh báo
      if (motionDetected) {
        motionLightOnTime = millis();
        motionLightEverTriggered = true;
      }
      lightStatus = motionLightEverTriggered && (millis() - motionLightOnTime < MOTION_LIGHT_MS);
    } else {
      // Khi BẬT bảo vệ: Đèn không tự động sáng để còi hú làm nhiệm vụ cảnh báo chính
      lightStatus = false;
    }
  }
  digitalWrite(ALARM_LED_PIN, lightStatus ? HIGH : LOW);

  // Nếu bảo vệ BẬT và phát hiện chuyển động -> Còi cảnh báo hú báo động
  if (securityMode && motionDetected) {
    warningMelody();
  }
}

// CRITICAL ALERT
void criticalMonitor() {
  if(plantState != previousState) {
    if(plantState == CRITICAL) warningMelody();
    if(plantState == DEAD) {
      for(int i=0;i<5;i++) { playTone(1500,200); stopTone(); delay(100); }
    }
  }
  previousState = plantState;
}

// LCD — chỉ ghi lại khi giá trị thay đổi (đỡ I2C load cho Wokwi)
float lcdLastTemp = -999;
int   lcdLastSoil = -1;
float lcdLastHum  = -999;
int   lcdLastLight = -1;

void updateLCD() {
  // So sánh giá trị hiện tại với giá trị trên LCD
  bool tempChanged  = ((int)(temperature * 10) != (int)(lcdLastTemp * 10));
  bool soilChanged  = (soilPercent != lcdLastSoil);
  bool humChanged   = ((int)(humidity * 10) != (int)(lcdLastHum * 10));
  bool lightChanged = (lightPercent != lcdLastLight);

  if (!tempChanged && !soilChanged && !humChanged && !lightChanged) return;

  if (tempChanged || soilChanged) {
    char line1[17];
    snprintf(line1, sizeof(line1), "T:%2.1f S:%02d%%", temperature, soilPercent);
    lcd.setCursor(0, 0);
    lcd.print(line1);
    lcd.print("  ");  // xoá rác cuối dòng
    lcdLastTemp = temperature;
    lcdLastSoil = soilPercent;
  }

  if (humChanged || lightChanged) {
    char line2[17];
    snprintf(line2, sizeof(line2), "H:%2.1f L:%02d%%", humidity, lightPercent);
    lcd.setCursor(0, 1);
    lcd.print(line2);
    lcd.print("  ");  // xoá rác cuối dòng
    lcdLastHum = humidity;
    lcdLastLight = lightPercent;
  }
}

// (Removed printStatus)

// FreeRTOS TASK: Chạy trên Core 0 (MQTT + AES + Serial)
void mqttTask(void* parameter) {
  unsigned long taskLastSend = 0;
  unsigned long taskLastReconnect = 0;

  for (;;) {
    if (!mqttClient.connected()) {
      if (millis() - taskLastReconnect > RECONNECT_INTERVAL_MS) {
        taskLastReconnect = millis();
        String clientId = "ESP32Client-" + String(random(0xffff), HEX);
        if (mqttClient.connect(clientId.c_str())) {
          mqttClient.subscribe("greenhouse/commands/control");
        }
      }
    } else {
      mqttClient.loop();
    }

    if (motionSendFlag || (millis() - taskLastSend >= SEND_INTERVAL_MS)) {
      motionSendFlag = false;
      sendData();
      taskLastSend = millis();
    }

    vTaskDelay(10 / portTICK_PERIOD_MS); // loop mỗi 10ms ảo để giữ kết nối MQTT
  }
}

// SETUP
void setup() {
  Serial.begin(115200);
  dht.setup(DHT_PIN, DHTesp::DHT22);
  shadeServo.attach(SERVO_PIN, 500, 2400);
  lcd.init();
  lcd.backlight();

  pinMode(PUMP_LED_PIN,  OUTPUT);
  pinMode(FAN_LED_PIN,   OUTPUT);
  pinMode(ALARM_LED_PIN, OUTPUT);
  pinMode(PIR_PIN, INPUT);

  ledcSetup(0, 2000, 8);
  ledcAttachPin(BUZZER_PIN, 0);

  WiFi.begin("Wokwi-GUEST", "");
  espClient.setTimeout(1000); // Tránh kết nối mạng treo quá lâu gây lỗi Watchdog
  while(WiFi.status() != WL_CONNECTED) delay(100);

  mqttClient.setServer(mqtt_server, 1883);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setBufferSize(512);

  moveShade(0);

  // Tạo task MQTT chạy trên Core 0 (Core 1 chạy loop chính)
  xTaskCreatePinnedToCore(
    mqttTask,
    "MQTTTask",
    8192,
    NULL,
    0,   // Đặt độ ưu tiên = 0 để chia sẻ CPU với IDLE0, tránh lỗi Watchdog
    NULL,
    0
  );
}

// LOOP — chạy trên Core 1, CHỈ xử lý cảm biến và LCD
void loop() {
  bool currentMotion = digitalRead(PIR_PIN);
  if (currentMotion != motionDetected) {
    motionDetected = currentMotion;
    controlMotionLight();
    motionSendFlag = true;
  }

  if (millis() - lastSensorRead >= SENSOR_READ_MS) {
    readSensors();
    plantState = evaluatePlant();
    controlFan();
    controlPump();
    controlShade();
    controlMotionLight();
    criticalMonitor();
    updateLCD();
    lastSensorRead = millis();
  }

  delay(20); // Tăng delay ảo lên 20ms giúp giảm tải CPU Wokwi, kéo tốc độ mô phỏng (Simulation Speed) lên 100%
}
