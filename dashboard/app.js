// ─── Config ───────────────────────────────────────
const API              = "http://localhost:5000";
const SIM_SPEED        = 12;                                      // 1 simulated min = 5 real s
const LATEST_INTERVAL  = Math.round(10_000 / SIM_SPEED);         // 833 ms
const HISTORY_INTERVAL = Math.round(30_000 / SIM_SPEED);         // 2500 ms
const SPARK_MAX        = 20;
const PREDICT_HORIZON_MINUTES = 30;   // dự báo 30 phút MÔ PHỎNG tới (Giai đoạn 2 - Analytics Layer)

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

// ─── Analytics/Prediction Layer state ──────────────
// Số điểm dữ liệu THẬT hiện có trong labels/dataset của 3 chart chính (không
// tính đoạn nhãn/giá trị dự báo đã nối thêm bởi updateForecast()). Dùng để
// SSE (appendToCharts) biết cắt bỏ đúng đoạn dự báo cũ trước khi thêm điểm
// thật mới — tránh chèn nhãn "hiện tại" vào sau các nhãn "tương lai" đã vẽ.
let chartHistoryLen = 0;

function stripForecastTail() {
  [[chartTemp, 3], [chartHum, 1], [chartSoil, 4]].forEach(([ch, idx]) => {
    if (!ch) return;
    ch.data.labels           = ch.data.labels.slice(0, chartHistoryLen);
    ch.data.datasets[idx].data = ch.data.datasets[idx].data.slice(0, chartHistoryLen);
  });
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

window.pendingCmds = { fan: 0, pump: 0, servo: 0, light: 0, security: 0 };

function updateActuators(d) {
  const now = Date.now();
  if (now - pendingCmds.fan > 25000)   setBadge("act-fan",   d.fan_status);
  if (now - pendingCmds.pump > 25000)  setBadge("act-pump",  d.pump_status);
  if (now - pendingCmds.light > 25000) setBadge("act-light", d.light_status);

  if (now - pendingCmds.servo > 25000) {
    const roofEl = document.getElementById("act-roof");
    if (roofEl) {
      roofEl.textContent = d.servo_angle + "°";
      roofEl.className   = "actuator-badge " + (d.servo_angle < 90 ? "on" : "off");
    }
  }

  const motionEl = document.getElementById("act-motion");
  if (motionEl) {
    motionEl.textContent = d.motion_detected ? "YES" : "no";
    motionEl.className   = d.motion_detected ? "actuator-badge alert" : "actuator-badge off";
  }

  const secEl = document.getElementById("act-security");
  if (secEl && now - pendingCmds.security > 25000) {
    if (d.security_mode && d.motion_detected) {
      secEl.innerHTML = '<i class="fa-solid fa-triangle-exclamation fa-beat"></i> BÁO ĐỘNG';
      secEl.className = "actuator-badge alert";
    } else {
      secEl.textContent = d.security_mode ? "ON" : "OFF";
      secEl.className   = "actuator-badge " + (d.security_mode ? "on" : "off");
    }
  }
}

// ─── Server-Sent Events (real-time Wokwi sync) ────
let sseSource = null;
let sseActive = false;

function appendToCharts(row) {
  // Cắt bỏ đoạn dự báo (nếu có) trước khi thêm điểm thật — nếu không, nhãn
  // "hiện tại" sẽ bị chèn vào sau các nhãn "tương lai" đã vẽ bởi updateForecast().
  stripForecastTail();

  const label = new Date((row.created_at || "").replace(" ", "T"))
    .toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const MAX = 100;
  [chartTemp, chartHum, chartSoil].forEach(ch => {
    if (ch.data.labels.length >= MAX) {
      ch.data.labels.shift();
      ch.data.datasets.forEach(ds => ds.data.shift());
      chartHistoryLen--;
    }
  });
  chartTemp.data.labels.push(label);
  chartTemp.data.datasets[0].data.push(row.temperature);
  chartTemp.data.datasets[1].data.push(ACTUATOR_RULES.fan.onAbove);
  chartTemp.data.datasets[2].data.push(ACTUATOR_RULES.roof.fullOpenAbove);
  chartTemp.data.datasets[3].data.push(null);   // giữ đồng bộ độ dài; forecast sẽ được vẽ lại bởi fetchPredict()
  chartTemp.update("none");

  chartHum.data.labels.push(label);
  chartHum.data.datasets[0].data.push(row.humidity);
  chartHum.data.datasets[1].data.push(null);
  chartHum.update("none");

  chartSoil.data.labels.push(label);
  chartSoil.data.datasets[0].data.push(row.soil_moisture);
  chartSoil.data.datasets[1].data.push(row.light_level);
  chartSoil.data.datasets[2].data.push(ACTUATOR_RULES.pump.onBelow);
  chartSoil.data.datasets[3].data.push(ACTUATOR_RULES.pump.offAbove);
  chartSoil.data.datasets[4].data.push(null);
  chartSoil.update("none");

  chartHistoryLen++;
}

function connectSSE() {
  if (sseSource) { sseSource.close(); sseSource = null; }
  sseSource = new EventSource(API + "/api/stream");

  sseSource.onopen = () => { sseActive = true; };

  sseSource.onmessage = (e) => {
    if (simState) return;   // đang mô phỏng (simulation.js) — bỏ qua dữ liệu SSE thật nếu Wokwi vẫn đang chạy song song
    let d;
    try { d = JSON.parse(e.data); } catch { return; }
    if (!d || d.temperature == null) return;
    sseActive = true;
    setStatus("live", "LIVE");
    setLastUpdate(d.created_at);
    updateCards(d);
    updatePlantHealth(d);
    updateActuators(d);
    appendToCharts(d);
    if (typeof window.update3DTwin === "function") window.update3DTwin(d);
  };

  sseSource.onerror = () => {
    sseActive = false;
    sseSource.close();
    sseSource = null;
    setTimeout(connectSSE, 5000);
  };
}

// ─── Fetch latest ─────────────────────────────────
async function fetchLatest() {
  if (simState) return;   // đang ở chế độ mô phỏng (simulation.js) — không ghi đè bằng dữ liệu thật
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
    if (typeof window.update3DTwin === "function") window.update3DTwin(d);
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

// Helper: forecast dataset — nét đứt cùng màu với đường dữ liệu thật, nối tiếp
// ngay sau điểm hiện tại (xem updateForecast()). Giai đoạn 2 - Analytics Layer.
function forecastDS(label, color) {
  return {
    label,
    data:        [],
    borderColor: color,
    borderDash:  [6, 3],
    borderWidth: 2,
    pointRadius: 0,
    fill:        false,
    tension:     0.3,
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
        forecastDS("Dự báo nhiệt độ", "#f85149"),
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
        forecastDS("Dự báo độ ẩm KK", "#58a6ff"),
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
        forecastDS("Dự báo độ ẩm đất", "#3fb950"),
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
  chartHistoryLen = n;   // mốc để stripForecastTail()/updateForecast() biết ranh giới thật/dự báo
  const labels = sorted.map(r => {
    const d = new Date(r.created_at.replace(" ", "T"));
    return d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  });

  chartTemp.data.labels           = labels;
  chartTemp.data.datasets[0].data = sorted.map(r => r.temperature);
  chartTemp.data.datasets[1].data = Array(n).fill(ACTUATOR_RULES.fan.onAbove);
  chartTemp.data.datasets[2].data = Array(n).fill(ACTUATOR_RULES.roof.fullOpenAbove);
  chartTemp.data.datasets[3].data = Array(n).fill(null);   // xóa forecast cũ, chờ fetchPredict() vẽ lại
  chartTemp.update("none");

  chartHum.data.labels           = labels;
  chartHum.data.datasets[0].data = sorted.map(r => r.humidity);
  chartHum.data.datasets[1].data = Array(n).fill(null);
  chartHum.update("none");

  chartSoil.data.labels           = labels;
  chartSoil.data.datasets[0].data = sorted.map(r => r.soil_moisture);
  chartSoil.data.datasets[1].data = sorted.map(r => r.light_level);
  chartSoil.data.datasets[2].data = Array(n).fill(ACTUATOR_RULES.pump.onBelow);
  chartSoil.data.datasets[3].data = Array(n).fill(ACTUATOR_RULES.pump.offAbove);
  chartSoil.data.datasets[4].data = Array(n).fill(null);
  chartSoil.update("none");
}

// ─── Dự báo (Analytics/Prediction Layer — Giai đoạn 2) ─────────────────────
// Nối đường nét đứt ngay sau điểm dữ liệu thật cuối cùng, dùng chung nhãn thời
// gian thực (real_seconds_ahead quy đổi từ SIM_SPEED) để khớp trục X với lịch sử.
function updateForecast(forecastPoints, lastRow) {
  if (!forecastPoints?.length || !lastRow?.created_at) return;

  stripForecastTail();   // đảm bảo nối vào đúng ranh giới thật/dự báo, kể cả khi gọi 2 lần liên tiếp
  const histLen   = chartHistoryLen;
  const baseTime  = new Date(lastRow.created_at.replace(" ", "T")).getTime();
  const anchorPad = Array(Math.max(histLen - 1, 0)).fill(null);

  const forecastLabels = forecastPoints.map(p => {
    const t = new Date(baseTime + p.real_seconds_ahead * 1000);
    return t.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  });

  // Điểm neo (lastRow) đặt tại index histLen-1 — trùng đúng nhãn cuối cùng của
  // lịch sử đã có sẵn, nên đường nét đứt bắt đầu đúng vị trí đường liền nét kết
  // thúc (không có khoảng hở giữa thật và dự báo).
  chartTemp.data.labels           = [...chartTemp.data.labels, ...forecastLabels];
  chartTemp.data.datasets[3].data = [...anchorPad, lastRow.temperature, ...forecastPoints.map(p => p.temperature)];

  chartHum.data.labels           = [...chartHum.data.labels, ...forecastLabels];
  chartHum.data.datasets[1].data = [...anchorPad, lastRow.humidity, ...forecastPoints.map(p => p.humidity)];

  chartSoil.data.labels           = [...chartSoil.data.labels, ...forecastLabels];
  chartSoil.data.datasets[4].data = [...anchorPad, lastRow.soil_moisture, ...forecastPoints.map(p => p.soil_moisture)];

  chartTemp.update("none");
  chartHum.update("none");
  chartSoil.update("none");
}

async function fetchPredict(lastRow) {
  if (!lastRow) return;
  try {
    const res = await fetch(
      `${API}/api/predict?horizon_minutes=${PREDICT_HORIZON_MINUTES}`,
      { headers: getAuthHeaders() }
    );
    if (res.status === 401) { handle401(); return; }
    if (!res.ok) return;
    const d = await res.json();
    if (d.forecast?.length) updateForecast(d.forecast, lastRow);
  } catch {
    // silent — dự báo là tính năng bổ trợ, không chặn luồng dữ liệu thật nếu lỗi
  }
}

async function fetchHistory() {
  if (typeof simState !== 'undefined' && simState) return;   // simulation builds its own chart history
  try {
    const res = await fetch(API + "/api/history", { headers: getAuthHeaders() });
    if (res.status === 401) { handle401(); return; }
    if (!res.ok) return;
    const rows = await res.json();
    if (rows.length) {
      updateCharts(rows);
      fetchPredict(rows[0]);   // rows[0] = bản ghi mới nhất (API trả về ORDER BY created_at DESC)
    }
  } catch {
    // silent — giữ nguyên chart cũ
  }
}

// ─── Control ──────────────────────────────────────
async function sendCommand(device, value) {
  pendingCmds[device] = Date.now(); // Khóa UI không cho update từ DB trong 3s để chờ Wokwi phản hồi
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
  if (typeof simState !== 'undefined' && simState) {
    if      (device === "fan")   simState.fanCmd  = value;
    else if (device === "pump")  simState.pumpCmd = value;
    else if (device === "servo") simState.roofCmd = value;
  }
}

function setActiveBtn(device, valStr) {
  const groups = document.querySelectorAll(`.btn-group[data-device="${device}"]`);
  if (groups.length === 0) return;
  groups.forEach(group => {
    group.querySelectorAll(".btn-ctrl").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.val === valStr);
    });
  });

  // Đồng bộ trạng thái tức thời sang mô hình 3D
  let value;
  if      (valStr === "null")  value = null;
  else if (valStr === "true")  value = true;
  else if (valStr === "false") value = false;
  else                         value = parseInt(valStr, 10);
  if (typeof window.update3DTwinManual === "function") {
    window.update3DTwinManual(device, value);
  }

  // Sync actuator badge immediately so both panels stay consistent
  if (valStr === "null") return;   // AUTO — badge updates on next poll
  if (device === "fan")  { setBadge("act-fan",  valStr === "true"); return; }
  if (device === "pump") { setBadge("act-pump", valStr === "true"); return; }
  if (device === "light") { setBadge("act-light", valStr === "true"); return; }
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
    ["fan", "pump", "light"].forEach(dev => {
      const v = cmds[dev];
      setActiveBtn(dev, v === null ? "null" : String(v));
    });
    setActiveBtn("servo", cmds.servo === null ? "null" : String(cmds.servo));
    setActiveBtn("security", String(cmds.security));
  } catch {
    // Flask chưa sẵn — bỏ qua
  }
}

// ─── TAB NAVIGATION ──────────────────────────────────
function bindTabs() {
  const tabs = document.querySelectorAll('.nav-tab');
  const contents = document.querySelectorAll('.tab-content');
  const loader = document.getElementById("loader-overlay");

  tabs.forEach(tab => {
    tab.addEventListener('click', async () => {
      // Bỏ qua nếu click lại chính tab đang mở
      if (tab.classList.contains('active')) return;

      // 1. Hiển thị màn hình Loading
      if (loader) loader.style.display = "flex";

      // 2. Ẩn nội dung tab cũ
      tabs.forEach(t => t.classList.remove('active'));
      contents.forEach(c => c.classList.remove('active'));
      
      // 3. Chuẩn bị tab mới
      tab.classList.add('active');
      const targetId = tab.getAttribute('data-target');
      const targetContent = document.getElementById(targetId);

      // XỬ LÝ CHO TAB DASHBOARD
      if (targetId === 'tab-dashboard') {
        window.is3DTabActive = false;
        if (typeof window.stop3DAnimation === 'function') {
            window.stop3DAnimation();
        }
        // Đợi gọi API lấy dữ liệu và delay render
        await Promise.all([
            fetchLatest(), 
            fetchHistory(),
            new Promise(resolve => setTimeout(resolve, 500))
        ]);
        
        targetContent.classList.add('active');
        if (loader) loader.style.display = "none";
      } 
      
      // XỬ LÝ CHO TAB 3D TWIN
      else if (targetId === 'tab-3d') {
        window.is3DTabActive = true;
        targetContent.classList.add('active');
        
        // Khởi tạo 3D nếu là lần đầu
        if (typeof init3DTwin === 'function' && !window._3dInitialized) {
          init3DTwin();
          window._3dInitialized = true;
        } else if (typeof window.start3DAnimation === 'function') {
          // Restart animation loop if it was paused
          window.start3DAnimation();
        }

        // Kiểm tra xem đã ready chưa (dành cho các lần mở lại sau)
        if (window.is3DReady === true) {
          setTimeout(() => { if (loader) loader.style.display = "none"; }, 300);
        } else {
          // Nếu chưa, đợi biến cờ window.is3DReady được bật lên từ file 3d_twin.js
          let attempts = 0;
          const maxAttempts = 75; // Đợi tối đa 30 giây (tránh kẹt)
          
          const check3DLoad = setInterval(() => {
            attempts++;
            if (window.is3DReady === true || attempts >= maxAttempts) {
              clearInterval(check3DLoad);
              setTimeout(() => {
                if (loader) loader.style.display = "none";
              }, 300);
            }
          }, 400);
        }
      }
    });
  });
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
        
        // Cập nhật UI text badge tức thời cho Dashboard
        if (device === "fan") setBadge("act-fan", value);
        else if (device === "pump") setBadge("act-pump", value);
        else if (device === "light") setBadge("act-light", value);
        else if (device === "security") {
          const secEl = document.getElementById("act-security");
          if (secEl) {
            secEl.textContent = value ? "ON" : "OFF";
            secEl.className = "actuator-badge " + (value ? "on" : "off");
          }
        }
        else if (device === "roof") {
          const roofEl = document.getElementById("act-roof");
          if (roofEl) {
            roofEl.textContent = value !== null ? value + "°" : "--";
            roofEl.className = "actuator-badge " + (value !== null && value < 90 ? "on" : "off");
          }
        }

        // Cập nhật phản hồi hình ảnh/âm thanh 3D tức thời
        if (typeof window.update3DTwinManual === "function") {
          window.update3DTwinManual(device, value);
        }
      });
    });
  });
}

// ─── INIT & AUTH ─────────────────────────────────────
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
  if (loader) loader.style.display = "flex";

  bindTabs();
  initSparklines();
  initCharts();
  bindControls();
  connectSSE();
  await loadCommandState();

  await Promise.all([
      fetchLatest(), 
      fetchHistory(),
      new Promise(resolve => setTimeout(resolve, 800))
  ]);
  if (loader) loader.style.display = "none";

  setInterval(fetchLatest,  LATEST_INTERVAL);
  setInterval(fetchHistory, HISTORY_INTERVAL);
}

init();