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

from mysql.connector import pooling
db_pool = pooling.MySQLConnectionPool(
    pool_name="mypool",
    pool_size=10,
    pool_reset_session=True,
    **_config
)

def _ensure_table():
    conn = db_pool.get_connection()
    cur = conn.cursor()
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
    """Tạo kết nối riêng — dùng cho MQTT thread để tránh race condition."""
    return mysql.connector.connect(**_config)

def get_training_window(hours: int = 6, max_rows: int = 500) -> list:
    """
    Lấy dữ liệu lịch sử SÂU HƠN giới hạn 100 bản ghi của GET /api/history —
    dành riêng cho huấn luyện mô hình dự đoán (Giai đoạn 3 - Analytics Layer,
    implementation_plan.md mục 3.2). Không dùng chung với ManagedCursor vì cần
    cursor(dictionary=True) để trả về dict thay vì tuple.

    Trả về danh sách dict sắp xếp theo created_at TĂNG DẦN (cũ → mới) — ngược
    thứ tự với /api/history (DESC) vì ARIMA/backtest cần chuỗi thời gian xuôi.
    """
    conn = db_pool.get_connection()
    cur = conn.cursor(dictionary=True)
    # DESC + LIMIT trước để lấy đúng max_rows bản ghi GẦN NHẤT trong cửa sổ
    # `hours` giờ (nếu dùng ASC + LIMIT sẽ lấy nhầm max_rows bản ghi CŨ NHẤT
    # trong cửa sổ đó khi dữ liệu vượt quá max_rows) — rồi đảo lại thành ASC.
    cur.execute("""
        SELECT temperature, humidity, soil_moisture, light_level,
               fan_status, pump_status, servo_angle, created_at
        FROM sensor_data
        WHERE created_at >= NOW() - INTERVAL %s HOUR
        ORDER BY created_at DESC
        LIMIT %s
    """, (hours, max_rows))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    rows.reverse()
    return rows

print("MySQL Connected — table ready")