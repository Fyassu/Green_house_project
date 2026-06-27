#include <DHTesp.h>
#include <ESP32Servo.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <WiFi.h>
#include <time.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>

const char* ssid      = "Wokwi-GUEST";
const char* NGROK_BASE = "https://reclusive-penpal-duress.ngrok-free.dev";


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
enum PlantState {
  GOOD,
  SLOW,
  DECLINE,
  CRITICAL,
  DEAD
};

PlantState plantState = GOOD;
PlantState previousState = GOOD;

// SENSOR DATA
float temperature = 0;
float humidity = 0;

int soilPercent = 0;
int lightPercent = 0;

bool motionDetected = false;

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
  if (WiFi.status() == WL_CONNECTED)
  {
    WiFiClientSecure client;
    client.setInsecure();

    HTTPClient http;

    http.begin(
      client,
      String(NGROK_BASE) + "/api/sensor-data"
    );

    http.addHeader("Content-Type", "application/json");
    http.addHeader("ngrok-skip-browser-warning", "true");

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

    int responseCode = http.POST(json);

    Serial.print("HTTP Response: ");
    Serial.println(responseCode);

    String responseBody = http.getString();

    Serial.println("Response Body:");
    Serial.println(responseBody);

    http.end();
  }
}

unsigned long lastSend    = -10000UL;
unsigned long lastCmdPoll = 0;

// COMMAND POLL
// Trả về: -1=null(auto), 0=false, 1=true
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

// Trả về: -1=null(auto), >=0 = giá trị số
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

void pollCommands() {
  if (WiFi.status() != WL_CONNECTED) return;

  WiFiClientSecure client;
  client.setInsecure();

  HTTPClient http;
  http.begin(client, String(NGROK_BASE) + "/api/commands");
  http.addHeader("ngrok-skip-browser-warning", "true");

  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    fanCmd   = parseJsonBool(body, "fan");
    pumpCmd  = parseJsonBool(body, "pump");
    servoCmd = parseJsonInt(body,  "servo");
  }
  http.end();
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

  if(!getLocalTime(&timeinfo))
    return 12;

  return timeinfo.tm_hour;
}

// READ SENSORS
void readSensors() {

  TempAndHumidity data =
      dht.getTempAndHumidity();

  if(!isnan(data.temperature))
    temperature = data.temperature;

  if(!isnan(data.humidity))
    humidity = data.humidity;

  int soilRaw =
      analogRead(SOIL_PIN);

  int lightRaw =
      analogRead(LDR_PIN);

  soilPercent =
      map(soilRaw,0,4095,100,0);

  lightPercent =
      map(lightRaw,0,4095,0,100);

  motionDetected =
      digitalRead(PIR_PIN);
}

// PLANT HEALTH
PlantState evaluatePlant() {

  if(
      temperature > 45 ||
      temperature < 5  ||
      soilPercent < 5
    )
      return DEAD;

  if(
      temperature > 38 ||
      temperature < 10 ||
      soilPercent < 20 ||
      humidity < 20
    )
      return CRITICAL;

  if(
      temperature > 34 ||
      temperature < 15 ||
      soilPercent < 35 ||
      humidity < 35
    )
      return DECLINE;

  if(
      temperature > 30 ||
      temperature < 20 ||
      soilPercent < 50 ||
      humidity < 45
    )
      return SLOW;

  return GOOD;
}

// FAN CONTROL
void controlFan() {

  if (fanCmd >= 0) {
    fanState = (fanCmd == 1);        // override từ dashboard
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
    pumpState = (pumpCmd == 1);      // override từ dashboard
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

  if(target == roofPosition)
    return;

  roofServo.write(target);

  if(target < roofPosition)
    roofOpenMelody();
  else
    roofCloseMelody();

  roofPosition = target;
}

void controlRoof() {

  if (servoCmd >= 0) {
    moveRoof(servoCmd);              // override từ dashboard
    return;
  }

  int hour = currentHour();

  if (temperature > 34)                          { moveRoof(0);  return; }
  if (hour >= 11 && hour <= 14 && lightPercent > 75) { moveRoof(45); return; }
  moveRoof(90);
}

// MOTION LIGHT
void controlMotionLight() {

  // Kích hoạt đèn khi phát hiện chuyển động + ánh sáng yếu (< 25%)
  if (motionDetected && lightPercent < 25) {
    motionLightOnTime = millis();
    motionLightEverTriggered = true;
  }

  // Giữ đèn sáng trong 15 giây kể từ lần kích hoạt cuối
  lightStatus =
      motionLightEverTriggered &&
      (millis() - motionLightOnTime < 15000UL);

  digitalWrite(ALARM_LED_PIN, lightStatus ? HIGH : LOW);

  // Còi báo động riêng: ban đêm + chuyển động
  bool night =
      currentHour() >= 18 ||
      currentHour() < 6;

  if (night && motionDetected) {
    warningMelody();
  }
}

// CRITICAL ALERT
void criticalMonitor() {

  if(
      plantState != previousState
    ) {

    if(
        plantState ==
        CRITICAL
      ) {

      warningMelody();
    }

    if(
        plantState ==
        DEAD
      ) {

      for(int i=0;i<5;i++) {

        playTone(1500,200);

        stopTone();

        delay(100);
      }
    }
  }

  previousState =
      plantState;
}

// LCD
void updateLCD() {

  lcd.setCursor(0,0);

  char line1[17];

  snprintf(
      line1,
      sizeof(line1),
      "T:%2.1f S:%02d%%",
      temperature,
      soilPercent
  );

  lcd.print("                ");
  lcd.setCursor(0,0);
  lcd.print(line1);

  lcd.setCursor(0,1);

  switch(plantState) {

    case GOOD:
      lcd.print("GOOD            ");
      break;

    case SLOW:
      lcd.print("SLOW            ");
      break;

    case DECLINE:
      lcd.print("DECLINE         ");
      break;

    case CRITICAL:
      lcd.print("CRITICAL        ");
      break;

    case DEAD:
      lcd.print("DEAD            ");
      break;
  }
}


// SERIAL MONITOR
void printStatus() {

  Serial.println("==========");

  Serial.printf(
      "Temp: %.1f C\n",
      temperature
  );

  Serial.printf(
      "Humidity: %.1f %%\n",
      humidity
  );

  Serial.printf(
      "Soil: %d %%\n",
      soilPercent
  );

  Serial.printf(
      "Light: %d %%\n",
      lightPercent
  );

  Serial.printf(
      "Fan: %s\n",
      fanState ? "ON":"OFF"
  );

  Serial.printf(
      "Pump: %s\n",
      pumpState ? "ON":"OFF"
  );

  Serial.printf(
      "Roof: %d\n",
      roofPosition
  );

  Serial.printf(
      "Light: %s\n",
      lightStatus ? "ON" : "OFF"
  );
}

// SETUP
void setup() {

  Serial.begin(115200);

  dht.setup(
      DHT_PIN,
      DHTesp::DHT22
  );

  roofServo.attach(
      SERVO_PIN,
      500,
      2400
  );

  lcd.init();
  lcd.backlight();

  pinMode(PUMP_LED_PIN,OUTPUT);
  pinMode(FAN_LED_PIN,OUTPUT);
  pinMode(ALARM_LED_PIN,OUTPUT);

  pinMode(PIR_PIN,INPUT);

  ledcSetup(0,2000,8);
  ledcAttachPin(BUZZER_PIN,0);

  WiFi.begin(
      "Wokwi-GUEST",
      ""
  );

  while(
      WiFi.status() !=
      WL_CONNECTED
    ) {

    delay(100);
  }

  configTime(
      7 * 3600,
      0,
      "pool.ntp.org"
  );

  moveRoof(90);
}

// LOOP
void loop() {

  readSensors();

  plantState =
      evaluatePlant();

  controlFan();

  controlPump();

  controlRoof();

  controlMotionLight();

  criticalMonitor();

  updateLCD();

  printStatus();

  if (millis() - lastSend >= 10000)
  {
      sendData();
      lastSend = millis();
  }

  if (millis() - lastCmdPoll >= 5000)
  {
      pollCommands();
      lastCmdPoll = millis();
  }

  delay(1000);
}

