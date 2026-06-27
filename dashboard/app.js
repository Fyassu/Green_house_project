// ─── Config ───────────────────────────────────────
const API              = "http://localhost:5000";
const LATEST_INTERVAL  = 10_000;   // ms — khớp với tần suất gửi của ESP32
const HISTORY_INTERVAL = 60_000;   // ms

// ─── Chart.js defaults ────────────────────────────
Chart.defaults.color       = "#8b949e";
Chart.defaults.borderColor = "#30363d";
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";
Chart.defaults.font.size   = 11;

// ─── Plant health (mirrors firmware evaluatePlant) ─
const PLANT_HINTS = {
  GOOD:     "Cây phát triển tốt — môi trường lý tưởng",
  SLOW:     "Cây phát triển chậm — kiểm tra nhiệt độ hoặc độ ẩm",
  DECLINE:  "Cây đang suy yếu — cần can thiệp sớm",
  CRITICAL: "Cây trong tình trạng nguy hiểm!",
  DEAD:     "Cây đã chết hoặc môi trường cực kỳ bất lợi",
};

function evaluatePlant(temp, soil, hum) {
  if (temp > 45 || temp < 5  || soil < 5)              return "DEAD";
  if (temp > 38 || temp < 10 || soil < 20 || hum < 20) return "CRITICAL";
  if (temp > 34 || temp < 15 || soil < 35 || hum < 35) return "DECLINE";
  if (temp > 30 || temp < 20 || soil < 50 || hum < 45) return "SLOW";
  return "GOOD";
}

// ─── Status bar ───────────────────────────────────
function setStatus(state, msg) {
  document.getElementById("status-dot").className   = "status-dot " + state;
  document.getElementById("status-label").textContent = msg;
}

function setLastUpdate(ts) {
  if (!ts) return;
  const d = new Date(ts.replace(" ", "T"));
  document.getElementById("last-update").textContent =
    "Cập nhật: " + d.toLocaleTimeString("vi-VN");
}

// ─── Sensor cards ─────────────────────────────────
function applyCardClass(id, value, goodRange, warnRange) {
  const el = document.getElementById(id);
  el.classList.remove("good", "warn", "danger");
  if (value >= goodRange[0] && value <= goodRange[1]) el.classList.add("good");
  else if (value >= warnRange[0] && value <= warnRange[1]) el.classList.add("warn");
  else el.classList.add("danger");
}

function updateCards(d) {
  document.getElementById("val-temp").textContent  = d.temperature.toFixed(1);
  document.getElementById("val-hum").textContent   = d.humidity.toFixed(1);
  document.getElementById("val-soil").textContent  = d.soil_moisture;
  document.getElementById("val-light").textContent = d.light_level;

  applyCardClass("card-temp",  d.temperature,  [20, 30], [15, 34]);
  applyCardClass("card-hum",   d.humidity,     [45, 100], [35, 100]);
  applyCardClass("card-soil",  d.soil_moisture,[50, 100], [35, 100]);
  applyCardClass("card-light", d.light_level,  [20, 75],  [10, 90]);
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
  setBadge("act-fan",  d.fan_status);
  setBadge("act-pump", d.pump_status);
  setBadge("act-light", d.light_status);

  // Mái: hiển thị góc
  const roofEl = document.getElementById("act-roof");
  roofEl.textContent = d.servo_angle + "°";
  roofEl.className   = "actuator-badge " + (d.servo_angle < 90 ? "on" : "off");

  // Chuyển động: dùng màu alert
  const motionEl = document.getElementById("act-motion");
  motionEl.textContent = d.motion_detected ? "YES" : "no";
  motionEl.className   = d.motion_detected ? "actuator-badge alert" : "actuator-badge off";
}

// ─── Fetch latest ─────────────────────────────────
async function fetchLatest() {
  try {
    const res = await fetch(API + "/api/latest");
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
let chartEnv, chartSoil;

const CHART_OPTS_BASE = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: "index", intersect: false },
  animation: { duration: 400 },
  plugins: {
    legend: {
      position: "top",
      labels: { boxWidth: 12, padding: 16, color: "#8b949e" },
    },
    tooltip: {
      backgroundColor: "#161b22",
      borderColor: "#30363d",
      borderWidth: 1,
      titleColor: "#e6edf3",
      bodyColor: "#8b949e",
    },
  },
  scales: {
    x: {
      grid: { color: "#21262d" },
      ticks: { maxTicksLimit: 10, maxRotation: 0 },
    },
  },
};

function initCharts() {
  chartEnv = new Chart(document.getElementById("chart-env"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Nhiệt độ (°C)",
          data: [],
          borderColor: "#f85149",
          backgroundColor: "rgba(248,81,73,0.07)",
          fill: true,
          tension: 0.35,
          pointRadius: 2,
          yAxisID: "y",
        },
        {
          label: "Độ ẩm (%)",
          data: [],
          borderColor: "#58a6ff",
          backgroundColor: "rgba(88,166,255,0.07)",
          fill: true,
          tension: 0.35,
          pointRadius: 2,
          yAxisID: "y2",
        },
      ],
    },
    options: {
      ...CHART_OPTS_BASE,
      scales: {
        ...CHART_OPTS_BASE.scales,
        y:  { grid: { color: "#21262d" }, title: { display: true, text: "°C", color: "#8b949e" } },
        y2: { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "%", color: "#8b949e" } },
      },
    },
  });

  chartSoil = new Chart(document.getElementById("chart-soil"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Độ ẩm đất (%)",
          data: [],
          borderColor: "#3fb950",
          backgroundColor: "rgba(63,185,80,0.07)",
          fill: true,
          tension: 0.35,
          pointRadius: 2,
        },
        {
          label: "Ánh sáng (%)",
          data: [],
          borderColor: "#e3b341",
          backgroundColor: "rgba(227,179,65,0.07)",
          fill: true,
          tension: 0.35,
          pointRadius: 2,
        },
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
  // API trả về newest-first → đảo lại cho chart (oldest → newest)
  const sorted = [...rows].reverse();
  const labels = sorted.map(r => {
    const d = new Date(r.created_at.replace(" ", "T"));
    return d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  });

  chartEnv.data.labels           = labels;
  chartEnv.data.datasets[0].data = sorted.map(r => r.temperature);
  chartEnv.data.datasets[1].data = sorted.map(r => r.humidity);
  chartEnv.update("none");

  chartSoil.data.labels           = labels;
  chartSoil.data.datasets[0].data = sorted.map(r => r.soil_moisture);
  chartSoil.data.datasets[1].data = sorted.map(r => r.light_level);
  chartSoil.update("none");
}

async function fetchHistory() {
  try {
    const res = await fetch(API + "/api/history");
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
    await fetch(API + "/api/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [device]: value }),
    });
  } catch {
    // silent
  }
}

function setActiveBtn(device, valStr) {
  const group = document.querySelector(`.btn-group[data-device="${device}"]`);
  if (!group) return;
  group.querySelectorAll(".btn-ctrl").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.val === valStr);
  });
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
  initCharts();
  bindControls();
  await loadCommandState();
  await fetchLatest();
  await fetchHistory();
  setInterval(fetchLatest,  LATEST_INTERVAL);
  setInterval(fetchHistory, HISTORY_INTERVAL);
}

init();
