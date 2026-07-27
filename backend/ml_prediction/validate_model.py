# -*- coding: utf-8 -*-
"""One-command validation report for the greenhouse residual-PGML model."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys

import pandas as pd


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(BASE_DIR)
PROJECT_DIR = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from hybrid_model import HybridPredictionEngine  # noqa: E402
from pbm_engine import (  # noqa: E402
    BoundaryConditions,
    GreenhouseState,
    PHYSICS_MODEL_VERSION,
    ProcessBasedModelEngine,
)


DATASET_PATH = os.path.join(PROJECT_DIR, "simulated_greenhouse_dataset.csv")
MODELS_DIR = os.path.join(BASE_DIR, "models")
METADATA_PATH = os.path.join(MODELS_DIR, "model_metadata.json")
REPORT_PATH = os.path.join(MODELS_DIR, "training_report.json")


class ValidationReport:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, condition: bool, name: str, detail: str = "") -> None:
        if condition:
            self.passed += 1
            status = "PASS"
        else:
            self.failed += 1
            status = "FAIL"
        suffix = f" — {detail}" if detail else ""
        print(f"[{status}] {name}{suffix}")

    def finish(self) -> int:
        print("\n" + "=" * 68)
        print(f"Ket qua: {self.passed} PASS, {self.failed} FAIL")
        if self.failed:
            print("Model CHUA dat validation. Xem cac dong [FAIL] o tren.")
            return 1
        print("Model dat cac kiem tra logic, artifact va synthetic holdout.")
        print("Luu y: ket qua nay KHONG thay the validation bang du lieu that.")
        return 0


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_physics(report: ValidationReport) -> None:
    print("\n1. KIEM TRA VAT LY")
    model = ProcessBasedModelEngine()

    hot_inside = GreenhouseState(35.0, 65.0, 55.0, 60.0)
    cool_outside = BoundaryConditions(27.0, 70.0, 500.0, 1.0)
    off = model.forecast(
        hot_inside, False, False, 0, steps=3, boundary=cool_outside
    )[-1]
    ventilated = model.forecast(
        hot_inside, True, False, 0, steps=3, boundary=cool_outside
    )[-1]
    report.check(
        ventilated["temperature"] < off["temperature"],
        "Quat lam mat khi ben ngoai mat hon",
        (
            f"OFF={off['temperature']:.3f} degC, "
            f"ON={ventilated['temperature']:.3f} degC"
        ),
    )

    cool_inside = GreenhouseState(25.0, 60.0, 55.0, 0.0)
    hot_outside = BoundaryConditions(35.0, 45.0, 0.0, 1.0)
    fan_off = model.forecast(
        cool_inside, False, False, 0, steps=2, boundary=hot_outside
    )[-1]
    fan_on = model.forecast(
        cool_inside, True, False, 0, steps=2, boundary=hot_outside
    )[-1]
    report.check(
        fan_on["temperature"] > fan_off["temperature"],
        "Thong gio co the lam nong khi ben ngoai nong hon",
        (
            f"fan_off={fan_off['temperature']:.3f} degC, "
            f"fan_on={fan_on['temperature']:.3f} degC"
        ),
    )

    dry = GreenhouseState(29.0, 60.0, 40.0, 55.0)
    no_pump = model.forecast(dry, False, False, 0, steps=2)[-1]
    pump = model.forecast(dry, False, True, 0, steps=2)[-1]
    report.check(
        pump["soil_moisture"] > no_pump["soil_moisture"],
        "Bom lam tang do am dat",
        (
            f"OFF={no_pump['soil_moisture']:.3f}%, "
            f"ON={pump['soil_moisture']:.3f}%"
        ),
    )

    shade_off_flow = model.ventilation_flow_m3_per_s(False, 0, 1.0)
    shade_full_flow = model.ventilation_flow_m3_per_s(False, 90, 1.0)
    report.check(
        abs(shade_off_flow - shade_full_flow) < 1e-12,
        "Mai che khong lam thay doi luu luong thong gio",
        (
            f"shade_off={shade_off_flow:.4f}, "
            f"shade_full={shade_full_flow:.4f} m3/s"
        ),
    )
    no_fan_flow = model.ventilation_flow_m3_per_s(False, 0, 1.0)
    fan_flow = model.ventilation_flow_m3_per_s(True, 0, 1.0)
    report.check(
        fan_flow > no_fan_flow,
        "Quat lam tang luu luong thong gio",
        f"fan_off={no_fan_flow:.4f}, fan_on={fan_flow:.4f} m3/s",
    )

    sunny = BoundaryConditions(30.0, 60.0, 900.0, 1.0)
    unshaded = model.forecast(
        hot_inside, False, False, 0, steps=3, boundary=sunny
    )[-1]
    shaded = model.forecast(
        hot_inside, False, False, 90, steps=3, boundary=sunny
    )[-1]
    report.check(
        shaded["light_level"] < unshaded["light_level"]
        and shaded["temperature"] < unshaded["temperature"],
        "Che nang lam giam anh sang va nhiet do",
        (
            f"light {unshaded['light_level']:.1f}->{shaded['light_level']:.1f}%, "
            f"temp {unshaded['temperature']:.3f}->{shaded['temperature']:.3f} degC"
        ),
    )

    night = BoundaryConditions(24.0, 85.0, 0.0, 0.5)
    night_state = model.forecast(
        GreenhouseState(26.0, 70.0, 55.0, 40.0),
        False,
        False,
        0,
        steps=1,
        boundary=night,
    )[0]
    report.check(
        night_state["light_level"] == 0.0,
        "Ban dem khong co anh sang mat troi",
        f"light={night_state['light_level']:.1f}%",
    )

    next_state, diagnostics = model.advance(
        hot_inside, cool_outside, True, True, 0
    )
    finite = all(
        math.isfinite(value)
        for value in (
            next_state.temperature_c,
            next_state.relative_humidity_pct,
            next_state.soil_moisture_pct,
            next_state.light_level_pct,
            *diagnostics.values(),
        )
    )
    bounded = (
        5 <= next_state.temperature_c <= 55
        and 0 <= next_state.relative_humidity_pct <= 100
        and 0 <= next_state.soil_moisture_pct <= 100
        and 0 <= next_state.light_level_pct <= 100
    )
    report.check(finite and bounded, "State va flux huu han, nam trong gioi han")


def validate_dataset(report: ValidationReport) -> None:
    print("\n2. KIEM TRA DATASET")
    report.check(os.path.exists(DATASET_PATH), "Dataset ton tai", DATASET_PATH)
    if not os.path.exists(DATASET_PATH):
        return

    data = pd.read_csv(DATASET_PATH)
    core = [
        "Ambient_Temperature",
        "Humidity",
        "Soil_Moisture",
        "Light_Intensity",
        "Fan_Status",
        "Pump_Status",
        "Servo_Angle",
        "Day_ID",
        "Weather_State",
        "Outdoor_Temperature",
        "Outdoor_Humidity",
        "Solar_Radiation_W_m2",
        "Wind_Speed_m_s",
        "Episode_ID",
        "Control_Mode",
        "Physics_Model_Version",
    ]
    report.check(
        all(column in data.columns for column in core),
        "Dataset co du cac cot bat buoc",
    )
    if not all(column in data.columns for column in core):
        return

    report.check(len(data) == 7200, "So dong dataset", str(len(data)))
    report.check(
        not data[core].isna().any().any(),
        "Khong co missing value trong cot cot loi",
    )
    report.check(
        data["Humidity"].between(0, 100).all()
        and data["Soil_Moisture"].between(0, 100).all()
        and data["Light_Intensity"].between(0, 100).all(),
        "Cac bien phan tram nam trong 0..100",
    )
    report.check(
        set(data["Servo_Angle"].unique()) == {0, 45, 90},
        "Co du ba goc servo",
        str(sorted(data["Servo_Angle"].unique().tolist())),
    )
    report.check(
        data["Fan_Status"].nunique() == 2
        and data["Pump_Status"].nunique() == 2,
        "Quat va bom deu co trang thai ON/OFF",
    )
    excitation_count = int(
        data["Control_Mode"].str.startswith("excitation").sum()
    )
    report.check(
        excitation_count > 0,
        "Co actuator excitation de giam confounding",
        f"{excitation_count} rows",
    )
    report.check(
        data["Day_ID"].nunique() == 25
        and data.groupby("Day_ID").size().eq(288).all(),
        "Co 25 ngay doc lap, moi ngay 288 moc 5 phut",
        f"{data['Day_ID'].nunique()} days",
    )
    report.check(
        set(data["Weather_State"].unique())
        == {"Hot_Sunny", "Normal", "Cloudy", "Cool_Humid"},
        "Dataset co du bon kieu thoi tiet",
        str(sorted(data["Weather_State"].unique().tolist())),
    )
    report.check(
        data["Outdoor_Humidity"].between(0, 100).all()
        and (data["Solar_Radiation_W_m2"] >= 0).all()
        and (data["Wind_Speed_m_s"] > 0).all(),
        "Du lieu moi truong ngoai nam trong mien hop ly",
    )
    nighttime = pd.to_datetime(data["Timestamp"]).dt.hour.isin(
        list(range(0, 6)) + list(range(19, 24))
    )
    report.check(
        (data.loc[nighttime, "Solar_Radiation_W_m2"] == 0).all(),
        "Buc xa ngoai troi bang 0 vao ban dem",
    )
    report.check(
        set(data["Physics_Model_Version"].astype(str))
        == {PHYSICS_MODEL_VERSION},
        "Physics version trong dataset khop code",
        PHYSICS_MODEL_VERSION,
    )


def validate_artifacts_and_forecast(report: ValidationReport) -> None:
    print("\n3. KIEM TRA ARTIFACT VA INFERENCE")
    files_exist = all(
        os.path.exists(path)
        for path in (METADATA_PATH, REPORT_PATH, DATASET_PATH)
    )
    report.check(files_exist, "Metadata, training report va dataset ton tai")
    if not files_exist:
        return

    with open(METADATA_PATH, encoding="utf-8") as handle:
        metadata = json.load(handle)
    with open(REPORT_PATH, encoding="utf-8") as handle:
        training = json.load(handle)

    dataset_hash = _sha256(DATASET_PATH)
    report.check(
        dataset_hash
        == metadata["dataset_sha256"]
        == training["dataset"]["sha256"],
        "Dataset hash khop artifact da train",
    )
    report.check(
        metadata["feature_count"] == 79
        and metadata["target_count"] == 24,
        "Artifact contract 79 inputs / 24 outputs",
    )
    report.check(
        metadata["physics_model_version"] == PHYSICS_MODEL_VERSION,
        "Physics version cua model khop code",
    )

    engine = HybridPredictionEngine()
    report.check(
        engine.rf_model is not None,
        "Load duoc residual-PGML model",
        engine.artifact_error or "OK",
    )
    if engine.rf_model is None:
        return

    history = [
        [31.0, 62.0, 55.0, 65.0, 0, 0, 0, 28.0, 68.0, 800.0, 1.0],
        [32.0, 60.0, 54.8, 68.0, 0, 0, 0, 28.2, 67.0, 820.0, 1.0],
        [33.0, 58.0, 54.5, 70.0, 0, 0, 0, 28.5, 66.0, 840.0, 1.1],
        [34.0, 56.0, 54.3, 69.0, 0, 0, 0, 28.7, 65.0, 850.0, 1.1],
        [35.0, 54.0, 54.0, 68.0, 0, 0, 0, 29.0, 64.0, 850.0, 1.2],
    ]
    baseline = engine.predict_hybrid(history, False, False, 0)
    fan_shade = engine.predict_hybrid(history, True, False, 90)
    pump = engine.predict_hybrid(history, False, True, 0)
    report.check(
        len(baseline) == len(fan_shade) == len(pump) == 6,
        "Forecast co du 6 moc tu +5 den +30 phut",
    )
    report.check(
        fan_shade[2]["temperature"] < baseline[2]["temperature"],
        "Counterfactual +15m: quat + che nang mat hon",
        (
            f"OFF={baseline[2]['temperature']:.2f}, "
            f"ON={fan_shade[2]['temperature']:.2f} degC"
        ),
    )
    report.check(
        pump[1]["soil_moisture"] > baseline[1]["soil_moisture"],
        "Counterfactual +10m: bom lam dat am hon",
        (
            f"OFF={baseline[1]['soil_moisture']:.2f}, "
            f"ON={pump[1]['soil_moisture']:.2f}%"
        ),
    )
    values_bounded = all(
        5 <= step["temperature"] <= 55
        and 0 <= step["humidity"] <= 100
        and 0 <= step["soil_moisture"] <= 100
        and 0 <= step["light_level"] <= 100
        for step in baseline + fan_shade + pump
    )
    report.check(values_bounded, "Tat ca forecast nam trong gioi han")


def validate_metrics(report: ValidationReport) -> None:
    print("\n4. KIEM TRA SYNTHETIC HOLDOUT METRICS")
    if not os.path.exists(REPORT_PATH):
        report.check(False, "Training report ton tai")
        return
    with open(REPORT_PATH, encoding="utf-8") as handle:
        metrics = json.load(handle)["metrics"]

    pgml = metrics["residual_pgml"]["by_variable_all_horizons"]
    physics = metrics["physics_only"]["by_variable_all_horizons"]
    data_only = metrics["data_only_random_forest"][
        "by_variable_all_horizons"
    ]
    for variable in (
        "temperature",
        "humidity",
        "soil_moisture",
        "light_level",
    ):
        pgml_rmse = pgml[variable]["rmse"]
        physics_rmse = physics[variable]["rmse"]
        data_rmse = data_only[variable]["rmse"]
        report.check(
            pgml_rmse < physics_rmse and pgml_rmse < data_rmse,
            f"PGML RMSE tot hon 2 baseline: {variable}",
            (
                f"PGML={pgml_rmse:.3f}, physics={physics_rmse:.3f}, "
                f"data-only={data_rmse:.3f}"
            ),
        )


def main() -> int:
    print("=" * 68)
    print("VALIDATION REPORT — GREENHOUSE RESIDUAL-PGML")
    print("=" * 68)
    report = ValidationReport()
    validate_physics(report)
    validate_dataset(report)
    validate_artifacts_and_forecast(report)
    validate_metrics(report)
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main())
