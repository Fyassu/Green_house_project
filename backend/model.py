# Greenhouse business rules — single source of truth for the Python backend.
# Mirrors dashboard/model.js and firmware sketch.ino — keep in sync.

PLANT_HINTS: dict[str, str] = {
    "GOOD":     "Cây phát triển tốt — môi trường lý tưởng",
    "SLOW":     "Cây phát triển chậm — kiểm tra nhiệt độ hoặc độ ẩm",
    "DECLINE":  "Cây đang suy yếu — cần can thiệp sớm",
    "CRITICAL": "Cây trong tình trạng nguy hiểm!",
    "DEAD":     "Cây đã chết hoặc môi trường cực kỳ bất lợi",
}

# Mirrors evaluatePlant() in sketch.ino — keep in sync
PLANT_THRESHOLDS = {
    "DEAD":     {"temp_high": 45, "temp_low": 5,  "soil": 5},
    "CRITICAL": {"temp_high": 38, "temp_low": 10, "soil": 20, "hum": 20},
    "DECLINE":  {"temp_high": 34, "temp_low": 15, "soil": 35, "hum": 35},
    "SLOW":     {"temp_high": 30, "temp_low": 20, "soil": 50, "hum": 45},
}

# Mirrors controlFan / controlPump / controlRoof / controlMotionLight in sketch.ino
ACTUATOR_RULES = {
    "fan": {
        "on_above":  30,    # °C — turn fan ON  when temp > this
        "off_below": 28,    # °C — turn fan OFF when temp < this
    },
    "pump": {
        "on_below":  45,    # % soil — turn pump ON  when soil < this
        "off_above": 65,    # % soil — turn pump OFF when soil > this
    },
    "roof": {
        "full_open_above":      34,   # °C — roof fully open (0°) when temp > this
        "half_open_light_min":  75,   # % light — half-open (45°) condition
        "half_open_hour_start": 11,   # hour range for half-open
        "half_open_hour_end":   14,
    },
    "motion_light": {
        "trigger_below_light": 25,   # % light — activates when light < this
        "duration_ms":         1250,  # ms real-time (= 15 000 ms sim-time ÷ 12× speed)
    },
}

# ── Actuator effects on environment ───────────────────────────────────────
# Each entry: {"delta": float, "limit": float}
#   delta — change per 10-second tick while actuator is ON
#   limit — equilibrium; apply_effect() stops the delta once value reaches it
# Mirrors ACTUATOR_EFFECTS in dashboard/model.js — keep in sync.
ACTUATOR_EFFECTS = {
    "fan": {
        "temp": {"delta": -0.4, "limit": 22},   # cools toward outdoor ambient
        "hum":  {"delta": +0.3, "limit": 85},   # evaporative saturation
    },
    "pump": {
        "soil": {"delta": +3.0, "limit": 95},   # field capacity
        "hum":  {"delta": +1.0, "limit": 88},   # irrigation mist ceiling
    },
    "roof": {
        "open": {
            "temp":  {"delta": -0.8, "limit": 24},
            "hum":   {"delta": +0.2, "limit": 80},
        },
        "half": {
            "temp":  {"delta": -0.3, "limit": 26},
            "light": {"delta": -6,   "limit": 10},
        },
        "closed": {
            "temp":  {"delta": +0.2, "limit": 55},
        },
    },
}

# Natural drift per 10-second tick with no actuators active
ENV_DRIFT = {
    "temp":  +0.15,
    "hum":   -0.25,
    "soil":  -0.5,
    "light":  0.0,
}

# Hard simulation bounds
ENV_BOUNDS = {
    "temp":  (5,  60),
    "hum":   (0, 100),
    "soil":  (0, 100),
    "light": (0, 100),
}


def roof_effect_key(angle: int) -> str:
    if angle <= 0:   return "open"
    if angle < 90:   return "half"
    return "closed"


def apply_effect(value: float, effect: dict) -> float:
    delta = effect["delta"]
    limit = effect.get("limit")
    if limit is not None:
        if delta < 0 and value <= limit: return value
        if delta > 0 and value >= limit: return value
    return value + delta


def clamp_env(key: str, value: float) -> float:
    lo, hi = ENV_BOUNDS.get(key, (None, None))
    if lo is None:
        return value
    return max(lo, min(hi, value))


# ── Sensor card color bands (good → green, warn → yellow, else → red) ───────
CARD_RANGES = {
    "temp":  {"good": (20, 30),  "warn": (15, 34)},
    "hum":   {"good": (45, 100), "warn": (35, 100)},
    "soil":  {"good": (50, 100), "warn": (35, 100)},
    "light": {"good": (20, 75),  "warn": (10, 90)},
}


def evaluate_plant(temp: float, soil: float, hum: float) -> str:
    t = PLANT_THRESHOLDS
    if temp > t["DEAD"]["temp_high"]     or temp < t["DEAD"]["temp_low"]     or soil < t["DEAD"]["soil"]:                                   return "DEAD"
    if temp > t["CRITICAL"]["temp_high"] or temp < t["CRITICAL"]["temp_low"] or soil < t["CRITICAL"]["soil"] or hum < t["CRITICAL"]["hum"]: return "CRITICAL"
    if temp > t["DECLINE"]["temp_high"]  or temp < t["DECLINE"]["temp_low"]  or soil < t["DECLINE"]["soil"]  or hum < t["DECLINE"]["hum"]:  return "DECLINE"
    if temp > t["SLOW"]["temp_high"]     or temp < t["SLOW"]["temp_low"]     or soil < t["SLOW"]["soil"]     or hum < t["SLOW"]["hum"]:     return "SLOW"
    return "GOOD"
