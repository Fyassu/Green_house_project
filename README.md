# Smart Greenhouse — IoT & Digital Twin

Dự án nghiên cứu và xây dựng hệ thống giám sát, điều khiển nhà kính thông minh tích hợp công nghệ **Digital Twin (Bản sao số)** và bảo mật đa tầng.

Dự án phục vụ hai mục tiêu thực hành chuyên sâu:
- **Hệ thống IoT:** Mô phỏng các cảm biến/thiết bị tác động của nhà kính trên ESP32 (Wokwi), truyền dữ liệu mã hóa an toàn qua mạng MQTT toàn cầu (HiveMQ), lưu trữ dữ liệu SQLite và trực quan hóa thời gian thực.
- **Digital Twin:** Bản sao số 3D tương tác của nhà kính chạy trên trình duyệt web, tự động phản ánh trạng thái thực tế của thiết bị và cây trồng thông qua cơ chế đồng bộ hóa Server-Sent Events (SSE).

---

## 1. Kiến trúc Hệ thống & Luồng Dữ liệu (Data Flow)

Hệ thống được thiết kế tối ưu với 2 luồng dữ liệu độc lập, đảm bảo hiệu năng và độ trễ thấp nhất:

```text
  ┌────────────────────────────────────────────────────────┐
  │                   Wokwi (ESP32 ảo)                     │
  │   Core 1: Đọc cảm biến (200ms) & Điều khiển, Vẽ LCD    │
  │   Core 0: MQTTTask (Kết nối mạng & Giải mã/Mã hóa)     │
  └─────────────────┬────────────────────────┬─────────────┘
                    │ (1) Publish Data       │ (2) Subscribe Cmd
                    ▼                        ▲
  ┌─────────────────┴────────────────────────┴─────────────┐
  │                   Public MQTT Broker                   │
  │                  (broker.hivemq.com)                   │
  └─────────────────┬────────────────────────┴─────────────┘
                    │ (1) Subscribe Data     │ (2) Publish Cmd
                    ▼                        ▲
  ┌─────────────────┴────────────────────────┴─────────────┐
  │                 Flask Backend (Python)                 │
  │                                                        │
  │ ┌────────────────┐                                     │
  │ │  MQTT Worker   │─────┐                               │
  │ └────────────────┘     │    ┌──────────────────────┐   │      ┌─────────────────────┐
  │                        ├───►│MySQL Connection Pool │◄──┼─────►│ MySQL Database      │
  │ ┌────────────────┐     │    │    (pool_size=10)    │   │      │ (Lưu trữ lịch sử)   │
  │ │  REST & SSE    │─────┘    └──────────────────────┘   │      └─────────────────────┘
  │ └───────┬────────┘                                     │
  └─────────┼────────────────────────────────┬─────────────┘
            │ (3) SSE Stream                 │ (2) REST API
            ▼                               ▲▼
  ┌─────────┴────────────────────────────────┴─────────────┐
  │         Web App (Dashboard & Visualization)            │
  └────────────────────────────────────────────────────────┘
```

* **Luồng 1 (Từ phần cứng lên Backend):** ESP32 định kỳ đọc dữ liệu từ cảm biến, **mã hóa** chuỗi dữ liệu bằng thuật toán Speck và đẩy (Publish) qua MQTT. Backend Python (chạy ngầm luồng MQTT Worker) nhận, giải mã bằng Speck, sau đó cấp phát 1 connection từ **MySQL Connection Pool** để ghi dữ liệu lịch sử vào Database rồi lập tức trả lại connection.
* **Luồng 2 (Từ Web xuống phần cứng):** Trình duyệt gọi REST API `POST /api/control` (chạy trên luồng riêng của Flask). API này sẽ mượn 1 connection khác từ **Pool** để cập nhật Database. Việc sử dụng Pool giải quyết triệt để lỗi "đụng độ" (Thread-Collision) khi luồng ngầm MQTT và luồng Web Flask cùng cố gắng truy cập Database tại một thời điểm, đảm bảo tính toàn vẹn dữ liệu (Thread-Safety).
* **Luồng 3 (Đồng bộ thời gian thực SSE):** Ngay sau khi MQTT Worker lưu xong dữ liệu vào Database, nó lập tức kích hoạt luồng **Server-Sent Events (SSE)** tại endpoint `/api/stream` bắn thẳng dữ liệu xuống trình duyệt. Giao diện 3D Twin bắt được event này sẽ tự động thay đổi hiệu ứng ngay tắp lự.

---

## 2. Các Thiết Bị Phần Cứng Được Mô Phỏng (Wokwi)

Hệ thống sử dụng vi điều khiển trung tâm **ESP32 DevKit v4** kết nối với một loạt các thiết bị ngoại vi để mô phỏng nhà kính:

* **🌡️ Cảm biến môi trường:**
  * **DHT22 (Pin 15):** Đo nhiệt độ và độ ẩm không khí.
  * **Potentiometer (Pin 35):** Giả lập cảm biến đo độ ẩm đất.
  * **LDR (Pin 34):** Cảm biến quang trở đo cường độ ánh sáng môi trường.
  * **PIR Sensor (Pin 14):** Cảm biến hồng ngoại phát hiện chuyển động đột nhập.

* **⚙️ Thiết bị thực thi (Actuators) & Đèn báo trạng thái (LEDs):**
  * **Đèn LED Xanh Dương (Blue LED - Pin 4):** Biểu diễn trạng thái bật/tắt của hệ thống Quạt làm mát (Fan).
  * **Đèn LED Xanh Lá (Green LED - Pin 5):** Biểu diễn trạng thái bật/tắt của hệ thống Bơm tưới nước (Pump).
  * **Đèn LED Đỏ (Red LED - Pin 27):** Biểu diễn trạng thái báo động khẩn cấp (sáng khi chế độ bảo vệ đang BẬT và có người đột nhập).
  * **Còi Buzzer (Pin 23):** Phát ra âm thanh cảnh báo khi có đột nhập.
  * **Servo (Pin 18):** Động cơ điều khiển mái che nhà kính (0°: mở 100%, 45°: mở 50%, 90°: đóng hoàn toàn).

* **📺 Thiết bị hiển thị tại chỗ:**
  * **LCD 16x2 I2C (Mặc định: SDA 21 / SCL 22):** Hiển thị trực tiếp tại vườn các giá trị đo đạc để giám sát cục bộ, chỉ cập nhật I2C khi có sự thay đổi chỉ số để tối ưu hiệu năng giả lập.

---

## 3. Cơ Chế Bảo Mật Đa Tầng

Hệ thống được áp dụng các tiêu chuẩn bảo mật, phân bổ cả ở tầng Ứng dụng và tầng Mạng:

### Tầng Mạng (Network Layer - Mật mã học Payload):
Giao thức MQTT của hệ thống đi qua Public Broker công cộng nên rất dễ bị tin tặc theo dõi lưu lượng mạng (Sniffing) hoặc tiêm lệnh điều khiển giả (Man-in-the-Middle). Để khắc phục:
* **Mã hóa Payload 2 chiều:** Toàn bộ dữ liệu JSON chứa thông số cảm biến (Upstream) và lệnh điều khiển (Downstream) truyền trên đường mạng đều bị mã hóa thành những chuỗi ký tự vô nghĩa.
* **Thuật toán Speck-128-CTR (trên ESP32):** Mật mã học nhẹ (LWC - Lightweight Cryptography) được chọn cho ESP32. Thuật toán Speck thuộc kiến trúc ARX (Cộng, Dịch bit, XOR) không dùng bảng S-Box nặng nề như AES, giúp con chip ESP32 ảo giải mã mã hóa nhanh chớp nhoáng chỉ trong vài chu kỳ máy, triệt tiêu hoàn toàn độ trễ của Wokwi nhưng vẫn đảm bảo sức mạnh bảo mật cực kỳ cao.
* **Chế độ CTR (Counter Mode):** Biến thuật toán mã hóa khối (Block Cipher) thành mã hóa dòng (Stream Cipher), đầu vào bao nhiêu bytes thì đầu ra bấy nhiêu bytes, không cần nhồi thêm dữ liệu (No Padding). Đặc biệt, quá trình mã hóa và giải mã đều gọi chung duy nhất 1 hàm (XOR dữ liệu với Keystream), giúp tiết kiệm 50% dung lượng Flash của vi điều khiển.
* **Đồng bộ hóa Speck trên Backend:** Mã nguồn Python trên server cũng được thiết kế triển khai thuật toán Speck-128-CTR một cách đồng nhất và giải mã cực nhanh, giúp hệ thống tương thích ngược 100% với chip ESP32.

### Tầng Ứng dụng (Application Layer - Xác thực và Phân quyền):
* **JSON Web Token (JWT):** Tại tầng Web, Dashboard yêu cầu người dùng phải đăng nhập thành công qua `/api/auth`. Một chuỗi mã thông báo Token (JWT) được cấp bằng Secret Key của server và lưu trữ tại LocalStorage của trình duyệt.
* **Bảo vệ Endpoint API:** Mọi luồng API dùng để điều khiển thiết bị (`POST /api/control`) và truy xuất dữ liệu cảm biến (`GET /api/history`, `GET /api/latest`) trên ứng dụng Web đều đòi hỏi client phải gửi Token hợp lệ (Bearer Token) đính kèm trong Header. Người lạ truy cập vào trang Web nếu không có JWT sẽ bị chặn quyền ở cấp độ Ứng dụng và không thể tương tác hay xem số liệu của nhà kính.

---

## 4. Thiết kế Đa nhân (FreeRTOS Dual-Core)

## 4. Cơ chế Điều khiển Kép & Tự động Cứu hộ cục bộ (FreeRTOS Dual-Core)

Hệ thống được lập trình vô cùng linh hoạt với khả năng phân tách tác vụ nhờ hệ điều hành thời gian thực **FreeRTOS** trên 2 lõi CPU của vi điều khiển ESP32:

* **Core 0 (Task nền `MQTTTask` - Xử lý mạng lưới):**
  * Đảm nhận toàn bộ kết nối và tái kết nối mạng (WiFi, MQTT) dưới dạng không chặn (non-blocking).
  * Lắng nghe lệnh từ Web, tiến hành giải mã Speck-CTR để cập nhật cờ (flag) ghi đè hệ thống.
  * Định kỳ mã hóa dữ liệu cảm biến và gửi báo cáo về máy chủ.

* **Core 1 (Main Loop - Cơ chế Tự động Cứu hộ tại chỗ):**
  * Quét liên tục tất cả cảm biến phần cứng với tần số rất cao (200ms - 2000ms).
  * Hiển thị ngay lập tức các chỉ số môi trường ra màn hình LCD phục vụ người nông dân tại vườn.
  * **Tính năng Cứu hộ Cây trồng (Auto-Rescue):** Nếu người dùng không gửi lệnh ép buộc (Override) từ trang Web (tức là nút bấm đang để chữ AUTO), ESP32 sẽ tự động vận hành máy móc theo logic sau:

#### Logic điều khiển tự động
| Actuator | Bật khi | Tắt khi |
|---|---|---|
| Quạt | Nhiệt độ > 30°C | Nhiệt độ < 28°C |
| Bơm | Độ ẩm đất < 45% | Độ ẩm đất > 65% |
| Mái | Nhiệt độ > 34°C $\rightarrow$ mở hoàn toàn (0°) | Nhiệt độ bình thường $\rightarrow$ đóng (90°) |
| Mái | ánh sáng > 75% $\rightarrow$ mở một nửa (45°) | |
| Đèn led | Tắt chức năng bảo vệ + có chuyển động | |
| Alarm | Bật chức năng bảo vệ + có chuyển động | |

#### Đánh giá sức khỏe cây
| Trạng thái | Điều kiện |
|---|---|
| GOOD | Nhiệt độ 20–30°C, đất ≥ 50%, độ ẩm ≥ 45% |
| SLOW | Nhiệt độ 15–34°C, đất ≥ 35%, độ ẩm ≥ 35% |
| DECLINE | Nhiệt độ 10–38°C, đất ≥ 20%, độ ẩm ≥ 20% |
| CRITICAL | Nhiệt độ 5–45°C, đất ≥ 5% |
| DEAD | Nhiệt độ < 5°C hoặc > 45°C, hoặc đất < 5% |

**Sức mạnh của kiến trúc:** 
Các giao thức internet (như bắt tay TCP, giải DNS) rất dễ bị tắc nghẽn và treo luồng xử lý tới hàng chục giây nếu đường truyền không ổn định. Nhờ việc "nhốt" hoàn toàn module mạng sang Core 0, Core 1 luôn luôn ở trạng thái mượt mà. 
**Kết quả:** Ngay cả khi nhà kính bị đứt cáp internet, rớt mạng WiFi, hay Server máy chủ bị sập nguồn... **Hệ thống tự động cứu hộ tại chỗ** trên Core 1 vẫn chạy bình thường. Máy bơm vẫn tưới khi đất khô, quạt vẫn quay khi trời nóng, và còi vẫn hú khi có trộm mà hoàn toàn không phụ thuộc vào internet!

---

## 5. Cài đặt & Khởi động

### Các bước chuẩn bị:

1. **Tạo cơ sở dữ liệu MySQL:**
   Bật MySQL của bạn và tạo một database, ví dụ `smart_greenhouse` (backend sẽ tự tạo bảng `sensor_data` khi chạy). Cấu hình tài khoản đăng nhập ở file `backend/config.py`.

2. **Cài đặt các thư viện Python theo yêu cầu:**
   ```bash
   pip install -r requirements.txt
   ```

### Khởi chạy dự án:

Dự án hỗ trợ một script khởi chạy hợp nhất tất cả thành phần:
```bash
python start.py
```

* **Backend tiến trình (Terminal 1):** Khởi động Flask API Server (cổng 5000), kết nối MySQL Database Pool và MQTT Client chạy ngầm.
* **Frontend tiến trình (Terminal 2):** Tự động khởi động Web Server cục bộ phục vụ giao diện 3D Digital Twin (cổng 3000).

### Trải nghiệm hệ thống:
1. Mở trình duyệt truy cập: `http://localhost:3000`. Đăng nhập bằng tài khoản mặc định `admin` / mật khẩu `admin123`.
2. Mở phần mềm VS Code (chứa code dự án), nhấn tổ hợp phím **`F1`** và gõ **`Wokwi: Start Simulator`** để chạy mô phỏng mạch phần cứng.
3. Chỉnh các thông số cảm biến trên Wokwi, và quan sát sự thay đổi lập tức xuất hiện tại mô hình 3D trên Dashboard!

---

## 6. Chi tiết các API Backend (REST & SSE)

Dưới đây là toàn bộ các Endpoint API được triển khai tại Backend (Flask) chạy ở `http://localhost:5000`:

| Phương thức | Route | Xác thực (JWT) | Mô tả công dụng |
|---|---|---|---|
| `POST` | `/api/login` | Không | Nhận `username` và `password`. Nếu đúng (admin/admin123) sẽ trả về chuỗi Token (JWT) có thời hạn sử dụng. |
| `GET` | `/api/stream` | Không | API mở kết nối liên tục Server-Sent Events (SSE). Bất cứ khi nào Wokwi gửi dữ liệu lên hoặc khi có người điều khiển trạng thái thiết bị, API này lập tức đẩy dữ liệu JSON về phía Web Client theo thời gian thực (Live Stream). |
| `GET` | `/api/latest` | Có (Bearer) | Trả về thông số cảm biến mới nhất và trạng thái hiện tại của toàn bộ thiết bị (bao gồm cả các lệnh ghi đè). |
| `GET` | `/api/history` | Có (Bearer) | Lấy lịch sử 100 bản ghi dữ liệu cảm biến gần nhất từ MySQL để vẽ biểu đồ Line Chart. |
| `GET` | `/api/commands` | Không | Truy xuất trạng thái lệnh đang được ghi đè (Override) hiện tại. API này mở cho ESP32 lấy dữ liệu mà không cần Auth. |
| `POST` | `/api/control` | Có (Bearer) | Nhận JSON lệnh điều khiển (VD: `{"device": "fan", "value": true}`). Backend sẽ gộp chung với các lệnh khác, mã hóa bằng Speck-CTR và gửi xuống phần cứng qua MQTT. |
| `POST` | `/api/sensor-data` | Không | API phụ trợ để nhận dữ liệu cảm biến định dạng thô (không mã hóa) phục vụ các kịch bản test hoặc thiết bị giả lập cũ. |
| `GET` | `/api/test` | Không | API Health Check. Truy vấn `SELECT COUNT(*)` xuống DB để đếm tổng số bản ghi và trả về trạng thái hệ thống, được gọi lúc khởi động. |
