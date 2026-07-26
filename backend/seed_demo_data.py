# Seed dữ liệu giả lập — kịch bản demo ban đầu cho Digital Twin nhà kính.
#
# Sinh một chu trình "một ngày" (sáng mát -> trưa nóng -> chiều dịu) bằng cách
# tái sử dụng đúng engine vật lý đã có (prediction._advance_one_tick) và logic
# tự động hóa hysteresis giống ACTUATOR_RULES trong model.py/sketch.ino — để dữ
# liệu nhất quán với toàn bộ hệ thống, không phải random thuần túy.
#
# Chạy: python seed_demo_data.py  (cần backend/config.py đã có, MySQL đang chạy)
# CẢNH BÁO: script này XÓA SẠCH dữ liệu cũ trong sensor_data trước khi seed
# (TRUNCATE) — chỉ dùng cho demo/dev, không chạy khi đã có dữ liệu thật cần giữ.

import math
import random
from datetime import datetime, timedelta

import mysql.connector

from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME
from model import ACTUATOR_RULES
import prediction

random.seed(20260726)

N            = 400     # số bản ghi
STEP_REAL_S  = 0.833    # khớp LATEST_INTERVAL thực tế (10s mô phỏng / SIM_SPEED=12)

now = datetime.now()
t0  = now - timedelta(seconds=(N - 1) * STEP_REAL_S)

state   = {"temp": 21.0, "hum": 65.0, "soil": 60.0, "light": 15.0}   # sáng sớm: mát, ẩm, đất còn ướt, ánh sáng yếu
fan_on, pump_on, servo = False, False, 90

rows = []
for i in range(N):
    frac = i / (N - 1)
    daylight = 15 + 65 * math.sin(math.pi * frac)   # đường cong ngày: 15 -> 80 -> 15

    # Hysteresis auto-control — dùng đúng ngưỡng trong ACTUATOR_RULES (model.py)
    if   state["temp"] > ACTUATOR_RULES["fan"]["on_above"]:   fan_on = True
    elif state["temp"] < ACTUATOR_RULES["fan"]["off_below"]:  fan_on = False

    if   state["soil"] < ACTUATOR_RULES["pump"]["on_below"]:  pump_on = True
    elif state["soil"] > ACTUATOR_RULES["pump"]["off_above"]: pump_on = False

    if state["temp"] > ACTUATOR_RULES["roof"]["full_open_above"]:
        servo = 0
    elif daylight > ACTUATOR_RULES["roof"]["half_open_light_min"]:
        servo = 45
    else:
        servo = 90

    roof_key = prediction.roof_effect_key(servo)
    state = prediction._advance_one_tick(dict(state), fan_on, pump_on, roof_key)
    # Ánh sáng mô phỏng theo đường cong ngày/đêm riêng (ENV_DRIFT.light=0 nên
    # _advance_one_tick không tự tạo chu trình ngày/đêm — gán trực tiếp ở đây).
    state["light"] = max(0.0, min(100.0, daylight + random.gauss(0, 1.5)))

    noisy_temp = state["temp"] + random.gauss(0, 0.05)
    noisy_hum  = state["hum"]  + random.gauss(0, 0.3)
    noisy_soil = max(0, min(100, state["soil"]  + random.gauss(0, 0.3)))
    noisy_light = max(0, min(100, state["light"]))

    motion       = random.random() < 0.03   # ~3% số bước có "người/vật" đi qua (PIR)
    light_status = motion and noisy_light < ACTUATOR_RULES["motion_light"]["trigger_below_light"]

    rows.append((
        round(noisy_temp, 2), round(noisy_hum, 2),
        int(round(noisy_soil)), int(round(noisy_light)),
        motion, fan_on, pump_on, servo, light_status,
        t0 + timedelta(seconds=i * STEP_REAL_S),
    ))

conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)
cur = conn.cursor()
cur.execute("TRUNCATE TABLE sensor_data")
cur.executemany("""
    INSERT INTO sensor_data
        (temperature, humidity, soil_moisture, light_level, motion_detected,
         fan_status, pump_status, servo_angle, light_status, created_at)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
""", rows)
conn.commit()

print(f"Da seed {cur.rowcount} ban ghi kich ban demo (tu {t0} den {now}).")
cur.execute("""
    SELECT MIN(temperature), MAX(temperature), MIN(soil_moisture), MAX(soil_moisture),
           SUM(fan_status), SUM(pump_status)
    FROM sensor_data
""")
tmin, tmax, smin, smax, fan_on_count, pump_on_count = cur.fetchone()
print(f"Nhiet do: {tmin:.1f} - {tmax:.1f} C | Do am dat: {smin} - {smax} %")
print(f"So tick fan bat: {fan_on_count}/{N} | So tick pump bat: {pump_on_count}/{N}")
cur.close()
conn.close()
