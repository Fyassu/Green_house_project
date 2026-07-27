"""Unit tests for the predictive closed-loop safety gate."""

import unittest
from datetime import datetime, timedelta, timezone

from closed_loop import PredictiveController


ALERT = {
    "triggered": True,
    "commands": {"fan": True, "servo": 90},
    "messages": ["Nhiệt độ dự báo vượt ngưỡng."],
    "prediction": {"temperature": 35.2, "soil_moisture": 60.0},
    "decision_horizon_minutes": 15,
}


class PredictiveControllerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)
        self.manual = {
            "fan": None,
            "pump": None,
            "servo": None,
            "light": None,
            "security": True,
        }

    def make_controller(self, mode="AUTO"):
        return PredictiveController(
            mode=mode,
            confirmation_required=2,
            cooldown_seconds=60,
            stale_after_seconds=30,
            pump_max_run_seconds=120,
            fan_min_run_seconds=30,
        )

    def test_advisory_never_applies_commands(self):
        controller = self.make_controller("ADVISORY")
        decision = controller.evaluate(
            ALERT, self.manual, observed_at=self.now, now=self.now
        )
        self.assertEqual(decision["status"], "advisory")
        self.assertEqual(decision["applied_commands"], {})
        self.assertIsNone(controller.effective_commands(self.manual)["fan"])

    def test_auto_requires_two_consecutive_forecasts(self):
        controller = self.make_controller()
        first = controller.evaluate(
            ALERT, self.manual, observed_at=self.now, now=self.now
        )
        second = controller.evaluate(
            ALERT,
            self.manual,
            observed_at=self.now + timedelta(seconds=3),
            now=self.now + timedelta(seconds=3),
        )
        self.assertEqual(first["status"], "confirming")
        self.assertEqual(second["status"], "executed")
        self.assertTrue(controller.effective_commands(self.manual)["fan"])
        self.assertEqual(controller.effective_commands(self.manual)["servo"], 90)

    def test_manual_override_has_priority(self):
        controller = self.make_controller()
        manual = dict(self.manual, fan=False)
        controller.evaluate(ALERT, manual, observed_at=self.now, now=self.now)
        decision = controller.evaluate(
            ALERT,
            manual,
            observed_at=self.now + timedelta(seconds=3),
            now=self.now + timedelta(seconds=3),
        )
        self.assertEqual(decision["status"], "executed")
        self.assertIn("fan", decision["blocked_manual"])
        self.assertFalse(controller.effective_commands(manual)["fan"])
        self.assertEqual(controller.effective_commands(manual)["servo"], 90)

    def test_stale_data_blocks_auto_control(self):
        controller = self.make_controller()
        decision = controller.evaluate(
            ALERT,
            self.manual,
            observed_at=self.now,
            now=self.now + timedelta(seconds=31),
        )
        self.assertEqual(decision["status"], "blocked_stale_data")
        self.assertEqual(decision["applied_commands"], {})

    def test_two_clear_forecasts_release_auto_command(self):
        controller = self.make_controller()
        controller.evaluate(ALERT, self.manual, observed_at=self.now, now=self.now)
        controller.evaluate(
            ALERT,
            self.manual,
            observed_at=self.now + timedelta(seconds=3),
            now=self.now + timedelta(seconds=3),
        )
        controller.evaluate(
            None,
            self.manual,
            observed_at=self.now + timedelta(seconds=34),
            now=self.now + timedelta(seconds=34),
        )
        decision = controller.evaluate(
            None,
            self.manual,
            observed_at=self.now + timedelta(seconds=37),
            now=self.now + timedelta(seconds=37),
        )
        self.assertEqual(decision["status"], "released")
        self.assertIsNone(controller.effective_commands(self.manual)["fan"])

    def test_pump_has_maximum_runtime(self):
        controller = self.make_controller()
        pump_alert = dict(
            ALERT,
            commands={"pump": True},
            prediction={"temperature": 28.0, "soil_moisture": 40.0},
        )
        controller.evaluate(
            pump_alert, self.manual, observed_at=self.now, now=self.now
        )
        controller.evaluate(
            pump_alert,
            self.manual,
            observed_at=self.now + timedelta(seconds=3),
            now=self.now + timedelta(seconds=3),
        )
        decision = controller.evaluate(
            None,
            self.manual,
            observed_at=self.now + timedelta(seconds=124),
            now=self.now + timedelta(seconds=124),
        )
        self.assertEqual(decision["applied_commands"].get("pump"), None)
        self.assertIsNone(controller.effective_commands(self.manual)["pump"])


if __name__ == "__main__":
    unittest.main()
