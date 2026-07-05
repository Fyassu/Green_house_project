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
const char* mqtt_server = "broker.hivemq.com";

WiFiClient espClient;
PubSubClient mqttClient(espClient);

#include "mbedtls/aes.h"
#include "mbedtls/base64.h"

const char* AES_KEY = "MySuperSecretKey";
const char* AES_IV  = "1234567890123456";

String encryptAES(String plainText) {
  mbedtls_aes_context aes;
  mbedtls_aes_init(&aes);
  mbedtls_aes_setkey_enc(&aes, (const unsigned char*)AES_KEY, 128);

  int len = plainText.length();
  int pad = 16 - (len % 16);
  int paddedLen = len + pad;
  unsigned char* paddedData = (unsigned char*)malloc(paddedLen);
  memcpy(paddedData, plainText.c_str(), len);
  for (int i = len; i < paddedLen; i++) paddedData[i] = pad;

  unsigned char* encryptedData = (unsigned char*)malloc(paddedLen);
  unsigned char iv[16];
  memcpy(iv, AES_IV, 16);

  mbedtls_aes_crypt_cbc(&aes, MBEDTLS_AES_ENCRYPT, paddedLen, iv, paddedData, encryptedData);

  size_t olen = 0;
  mbedtls_base64_encode(NULL, 0, &olen, encryptedData, paddedLen);
  unsigned char* base64Data = (unsigned char*)malloc(olen + 1);
  mbedtls_base64_encode(base64Data, olen, &olen, encryptedData, paddedLen);
  base64Data[olen] = '\0';

  String result = String((char*)base64Data);

  free(paddedData);
  free(encryptedData);
  free(base64Data);
  mbedtls_aes_free(&aes);

  return result;
}

String decryptAES(String base64Text) {
  size_t olen = 0;
  mbedtls_base64_decode(NULL, 0, &olen, (const unsigned char*)base64Text.c_str(), base64Text.length());
  unsigned char* encryptedData = (unsigned char*)malloc(olen);
  mbedtls_base64_decode(encryptedData, olen, &olen, (const unsigned char*)base64Text.c_str(), base64Text.length());

  mbedtls_aes_context aes;
  mbedtls_aes_init(&aes);
  mbedtls_aes_setkey_dec(&aes, (const unsigned char*)AES_KEY, 128);

  unsigned char* decryptedData = (unsigned char*)malloc(olen);
  unsigned char iv[16];
  memcpy(iv, AES_IV, 16);

  mbedtls_aes_crypt_cbc(&aes, MBEDTLS_AES_DECRYPT, olen, iv, encryptedData, decryptedData);

  int pad = decryptedData[olen - 1];
  String result = "";
  if(pad > 0 && pad <= 16) {
    int originalLen = (int)olen - pad;
    if(originalLen > 0) {
      decryptedData[originalLen] = '\0';
      result = String((char*)decryptedData);
    }
  }

  free(encryptedData);
  free(decryptedData);
  mbedtls_aes_free(&aes);

  return result;
}


// ── Simulation speed ── 12× (1 simulated minute = 5 real seconds)
const unsigned long SIM_SPEED        = 12;
const unsigned long SEND_INTERVAL_MS = 10000UL / SIM_SPEED;  //  833 ms
const unsigned long SENSOR_READ_MS   =  2000UL / SIM_SPEED;  //  167 ms
const unsigned long MOTION_LIGHT_MS  = 15000UL / SIM_SPEED;  // 1250 ms

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
Servo roofServo;
LiquidCrystal_I2C lcd(0x27, 16, 2);

// STATES
PlantState plantState = GOOD;
PlantState previousState = GOOD;

// SENSOR DATA — effective values (ambient + actuator feedback), used everywhere
float temperature = 0;
float humidity    = 0;
int   soilPercent = 0;
int   lightPercent = 0;
bool  motionDetected = false;

// AMBIENT — raw hardware readings from Wokwi sensors
float ambientTemp  = 0.0f;
float ambientHum   = 0.0f;
int   ambientSoil  = 0;
int   ambientLight = 0;

// EFFECTIVE — accumulate actuator feedback over time
float effTemp  = 0.0f;
float effHum   = 0.0f;
float effSoil  = 0.0f;
float effLight = 0.0f;
bool  feedbackReady = false;

// COMMAND OVERRIDES (-1 = auto, 0/1 = override for fan/pump, 0/45/90 = servo)
int8_t fanCmd   = -1;
int8_t pumpCmd  = -1;
int    servoCmd = -1;

// ACTUATOR STATES
bool fanState = false;
bool pumpState = false;
bool lightStatus = false;

int roofPosition = 90;

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
    json += "\"servo_angle\":"    + String(roofPosition)                      + ",";
    json += "\"light_status\":"   + String(lightStatus ? "true" : "false");
    json += "}";

    String encrypted = encryptAES(json);

    Serial.println("\n[ESP32] Gửi dữ liệu cảm biến (Publish):");
    Serial.println(" -> Raw JSON: " + json);
    Serial.println(" -> Encrypted: " + encrypted);

    mqttClient.publish("greenhouse/sensors/data", encrypted.c_str());
  }
}

unsigned long lastSend       = 0UL - SEND_INTERVAL_MS;  // triggers on first loop iteration
unsigned long lastSensorRead = 0;

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
  Serial.println("\n[ESP32] Nhận lệnh điều khiển (Subscribe):");
  Serial.print(" <- Encrypted Raw: ");
  Serial.println(message);

  String decrypted = decryptAES(message);
  Serial.print(" <- Decrypted JSON: ");
  Serial.println(decrypted);

  fanCmd   = parseJsonBool(decrypted, "fan");
  pumpCmd  = parseJsonBool(decrypted, "pump");
  servoCmd = parseJsonInt(decrypted,  "servo");
}

void mqttReconnect() {
  int retries = 0;
  while (!mqttClient.connected() && retries < 5) {
    retries++;
    Serial.print("Attempting MQTT connection...");
    String clientId = "ESP32Client-";
    clientId += String(random(0xffff), HEX);
    if (mqttClient.connect(clientId.c_str())) {
      Serial.println("connected");
      mqttClient.subscribe("greenhouse/commands/control");
    } else {
      Serial.print("failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" try again in 2 seconds");
      delay(2000);
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

void roofOpenMelody() {
  playTone(262,150);
  playTone(330,150);
  playTone(392,200);
  stopTone();
}

void roofCloseMelody() {
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

// TIME
int currentHour() {
  struct tm timeinfo;
  if(!getLocalTime(&timeinfo)) return 12;
  return timeinfo.tm_hour;
}

// READ SENSORS
void readSensors() {
  TempAndHumidity data = dht.getTempAndHumidity();
  if (!isnan(data.temperature)) ambientTemp = data.temperature;
  if (!isnan(data.humidity))    ambientHum  = data.humidity;

  int soilRaw  = analogRead(SOIL_PIN);
  int lightRaw = analogRead(LDR_PIN);
  ambientSoil  = (int)map(soilRaw,  0, 4095, 100, 0);
  ambientLight = (int)map(lightRaw, 0, 4095, 0, 100);

  // First read: seed effective values from hardware so there's no jump
  if (!feedbackReady) {
    effTemp  = ambientTemp;
    effHum   = ambientHum;
    effSoil  = (float)ambientSoil;
    effLight = (float)ambientLight;
    feedbackReady = true;
  }

  // Expose current effective values as the working sensor globals
  temperature  = constrain(effTemp,  5.0f,  60.0f);
  humidity     = constrain(effHum,   0.0f, 100.0f);
  soilPercent  = (int)constrain(effSoil,  0.0f, 100.0f);
  lightPercent = (int)constrain(effLight, 0.0f, 100.0f);

  motionDetected = digitalRead(PIR_PIN);
}

// ACTUATOR FEEDBACK — mirrors ACTUATOR_EFFECTS + ENV_DRIFT in model.js
// Called every SEND_INTERVAL_MS (833 ms = 1 simulated 10-s tick).
// Effective values gradually approach ambient (greenhouse not perfectly insulated),
// then natural drift and actuator deltas are applied on top.
void applyActuatorFeedback() {
  if (!feedbackReady) return;

  // Pull effective values toward ambient (heat / humidity exchange with outside)
  // 0.02 = slow bleed-through so actuator effects stay clearly visible
  const float APPROACH = 0.02f;
  effTemp  += (ambientTemp - effTemp) * APPROACH;
  effHum   += (ambientHum  - effHum)  * APPROACH;
  effSoil  += (ambientSoil - effSoil) * APPROACH;
  effLight  = (float)ambientLight;   // light tracks hardware directly

  // ENV_DRIFT: natural greenhouse accumulation per tick
  effTemp += 0.15f;
  effHum  -= 0.25f;
  effSoil -= 1.0f;

  // Fan: cools room, slight evaporative humidity rise
  if (fanState) {
    if (effTemp > 22.0f) effTemp -= 0.4f;
    if (effHum  < 85.0f) effHum  += 0.3f;
  }
  // Pump: raises soil moisture, adds humidity
  if (pumpState) {
    if (effSoil < 95.0f) effSoil = min(95.0f, effSoil + 3.0f);
    if (effHum  < 88.0f) effHum  += 1.0f;
  }
  // Roof position
  if (roofPosition == 0) {          // fully open — strong ventilation
    if (effTemp > 24.0f) effTemp -= 0.8f;
    if (effHum  < 80.0f) effHum  += 0.2f;
  } else if (roofPosition == 45) {  // half open — partial shade + gentle flow
    if (effTemp > 26.0f) effTemp -= 0.3f;
    if (effLight > 10.0f) effLight -= 6.0f;
  } else {                          // closed — greenhouse effect
    if (effTemp < 55.0f) effTemp += 0.2f;
  }

  // Clamp to physical bounds
  effTemp  = constrain(effTemp,  5.0f,  60.0f);
  effHum   = constrain(effHum,   0.0f, 100.0f);
  effSoil  = constrain(effSoil,  0.0f, 100.0f);
  effLight = constrain(effLight, 0.0f, 100.0f);

  // Expose to sensor globals so sendData() uses the freshly computed values
  temperature  = effTemp;
  humidity     = effHum;
  soilPercent  = (int)effSoil;
  lightPercent = (int)effLight;
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

// ROOF CONTROL
void moveRoof(int target) {
  if(target == roofPosition) return;
  roofServo.write(target);
  if(target < roofPosition) roofOpenMelody();
  else                      roofCloseMelody();
  roofPosition = target;
}

void controlRoof() {
  if (servoCmd >= 0) { moveRoof(servoCmd); return; }
  int hour = currentHour();
  if (temperature > 34)                               { moveRoof(0);  return; }
  if (hour >= 11 && hour <= 14 && lightPercent > 75)  { moveRoof(45); return; }
  moveRoof(90);
}

// MOTION LIGHT
void controlMotionLight() {
  if (motionDetected && lightPercent < 25) {
    motionLightOnTime = millis();
    motionLightEverTriggered = true;
  }
  lightStatus = motionLightEverTriggered && (millis() - motionLightOnTime < MOTION_LIGHT_MS);
  digitalWrite(ALARM_LED_PIN, lightStatus ? HIGH : LOW);

  bool night = currentHour() >= 18 || currentHour() < 6;
  if (night && motionDetected) warningMelody();
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

// LCD
void updateLCD() {
  char line1[17];
  snprintf(line1, sizeof(line1), "T:%2.1f S:%02d%%", temperature, soilPercent);
  lcd.print("                ");
  lcd.setCursor(0,0);
  lcd.print(line1);
  lcd.setCursor(0,1);
  switch(plantState) {
    case GOOD:     lcd.print("GOOD            "); break;
    case SLOW:     lcd.print("SLOW            "); break;
    case DECLINE:  lcd.print("DECLINE         "); break;
    case CRITICAL: lcd.print("CRITICAL        "); break;
    case DEAD:     lcd.print("DEAD            "); break;
  }
}

// SERIAL MONITOR
void printStatus() {
  Serial.println("==========");
  Serial.printf("Temp: %.1f C\r\n",     temperature);
  Serial.printf("Humidity: %.1f %%\r\n", humidity);
  Serial.printf("Soil: %d %%\r\n",      soilPercent);
  Serial.printf("Light: %d %%\r\n",     lightPercent);
  Serial.printf("Fan: %s\r\n",          fanState    ? "ON":"OFF");
  Serial.printf("Pump: %s\r\n",         pumpState   ? "ON":"OFF");
  Serial.printf("Roof: %d\r\n",         roofPosition);
  Serial.printf("MotionLight: %s\r\n",  lightStatus ? "ON":"OFF");
}

// SETUP
void setup() {
  Serial.begin(115200);
  dht.setup(DHT_PIN, DHTesp::DHT22);
  roofServo.attach(SERVO_PIN, 500, 2400);
  lcd.init();
  lcd.backlight();

  pinMode(PUMP_LED_PIN,  OUTPUT);
  pinMode(FAN_LED_PIN,   OUTPUT);
  pinMode(ALARM_LED_PIN, OUTPUT);
  pinMode(PIR_PIN, INPUT);

  ledcSetup(0, 2000, 8);
  ledcAttachPin(BUZZER_PIN, 0);

  WiFi.begin("Wokwi-GUEST", "");
  while(WiFi.status() != WL_CONNECTED) delay(100);

  mqttClient.setServer(mqtt_server, 1883);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setBufferSize(512);

  configTime(7 * 3600, 0, "pool.ntp.org");
  moveRoof(90);
}

// LOOP
void loop() {
  if (millis() - lastSensorRead >= SENSOR_READ_MS) {
    readSensors();
    plantState = evaluatePlant();
    controlFan();
    controlPump();
    controlRoof();
    controlMotionLight();
    criticalMonitor();
    updateLCD();
    printStatus();
    lastSensorRead = millis();
  }

  if (!mqttClient.connected()) mqttReconnect();
  mqttClient.loop();

  if (millis() - lastSend >= SEND_INTERVAL_MS) {
    applyActuatorFeedback();
    sendData();
    lastSend = millis();
  }

  delay(20);
}
