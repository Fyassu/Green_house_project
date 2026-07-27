# -*- coding: utf-8 -*-
"""Compatibility adapter for the former ``engine=ml`` API option.

There is now one artifact contract only: residual-PGML.  Keeping a second
35-feature loader here was the source of the previous runtime failure because
the deployed scaler expected a different feature count.
"""

from __future__ import annotations

from hybrid_model import HybridPredictionEngine


_hybrid_engine: HybridPredictionEngine | None = None


def _engine() -> HybridPredictionEngine:
    global _hybrid_engine
    if _hybrid_engine is None:
        _hybrid_engine = HybridPredictionEngine()
    return _hybrid_engine


def _number(row: dict, key: str, default: float) -> float:
    value = row.get(key)
    return default if value is None else float(value)


def ml_forecast(
    history_rows: list[dict],
    horizon_minutes: int = 30,
) -> list[dict]:
    """Return the same residual-PGML forecast through the legacy entry point."""
    if not history_rows:
        return []
    recent = history_rows[-5:]
    while len(recent) < 5:
        recent.insert(0, recent[0])

    window = []
    for row in recent:
        values = [
            _number(row, "temperature", 25.0),
            _number(row, "humidity", 60.0),
            _number(row, "soil_moisture", 65.0),
            _number(row, "light_level", 50.0),
            float(bool(row.get("fan_status", False))),
            float(bool(row.get("pump_status", False))),
            _number(row, "servo_angle", 0.0),
        ]
        outdoor_keys = (
            "outdoor_temperature",
            "outdoor_humidity",
            "solar_radiation",
            "wind_speed",
        )
        if all(row.get(key) is not None for key in outdoor_keys):
            values.extend(
                _number(row, key, default)
                for key, default in zip(
                    outdoor_keys,
                    (24.0, 70.0, 0.0, 0.8),
                )
            )
        window.append(values)
    latest = recent[-1]
    steps = _engine().predict_hybrid(
        window,
        bool(latest.get("fan_status", False)),
        bool(latest.get("pump_status", False)),
        int(_number(latest, "servo_angle", 0.0)),
    )
    requested = max(5, min(int(horizon_minutes), 30))
    result = []
    for point in steps:
        if point["minutes"] > requested:
            continue
        item = dict(point)
        item["minute_offset"] = item["minutes"]
        item["sim_seconds_ahead"] = item["minutes"] * 60
        item["real_seconds_ahead"] = item["minutes"] * 5.0
        result.append(item)
    return result
