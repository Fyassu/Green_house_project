# Smart Greenhouse — IoT & Digital Twin

Dự án nhà kính thông minh phục vụ hai môn học:

- **Môn IoT** — Mô phỏng phần cứng (sensor + actuator) trên Wokwi, thu thập dữ liệu vào MySQL, trực quan hóa trên dashboard.
- **Môn Digital Twin** — Mô phỏng số cây trồng, môi trường, actuator và nhà kính, đồng bộ với dữ liệu thực tế theo thời gian thực.

---

## Kiến trúc

```
Wokwi (ESP32)
    │  HTTP POST /api/sensor-data  (mỗi 10 giây)
    │  qua ngrok tunnel
    ▼
Flask Backend (Python)  ──►  MySQL  (smart_greenhouse)
    │                              └─ bảng: sensor_data
    ├─ GET /api/latest
    ├─ GET /api/history
    └─ GET /api/test
    ▼
Dashboard  +  Digital Twin
```

---

## Phần cứng mô phỏng (ESP32 trên Wokwi)

| Linh kiện | Chức năng | Pin |
|-----------|-----------|-----|
| DHT22 | Nhiệt độ & độ ẩm | 15 |
| LDR | Cường độ ánh sáng | 34 |
| Soil sensor | Độ ẩm đất | 35 |
| PIR | Phát hiện chuyển động | 14 |
| Servo | Điều khiển mái nhà kính | 18 |
| Fan LED | Quạt làm mát | 4 |
| Pump LED | Bơm tưới nước | 5 |
| Alarm LED | Còi/đèn cảnh báo | 27 |
| Buzzer | Âm thanh thông báo | 23 |
| LCD 16×2 I2C | Hiển thị trạng thái | SDA/SCL |

### Logic điều khiển tự động

| Actuator | Bật khi | Tắt khi |
|----------|---------|---------|
| Quạt | Nhiệt độ > 30°C | Nhiệt độ < 28°C |
| Bơm | Độ ẩm đất < 45% | Độ ẩm đất > 65% |
| Mái | Nhiệt độ > 34°C → mở hoàn toàn (0°) | Nhiệt độ bình thường → đóng (90°) |
| Mái | Giờ 11–14 & ánh sáng > 75% → mở một nửa (45°) | |
| Alarm | Ban đêm (18:00–06:00) + có chuyển động | |

### Đánh giá sức khỏe cây

| Trạng thái | Điều kiện |
|-----------|-----------|
| GOOD | Nhiệt độ 20–30°C, đất ≥ 50%, độ ẩm ≥ 45% |
| SLOW | Nhiệt độ 15–34°C, đất ≥ 35%, độ ẩm ≥ 35% |
| DECLINE | Nhiệt độ 10–38°C, đất ≥ 20%, độ ẩm ≥ 20% |
| CRITICAL | Nhiệt độ 5–45°C, đất ≥ 5% |
| DEAD | Nhiệt độ < 5°C hoặc > 45°C, hoặc đất < 5% |

---

## Cài đặt

### Yêu cầu

- Python 3.x + virtualenv
- Node.js (backend thay thế)
- MySQL (database: `smart_greenhouse`)
- [PlatformIO](https://platformio.org/) (build firmware ESP32)
- [ngrok](https://ngrok.com/) (tunnel Wokwi → localhost)
- VS Code + [Wokwi extension](https://marketplace.visualstudio.com/items?itemName=wokwi.wokwi-vscode)

### Tạo database MySQL

```sql
CREATE DATABASE smart_greenhouse;
```

> Bảng `sensor_data` được tạo tự động khi Flask khởi động.

### Cài đặt Python backend

```bash
cd backend
python -m venv greenhouse_wokwi
greenhouse_wokwi\Scripts\activate
pip install -r requirements.txt
```

### Cấu hình database

```bash
# Sao chép file mẫu
copy backend\config.example.py backend\config.py

# Chỉnh sửa backend\config.py với thông tin MySQL của bạn
```

```python
# backend/config.py
DB_HOST     = "localhost"
DB_USER     = "root"
DB_PASSWORD = "your_password"
DB_NAME     = "smart_greenhouse"
```

---

## Chạy dự án

### Khởi động 1 lệnh (khuyến nghị)

```bash
python start.py
```

Script tự động:
1. Khởi động Flask backend (port 5000)
2. Khởi động ngrok tunnel
3. Cập nhật URL ngrok vào firmware (`src/sketch.ino`)
4. Build firmware với PlatformIO

Sau khi script chạy xong: **F1 → "Wokwi: Start Simulator"** trong VS Code.

### Khởi động thủ công

```bash
# Terminal 1 — Flask
cd backend
greenhouse_wokwi\Scripts\python.exe app.py

# Terminal 2 — ngrok
ngrok http 5000

# Cập nhật URL ngrok trong src/sketch.ino, sau đó build
pio run
```

---

## API Endpoints

| Method | Route | Mô tả |
|--------|-------|-------|
| `POST` | `/api/sensor-data` | Nhận dữ liệu từ ESP32 và lưu vào MySQL |
| `GET` | `/api/latest` | Lấy bản ghi mới nhất |
| `GET` | `/api/history` | Lấy 100 bản ghi gần nhất |
| `GET` | `/api/test` | Health check — trả về status và số rows |

---

## Debug & Kiểm tra

```bash
cd backend

# Xem dữ liệu trong MySQL (cấu trúc, thống kê, bản ghi mới nhất)
greenhouse_wokwi\Scripts\python.exe test_db.py

# Chẩn đoán pipeline từng bước (Flask → MySQL → ngrok)
greenhouse_wokwi\Scripts\python.exe diagnose.py
```

`diagnose.py` kiểm tra tuần tự:
- **A** Flask có chạy không
- **B/C** Flask có ghi được vào MySQL không
- **D** ngrok tunnel còn sống không
- **E** Toàn bộ đường đi Wokwi → ngrok → Flask → MySQL

---

## Cấu trúc thư mục

```
Greenhouse_project/
├── src/
│   └── sketch.ino          # Firmware ESP32 (Arduino/PlatformIO)
├── backend/
│   ├── app.py              # Flask API server (port 5000)
│   ├── db.py               # MySQL connection + auto-create table
│   ├── config.py           # Credentials (gitignored — tạo từ config.example.py)
│   ├── config.example.py   # Template cấu hình
│   ├── server.js           # Express API server (port 3000, thay thế Flask)
│   ├── test_db.py          # Kiểm tra dữ liệu trong MySQL
│   ├── diagnose.py         # Chẩn đoán pipeline
│   └── requirements.txt    # Python dependencies
├── start.py                # Script khởi động 1 lệnh
├── platformio.ini          # PlatformIO build config
├── wokwi.toml              # Wokwi simulator config
└── diagram.json            # Sơ đồ mạch Wokwi
```

---

## Wokwi Simulation

Project Wokwi: https://wokwi.com/projects/466704710260621313

Firmware được build bởi PlatformIO và load vào Wokwi qua VS Code extension.
Dữ liệu sensor được gửi lên backend mỗi 10 giây qua HTTPS (ngrok tunnel).
