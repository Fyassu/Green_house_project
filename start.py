import os
import sys
import json
import time
import subprocess
import urllib.request

# Fix Windows terminal encoding so Vietnamese/Unicode print() calls don't crash
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_DIR  = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR  = os.path.join(PROJECT_DIR, "backend")
VENV_PYTHON  = os.path.join(BACKEND_DIR, "greenhouse_wokwi", "Scripts", "python.exe")
FLASK_PORT   = 5000

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
    step(1, 2, "Starting Flask backend...")
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
def start_dashboard():
    step(2, 2, "Starting Dashboard (localhost:3000)...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", "3000"],
        cwd=os.path.join(PROJECT_DIR, "dashboard"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ok("Dashboard running  →  http://localhost:3000")
    return proc

# ─────────────────────────────────────────────
def print_summary():
    print("══════════════════════════════════════════")
    print(f"  Flask      →  http://localhost:{FLASK_PORT}")
    print(f"  Dashboard  →  http://localhost:3000")
    print("══════════════════════════════════════════")
    print("  Firmware ready — start Wokwi in VS Code")
    print("  (F1 → \"Wokwi: Start Simulator\")")
    print("══════════════════════════════════════════")
    print("  Ctrl+C to stop Flask + Dashboard\n")

# ─────────────────────────────────────────────
def main():
    print("\n------------------------------------------")
    print("       Greenhouse Project Launcher        ")
    print("------------------------------------------\n")

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

    flask_proc     = start_flask(VENV_PYTHON)
    dashboard_proc = start_dashboard()

    print_summary()

    try:
        flask_proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        flask_proc.terminate()
        dashboard_proc.terminate()
        print("✓ All processes stopped")

if __name__ == "__main__":
    main()
