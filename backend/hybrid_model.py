# -*- coding: utf-8 -*-
"""Runtime residual physics-guided forecasting and closed-loop checks."""

from __future__ import annotations

import json
import os
import pickle

import numpy as np

from pbm_engine import (
    BoundaryConditions,
    GreenhouseState,
    PHYSICS_MODEL_VERSION,
    TARGET_ORDER,
    ProcessBasedModelEngine,
    flatten_physics_trace,
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "ml_prediction", "models")
MODEL_PATH = os.path.join(MODELS_DIR, "greenhouse_rf_model.pkl")
SCALER_PATH = os.path.join(MODELS_DIR, "scaler.pkl")
SCALER_Y_PATH = os.path.join(MODELS_DIR, "scaler_y.pkl")
METADATA_PATH = os.path.join(MODELS_DIR, "model_metadata.json")

WINDOW_SIZE = 5
FORECAST_STEPS = 6
HISTORY_WIDTH = 11
EXPECTED_FEATURES = 79
MODEL_CONTRACT_VERSION = "residual-pgml-v4-external-weather"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


class HybridPredictionEngine:
    def __init__(self):
        self.pbm_engine = ProcessBasedModelEngine()
        self.rf_model = None
        self.scaler = None
        self.scaler_y = None
        self.metadata = None
        self.artifact_error = None
        self._load_artifacts()

    def _load_artifacts(self) -> None:
        required = (
            MODEL_PATH,
            SCALER_PATH,
            SCALER_Y_PATH,
            METADATA_PATH,
        )
        if not all(os.path.exists(path) for path in required):
            self.artifact_error = "Residual-PGML artifacts are incomplete"
            print(f"[HYBRID WARNING] {self.artifact_error}")
            return
        try:
            with open(METADATA_PATH, encoding="utf-8") as handle:
                metadata = json.load(handle)
            if metadata.get("model_contract_version") != MODEL_CONTRACT_VERSION:
                raise ValueError(
                    "model contract mismatch: "
                    f"{metadata.get('model_contract_version')}"
                )
            if metadata.get("physics_model_version") != PHYSICS_MODEL_VERSION:
                raise ValueError(
                    "physics model mismatch: "
                    f"{metadata.get('physics_model_version')}"
                )
            if int(metadata.get("feature_count", -1)) != EXPECTED_FEATURES:
                raise ValueError(
                    "feature count mismatch: "
                    f"{metadata.get('feature_count')}"
                )

            with open(MODEL_PATH, "rb") as handle:
                self.rf_model = pickle.load(handle)
            with open(SCALER_PATH, "rb") as handle:
                self.scaler = pickle.load(handle)
            with open(SCALER_Y_PATH, "rb") as handle:
                self.scaler_y = pickle.load(handle)

            scaler_features = int(
                getattr(self.scaler, "n_features_in_", -1)
            )
            model_features = int(
                getattr(self.rf_model, "n_features_in_", -1)
            )
            if (
                scaler_features != EXPECTED_FEATURES
                or model_features != EXPECTED_FEATURES
            ):
                raise ValueError(
                    "artifact feature mismatch: "
                    f"scaler={scaler_features}, model={model_features}"
                )
            self.metadata = metadata
            print(
                "[HYBRID PGML] Loaded residual model "
                f"({EXPECTED_FEATURES} inputs, physics={PHYSICS_MODEL_VERSION})"
            )
        except Exception as exc:
            self.rf_model = None
            self.scaler = None
            self.scaler_y = None
            self.metadata = None
            self.artifact_error = str(exc)
            print(f"[HYBRID ERROR] {self.artifact_error}")

    def _normalise_window(
        self,
        window_matrix: list,
    ) -> list[list[float]]:
        default = [
            25.0,
            60.0,
            65.0,
            50.0,
            0.0,
            0.0,
            0.0,
            24.0,
            70.0,
            0.0,
            0.8,
        ]
        rows = []
        for source in (window_matrix or [])[-WINDOW_SIZE:]:
            supplied = len(source)
            row = list(source[:HISTORY_WIDTH])
            row += default[len(row) :]
            if supplied < HISTORY_WIDTH:
                state = GreenhouseState(
                    float(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                )
                estimated = self.pbm_engine.estimate_boundary_from_observation(
                    state, int(round(float(row[6])))
                )
                fallback = [
                    estimated.outdoor_temperature_c,
                    estimated.outdoor_relative_humidity_pct,
                    estimated.solar_radiation_w_per_m2,
                    estimated.wind_speed_m_per_s,
                ]
                for offset, value in enumerate(fallback, start=7):
                    if supplied <= offset:
                        row[offset] = value
            rows.append([float(value) for value in row])
        if not rows:
            rows = [default.copy()]
        while len(rows) < WINDOW_SIZE:
            rows.insert(0, rows[0].copy())

        return rows

    def _physical_trace(
        self,
        rows: list[list[float]],
        fan_status: bool,
        pump_status: bool,
        servo_angle: int,
        boundary: BoundaryConditions | None = None,
    ) -> list[dict]:
        latest = rows[-1]
        state = GreenhouseState(
            latest[0], latest[1], latest[2], latest[3]
        )
        return self.pbm_engine.forecast(
            state,
            bool(fan_status),
            bool(pump_status),
            int(servo_angle),
            steps=FORECAST_STEPS,
            boundary=boundary,
        )

    def predict_hybrid(
        self,
        window_matrix: list,
        fan_status: bool,
        pump_status: bool,
        servo_angle: int,
    ) -> list[dict]:
        """Forecast six 5-minute steps using physics + learned residual."""
        rows = self._normalise_window(window_matrix)
        # Residual correction is inferred from the action that was actually
        # observed at time t. For a counterfactual candidate action, that same
        # disturbance correction is held fixed and only the causal physical
        # rollout changes.
        observed = rows[-1]
        observed_boundary = BoundaryConditions(
            outdoor_temperature_c=observed[7],
            outdoor_relative_humidity_pct=observed[8],
            solar_radiation_w_per_m2=observed[9],
            wind_speed_m_per_s=observed[10],
        )
        observed_physical_trace = self._physical_trace(
            rows,
            bool(round(observed[4])),
            bool(round(observed[5])),
            int(round(observed[6])),
            boundary=observed_boundary,
        )
        model_physics_vector = np.asarray(
            flatten_physics_trace(observed_physical_trace), dtype=float
        )
        candidate_physical_trace = self._physical_trace(
            rows,
            fan_status,
            pump_status,
            servo_angle,
            boundary=observed_boundary,
        )
        candidate_physics_vector = np.asarray(
            flatten_physics_trace(candidate_physical_trace), dtype=float
        )

        if (
            self.rf_model is None
            or self.scaler is None
            or self.scaler_y is None
        ):
            return self._format_steps(
                candidate_physics_vector,
                candidate_physics_vector,
                np.zeros_like(candidate_physics_vector),
                model_used=False,
            )

        try:
            history_vector = np.asarray(rows, dtype=float).reshape(-1)
            model_input = np.concatenate(
                [history_vector, model_physics_vector]
            ).reshape(1, -1)
            if model_input.shape[1] != EXPECTED_FEATURES:
                raise ValueError(
                    f"Expected {EXPECTED_FEATURES} inputs, "
                    f"got {model_input.shape[1]}"
                )
            scaled_input = self.scaler.transform(model_input)
            residual_scaled = self.rf_model.predict(scaled_input)
            residual = self.scaler_y.inverse_transform(
                residual_scaled
            )[0]
            # The residual model was learned locally around the observed
            # action. For a counterfactual action that moves the physical state
            # far from that anchor, exponentially attenuate the correction
            # instead of extrapolating a large additive RH/soil bias.
            trust_scales = np.tile(
                np.asarray([3.0, 15.0, 12.0, 25.0]), FORECAST_STEPS
            )
            residual_trust = np.exp(
                -np.abs(
                    candidate_physics_vector - model_physics_vector
                )
                / trust_scales
            )
            applied_residual = residual * residual_trust
            final_prediction = (
                candidate_physics_vector + applied_residual
            )
            return self._format_steps(
                final_prediction,
                candidate_physics_vector,
                applied_residual,
                model_used=True,
            )
        except Exception as exc:
            print(
                f"[HYBRID EXCEPTION] {exc}; using physics-only forecast"
            )
            return self._format_steps(
                candidate_physics_vector,
                candidate_physics_vector,
                np.zeros_like(candidate_physics_vector),
                model_used=False,
            )

    @staticmethod
    def _format_steps(
        final_vector: np.ndarray,
        physics_vector: np.ndarray,
        residual_vector: np.ndarray,
        model_used: bool,
    ) -> list[dict]:
        limits = {
            "temperature": (5.0, 55.0),
            "humidity": (0.0, 100.0),
            "soil_moisture": (0.0, 100.0),
            "light_level": (0.0, 100.0),
        }
        steps = []
        for step_index in range(FORECAST_STEPS):
            offset = step_index * len(TARGET_ORDER)
            final = {}
            physics = {}
            residual = {}
            for variable_index, name in enumerate(TARGET_ORDER):
                index = offset + variable_index
                low, high = limits[name]
                final[name] = round(
                    _clamp(final_vector[index], low, high), 2
                )
                physics[name] = round(
                    _clamp(physics_vector[index], low, high), 2
                )
                residual[name] = round(float(residual_vector[index]), 3)
            minutes = (step_index + 1) * 5
            steps.append(
                {
                    "step": f"+{minutes}m",
                    "minutes": minutes,
                    **final,
                    "physics_baseline": physics,
                    "residual_correction": residual,
                    "model_used": (
                        "residual_pgml" if model_used else "physics_only"
                    ),
                    "physics_model_version": PHYSICS_MODEL_VERSION,
                }
            )
        return steps

    def check_predictive_closed_loop(
        self, hybrid_steps: list
    ) -> dict | None:
        """Return proposed actuator overrides from the +15 minute forecast."""
        if not hybrid_steps or len(hybrid_steps) < 3:
            return None
        t15 = hybrid_steps[2]
        pred_temp = float(t15["temperature"])
        pred_soil = float(t15["soil_moisture"])

        interventions = []
        commands = {}
        if pred_temp >= 34.0:
            interventions.append(
                f"Du bao +15 phut: nhiet do {pred_temp:.1f} degC "
                "-> bat quat va che nang"
            )
            commands["fan"] = True
            commands["servo"] = 90
        if pred_soil <= 50.0:
            interventions.append(
                f"Du bao +15 phut: do am dat {pred_soil:.1f}% "
                "-> bat bom"
            )
            commands["pump"] = True
        if not commands:
            return None
        return {
            "triggered": True,
            "commands": commands,
            "messages": interventions,
            "prediction": {
                "temperature": round(pred_temp, 2),
                "soil_moisture": round(pred_soil, 2),
            },
            "decision_horizon_minutes": 15,
        }
