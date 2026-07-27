# Analytics/prediction layer.
# The physics-only path now uses the same unit-consistent PBM as training and
# residual-PGML inference. Actuator state and the estimated outdoor boundary are
# held constant over the requested horizon ("what if nothing else changes").
# ARIMA/backtest/anomaly functions remain for historical comparisons.

import pandas as pd
from statsmodels.tsa.arima.model import ARIMA

from pbm_engine import (
    GreenhouseState,
    PHYSICS_MODEL_VERSION,
    ProcessBasedModelEngine,
)

from ml_prediction.prediction_engine import ml_forecast

TICK_SECONDS = 10   # 1 tick = 10 giây THỜI GIAN MÔ PHỎNG — mirrors model.py/model.js/sketch.ino
SIM_SPEED    = 12   # mirrors dashboard/app.js SIM_SPEED — 1 phút mô phỏng = 5 giây thực
_process_model = ProcessBasedModelEngine()


def simulate_forward(reading: dict, horizon_minutes: int = 30, sample_every_ticks: int = 6) -> list[dict]:
    """
    Dự báo đa bước bằng Lumped Parameter Model (Bài 4 - Physics-based Model).

    reading: dict theo đúng field name của GET /api/latest (temperature,
             humidity, soil_moisture, light_level, fan_status, pump_status,
             servo_angle) — lấy từ bản ghi sensor_data mới nhất.
    horizon_minutes: số phút MÔ PHỎNG muốn dự báo tới (không phải phút thực —
             xem SIM_SPEED). Giới hạn 5-120 phút do endpoint gọi hàm này.
    sample_every_ticks: khoảng cách lấy mẫu giữa các điểm trả về, để tránh
             payload quá dài (mặc định 6 tick = 1 phút mô phỏng/điểm).

    Trả về danh sách điểm, mỗi điểm có thêm 2 trường thời gian:
      - sim_seconds_ahead:  số giây MÔ PHỎNG kể từ hiện tại
      - real_seconds_ahead: số giây THỰC kể từ hiện tại (sim_seconds_ahead / SIM_SPEED),
        dùng để đặt nhãn trục thời gian thực trên chart cho khớp với dữ liệu lịch sử.
    """
    # ``sample_every_ticks`` is retained only for API compatibility. The
    # scientific process model has a fixed reporting cadence of 5 minutes.
    del sample_every_ticks
    horizon_minutes = max(5, min(int(horizon_minutes), 120))
    state = GreenhouseState.from_mapping(reading)
    fan_on = bool(reading.get("fan_status"))
    pump_on = bool(reading.get("pump_status"))
    servo_angle = int(reading.get("servo_angle", 0) or 0)
    boundary = _process_model.estimate_boundary_from_observation(
        state, servo_angle
    )

    trace = []
    elapsed_seconds = 0.0
    remaining_seconds = float(horizon_minutes * 60)
    while remaining_seconds > 0:
        step_seconds = min(300.0, remaining_seconds)
        state, _ = _process_model.advance(
            state,
            boundary,
            fan_on,
            pump_on,
            servo_angle,
            dt_seconds=step_seconds,
        )
        elapsed_seconds += step_seconds
        remaining_seconds -= step_seconds
        point = state.as_observation()
        point["minute_offset"] = int(round(elapsed_seconds / 60.0))
        point["sim_seconds_ahead"] = int(round(elapsed_seconds))
        point["real_seconds_ahead"] = round(
            elapsed_seconds / SIM_SPEED, 1
        )
        point["physics_model_version"] = PHYSICS_MODEL_VERSION
        point["boundary_source"] = "estimated_from_indoor_observation"
        trace.append(point)
    return trace


def project_n_ticks(reading: dict, n_ticks: float) -> dict:
    """Trả về ĐÚNG 1 điểm dự báo sau n_ticks bước (physics-based) — dùng cho
    backtest_compare() khi cần so sánh với 1 điểm dữ liệu thực tế cụ thể."""
    state = GreenhouseState.from_mapping(reading)
    fan_on = bool(reading.get("fan_status"))
    pump_on = bool(reading.get("pump_status"))
    servo_angle = int(reading.get("servo_angle", 0) or 0)
    boundary = _process_model.estimate_boundary_from_observation(
        state, servo_angle
    )
    state, _ = _process_model.advance(
        state,
        boundary,
        fan_on,
        pump_on,
        servo_angle,
        dt_seconds=max(TICK_SECONDS, float(n_ticks) * TICK_SECONDS),
    )
    return state.as_observation()


# ─── Giai đoạn 3 — Data-driven Model (ARIMA) ────────────────────────────────

MIN_TRAINING_POINTS = 15   # tối thiểu để ARIMA(2,1,2) hội tụ có ý nghĩa


def arima_forecast(history_rows: list[dict], column: str, steps: int) -> list[float]:
    """
    Data-driven forecast (Bài 4 - Data-driven Model, nhóm Statistical: ARIMA).
    Lựa chọn ARIMA thay vì LSTM: huấn luyện nhanh (giây), không cần GPU, phù hợp
    tài nguyên hạn chế — xem implementation_plan.md mục 3.2 (so sánh ARIMA vs
    LSTM cho dữ liệu IoT môi trường).

    history_rows: kết quả của db.get_training_window(), sắp xếp thời gian TĂNG DẦN.
    column: tên field cần dự báo (temperature/humidity/soil_moisture/light_level).
    steps: số bước dự báo, tính theo đơn vị "1 bước = khoảng cách giữa 2 bản ghi
           lịch sử" (KHÔNG phải 1 tick 10s như simulate_forward — vì lịch sử lấy
           theo nhịp ghi dữ liệu thực tế, có thể khác 10s).
    """
    if len(history_rows) < MIN_TRAINING_POINTS:
        raise ValueError(
            f"Cần tối thiểu {MIN_TRAINING_POINTS} điểm dữ liệu lịch sử để huấn luyện ARIMA, "
            f"hiện có {len(history_rows)}"
        )
    series = pd.Series([r[column] for r in history_rows], dtype="float64")
    fitted = ARIMA(series, order=(2, 1, 2)).fit()
    return fitted.forecast(steps=steps).tolist()


def compute_rmse(actual: list[float], predicted: list[float]) -> float | None:
    n = min(len(actual), len(predicted))
    if n == 0:
        return None
    mse = sum((a - p) ** 2 for a, p in zip(actual[:n], predicted[:n])) / n
    return round(mse ** 0.5, 3)


def _avg_row_interval_ticks(rows: list[dict]) -> float:
    """
    Ước lượng số tick (10s mô phỏng) trung bình giữa 2 bản ghi lịch sử liên
    tiếp, dựa trên timestamp thực tế `created_at` và SIM_SPEED. Dùng để quy đổi
    "N bước ARIMA" (N hàng lịch sử) sang "N x ticks" khi chạy song song với
    physics-based trong backtest_compare() — không giả định cứng nhịp ghi dữ
    liệu là bao nhiêu giây, mà đo trực tiếp từ dữ liệu thật.
    """
    timestamps = [r["created_at"] for r in rows if r.get("created_at")]
    if len(timestamps) < 2:
        return 1.0
    deltas_real_s = [
        (timestamps[i + 1] - timestamps[i]).total_seconds()
        for i in range(len(timestamps) - 1)
    ]
    deltas_real_s = [d for d in deltas_real_s if d > 0]
    if not deltas_real_s:
        return 1.0
    avg_real_s = sum(deltas_real_s) / len(deltas_real_s)
    avg_sim_s  = avg_real_s * SIM_SPEED
    return max(avg_sim_s / TICK_SECONDS, 0.1)


def backtest_compare(history_rows: list[dict], column: str, holdout: int = 10) -> dict:
    """
    So sánh RMSE giữa Physics-based (simulate_forward/project_n_ticks) và
    Data-driven (ARIMA) trên cùng một đoạn lịch sử đã biết trước kết quả thực
    tế (backtesting): cắt `holdout` điểm cuối làm "tương lai giả lập", huấn
    luyện/mô phỏng chỉ trên phần còn lại, rồi so kết quả dự báo với giá trị
    thật đã bị cắt.

    history_rows: kết quả của db.get_training_window(), sắp xếp thời gian TĂNG DẦN.
    column: temperature/humidity/soil_moisture/light_level.
    holdout: số điểm cuối dùng để kiểm tra (không dùng để huấn luyện).
    """
    if len(history_rows) < holdout + MIN_TRAINING_POINTS:
        raise ValueError(
            f"Cần tối thiểu {holdout + MIN_TRAINING_POINTS} bản ghi lịch sử để backtest "
            f"(holdout={holdout} + tối thiểu {MIN_TRAINING_POINTS} điểm huấn luyện), "
            f"hiện có {len(history_rows)}"
        )

    train_rows = history_rows[:-holdout]
    test_rows  = history_rows[-holdout:]
    actual     = [r[column] for r in test_rows]

    result = {"column": column, "holdout": holdout, "actual": actual}

    # Data-driven (ARIMA)
    try:
        arima_pred = arima_forecast(train_rows, column, steps=holdout)
        result["data_driven"] = {"forecast": arima_pred, "rmse": compute_rmse(actual, arima_pred)}
    except Exception as e:
        result["data_driven"] = {"error": str(e)}

    # Physics-based — mốc gốc là bản ghi cuối cùng của train_rows, giả định
    # actuator giữ nguyên trạng thái tại thời điểm đó suốt giai đoạn backtest.
    last_train      = train_rows[-1]
    ticks_per_step  = _avg_row_interval_ticks(train_rows)
    physics_pred    = []
    cumulative_ticks = 0.0
    for _ in range(holdout):
        cumulative_ticks += ticks_per_step
        point = project_n_ticks(last_train, cumulative_ticks)
        physics_pred.append(point[column])
    result["physics"] = {"forecast": physics_pred, "rmse": compute_rmse(actual, physics_pred)}

    return result


# ─── Giai đoạn 4 — Hybrid Model (Physics-Informed) ──────────────────────────

HYBRID_BIAS_WINDOW = 5   # số điểm lịch sử gần nhất dùng để ước lượng độ lệch (bias)


def hybrid_forecast(history_rows: list[dict], reading: dict, horizon_minutes: int = 30,
                     sample_every_ticks: int = 6, bias_window: int = HYBRID_BIAS_WINDOW) -> dict:
    """
    Hybrid Model (Bài 4): dùng Physics-based làm baseline (đảm bảo tuân thủ vật
    lý, ngoại suy an toàn), rồi hiệu chỉnh bằng "bias" — độ lệch trung bình giữa
    những gì physics LẼ RA đã dự báo và giá trị THỰC TẾ đã xảy ra trong
    `bias_window` điểm gần nhất. Nếu physics gần đây liên tục lệch theo một
    hướng nhất định (ví dụ luôn thấp hơn thực tế ~1°C), giả định độ lệch đó vẫn
    tiếp diễn và cộng bù vào dự báo tương lai.

    history_rows: kết quả db.get_training_window(), sắp xếp thời gian TĂNG DẦN,
                  phải có ít nhất bias_window+1 điểm để tính được bias (nếu
                  không đủ, bias = 0 — hybrid suy biến về đúng physics thuần).
    reading:      bản ghi mới nhất (giống input của simulate_forward()).
    """
    columns = ("temperature", "humidity", "soil_moisture", "light_level")
    bias = {c: 0.0 for c in columns}

    if len(history_rows) >= bias_window + 1:
        anchor         = history_rows[-(bias_window + 1)]
        window_rows    = history_rows[-bias_window:]
        ticks_per_step = _avg_row_interval_ticks(history_rows[-(bias_window + 1):])

        residuals = {c: [] for c in columns}
        cumulative_ticks = 0.0
        for row in window_rows:
            cumulative_ticks += ticks_per_step
            predicted = project_n_ticks(anchor, cumulative_ticks)
            for c in columns:
                residuals[c].append(row[c] - predicted[c])
        bias = {c: round(sum(vals) / len(vals), 3) for c, vals in residuals.items()}

    physics_trace = simulate_forward(reading, horizon_minutes, sample_every_ticks)
    hybrid_trace = []
    for point in physics_trace:
        adjusted = dict(point)
        for c in columns:
            adjusted[c] = round(point[c] + bias[c], 2)
        hybrid_trace.append(adjusted)

    return {"forecast": hybrid_trace, "bias": bias, "bias_window": bias_window}


# ─── Giai đoạn 4 — Anomaly Detection (bơm/quạt) ─────────────────────────────

ANOMALY_WINDOW    = 10     # số bản ghi gần nhất để đo hiệu ứng thực tế
ANOMALY_THRESHOLD = 0.3    # tỷ lệ actual/expected dưới ngưỡng này -> nghi ngờ hỏng

# Cột môi trường chịu ảnh hưởng chính của mỗi actuator — dùng để đo hiệu ứng
ANOMALY_TARGETS = {
    "fan":  "temperature",
    "pump": "soil_moisture",
}


def _anomaly_verdict(actuator: str, column: str, expected: float, actual: float) -> dict:
    ratio = round(actual / expected, 2) if abs(expected) > 0.01 else None
    # Nếu expected_delta gần như bằng 0 (hệ đã ở trạng thái cân bằng — actuator
    # khỏe mạnh cũng không tạo thay đổi đáng kể), không đủ cơ sở kết luận —
    # tránh báo động giả khi actuator đã đạt điểm bão hòa/cân bằng (limit).
    suspected = ratio is not None and abs(expected) > 0.3 and ratio < ANOMALY_THRESHOLD
    return {
        "actuator":        actuator,
        "column":          column,
        "expected_delta":  round(expected, 2),
        "actual_delta":    round(actual, 2),
        "ratio":           ratio,
        "suspected_fault": suspected,
    }


def detect_actuator_anomalies(history_rows: list[dict], window: int = ANOMALY_WINDOW) -> list[dict]:
    """
    Phát hiện bất thường/bảo trì dự đoán cho bơm–quạt (Bài 4 — ước lượng biến
    không đo trực tiếp: ở đây là "tình trạng sức khỏe" của actuator).

    Ý tưởng: nếu actuator BẬT LIÊN TỤC suốt `window` bản ghi gần nhất nhưng đại
    lượng môi trường mà nó ảnh hưởng thay đổi ít hơn nhiều so với những gì mô
    hình vật lý (project_n_ticks) dự đoán — với ĐẦY ĐỦ mọi hiệu ứng đang diễn
    ra đồng thời (quạt + bơm + mái che + drift tự nhiên, không chỉ riêng actuator
    đang xét) — hoặc thay đổi SAI HƯỚNG, thì nghi ngờ actuator đang hỏng/kẹt.

    QUAN TRỌNG: "kỳ vọng" được tính bằng project_n_ticks(), tức rollout đồng
    thời các cân bằng năng lượng, hơi nước và nước vùng rễ. Không dùng một
    delta actuator cố định vì tác động của quạt phụ thuộc điều kiện biên và
    chênh lệch trạng thái trong/ngoài nhà kính.

    history_rows: kết quả db.get_training_window(), sắp xếp thời gian TĂNG DẦN,
                  cần tối thiểu window+1 bản ghi.
    Trả về danh sách kết quả — chỉ gồm các actuator ĐANG bật liên tục suốt cửa
    sổ quan sát (không đánh giá được nếu actuator tắt/bật xen kẽ trong window).
    """
    if len(history_rows) < window + 1:
        return []

    recent = history_rows[-(window + 1):]
    anchor = recent[0]
    ticks  = _avg_row_interval_ticks(recent) * window

    anchor_reading = {
        "temperature":   anchor["temperature"],
        "humidity":      anchor["humidity"],
        "soil_moisture": anchor["soil_moisture"],
        "light_level":   anchor["light_level"],
        "fan_status":    anchor["fan_status"],
        "pump_status":   anchor["pump_status"],
        "servo_angle":   anchor["servo_angle"],
    }
    # Dự báo "đầy đủ" — đúng mọi hiệu ứng đang thực sự diễn ra đồng thời tại
    # thời điểm mốc, dùng làm chuẩn kỳ vọng thay vì delta riêng lẻ 1 actuator.
    predicted_full = project_n_ticks(anchor_reading, ticks)

    results = []
    for actuator, column in ANOMALY_TARGETS.items():
        status_key = f"{actuator}_status"   # get_training_window() luôn có field này
        if not all(bool(r.get(status_key)) for r in recent[1:]):
            continue   # actuator không bật liên tục suốt window -> không đủ điều kiện đánh giá

        expected = predicted_full[column] - anchor[column]
        actual   = recent[-1][column] - anchor[column]
        results.append(_anomaly_verdict(actuator, column, expected, actual))

    return results
