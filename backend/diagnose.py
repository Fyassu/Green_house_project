"""
Pipeline diagnostic: tests each link in the chain independently.

  [A] Flask reachable?          → GET  localhost:5000/api/test
  [B] Flask can write to MySQL? → POST localhost:5000/api/sensor-data  (mock data)
  [C] Row actually in MySQL?    → SELECT sensor_data WHERE id = <new id>
  [D] MQTT broker reachable?    → TCP connect to test.mosquitto.org:1883

Run from the backend/ folder:
    greenhouse_wokwi\\Scripts\\python.exe diagnose.py
"""

import json
import socket
import sys
import urllib.request
import urllib.error

FLASK_LOCAL = "http://localhost:5000"

MOCK_PAYLOAD = json.dumps({
    "temperature":    25.0,
    "humidity":       60.0,
    "soil_moisture":  50,
    "light_level":    40,
    "motion_detected": False,
    "fan_status":     False,
    "pump_status":    False,
    "servo_angle":    0,
    "light_status":   False,
}).encode()

SEP = "─" * 56

def title(label):
    print(f"\n{SEP}")
    print(f"  {label}")
    print(SEP)

def passed(msg): print(f"  [PASS] {msg}")
def failed(msg): print(f"  [FAIL] {msg}")
def info(msg):   print(f"         {msg}")

# ─────────────────────────────────────────────────────────
def get(url, timeout=5):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.load(r)

def post(url, timeout=10):
    req = urllib.request.Request(
        url,
        data=MOCK_PAYLOAD,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode())
        except Exception:
            pass
        return e.code, body

# ─────────────────────────────────────────────────────────
def check_flask_health():
    title("A  Flask reachable at localhost:5000 ?")
    try:
        status, data = get(f"{FLASK_LOCAL}/api/test")
        if data.get("status") == "ok":
            passed(f"Flask is UP  —  current rows in DB: {data['rows']}")
            return True
        else:
            failed(f"Flask responded but DB error: {data.get('detail')}")
            info("→ Check MySQL is running and credentials in db.py are correct")
            return False
    except urllib.error.URLError as e:
        failed(f"Cannot reach Flask: {e.reason}")
        info("→ Run: greenhouse_wokwi\\Scripts\\python.exe app.py")
        return False
    except Exception as e:
        failed(str(e))
        return False

# ─────────────────────────────────────────────────────────
def check_flask_insert():
    title("B + C  Flask POST → MySQL INSERT ?")
    try:
        # Count before
        _, before = get(f"{FLASK_LOCAL}/api/test")
        rows_before = before.get("rows", 0)

        # POST mock data
        status, resp = post(f"{FLASK_LOCAL}/api/sensor-data")
        info(f"POST status  : {status}")
        info(f"POST response: {resp}")

        if status != 200 or resp.get("message") != "saved":
            failed(f"HTTP {status} — {resp.get('error', resp)}")
            return False

        # Count after
        _, after = get(f"{FLASK_LOCAL}/api/test")
        rows_after = after.get("rows", 0)

        if rows_after > rows_before:
            passed(f"Row inserted  ({rows_before} → {rows_after} rows)")
            return True
        else:
            failed("POST returned 200 but row count did not increase")
            info("→ Possible commit issue — check db.py get_cursor()")
            return False

    except Exception as e:
        failed(str(e))
        return False

# ─────────────────────────────────────────────────────────
def check_mqtt_broker():
    title("D  MQTT Broker reachable (test.mosquitto.org:1883) ?")
    try:
        sock = socket.create_connection(("test.mosquitto.org", 1883), timeout=8)
        sock.close()
        passed("TCP connect to test.mosquitto.org:1883 OK")
        info("→ ESP32 và Flask đều có thể kết nối MQTT broker")
        return True
    except OSError as e:
        failed(f"Cannot reach MQTT broker: {e}")
        info("→ Kiểm tra kết nối internet hoặc firewall chặn port 1883")
        return False

# ─────────────────────────────────────────────────────────
def main():
    print("\n" + "═" * 56)
    print("  Greenhouse Pipeline Diagnostic")
    print("═" * 56)

    # A — Flask
    flask_ok = check_flask_health()
    if not flask_ok:
        print("\n  ✗ Flask is not running. Fix step A first.\n")
        sys.exit(1)

    # B + C — Flask → MySQL
    insert_ok = check_flask_insert()
    if not insert_ok:
        print("\n  ✗ Flask→MySQL broken. Fix step B/C first.\n")
        sys.exit(1)

    # D — MQTT broker
    check_mqtt_broker()

    print(f"\n{'═'*56}\n")

if __name__ == "__main__":
    main()
