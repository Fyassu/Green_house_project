const express = require("express");
const cors = require("cors");

const db = require("./db");

const app = express();

app.use(cors());
app.use(express.json());

app.post("/api/sensor-data", (req, res) => {

  const {
    temperature,
    humidity,
    soil_moisture,
    light_level,
    motion_detected,
    fan_status,
    pump_status,
    servo_angle
  } = req.body;

  const sql = `
    INSERT INTO sensor_data
    (
      temperature,
      humidity,
      soil_moisture,
      light_level,
      motion_detected,
      fan_status,
      pump_status,
      servo_angle
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `;

  db.query(
    sql,
    [
      temperature,
      humidity,
      soil_moisture,
      light_level,
      motion_detected,
      fan_status,
      pump_status,
      servo_angle
    ],
    (err, result) => {

      if (err) {
        console.log(err);
        return res.status(500).json({
          message: "Insert failed"
        });
      }

      res.json({
        message: "Data saved"
      });

    }
  );
});

app.listen(3000, () => {
  console.log("Server running on port 3000");
});