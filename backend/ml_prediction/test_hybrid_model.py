# -*- coding: utf-8 -*-
"""
Test Script for Phase 2: Hybrid Model & Predictive Closed-Loop Control Engine.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

# Ensure backend folder is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from hybrid_model import HybridPredictionEngine
from plant_hp import PlantHPEngine

def run_tests():
    print("=======================================================")
    print(" 🧪 KIỂM THỬ KỊCH BẢN PHÂN ĐOẠN 2: HYBRID MODEL & CLOSED-LOOP")
    print("=======================================================\n")

    hybrid_engine = HybridPredictionEngine()
    plant_hp_engine = PlantHPEngine(max_update_gap_seconds=7200)

    # 1. Test Hybrid Forecast Scenario A (High temp 34°C, Fan OFF)
    print("-------------------------------------------------------")
    print("🔥 KỊCH BẢN 1: Nhiệt độ tăng (35.5°C) ➔ QUẠT TẮT (Fan=0)")
    print("-------------------------------------------------------")
    sample_window = [
        [28.0, 60.0, 65.0, 70.0, 0.0, 0.0, 0.0],
        [30.0, 55.0, 64.0, 80.0, 0.0, 0.0, 0.0],
        [32.0, 50.0, 63.0, 85.0, 0.0, 0.0, 0.0],
        [34.0, 45.0, 62.0, 90.0, 0.0, 0.0, 0.0],
        [35.5, 40.0, 61.0, 95.0, 0.0, 0.0, 0.0],
    ]

    forecast_fan_off = hybrid_engine.predict_hybrid(sample_window, fan_status=False, pump_status=False, servo_angle=0)
    for step in forecast_fan_off:
        print(f"   ► [{step['step']}] Temp: {step['temperature']}°C | Hum: {step['humidity']}% | Soil: {step['soil_moisture']}% | Light: {step['light_level']} lux")

    # Check Predictive Closed-Loop Intervention
    intervention_off = hybrid_engine.check_predictive_closed_loop(forecast_fan_off)
    print(f"\n   ⚡ Predictive Closed-Loop Status: {intervention_off}\n")

    # 2. Test Hybrid Forecast Scenario B (Fan ON & shade fully deployed)
    print("-------------------------------------------------------")
    print("❄️ KỊCH BẢN 2: Nhiệt độ tăng (35.5°C) ➔ QUẠT BẬT (Fan=1) & CHE NẮNG (Servo=90°)")
    print("-------------------------------------------------------")
    forecast_fan_on = hybrid_engine.predict_hybrid(sample_window, fan_status=True, pump_status=False, servo_angle=90)
    for step in forecast_fan_on:
        print(f"   ► [{step['step']}] Temp: {step['temperature']}°C | Hum: {step['humidity']}% | Soil: {step['soil_moisture']}% | Light: {step['light_level']} lux")

    # 3. Test Plant HP Engine for 3 crops
    print("\n-------------------------------------------------------")
    print("🌱 KỊCH BẢN 3: Kiểm thử Điểm Máu Plant HP 3 Loại Cây Trồng")
    print("-------------------------------------------------------")
    # Test at 30°C (Tomato normal tolerance zone -> 0 HP loss; Strawberry mild warning -> HP loss)
    hp_clock = datetime(2026, 7, 28, tzinfo=timezone.utc)
    hp_summary = plant_hp_engine.update_all_crops(
        temp=30.0,
        humid=50.0,
        soil=65.0,
        observed_at=hp_clock,
    )
    for crop_key, crop in hp_summary.items():
        print(f"   ► [{crop['name']}] HP: {crop['hp']}/100 | Trạng thái: {crop['status_text']} | Delta: {crop['last_delta']}")

    # Test severe heat shock (42°C) until death
    print("\n🔥 Giả lập Sốc Nhiệt Khắc Nghiệt (42°C) liên tục...")
    for i in range(15):
        hp_clock += timedelta(hours=2)
        plant_hp_engine.update_all_crops(
            temp=42.0,
            humid=30.0,
            soil=30.0,
            light=5.0,
            observed_at=hp_clock,
        )

    dead_summary = plant_hp_engine.get_summary()
    for crop_key, crop in dead_summary.items():
        print(f"   ► [{crop['name']}] HP: {crop['hp']}/100 | Cây Đã Chết (isDead): {crop['is_dead']} | {crop['status_text']}")

    # Test reset crop
    print("\n🔄 Thực hiện Trồng Lại Cây Mới (Reset Crop)...")
    plant_hp_engine.reset_crop("all")
    reset_summary = plant_hp_engine.get_summary()
    for crop_key, crop in reset_summary.items():
        print(f"   ► [{crop['name']}] HP: {crop['hp']}/100 | Cây Đã Chết: {crop['is_dead']} | {crop['status_text']}")

    print("\n=======================================================")
    print("🎉 TẤT CẢ TEST SCRIPT HYBRID MODEL & CLOSED-LOOP THÀNH CÔNG!")
    print("=======================================================\n")

if __name__ == "__main__":
    run_tests()
