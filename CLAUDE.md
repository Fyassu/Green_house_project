# Greenhouse Project — CLAUDE.md

## Project Overview

Dự án nhà kính thông minh phục vụ hai môn học:

- **Môn IoT**: Mô phỏng phần cứng nhà kính (sensor + actuator) trên Wokwi, thu thập dữ liệu vào MySQL, trực quan hóa trên dashboard.
- **Môn Digital Twin**: Mô phỏng số (digital twin) của cây trồng, môi trường, actuator và nhà kính — chạy song song với firmware, phản ánh trạng thái thực tế theo thời gian thực.

---

## Architecture

```
Wokwi (ESP32)
    │  HTTP POST /api/sensor-data (every 10s)
    │  via ngrok tunnel
    ▼
Backend API  ──────────►  MySQL (smart_greenhouse)
    │                         └─ table: sensor_data
    │  GET /api/latest
    │  GET /api/history
    ▼
Dashboard (visualization)
    └─ Digital Twin (3D/2D simulation)
```

---

## Directory Structure

```
Greenhouse_project/
├── src/
│   └── sketch.ino          # ESP32 firmware (Arduino/PlatformIO)
├── backend/
│   ├── app.py              # Flask API server (Python, port 5000) — PRIMARY
│   ├── db.py               # MySQL connection (Python)
│   ├── server.js           # Express API server (Node.js, port 3000) — ALTERNATIVE
│   ├── db.js               # MySQL connection (Node.js)
│   └── requirements.txt    # Python dependencies
├── platformio.ini          # PlatformIO build config (ESP32)
├── wokwi.toml              # Wokwi simulator config
└── wokwi-project.txt       # Wokwi project link
```

---

## Firmware (ESP32 — src/sketch.ino)

### Hardware Pin Map

| Component         | Pin |
|-------------------|-----|
| DHT22 (temp/hum)  | 15  |
| Servo (roof)      | 18  |
| Pump LED          | 5   |
| Fan LED           | 4   |
| Alarm LED         | 27  |
| LDR (light)       | 34  |
| Soil moisture     | 35  |
| PIR (motion)      | 14  |
| Buzzer            | 23  |
| LCD I2C (0x27)    | SDA/SCL |

### Control Logic

| Actuator | Trigger ON              | Trigger OFF           |
|----------|-------------------------|-----------------------|
| Fan      | temperature > 30°C      | temperature < 28°C    |
| Pump     | soil_moisture < 45%     | soil_moisture > 65%   |
| Roof     | temp > 34°C → fully open (0°); hour 11–14 & light > 75% → half open (45°); else closed (90°) |
| Alarm    | night (18:00–06:00) + motion detected |      |

### Plant Health States

```
GOOD      → temp 20–30°C, soil ≥ 50%, humidity ≥ 45%
SLOW      → temp 15–34°C, soil ≥ 35%, humidity ≥ 35%
DECLINE   → temp 10–38°C, soil ≥ 20%, humidity ≥ 20%
CRITICAL  → temp 5–45°C,  soil ≥ 5%
DEAD      → temp < 5°C or > 45°C, or soil < 5%
```

### Data Transmission
- WiFi: `Wokwi-GUEST` (Wokwi built-in)
- Endpoint: `https://<ngrok-url>/api/sensor-data`
- Interval: every 10 seconds
- Payload: JSON with temperature, humidity, soil_moisture, light_level, motion_detected, fan_status, pump_status, servo_angle

---

## Backend API

### One-command startup
```bash
# từ thư mục gốc dự án (Flask, ngrok, build firmware tự động)
python start.py
```
Sau khi script xong: F1 → "Wokwi: Start Simulator" trong VS Code.

### Running thủ công (Flask — recommended)
```bash
cd backend
greenhouse_wokwi\Scripts\python.exe app.py   # dùng đúng venv
```

### Running (Express — alternative)
```bash
cd backend
npm install
node server.js       # starts on port 3000
```

### Endpoints

| Method | Route               | Description                    |
|--------|---------------------|--------------------------------|
| POST   | /api/sensor-data    | Insert sensor reading          |
| GET    | /api/latest         | Get most recent record         |
| GET    | /api/history        | Get last 100 records           |
| GET    | /api/test           | Health check — trả về status + row count |

### ngrok Setup
```bash
ngrok http 5000      # or 3000 for Express
# copy the https URL → update sketch.ino http.begin(...)
```

---

## Database (MySQL)

**Database:** `smart_greenhouse`

```sql
CREATE DATABASE smart_greenhouse;
USE smart_greenhouse;

CREATE TABLE sensor_data (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    temperature     FLOAT,
    humidity        FLOAT,
    soil_moisture   INT,
    light_level     INT,
    motion_detected BOOLEAN,
    fan_status      BOOLEAN,
    pump_status     BOOLEAN,
    servo_angle     INT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Connection:** `localhost`, user `root`, database `smart_greenhouse`
(password stored in `db.py` / `db.js` — do not commit real passwords)

---

## Development Workflow

### Wokwi Simulation
1. Build firmware: `pio run` (PlatformIO)
2. Open Wokwi project: https://wokwi.com/projects/466704710260621313
3. Start backend + ngrok
4. Update ngrok URL in `sketch.ino` if changed
5. Run simulation — data flows to MySQL every 10s

### PlatformIO Libraries (platformio.ini)
```
LiquidCrystal_I2C  — johnrickman
DHT sensor library — beegee-tokyo (ESPx)
ESP32Servo         — madhephaestus
```

---

## Planned Components

### Dashboard (Môn IoT)
- Visualize sensor history from MySQL
- Show current plant health state
- Charts: temperature, humidity, soil moisture, light level over time
- Actuator status (fan, pump, roof angle, alarm)

### Digital Twin (Môn Digital Twin)
- Real-time 3D/2D simulation of greenhouse
- Plant growth model driven by sensor state (GOOD/SLOW/DECLINE/CRITICAL/DEAD)
- Environment simulation: temperature, humidity, light zones
- Actuator animation: roof servo, pump spray, fan spin, alarm flash
- Synced with live MySQL data via polling or WebSocket

---

## Key Constraints

- Wokwi runs in the cloud — backend must be exposed via ngrok (not localhost)
- ngrok free tier generates a new URL on each restart → `start.py` tự cập nhật `sketch.ino`
- Flask backend là primary; Express là backup/alternative
- Venv bắt buộc: Flask cài trong `backend/greenhouse_wokwi/` — luôn dùng `greenhouse_wokwi\Scripts\python.exe`
- Timezone UTC+7 (Vietnam) via `configTime(7*3600, 0, "pool.ntp.org")`
- LCD 16×2 I2C tại address 0x27

---

## Known Issues & Fixes

### `mysql-connector-python` — cursor không có `.connection`
Cả `CMySQLCursor` (C extension) lẫn `MySQLCursor` (pure Python) đều không có thuộc tính `.connection`.

**Fix:** Dùng hàm `commit()` riêng trong `db.py` thay vì `cursor.connection.commit()`:
```python
# db.py
def commit():
    db.commit()

# app.py
from db import get_cursor, commit
cursor = get_cursor()
cursor.execute(sql, values)
cursor.close()
commit()
```
Thêm `use_pure=True` vào `_config` để dùng pure Python cursor.

### Wokwi — lần gửi đầu tiên chờ 10 giây
`unsigned long lastSend = -10000UL` để vòng loop đầu tiên gửi data ngay lập tức.

### Table tự tạo khi khởi động
`db.py` gọi `_ensure_table()` khi import — không cần chạy SQL tạo bảng thủ công.

---

## Debug Tools

```bash
cd backend

# Kiểm tra dữ liệu trong MySQL
greenhouse_wokwi\Scripts\python.exe test_db.py

# Chẩn đoán pipeline (Flask → MySQL → ngrok)
greenhouse_wokwi\Scripts\python.exe diagnose.py
```

`diagnose.py` kiểm tra từng bước A→E và dừng + hiện lỗi tại chỗ bị đứt.
