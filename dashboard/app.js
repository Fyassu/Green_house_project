// ─── Config ───────────────────────────────────────
const API              = "http://localhost:5000";
const SIM_SPEED        = 12;                                      // 1 simulated min = 5 real s
const LATEST_INTERVAL  = Math.round(10_000 / SIM_SPEED);         // 833 ms
const HISTORY_INTERVAL = Math.round(30_000 / SIM_SPEED);         // 2500 ms
const SPARK_MAX        = 20;

// ─── Auth helpers ─────────────────────────────────
function getAuthHeaders() {
  const token = localStorage.getItem("gh_token");
  return {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${token}`,
  };
}

function handle401() {
  localStorage.removeItem("gh_token");
  localStorage.removeItem("gh_username");
  window.location.href = "login.html";
}

// ─── Chart.js plugins ─────────────────────────────
const crosshairPlugin = {
  id: "crosshair",
  afterDraw(chart) {
    if (!chart.tooltip?._active?.length) return;
    const ctx  = chart.ctx;
    const x    = chart.tooltip._active[0].element.x;
    const { top, bottom } = chart.chartArea;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.lineWidth    = 1;
    ctx.strokeStyle  = "rgba(139,148,158,0.35)";
    ctx.stroke();
    ctx.restore();
  },
};

const lastPointLabelPlugin = {
  id: "lastPointLabel",
  afterDatasetsDraw(chart) {
    const ctx = chart.ctx;
    chart.data.datasets.forEach((dataset, i) => {
      if (dataset.borderDash?.length) return;        // skip threshold lines
      const meta = chart.getDatasetMeta(i);
      if (!meta.visible || !meta.data.length) return;
      const lastEl  = meta.data[meta.data.length - 1];
      const lastVal = dataset.data[dataset.data.length - 1];
      if (lastVal == null) return;
      ctx.save();
      ctx.fillStyle    = dataset.borderColor;
      ctx.font         = 'bold 10px "Segoe UI", system-ui, sans-serif';
      ctx.textAlign    = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(
        typeof lastVal === "number" ? lastVal.toFixed(1) : String(lastVal),
        lastEl.x + 5,
        lastEl.y
      );
      ctx.restore();
    });
  },
};

Chart.register(crosshairPlugin, lastPointLabelPlugin);

// ─── Chart.js defaults ────────────────────────────
Chart.defaults.color       = "#8b949e";
Chart.defaults.borderColor = "#30363d";
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";
Chart.defaults.font.size   = 11;

// ─── Demo Simulation Engine ───────────────────────
let simState    = null;   // null = real-data mode; object = simulation active
let simInterval = null;
let simHistory  = [];

const SIM_PRESETS = {
  hot:   { temp: 38,   hum: 35, soil: 25, light: 90 },  // fan + roof open + pump
  cold:  { temp: 12,   hum: 80, soil: 70, light: 20 },  // cold damp, roof closed
  dry:   { temp: 28,   hum: 40, soil: 20, light: 60 },  // pump activates
  ideal: { temp: 25,   hum: 60, soil: 55, light: 50 },  // all green
};

function simTick() {
  if (!simState) return;
  const s = simState;

  // 1. Natural drift — mirrors ENV_DRIFT in model.js
  s.temp  += ENV_DRIFT.temp;
  s.hum   += ENV_DRIFT.hum;
  s.soil  += ENV_DRIFT.soil;

  // 2. Actuator control — manual override (fanCmd/pumpCmd/roofCmd) beats auto-logic
  if (s.fanCmd !== null) {
    s.fan = s.fanCmd;
  } else {
    if (!s.fan  && s.temp > ACTUATOR_RULES.fan.onAbove)   s.fan  = true;
    if ( s.fan  && s.temp < ACTUATOR_RULES.fan.offBelow)  s.fan  = false;
  }
  if (s.pumpCmd !== null) {
    s.pump = s.pumpCmd;
  } else {
    if (!s.pump && s.soil < ACTUATOR_RULES.pump.onBelow)  s.pump = true;
    if ( s.pump && s.soil > ACTUATOR_RULES.pump.offAbove) s.pump = false;
  }
  if (s.roofCmd !== null) {
    s.roof = s.roofCmd;
  } else {
    const hr = new Date().getHours();
    if (s.temp > ACTUATOR_RULES.roof.fullOpenAbove) {
      s.roof = 0;
    } else if (hr >= ACTUATOR_RULES.roof.halfOpenHourStart &&
               hr <= ACTUATOR_RULES.roof.halfOpenHourEnd   &&
               s.light > ACTUATOR_RULES.roof.halfOpenLightMin) {
      s.roof = 45;
    } else {
      s.roof = 90;
    }
  }

  // 3. Actuator effects — mirrors ACTUATOR_EFFECTS in model.js
  if (s.fan) {
    s.temp = applyEffect(s.temp, ACTUATOR_EFFECTS.fan.temp);
    s.hum  = applyEffect(s.hum,  ACTUATOR_EFFECTS.fan.hum);
  }
  if (s.pump) {
    s.soil = applyEffect(s.soil, ACTUATOR_EFFECTS.pump.soil);
    s.hum  = applyEffect(s.hum,  ACTUATOR_EFFECTS.pump.hum);
  }
  const rk = roofEffectKey(s.roof);
  const re = ACTUATOR_EFFECTS.roof[rk];
  if (re.temp)  s.temp  = applyEffect(s.temp,  re.temp);
  if (re.hum)   s.hum   = applyEffect(s.hum,   re.hum);
  if (re.light) s.light = applyEffect(s.light, re.light);

  // 4. Clamp to physical bounds
  s.temp  = clampEnv("temp",  s.temp);
  s.hum   = clampEnv("hum",   s.hum);
  s.soil  = clampEnv("soil",  s.soil);
  s.light = clampEnv("light", s.light);

  // 5. Build display record
  const d = {
    temperature:     parseFloat(s.temp.toFixed(1)),
    humidity:        parseFloat(s.hum.toFixed(1)),
    soil_moisture:   Math.round(s.soil),
    light_level:     Math.round(s.light),
    fan_status:      s.fan,
    pump_status:     s.pump,
    servo_angle:     s.roof,
    light_status:    false,
    motion_detected: false,
    created_at:      new Date().toISOString(),
    plant_health:    evaluatePlant(s.temp, s.soil, s.hum),
  };

  // 6. Push to all dashboard components
  setStatus("sim", "DEMO");
  setLastUpdate(d.created_at);
  updateCards(d);
  updatePlantHealth(d);
  updateActuators(d);

  // 7. Accumulate chart history
  simHistory.push(d);
  if (simHistory.length > 100) simHistory.shift();
  if (simHistory.length > 1)   updateCharts(simHistory);
}

function startSim() {
  const temp  = parseFloat(document.getElementById("sim-temp").value);
  const hum   = parseFloat(document.getElementById("sim-hum").value);
  const soil  = parseFloat(document.getElementById("sim-soil").value);
  const light = parseFloat(document.getElementById("sim-light").value);

  simState   = { temp, hum, soil, light, fan: false, pump: false, roof: 90,
                 fanCmd: null, pumpCmd: null, roofCmd: null };
  simHistory = [];

  document.getElementById("btn-sim-start").disabled = true;
  document.getElementById("btn-sim-stop").disabled  = false;
  document.getElementById("btn-sim-start").classList.add("running");
  document.getElementById("demo-panel").classList.add("running");

  if (simInterval) clearInterval(simInterval);
  simInterval = setInterval(simTick, LATEST_INTERVAL);
  simTick();
}

function stopSim() {
  simState = null;
  if (simInterval) { clearInterval(simInterval); simInterval = null; }
  simHistory = [];

  document.getElementById("btn-sim-start").disabled = false;
  document.getElementById("btn-sim-stop").disabled  = true;
  document.getElementById("btn-sim-start").classList.remove("running");
  document.getElementById("demo-panel").classList.remove("running");
  document.querySelectorAll(".btn-preset").forEach(b => b.classList.remove("active"));

  setStatus("live", "LIVE");
  fetchLatest();
  fetchHistory();
}

function bindSimPanel() {
  document.getElementById("btn-sim-start").addEventListener("click", startSim);
  document.getElementById("btn-sim-stop").addEventListener("click",  stopSim);

  // Slider → live value display
  [["sim-temp",  "sim-temp-val",  v => parseFloat(v).toFixed(1) + "°C"],
   ["sim-hum",   "sim-hum-val",   v => Math.round(v) + "%"],
   ["sim-soil",  "sim-soil-val",  v => Math.round(v) + "%"],
   ["sim-light", "sim-light-val", v => Math.round(v) + "%"]].forEach(([id, valId, fmt]) => {
    document.getElementById(id).addEventListener("input", function() {
      document.getElementById(valId).textContent = fmt(this.value);
    });
  });

  // Preset buttons → fill sliders + auto-start
  document.querySelectorAll(".btn-preset").forEach(btn => {
    btn.addEventListener("click", () => {
      const p = SIM_PRESETS[btn.dataset.preset];
      if (!p) return;
      document.getElementById("sim-temp").value         = p.temp;
      document.getElementById("sim-hum").value          = p.hum;
      document.getElementById("sim-soil").value         = p.soil;
      document.getElementById("sim-light").value        = p.light;
      document.getElementById("sim-temp-val").textContent  = p.temp.toFixed(1) + "°C";
      document.getElementById("sim-hum-val").textContent   = p.hum + "%";
      document.getElementById("sim-soil-val").textContent  = p.soil + "%";
      document.getElementById("sim-light-val").textContent = p.light + "%";
      document.querySelectorAll(".btn-preset").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      startSim();
    });
  });
}

// ─── Status bar ───────────────────────────────────
function setStatus(state, msg) {
  document.getElementById("status-dot").className    = "status-dot " + state;
  document.getElementById("status-label").textContent = msg;
}

function setLastUpdate(ts) {
  if (!ts) return;
  const d = new Date(ts.replace(" ", "T"));
  document.getElementById("last-update").textContent =
    "Cập nhật: " + d.toLocaleTimeString("vi-VN");
}

// ─── Sparkline buffers ────────────────────────────
const sparkBuf = { temp: [], hum: [], soil: [], light: [] };
let sparkTemp, sparkHum, sparkSoil, sparkLight;

function pushSpark(key, value) {
  if (sparkBuf[key].length >= SPARK_MAX) sparkBuf[key].shift();
  sparkBuf[key].push(value);
}

function initSparklines() {
  const cfg = (color) => ({
    type: "line",
    data: { labels: [], datasets: [{ data: [], borderColor: color, borderWidth: 1.5, pointRadius: 0, fill: false, tension: 0.3 }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 0 },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales:  { x: { display: false }, y: { display: false } },
    },
  });
  sparkTemp  = new Chart(document.getElementById("spark-temp"),  cfg("#f85149"));
  sparkHum   = new Chart(document.getElementById("spark-hum"),   cfg("#58a6ff"));
  sparkSoil  = new Chart(document.getElementById("spark-soil"),  cfg("#3fb950"));
  sparkLight = new Chart(document.getElementById("spark-light"), cfg("#e3b341"));
}

function updateSparklines(d) {
  pushSpark("temp",  d.temperature);
  pushSpark("hum",   d.humidity);
  pushSpark("soil",  d.soil_moisture);
  pushSpark("light", d.light_level);
  const fakeLabels = sparkBuf.temp.map((_, i) => i);
  [[sparkTemp, "temp"], [sparkHum, "hum"], [sparkSoil, "soil"], [sparkLight, "light"]].forEach(([chart, key]) => {
    chart.data.labels           = fakeLabels;
    chart.data.datasets[0].data = [...sparkBuf[key]];
    chart.update("none");
  });
}

// ─── Sensor cards ─────────────────────────────────
function applyCardClass(id, value, goodRange, warnRange) {
  const el = document.getElementById(id);
  el.classList.remove("good", "warn", "danger");
  if (value >= goodRange[0] && value <= goodRange[1])      el.classList.add("good");
  else if (value >= warnRange[0] && value <= warnRange[1]) el.classList.add("warn");
  else                                                       el.classList.add("danger");
}

function updateCards(d) {
  document.getElementById("val-temp").textContent  = d.temperature.toFixed(1);
  document.getElementById("val-hum").textContent   = d.humidity.toFixed(1);
  document.getElementById("val-soil").textContent  = d.soil_moisture;
  document.getElementById("val-light").textContent = d.light_level;

  applyCardClass("card-temp",  d.temperature,  CARD_RANGES.temp.good,  CARD_RANGES.temp.warn);
  applyCardClass("card-hum",   d.humidity,     CARD_RANGES.hum.good,   CARD_RANGES.hum.warn);
  applyCardClass("card-soil",  d.soil_moisture,CARD_RANGES.soil.good,  CARD_RANGES.soil.warn);
  applyCardClass("card-light", d.light_level,  CARD_RANGES.light.good, CARD_RANGES.light.warn);

  // Trend arrows — driven by ACTUATOR_EFFECTS + ENV_DRIFT from model.js
  ["temp", "hum", "soil", "light"].forEach(key => {
    const el = document.getElementById("trend-" + key);
    if (!el) return;
    const delta = computeTrendDelta(key, d);
    if      (delta >  0.05) { el.textContent = "↑"; el.className = "trend-arrow up"; }
    else if (delta < -0.05) { el.textContent = "↓"; el.className = "trend-arrow down"; }
    else                    { el.textContent = "→"; el.className = "trend-arrow stable"; }
  });

  updateSparklines(d);
}

// ─── Plant health ─────────────────────────────────
function updatePlantHealth(d) {
  const state = evaluatePlant(d.temperature, d.soil_moisture, d.humidity);
  const badge = document.getElementById("plant-badge");
  badge.textContent = state;
  badge.className   = "plant-badge " + state;
  document.getElementById("plant-hint").textContent = PLANT_HINTS[state] ?? "";
}

// ─── Actuators ────────────────────────────────────
function setBadge(id, on, label) {
  const el = document.getElementById(id);
  el.textContent = label ?? (on ? "ON" : "OFF");
  el.className   = "actuator-badge " + (on ? "on" : "off");
}

function updateActuators(d) {
  setBadge("act-fan",   d.fan_status);
  setBadge("act-pump",  d.pump_status);
  setBadge("act-light", d.light_status);

  const roofEl = document.getElementById("act-roof");
  roofEl.textContent = d.servo_angle + "°";
  roofEl.className   = "actuator-badge " + (d.servo_angle < 90 ? "on" : "off");

  const motionEl = document.getElementById("act-motion");
  motionEl.textContent = d.motion_detected ? "YES" : "no";
  motionEl.className   = d.motion_detected ? "actuator-badge alert" : "actuator-badge off";
}

// ─── Fetch latest ─────────────────────────────────
async function fetchLatest() {
  if (simState) return;   // simulation owns the display
  try {
    const res = await fetch(API + "/api/latest", { headers: getAuthHeaders() });
    if (res.status === 401) { handle401(); return; }
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();
    if (d.message === "No data") {
      setStatus("error", "Chưa có dữ liệu — chạy Wokwi để gửi data");
      return;
    }
    setStatus("live", "LIVE");
    setLastUpdate(d.created_at);
    updateCards(d);
    updatePlantHealth(d);
    updateActuators(d);
  } catch {
    setStatus("error", "Mất kết nối Flask — kiểm tra backend");
  }
}

// ─── Charts ───────────────────────────────────────
let chartTemp, chartHum, chartSoil;

const CHART_OPTS_BASE = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: "index", intersect: false },
  animation:   { duration: 400 },
  plugins: {
    legend: {
      position: "top",
      labels: { boxWidth: 12, padding: 16, color: "#8b949e" },
    },
    tooltip: {
      backgroundColor: "#161b22",
      borderColor:     "#30363d",
      borderWidth:     1,
      titleColor:      "#e6edf3",
      bodyColor:       "#8b949e",
    },
  },
  scales: {
    x: {
      grid:  { color: "#21262d" },
      ticks: { maxTicksLimit: 10, maxRotation: 0 },
    },
  },
};

// Helper: threshold dataset
function thresholdDS(label, color) {
  return {
    label,
    data:        [],
    borderColor: color,
    borderDash:  [5, 4],
    borderWidth: 1,
    pointRadius: 0,
    fill:        false,
    tension:     0,
  };
}

function initCharts() {
  // Temperature — single axis, 2 threshold lines
  chartTemp = new Chart(document.getElementById("chart-temp"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Nhiệt độ (°C)",
          data: [], borderColor: "#f85149",
          backgroundColor: "rgba(248,81,73,0.07)",
          fill: true, tension: 0.35, pointRadius: 2, borderWidth: 2,
        },
        thresholdDS(`${ACTUATOR_RULES.fan.onAbove}°C — bật quạt`,       "rgba(227,179,65,0.75)"),
        thresholdDS(`${ACTUATOR_RULES.roof.fullOpenAbove}°C — mở mái`, "rgba(248,81,73,0.55)"),
      ],
    },
    options: {
      ...CHART_OPTS_BASE,
      scales: {
        ...CHART_OPTS_BASE.scales,
        y: { grid: { color: "#21262d" }, title: { display: true, text: "°C", color: "#8b949e" } },
      },
    },
  });

  // Air humidity — single axis
  chartHum = new Chart(document.getElementById("chart-hum"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Độ ẩm KK (%)",
          data: [], borderColor: "#58a6ff",
          backgroundColor: "rgba(88,166,255,0.07)",
          fill: true, tension: 0.35, pointRadius: 2, borderWidth: 2,
        },
      ],
    },
    options: {
      ...CHART_OPTS_BASE,
      scales: {
        ...CHART_OPTS_BASE.scales,
        y: { grid: { color: "#21262d" }, min: 0, max: 100, title: { display: true, text: "%", color: "#8b949e" } },
      },
    },
  });

  // Soil + Light — same 0-100 axis, 2 threshold lines for pump
  chartSoil = new Chart(document.getElementById("chart-soil"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Độ ẩm đất (%)",
          data: [], borderColor: "#3fb950",
          backgroundColor: "rgba(63,185,80,0.07)",
          fill: true, tension: 0.35, pointRadius: 2, borderWidth: 2,
        },
        {
          label: "Ánh sáng (%)",
          data: [], borderColor: "#e3b341",
          backgroundColor: "rgba(227,179,65,0.07)",
          fill: true, tension: 0.35, pointRadius: 2, borderWidth: 2,
        },
        thresholdDS(`${ACTUATOR_RULES.pump.onBelow}% — bật bơm`,  "rgba(63,185,80,0.65)"),
        thresholdDS(`${ACTUATOR_RULES.pump.offAbove}% — tắt bơm`, "rgba(63,185,80,0.40)"),
      ],
    },
    options: {
      ...CHART_OPTS_BASE,
      scales: {
        ...CHART_OPTS_BASE.scales,
        y: { grid: { color: "#21262d" }, min: 0, max: 100 },
      },
    },
  });
}

function updateCharts(rows) {
  const sorted = [...rows].reverse();
  const n      = sorted.length;
  const labels = sorted.map(r => {
    const d = new Date(r.created_at.replace(" ", "T"));
    return d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  });

  chartTemp.data.labels           = labels;
  chartTemp.data.datasets[0].data = sorted.map(r => r.temperature);
  chartTemp.data.datasets[1].data = Array(n).fill(ACTUATOR_RULES.fan.onAbove);
  chartTemp.data.datasets[2].data = Array(n).fill(ACTUATOR_RULES.roof.fullOpenAbove);
  chartTemp.update("none");

  chartHum.data.labels           = labels;
  chartHum.data.datasets[0].data = sorted.map(r => r.humidity);
  chartHum.update("none");

  chartSoil.data.labels           = labels;
  chartSoil.data.datasets[0].data = sorted.map(r => r.soil_moisture);
  chartSoil.data.datasets[1].data = sorted.map(r => r.light_level);
  chartSoil.data.datasets[2].data = Array(n).fill(ACTUATOR_RULES.pump.onBelow);
  chartSoil.data.datasets[3].data = Array(n).fill(ACTUATOR_RULES.pump.offAbove);
  chartSoil.update("none");
}

async function fetchHistory() {
  if (simState) return;   // simulation builds its own chart history
  try {
    const res = await fetch(API + "/api/history", { headers: getAuthHeaders() });
    if (res.status === 401) { handle401(); return; }
    if (!res.ok) return;
    const rows = await res.json();
    if (rows.length) updateCharts(rows);
  } catch {
    // silent — giữ nguyên chart cũ
  }
}

// ─── Control ──────────────────────────────────────
async function sendCommand(device, value) {
  try {
    const res = await fetch(API + "/api/control", {
      method:  "POST",
      headers: getAuthHeaders(),
      body:    JSON.stringify({ [device]: value }),
    });
    if (res.status === 401) handle401();
  } catch {
    // silent
  }
  // Apply override to simulation engine so it takes effect immediately
  if (simState) {
    if      (device === "fan")   simState.fanCmd  = value;
    else if (device === "pump")  simState.pumpCmd = value;
    else if (device === "servo") simState.roofCmd = value;
  }
}

function setActiveBtn(device, valStr) {
  const group = document.querySelector(`.btn-group[data-device="${device}"]`);
  if (!group) return;
  group.querySelectorAll(".btn-ctrl").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.val === valStr);
  });
  // Sync actuator badge immediately so both panels stay consistent
  if (valStr === "null") return;   // AUTO — badge updates on next poll
  if (device === "fan")  { setBadge("act-fan",  valStr === "true"); return; }
  if (device === "pump") { setBadge("act-pump", valStr === "true"); return; }
  if (device === "servo") {
    const angle  = parseInt(valStr, 10);
    const roofEl = document.getElementById("act-roof");
    if (!roofEl) return;
    roofEl.textContent = angle + "°";
    roofEl.className   = "actuator-badge " + (angle < 90 ? "on" : "off");
  }
}

async function loadCommandState() {
  try {
    const res  = await fetch(API + "/api/commands");
    const cmds = await res.json();
    ["fan", "pump"].forEach(dev => {
      const v = cmds[dev];
      setActiveBtn(dev, v === null ? "null" : String(v));
    });
    setActiveBtn("servo", cmds.servo === null ? "null" : String(cmds.servo));
  } catch {
    // Flask chưa sẵn — bỏ qua
  }
}

function bindControls() {
  document.querySelectorAll(".btn-group").forEach(group => {
    const device = group.dataset.device;
    group.querySelectorAll(".btn-ctrl").forEach(btn => {
      btn.addEventListener("click", async () => {
        const valStr = btn.dataset.val;
        let value;
        if      (valStr === "null")  value = null;
        else if (valStr === "true")  value = true;
        else if (valStr === "false") value = false;
        else                         value = parseInt(valStr, 10);
        await sendCommand(device, value);
        setActiveBtn(device, valStr);
      });
    });
  });
}

// ─── Init ─────────────────────────────────────────
async function init() {
  const token    = localStorage.getItem("gh_token");
  const username = localStorage.getItem("gh_username");

  if (!token) {
    window.location.href = "login.html";
    return;
  }

  const userInfoEl = document.getElementById("user-info");
  if (userInfoEl) {
    document.getElementById("username-display").textContent = username || "";
    userInfoEl.style.display = "flex";
    document.getElementById("logout-btn").addEventListener("click", () => {
      localStorage.removeItem("gh_token");
      localStorage.removeItem("gh_username");
      window.location.href = "login.html";
    });
  }

  const loader = document.getElementById("loader-overlay");
  if (loader) loader.style.display = "none";

  initSparklines();
  initCharts();
  bindControls();
  bindSimPanel();
  await loadCommandState();
  await fetchLatest();
  await fetchHistory();
  setInterval(fetchLatest,  LATEST_INTERVAL);
  setInterval(fetchHistory, HISTORY_INTERVAL);
}

init();
