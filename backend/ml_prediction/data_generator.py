# -*- coding: utf-8 -*-
"""Generate daily synthetic weather and greenhouse observations.

Each simulated day is an independent virtual experiment with one weather type
and one slightly different set of grey-box coefficients.  The dataset is
explicitly synthetic; it is intended to demonstrate and test the Digital Twin
pipeline until real greenhouse measurements become available.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import math
import os
import sys

import numpy as np
import pandas as pd


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(BASE_DIR)
PROJECT_DIR = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from pbm_engine import (  # noqa: E402
    BoundaryConditions,
    GreenhouseParameters,
    GreenhouseState,
    PHYSICS_MODEL_VERSION,
    ProcessBasedModelEngine,
)


DEFAULT_DAYS = 25
INTERVAL_MINUTES = 5
STEPS_PER_DAY = 24 * 60 // INTERVAL_MINUTES
DEFAULT_ROWS = DEFAULT_DAYS * STEPS_PER_DAY
DEFAULT_EPISODES = DEFAULT_DAYS
SEED = 42

WEATHER_NAMES = (
    "Hot_Sunny",
    "Normal",
    "Cloudy",
    "Cool_Humid",
)
WEATHER_TRANSITION = np.array(
    [
        [0.45, 0.35, 0.15, 0.05],
        [0.20, 0.45, 0.25, 0.10],
        [0.08, 0.35, 0.40, 0.17],
        [0.05, 0.30, 0.35, 0.30],
    ]
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _randomised_parameters(
    rng: np.random.Generator,
) -> tuple[GreenhouseParameters, dict]:
    """Create one plausible coefficient set for a complete simulated day."""
    nominal = GreenhouseParameters()
    scales = {
        "solar_gain_scale": rng.uniform(0.88, 1.12),
        "temperature_exchange_scale": rng.uniform(0.85, 1.15),
        "fan_exchange_scale": rng.uniform(0.88, 1.12),
        "humidity_response_scale": rng.uniform(0.85, 1.15),
        "pump_soil_scale": rng.uniform(0.90, 1.10),
        "drying_scale": rng.uniform(0.85, 1.15),
    }
    parameters = replace(
        nominal,
        solar_temperature_gain_c_per_step=(
            nominal.solar_temperature_gain_c_per_step
            * scales["solar_gain_scale"]
        ),
        passive_temperature_exchange_per_step=(
            nominal.passive_temperature_exchange_per_step
            * scales["temperature_exchange_scale"]
        ),
        fan_temperature_exchange_per_step=(
            nominal.fan_temperature_exchange_per_step
            * scales["fan_exchange_scale"]
        ),
        plant_humidity_gain_pct_per_step=(
            nominal.plant_humidity_gain_pct_per_step
            * scales["humidity_response_scale"]
        ),
        passive_humidity_exchange_per_step=(
            nominal.passive_humidity_exchange_per_step
            * scales["humidity_response_scale"]
        ),
        fan_humidity_exchange_per_step=(
            nominal.fan_humidity_exchange_per_step
            * scales["humidity_response_scale"]
        ),
        pump_soil_gain_pct_per_step=(
            nominal.pump_soil_gain_pct_per_step
            * scales["pump_soil_scale"]
        ),
        base_soil_dry_loss_pct_per_step=(
            nominal.base_soil_dry_loss_pct_per_step
            * scales["drying_scale"]
        ),
        light_soil_dry_loss_pct_per_step=(
            nominal.light_soil_dry_loss_pct_per_step
            * scales["drying_scale"]
        ),
    )
    return parameters, scales


def _new_daily_weather(
    previous_state: int,
    rng: np.random.Generator,
    forced_state: int | None = None,
) -> tuple[int, dict]:
    """Choose a weather class and sample one coherent daily profile."""
    state = (
        int(forced_state)
        if forced_state is not None
        else int(rng.choice(4, p=WEATHER_TRANSITION[previous_state]))
    )
    if state == 0:
        daily = {
            "solar_factor": rng.uniform(0.90, 1.05),
            "mean_temp": rng.uniform(29.0, 31.0),
            "temp_amplitude": rng.uniform(5.0, 6.5),
            "mean_humidity": rng.uniform(58.0, 67.0),
            "humidity_amplitude": rng.uniform(13.0, 18.0),
            "wind": rng.uniform(0.5, 1.4),
        }
    elif state == 1:
        daily = {
            "solar_factor": rng.uniform(0.68, 0.86),
            "mean_temp": rng.uniform(26.0, 28.5),
            "temp_amplitude": rng.uniform(3.8, 5.2),
            "mean_humidity": rng.uniform(66.0, 76.0),
            "humidity_amplitude": rng.uniform(9.0, 14.0),
            "wind": rng.uniform(0.7, 1.7),
        }
    elif state == 2:
        daily = {
            "solar_factor": rng.uniform(0.35, 0.62),
            "mean_temp": rng.uniform(24.5, 27.0),
            "temp_amplitude": rng.uniform(2.5, 4.0),
            "mean_humidity": rng.uniform(74.0, 84.0),
            "humidity_amplitude": rng.uniform(6.0, 10.0),
            "wind": rng.uniform(0.9, 2.1),
        }
    else:
        daily = {
            "solar_factor": rng.uniform(0.18, 0.40),
            "mean_temp": rng.uniform(22.5, 25.0),
            "temp_amplitude": rng.uniform(1.8, 3.0),
            "mean_humidity": rng.uniform(82.0, 90.0),
            "humidity_amplitude": rng.uniform(3.0, 7.0),
            "wind": rng.uniform(1.0, 2.4),
        }
    return state, daily


def _boundary_for_time(
    timestamp: datetime,
    daily: dict,
    cloud_memory: float,
    rng: np.random.Generator,
) -> tuple[BoundaryConditions, float]:
    """Return correlated outdoor temperature, humidity, light and wind."""
    hour = timestamp.hour + timestamp.minute / 60.0
    daylight = (
        max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))
        if 6.0 <= hour <= 18.0
        else 0.0
    )
    cloud_memory = _clamp(
        0.94 * cloud_memory + 0.06 * rng.normal(1.0, 0.14),
        0.60,
        1.25,
    )
    solar = (
        950.0
        * daylight
        * daily["solar_factor"]
        * cloud_memory
    )

    # Maximum temperature is near 14:00; minimum is near 02:00.
    diurnal = math.sin(2.0 * math.pi * (hour - 8.0) / 24.0)
    outdoor_temperature = (
        daily["mean_temp"]
        + daily["temp_amplitude"] * diurnal
        + rng.normal(0.0, 0.10)
    )
    # Outdoor RH normally moves in the opposite direction to temperature.
    outdoor_humidity = (
        daily["mean_humidity"]
        - daily["humidity_amplitude"] * diurnal
        + rng.normal(0.0, 0.35)
    )
    wind = max(0.1, daily["wind"] + rng.normal(0.0, 0.12))

    return (
        BoundaryConditions(
            outdoor_temperature_c=outdoor_temperature,
            outdoor_relative_humidity_pct=_clamp(
                outdoor_humidity, 30.0, 98.0
            ),
            solar_radiation_w_per_m2=max(0.0, solar),
            wind_speed_m_per_s=wind,
        ),
        cloud_memory,
    )


def _sensor_observation(
    state: GreenhouseState,
    sensor_bias: dict,
    rng: np.random.Generator,
) -> dict:
    """Add small measurement error only to the recorded indoor values."""
    return {
        "Ambient_Temperature": round(
            _clamp(
                state.temperature_c
                + sensor_bias["temperature"]
                + rng.normal(0.0, 0.15),
                5.0,
                55.0,
            ),
            2,
        ),
        "Humidity": round(
            _clamp(
                state.relative_humidity_pct
                + sensor_bias["humidity"]
                + rng.normal(0.0, 0.40),
                5.0,
                100.0,
            ),
            2,
        ),
        "Soil_Moisture": round(
            _clamp(
                state.soil_moisture_pct
                + sensor_bias["soil"]
                + rng.normal(0.0, 0.35),
                0.0,
                100.0,
            ),
            2,
        ),
        "Light_Intensity": round(
            _clamp(
                state.light_level_pct
                + sensor_bias["light"]
                + rng.normal(0.0, 0.80),
                0.0,
                100.0,
            ),
            2,
        ),
    }


def _controller_step(
    state: GreenhouseState,
    fan_latch: bool,
    pump_latch: bool,
    hour: float,
) -> tuple[bool, bool, int]:
    """Simple hysteresis policy used only to create realistic actions."""
    if state.temperature_c > 30.0:
        fan_latch = True
    elif state.temperature_c < 27.5:
        fan_latch = False

    if state.soil_moisture_pct < 45.0:
        pump_latch = True
    elif state.soil_moisture_pct > 67.0:
        pump_latch = False

    if state.temperature_c > 32.0 or state.light_level_pct > 85.0:
        servo = 90
    elif state.light_level_pct > 60.0 and 10.0 <= hour <= 16.0:
        servo = 45
    else:
        servo = 0
    return fan_latch, pump_latch, servo


def _start_excitation(
    normal_action: tuple[bool, bool, int],
    soil_moisture_pct: float,
    rng: np.random.Generator,
) -> tuple[tuple[bool, bool, int], str]:
    """Safely change one actuator so the model observes its effect."""
    fan, pump, servo = normal_action
    actuator = str(rng.choice(("fan", "pump", "servo")))
    if actuator == "fan":
        fan = not fan
    elif actuator == "pump":
        pump = (not pump) if soil_moisture_pct < 82.0 else False
    else:
        alternatives = [angle for angle in (0, 45, 90) if angle != servo]
        servo = int(rng.choice(alternatives))
    return (fan, pump, servo), f"excitation_{actuator}"


def generate_simulated_dataset(
    num_days: int = DEFAULT_DAYS,
    start_date_str: str = "2024-01-01 00:00:00",
    seed: int = SEED,
) -> pd.DataFrame:
    """Generate complete five-minute days; one day equals one episode."""
    num_days = int(num_days)
    if num_days < 4:
        raise ValueError("num_days must be at least 4")

    rng = np.random.default_rng(seed)
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d %H:%M:%S")
    rows: list[dict] = []
    previous_weather_state = 1

    for day_index in range(num_days):
        current_time = start_date + timedelta(days=day_index)
        parameters, scales = _randomised_parameters(rng)
        model = ProcessBasedModelEngine(parameters)

        # Guarantee that every dataset contains all four weather classes.
        forced_state = day_index if day_index < len(WEATHER_NAMES) else None
        weather_state, daily = _new_daily_weather(
            previous_weather_state,
            rng,
            forced_state=forced_state,
        )
        previous_weather_state = weather_state
        cloud_memory = 1.0
        boundary, cloud_memory = _boundary_for_time(
            current_time, daily, cloud_memory, rng
        )
        state = GreenhouseState(
            temperature_c=(
                boundary.outdoor_temperature_c + rng.uniform(0.5, 2.0)
            ),
            relative_humidity_pct=_clamp(
                boundary.outdoor_relative_humidity_pct
                + rng.uniform(-4.0, 4.0),
                40.0,
                95.0,
            ),
            soil_moisture_pct=rng.uniform(42.0, 76.0),
            light_level_pct=0.0,
        )

        fan_latch = False
        pump_latch = False
        previous_servo = 0
        excitation_left = 0
        excitation_action = (False, False, 0)
        excitation_label = "controller"
        sensor_bias = {
            "temperature": rng.normal(0.0, 0.12),
            "humidity": rng.normal(0.0, 0.35),
            "soil": rng.normal(0.0, 0.30),
            "light": rng.normal(0.0, 0.45),
        }

        for _ in range(STEPS_PER_DAY):
            boundary, cloud_memory = _boundary_for_time(
                current_time,
                daily,
                cloud_memory,
                rng,
            )
            shade = model.shade_fraction(previous_servo)
            light_pct = (
                boundary.solar_radiation_w_per_m2
                * parameters.cover_transmissivity
                * (1.0 - shade)
                / parameters.solar_reference_w_per_m2
            )
            state = replace(
                state,
                light_level_pct=_clamp(light_pct, 0.0, 100.0),
            )

            hour = current_time.hour + current_time.minute / 60.0
            fan_latch, pump_latch, normal_servo = _controller_step(
                state, fan_latch, pump_latch, hour
            )
            normal_action = (fan_latch, pump_latch, normal_servo)

            if excitation_left > 0:
                applied_action = excitation_action
                control_mode = excitation_label
                excitation_left -= 1
            elif rng.random() < 0.04:
                excitation_action, excitation_label = _start_excitation(
                    normal_action, state.soil_moisture_pct, rng
                )
                excitation_left = int(rng.integers(1, 5))
                applied_action = excitation_action
                control_mode = excitation_label
            else:
                applied_action = normal_action
                control_mode = "controller"

            fan, pump, servo = applied_action
            observation = _sensor_observation(state, sensor_bias, rng)
            next_state, diagnostics = model.advance(
                state,
                boundary,
                fan,
                pump,
                servo,
                dt_seconds=INTERVAL_MINUTES * 60,
            )

            rows.append(
                {
                    "Timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "Day_ID": day_index + 1,
                    "Simulation_Date": current_time.strftime("%Y-%m-%d"),
                    **observation,
                    "Fan_Status": int(fan),
                    "Pump_Status": int(pump),
                    "Servo_Angle": int(servo),
                    "Weather_State": WEATHER_NAMES[weather_state],
                    "Outdoor_Temperature": round(
                        boundary.outdoor_temperature_c, 3
                    ),
                    "Outdoor_Humidity": round(
                        boundary.outdoor_relative_humidity_pct, 3
                    ),
                    "Solar_Radiation_W_m2": round(
                        boundary.solar_radiation_w_per_m2, 3
                    ),
                    "Wind_Speed_m_s": round(
                        boundary.wind_speed_m_per_s, 3
                    ),
                    "Control_Mode": control_mode,
                    "Episode_ID": day_index + 1,
                    "Parameter_Set_ID": day_index + 1,
                    "Physics_Model_Version": PHYSICS_MODEL_VERSION,
                    "Airflow_m3_s": diagnostics["airflow_m3_per_s"],
                    "Soil_Dry_Loss_pct_5min": diagnostics[
                        "soil_dry_loss_pct"
                    ],
                    **{
                        name.replace("_scale", "").title().replace("_", "")
                        + "_Scale": round(value, 5)
                        for name, value in scales.items()
                    },
                }
            )

            state = replace(
                next_state,
                temperature_c=_clamp(
                    next_state.temperature_c + rng.normal(0.0, 0.03),
                    5.0,
                    55.0,
                ),
                relative_humidity_pct=_clamp(
                    next_state.relative_humidity_pct
                    + rng.normal(0.0, 0.10),
                    5.0,
                    100.0,
                ),
                soil_moisture_pct=_clamp(
                    next_state.soil_moisture_pct
                    + rng.normal(0.0, 0.03),
                    0.0,
                    100.0,
                ),
            )
            previous_servo = servo
            current_time += timedelta(minutes=INTERVAL_MINUTES)

    return pd.DataFrame(rows)


def dataset_quality_report(df: pd.DataFrame) -> dict:
    actuator_counts = {
        "fan_on": int(df["Fan_Status"].sum()),
        "pump_on": int(df["Pump_Status"].sum()),
        "servo_0": int((df["Servo_Angle"] == 0).sum()),
        "servo_45": int((df["Servo_Angle"] == 45).sum()),
        "servo_90": int((df["Servo_Angle"] == 90).sum()),
        "excitation_rows": int(
            df["Control_Mode"].str.startswith("excitation").sum()
        ),
    }
    bounds_ok = bool(
        df["Humidity"].between(0, 100).all()
        and df["Soil_Moisture"].between(0, 100).all()
        and df["Light_Intensity"].between(0, 100).all()
        and df["Outdoor_Humidity"].between(0, 100).all()
    )
    return {
        "rows": int(len(df)),
        "days": int(df["Day_ID"].nunique()),
        "weather_types": sorted(df["Weather_State"].unique().tolist()),
        "physics_model_version": str(
            df["Physics_Model_Version"].iloc[0]
        ),
        "bounds_ok": bounds_ok,
        "actuator_counts": actuator_counts,
    }


if __name__ == "__main__":
    output_path = os.path.join(
        PROJECT_DIR, "simulated_greenhouse_dataset.csv"
    )
    dataset = generate_simulated_dataset()
    dataset.to_csv(output_path, index=False)
    print(f"Generated {len(dataset)} rows: {output_path}")
    print(dataset_quality_report(dataset))
    print(
        dataset.groupby("Weather_State")[
            [
                "Outdoor_Temperature",
                "Outdoor_Humidity",
                "Solar_Radiation_W_m2",
            ]
        ].mean().round(2)
    )
