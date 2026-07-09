import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import paho.mqtt.client as mqtt
import json
import threading
import queue as _queue
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import base64
import jwt
import bcrypt
import datetime
from datetime import timezone
import time
from functools import wraps
import struct

from db import get_cursor, get_new_connection
from model import evaluate_plant

app = Flask(__name__)
CORS(app)

JWT_SECRET = "GreenHouseProject_SecretKey_Dk9#mN2!pQ8z$vL5*bC1"

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token:
            return jsonify({"error": "Token missing"}), 401
        try:
            jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token invalid"}), 401
        return f(*args, **kwargs)
    return decorated

MQTT_BROKER       = "broker.hivemq.com"
MQTT_PORT         = 1883
MQTT_TOPIC_SENSORS  = "greenhouse/sensors/data"
MQTT_TOPIC_COMMANDS = "greenhouse/commands/control"

SPECK_KEY = b"MySuperSecretKey"  # 16 bytes
SPECK_IV  = 0x1234567890abcdef   # 64-bit integer

def ROTL32(x, r):
    return (((x << r) & 0xffffffff) | (x >> (32 - r))) & 0xffffffff

def ROTR32(x, r):
    return ((x >> r) | ((x << (32 - r)) & 0xffffffff)) & 0xffffffff

def speck_encrypt_block(pt, rk):
    y, x = pt
    for i in range(27):
        x = ((ROTR32(x, 8) + y) & 0xffffffff) ^ rk[i]
        y = ROTL32(y, 3) ^ x
    return y, x

def speck_key_schedule(key_bytes):
    k = list(struct.unpack("<4I", key_bytes))
    rk = [0] * 27
    rk[0] = k[0]
    l = [k[1], k[2], k[3]]
    for i in range(26):
        l_new = ((ROTR32(l[i], 8) + rk[i]) & 0xffffffff) ^ i
        l.append(l_new)
        rk[i+1] = ROTL32(rk[i], 3) ^ l_new
    return rk

def speck_ctr_encrypt(data_bytes, key_bytes, iv_int):
    rk = speck_key_schedule(key_bytes)
    out = bytearray()
    num_blocks = (len(data_bytes) + 7) // 8
    for j in range(num_blocks):
        counter = (iv_int + j) & 0xffffffffffffffff
        y = counter & 0xffffffff
        x = (counter >> 32) & 0xffffffff
        cy, cx = speck_encrypt_block((y, x), rk)
        keystream = struct.pack("<2I", cy, cx)
        start = j * 8
        end = min(start + 8, len(data_bytes))
        for idx in range(start, end):
            out.append(data_bytes[idx] ^ keystream[idx - start])
    return bytes(out)

def encrypt_aes(plaintext_str):
    data_bytes = plaintext_str.encode("utf-8")
    ct_bytes = speck_ctr_encrypt(data_bytes, SPECK_KEY, SPECK_IV)
    return base64.b64encode(ct_bytes).decode("utf-8")

def decrypt_aes(base64_str):
    ct_bytes = base64.b64decode(base64_str)
    pt_bytes = speck_ctr_encrypt(ct_bytes, SPECK_KEY, SPECK_IV)
    return pt_bytes.decode("utf-8")

# In-memory command store — reset khi Flask khởi động lại
# None = auto (firmware tự điều khiển), True/False = override
_commands = {"fan": None, "pump": None, "servo": None, "light": None, "security": True}

# ── Server-Sent Events broadcast ──────────────────────────────────────────
_sse_clients: list[_queue.Queue] = []
_sse_lock = threading.Lock()

def _sse_broadcast(payload: dict):
    msg = json.dumps(payload)
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)

@app.route("/api/stream")
def sse_stream():
    q = _queue.Queue(maxsize=30)
    with _sse_lock:
        _sse_clients.append(q)

    def generate():
        try:
            while True:
                try:
                    msg = q.get(timeout=5)   # short timeout → fast thread cleanup on disconnect
                    yield f"data: {msg}\n\n"
                except _queue.Empty:
                    yield ": heartbeat\n\n"   # keep-alive; browser EventSource ignores comments
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Connection":                  "keep-alive",
            "Access-Control-Allow-Origin": "*",   # explicit — streaming bypasses after_request hooks
        },
    )


# ==========================
# LOGIN
# ==========================
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    try:
        cursor = get_cursor()
        cursor.execute("SELECT password_hash FROM users WHERE username = %s", (username,))
        row = cursor.fetchone()
        cursor.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if row is None or not bcrypt.checkpw(password.encode(), row[0].encode()):
        return jsonify({"error": "Invalid credentials"}), 401

    token = jwt.encode(
        {"sub": username, "exp": datetime.datetime.now(timezone.utc) + datetime.timedelta(hours=8)},
        JWT_SECRET, algorithm="HS256"
    )
    return jsonify({"token": token, "username": username})


# ==========================
# HEALTH CHECK
# ==========================
@app.route("/api/test", methods=["GET"])
def test():
    try:
        cursor = get_cursor()
        cursor.execute("SELECT COUNT(*) FROM sensor_data")
        count = cursor.fetchone()[0]
        cursor.close()
        return jsonify({"status": "ok", "rows": count})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


# ==========================
# POST SENSOR DATA
# ==========================
@app.route("/api/sensor-data", methods=["POST"])
def save_sensor_data():

    data = request.get_json(silent=True)

    if data is None:
        return jsonify({
            "error": "JSON parse failed"
        }), 400

    try:

        sql = """
        INSERT INTO sensor_data(
            temperature,
            humidity,
            soil_moisture,
            light_level,
            motion_detected,
            fan_status,
            pump_status,
            servo_angle,
            light_status
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """

        values = (
            data["temperature"],
            data["humidity"],
            data["soil_moisture"],
            data["light_level"],
            data["motion_detected"],
            data["fan_status"],
            data["pump_status"],
            data["servo_angle"],
            data.get("light_status", False)
        )

        cursor = get_cursor()
        cursor.execute(sql, values)
        cursor.close()

        return jsonify({"message": "saved"})

    except Exception as e:

        print("DATABASE ERROR:")
        print(str(e))

        return jsonify({
            "error": str(e)
        }), 500


# ==========================
# GET LATEST RECORD
# ==========================
@app.route("/api/latest", methods=["GET"])
@token_required
def latest_data():

    try:

        cursor = get_cursor()
        cursor.execute("""
            SELECT *
            FROM sensor_data
            ORDER BY id DESC
            LIMIT 1
        """)

        row = cursor.fetchone()
        cursor.close()

        if row is None:
            return jsonify({
                "message": "No data"
            })

        return jsonify({
            "id": row[0],
            "temperature": row[1],
            "humidity": row[2],
            "soil_moisture": row[3],
            "light_level": row[4],
            "motion_detected": bool(row[5]),
            "fan_status": bool(row[6]),
            "pump_status": bool(row[7]),
            "servo_angle": row[8],
            "light_status": bool(row[9]),
            "security_mode": _commands.get("security", True),
            "created_at": str(row[10] + datetime.timedelta(hours=7)) if row[10] else None,
            "plant_health": evaluate_plant(row[1], row[3], row[2]),
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500


# ==========================
# GET HISTORY
# ==========================
@app.route("/api/history", methods=["GET"])
@token_required
def history():

    try:

        cursor = get_cursor()
        cursor.execute("""
            SELECT *
            FROM sensor_data
            ORDER BY created_at DESC
            LIMIT 100
        """)

        rows = cursor.fetchall()
        cursor.close()

        result = []

        for row in rows:

            result.append({
                "id": row[0],
                "temperature": row[1],
                "humidity": row[2],
                "soil_moisture": row[3],
                "light_level": row[4],
                "motion_detected": bool(row[5]),
                "fan_status": bool(row[6]),
                "pump_status": bool(row[7]),
                "servo_angle": row[8],
                "light_status": bool(row[9]),
                "created_at": str(row[10] + datetime.timedelta(hours=7)) if row[10] else None,
                "plant_health": evaluate_plant(row[1], row[3], row[2]),
            })

        return jsonify(result)

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500


# ==========================
# GET COMMANDS (ESP32 polls this)
# ==========================
@app.route("/api/commands", methods=["GET"])
def get_commands():
    return jsonify(_commands)


# ==========================
# POST CONTROL (dashboard sends commands)
# ==========================
@app.route("/api/control", methods=["POST"])
@token_required
def set_control():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid JSON"}), 400
    for key in ("fan", "pump", "servo", "light", "security"):
        if key in data:
            _commands[key] = data[key]

    # Publish lệnh điều khiển qua MQTT (mã hóa AES)
    payload_json = json.dumps(_commands)
    encrypted    = encrypt_aes(payload_json)
    print(f"\n[BACKEND] Gửi lệnh điều khiển (Publish):")
    print(f" -> Raw JSON : {payload_json}")
    print(f" -> Encrypted: {encrypted}")
    mqtt_client.publish(MQTT_TOPIC_COMMANDS, encrypted)

    return jsonify({"ok": True, "commands": _commands})


# ==========================
# MQTT CLIENT SETUP
# ==========================
def on_mqtt_connect(client, userdata, flags, reason_code, properties=None):
    print(f"MQTT Connected — code: {reason_code}")
    client.subscribe(MQTT_TOPIC_SENSORS)

def on_mqtt_message(client, userdata, msg):
    try:
        raw_text = msg.payload.decode()

        # Tự động nhận diện: plain JSON hay AES encrypted
        try:
            data = json.loads(raw_text)
            print(f"\n[BACKEND] Nhận dữ liệu (Plain JSON):")
            print(f" <- {data}")
        except (json.JSONDecodeError, ValueError):
            print(f"\n[BACKEND] Nhận dữ liệu cảm biến (Subscribe):")
            print(f" <- Encrypted: {raw_text}")
            data = json.loads(decrypt_aes(raw_text))
            print(f" <- Decrypted: {data}")

        sql = """
        INSERT INTO sensor_data(
            temperature, humidity, soil_moisture, light_level,
            motion_detected, fan_status, pump_status, servo_angle, light_status
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        values = (
            data.get("temperature", 0),
            data.get("humidity", 0),
            data.get("soil_moisture", 0),
            data.get("light_level", 0),
            data.get("motion_detected", False),
            data.get("fan_status", False),
            data.get("pump_status", False),
            data.get("servo_angle", 90),
            data.get("light_status", False),
        )
        conn = get_new_connection()
        cur  = conn.cursor()
        cur.execute(sql, values)
        cur.close()
        conn.close()
        print("MQTT data saved OK")

        # Tự động gửi lại cấu hình lệnh điều khiển hiện tại cho ESP32 để đồng bộ (đặc biệt khi ESP32 restart)
        payload_json = json.dumps(_commands)
        encrypted    = encrypt_aes(payload_json)
        client.publish(MQTT_TOPIC_COMMANDS, encrypted)

        _sse_broadcast({
            "temperature":     data.get("temperature", 0),
            "humidity":        data.get("humidity", 0),
            "soil_moisture":   data.get("soil_moisture", 0),
            "light_level":     data.get("light_level", 0),
            "motion_detected": data.get("motion_detected", False),
            "fan_status":      data.get("fan_status", False),
            "pump_status":     data.get("pump_status", False),
            "servo_angle":     data.get("servo_angle", 90),
            "light_status":    data.get("light_status", False),
            "security_mode":   _commands.get("security", True),
            "plant_health":    evaluate_plant(
                                   data.get("temperature", 0),
                                   data.get("soil_moisture", 0),
                                   data.get("humidity", 0),
                               ),
            "created_at": str(datetime.datetime.now()),
        })
    except Exception as e:
        print("MQTT DB ERROR:", str(e))

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message

def start_mqtt():
    while True:
        try:
            mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            mqtt_client.loop_forever()
        except Exception as e:
            print(f"MQTT Connect Error: {e}. Retrying in 5s...")
            time.sleep(5)


# ==========================
# MAIN
# ==========================
if __name__ == "__main__":

    print("Flask Server Started — starting MQTT thread...")
    mqtt_thread = threading.Thread(target=start_mqtt, daemon=True)
    mqtt_thread.start()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False  # tránh duplicate MQTT client khi debug reloader
    )

