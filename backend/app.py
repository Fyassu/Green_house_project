from flask import Flask, request, jsonify
from flask_cors import CORS

from db import get_cursor, commit

app = Flask(__name__)
CORS(app)


# ==========================
# HEALTH CHECK
# ==========================
@app.route("/api/test", methods=["GET"])
def test():
    try:
        cursor = get_cursor()
        cursor.execute("SELECT COUNT(*) FROM sensor_data")
        count = cursor.fetchone()[0]
        cursor.close()
        return jsonify({"status": "ok", "rows": count})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


# ==========================
# POST SENSOR DATA
# ==========================
@app.route("/api/sensor-data", methods=["POST"])
def save_sensor_data():

    print("\n========== NEW REQUEST ==========")
    print("Content-Type:", request.content_type)
    print("Raw Data:", request.data)

    data = request.get_json(silent=True)

    print("Parsed JSON:", data)

    if data is None:
        return jsonify({
            "error": "JSON parse failed"
        }), 400

    try:

        sql = """
        INSERT INTO sensor_data(
            temperature,
            humidity,
            soil_moisture,
            light_level,
            motion_detected,
            fan_status,
            pump_status,
            servo_angle
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """

        values = (
            data["temperature"],
            data["humidity"],
            data["soil_moisture"],
            data["light_level"],
            data["motion_detected"],
            data["fan_status"],
            data["pump_status"],
            data["servo_angle"]
        )

        cursor = get_cursor()
        cursor.execute(sql, values)
        cursor.close()
        commit()

        print("Data saved OK")

        return jsonify({
            "message": "saved"
        })

    except Exception as e:

        print("DATABASE ERROR:")
        print(str(e))

        return jsonify({
            "error": str(e)
        }), 500


# ==========================
# GET LATEST RECORD
# ==========================
@app.route("/api/latest", methods=["GET"])
def latest_data():

    try:

        cursor = get_cursor()
        cursor.execute("""
            SELECT *
            FROM sensor_data
            ORDER BY id DESC
            LIMIT 1
        """)

        row = cursor.fetchone()
        cursor.close()

        if row is None:
            return jsonify({
                "message": "No data"
            })

        return jsonify({
            "id": row[0],
            "temperature": row[1],
            "humidity": row[2],
            "soil_moisture": row[3],
            "light_level": row[4],
            "motion_detected": bool(row[5]),
            "fan_status": bool(row[6]),
            "pump_status": bool(row[7]),
            "servo_angle": row[8],
            "created_at": str(row[9])
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500


# ==========================
# GET HISTORY
# ==========================
@app.route("/api/history", methods=["GET"])
def history():

    try:

        cursor = get_cursor()
        cursor.execute("""
            SELECT *
            FROM sensor_data
            ORDER BY created_at DESC
            LIMIT 100
        """)

        rows = cursor.fetchall()
        cursor.close()

        result = []

        for row in rows:

            result.append({
                "id": row[0],
                "temperature": row[1],
                "humidity": row[2],
                "soil_moisture": row[3],
                "light_level": row[4],
                "motion_detected": bool(row[5]),
                "fan_status": bool(row[6]),
                "pump_status": bool(row[7]),
                "servo_angle": row[8],
                "created_at": str(row[9])
            })

        return jsonify(result)

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500


# ==========================
# MAIN
# ==========================
if __name__ == "__main__":

    print("Flask Server Started")

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )

