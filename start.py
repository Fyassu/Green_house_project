import os
import re
import sys
import json
import time
import subprocess
import webbrowser
import urllib.request

PROJECT_DIR  = os.path.dirname(os.path.abspath(__file__))
SKETCH_PATH  = os.path.join(PROJECT_DIR, "src", "sketch.ino")
BACKEND_DIR  = os.path.join(PROJECT_DIR, "backend")
VENV_PYTHON  = os.path.join(BACKEND_DIR, "greenhouse_wokwi", "Scripts", "python.exe")
FLASK_PORT   = 5000
NGROK_API    = "http://localhost:4040/api/tunnels"

# ─────────────────────────────────────────────
def step(n, total, msg):
    print(f"[{n}/{total}] {msg}")

def ok(msg):
    print(f"      ✓ {msg}\n")

def fail(msg):
    print(f"      ✗ {msg}\n")
    sys.exit(1)

# ─────────────────────────────────────────────
def start_flask(python_exe=VENV_PYTHON):
    step(1, 4, "Starting Flask backend...")
    proc = subprocess.Popen(
        [python_exe, "app.py"],
        cwd=BACKEND_DIR,
    )
    # Wait up to 8s for Flask + MySQL to be ready
    for i in range(8):
        time.sleep(1)
        if proc.poll() is not None:
            fail("Flask crashed on startup — check MySQL credentials in backend/db.py")
        try:
            with urllib.request.urlopen(
                f"http://localhost:{FLASK_PORT}/api/test", timeout=2
            ) as r:
                data = json.load(r)
                if data.get("status") == "ok":
                    ok(f"Flask ready  —  MySQL rows: {data['rows']}")
                    return proc
                else:
                    fail(f"Flask started but DB error: {data.get('detail')}")
        except Exception:
            pass
    fail("Flask did not respond on /api/test after 8s — check MySQL is running")

# ─────────────────────────────────────────────
def start_ngrok():
    step(2, 4, "Starting ngrok tunnel...")
    proc = subprocess.Popen(
        ["ngrok", "http", str(FLASK_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc

# ─────────────────────────────────────────────
def get_ngrok_url(timeout=30):
    step(3, 4, "Waiting for ngrok URL...")
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(NGROK_API, timeout=2) as r:
                data = json.load(r)
                for tunnel in data.get("tunnels", []):
                    if tunnel.get("proto") == "https":
                        return tunnel["public_url"]
        except Exception:
            pass
        time.sleep(1)
    return None

# ─────────────────────────────────────────────
def update_sketch(ngrok_url):
    endpoint = ngrok_url.rstrip("/") + "/api/sensor-data"
    with open(SKETCH_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    updated = re.sub(
        r'"https://[^"]+/api/sensor-data"',
        f'"{endpoint}"',
        content,
    )

    if updated == content:
        print(f"      URL already up-to-date")
        return False

    with open(SKETCH_PATH, "w", encoding="utf-8") as f:
        f.write(updated)
    print(f"      sketch.ino  →  {endpoint}")
    return True

# ─────────────────────────────────────────────
def build_firmware(url_changed):
    step(4, 4, "Building firmware with PlatformIO...")
    if not url_changed:
        ok("URL unchanged — skipping rebuild")
        return

    result = subprocess.run(["pio", "run"], cwd=PROJECT_DIR)
    if result.returncode != 0:
        fail("PlatformIO build failed — run 'pio run' manually to see errors")
    ok("Firmware built  →  .pio/build/esp32dev/firmware.bin")

# ─────────────────────────────────────────────
def print_summary(ngrok_url):
    print("══════════════════════════════════════════")
    print(f"  Flask   →  http://localhost:{FLASK_PORT}")
    print(f"  ngrok   →  {ngrok_url}")
    print(f"  API     →  {ngrok_url}/api/sensor-data")
    print("══════════════════════════════════════════")
    print("  Firmware ready — start Wokwi in VS Code")
    print("  (F1 → \"Wokwi: Start Simulator\")")
    print("══════════════════════════════════════════")
    print("  Ctrl+C to stop Flask + ngrok\n")

# ─────────────────────────────────────────────
def main():
    print("\n╔══════════════════════════════════════════╗")
    print("║       Greenhouse Project Launcher        ║")
    print("╚══════════════════════════════════════════╝\n")

    if not os.path.exists(VENV_PYTHON):
        fail(
            f"venv not found at:\n"
            f"        {VENV_PYTHON}\n\n"
            f"      Create it with:\n"
            f"        cd backend\n"
            f"        python -m venv greenhouse_wokwi\n"
            f"        greenhouse_wokwi\\Scripts\\activate\n"
            f"        pip install -r requirements.txt"
        )

    print(f"      Using venv: {VENV_PYTHON}\n")

    flask_proc = start_flask(VENV_PYTHON)
    ngrok_proc = start_ngrok()

    ngrok_url = get_ngrok_url()
    if not ngrok_url:
        flask_proc.terminate()
        ngrok_proc.terminate()
        fail("ngrok URL not found — is ngrok installed? Run: winget install ngrok")

    ok(ngrok_url)

    url_changed = update_sketch(ngrok_url)
    build_firmware(url_changed)

    print_summary(ngrok_url)

    try:
        flask_proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        flask_proc.terminate()
        ngrok_proc.terminate()
        print("✓ All processes stopped")

if __name__ == "__main__":
    main()
