"""Start the Flask backend and static dashboard together."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(PROJECT_DIR, "backend")
FLASK_PORT = 5000


def find_venv_python() -> str:
    candidates = (
        os.path.join(BACKEND_DIR, "venv", "Scripts", "python.exe"),
        os.path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe"),
        os.path.join(
            BACKEND_DIR, "greenhouse_wokwi", "Scripts", "python.exe"
        ),
    )
    return next((path for path in candidates if os.path.exists(path)), sys.executable)


def wait_for_backend(process: subprocess.Popen, timeout_seconds: int = 12) -> None:
    for _ in range(timeout_seconds):
        time.sleep(1)
        if process.poll() is not None:
            raise RuntimeError(
                "Flask stopped during startup. Check backend/config.py and MySQL."
            )
        try:
            with urllib.request.urlopen(
                f"http://localhost:{FLASK_PORT}/api/test", timeout=2
            ) as response:
                payload = json.load(response)
            if payload.get("status") == "ok":
                print(f"[OK] Flask ready - MySQL rows: {payload['rows']}")
                return
        except Exception:
            continue
    raise RuntimeError("Flask did not become ready within 12 seconds.")


def main() -> None:
    python_exe = find_venv_python()
    print("Greenhouse Digital Twin")
    print(f"Python: {python_exe}")

    backend = subprocess.Popen(
        [python_exe, "app.py"],
        cwd=BACKEND_DIR,
    )
    dashboard = None
    try:
        wait_for_backend(backend)
        dashboard = subprocess.Popen(
            [sys.executable, "-m", "http.server", "3000"],
            cwd=os.path.join(PROJECT_DIR, "dashboard"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("[OK] Dashboard: http://localhost:3000")
        print("[INFO] Start Wokwi with: F1 -> Wokwi: Start Simulator")
        print("[INFO] Press Ctrl+C to stop.")
        backend.wait()
    except KeyboardInterrupt:
        print("\nStopping Greenhouse Digital Twin...")
    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1) from exc
    finally:
        if backend.poll() is None:
            backend.terminate()
        if dashboard and dashboard.poll() is None:
            dashboard.terminate()
        print("[OK] Processes stopped.")


if __name__ == "__main__":
    main()
