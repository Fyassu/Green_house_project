# -*- coding: utf-8 -*-
"""Compatibility wrapper around the authoritative process-balance model.

New code should import :class:`pbm_engine.ProcessBasedModelEngine` directly.
This class remains because older scenario scripts call ``apply_physics_step``.
It no longer contains a second, contradictory set of per-tick constants.
"""

from __future__ import annotations

from pbm_engine import GreenhouseState, ProcessBasedModelEngine


class ActuatorPhysicsEngine:
    def __init__(self):
        self.process_model = ProcessBasedModelEngine()

    def apply_physics_step(
        self,
        current_state: dict,
        fan_status: bool,
        pump_status: bool,
        servo_angle: int,
        steps: int = 1,
    ) -> dict:
        """Project ``steps`` five-minute intervals with the balance model."""
        state = GreenhouseState.from_mapping(current_state)
        trace = self.process_model.forecast(
            state,
            bool(fan_status),
            bool(pump_status),
            int(servo_angle),
            steps=max(1, int(steps)),
        )
        point = trace[-1]
        return {
            "temperature": round(point["temperature"], 2),
            "humidity": round(point["humidity"], 2),
            "soil_moisture": round(point["soil_moisture"], 2),
            "light_level": round(point["light_level"], 2),
        }

    def calculate_actuator_deltas(
        self,
        fan_status: bool,
        pump_status: bool,
        servo_angle: int,
        current_state: dict | None = None,
    ) -> dict:
        """Return a local five-minute delta at an explicit reference state.

        The optional state was added because an actuator does not have a
        context-free physical delta. The legacy default is documented and only
        supports old diagnostic callers.
        """
        reference = current_state or {
            "temperature": 25.0,
            "humidity": 65.0,
            "soil_moisture": 60.0,
            "light_level": 50.0,
        }
        projected = self.apply_physics_step(
            reference,
            fan_status,
            pump_status,
            servo_angle,
            steps=1,
        )
        return {
            "delta_temp": round(
                projected["temperature"] - float(reference["temperature"]),
                4,
            ),
            "delta_humid": round(
                projected["humidity"] - float(reference["humidity"]),
                4,
            ),
            "delta_soil": round(
                projected["soil_moisture"]
                - float(reference["soil_moisture"]),
                4,
            ),
            "light_pct": projected["light_level"],
        }
