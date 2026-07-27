"""Safety layer for predictive greenhouse control.

The forecasting model only proposes actions.  This module decides whether a
proposal is displayed, executed, delayed, or blocked.  It deliberately has no
Flask, MQTT, or database dependency so its behaviour can be unit-tested.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


VALID_MODES = {"OFF", "ADVISORY", "AUTO"}
ACTUATORS = ("fan", "pump", "servo", "light")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def _as_utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PredictiveController:
    """Stateful OFF/ADVISORY/AUTO decision gate with basic actuator safety."""

    def __init__(
        self,
        mode: str | None = None,
        confirmation_required: int | None = None,
        cooldown_seconds: int | None = None,
        stale_after_seconds: int | None = None,
        pump_max_run_seconds: int | None = None,
        fan_min_run_seconds: int | None = None,
    ) -> None:
        requested_mode = (mode or os.getenv("CONTROL_MODE", "ADVISORY")).upper()
        self.mode = requested_mode if requested_mode in VALID_MODES else "ADVISORY"
        self.confirmation_required = confirmation_required or _env_int(
            "CONTROL_CONFIRMATION_COUNT", 2, 1
        )
        self.cooldown_seconds = (
            cooldown_seconds
            if cooldown_seconds is not None
            else _env_int("CONTROL_COOLDOWN_SECONDS", 60, 0)
        )
        self.stale_after_seconds = (
            stale_after_seconds
            if stale_after_seconds is not None
            else _env_int("CONTROL_STALE_AFTER_SECONDS", 30, 1)
        )
        self.pump_max_run_seconds = (
            pump_max_run_seconds
            if pump_max_run_seconds is not None
            else _env_int("PUMP_MAX_RUN_SECONDS", 120, 1)
        )
        self.fan_min_run_seconds = (
            fan_min_run_seconds
            if fan_min_run_seconds is not None
            else _env_int("FAN_MIN_RUN_SECONDS", 30, 0)
        )

        self.auto_commands: dict[str, Any] = {
            actuator: None for actuator in ACTUATORS
        }
        self.active_since: dict[str, datetime | None] = {
            actuator: None for actuator in ACTUATORS
        }
        self.last_action_at: dict[str, datetime | None] = {
            actuator: None for actuator in ACTUATORS
        }
        self._candidate_signature: str | None = None
        self._candidate_count = 0
        self._clear_count = 0
        self._last_log_key: str | None = None
        self.last_decision = self._decision(status="idle")

    def _decision(self, status: str, **extra: Any) -> dict:
        result = {
            "triggered": False,
            "mode": self.mode,
            "status": status,
            "commands": {},
            "applied_commands": {},
            "messages": [],
            "confirmation": {
                "current": 0,
                "required": self.confirmation_required,
            },
            "should_log": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result.update(extra)
        return result

    @staticmethod
    def _signature(commands: dict) -> str:
        return json.dumps(commands, sort_keys=True, separators=(",", ":"))

    def _mark_log_once(self, decision: dict) -> dict:
        key = "|".join(
            (
                decision["mode"],
                decision["status"],
                self._signature(decision.get("commands", {})),
                self._signature(decision.get("applied_commands", {})),
            )
        )
        decision["should_log"] = key != self._last_log_key
        if decision["should_log"]:
            self._last_log_key = key
        return decision

    def set_mode(self, mode: str) -> dict:
        normalised = str(mode).upper()
        if normalised not in VALID_MODES:
            raise ValueError(
                f"mode must be one of {', '.join(sorted(VALID_MODES))}"
            )
        previous = self.mode
        self.mode = normalised
        self._candidate_signature = None
        self._candidate_count = 0
        self._clear_count = 0
        released = {}
        if normalised != "AUTO":
            released = self.clear_auto()
        self.last_decision = self._decision(
            status="mode_changed",
            previous_mode=previous,
            released_commands=released,
        )
        return self.last_decision

    def clear_auto(self, actuators: tuple[str, ...] | list[str] | None = None) -> dict:
        released = {}
        for actuator in actuators or ACTUATORS:
            if actuator in self.auto_commands and self.auto_commands[actuator] is not None:
                self.auto_commands[actuator] = None
                self.active_since[actuator] = None
                released[actuator] = None
        return released

    def effective_commands(self, manual_overrides: dict) -> dict:
        result = {}
        for actuator in ACTUATORS:
            manual_value = manual_overrides.get(actuator)
            if manual_value is not None:
                result[actuator] = manual_value
            elif self.mode == "AUTO":
                result[actuator] = self.auto_commands.get(actuator)
            else:
                result[actuator] = None
        result["security"] = bool(manual_overrides.get("security", True))
        return result

    def preview(self, alert: dict | None) -> dict:
        """Describe a forecast proposal without changing confirmation state."""
        if not alert or not alert.get("triggered"):
            return self._decision(status="normal")
        status = {
            "OFF": "disabled",
            "ADVISORY": "advisory",
            "AUTO": "awaiting_live_confirmation",
        }[self.mode]
        return self._decision(
            status=status,
            triggered=True,
            commands=dict(alert.get("commands", {})),
            messages=list(alert.get("messages", [])),
            prediction=dict(alert.get("prediction", {})),
            decision_horizon_minutes=alert.get("decision_horizon_minutes", 15),
        )

    def _release_after_clear(self, now: datetime) -> dict:
        released = {}
        for actuator, current in tuple(self.auto_commands.items()):
            if current is None:
                continue
            since = self.active_since.get(actuator)
            runtime = (now - since).total_seconds() if since else 0
            if actuator == "fan" and runtime < self.fan_min_run_seconds:
                continue
            self.auto_commands[actuator] = None
            self.active_since[actuator] = None
            self.last_action_at[actuator] = now
            released[actuator] = None
        return released

    def _enforce_pump_timeout(self, now: datetime) -> dict:
        if self.auto_commands.get("pump") is not True:
            return {}
        since = self.active_since.get("pump")
        if not since:
            return {}
        if (now - since).total_seconds() < self.pump_max_run_seconds:
            return {}
        self.auto_commands["pump"] = None
        self.active_since["pump"] = None
        self.last_action_at["pump"] = now
        return {"pump": None}

    def evaluate(
        self,
        alert: dict | None,
        manual_overrides: dict,
        *,
        observed_at: datetime | None = None,
        model_available: bool = True,
        now: datetime | None = None,
    ) -> dict:
        """Evaluate one live forecast and return an auditable decision."""
        now_utc = _as_utc(now)
        observed_utc = _as_utc(observed_at)
        timed_out = self._enforce_pump_timeout(now_utc)

        if self.mode != "AUTO" and any(
            value is not None for value in self.auto_commands.values()
        ):
            timed_out.update(self.clear_auto())

        if (now_utc - observed_utc).total_seconds() > self.stale_after_seconds:
            decision = self._decision(
                status="blocked_stale_data",
                messages=["Dữ liệu cảm biến đã cũ, không tự động phát lệnh."],
                applied_commands=timed_out,
            )
            self.last_decision = self._mark_log_once(decision)
            return self.last_decision

        if not model_available:
            decision = self._decision(
                status="blocked_model_unavailable",
                messages=["Không tải được mô hình PGML, chỉ cho phép giám sát."],
                applied_commands=timed_out,
            )
            self.last_decision = self._mark_log_once(decision)
            return self.last_decision

        triggered = bool(alert and alert.get("triggered"))
        commands = dict(alert.get("commands", {})) if triggered else {}
        messages = list(alert.get("messages", [])) if triggered else []
        prediction = dict(alert.get("prediction", {})) if triggered else {}

        if self.mode == "OFF":
            decision = self._decision(
                status="disabled",
                triggered=triggered,
                commands=commands,
                messages=messages,
                prediction=prediction,
                applied_commands=timed_out,
            )
            self.last_decision = decision
            return decision

        if self.mode == "ADVISORY":
            decision = self._decision(
                status="advisory" if triggered else "normal",
                triggered=triggered,
                commands=commands,
                messages=messages,
                prediction=prediction,
                applied_commands=timed_out,
                decision_horizon_minutes=(
                    alert.get("decision_horizon_minutes", 15) if alert else 15
                ),
            )
            self.last_decision = (
                self._mark_log_once(decision) if triggered or timed_out else decision
            )
            return self.last_decision

        if not triggered:
            self._candidate_signature = None
            self._candidate_count = 0
            self._clear_count += 1
            released = dict(timed_out)
            if self._clear_count >= self.confirmation_required:
                released.update(self._release_after_clear(now_utc))
            decision = self._decision(
                status="released" if released else "normal",
                applied_commands=released,
            )
            self.last_decision = (
                self._mark_log_once(decision) if released else decision
            )
            return self.last_decision

        self._clear_count = 0
        signature = self._signature(commands)
        if signature == self._candidate_signature:
            self._candidate_count += 1
        else:
            self._candidate_signature = signature
            self._candidate_count = 1

        confirmation = {
            "current": min(self._candidate_count, self.confirmation_required),
            "required": self.confirmation_required,
        }
        if self._candidate_count < self.confirmation_required:
            decision = self._decision(
                status="confirming",
                triggered=True,
                commands=commands,
                messages=messages,
                prediction=prediction,
                confirmation=confirmation,
            )
            self.last_decision = decision
            return decision

        applied = dict(timed_out)
        blocked_manual = []
        blocked_cooldown = []
        for actuator, value in commands.items():
            if actuator not in ACTUATORS:
                continue
            if manual_overrides.get(actuator) is not None:
                blocked_manual.append(actuator)
                continue
            last_action = self.last_action_at.get(actuator)
            if (
                last_action
                and (now_utc - last_action).total_seconds() < self.cooldown_seconds
                and self.auto_commands.get(actuator) != value
            ):
                blocked_cooldown.append(actuator)
                continue
            if self.auto_commands.get(actuator) != value:
                self.auto_commands[actuator] = value
                self.last_action_at[actuator] = now_utc
                self.active_since[actuator] = now_utc if value is not None else None
                applied[actuator] = value

        if applied:
            status = "executed"
        elif blocked_manual:
            status = "blocked_manual_override"
        elif blocked_cooldown:
            status = "blocked_cooldown"
        else:
            status = "already_active"

        decision = self._decision(
            status=status,
            triggered=True,
            commands=commands,
            applied_commands=applied,
            messages=messages,
            prediction=prediction,
            confirmation=confirmation,
            blocked_manual=blocked_manual,
            blocked_cooldown=blocked_cooldown,
            decision_horizon_minutes=alert.get("decision_horizon_minutes", 15),
        )
        self.last_decision = (
            self._mark_log_once(decision)
            if status in {"executed", "blocked_manual_override", "blocked_cooldown"}
            else decision
        )
        return self.last_decision

    def snapshot(self) -> dict:
        return {
            "mode": self.mode,
            "auto_commands": dict(self.auto_commands),
            "confirmation_required": self.confirmation_required,
            "cooldown_seconds": self.cooldown_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            "pump_max_run_seconds": self.pump_max_run_seconds,
            "fan_min_run_seconds": self.fan_min_run_seconds,
            "last_decision": dict(self.last_decision),
        }
