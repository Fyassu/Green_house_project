// ─── Chế độ mô phỏng dữ liệu độc lập (không cần Wokwi) ─────────────────────
// Trả lời yêu cầu: sau khi thử Wokwi Automation Scenario (gặp bug alpha phía
// Wokwi, xem lịch sử trao đổi) và thao tác thủ công (không tạo được liên kết
// tự động giữa nhiệt độ/ánh sáng/đất/thời gian), tính năng này tái sử dụng:
//   - Đồng hồ currentHour/isPlayingTime đã có sẵn trong 3d_twin.js (vốn chỉ
//     điều khiển hiệu ứng mặt trời/bầu trời) làm NGUỒN THỜI GIAN DUY NHẤT,
//     để dữ liệu mô phỏng và cảnh 3D luôn đồng bộ.
//   - ACTUATOR_EFFECTS/ENV_DRIFT/ACTUATOR_RULES/evaluatePlant/roofEffectKey
//     đã có sẵn trong model.js (dùng chung với Physics-based Prediction Layer
//     ở backend/prediction.py) — cùng MỘT bộ luật vật lý cho cả dự đoán lẫn
//     mô phỏng hiển thị, không phát minh lại.
//
// simState khác null <=> đang ở chế độ mô phỏng: fetchLatest()/fetchHistory()
// (app.js) tự động bỏ qua dữ liệu thật, nhường quyền hiển thị cho tick ở đây.

let simState = null;   // null = tắt (dùng dữ liệu Wokwi/MySQL thật); object = đang mô phỏng

// Tốc độ mô phỏng: số "model-tick" (đơn vị hiệu chỉnh ACTUATOR_EFFECTS/ENV_DRIFT,
// vốn tính theo mỗi 10s mô phỏng) tương đương mỗi GIÂY THỰC trôi qua. Giá trị
// này độc lập với tốc độ đồng hồ ngày/đêm (currentHour += delta*1.5 trong
// 3d_twin.js) — cố tình tách riêng để nhiệt độ/độ ẩm/đất biến đổi từ từ, quan
// sát được bằng mắt, thay vì nhảy tức thời theo tốc độ ngày/đêm rất nhanh.
// Cảm thấy quá nhanh/chậm khi xem thực tế thì chỉnh lại số này.
// Giảm còn 1/2 tốc độ gốc (0.15 -> 0.075) theo yêu cầu — đồng bộ với tốc độ
// đồng hồ ngày/đêm (3d_twin.js, currentHour += delta*0.75) cũng giảm 1/2.
const SIM_RATE = 0.075;

// Chỉ đẩy dữ liệu ra dashboard (chart/card/3D) mỗi khoảng này, dù vật lý được
// tích lũy mượt mỗi khung hình — tránh chart nhận hàng chục điểm/giây.
const SIM_PUBLISH_INTERVAL_S = 1.0;

function applyEffectScaled(value, effect, ticks) {
  const next = value + effect.delta * ticks;
  if (effect.limit === undefined) return next;
  return effect.delta < 0 ? Math.max(next, effect.limit) : Math.min(next, effect.limit);
}

// Đường cong nhiệt độ/ánh sáng theo giờ trong ngày: thấp nhất lúc 1h sáng,
// cao nhất lúc 13h — cùng narrative "sáng mát -> trưa nóng -> chiều dịu" đã
// dùng ở backend/seed_demo_data.py và wokwi-demo-scenario.yaml, để nhất quán
// giữa 3 kịch bản demo (MySQL seed / Wokwi scenario / mô phỏng trình duyệt).
function hourToBaseline(hour) {
  const angle = ((hour - 13 + 24) % 24) / 24 * 2 * Math.PI;
  const wave  = (Math.cos(angle) + 1) / 2;              // 0 (đêm) -> 1 (13h) -> 0
  return {
    temp:  18 + wave * 16,   // 18°C (đêm) -> 34°C (13h)
    light: 5  + wave * 90,   // 5% (đêm)   -> 95% (13h)
  };
}

function resolveActuator(cmd, autoValue) {
  return (cmd === null || cmd === undefined) ? autoValue : cmd;
}

// Bắt đầu chế độ mô phỏng — gọi khi bật nút "Bật mô phỏng dữ liệu".
// baseReading (tùy chọn): dùng làm điểm khởi đầu (ví dụ bản ghi /api/latest
// cuối cùng), để chuyển từ dữ liệu thật sang mô phỏng không bị giật cục.
function startSimulation(baseReading) {
  simState = {
    temp:  baseReading?.temperature   ?? 24,
    hum:   baseReading?.humidity      ?? 60,
    soil:  baseReading?.soil_moisture ?? 55,
    light: baseReading?.light_level   ?? 40,
    motion: false,
    // null = AUTO (theo hysteresis ACTUATOR_RULES); true/false = ép buộc thủ
    // công qua các nút ON/OFF sẵn có (sendCommand() trong app.js đã tự gán
    // vào đây — xem đoạn "if (typeof simState !== 'undefined' && simState)").
    fanCmd: null, pumpCmd: null, roofCmd: null,
    // Trạng thái AUTO hiện tại (có nhớ để tạo hysteresis, không dao động liên tục ở ngưỡng)
    fanAuto: false, pumpAuto: false,
    accumSec: 0,
  };
}

function stopSimulation() {
  simState = null;
}

function _round(v, d = 1) { return Math.round(v * 10 ** d) / 10 ** d; }

// Gọi mỗi khung hình từ animate() (3d_twin.js) khi isPlayingTime=true, hoặc
// gọi 1 lần với deltaRealSeconds=0 khi người dùng kéo tay slider thời gian
// (để ánh sáng phản ánh ngay giờ mới, không cần chờ play).
function runSimTick(deltaRealSeconds) {
  if (!simState) return;

  const baseline = hourToBaseline(currentHour);   // currentHour: biến toàn cục có sẵn trong 3d_twin.js
  const ticks    = deltaRealSeconds * SIM_RATE;

  // --- Actuator: AUTO (hysteresis) hoặc ép buộc thủ công ---
  if (simState.temp > ACTUATOR_RULES.fan.onAbove)       simState.fanAuto = true;
  else if (simState.temp < ACTUATOR_RULES.fan.offBelow) simState.fanAuto = false;
  const fanOn = resolveActuator(simState.fanCmd, simState.fanAuto);

  if (simState.soil < ACTUATOR_RULES.pump.onBelow)        simState.pumpAuto = true;
  else if (simState.soil > ACTUATOR_RULES.pump.offAbove)  simState.pumpAuto = false;
  const pumpOn = resolveActuator(simState.pumpCmd, simState.pumpAuto);

  let roofAngle;
  if (simState.roofCmd !== null && simState.roofCmd !== undefined) {
    roofAngle = simState.roofCmd;
  } else if (simState.temp > ACTUATOR_RULES.roof.fullOpenAbove) {
    roofAngle = 0;
  } else if (
    simState.light > ACTUATOR_RULES.roof.halfOpenLightMin &&
    currentHour >= ACTUATOR_RULES.roof.halfOpenHourStart &&
    currentHour <= ACTUATOR_RULES.roof.halfOpenHourEnd
  ) {
    roofAngle = 45;
  } else {
    roofAngle = 90;
  }
  const roofKey = roofEffectKey(roofAngle);

  // --- Vật lý: nhiệt độ/độ ẩm/đất dùng ACTUATOR_EFFECTS + ENV_DRIFT (model.js,
  // cùng bộ hằng số với backend/prediction.py) + "kéo dần" về baseline ngày/đêm
  // cho riêng nhiệt độ (ENV_DRIFT.temp vốn là hằng số, không tự nguội ban đêm
  // nếu dùng một mình — xem giải thích trong tài liệu trao đổi). Ánh sáng theo
  // dõi baseline tức thời (không có quán tính nhiệt như temp/hum/soil).
  // Hệ số "kéo về baseline" (trước là 0.1) quá yếu so với chu kỳ ngày/đêm đã
  // được tua nhanh cho demo (24h mô phỏng ≈ vài chục giây thực): nhiệt độ gần
  // như không kịp dao động theo baseline, luôn quanh quẩn giữa 28-30°C nên
  // fan/mái không bao giờ tự bật. Tăng lên 4 để nhiệt độ bám sát biên độ
  // ngày/đêm (chạm ngưỡng ON của fan ~30°C ban trưa, tụt dưới OFF ~28°C ban
  // đêm), actuator AUTO mới có cơ hội phản ứng thật.
  simState.temp += (baseline.temp - simState.temp) * Math.min(1, 4 * ticks);
  simState.temp  = applyEffectScaled(simState.temp, { delta: ENV_DRIFT.temp, limit: undefined }, ticks);
  if (fanOn)  simState.temp = applyEffectScaled(simState.temp, ACTUATOR_EFFECTS.fan.temp, ticks);
  simState.temp = applyEffectScaled(simState.temp, ACTUATOR_EFFECTS.roof[roofKey].temp ?? { delta: 0 }, ticks);

  simState.hum = applyEffectScaled(simState.hum, { delta: ENV_DRIFT.hum, limit: undefined }, ticks);
  if (fanOn)  simState.hum = applyEffectScaled(simState.hum, ACTUATOR_EFFECTS.fan.hum, ticks);
  if (pumpOn) simState.hum = applyEffectScaled(simState.hum, ACTUATOR_EFFECTS.pump.hum, ticks);
  simState.hum = applyEffectScaled(simState.hum, ACTUATOR_EFFECTS.roof[roofKey].hum ?? { delta: 0 }, ticks);

  simState.soil = applyEffectScaled(simState.soil, { delta: ENV_DRIFT.soil, limit: undefined }, ticks);
  if (pumpOn) simState.soil = applyEffectScaled(simState.soil, ACTUATOR_EFFECTS.pump.soil, ticks);

  simState.light = baseline.light + (ACTUATOR_EFFECTS.roof[roofKey].light?.delta ?? 0);

  simState.temp  = clampEnv("temp",  simState.temp);
  simState.hum   = clampEnv("hum",   simState.hum);
  simState.soil  = clampEnv("soil",  simState.soil);
  simState.light = clampEnv("light", simState.light);

  // Chuyển động ngẫu nhiên nhẹ cho vui mắt (không ảnh hưởng vật lý môi trường)
  if (Math.random() < 0.02 * Math.max(ticks, 0.01)) simState.motion = true;
  else if (Math.random() < 0.3) simState.motion = false;

  // --- Đẩy ra dashboard, đều đặn theo SIM_PUBLISH_INTERVAL_S ---
  simState.accumSec += deltaRealSeconds;
  if (simState.accumSec < SIM_PUBLISH_INTERVAL_S && deltaRealSeconds > 0) return;
  simState.accumSec = 0;

  const reading = {
    temperature:     _round(simState.temp, 2),
    humidity:        _round(simState.hum, 2),
    soil_moisture:   Math.round(simState.soil),
    light_level:     Math.round(simState.light),
    motion_detected: simState.motion,
    fan_status:      fanOn,
    pump_status:     pumpOn,
    servo_angle:     roofAngle,
    light_status:    simState.motion && simState.light < ACTUATOR_RULES.motionLight.triggerBelowLight,
    security_mode:   true,
    created_at:      new Date().toISOString().slice(0, 19).replace("T", " "),
  };
  reading.plant_health = evaluatePlant(reading.temperature, reading.soil_moisture, reading.humidity);

  updateCards(reading);
  updatePlantHealth(reading);
  updateActuators(reading);
  appendToCharts(reading);
  if (typeof window.update3DTwin === "function") window.update3DTwin(reading);
}

// ─── Nút bật/tắt chế độ mô phỏng ────────────────────────────────────────────
// Không bọc DOMContentLoaded: script nằm cuối <body> (sau app.js trong
// index.html) nên DOM đã sẵn sàng khi chạy tới đây — event DOMContentLoaded
// có thể đã fire trước đó, khớp đúng cách các script khác trong dự án đang làm
// (initSparklines/initCharts/bindControls đều gọi trực tiếp không cần đợi).
(function initSimModeButton() {
  const btn = document.getElementById("btn-sim-mode");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    if (simState) {
      stopSimulation();
      btn.classList.remove("active");
      btn.innerHTML = '<i class="fa-solid fa-flask"></i> Bật mô phỏng dữ liệu (không cần Wokwi)';
    } else {
      let baseReading = null;
      try {
        const res = await fetch(`${API}/api/latest`, { headers: getAuthHeaders() });
        if (res.ok) baseReading = await res.json();
      } catch { /* dùng giá trị mặc định nếu không lấy được */ }
      startSimulation(baseReading?.message ? null : baseReading);
      btn.classList.add("active");
      btn.innerHTML = '<i class="fa-solid fa-flask"></i> Tắt mô phỏng dữ liệu (dùng lại Wokwi thật)';
    }
  });
})();

// ─── Kịch bản môi trường khắc nghiệt ────────────────────────────────────────
// Đẩy trực tiếp simState sang mức khắc nghiệt (nóng + khô hạn) và trả actuator
// về AUTO, để người dùng thấy quạt/bơm/mái che (ACTUATOR_RULES, model.js) tự
// phản ứng ngay trong tick kế tiếp của runSimTick() thay vì phải chờ nhiều
// phút cho baseline ngày/đêm tự trôi tới ngưỡng.
function triggerExtremeScenario() {
  if (!simState) return;
  simState.temp  = 40;   // > roof.fullOpenAbove (34) và fan.onAbove (30)
  simState.hum   = 15;   // < CRITICAL.hum (20)
  simState.soil  = 10;   // < pump.onBelow (45), gần DEAD.soil (5)
  simState.light = 90;
  simState.fanCmd = null;   simState.pumpCmd = null;   simState.roofCmd = null;
  simState.fanAuto = true;  simState.pumpAuto = true;  // phản ứng ngay, không đợi tick kiểm tra ngưỡng
  // Đồng bộ nút AUTO ở panel "Thiết bị & Điều khiển" (app.js) nếu người dùng
  // trước đó có ép buộc thủ công — tránh UI hiển thị sai lệch với simState.
  if (typeof setActiveBtn === "function") {
    setActiveBtn("fan", "null");
    setActiveBtn("pump", "null");
    setActiveBtn("servo", "null");
  }
}

(function initExtremeScenarioButton() {
  const btn = document.getElementById("btn-sim-extreme");
  if (!btn) return;
  btn.addEventListener("click", () => {
    if (!simState) {
      alert('Hãy bật "Bật mô phỏng dữ liệu" trước khi tạo kịch bản khắc nghiệt.');
      return;
    }
    triggerExtremeScenario();
  });
})();
