"""Create a repeatable one-day demo trace with the current physics engine.

Run from backend/:
    python seed_demo_data.py

Use ``--replace`` only when old telemetry may safely be removed:
    python seed_demo_data.py --replace
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import datetime, timedelta

import mysql.connector

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_USER
from model import ACTUATOR_RULES
from pbm_engine import (
    BoundaryConditions,
    GreenhouseState,
    ProcessBasedModelEngine,
)


RANDOM_SEED = 20260726
ROW_COUNT = 400
DISPLAY_STEP_SECONDS = 0.833
PHYSICS_STEP_SECONDS = 300


def build_rows() -> list[tuple]:
    random.seed(RANDOM_SEED)
    engine = ProcessBasedModelEngine()
    state = GreenhouseState(21.0, 65.0, 60.0, 15.0)
    fan_on = False
    pump_on = False
    servo = 0

    now = datetime.now()
    first_timestamp = now - timedelta(
        seconds=(ROW_COUNT - 1) * DISPLAY_STEP_SECONDS
    )
    rows = []

    for index in range(ROW_COUNT):
        fraction = index / (ROW_COUNT - 1)
        daylight_curve = max(0.0, math.sin(math.pi * fraction))
        solar_w_m2 = 80.0 + 820.0 * daylight_curve
        outdoor_temp = 20.0 + 12.0 * daylight_curve
        outdoor_rh = 78.0 - 35.0 * daylight_curve

        if state.temperature_c > ACTUATOR_RULES["fan"]["on_above"]:
            fan_on = True
        elif state.temperature_c < ACTUATOR_RULES["fan"]["off_below"]:
            fan_on = False

        if state.soil_moisture_pct < ACTUATOR_RULES["pump"]["on_below"]:
            pump_on = True
        elif state.soil_moisture_pct > ACTUATOR_RULES["pump"]["off_above"]:
            pump_on = False

        light_pct = min(100.0, solar_w_m2 * 0.68 / 10.0)
        simulated_hour = 6.0 + 12.0 * fraction
        shade_rules = ACTUATOR_RULES["shade"]
        if (
            state.temperature_c
            > shade_rules["full_deploy_temp_above"]
            or light_pct > shade_rules["full_deploy_light_min"]
        ):
            servo = 90
        elif (
            light_pct > shade_rules["half_deploy_light_min"]
            and shade_rules["active_hour_start"]
            <= simulated_hour
            <= shade_rules["active_hour_end"]
        ):
            servo = 45
        else:
            servo = 0

        boundary = BoundaryConditions(
            outdoor_temperature_c=outdoor_temp,
            outdoor_relative_humidity_pct=outdoor_rh,
            solar_radiation_w_per_m2=solar_w_m2,
            wind_speed_m_per_s=0.8,
        )
        state, _ = engine.advance(
            state,
            boundary,
            fan_on,
            pump_on,
            servo,
            dt_seconds=PHYSICS_STEP_SECONDS,
        )

        temperature = state.temperature_c + random.gauss(0, 0.05)
        humidity = state.relative_humidity_pct + random.gauss(0, 0.3)
        soil = max(
            0.0,
            min(100.0, state.soil_moisture_pct + random.gauss(0, 0.3)),
        )
        light = max(
            0.0,
            min(100.0, state.light_level_pct + random.gauss(0, 0.8)),
        )
        motion = random.random() < 0.03
        light_status = (
            motion
            and light < ACTUATOR_RULES["motion_light"]["trigger_below_light"]
        )

        rows.append(
            (
                round(temperature, 2),
                round(humidity, 2),
                round(soil, 2),
                round(light, 2),
                motion,
                fan_on,
                pump_on,
                servo,
                light_status,
                first_timestamp
                + timedelta(seconds=index * DISPLAY_STEP_SECONDS),
            )
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing sensor_data before inserting the demo trace.",
    )
    args = parser.parse_args()
    rows = build_rows()

    conn = mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
    )
    cur = conn.cursor()
    if args.replace:
        cur.execute("TRUNCATE TABLE sensor_data")

    cur.executemany(
        """
        INSERT INTO sensor_data (
            temperature, humidity, soil_moisture, light_level,
            motion_detected, fan_status, pump_status, servo_angle,
            light_status, created_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        rows,
    )
    inserted = cur.rowcount
    conn.commit()

    cur.execute(
        """
        SELECT MIN(temperature), MAX(temperature),
               MIN(soil_moisture), MAX(soil_moisture),
               SUM(fan_status), SUM(pump_status)
        FROM sensor_data
        """
    )
    tmin, tmax, smin, smax, fan_count, pump_count = cur.fetchone()
    cur.close()
    conn.close()

    print(f"Inserted {inserted} physics-based demo rows.")
    print(
        f"Temperature: {tmin:.1f}..{tmax:.1f} C | "
        f"Soil: {smin:.1f}..{smax:.1f}%"
    )
    print(f"Fan ON rows: {fan_count} | Pump ON rows: {pump_count}")


if __name__ == "__main__":
    main()
