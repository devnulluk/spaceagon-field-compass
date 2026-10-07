"""Native Spaceagon/Tildagon app for the magnetic-field compass."""
import math
import app
from events.input import Buttons, BUTTON_TYPES

try:
    import imu
except ImportError:
    imu = None

try:
    import ujson as json
except ImportError:
    import json

try:
    from .compass_math import HeadingController, HardIronCalibration
    from .compass_view import draw_compass
except ImportError:
    from compass_math import HeadingController, HardIronCalibration
    from compass_view import draw_compass

SETTINGS_PATH = "/data/spaceagon_compass.json"


class FieldCompassApp(app.App):
    def __init__(self):
        super().__init__()
        self.button_states = Buttons(self)
        self.sensor = HeadingController(imu)
        self.elapsed = 0.0
        self.demo = False
        self.demo_heading = 0.0
        self.demo_rate = 18.0
        self.help = False
        self.calibrating = False
        self.aligning = False
        self.calibration_backup = None
        self.message = ""
        self.message_until = 0.0
        self.tilt = (0.0, 0.0)
        self.state = {"heading": None, "mode": "sensor_fault", "message": "Starting sensors"}
        self._load_settings()

    def _notice(self, text, seconds=5):
        self.message = text
        self.message_until = self.elapsed + seconds

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, "r") as stream:
                data = json.load(stream)
            offset = float(data.get("heading_offset_deg", 0))
            declination = float(data.get("declination_deg", 0))
            if not (math.isfinite(offset) and math.isfinite(declination)):
                return
            self.sensor.calibration.load(data.get("calibration", {}))
            self.sensor.heading_offset_deg = offset % 360
            self.sensor.declination_deg = declination
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

    def _save_settings(self):
        import os
        try:
            try:
                os.mkdir("/data")
            except OSError:
                pass
            data = {"version": 1, "heading_offset_deg": self.sensor.heading_offset_deg,
                    "declination_deg": self.sensor.declination_deg,
                    "calibration": self.sensor.calibration.to_dict()}
            # One write per completed calibration; no flash writes per frame.
            with open(SETTINGS_PATH + ".tmp", "w") as stream:
                json.dump(data, stream)
            os.rename(SETTINGS_PATH + ".tmp", SETTINGS_PATH)
            return True
        except (OSError, ValueError, TypeError):
            return False

    def _button(self, name):
        return self.button_states.get(BUTTON_TYPES[name])

    def _cancel_calibration(self):
        self.sensor.cancel_calibration()
        if self.calibration_backup is not None:
            data, offset = self.calibration_backup
            self.sensor.calibration = HardIronCalibration()
            self.sensor.calibration.load(data)
            self.sensor.heading_offset_deg = offset
        self.calibration_backup = None
        self.calibrating = False
        self.aligning = False
        self._notice("Calibration cancelled")

    def _calibration_button(self):
        if self.demo:
            self._notice("D switches to live sensors")
        elif self.aligning:
            if self.sensor.align_north():
                self.aligning = False
                self.calibration_backup = None
                self._notice("Saved magnetic north" if self._save_settings()
                             else "Calibrated; save failed")
            else:
                self._notice("Wait for magnetic reading")
        elif self.calibrating:
            if self.sensor.finish_calibration():
                self.calibrating = False
                self.aligning = True
                self._notice("Point top north; press C", 600)
            else:
                self._notice("Keep rotating all axes")
        elif self.state["mode"].startswith("gyro"):
            self.sensor.reset_gyro()
            self._notice("Hold still to zero gyro")
        elif self.state["mode"] == "sensor_fault":
            self._notice("Sensor unavailable; D demo")
        else:
            self.help = False
            self.calibration_backup = (self.sensor.calibration.to_dict(),
                                       self.sensor.heading_offset_deg)
            self.sensor.start_calibration()
            self.calibrating = True
            self._notice("Slow figure-eight motion")

    def update(self, delta):
        dt = max(0.0, min(float(delta), 250.0))
        self.elapsed += dt / 1000
        self.state = self.sensor.update(dt)
        if self._button("CANCEL"):
            self.button_states.clear()
            if self.calibrating or self.aligning:
                self._cancel_calibration()
            else:
                self.minimise()
            return
        if self._button("UP"):
            self.help = not self.help
            self.button_states.clear()
        elif self._button("CONFIRM"):
            self._calibration_button()
            self.button_states.clear()
        elif self._button("DOWN"):
            if self.calibrating or self.aligning:
                self._cancel_calibration()
            self.demo = not self.demo
            self.help = False
            if self.demo:
                self.demo_heading = self.state["heading"] or 0
                self.demo_rate = 18.0
            self._notice("Simulated heading" if self.demo else "Live sensors")
            self.button_states.clear()
        elif self._button("RIGHT") or self._button("LEFT"):
            if self.demo:
                self.demo_heading = (self.demo_heading +
                                     (15 if self._button("RIGHT") else -15)) % 360
                self.demo_rate = 0.0
            else:
                self._notice("D opens the animated demo")
            self.button_states.clear()
        if self.demo:
            self.demo_heading = (self.demo_heading + self.demo_rate * dt / 1000) % 360
        acc = getattr(self.sensor, "last_acc", None)
        if acc:
            x, y, z = acc
            # Tilt affects the artwork only; the controller handles compensation.
            target = (math.atan2(y, math.sqrt(x * x + z * z)),
                      math.atan2(-x, math.sqrt(y * y + z * z)))
            alpha = dt / (180 + dt)
            self.tilt = tuple(a + alpha * (b - a) for a, b in zip(self.tilt, target))

    def draw(self, ctx):
        names = {"compass": "MAGNETIC", "magnetic_raw": "UNCALIBRATED",
                 "gyro_calibrating": "GYRO ZEROING", "gyro_relative": "RELATIVE GYRO",
                 "sensor_fault": "SENSOR FAULT"}
        message = self.message if self.elapsed < self.message_until else self.state.get("message", "")
        if self.aligning:
            message = "Point top north; press C"
        progress = None
        if self.calibrating:
            progress = self.state.get("calibration_progress", 0)
        draw_compass(ctx,
                     heading=self.demo_heading if self.demo else self.state["heading"],
                     turn_rate=self.demo_rate if self.demo else getattr(self.sensor, "last_rate", 0),
                     elapsed=self.elapsed,
                     mode="DEMO" if self.demo else names.get(self.state["mode"], "SENSOR FAULT"),
                     message=message, calibration_progress=progress,
                     show_help=self.help, tilt=self.tilt)

__app_export__ = FieldCompassApp
