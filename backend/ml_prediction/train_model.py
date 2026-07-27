# -*- coding: utf-8 -*-
"""Train and evaluate the residual physics-guided forecasting model.

Contract
--------
History input:
    5 time steps x 11 indoor/control/outdoor variables = 55 features
Physical rollout:
    6 horizons x 4 state variables = 24 features
Random-forest target:
    residual = measured_future - physical_rollout = 24 outputs
Final prediction:
    physical_rollout + predicted_residual

The held-out set contains complete simulated days that are absent from
training. This prevents nearly identical neighbouring rows from leaking into
both train and test sets.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(BASE_DIR)
PROJECT_DIR = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from pbm_engine import (  # noqa: E402
    BoundaryConditions,
    GreenhouseState,
    PHYSICS_MODEL_VERSION,
    TARGET_ORDER,
    ProcessBasedModelEngine,
)


DATASET_PATH = os.path.join(PROJECT_DIR, "simulated_greenhouse_dataset.csv")
MODELS_DIR = os.path.join(BASE_DIR, "models")
MODEL_PATH = os.path.join(MODELS_DIR, "greenhouse_rf_model.pkl")
SCALER_PATH = os.path.join(MODELS_DIR, "scaler.pkl")
SCALER_Y_PATH = os.path.join(MODELS_DIR, "scaler_y.pkl")
METADATA_PATH = os.path.join(MODELS_DIR, "model_metadata.json")
REPORT_PATH = os.path.join(MODELS_DIR, "training_report.json")

WINDOW_SIZE = 5
FORECAST_STEPS = 6
HISTORY_COLUMNS = (
    "temperature",
    "humidity",
    "soil_moisture",
    "light_level",
    "fan_status",
    "pump_status",
    "servo_angle",
    "outdoor_temperature",
    "outdoor_humidity",
    "solar_radiation",
    "wind_speed",
)
STATE_COLUMNS = TARGET_ORDER
FEATURE_COUNT = WINDOW_SIZE * len(HISTORY_COLUMNS) + (
    FORECAST_STEPS * len(STATE_COLUMNS)
)
TARGET_COUNT = FORECAST_STEPS * len(STATE_COLUMNS)
MODEL_CONTRACT_VERSION = "residual-pgml-v4-external-weather"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_and_preprocess_data(
    dataset_path: str = DATASET_PATH,
) -> pd.DataFrame:
    if not os.path.exists(dataset_path):
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    raw = pd.read_csv(dataset_path)
    required = [
        "Timestamp",
        "Ambient_Temperature",
        "Humidity",
        "Soil_Moisture",
        "Light_Intensity",
        "Fan_Status",
        "Pump_Status",
        "Servo_Angle",
        "Outdoor_Temperature",
        "Outdoor_Humidity",
        "Solar_Radiation_W_m2",
        "Wind_Speed_m_s",
        "Episode_ID",
        "Physics_Model_Version",
    ]
    missing = [column for column in required if column not in raw.columns]
    if missing:
        raise ValueError(f"Dataset is missing required columns: {missing}")

    versions = set(raw["Physics_Model_Version"].dropna().astype(str))
    if versions != {PHYSICS_MODEL_VERSION}:
        raise ValueError(
            "Physics-version mismatch. "
            f"Expected {PHYSICS_MODEL_VERSION}, found {sorted(versions)}"
        )

    df = raw[required].rename(
        columns={
            "Timestamp": "timestamp",
            "Ambient_Temperature": "temperature",
            "Humidity": "humidity",
            "Soil_Moisture": "soil_moisture",
            "Light_Intensity": "light_level",
            "Fan_Status": "fan_status",
            "Pump_Status": "pump_status",
            "Servo_Angle": "servo_angle",
            "Outdoor_Temperature": "outdoor_temperature",
            "Outdoor_Humidity": "outdoor_humidity",
            "Solar_Radiation_W_m2": "solar_radiation",
            "Wind_Speed_m_s": "wind_speed",
            "Episode_ID": "episode_id",
            "Physics_Model_Version": "physics_model_version",
        }
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df.dropna(subset=["timestamp"], inplace=True)
    df.sort_values(["episode_id", "timestamp"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    numeric_columns = [*HISTORY_COLUMNS, "episode_id"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if df[numeric_columns].isna().any().any():
        bad = df[numeric_columns].isna().sum()
        raise ValueError(
            f"Dataset contains non-numeric/missing values: "
            f"{bad[bad > 0].to_dict()}"
        )

    bounded = (
        df["humidity"].between(0, 100).all()
        and df["soil_moisture"].between(0, 100).all()
        and df["light_level"].between(0, 100).all()
    )
    if not bounded:
        raise ValueError("Dataset violates physical sensor bounds")
    return df


def _feature_names() -> list[str]:
    names = []
    for lag in range(WINDOW_SIZE - 1, -1, -1):
        names.extend(f"t-{lag}_{column}" for column in HISTORY_COLUMNS)
    for horizon in range(1, FORECAST_STEPS + 1):
        names.extend(
            f"physics_t+{horizon * 5}m_{column}"
            for column in STATE_COLUMNS
        )
    return names


def build_sliding_windows(
    df: pd.DataFrame,
    window_size: int = WINDOW_SIZE,
    forecast_steps: int = FORECAST_STEPS,
) -> dict[str, np.ndarray]:
    if window_size != WINDOW_SIZE or forecast_steps != FORECAST_STEPS:
        raise ValueError(
            "Changing window/horizon requires a new model contract version"
        )

    features = df[list(HISTORY_COLUMNS)].to_numpy(dtype=float)
    targets = df[list(STATE_COLUMNS)].to_numpy(dtype=float)
    episodes = df["episode_id"].to_numpy(dtype=int)
    pbm = ProcessBasedModelEngine()

    x_hybrid = []
    x_history = []
    y_residual = []
    y_true = []
    y_physics = []
    y_persistence = []
    sample_episode = []

    last_start = len(df) - window_size - forecast_steps + 1
    for start in range(max(0, last_start)):
        stop = start + window_size + forecast_steps
        episode_slice = episodes[start:stop]
        if np.any(episode_slice != episode_slice[0]):
            continue

        history = features[start : start + window_size]
        latest = history[-1]
        (
            temp,
            humid,
            soil,
            light,
            fan,
            pump,
            servo,
            outdoor_temp,
            outdoor_humidity,
            solar_radiation,
            wind_speed,
        ) = latest
        state = GreenhouseState(temp, humid, soil, light)
        boundary = BoundaryConditions(
            outdoor_temperature_c=outdoor_temp,
            outdoor_relative_humidity_pct=outdoor_humidity,
            solar_radiation_w_per_m2=solar_radiation,
            wind_speed_m_per_s=wind_speed,
        )
        physics_vector = np.asarray(
            pbm.forecast_vector(
                state,
                bool(round(fan)),
                bool(round(pump)),
                int(round(servo)),
                steps=forecast_steps,
                boundary=boundary,
            ),
            dtype=float,
        )
        actual_vector = targets[
            start + window_size : stop
        ].reshape(-1)
        persistence_vector = np.tile(latest[:4], forecast_steps)
        history_vector = history.reshape(-1)

        x_history.append(history_vector)
        x_hybrid.append(
            np.concatenate([history_vector, physics_vector])
        )
        y_true.append(actual_vector)
        y_physics.append(physics_vector)
        y_persistence.append(persistence_vector)
        y_residual.append(actual_vector - physics_vector)
        sample_episode.append(int(episode_slice[0]))

    arrays = {
        "X": np.asarray(x_hybrid, dtype=float),
        "X_history": np.asarray(x_history, dtype=float),
        "Y_residual": np.asarray(y_residual, dtype=float),
        "Y_true": np.asarray(y_true, dtype=float),
        "Y_physics": np.asarray(y_physics, dtype=float),
        "Y_persistence": np.asarray(y_persistence, dtype=float),
        "episode_id": np.asarray(sample_episode, dtype=int),
    }
    if arrays["X"].shape[1:] != (FEATURE_COUNT,):
        raise AssertionError(
            f"Expected {FEATURE_COUNT} input features, got "
            f"{arrays['X'].shape}"
        )
    return arrays


def _metric_bundle(actual: np.ndarray, predicted: np.ndarray) -> dict:
    return {
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
        "mae": float(mean_absolute_error(actual, predicted)),
        "r2": float(r2_score(actual, predicted)),
    }


def evaluate_forecast(
    actual: np.ndarray,
    predicted: np.ndarray,
) -> dict:
    actual_3d = actual.reshape(-1, FORECAST_STEPS, len(STATE_COLUMNS))
    predicted_3d = predicted.reshape(
        -1, FORECAST_STEPS, len(STATE_COLUMNS)
    )
    by_variable = {}
    for index, name in enumerate(STATE_COLUMNS):
        by_variable[name] = _metric_bundle(
            actual_3d[:, :, index].reshape(-1),
            predicted_3d[:, :, index].reshape(-1),
        )

    by_horizon = {}
    for horizon in range(FORECAST_STEPS):
        key = f"+{(horizon + 1) * 5}m"
        by_horizon[key] = {
            name: _metric_bundle(
                actual_3d[:, horizon, index],
                predicted_3d[:, horizon, index],
            )
            for index, name in enumerate(STATE_COLUMNS)
        }
    return {
        "by_variable_all_horizons": by_variable,
        "by_horizon": by_horizon,
    }


def _physics_sanity_checks() -> dict:
    model = ProcessBasedModelEngine()
    hot = GreenhouseState(35.0, 62.0, 55.0, 70.0)
    dry = GreenhouseState(29.0, 60.0, 40.0, 55.0)
    sunny_boundary = BoundaryConditions(29.0, 65.0, 850.0, 1.0)
    fan_off = model.forecast(
        hot, False, False, 0, steps=3, boundary=sunny_boundary
    )[-1]
    fan_on = model.forecast(
        hot, True, False, 0, steps=3, boundary=sunny_boundary
    )[-1]
    shade_off = model.forecast(
        hot, False, False, 0, steps=3, boundary=sunny_boundary
    )[-1]
    shade_full = model.forecast(
        hot, False, False, 90, steps=3, boundary=sunny_boundary
    )[-1]
    pump_off = model.forecast(dry, False, False, 0, steps=2)[-1]
    pump_on = model.forecast(dry, False, True, 0, steps=2)[-1]
    checks = {
        "fan_reduces_t15_temperature_when_outside_is_cooler": (
            fan_on["temperature"] < fan_off["temperature"]
        ),
        "full_shade_reduces_t15_temperature": (
            shade_full["temperature"] < shade_off["temperature"]
        ),
        "full_shade_reduces_t15_light": (
            shade_full["light_level"] < shade_off["light_level"]
        ),
        "pump_increases_t10_soil_moisture": (
            pump_on["soil_moisture"] > pump_off["soil_moisture"]
        ),
        "fan_off_t15_temperature_c": fan_off["temperature"],
        "fan_on_t15_temperature_c": fan_on["temperature"],
        "shade_off_t15_temperature_c": shade_off["temperature"],
        "shade_full_t15_temperature_c": shade_full["temperature"],
        "shade_off_t15_light_pct": shade_off["light_level"],
        "shade_full_t15_light_pct": shade_full["light_level"],
        "pump_off_t10_soil_pct": pump_off["soil_moisture"],
        "pump_on_t10_soil_pct": pump_on["soil_moisture"],
    }
    if not all(
        checks[key]
        for key in (
            "fan_reduces_t15_temperature_when_outside_is_cooler",
            "full_shade_reduces_t15_temperature",
            "full_shade_reduces_t15_light",
            "pump_increases_t10_soil_moisture",
        )
    ):
        raise AssertionError(f"Physics sanity check failed: {checks}")
    return checks


def train_and_evaluate(
    dataset_path: str = DATASET_PATH,
    save_artifacts: bool = True,
) -> dict:
    print(f"[1/5] Loading synthetic virtual-experiment data: {dataset_path}")
    df = load_and_preprocess_data(dataset_path)
    print(
        f"      rows={len(df)}, episodes={df['episode_id'].nunique()}, "
        f"physics={PHYSICS_MODEL_VERSION}"
    )

    print("[2/5] Building residual-PGML windows")
    arrays = build_sliding_windows(df)
    unique_episodes = np.sort(np.unique(arrays["episode_id"]))
    if len(unique_episodes) < 4:
        raise ValueError("At least four episodes are required for blocked holdout")
    # With the default 25-day dataset this yields 20 training days and the
    # final 5 complete days as an untouched chronological test set.
    test_episode_count = max(1, int(np.ceil(len(unique_episodes) * 0.20)))
    test_episodes = unique_episodes[-test_episode_count:]
    test_mask = np.isin(arrays["episode_id"], test_episodes)
    train_mask = ~test_mask
    print(
        f"      X={arrays['X'].shape}, Y={arrays['Y_residual'].shape}, "
        f"held-out episodes={test_episodes.tolist()}"
    )

    x_train = arrays["X"][train_mask]
    x_test = arrays["X"][test_mask]
    residual_train = arrays["Y_residual"][train_mask]
    y_test = arrays["Y_true"][test_mask]
    physics_test = arrays["Y_physics"][test_mask]

    scaler_x = StandardScaler().fit(x_train)
    scaler_residual = StandardScaler().fit(residual_train)
    x_train_scaled = scaler_x.transform(x_train)
    x_test_scaled = scaler_x.transform(x_test)
    residual_train_scaled = scaler_residual.transform(residual_train)

    print("[3/5] Training Random Forest on physical residuals")
    residual_model = RandomForestRegressor(
        n_estimators=120,
        max_depth=18,
        min_samples_leaf=2,
        max_features=0.80,
        random_state=42,
        n_jobs=-1,
    )
    residual_model.fit(x_train_scaled, residual_train_scaled)
    residual_pred = scaler_residual.inverse_transform(
        residual_model.predict(x_test_scaled)
    )
    hybrid_pred = physics_test + residual_pred

    # A fair data-only comparator uses the same history and tree settings.
    print("[4/5] Training data-only RF comparator and evaluating baselines")
    y_train = arrays["Y_true"][train_mask]
    history_scaler = StandardScaler().fit(
        arrays["X_history"][train_mask]
    )
    true_scaler = StandardScaler().fit(y_train)
    data_only_model = RandomForestRegressor(
        n_estimators=120,
        max_depth=18,
        min_samples_leaf=2,
        max_features=0.80,
        random_state=42,
        n_jobs=-1,
    )
    data_only_model.fit(
        history_scaler.transform(arrays["X_history"][train_mask]),
        true_scaler.transform(y_train),
    )
    data_only_pred = true_scaler.inverse_transform(
        data_only_model.predict(
            history_scaler.transform(arrays["X_history"][test_mask])
        )
    )

    metrics = {
        "persistence": evaluate_forecast(
            y_test, arrays["Y_persistence"][test_mask]
        ),
        "physics_only": evaluate_forecast(y_test, physics_test),
        "data_only_random_forest": evaluate_forecast(
            y_test, data_only_pred
        ),
        "residual_pgml": evaluate_forecast(y_test, hybrid_pred),
    }
    sanity = _physics_sanity_checks()

    report = {
        "report_version": MODEL_CONTRACT_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "classification": "synthetic_virtual_experiment",
            "path": os.path.abspath(dataset_path),
            "sha256": _sha256(dataset_path),
            "rows": int(len(df)),
            "days": int(len(unique_episodes)),
            "episodes": unique_episodes.tolist(),
            "training_episodes": unique_episodes[
                ~np.isin(unique_episodes, test_episodes)
            ].tolist(),
            "held_out_episodes": test_episodes.tolist(),
        },
        "feature_contract": {
            "window_size": WINDOW_SIZE,
            "forecast_steps": FORECAST_STEPS,
            "history_features": WINDOW_SIZE * len(HISTORY_COLUMNS),
            "physics_features": FORECAST_STEPS * len(STATE_COLUMNS),
            "total_features": FEATURE_COUNT,
            "residual_targets": TARGET_COUNT,
            "target_order": list(STATE_COLUMNS),
        },
        "metrics": metrics,
        "physics_sanity_checks": sanity,
    }

    pgml = metrics["residual_pgml"]["by_variable_all_horizons"]
    print("\nHeld-out episode metrics (all six horizons)")
    for name in STATE_COLUMNS:
        values = pgml[name]
        unit = "degC" if name == "temperature" else "%"
        print(
            f"  {name:14s} RMSE={values['rmse']:.3f} {unit:4s} "
            f"MAE={values['mae']:.3f} R2={values['r2']:.4f}"
        )
    print("  Baseline RMSE by physical variable (units are not mixed):")
    for method_name, bundle in metrics.items():
        by_var = bundle["by_variable_all_horizons"]
        print(
            f"    {method_name:24s} "
            + ", ".join(
                f"{name}={by_var[name]['rmse']:.3f}"
                for name in STATE_COLUMNS
            )
        )

    if save_artifacts:
        print("[5/5] Saving versioned artifact contract and report")
        os.makedirs(MODELS_DIR, exist_ok=True)
        with open(MODEL_PATH, "wb") as handle:
            pickle.dump(residual_model, handle)
        with open(SCALER_PATH, "wb") as handle:
            pickle.dump(scaler_x, handle)
        with open(SCALER_Y_PATH, "wb") as handle:
            pickle.dump(scaler_residual, handle)

        metadata = {
            "model_contract_version": MODEL_CONTRACT_VERSION,
            "physics_model_version": PHYSICS_MODEL_VERSION,
            "sklearn_version": sklearn.__version__,
            "model_type": "RandomForestRegressor residual correction",
            "feature_count": FEATURE_COUNT,
            "target_count": TARGET_COUNT,
            "window_size": WINDOW_SIZE,
            "forecast_steps": FORECAST_STEPS,
            "history_columns": list(HISTORY_COLUMNS),
            "target_order": list(STATE_COLUMNS),
            "feature_names": _feature_names(),
            "dataset_sha256": report["dataset"]["sha256"],
        }
        with open(METADATA_PATH, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2)
        with open(REPORT_PATH, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"      model:   {MODEL_PATH}")
        print(f"      metadata:{METADATA_PATH}")
        print(f"      report:  {REPORT_PATH}")
    return report


if __name__ == "__main__":
    train_and_evaluate()
