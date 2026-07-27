"""Behavioural tests for the time-based Plant HP indicator."""

import unittest
from datetime import datetime, timedelta, timezone

from plant_hp import PlantHPEngine


class PlantHPTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 7, 28, tzinfo=timezone.utc)

    def engine(self):
        return PlantHPEngine(
            time_scale=1.0,
            max_update_gap_seconds=3600.0,
        )

    def test_severe_environment_is_immediately_visible(self):
        engine = self.engine()
        state = engine.evaluate_crop_tick(
            "tomato", 42, 20, 30, 5, observed_at=self.t0
        )
        self.assertEqual(state["environment_status"], "CRITICAL")
        self.assertEqual(state["health_status"], "HEALTHY")
        self.assertEqual(state["hp"], 100.0)
        self.assertTrue(state["stress_factors"])

    def test_hp_depends_on_elapsed_time_not_message_count(self):
        frequent = self.engine()
        hourly = self.engine()
        severe = (42, 20, 30, 5)
        frequent.evaluate_crop_tick(
            "tomato", *severe, observed_at=self.t0
        )
        hourly.evaluate_crop_tick("tomato", *severe, observed_at=self.t0)

        for seconds in range(3, 3601, 3):
            frequent.evaluate_crop_tick(
                "tomato",
                *severe,
                observed_at=self.t0 + timedelta(seconds=seconds),
            )
        hourly.evaluate_crop_tick(
            "tomato",
            *severe,
            observed_at=self.t0 + timedelta(hours=1),
        )
        self.assertAlmostEqual(
            frequent.get_summary()["tomato"]["hp"],
            hourly.get_summary()["tomato"]["hp"],
            places=2,
        )

    def test_plant_does_not_die_after_eighteen_seconds(self):
        engine = self.engine()
        severe = (42, 20, 30, 5)
        engine.evaluate_crop_tick(
            "tomato", *severe, observed_at=self.t0
        )
        state = engine.evaluate_crop_tick(
            "tomato",
            *severe,
            observed_at=self.t0 + timedelta(seconds=18),
        )
        self.assertGreater(state["hp"], 99.0)
        self.assertFalse(state["is_dead"])
        self.assertEqual(state["environment_status"], "CRITICAL")

    def test_recovery_is_slow_and_time_based(self):
        initial = {
            "tomato": {
                "hp": 60.0,
                "last_updated_at": self.t0.isoformat(),
            }
        }
        engine = PlantHPEngine(
            initial,
            time_scale=1.0,
            max_update_gap_seconds=3600.0,
        )
        state = engine.evaluate_crop_tick(
            "tomato",
            24,
            67,
            67,
            80,
            observed_at=self.t0 + timedelta(hours=1),
        )
        self.assertEqual(state["environment_status"], "OPTIMAL")
        self.assertAlmostEqual(state["hp"], 60.5, places=2)

    def test_slightly_low_temperature_is_warning_not_shock(self):
        engine = self.engine()
        state = engine.evaluate_crop_tick(
            "tomato", 17, 67, 67, 80, observed_at=self.t0
        )
        self.assertEqual(state["factor_zones"]["temperature"], "WARNING")
        self.assertEqual(state["environment_status"], "WARNING")

    def test_unknown_crop_is_rejected(self):
        engine = self.engine()
        with self.assertRaises(ValueError):
            engine.reset_crop("unknown")


if __name__ == "__main__":
    unittest.main()
