import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import paho.mqtt.client as mqtt
import json
import os
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

from db import (
    get_control_events,
    get_cursor,
    get_new_connection,
    get_training_window,
    load_plant_health_states,
    log_control_event,
    save_plant_health_states,
)
from model import evaluate_plant
from prediction import simulate_forward, backtest_compare, hybrid_forecast, detect_actuator_anomalies, ml_forecast

from plant_hp import PlantHPEngine
from hybrid_model import HybridPredictionEngine
from closed_loop import PredictiveController

try:
    _initial_plant_states = load_plant_health_states()
except Exception as exc:
    print(f"[PLANT HP WARNING] Could not load saved state: {exc}")
    _initial_plant_states = {}
plant_hp_engine = PlantHPEngine(_initial_plant_states)
hybrid_engine = HybridPredictionEngine()
predictive_controller = PredictiveController()

app = Flask(__name__)
CORS(app, origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","))

JWT_SECRET = os.getenv(
    "JWT_SECRET",
    "dev-only-change-this-greenhouse-secret",
)

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

MQTT_BROKER = os.getenv("MQTT_BROKER", "test.mosquitto.org")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC_SENSORS = os.getenv(
    "MQTT_TOPIC_SENSORS", "greenhouse/sensors/data"
)
MQTT_TOPIC_COMMANDS = os.getenv(
    "MQTT_TOPIC_COMMANDS", "greenhouse/commands/control"
)





# In-memory command store — reset khi Flask khởi động lại
# None = auto (firmware tự điều khiển), True/False = override
_manual_overrides = {
    "fan": None,
    "pump": None,
    "servo": None,
    "light": None,
    "security": True,
}
_commands = predictive_controller.effective_commands(_manual_overrides)
_command_lock = threading.Lock()
_plant_hp_lock = threading.Lock()


def _refresh_commands() -> dict:
    global _commands
    with _command_lock:
        _commands = predictive_controller.effective_commands(_manual_overrides)
        return dict(_commands)


def _publish_commands(client=None) -> dict:
    commands = _refresh_commands()
    publisher = client or mqtt_client
    publisher.publish(MQTT_TOPIC_COMMANDS, json.dumps(commands))
    return commands


def _log_event_safely(event: dict) -> None:
    try:
        log_control_event(event)
    except Exception as exc:
        print(f"[CONTROL LOG WARNING] {exc}")


def _normalise_sensor_payload(data: dict) -> dict:
    """Validate telemetry before it reaches the database or prediction model."""
    if not isinstance(data, dict):
        raise ValueError("Sensor payload must be a JSON object")

    limits = {
        "temperature": (-20.0, 70.0),
        "humidity": (0.0, 100.0),
        "soil_moisture": (0.0, 100.0),
        "light_level": (0.0, 100.0),
        "servo_angle": (0.0, 90.0),
    }
    result = {}
    for field, (minimum, maximum) in limits.items():
        if field not in data:
            raise ValueError(f"Missing sensor field: {field}")
        value = data[field]
        if isinstance(value, bool):
            raise ValueError(f"{field} must be numeric")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be numeric") from exc
        if not minimum <= numeric <= maximum:
            raise ValueError(
                f"{field} must be between {minimum:g} and {maximum:g}"
            )
        result[field] = (
            int(round(numeric)) if field == "servo_angle" else numeric
        )

    for field in (
        "motion_detected",
        "fan_status",
        "pump_status",
        "light_status",
    ):
        value = data.get(field, False)
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be true or false")
        result[field] = value
    return result


def _update_plant_hp(data: dict, observed_at=None) -> dict:
    with _plant_hp_lock:
        summary = plant_hp_engine.update_all_crops(
            data["temperature"],
            data["humidity"],
            data["soil_moisture"],
            data["light_level"],
            observed_at=observed_at,
        )
        save_plant_health_states(summary)
        return summary

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
            "Access-Control-Allow-Origin": os.getenv(
                "CORS_ORIGINS", "http://localhost:3000"
            ).split(",")[0],
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
        data = _normalise_sensor_payload(data)

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

        hp_summary = _update_plant_hp(
            data, observed_at=datetime.datetime.now(timezone.utc)
        )
        return jsonify({"message": "saved", "plant_hp": hp_summary})

    except ValueError as e:
        return jsonify({"error": str(e)}), 400

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
            "plant_hp": plant_hp_engine.get_summary(),
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
# GET PREDICT (Analytics/Prediction Layer — Giai đoạn 2)
# ==========================
@app.route("/api/predict", methods=["GET"])
@token_required
def predict():
    try:
        horizon = request.args.get("horizon_minutes", default=30, type=int)

        cursor = get_cursor()
        cursor.execute("""
            SELECT temperature, humidity, soil_moisture, light_level,
                   fan_status, pump_status, servo_angle
            FROM sensor_data
            ORDER BY id DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        cursor.close()

        if row is None:
            return jsonify({"error": "No data"}), 404

        reading = {
            "temperature":   row[0],
            "humidity":      row[1],
            "soil_moisture": row[2],
            "light_level":   row[3],
            "fan_status":    bool(row[4]),
            "pump_status":   bool(row[5]),
            "servo_angle":   row[6],
        }

        engine = request.args.get("engine", default="hybrid", type=str)

        if engine in ("ml", "hybrid"):
            recent_rows = get_training_window(hours=1, max_rows=50)
            if engine == "ml":
                forecast = ml_forecast(recent_rows, horizon_minutes=horizon)
                return jsonify({
                    "method":          "residual_physics_guided_ml",
                    "engine_used":     "Residual PGML (legacy ml alias)",
                    "horizon_minutes": min(horizon, 30),
                    "forecast":        forecast,
                })
            else:
                # Build 5-step sliding window matrix
                window = []
                for r in (recent_rows[-5:] if len(recent_rows) >= 5 else recent_rows):
                    window.append([
                        r.get("temperature", 25.0),
                        r.get("humidity", 60.0),
                        r.get("soil_moisture", 65.0),
                        r.get("light_level", 50.0),
                        1.0 if r.get("fan_status") else 0.0,
                        1.0 if r.get("pump_status") else 0.0,
                        float(r.get("servo_angle", 0))
                    ])
                while len(window) < 5:
                    window.insert(0, [reading["temperature"], reading["humidity"], reading["soil_moisture"], reading["light_level"], 1.0 if reading["fan_status"] else 0.0, 1.0 if reading["pump_status"] else 0.0, float(reading["servo_angle"])])

                requested_horizon = max(5, min(horizon, 30))
                hybrid_steps = hybrid_engine.predict_hybrid(
                    window,
                    reading["fan_status"],
                    reading["pump_status"],
                    reading["servo_angle"],
                )
                hybrid_steps = [
                    point
                    for point in hybrid_steps
                    if point["minutes"] <= requested_horizon
                ]
                raw_alert = hybrid_engine.check_predictive_closed_loop(hybrid_steps)
                closed_loop_alert = predictive_controller.preview(raw_alert)
                return jsonify({
                    "method":          "hybrid_physics_ml",
                    "engine_used":     "Residual Physics-Guided ML Model",
                    "horizon_minutes": requested_horizon,
                    "forecast":        hybrid_steps,
                    "closed_loop_alert": closed_loop_alert,
                    "control_mode": predictive_controller.mode,
                })

        forecast = simulate_forward(reading, horizon_minutes=horizon)
        return jsonify({
            "method":          "physics_lumped_parameter",
            "assumption":      "Actuator giữ nguyên trạng thái hiện tại (fan/pump/servo)",
            "base_reading":    reading,
            "horizon_minutes": max(5, min(horizon, 120)),
            "forecast":        forecast,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# GET & RESET PLANT HP
# ==========================
@app.route("/api/plant-hp", methods=["GET"])
@token_required
def get_plant_hp():
    return jsonify(plant_hp_engine.get_summary())


@app.route("/api/plant-hp/evaluate", methods=["POST"])
@token_required
def evaluate_plant_hp():
    data = request.get_json(silent=True)
    try:
        data = _normalise_sensor_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    summary = _update_plant_hp(
        data, observed_at=datetime.datetime.now(timezone.utc)
    )
    return jsonify({"summary": summary})


@app.route("/api/plant-hp/reset", methods=["POST"])
@token_required
def reset_plant_hp():
    data = request.get_json(silent=True) or {}
    crop_key = data.get("crop", "all")
    try:
        with _plant_hp_lock:
            summary = plant_hp_engine.reset_crop(crop_key)
            save_plant_health_states(summary)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Crop HP reset successful", "summary": summary})


# ==========================
# GET PREDICT COMPARE (Analytics Layer — Giai đoạn 3: Physics vs Data-driven RMSE)
# ==========================
@app.route("/api/predict/compare", methods=["GET"])
@token_required
def predict_compare():
    try:
        column  = request.args.get("column", default="temperature", type=str)
        holdout = request.args.get("holdout", default=10, type=int)
        hours   = request.args.get("hours", default=6, type=int)

        valid_columns = {"temperature", "humidity", "soil_moisture", "light_level"}
        if column not in valid_columns:
            return jsonify({"error": f"column phải là một trong {sorted(valid_columns)}"}), 400
        holdout = max(3, min(holdout, 50))

        rows = get_training_window(hours=hours)
        if len(rows) < holdout + 15:
            return jsonify({
                "error": f"Chưa đủ dữ liệu lịch sử để backtest (cần tối thiểu {holdout + 15} "
                         f"bản ghi trong {hours} giờ gần nhất, hiện có {len(rows)}). "
                         f"Thử tăng `hours` hoặc chờ hệ thống chạy lâu hơn."
            }), 400

        result = backtest_compare(rows, column, holdout=holdout)
        result["hours_window"]     = hours
        result["training_points"]  = len(rows) - holdout
        return jsonify(result)

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# GET ANOMALY (Analytics Layer — Giai đoạn 4: phát hiện bơm/quạt bất thường)
# ==========================
@app.route("/api/anomaly", methods=["GET"])
@token_required
def anomaly():
    try:
        window = request.args.get("window", default=10, type=int)
        window = max(3, min(window, 50))

        rows = get_training_window(hours=1, max_rows=200)
        if len(rows) < window + 1:
            return jsonify({
                "error": f"Chưa đủ dữ liệu để đánh giá (cần tối thiểu {window + 1} "
                         f"bản ghi trong 1 giờ gần nhất, hiện có {len(rows)}).",
                "anomalies": [],
            }), 200

        results = detect_actuator_anomalies(rows, window=window)
        return jsonify({"window": window, "anomalies": results})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# GET COMMANDS (ESP32 polls this)
# ==========================
@app.route("/api/commands", methods=["GET"])
def get_commands():
    commands = _refresh_commands()
    return jsonify({
        **commands,
        "control_mode": predictive_controller.mode,
        "manual_overrides": dict(_manual_overrides),
        "controller": predictive_controller.snapshot(),
    })


@app.route("/api/control-mode", methods=["GET", "POST"])
@token_required
def control_mode():
    if request.method == "GET":
        return jsonify({
            **predictive_controller.snapshot(),
            "effective_commands": _refresh_commands(),
            "manual_overrides": dict(_manual_overrides),
        })

    data = request.get_json(silent=True) or {}
    try:
        decision = predictive_controller.set_mode(data.get("mode", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    commands = _publish_commands()
    _log_event_safely({
        **decision,
        "event_type": "mode",
        "source": "manual",
        "status": "mode_changed",
        "messages": [
            f"Control mode changed from "
            f"{decision.get('previous_mode')} to {predictive_controller.mode}."
        ],
    })
    payload = {
        **predictive_controller.snapshot(),
        "effective_commands": commands,
        "manual_overrides": dict(_manual_overrides),
    }
    _sse_broadcast({"type": "control_state", "control_state": payload})
    return jsonify(payload)


@app.route("/api/control-events", methods=["GET"])
@token_required
def control_events():
    try:
        limit = request.args.get("limit", default=30, type=int)
        return jsonify(get_control_events(limit))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/model/status", methods=["GET"])
@token_required
def model_status():
    metadata = hybrid_engine.metadata or {}
    return jsonify({
        "ready": hybrid_engine.rf_model is not None,
        "runtime": (
            "residual_pgml"
            if hybrid_engine.rf_model is not None
            else "physics_only_fallback"
        ),
        "artifact_error": hybrid_engine.artifact_error,
        "model_contract_version": metadata.get("model_contract_version"),
        "physics_model_version": metadata.get("physics_model_version"),
        "feature_count": metadata.get("feature_count"),
        "training_rows": metadata.get("training_rows"),
        "dataset_sha256": metadata.get("dataset_sha256"),
    })


# ==========================
# POST CONTROL (dashboard sends commands)
# ==========================
@app.route("/api/control", methods=["POST"])
@token_required
def set_control():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid JSON"}), 400

    allowed = {"fan", "pump", "servo", "light", "security"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        return jsonify({"error": f"Unknown control fields: {unknown}"}), 400

    changed = {}
    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if key in {"fan", "pump", "light"} and value not in {True, False, None}:
            return jsonify({"error": f"{key} must be true, false, or null"}), 400
        if key == "servo" and value not in {0, 45, 90, None}:
            return jsonify({"error": "servo must be 0, 45, 90, or null"}), 400
        if key == "security" and value not in {True, False}:
            return jsonify({"error": "security must be true or false"}), 400
        _manual_overrides[key] = value
        changed[key] = value

    predictive_controller.clear_auto(
        [key for key in changed if key in {"fan", "pump", "servo", "light"}]
    )

    # Publish lệnh điều khiển qua MQTT (JSON thô)
    commands = _publish_commands()
    payload_json = json.dumps(commands)
    print(f"\n[BACKEND] Gửi lệnh điều khiển (Publish):")
    print(f" -> Raw JSON : {payload_json}")
    _log_event_safely({
        "event_type": "command",
        "mode": predictive_controller.mode,
        "source": "manual",
        "status": "executed",
        "messages": ["Manual override updated from dashboard."],
        "applied_commands": changed,
    })
    _sse_broadcast({
        "type": "control_state",
        "control_state": {
            **predictive_controller.snapshot(),
            "effective_commands": commands,
            "manual_overrides": dict(_manual_overrides),
        },
    })

    return jsonify({
        "ok": True,
        "commands": commands,
        "manual_overrides": dict(_manual_overrides),
        "control_mode": predictive_controller.mode,
    })


# ==========================
# MQTT CLIENT SETUP
# ==========================
def on_mqtt_connect(client, userdata, flags, reason_code, properties=None):
    print(f"MQTT Connected — code: {reason_code}")
    client.subscribe(MQTT_TOPIC_SENSORS)

def on_mqtt_message(client, userdata, msg):
    try:
        raw_text = msg.payload.decode()

        try:
            data = json.loads(raw_text)
            print(f"\n[BACKEND] Nhận dữ liệu (Plain JSON): {data}")
        except Exception as err:
            print(f"\n[BACKEND] Lỗi parse JSON MQTT: {raw_text} | Error: {err}")
            return

        try:
            data = _normalise_sensor_payload(data)
        except ValueError as err:
            print(f"[MQTT VALIDATION ERROR] {err}")
            return

        sql = """
        INSERT INTO sensor_data(
            temperature, humidity, soil_moisture, light_level,
            motion_detected, fan_status, pump_status, servo_angle, light_status
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        soil_val = data["soil_moisture"]

        values = (
            data.get("temperature", 0),
            data.get("humidity", 0),
            soil_val,
            data.get("light_level", 0),
            data.get("motion_detected", False),
            data.get("fan_status", False),
            data.get("pump_status", False),
            data.get("servo_angle", 0),
            data.get("light_status", False),
        )
        conn = get_new_connection()
        cur  = conn.cursor()
        cur.execute(sql, values)
        cur.close()
        conn.close()
        observed_at = datetime.datetime.now(timezone.utc)
        hp_summary = _update_plant_hp(data, observed_at=observed_at)

        # Check Predictive Closed-Loop Control
        closed_loop_decision = None
        try:
            recent_rows = get_training_window(hours=1, max_rows=50)
            window = []
            for r in (recent_rows[-5:] if len(recent_rows) >= 5 else recent_rows):
                window.append([
                    r.get("temperature", 25.0),
                    r.get("humidity", 60.0),
                    r.get("soil_moisture", 65.0),
                    r.get("light_level", 50.0),
                    1.0 if r.get("fan_status") else 0.0,
                    1.0 if r.get("pump_status") else 0.0,
                    float(r.get("servo_angle", 0))
                ])
            while len(window) < 5:
                window.insert(0, [data.get("temperature", 25.0), data.get("humidity", 60.0), soil_val, data.get("light_level", 50.0), 1.0 if data.get("fan_status") else 0.0, 1.0 if data.get("pump_status") else 0.0, float(data.get("servo_angle", 0))])

            hybrid_steps = hybrid_engine.predict_hybrid(window, data.get("fan_status", False), data.get("pump_status", False), data.get("servo_angle", 0))
            raw_alert = hybrid_engine.check_predictive_closed_loop(hybrid_steps)

            closed_loop_decision = predictive_controller.evaluate(
                raw_alert,
                dict(_manual_overrides),
                observed_at=observed_at,
                model_available=hybrid_engine.rf_model is not None,
                now=observed_at,
            )
            if closed_loop_decision.get("should_log"):
                _log_event_safely({
                    **closed_loop_decision,
                    "event_type": "prediction",
                    "source": "pgml",
                })
        except Exception as cl_err:
            print(f"[CLOSED-LOOP ERROR] {cl_err}")

        # Sync command state to ESP32
        effective_commands = _publish_commands(client)

        _sse_broadcast({
            "temperature":     data.get("temperature", 0),
            "humidity":        data.get("humidity", 0),
            "soil_moisture":   soil_val,
            "light_level":     data.get("light_level", 0),
            "motion_detected": data.get("motion_detected", False),
            "fan_status":      data.get("fan_status", False),
            "pump_status":     data.get("pump_status", False),
            "servo_angle":     data.get("servo_angle", 0),
            "light_status":    data.get("light_status", False),
            "security_mode":   _commands.get("security", True),
            "plant_health":    evaluate_plant(
                                   data.get("temperature", 0),
                                   soil_val,
                                   data.get("humidity", 0),
                               ),
            "plant_hp":        hp_summary,
            "closed_loop_alert": closed_loop_decision,
            "control_state": {
                **predictive_controller.snapshot(),
                "effective_commands": effective_commands,
                "manual_overrides": dict(_manual_overrides),
            },
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
