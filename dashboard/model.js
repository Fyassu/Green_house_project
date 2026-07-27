// ─── Greenhouse Business Rules ────────────────────────────────────────────────
// Single source of truth for all thresholds. Mirrors firmware (sketch.ino)
// and backend (backend/model.py). Edit here → changes propagate to charts,
// card colors, and plant-health evaluation automatically.

// ─── Plant health ──────────────────────────────────────────────────────────
const PLANT_HINTS = {
  GOOD:     "Cây phát triển tốt — môi trường lý tưởng",
  SLOW:     "Cây phát triển chậm — kiểm tra nhiệt độ hoặc độ ẩm",
  DECLINE:  "Cây đang suy yếu — cần can thiệp sớm",
  CRITICAL: "Cây trong tình trạng nguy hiểm!",
  DEAD:     "Cây đã chết hoặc môi trường cực kỳ bất lợi",
};

// Mirrors evaluatePlant() in sketch.ino — keep in sync
const PLANT_THRESHOLDS = {
  DEAD:     { tempHigh: 45, tempLow: 5,  soil: 5 },
  CRITICAL: { tempHigh: 38, tempLow: 10, soil: 20, hum: 20 },
  DECLINE:  { tempHigh: 34, tempLow: 15, soil: 35, hum: 35 },
  SLOW:     { tempHigh: 30, tempLow: 20, soil: 50, hum: 45 },
};

function evaluatePlant(temp, soil, hum) {
  const t = PLANT_THRESHOLDS;
  if (temp > t.DEAD.tempHigh     || temp < t.DEAD.tempLow     || soil < t.DEAD.soil)                             return "DEAD";
  if (temp > t.CRITICAL.tempHigh || temp < t.CRITICAL.tempLow || soil < t.CRITICAL.soil || hum < t.CRITICAL.hum) return "CRITICAL";
  if (temp > t.DECLINE.tempHigh  || temp < t.DECLINE.tempLow  || soil < t.DECLINE.soil  || hum < t.DECLINE.hum)  return "DECLINE";
  if (temp > t.SLOW.tempHigh     || temp < t.SLOW.tempLow     || soil < t.SLOW.soil     || hum < t.SLOW.hum)     return "SLOW";
  return "GOOD";
}

// ─── Actuator control rules ────────────────────────────────────────────────
// Mirrors controlFan / controlPump / controlShade / controlMotionLight in sketch.ino
const ACTUATOR_RULES = {
  fan: {
    onAbove:  30,   // °C — turn fan ON  when temp > this
    offBelow: 28,   // °C — turn fan OFF when temp < this
  },
  pump: {
    onBelow:  45,   // % soil — turn pump ON  when soil < this
    offAbove: 65,   // % soil — turn pump OFF when soil > this
  },
  shade: {
    fullDeployTempAbove: 34,
    fullDeployLightMin:  85,
    halfDeployLightMin:  65,
    activeHourStart:     10,
    activeHourEnd:       16,
  },
  motionLight: {
    triggerBelowLight: 25,    // % light — motion light activates when light < this
    durationMs:        1250,  // ms real-time (= 15 000 ms sim-time ÷ 12× speed)
  },
};

// ─── Actuator effects on environment ──────────────────────────────────────
// Each effect entry: { delta, limit }
//   delta — change applied per 10-second tick while actuator is ON
//   limit — equilibrium point; delta stops being applied once value reaches it
//           (direction inferred from sign of delta)
//
// Calibration basis:
//   Fan    : greenhouse fan ≈ 2–3 °C/min → 0.4 °C/tick; equilibrium at ~22 °C (ambient)
//   Pump   : drip irrigation raises soil ~3 %/tick; saturates at field capacity ~95 %
//   Shade  : external screen reduces solar gain; it does not ventilate the shell
const ACTUATOR_EFFECTS = {
  fan: {
    temp: { delta: -0.4, limit: 22 },  // cools toward outdoor ambient (22 °C)
    hum:  { delta: +0.3, limit: 85 },  // evaporative effect saturates at 85 %
  },
  pump: {
    soil: { delta: +3.0, limit: 95 },  // field capacity — soil can't absorb beyond 95 %
    hum:  { delta: +1.0, limit: 88 },  // irrigation mist tops out at 88 % RH
  },
  shade: {
    retracted: {},                                // 0° — no shade
    half: {                                       // 45° — half deployed
      temp:  { delta: -0.15, limit: 24 },
      light: { delta: -25,   limit: 0 },
    },
    full: {                                       // 90° — fully deployed
      temp:  { delta: -0.30, limit: 24 },
      light: { delta: -55,   limit: 0 },
    },
  },
};

// Map servo angle → shade state key used in ACTUATOR_EFFECTS.shade
function shadeEffectKey(angle) {
  if (angle <= 0)  return "retracted";
  if (angle < 90)  return "half";
  return "full";
}

// Apply one actuator effect step.
// Stops at `limit` so the actuator can't push a value past its equilibrium.
function applyEffect(value, effect) {
  const { delta, limit } = effect;
  if (limit !== undefined) {
    if (delta < 0 && value <= limit) return value;
    if (delta > 0 && value >= limit) return value;
  }
  return value + delta;
}

// ─── Natural environmental drift (no actuators active) ─────────────────────
// Baseline change per 10-second tick from solar radiation, plant transpiration,
// and natural evapotranspiration — independent of actuator state.
const ENV_DRIFT = {
  temp:  +0.15,  // °C — greenhouse slowly heats from solar gain
  hum:   -0.25,  // % — humidity drops as warm air dries
  soil:  -0.5,   // % — soil dries via evapotranspiration
  light:  0,     // determined by time-of-day model, not drift
};

// Returns the expected net delta per 10-second tick for a given sensor key
// ("temp" | "hum" | "soil" | "light"), given the current actuator states from
// a live sensor reading. Limits are intentionally ignored here — direction only.
// Used by the dashboard to show trend arrows on sensor cards.
function computeTrendDelta(sensor, reading) {
  let delta = ENV_DRIFT[sensor] ?? 0;
  if (reading.fan_status)  delta += ACTUATOR_EFFECTS.fan[sensor]?.delta  ?? 0;
  if (reading.pump_status) delta += ACTUATOR_EFFECTS.pump[sensor]?.delta ?? 0;
  const shadeKey = shadeEffectKey(reading.servo_angle ?? 0);
  delta += ACTUATOR_EFFECTS.shade[shadeKey]?.[sensor]?.delta ?? 0;
  return delta;
}

// ─── Simulation bounds — clamp all computed values inside these limits ──────
const ENV_BOUNDS = {
  temp:  { min: 5,  max: 60  },
  hum:   { min: 0,  max: 100 },
  soil:  { min: 0,  max: 100 },
  light: { min: 0,  max: 100 },
};

// Clamp helper used by the digital twin simulation loop
function clampEnv(key, value) {
  const b = ENV_BOUNDS[key];
  return b ? Math.min(b.max, Math.max(b.min, value)) : value;
}

// ─── Sensor card color ranges ──────────────────────────────────────────────
// good  → green border   (ideal range)
// warn  → yellow border  (acceptable but suboptimal)
// else  → danger/red     (outside warn range)
const CARD_RANGES = {
  temp:  { good: [20, 30],  warn: [15, 34]  },
  hum:   { good: [45, 100], warn: [35, 100] },
  soil:  { good: [50, 100], warn: [35, 100] },
  light: { good: [20, 75],  warn: [10, 90]  },
};
