"""MySQL access and lightweight schema migrations for the greenhouse backend."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import bcrypt
import mysql.connector
from mysql.connector import pooling

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_USER


_config = {
    "host": os.getenv("DB_HOST", DB_HOST),
    "user": os.getenv("DB_USER", DB_USER),
    "password": os.getenv("DB_PASSWORD", DB_PASSWORD),
    "database": os.getenv("DB_NAME", DB_NAME),
    "use_pure": True,
    "autocommit": True,
}

db_pool = pooling.MySQLConnectionPool(
    pool_name="greenhouse_pool",
    pool_size=10,
    pool_reset_session=True,
    **_config,
)


def _index_exists(cur, table_name: str, index_name: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        """,
        (table_name, index_name),
    )
    return cur.fetchone()[0] > 0


def _ensure_table() -> None:
    conn = db_pool.get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sensor_data (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                temperature     FLOAT,
                humidity        FLOAT,
                soil_moisture   FLOAT,
                light_level     FLOAT,
                motion_detected BOOLEAN,
                fan_status      BOOLEAN,
                pump_status     BOOLEAN,
                servo_angle     INT,
                light_status    BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Migration for databases created by an earlier project version.
        cur.execute(
            """
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'sensor_data'
              AND COLUMN_NAME = 'light_status'
            """
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                "ALTER TABLE sensor_data "
                "ADD COLUMN light_status BOOLEAN DEFAULT FALSE"
            )

        # Time-range history and prediction-window queries need a B-tree index.
        if not _index_exists(cur, "sensor_data", "idx_sensor_created_at"):
            cur.execute(
                "ALTER TABLE sensor_data "
                "ADD INDEX idx_sensor_created_at (created_at)"
            )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                username      VARCHAR(64) UNIQUE NOT NULL,
                password_hash VARCHAR(128) NOT NULL
            )
            """
        )
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
        cur.execute(
            "SELECT COUNT(*) FROM users WHERE username = %s",
            (admin_username,),
        )
        if cur.fetchone()[0] == 0:
            hashed = bcrypt.hashpw(
                admin_password.encode(), bcrypt.gensalt()
            ).decode()
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (admin_username, hashed),
            )

        # Decision history proves the virtual model acted on the physical side.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS control_events (
                id                      BIGINT AUTO_INCREMENT PRIMARY KEY,
                event_type              VARCHAR(32) NOT NULL,
                control_mode            VARCHAR(16) NOT NULL,
                source                  VARCHAR(16) NOT NULL,
                status                  VARCHAR(40) NOT NULL,
                reason                  TEXT,
                predicted_temperature   FLOAT NULL,
                predicted_soil_moisture FLOAT NULL,
                commands_json           TEXT,
                created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_event_type_created_at (event_type, created_at),
                INDEX idx_source_created_at (source, created_at),
                INDEX idx_status_created_at (status, created_at)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plant_health_state (
                crop_key            VARCHAR(32) PRIMARY KEY,
                hp                  FLOAT NOT NULL,
                is_dead             BOOLEAN NOT NULL DEFAULT FALSE,
                health_status       VARCHAR(24) NOT NULL,
                environment_status  VARCHAR(24) NOT NULL,
                status_text         VARCHAR(128) NOT NULL,
                last_delta          FLOAT NOT NULL DEFAULT 0,
                hp_rate_per_hour    FLOAT NOT NULL DEFAULT 0,
                stress_json         TEXT,
                last_updated_at     DATETIME(6) NULL,
                updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
    finally:
        cur.close()
        conn.close()


_ensure_table()


class ManagedCursor:
    def __init__(self):
        self.conn = db_pool.get_connection()
        self.cur = self.conn.cursor()

    def execute(self, *args, **kwargs):
        return self.cur.execute(*args, **kwargs)

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    def close(self):
        self.cur.close()
        self.conn.close()


def get_cursor():
    return ManagedCursor()


def get_new_connection():
    """Create a dedicated connection for the MQTT worker thread."""
    return mysql.connector.connect(**_config)


def get_training_window(hours: int = 6, max_rows: int = 500) -> list[dict]:
    """Return recent telemetry in chronological order for forecasting."""
    safe_hours = max(1, min(int(hours), 24 * 30))
    safe_max_rows = max(1, min(int(max_rows), 10_000))
    conn = db_pool.get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT temperature, humidity, soil_moisture, light_level,
               fan_status, pump_status, servo_angle, created_at
        FROM sensor_data
        WHERE created_at >= NOW() - INTERVAL %s HOUR
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (safe_hours, safe_max_rows),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    rows.reverse()
    return rows


def log_control_event(event: dict) -> int:
    """Persist one manual, advisory, automatic, or mode-change decision."""
    prediction = event.get("prediction") or {}
    messages = event.get("messages") or []
    reason = " | ".join(str(message) for message in messages)
    commands = event.get("applied_commands") or event.get("commands") or {}

    conn = db_pool.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO control_events (
            event_type, control_mode, source, status, reason,
            predicted_temperature, predicted_soil_moisture, commands_json
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            event.get("event_type", "prediction"),
            event.get("mode", "OFF"),
            event.get("source", "pgml"),
            event.get("status", "unknown"),
            reason or None,
            prediction.get("temperature"),
            prediction.get("soil_moisture"),
            json.dumps(commands, ensure_ascii=False),
        ),
    )
    event_id = cur.lastrowid
    cur.close()
    conn.close()
    return event_id


def get_control_events(limit: int = 50) -> list[dict]:
    """Return the newest control decisions for the dashboard."""
    safe_limit = max(1, min(int(limit), 200))
    conn = db_pool.get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT id, event_type, control_mode, source, status, reason,
               predicted_temperature, predicted_soil_moisture,
               commands_json, created_at
        FROM control_events
        ORDER BY created_at DESC, id DESC
        LIMIT %s
        """,
        (safe_limit,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    for row in rows:
        try:
            row["commands"] = json.loads(row.pop("commands_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            row["commands"] = {}
        if row.get("created_at"):
            row["created_at"] = row["created_at"].isoformat()
    return rows


def load_plant_health_states() -> dict[str, dict]:
    """Load cumulative HP so a backend restart does not reset every crop."""
    conn = db_pool.get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT crop_key, hp, is_dead, health_status, environment_status,
               status_text, last_delta, hp_rate_per_hour, stress_json,
               last_updated_at
        FROM plant_health_state
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = {}
    for row in rows:
        stress = {}
        try:
            stress = json.loads(row.pop("stress_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            pass
        crop_key = row.pop("crop_key")
        row["is_dead"] = bool(row["is_dead"])
        row["stress_factors"] = stress.get("stress_factors", [])
        row["factor_zones"] = stress.get("factor_zones", {})
        if row.get("last_updated_at"):
            row["last_updated_at"] = row["last_updated_at"].isoformat()
        result[crop_key] = row
    return result


def save_plant_health_states(summary: dict[str, dict]) -> None:
    """Upsert the latest cumulative HP state for every crop."""
    conn = db_pool.get_connection()
    cur = conn.cursor()
    values = []
    for crop_key, state in summary.items():
        last_updated_at = state.get("last_updated_at")
        if isinstance(last_updated_at, str):
            last_updated_at = datetime.fromisoformat(last_updated_at)
        if isinstance(last_updated_at, datetime) and last_updated_at.tzinfo:
            last_updated_at = last_updated_at.astimezone(timezone.utc).replace(
                tzinfo=None
            )
        values.append(
            (
                crop_key,
                state["hp"],
                state["is_dead"],
                state["health_status"],
                state["environment_status"],
                state["status_text"],
                state["last_delta"],
                state["hp_rate_per_hour"],
                json.dumps(
                    {
                        "stress_factors": state.get("stress_factors", []),
                        "factor_zones": state.get("factor_zones", {}),
                    },
                    ensure_ascii=False,
                ),
                last_updated_at,
            )
        )
    cur.executemany(
        """
        INSERT INTO plant_health_state (
            crop_key, hp, is_dead, health_status, environment_status,
            status_text, last_delta, hp_rate_per_hour, stress_json,
            last_updated_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            hp = VALUES(hp),
            is_dead = VALUES(is_dead),
            health_status = VALUES(health_status),
            environment_status = VALUES(environment_status),
            status_text = VALUES(status_text),
            last_delta = VALUES(last_delta),
            hp_rate_per_hour = VALUES(hp_rate_per_hour),
            stress_json = VALUES(stress_json),
            last_updated_at = VALUES(last_updated_at)
        """,
        values,
    )
    cur.close()
    conn.close()


print("MySQL connected - schema ready")
