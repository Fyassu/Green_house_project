"""
Pipeline diagnostic: tests each link in the chain independently.

  [A] Flask reachable?          → GET  localhost:5000/api/test
  [B] Flask can write to MySQL? → POST localhost:5000/api/sensor-data  (mock data)
  [C] Row actually in MySQL?    → SELECT sensor_data WHERE id = <new id>
  [D] ngrok URL valid?          → reads src/sketch.ino, pings the URL
  [E] ngrok → Flask → MySQL?    → POST <ngrok-url>/api/sensor-data (mock data)

Run from the backend/ folder:
    greenhouse_wokwi\\Scripts\\python.exe diagnose.py
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error

FLASK_LOCAL  = "http://localhost:5000"
SKETCH_PATH  = os.path.join(os.path.dirname(__file__), "..", "src", "sketch.ino")

MOCK_PAYLOAD = json.dumps({
    "temperature":    25.0,
    "humidity":       60.0,
    "soil_moisture":  50,
    "light_level":    40,
    "motion_detected": False,
    "fan_status":     False,
    "pump_status":    False,
    "servo_angle":    90,
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
        headers={
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",
        },
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
def read_ngrok_url_from_sketch():
    try:
        with open(SKETCH_PATH, encoding="utf-8") as f:
            content = f.read()
        m = re.search(r'"(https://[^"]+)/api/sensor-data"', content)
        return m.group(1) if m else None
    except Exception:
        return None

def check_ngrok(ngrok_base):
    title("D  ngrok tunnel reachable ?")
    info(f"URL in sketch.ino: {ngrok_base}")
    try:
        status, data = get(f"{ngrok_base}/api/test", timeout=8)
        if data.get("status") == "ok":
            passed(f"ngrok tunnel is live  —  rows: {data['rows']}")
            return True
        else:
            failed(f"Tunnel reached Flask but DB error: {data.get('detail')}")
            return False
    except urllib.error.HTTPError as e:
        failed(f"HTTP {e.code} from ngrok — tunnel may be expired or wrong URL")
        info("→ Restart ngrok and run start.py again to update sketch.ino URL")
        return False
    except urllib.error.URLError as e:
        failed(f"Cannot reach ngrok URL: {e.reason}")
        info("→ Is ngrok running? Check with: ngrok http 5000")
        return False
    except Exception as e:
        failed(str(e))
        return False

# ─────────────────────────────────────────────────────────
def check_ngrok_insert(ngrok_base):
    title("E  ngrok → Flask → MySQL (full path Wokwi uses) ?")
    try:
        _, before = get(f"{FLASK_LOCAL}/api/test")
        rows_before = before.get("rows", 0)

        status, resp = post(f"{ngrok_base}/api/sensor-data", timeout=15)
        info(f"POST status  : {status}")
        info(f"POST response: {resp}")

        if status != 200 or resp.get("message") != "saved":
            failed(f"ngrok insert failed: {resp}")
            return False

        _, after = get(f"{FLASK_LOCAL}/api/test")
        rows_after = after.get("rows", 0)

        if rows_after > rows_before:
            passed(f"Full path works  ({rows_before} → {rows_after} rows)")
            info("→ Wokwi should be sending data successfully")
            info("   If Wokwi still doesn't send: check Serial monitor in Wokwi")
            info("   for the HTTP Response code after 'HTTP Response:'")
            return True
        else:
            failed("ngrok POST returned 200 but no new row in MySQL")
            return False

    except Exception as e:
        failed(str(e))
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

    # D — ngrok
    ngrok_base = read_ngrok_url_from_sketch()
    if not ngrok_base:
        print("\n  [SKIP] D+E  Could not read ngrok URL from sketch.ino\n")
        sys.exit(0)

    ngrok_ok = check_ngrok(ngrok_base)
    if not ngrok_ok:
        print("\n  ✗ ngrok tunnel broken. Fix step D first.\n")
        sys.exit(1)

    # E — ngrok → Flask → MySQL (same path Wokwi uses)
    check_ngrok_insert(ngrok_base)

    print(f"\n{'═'*56}\n")

if __name__ == "__main__":
    main()
