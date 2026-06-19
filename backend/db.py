import mysql.connector
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME

_config = {
    "host":     DB_HOST,
    "user":     DB_USER,
    "password": DB_PASSWORD,
    "database": DB_NAME,
    "use_pure": True,
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
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()
    cur.close()

_ensure_table()

def get_cursor():
    try:
        db.ping(reconnect=True, attempts=3, delay=2)
    except mysql.connector.Error:
        globals()["db"] = mysql.connector.connect(**_config)
    return db.cursor()

def commit():
    db.commit()

print("MySQL Connected — table ready")