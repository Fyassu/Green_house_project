# BÁO CÁO PROJECT: GREENHOUSE DIGITAL TWIN

## Tóm tắt

Project xây dựng một Digital Twin cho nhà kính gồm ESP32/Wokwi, MQTT, Flask,
MySQL, dashboard 2D/3D, mô hình dự báo và vòng phản hồi điều khiển.

Hệ thống theo dõi bốn trạng thái:

- nhiệt độ trong nhà kính;
- độ ẩm không khí;
- độ ẩm đất;
- mức ánh sáng.

Từ năm bản ghi gần nhất, hệ thống dự báo sáu mốc từ `+5` đến `+30` phút.
Phương pháp được sử dụng là mô hình lai:

```text
Dự báo cuối
= dự báo của mô hình grey-box
+ phần sai số do Random Forest dự đoán
```

Do chưa có đủ dữ liệu nhà kính thật, project tạo một dataset thí nghiệm ảo gồm
25 ngày. Mỗi ngày có một kiểu thời tiết bên ngoài riêng. Kết quả đánh giá trên
năm ngày cuối chưa được dùng để train cho thấy mô hình lai tốt hơn mô hình vật
lý giản lược và Random Forest thuần ở cả bốn biến.

Kết quả này chứng minh pipeline hoạt động trong mô phỏng, chưa chứng minh độ
chính xác trong nhà kính thật.

---

## 1. Đặt vấn đề

Dashboard cảm biến thông thường chỉ cho biết trạng thái hiện tại. Người vận hành
chỉ phản ứng sau khi nhiệt độ hoặc độ ẩm đã vượt ngưỡng.

Digital Twin bổ sung hai khả năng:

1. dự đoán trạng thái tương lai;
2. dùng dự đoán để đề xuất hoặc phát lệnh ngược lại thiết bị.

Luồng hoàn chỉnh của project:

```text
Cảm biến
→ Backend và database
→ Mô hình dự báo
→ Quyết định điều khiển
→ Quạt, bơm hoặc tấm che
→ Trạng thái mới quay lại hệ thống
```

Đây là điểm giúp hệ thống tiến từ Digital Shadow lên Digital Twin.

### Mục tiêu

- Nhận telemetry từ ESP32/Wokwi qua MQTT.
- Lưu dữ liệu trong MySQL.
- Hiển thị nhà kính trên dashboard và mô hình 3D.
- Dự báo bốn trạng thái môi trường trong 5–30 phút.
- Thử kịch bản bật/tắt thiết bị trước khi phát lệnh.
- Hỗ trợ chế độ giám sát, đề xuất và tự động.

### Phạm vi

Đây là bài tập lớn môn Digital Twin. Mô hình được giữ đủ đơn giản để:

- chạy nhanh trên máy cá nhân;
- kiểm tra được quan hệ nhân quả;
- dễ thay hệ số khi có dữ liệu thật.

Project không nhằm mô phỏng đầy đủ nhiệt động học và sinh lý cây trồng.

---

## 2. Kiến trúc hệ thống

           [ Lớp IoT & Thiết bị ]
     +-------------------------------+
     |         ESP32 / Wokwi         |
     |   (Cảm biến & Thiết bị Act)   |
     +-------+---------------+-------+
             |               ^
             | Telemetry     | Lệnh điều khiển
             v               |
     +-------------------------------+
     |     MQTT Broker (HiveMQ)      |
     +-------+---------------+-------+
             |               ^
             | Sub           | Pub
             v               |
     +-------------------------------+         +-------------------+
     |                               |  Lưu    |                   |
     |         Flask Backend         |-------->|   MySQL Database  |
     |                               |         |                   |
     |  +-------------------------+  |         +-------------------+
     |  |   Digital Twin Engine   |  |
     |  |-------------------------|  |
     |  | - Grey-box (PBM)        |  |
     |  | - Random Forest (PGML)  |  |
     |  | - Dự báo +5m ~ +30m     |  |
     |  | - Closed-loop Safety    |  |
     |  +-------------------------+  |
     +---------------+---------------+
                     |
                     | REST API / SSE
                     v
     +-------------------------------+
     |                               |
     |  Web Dashboard (2D & 3D UI)   |
     |                               |
     +-------------------------------+



| Lớp Digital Twin | Thành phần |
|---|---|
| Vật lý | Cảm biến và thiết bị ESP32/Wokwi |
| Kết nối | MQTT và Flask |
| Dữ liệu | MySQL |
| Mô hình | Grey-box và Random Forest |
| Hiển thị | Dashboard, biểu đồ và mô hình 3D |
| Điều khiển | Lệnh MQTT quay lại thiết bị |

---

## 3. Mô hình vật lý giản lược

### 3.1. Tổng quan

Bốn phương trình dưới đây là mô hình grey-box do project xây dựng từ các
quan hệ vật lý cơ bản:

- năng lượng mặt trời làm nhiệt độ tăng;
- nhiệt độ và độ ẩm tiến gần điều kiện bên ngoài khi trao đổi không khí;
- bơm bổ sung nước;
- ánh sáng làm đất mất nước nhanh hơn;
- tấm che làm giảm bức xạ đi vào.

Các hệ số `k` không phải hằng số vật lý phổ quát. Chúng là hệ số danh định được
chọn cho mô phỏng và phải hiệu chỉnh khi có số đo thật.



### 3.2. Ánh sáng và tấm che

Mái trong suốt của nhà kính luôn đóng. Servo điều khiển tấm che nắng nằm phía
trên mái:

```text
shade_level = servo_angle / 90

0 độ  → không che
45 độ → che một nửa
90 độ → che kín
```

Ánh sáng trong nhà kính:

```text
Light_inside
= Solar_outside
× cover_transmissivity
× (1 - 0.70 × shade_level)
```

`0.70` nghĩa là tấm che kín được giả định chặn tối đa 70% bức xạ. Đây là giả
định của project, không phải thông số của một vật liệu che cụ thể.

### 3.3. Nhiệt độ

```text
T_next
= T_now
+ k_solar × Light_inside
+ (k_loss + k_fan × Fan) × (T_outside - T_now)
```

Diễn giải:

- ánh sáng làm nhà kính nóng lên;
- chênh lệch nhiệt độ tạo trao đổi với bên ngoài;
- bật quạt làm nhiệt độ trong nhà tiến gần nhiệt độ ngoài nhanh hơn.

Quạt không phải lúc nào cũng làm mát. Nếu ngoài trời nóng hơn bên trong, bật
quạt có thể làm nhiệt độ trong nhà tăng.

### 3.4. Độ ẩm không khí

```text
RH_next
= RH_now
+ k_plant × Light_inside × soil_factor
+ k_pump_air × Pump
+ (k_air + k_fan_air × Fan) × (RH_outside - RH_now)
```

Diễn giải:

- khi có ánh sáng và đất còn nước, cây làm không khí ẩm hơn;
- tưới có thể làm độ ẩm không khí tăng nhẹ;
- quạt làm độ ẩm trong nhà tiến gần độ ẩm ngoài trời.

Mô hình không cộng một lượng nước trực tiếp vào `%RH` theo nhiệt động học. Đây
là phương trình phản ứng bậc nhất phục vụ mô phỏng ngắn hạn.

### 3.5. Độ ẩm đất

```text
Soil_next
= Soil_now
+ k_irrigation × Pump
- k_dry_base
- k_dry_light × Light_inside
```

Diễn giải:

- bật bơm làm độ ẩm đất tăng;
- đất mất nước chậm ngay cả khi trời tối;
- ánh sáng mạnh làm tốc độ mất nước tăng.

### 3.6. Các hệ số hiện tại

| Nhóm | Hệ số chính | Giá trị danh định mỗi 5 phút |
|---|---|---:|
| Ánh sáng | Độ truyền sáng của mái | 0,72 |
| Tấm che | Mức chặn tối đa | 0,70 |
| Nhiệt độ | Tăng do ánh sáng cực đại | 0,18°C |
| Nhiệt độ | Trao đổi thụ động | 0,015 |
| Nhiệt độ | Trao đổi bổ sung khi bật quạt | 0,060 |
| Độ ẩm khí | Tăng do cây | 0,10 điểm % |
| Độ ẩm khí | Tăng do tưới | 0,08 điểm % |
| Độ ẩm đất | Tăng khi bật bơm | 2,00 điểm % |
| Độ ẩm đất | Mất nước nền | 0,04 điểm % |
| Độ ẩm đất | Mất thêm khi sáng mạnh | tối đa 0,12 điểm % |

Các giá trị này được dao động nhẹ giữa các ngày mô phỏng để Random Forest
không chỉ học thuộc đúng một bộ hệ số.

---

## 4. Mô phỏng môi trường bên ngoài

### 4.1. Các biến bên ngoài

Mỗi mốc 5 phút có:

- `Outdoor_Temperature`;
- `Outdoor_Humidity`;
- `Solar_Radiation_W_m2`;
- `Wind_Speed_m_s`;
- `Day_ID`;
- `Weather_State`.

### 4.2. Bốn kiểu ngày

| Kiểu ngày | Nhiệt độ | Độ ẩm | Bức xạ |
|---|---|---|---|
| `Hot_Sunny` | Cao | Thấp hơn vào buổi trưa | Mạnh |
| `Normal` | Trung bình | Trung bình | Khá ổn định |
| `Cloudy` | Thấp hơn | Cao hơn | Yếu và dao động |
| `Cool_Humid` | Thấp | Cao | Rất yếu |

Mỗi ngày lấy một bộ tham số riêng cho nhiệt độ trung bình, biên độ nhiệt, độ ẩm,
bức xạ và gió.

### 4.3. Chu kỳ ngày–đêm

```text
Sáng     → ánh sáng và nhiệt độ tăng
Trưa     → ánh sáng gần cực đại
Chiều    → ánh sáng và nhiệt độ giảm
Ban đêm  → bức xạ bằng 0, nhiệt độ thấp hơn, độ ẩm cao hơn
```

Nhiệt độ, độ ẩm và ánh sáng có tương quan; chúng không được lấy ngẫu nhiên độc
lập ở từng dòng. Nhiễu nhỏ được thêm vào để dữ liệu bớt lý tưởng.

---

## 5. Dataset thí nghiệm ảo

| Thuộc tính | Giá trị |
|---|---:|
| Số ngày | 25 |
| Khoảng lấy mẫu | 5 phút |
| Số mốc mỗi ngày | 288 |
| Tổng số dòng | 7.200 |
| Số kiểu thời tiết | 4 |
| Phiên bản vật lý | `greenhouse-greybox-v1.0` |

Một ngày được xem là một episode độc lập. Sliding window không được đi qua ranh
giới giữa hai ngày.

Ngoài điều khiển tự động theo ngưỡng, dataset có 895 dòng kích thích thiết bị.
Ở các mốc này, một thiết bị được thay đổi trong thời gian ngắn để dataset quan
sát được tác dụng của quạt, bơm và tấm che.

Nếu chỉ bật quạt khi nhiệt độ cao, AI có thể nhầm rằng “bật quạt gây ra nhiệt độ
cao”. Các kích thích ngắn giúp giảm nhầm lẫn nguyên nhân này.

Dataset được phân loại rõ là:

```text
synthetic_virtual_experiment
```

Không được gọi đây là dữ liệu cảm biến thật.

---

## 6. Mô hình AI dự báo

### 6.1. Tại sao chọn mô hình lai?

Mô hình vật lý giản lược dễ giải thích nhưng không mô tả hết nhiễu và sai số
tham số. Mô hình dữ liệu thuần có thể học tốt nhưng dễ dự báo thiếu hợp lý khi
dữ liệu ít.

Mô hình lai kết hợp hai ưu điểm:

```text
Physics forecast = xu hướng có nguyên nhân
Random Forest    = học phần sai số còn lại
```

### 6.2. Random Forest học gì?

Đầu tiên, mô hình grey-box dự báo tương lai:

```text
Y_physics
```

Từ dữ liệu mô phỏng, tính sai số:

```text
Residual = Y_actual - Y_physics
```

Random Forest được huấn luyện để dự đoán `Residual`. Khi chạy thật:

```text
Y_final = Y_physics + Residual_predicted
```

### 6.3. Input

Năm mốc gần nhất, mỗi mốc có 11 biến:

```text
4 trạng thái trong nhà
+ 3 trạng thái thiết bị
+ 4 biến môi trường ngoài
= 11 biến
```

Phần lịch sử:

```text
5 × 11 = 55 features
```

Mô hình vật lý tạo sáu mốc, mỗi mốc bốn biến:

```text
6 × 4 = 24 physics features
```

Tổng:

```text
55 + 24 = 79 input features
```

### 6.4. Output

Mỗi dự báo gồm bốn biến tại sáu mốc:

```text
6 × 4 = 24 residual outputs
```

Hợp đồng model:

```text
79 inputs → Random Forest → 24 residual outputs
```

### 6.5. Lý do chọn Random Forest

- Huấn luyện nhanh, không cần GPU.
- Phù hợp dataset vừa và nhỏ.
- Học được quan hệ phi tuyến.
- Hỗ trợ nhiều output.
- Ít công đoạn hơn LSTM.
- Phù hợp thời gian và phạm vi bài tập lớn.

Thông số chính:

| Tham số | Giá trị |
|---|---:|
| Số cây | 120 |
| Độ sâu tối đa | 18 |
| Số mẫu tối thiểu tại lá | 2 |
| `max_features` | 0,80 |
| `random_state` | 42 |

---

## 7. Chia dữ liệu và đánh giá

Không trộn ngẫu nhiên các dòng liền kề.

```text
Ngày 1–20  → train
Ngày 21–25 → test
```

Cách chia nguyên ngày giúp tập test chứa các chuỗi thời gian chưa xuất hiện
trong train.

### Các mô hình được so sánh

1. `Persistence`: giữ nguyên giá trị hiện tại.
2. `Physics only`: chỉ dùng grey-box.
3. `Data-only RF`: Random Forest dự đoán trực tiếp.
4. `Residual PGML`: grey-box cộng residual của Random Forest.

### Kết quả trên năm ngày test

RMSE tính chung cho sáu mốc:

| Mô hình | Nhiệt độ (°C) | Độ ẩm khí (%) | Độ ẩm đất (%) | Ánh sáng (%) |
|---|---:|---:|---:|---:|
| Persistence | 0,284 | 0,686 | 1,459 | 3,129 |
| Physics only | 0,216 | 0,592 | 1,255 | 2,855 |
| Data-only RF | 0,755 | 1,822 | 3,403 | 3,255 |
| **Residual PGML** | **0,209** | **0,586** | **0,981** | **2,233** |

Chỉ số riêng của mô hình lai:

| Biến | MAE | R² |
|---|---:|---:|
| Nhiệt độ | 0,165°C | 0,9935 |
| Độ ẩm khí | 0,467% | 0,9895 |
| Độ ẩm đất | 0,572% | 0,9695 |
| Ánh sáng | 1,046% | 0,9646 |

Không nên diễn giải các con số này thành độ chính xác ngoài thực tế. Dataset test
vẫn được tạo từ cùng bộ quy luật mô phỏng.

---

## 8. Dự báo kịch bản thiết bị

Hệ thống trả sáu mốc:

```text
+5, +10, +15, +20, +25, +30 phút
```

Metadata kiểm tra:

- contract `residual-pgml-v4-external-weather`;
- physics version `greenhouse-greybox-v1.0`;
- 79 input và 24 output;
- SHA-256 của dataset;
- phiên bản scikit-learn.

Nếu artifact không tải được, backend chuyển sang `physics_only`.

Khi thử một trạng thái thiết bị khác, ví dụ bật quạt và che kín:

1. Random Forest ước lượng sai số quanh trạng thái đã quan sát;
2. grey-box chạy lại với trạng thái thiết bị mới;
3. tác động nhân quả của thiết bị lấy từ grey-box;
4. residual bị giảm nếu kịch bản mới cách quá xa trạng thái ban đầu.

---

## 9. Vòng phản hồi điều khiển

Giao diện thiết bị vẫn giữ nguyên:

- quạt, bơm, đèn: `AUTO / ON / OFF`;
- tấm che: `AUTO / Không che 0° / Che nửa 45° / Che kín 90°`;
- bảo vệ: `ON / OFF`.

Backend có ba chế độ toàn cục:

| Chế độ | Hành vi |
|---|---|
| `OFF` | Chỉ giám sát và dự báo |
| `ADVISORY` | Đề xuất, người dùng quyết định |
| `AUTO` | Có thể phát lệnh sau kiểm tra an toàn |

Ngưỡng dự báo tại `+15 phút`:

```text
Nhiệt độ ≥ 34°C
→ đề xuất bật quạt và che nắng

Độ ẩm đất ≤ 50%
→ đề xuất bật bơm
```

Chế độ `AUTO` yêu cầu hai dự báo liên tiếp, kiểm tra dữ liệu cũ, áp dụng
cooldown, giới hạn thời gian bơm và giữ quyền ưu tiên cho lệnh thủ công.

---

## 10. Plant HP

Plant HP là chỉ số trực quan cho dashboard, không phải đại lượng sinh học chuẩn.

HP được cập nhật theo thời gian cây ở ngoài vùng môi trường phù hợp:

```text
HP_new = clamp(HP_old + recovery - damage, 0, 100)
```

Ba mức hiển thị:

| HP | Trạng thái |
|---:|---|
| 80–100 | Khỏe |
| 40–79 | Căng thẳng |
| 1–39 | Nguy hiểm |
| 0 | Cây chết |

Phần hiển thị HP phía trên mô hình cây 3D được để lại cho giai đoạn giao diện.

---

## 11. Cơ sở dữ liệu

| Bảng | Nội dung |
|---|---|
| `sensor_data` | Telemetry |
| `users` | Tài khoản |
| `control_events` | Quyết định và lệnh điều khiển |
| `plant_health_state` | Trạng thái HP |

Project dùng B-tree index cho `sensor_data.created_at`. Inverted index không cần
thiết vì truy vấn chính lọc theo thời gian, không tìm kiếm toàn văn trong log.

---

## 12. Kiểm tra tính đúng đắn

### Kiểm tra quy luật

- Quạt làm mát khi ngoài trời mát hơn.
- Quạt có thể làm nóng khi ngoài trời nóng hơn.
- Bơm làm độ ẩm đất tăng.
- Tấm che giảm ánh sáng và nhiệt nhận vào.
- Tấm che không thay đổi lưu lượng thông gió.
- Bức xạ mặt trời bằng 0 vào ban đêm.

### Kiểm tra dataset

- đúng 7.200 dòng;
- 25 ngày, mỗi ngày 288 mốc;
- đủ bốn kiểu thời tiết;
- đủ trạng thái quạt, bơm và ba góc servo;
- không có giá trị thiếu ở cột lõi;
- giá trị phần trăm nằm trong 0–100.

### Kiểm tra model

- hash dataset khớp artifact;
- đúng 79 input và 24 output;
- tách 20 ngày train và 5 ngày test;
- đủ sáu mốc dự báo;
- mô hình lai tốt hơn hai baseline học máy/vật lý.

Kết quả hiện tại:

```text
Validation mô hình, dataset, artifact: 34 PASS, 0 FAIL
Plant HP:                             6 PASS, 0 FAIL
Closed-loop:                          6 PASS, 0 FAIL
JavaScript syntax:                    PASS
```

---

## 13. Đóng góp và giới hạn

### Đóng góp

1. Hoàn thiện luồng hai chiều của Digital Twin.
2. Xây dựng grey-box nhỏ, dễ giải thích và có kiểm tra nhân quả.
3. Tạo 25 ngày thời tiết bên ngoài có chu kỳ ngày–đêm.
4. Kết hợp dự báo vật lý với Random Forest học residual.
5. Tách test theo ngày để tránh rò rỉ chuỗi thời gian.
6. Có cơ chế fallback và chốt an toàn điều khiển.

### Giới hạn

- Dataset hiện tại là dữ liệu tổng hợp.
- Các hệ số grey-box chưa được hiệu chỉnh bằng nhà kính thật.
- Dự báo ngắn hạn giả định điều kiện ngoài trời gần như giữ nguyên.
- Chưa có cảm biến môi trường ngoài trời trong hệ thống triển khai.
- Plant HP chỉ là thang điểm minh họa.
- Chưa biểu diễn khoảng bất định của dự báo.

Lời trình bày ngắn:

> Do chưa có đủ dữ liệu thật, nhóm không dùng AI thuần túy. Nhóm xây dựng một mô
> hình grey-box gồm bốn quan hệ dễ giải thích: ánh sáng và không khí bên ngoài
> ảnh hưởng nhiệt độ, cây và trao đổi khí ảnh hưởng độ ẩm, bơm và ánh sáng ảnh
> hưởng nước trong đất, còn tấm che làm giảm ánh sáng. Các hệ số hiện là giả
> định mô phỏng, không phải hằng số khoa học.
>
> Nhóm tạo 25 ngày thời tiết ngoài trời, dùng 20 ngày để train và giữ nguyên năm
> ngày cuối để test. Grey-box tạo dự báo nền, Random Forest chỉ học phần sai số.
> Mô hình lai tốt hơn physics-only và Random Forest thuần ở cả bốn biến. Tuy
> nhiên đây vẫn là kết quả trên dữ liệu tổng hợp; bước tiếp theo là hiệu chỉnh
> và kiểm định bằng cảm biến thật.

### Hướng phát triển

Ưu tiên cao nhất là thu dữ liệu thật đồng thời trong và ngoài nhà kính, sau đó:

1. hiệu chỉnh các hệ số `k`;
2. train lại residual model;
3. đánh giá trên các ngày chưa dùng để hiệu chỉnh;
4. chỉ khi đó mới kết luận về độ chính xác thực tế.

---

## 14. Hướng dẫn chạy

### Chuẩn bị MySQL

```sql
CREATE DATABASE smart_greenhouse;
```

```powershell
Copy-Item backend/config.example.py backend/config.py
```

Sau đó sửa thông tin MySQL trong `backend/config.py`.

### Cài thư viện

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd ..
```

### Khởi động

```powershell
python start.py
```

Mở `http://localhost:3000`, sau đó chạy Wokwi bằng:

```text
F1 → Wokwi: Start Simulator
```

### Tạo lại dataset và train

```powershell
python backend/ml_prediction/data_generator.py
python backend/ml_prediction/train_model.py
python backend/ml_prediction/validate_model.py
```

Không tạo dataset mới rồi tiếp tục dùng artifact cũ. Validation dùng SHA-256 để
phát hiện dataset và model không cùng phiên bản.

---

## 15. API chính

| Method | Route | Mục đích |
|---|---|---|
| `POST` | `/api/login` | Đăng nhập |
| `GET` | `/api/latest` | Telemetry mới nhất |
| `GET` | `/api/history` | Dữ liệu lịch sử |
| `GET` | `/api/predict?engine=hybrid` | Dự báo mô hình lai |
| `GET/POST` | `/api/control-mode` | Chế độ Digital Twin |
| `POST` | `/api/control` | Điều khiển thiết bị |
| `GET` | `/api/control-events` | Nhật ký điều khiển |
| `GET` | `/api/model/status` | Phiên bản và trạng thái model |
| `GET` | `/api/plant-hp` | Plant HP |
| `GET` | `/api/stream` | SSE thời gian thực |

---

## 16. Cấu trúc source code

| File | Vai trò |
|---|---|
| `src/sketch.ino` | Firmware ESP32/Wokwi |
| `backend/app.py` | Flask, MQTT và API |
| `backend/pbm_engine.py` | Grey-box bốn trạng thái |
| `backend/ml_prediction/data_generator.py` | Mô phỏng 25 ngày |
| `backend/ml_prediction/train_model.py` | Train và đánh giá |
| `backend/hybrid_model.py` | Suy luận physics + residual |
| `backend/closed_loop.py` | Chốt an toàn |
| `backend/plant_hp.py` | Plant HP |
| `backend/db.py` | MySQL |
| `dashboard/` | Dashboard và mô hình 3D |
| `PHYSICS_GUIDED_MODEL.md` | Giải thích riêng phần mô hình |
| `PRESENTATION_NOTES.md` | Dàn ý slide và câu hỏi phản biện |

---

## 17. Tài liệu tham khảo

- Karpatne và cộng sự, [Theory-Guided Data Science](https://arxiv.org/abs/1612.08544).
- Scikit-learn, [RandomForestRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html).
- Scikit-learn, [Multi-output Random Forest](https://scikit-learn.org/stable/auto_examples/ensemble/plot_random_forest_regression_multioutput.html).

Các công thức grey-box trong project không được chép nguyên từ các tài liệu trên.
Chúng là mô hình bậc nhất do project xây dựng và được công bố rõ là cần hiệu
chỉnh bằng dữ liệu thật.
