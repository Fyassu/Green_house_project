# -*- coding: utf-8 -*-
"""Simple grey-box model for the greenhouse Digital Twin.

The goal of this model is not to reproduce every heat and moisture mechanism
inside a real greenhouse.  It keeps only four relationships that are easy to
explain in a course presentation:

* sunlight and outdoor air change indoor temperature;
* plants, irrigation and outdoor air change indoor relative humidity;
* irrigation adds soil water while drying removes it;
* the external shade reduces incoming light.

All coefficients are nominal project assumptions.  They must be calibrated
with real sensor data before the model is used outside the classroom demo.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


PHYSICS_MODEL_VERSION = "greenhouse-greybox-v1.0"
TARGET_ORDER = ("temperature", "humidity", "soil_moisture", "light_level")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class GreenhouseParameters:
    """Nominal coefficients for one five-minute model step.

    The names describe the direction of each effect.  These are calibration
    coefficients, not measured constants or universal greenhouse values.
    """

    # Light model.
    cover_transmissivity: float = 0.72
    max_shade_fraction: float = 0.70
    solar_reference_w_per_m2: float = 10.0

    # Temperature model [degC per five-minute step].
    solar_temperature_gain_c_per_step: float = 0.18
    passive_temperature_exchange_per_step: float = 0.015
    fan_temperature_exchange_per_step: float = 0.060

    # Relative-humidity model [percentage points per five-minute step].
    plant_humidity_gain_pct_per_step: float = 0.10
    pump_humidity_gain_pct_per_step: float = 0.08
    passive_humidity_exchange_per_step: float = 0.010
    fan_humidity_exchange_per_step: float = 0.060

    # Soil-moisture model [percentage points per five-minute step].
    pump_soil_gain_pct_per_step: float = 2.00
    base_soil_dry_loss_pct_per_step: float = 0.04
    light_soil_dry_loss_pct_per_step: float = 0.12

    # Only retained for a readable airflow diagnostic.
    natural_airflow_m3_per_s: float = 0.018
    fan_airflow_m3_per_s: float = 0.30


@dataclass(frozen=True)
class BoundaryConditions:
    """Outdoor forcing assumed constant during a short forecast."""

    outdoor_temperature_c: float
    outdoor_relative_humidity_pct: float
    solar_radiation_w_per_m2: float
    wind_speed_m_per_s: float = 0.8


@dataclass(frozen=True)
class GreenhouseState:
    temperature_c: float
    relative_humidity_pct: float
    soil_moisture_pct: float
    light_level_pct: float

    @classmethod
    def from_mapping(cls, state: dict) -> "GreenhouseState":
        return cls(
            temperature_c=float(state.get("temperature", 25.0)),
            relative_humidity_pct=float(state.get("humidity", 65.0)),
            soil_moisture_pct=float(state.get("soil_moisture", 60.0)),
            light_level_pct=float(state.get("light_level", 50.0)),
        )

    def as_observation(self) -> dict:
        return {
            "temperature": round(self.temperature_c, 3),
            "humidity": round(self.relative_humidity_pct, 3),
            "soil_moisture": round(self.soil_moisture_pct, 3),
            "light_level": round(self.light_level_pct, 3),
        }


class ProcessBasedModelEngine:
    """Four-state first-order grey-box model."""

    def __init__(self, parameters: GreenhouseParameters | None = None):
        self.parameters = parameters or GreenhouseParameters()

    @staticmethod
    def shade_deployment_fraction(servo_angle: int | float) -> float:
        """0 degrees means no shade; 90 degrees means fully deployed."""
        return _clamp(float(servo_angle), 0.0, 90.0) / 90.0

    def shade_fraction(self, servo_angle: int | float) -> float:
        """Fraction of incoming radiation blocked by the external shade."""
        return (
            self.parameters.max_shade_fraction
            * self.shade_deployment_fraction(servo_angle)
        )

    def compute_cs(self, servo_angle: int | float) -> float:
        """Backward-compatible alias used by older project code."""
        return self.shade_fraction(servo_angle)

    def ventilation_flow_m3_per_s(
        self,
        fan: bool,
        servo_angle: int | float,
        wind_speed_m_per_s: float,
    ) -> float:
        """Diagnostic airflow; the shade never opens the greenhouse shell."""
        del servo_angle
        wind_factor = _clamp(
            0.75 + 0.25 * float(wind_speed_m_per_s),
            0.50,
            1.50,
        )
        natural = self.parameters.natural_airflow_m3_per_s * wind_factor
        forced = self.parameters.fan_airflow_m3_per_s if fan else 0.0
        return natural + forced

    def estimate_boundary_from_observation(
        self,
        state: GreenhouseState,
        servo_angle: int | float,
    ) -> BoundaryConditions:
        """Fallback when the deployment has no outdoor weather sensor.

        Synthetic training data contains explicit outdoor measurements.  The
        running dashboard currently does not, so this transparent approximation
        reconstructs a plausible short-term boundary from the latest indoor
        reading.  It is marked as a fallback in API responses.
        """
        p = self.parameters
        transmission = p.cover_transmissivity * (
            1.0 - self.shade_fraction(servo_angle)
        )
        solar = (
            _clamp(state.light_level_pct, 0.0, 100.0)
            * p.solar_reference_w_per_m2
            / max(transmission, 0.15)
        )
        solar = _clamp(solar, 0.0, 1050.0)
        daylight_fraction = solar / 1050.0
        outdoor_temp = state.temperature_c - (
            0.5 + 2.5 * daylight_fraction
        )
        outdoor_humidity = _clamp(
            state.relative_humidity_pct
            + 5.0
            - 10.0 * daylight_fraction,
            30.0,
            98.0,
        )
        return BoundaryConditions(
            outdoor_temperature_c=outdoor_temp,
            outdoor_relative_humidity_pct=outdoor_humidity,
            solar_radiation_w_per_m2=solar,
            wind_speed_m_per_s=0.8,
        )

    def advance(
        self,
        state: GreenhouseState,
        boundary: BoundaryConditions,
        fan: bool,
        pump: bool,
        servo_angle: int | float,
        dt_seconds: float = 300.0,
        integration_substep_s: float = 60.0,
    ) -> tuple[GreenhouseState, dict]:
        """Advance the model by ``dt_seconds`` and return readable diagnostics."""
        del integration_substep_s
        p = self.parameters
        step_scale = max(0.0, float(dt_seconds)) / 300.0

        shade = self.shade_fraction(servo_angle)
        solar_inside = (
            max(0.0, boundary.solar_radiation_w_per_m2)
            * p.cover_transmissivity
            * (1.0 - shade)
        )
        light_pct = _clamp(
            solar_inside / p.solar_reference_w_per_m2,
            0.0,
            100.0,
        )

        wind_factor = _clamp(
            0.75 + 0.25 * boundary.wind_speed_m_per_s,
            0.50,
            1.50,
        )
        temperature_exchange_rate = (
            p.passive_temperature_exchange_per_step * wind_factor
            + (p.fan_temperature_exchange_per_step if fan else 0.0)
        )
        solar_temperature_gain = (
            p.solar_temperature_gain_c_per_step * light_pct / 100.0
        )
        temperature_exchange = temperature_exchange_rate * (
            boundary.outdoor_temperature_c - state.temperature_c
        )
        next_temperature = state.temperature_c + step_scale * (
            solar_temperature_gain + temperature_exchange
        )

        soil_factor = _clamp(state.soil_moisture_pct / 50.0, 0.20, 1.0)
        plant_humidity_gain = (
            p.plant_humidity_gain_pct_per_step
            * light_pct
            / 100.0
            * soil_factor
        )
        pump_humidity_gain = (
            p.pump_humidity_gain_pct_per_step if pump else 0.0
        )
        humidity_exchange_rate = (
            p.passive_humidity_exchange_per_step * wind_factor
            + (p.fan_humidity_exchange_per_step if fan else 0.0)
        )
        humidity_exchange = humidity_exchange_rate * (
            boundary.outdoor_relative_humidity_pct
            - state.relative_humidity_pct
        )
        next_humidity = state.relative_humidity_pct + step_scale * (
            plant_humidity_gain
            + pump_humidity_gain
            + humidity_exchange
        )

        irrigation_gain = (
            p.pump_soil_gain_pct_per_step if pump else 0.0
        )
        drying_loss = (
            p.base_soil_dry_loss_pct_per_step
            + p.light_soil_dry_loss_pct_per_step * light_pct / 100.0
        )
        next_soil = state.soil_moisture_pct + step_scale * (
            irrigation_gain - drying_loss
        )

        next_state = GreenhouseState(
            temperature_c=_clamp(next_temperature, 5.0, 55.0),
            relative_humidity_pct=_clamp(next_humidity, 5.0, 100.0),
            soil_moisture_pct=_clamp(next_soil, 0.0, 100.0),
            light_level_pct=light_pct,
        )
        diagnostics = {
            "solar_temperature_gain_c": round(
                solar_temperature_gain * step_scale, 6
            ),
            "temperature_exchange_c": round(
                temperature_exchange * step_scale, 6
            ),
            "plant_humidity_gain_pct": round(
                plant_humidity_gain * step_scale, 6
            ),
            "pump_humidity_gain_pct": round(
                pump_humidity_gain * step_scale, 6
            ),
            "humidity_exchange_pct": round(
                humidity_exchange * step_scale, 6
            ),
            "soil_irrigation_gain_pct": round(
                irrigation_gain * step_scale, 6
            ),
            "soil_dry_loss_pct": round(drying_loss * step_scale, 6),
            "airflow_m3_per_s": round(
                self.ventilation_flow_m3_per_s(
                    fan, servo_angle, boundary.wind_speed_m_per_s
                ),
                6,
            ),
            "shade_deployment_fraction": round(
                self.shade_deployment_fraction(servo_angle), 6
            ),
            "shade_fraction": round(shade, 6),
            "solar_inside_w_per_m2": round(solar_inside, 3),
        }
        return next_state, diagnostics

    def forecast(
        self,
        state: GreenhouseState,
        fan: bool,
        pump: bool,
        servo_angle: int | float,
        steps: int = 6,
        dt_seconds: float = 300.0,
        boundary: BoundaryConditions | None = None,
    ) -> list[dict]:
        """Roll out fixed actuators and a persistent outdoor boundary."""
        forcing = boundary or self.estimate_boundary_from_observation(
            state, servo_angle
        )
        current = state
        result = []
        for step_index in range(max(0, int(steps))):
            current, diagnostics = self.advance(
                current,
                forcing,
                bool(fan),
                bool(pump),
                servo_angle,
                dt_seconds=dt_seconds,
            )
            point = current.as_observation()
            point["minutes"] = int((step_index + 1) * dt_seconds / 60.0)
            point["diagnostics"] = diagnostics
            result.append(point)
        return result

    def forecast_vector(
        self,
        state: GreenhouseState,
        fan: bool,
        pump: bool,
        servo_angle: int | float,
        steps: int = 6,
        boundary: BoundaryConditions | None = None,
    ) -> list[float]:
        trace = self.forecast(
            state,
            fan,
            pump,
            servo_angle,
            steps=steps,
            boundary=boundary,
        )
        return [
            float(point[target])
            for point in trace
            for target in TARGET_ORDER
        ]

    def compute_pbm_step(
        self,
        temp: float,
        humid: float,
        soil: float,
        light: float,
        fan: bool,
        pump: bool,
        servo_angle: int,
    ) -> dict:
        """Compatibility API used by older project modules."""
        point = self.forecast(
            GreenhouseState(temp, humid, soil, light),
            fan,
            pump,
            servo_angle,
            steps=1,
        )[0]
        return {
            "T_phys": round(point["temperature"], 2),
            "H_phys": round(point["humidity"], 2),
            "SM_phys": round(point["soil_moisture"], 2),
        }

    def parameter_manifest(self) -> dict:
        return {
            "physics_model_version": PHYSICS_MODEL_VERSION,
            "model_type": "first_order_grey_box",
            "parameter_status": "nominal_project_assumptions",
            "parameters": asdict(self.parameters),
        }


def flatten_physics_trace(trace: Iterable[dict]) -> list[float]:
    """Flatten trace in [temperature, humidity, soil, light] order."""
    return [
        float(point[target])
        for point in trace
        for target in TARGET_ORDER
    ]
