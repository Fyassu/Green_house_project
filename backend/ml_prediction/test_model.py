# -*- coding: utf-8 -*-
"""Artifact-contract and counterfactual smoke tests for residual PGML."""

from __future__ import annotations

import json
import os
import sys


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(BASE_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from hybrid_model import HybridPredictionEngine  # noqa: E402
from pbm_engine import PHYSICS_MODEL_VERSION  # noqa: E402
from train_model import (  # noqa: E402
    FEATURE_COUNT,
    METADATA_PATH,
    REPORT_PATH,
    TARGET_COUNT,
)


def run_inspection_and_test() -> None:
    with open(METADATA_PATH, encoding="utf-8") as handle:
        metadata = json.load(handle)
    with open(REPORT_PATH, encoding="utf-8") as handle:
        report = json.load(handle)

    assert metadata["feature_count"] == FEATURE_COUNT == 79
    assert metadata["target_count"] == TARGET_COUNT == 24
    assert metadata["physics_model_version"] == PHYSICS_MODEL_VERSION
    assert report["dataset"]["classification"] == (
        "synthetic_virtual_experiment"
    )

    engine = HybridPredictionEngine()
    assert engine.rf_model is not None, engine.artifact_error
    assert engine.scaler.n_features_in_ == FEATURE_COUNT
    assert engine.rf_model.n_features_in_ == FEATURE_COUNT

    history = [
        [31.0, 62.0, 55.0, 65.0, 0, 0, 0, 28.0, 68.0, 800.0, 1.0],
        [32.0, 60.0, 54.8, 68.0, 0, 0, 0, 28.2, 67.0, 820.0, 1.0],
        [33.0, 58.0, 54.5, 70.0, 0, 0, 0, 28.5, 66.0, 840.0, 1.1],
        [34.0, 56.0, 54.3, 69.0, 0, 0, 0, 28.7, 65.0, 850.0, 1.1],
        [35.0, 54.0, 54.0, 68.0, 0, 0, 0, 29.0, 64.0, 850.0, 1.2],
    ]
    baseline = engine.predict_hybrid(history, False, False, 0)
    ventilated = engine.predict_hybrid(history, True, False, 90)
    irrigated = engine.predict_hybrid(history, False, True, 0)

    assert len(baseline) == len(ventilated) == len(irrigated) == 6
    assert ventilated[2]["temperature"] < baseline[2]["temperature"]
    assert irrigated[1]["soil_moisture"] > baseline[1]["soil_moisture"]
    for step in baseline + ventilated + irrigated:
        assert 5.0 <= step["temperature"] <= 55.0
        assert 0.0 <= step["humidity"] <= 100.0
        assert 0.0 <= step["soil_moisture"] <= 100.0
        assert 0.0 <= step["light_level"] <= 100.0
        assert step["model_used"] == "residual_pgml"

    pgml = report["metrics"]["residual_pgml"][
        "by_variable_all_horizons"
    ]
    print("Residual-PGML artifact contract: PASS")
    print(
        f"Features={FEATURE_COUNT}, targets={TARGET_COUNT}, "
        f"physics={PHYSICS_MODEL_VERSION}"
    )
    for name, metric in pgml.items():
        print(
            f"{name:14s} RMSE={metric['rmse']:.3f} "
            f"MAE={metric['mae']:.3f} R2={metric['r2']:.4f}"
        )
    print(
        "Counterfactual +15m temperature: "
        f"closed/fan-off={baseline[2]['temperature']:.2f}, "
        f"shade-full/fan-on={ventilated[2]['temperature']:.2f}"
    )
    print(
        "Counterfactual +10m soil moisture: "
        f"pump-off={baseline[1]['soil_moisture']:.2f}, "
        f"pump-on={irrigated[1]['soil_moisture']:.2f}"
    )


if __name__ == "__main__":
    run_inspection_and_test()
