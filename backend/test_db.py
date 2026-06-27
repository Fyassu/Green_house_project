import sys
import mysql.connector
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME

_config = {
    "host":     DB_HOST,
    "user":     DB_USER,
    "password": DB_PASSWORD,
    "database": DB_NAME,
}

SEP  = "─" * 60
SEP2 = "═" * 60

# ─────────────────────────────────────────────
def connect():
    try:
        conn = mysql.connector.connect(**_config)
        print(f"✓ Connected to MySQL  →  database: {_config['database']}\n")
        return conn
    except mysql.connector.Error as e:
        print(f"✗ MySQL connection failed: {e}")
        sys.exit(1)

# ─────────────────────────────────────────────
def check_table(cursor):
    print(SEP)
    print("TABLE STRUCTURE")
    print(SEP)
    cursor.execute("DESCRIBE sensor_data")
    rows = cursor.fetchall()
    if not rows:
        print("  Table 'sensor_data' not found!")
        return False
    print(f"  {'Column':<20} {'Type':<20} {'Null':<6} {'Key':<6} {'Default'}")
    print(f"  {'──────':<20} {'────':<20} {'────':<6} {'───':<6} {'───────'}")
    for r in rows:
        print(f"  {str(r[0]):<20} {str(r[1]):<20} {str(r[2]):<6} {str(r[3]):<6} {str(r[4])}")
    print()
    return True

# ─────────────────────────────────────────────
def check_count(cursor):
    print(SEP)
    print("ROW COUNT")
    print(SEP)
    cursor.execute("SELECT COUNT(*) FROM sensor_data")
    count = cursor.fetchone()[0]
    print(f"  Total records : {count}")

    cursor.execute("SELECT MIN(created_at), MAX(created_at) FROM sensor_data")
    row = cursor.fetchone()
    if row[0]:
        print(f"  Oldest record : {row[0]}")
        print(f"  Newest record : {row[1]}")
    print()
    return count

# ─────────────────────────────────────────────
def check_latest(cursor, n=5):
    print(SEP)
    print(f"LATEST {n} RECORDS")
    print(SEP)
    cursor.execute(f"""
        SELECT id, temperature, humidity, soil_moisture,
               light_level, motion_detected,
               fan_status, pump_status, servo_angle, light_status, created_at
        FROM sensor_data
        ORDER BY id DESC
        LIMIT {n}
    """)
    rows = cursor.fetchall()
    if not rows:
        print("  No data yet.\n")
        return

    for r in rows:
        print(f"  [#{r[0]}]  {r[10]}")
        print(f"    Temp={r[1]}°C  Hum={r[2]}%  Soil={r[3]}%  Light={r[4]}%")
        print(f"    Motion={'YES' if r[5] else 'no '}  Fan={'ON ' if r[6] else 'off'}  "
              f"Pump={'ON ' if r[7] else 'off'}  Roof={r[8]}°  MotionLight={'ON ' if r[9] else 'off'}")
        print()

# ─────────────────────────────────────────────
def check_stats(cursor):
    print(SEP)
    print("SENSOR STATISTICS  (all records)")
    print(SEP)
    cursor.execute("""
        SELECT
            ROUND(MIN(temperature),1),  ROUND(MAX(temperature),1),  ROUND(AVG(temperature),1),
            ROUND(MIN(humidity),1),     ROUND(MAX(humidity),1),     ROUND(AVG(humidity),1),
            ROUND(MIN(soil_moisture),1),ROUND(MAX(soil_moisture),1),ROUND(AVG(soil_moisture),1),
            ROUND(MIN(light_level),1),  ROUND(MAX(light_level),1),  ROUND(AVG(light_level),1)
        FROM sensor_data
    """)
    r = cursor.fetchone()
    if not r or r[0] is None:
        print("  No data yet.\n")
        return

    print(f"  {'Metric':<16} {'Min':>8} {'Max':>8} {'Avg':>8}")
    print(f"  {'──────':<16} {'───':>8} {'───':>8} {'───':>8}")
    print(f"  {'Temperature':<16} {str(r[0])+' °C':>8} {str(r[1])+' °C':>8} {str(r[2])+' °C':>8}")
    print(f"  {'Humidity':<16} {str(r[3])+'  %':>8} {str(r[4])+'  %':>8} {str(r[5])+'  %':>8}")
    print(f"  {'Soil Moisture':<16} {str(r[6])+'  %':>8} {str(r[7])+'  %':>8} {str(r[8])+'  %':>8}")
    print(f"  {'Light Level':<16} {str(r[9])+'  %':>8} {str(r[10])+'  %':>8} {str(r[11])+'  %':>8}")
    print()

# ─────────────────────────────────────────────
def check_actuators(cursor):
    print(SEP)
    print("ACTUATOR ACTIVITY  (all records)")
    print(SEP)
    cursor.execute("""
        SELECT
            SUM(fan_status),    COUNT(*),
            SUM(pump_status),
            SUM(motion_detected),
            SUM(light_status)
        FROM sensor_data
    """)
    r = cursor.fetchone()
    if not r or r[1] == 0:
        print("  No data yet.\n")
        return

    total  = r[1]
    fan    = int(r[0] or 0)
    pump   = int(r[2] or 0)
    motion = int(r[3] or 0)
    light  = int(r[4] or 0)

    def pct(n): return f"{n}/{total} ({100*n//total}%)"

    print(f"  Fan ON          : {pct(fan)}")
    print(f"  Pump ON         : {pct(pump)}")
    print(f"  Motion detected : {pct(motion)}")
    print(f"  Motion light ON : {pct(light)}")
    print()

# ─────────────────────────────────────────────
def main():
    print()
    print(SEP2)
    print("  smart_greenhouse  —  Database Test")
    print(SEP2)
    print()

    conn   = connect()
    cursor = conn.cursor()

    ok = check_table(cursor)
    if not ok:
        conn.close()
        sys.exit(1)

    count = check_count(cursor)
    if count > 0:
        check_latest(cursor)
        check_stats(cursor)
        check_actuators(cursor)
    else:
        print("  Database is empty — run Wokwi simulation to populate data.\n")

    cursor.close()
    conn.close()
    print(SEP2)
    print("  Test complete")
    print(SEP2)

if __name__ == "__main__":
    main()
