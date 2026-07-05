import mysql.connector
import bcrypt
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME

_config = {
    "host":       DB_HOST,
    "user":       DB_USER,
    "password":   DB_PASSWORD,
    "database":   DB_NAME,
    "use_pure":   True,
    "autocommit": True,
}

db = mysql.connector.connect(**_config)

def _ensure_table():
    cur = db.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            temperature     FLOAT,
            humidity        FLOAT,
            soil_moisture   INT,
            light_level     INT,
            motion_detected BOOLEAN,
            fan_status      BOOLEAN,
            pump_status     BOOLEAN,
            servo_angle     INT,
            light_status    BOOLEAN DEFAULT FALSE,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Schema migration: thêm light_status nếu bảng cũ chưa có
    cur.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'sensor_data'
          AND COLUMN_NAME  = 'light_status'
    """)
    if cur.fetchone()[0] == 0:
        cur.execute(
            "ALTER TABLE sensor_data ADD COLUMN light_status BOOLEAN DEFAULT FALSE"
        )

    # Bảng users cho JWT authentication
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            username      VARCHAR(64) UNIQUE NOT NULL,
            password_hash VARCHAR(128) NOT NULL
        )
    """)
    # Seed tài khoản admin mặc định nếu chưa có
    cur.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
    if cur.fetchone()[0] == 0:
        hashed = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode()
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES ('admin', %s)", (hashed,)
        )
    cur.close()

_ensure_table()

def get_cursor():
    try:
        db.ping(reconnect=True, attempts=3, delay=2)
    except mysql.connector.Error:
        globals()["db"] = mysql.connector.connect(**_config)
    return db.cursor()

def get_new_connection():
    """Tạo kết nối riêng — dùng cho MQTT thread để tránh race condition."""
    return mysql.connector.connect(**_config)

print("MySQL Connected — table ready")