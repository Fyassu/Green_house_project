"""Time-based rule health index for greenhouse crops.

This is a transparent course-project indicator, not a validated biological
growth model.  It separates:

* environment_status: immediate conditions around the plant;
* health_status / hp: cumulative condition changing with elapsed time.

HP rates are expressed per hour, so changing the MQTT publish interval does not
change how quickly a plant loses or recovers health.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


CROP_SPECS = {
    "tomato": {
        "name": "Cà chua (Tomato)",
        "icon": "🍅",
        "temperature": {
            "optimal": (20.0, 27.0),
            "tolerance": (18.0, 32.0),
            "warning": (15.0, 37.0),
        },
        "soil_moisture": {
            "optimal": (60.0, 75.0),
            "tolerance": (50.0, 80.0),
            "warning": (40.0, 90.0),
        },
        "humidity": {
            "optimal": (60.0, 75.0),
            "tolerance": (50.0, 85.0),
            "warning": (40.0, 95.0),
        },
        "light_level": {
            "optimal": (60.0, 100.0),
            "tolerance": (40.0, 100.0),
            "warning": (20.0, 100.0),
        },
    },
    "strawberry": {
        "name": "Dâu tây (Strawberry)",
        "icon": "🍓",
        "temperature": {
            "optimal": (15.0, 22.0),
            "tolerance": (12.0, 27.0),
            "warning": (8.0, 32.0),
        },
        "soil_moisture": {
            "optimal": (60.0, 70.0),
            "tolerance": (50.0, 75.0),
            "warning": (40.0, 85.0),
        },
        "humidity": {
            "optimal": (65.0, 80.0),
            "tolerance": (55.0, 85.0),
            "warning": (45.0, 95.0),
        },
        "light_level": {
            "optimal": (40.0, 70.0),
            "tolerance": (20.0, 85.0),
            "warning": (10.0, 95.0),
        },
    },
    "cantaloupe": {
        "name": "Dưa lưới (Cantaloupe)",
        "icon": "🍈",
        "temperature": {
            "optimal": (25.0, 30.0),
            "tolerance": (20.0, 35.0),
            "warning": (15.0, 40.0),
        },
        "soil_moisture": {
            "optimal": (70.0, 85.0),
            "tolerance": (60.0, 90.0),
            "warning": (50.0, 95.0),
        },
        "humidity": {
            "optimal": (50.0, 65.0),
            "tolerance": (40.0, 75.0),
            "warning": (30.0, 85.0),
        },
        "light_level": {
            "optimal": (70.0, 100.0),
            "tolerance": (50.0, 100.0),
            "warning": (30.0, 100.0),
        },
    },
}

FACTOR_LABELS = {
    "temperature": "nhiệt độ",
    "humidity": "độ ẩm không khí",
    "soil_moisture": "độ ẩm đất",
    "light_level": "ánh sáng",
}

ZONE_SCORE = {
    "OPTIMAL": 0,
    "ACCEPTABLE": 1,
    "WARNING": 2,
    "CRITICAL": 3,
}

HEALTH_TEXT = {
    "HEALTHY": "HEALTHY (Sức khỏe tốt)",
    "STRESSED": "STRESSED (Đang chịu stress)",
    "WEAK": "WEAK (Cây suy yếu)",
    "CRITICAL": "CRITICAL (Sức khỏe nguy hiểm)",
    "DEAD": "DEAD (Cây đã chết)",
}


def _utc(value: datetime | str | None) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _float_env(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


class PlantHPEngine:
    """Maintain a cumulative 0..100 health index for each configured crop."""

    def __init__(
        self,
        initial_states: dict[str, dict] | None = None,
        *,
        time_scale: float | None = None,
        max_update_gap_seconds: float | None = None,
    ):
        self.time_scale = (
            time_scale
            if time_scale is not None
            else _float_env("PLANT_HP_TIME_SCALE", 1.0, 0.0)
        )
        self.max_update_gap_seconds = (
            max_update_gap_seconds
            if max_update_gap_seconds is not None
            else _float_env("PLANT_HP_MAX_GAP_SECONDS", 300.0, 1.0)
        )
        self.crop_states = {}
        for crop_key in CROP_SPECS:
            source = (initial_states or {}).get(crop_key, {})
            hp = max(0.0, min(100.0, float(source.get("hp", 100.0))))
            is_dead = bool(source.get("is_dead", hp <= 0.0))
            health_status = (
                "DEAD" if is_dead else self._health_status_from_hp(hp)
            )
            self.crop_states[crop_key] = {
                "hp": 0.0 if is_dead else hp,
                "is_dead": is_dead,
                "health_status": health_status,
                "environment_status": source.get(
                    "environment_status", "UNKNOWN"
                ),
                "status_text": HEALTH_TEXT[health_status],
                "last_delta": float(source.get("last_delta", 0.0)),
                "hp_rate_per_hour": float(
                    source.get("hp_rate_per_hour", 0.0)
                ),
                "stress_factors": list(source.get("stress_factors", [])),
                "factor_zones": dict(source.get("factor_zones", {})),
                "last_updated_at": (
                    _utc(source["last_updated_at"])
                    if source.get("last_updated_at")
                    else None
                ),
            }

    @staticmethod
    def _health_status_from_hp(hp: float) -> str:
        if hp >= 80.0:
            return "HEALTHY"
        if hp >= 50.0:
            return "STRESSED"
        if hp >= 25.0:
            return "WEAK"
        if hp > 0.0:
            return "CRITICAL"
        return "DEAD"

    @staticmethod
    def _factor_zone(value: float, bands: dict) -> str:
        if bands["optimal"][0] <= value <= bands["optimal"][1]:
            return "OPTIMAL"
        if bands["tolerance"][0] <= value <= bands["tolerance"][1]:
            return "ACCEPTABLE"
        if bands["warning"][0] <= value <= bands["warning"][1]:
            return "WARNING"
        return "CRITICAL"

    @staticmethod
    def _environment_status(factor_zones: dict[str, str]) -> str:
        return max(factor_zones.values(), key=lambda zone: ZONE_SCORE[zone])

    @staticmethod
    def _hp_rate(environment_status: str, factor_zones: dict[str, str]) -> float:
        """Return HP/hour; recovery is intentionally slower than damage."""
        if environment_status == "OPTIMAL":
            return 0.5
        if environment_status == "ACCEPTABLE":
            return 0.0
        if environment_status == "WARNING":
            warning_count = sum(
                zone == "WARNING" for zone in factor_zones.values()
            )
            return -1.0 - 0.5 * max(0, warning_count - 1)

        critical_count = sum(
            zone == "CRITICAL" for zone in factor_zones.values()
        )
        warning_count = sum(
            zone == "WARNING" for zone in factor_zones.values()
        )
        return -4.0 - 1.5 * max(0, critical_count - 1) - 0.5 * warning_count

    def evaluate_crop_tick(
        self,
        crop_key: str,
        temp: float,
        humid: float,
        soil: float,
        light: float = 50.0,
        *,
        observed_at: datetime | str | None = None,
    ) -> dict:
        if crop_key not in CROP_SPECS:
            raise ValueError(f"Unknown crop: {crop_key}")

        spec = CROP_SPECS[crop_key]
        state = self.crop_states[crop_key]
        now = _utc(observed_at)
        values = {
            "temperature": float(temp),
            "humidity": float(humid),
            "soil_moisture": float(soil),
            "light_level": float(light),
        }
        factor_zones = {
            factor: self._factor_zone(values[factor], spec[factor])
            for factor in values
        }
        environment_status = self._environment_status(factor_zones)
        stress_factors = [
            f"{FACTOR_LABELS[factor]}: {zone}"
            for factor, zone in factor_zones.items()
            if zone in {"WARNING", "CRITICAL"}
        ]
        hp_rate = self._hp_rate(environment_status, factor_zones)

        previous_at = state.get("last_updated_at")
        elapsed_seconds = 0.0
        if previous_at is not None:
            elapsed_seconds = max(0.0, (now - _utc(previous_at)).total_seconds())
            elapsed_seconds = min(
                elapsed_seconds, self.max_update_gap_seconds
            )

        delta = 0.0
        if not state["is_dead"]:
            elapsed_hours = (
                elapsed_seconds / 3600.0 * self.time_scale
            )
            delta = hp_rate * elapsed_hours
            state["hp"] = max(0.0, min(100.0, state["hp"] + delta))
            if state["hp"] <= 0.0:
                state["hp"] = 0.0
                state["is_dead"] = True

        health_status = (
            "DEAD"
            if state["is_dead"]
            else self._health_status_from_hp(state["hp"])
        )
        state.update(
            {
                "hp": float(state["hp"]),
                "health_status": health_status,
                "environment_status": environment_status,
                "status_text": HEALTH_TEXT[health_status],
                "last_delta": round(delta, 4),
                "hp_rate_per_hour": round(hp_rate, 2),
                "stress_factors": stress_factors,
                "factor_zones": factor_zones,
                "last_updated_at": now,
            }
        )
        return self._public_state(crop_key)

    def update_all_crops(
        self,
        temp: float,
        humid: float,
        soil: float,
        light: float = 50.0,
        *,
        observed_at: datetime | str | None = None,
    ) -> dict:
        now = _utc(observed_at)
        for crop_key in CROP_SPECS:
            self.evaluate_crop_tick(
                crop_key,
                temp,
                humid,
                soil,
                light,
                observed_at=now,
            )
        return self.get_summary()

    def reset_crop(
        self,
        crop_key: str = "all",
        *,
        observed_at: datetime | str | None = None,
    ) -> dict:
        if crop_key != "all" and crop_key not in self.crop_states:
            raise ValueError(f"Unknown crop: {crop_key}")
        targets = self.crop_states if crop_key == "all" else (crop_key,)
        now = _utc(observed_at)
        for crop in targets:
            self.crop_states[crop] = {
                "hp": 100.0,
                "is_dead": False,
                "health_status": "HEALTHY",
                "environment_status": "UNKNOWN",
                "status_text": HEALTH_TEXT["HEALTHY"],
                "last_delta": 0.0,
                "hp_rate_per_hour": 0.0,
                "stress_factors": [],
                "factor_zones": {},
                "last_updated_at": now,
            }
        return self.get_summary()

    def _public_state(self, crop_key: str) -> dict[str, Any]:
        state = self.crop_states[crop_key]
        updated_at = state.get("last_updated_at")
        return {
            "name": CROP_SPECS[crop_key]["name"],
            "icon": CROP_SPECS[crop_key]["icon"],
            "hp": round(float(state["hp"]), 2),
            "is_dead": bool(state["is_dead"]),
            "health_status": state["health_status"],
            "environment_status": state["environment_status"],
            "status_text": state["status_text"],
            "last_delta": state["last_delta"],
            "hp_rate_per_hour": state["hp_rate_per_hour"],
            "stress_factors": list(state["stress_factors"]),
            "factor_zones": dict(state["factor_zones"]),
            "last_updated_at": (
                _utc(updated_at).isoformat() if updated_at else None
            ),
        }

    def get_summary(self) -> dict:
        return {
            crop_key: self._public_state(crop_key)
            for crop_key in CROP_SPECS
        }
