"""Desktop integration tests for the real badge app and heading controller.

Only the badge event transport and physical sensors are substituted. These
tests exercise calibration transactions, persistence, and honest fault/demo
presentation through the app's public update/draw lifecycle.
"""

import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.preview import SVGContext


class FakeBadgeApp:
    def minimise(self):
        self.minimised = True


class EdgeButtons:
    """Match badge Buttons' latched press events and clear operation."""

    def __init__(self, owner):
        self.pending = set()
        self.held = set()

    def press(self, name):
        if name not in self.held:
            self.pending.add(name)
        self.held.add(name)

    def release(self, name):
        self.held.discard(name)

    def get(self, button):
        return button in self.pending

    def clear(self):
        self.pending.clear()


class FakeIMU(types.ModuleType):
    def __init__(self, magnetic=(0.4, 0.0, 0.1), gyro=None):
        super().__init__("imu")
        self.magnetic = magnetic
        self.acceleration = (0.0, 0.0, 9.81)
        self.gyro = gyro
        self.failed = False

    def _read(self, reading):
        if self.failed:
            raise OSError("sensor disconnected")
        return reading

    def mag_read(self):
        return self._read(self.magnetic)

    def acc_read(self):
        return self._read(self.acceleration)

    def gyro_read(self):
        return self._read(self.gyro)


class AppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Path(self.directory.name) / "compass.json"
        badge_api = types.ModuleType("app")
        badge_api.App = FakeBadgeApp
        events = types.ModuleType("events")
        events.__path__ = []
        inputs = types.ModuleType("events.input")
        inputs.Buttons = EdgeButtons
        inputs.BUTTON_TYPES = {
            name: name for name in ("UP", "RIGHT", "CONFIRM", "DOWN", "LEFT", "CANCEL")
        }
        self.hardware = FakeIMU()
        with mock.patch.dict(sys.modules, {"app": badge_api, "events": events,
                                          "events.input": inputs, "imu": self.hardware}):
            spec = importlib.util.spec_from_file_location("spaceagon_badge_app_test", ROOT / "app.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.module.SETTINGS_PATH = str(self.settings)

    def make_app(self, sensor=None):
        self.module.imu = self.hardware if sensor is None else sensor
        return self.module.FieldCompassApp()

    def pulse(self, app, name, delta=0):
        app.button_states.press(name)
        app.update(delta)
        app.button_states.release(name)

    def svg(self, app):
        ctx = SVGContext()
        app.draw(ctx)
        return ctx.to_svg()

    def fit_calibration(self, app):
        self.pulse(app, "CONFIRM")
        self.assertTrue(app.calibrating)
        for index in range(36):
            self.hardware.magnetic = (-0.5, 0.2, -0.7) if index % 2 else (0.7, -0.6, 0.4)
            app.update(40)
        self.pulse(app, "CONFIRM")
        self.assertFalse(app.calibrating)
        self.assertTrue(app.aligning)
        self.assertTrue(app.sensor.calibration.valid)

    def test_missing_sensors_stay_faulted_until_demo_is_explicitly_selected(self):
        self.module.imu = None
        app = self.module.FieldCompassApp()
        app.update(100)
        self.assertFalse(app.demo)
        self.assertIsNone(app.state["heading"])
        self.assertEqual(app.state["mode"], "sensor_fault")
        self.assertIn("SENSOR FAULT", self.svg(app))
        self.assertIn("---", self.svg(app))

        self.pulse(app, "DOWN", 100)
        self.assertTrue(app.demo)
        first_heading = app.demo_heading
        app.update(100)
        self.assertGreater(app.demo_heading, first_heading)
        self.assertIn(">DEMO<", self.svg(app))
        self.assertIsNone(app.state["heading"])
        self.pulse(app, "DOWN")
        self.assertFalse(app.demo)
        self.assertEqual(app.state["mode"], "sensor_fault")

    def test_disconnection_preserves_last_heading_and_reports_fault(self):
        self.hardware.magnetic = (0.0, -0.4, 0.1)
        app = self.make_app()
        app.update(100)
        heading = app.state["heading"]
        self.assertAlmostEqual(heading, 90)
        self.hardware.failed = True
        for _ in range(5):
            app.update(250)
        self.assertAlmostEqual(app.state["heading"], heading)
        self.assertTrue(app.state["stale"])
        self.assertEqual(app.state["mode"], "sensor_fault")
        self.assertFalse(app.demo)
        self.assertIn("SENSOR FAULT", self.svg(app))

    def test_held_press_is_consumed_once_then_requires_a_new_press_edge(self):
        app = self.make_app()
        for name, attribute in (("UP", "help"), ("DOWN", "demo")):
            with self.subTest(button=name):
                app.button_states.press(name)
                app.update(20)
                self.assertTrue(getattr(app, attribute))
                for _ in range(10):
                    app.button_states.press(name)
                    app.update(20)
                self.assertTrue(getattr(app, attribute))
                app.button_states.release(name)
                self.pulse(app, name)
                self.assertFalse(getattr(app, attribute))

    def test_cancel_during_collection_keeps_saved_calibration_and_offset(self):
        app = self.make_app()
        original = {"minimum": [-0.4, -0.4, -0.4], "maximum": [0.6, 0.8, 0.9], "samples": 40}
        self.assertTrue(app.sensor.calibration.load(original))
        app.sensor.heading_offset_deg = 37.0
        app.update(30)
        self.pulse(app, "CONFIRM")
        self.hardware.magnetic = (0.8, -0.6, 0.4)
        app.update(30)
        self.pulse(app, "CANCEL")
        self.assertFalse(app.calibrating)
        self.assertFalse(app.aligning)
        self.assertEqual(app.sensor.calibration.to_dict(), original)
        self.assertEqual(app.sensor.heading_offset_deg, 37.0)
        self.assertFalse(self.settings.exists())
        self.assertFalse(getattr(app, "minimised", False))

    def test_cancel_after_fitting_restores_prior_calibration_even_when_uncalibrated(self):
        for calibrated in (False, True):
            with self.subTest(previously_calibrated=calibrated):
                app = self.make_app()
                if calibrated:
                    app.sensor.calibration.load({"minimum": [-0.4] * 3, "maximum": [0.4] * 3, "samples": 40})
                original = app.sensor.calibration.to_dict()
                app.sensor.heading_offset_deg = 22.0
                app.update(40)
                self.fit_calibration(app)
                self.pulse(app, "CANCEL")
                self.assertFalse(app.aligning)
                self.assertEqual(app.sensor.calibration.valid, calibrated)
                self.assertEqual(app.sensor.calibration.to_dict(), original)
                self.assertEqual(app.sensor.heading_offset_deg, 22.0)
                self.assertFalse(self.settings.exists())

    def test_incomplete_calibration_keeps_collecting_without_committing(self):
        app = self.make_app()
        app.update(40)
        self.pulse(app, "CONFIRM")
        for _ in range(4):
            app.update(40)
        self.pulse(app, "CONFIRM")
        self.assertTrue(app.calibrating)
        self.assertFalse(app.aligning)
        self.assertFalse(app.sensor.calibration.valid)
        self.assertEqual(app.message, "Keep rotating all axes")
        self.assertFalse(self.settings.exists())

    def test_alignment_rejects_stale_reading_after_sensor_disconnect(self):
        app = self.make_app()
        app.update(40)
        self.fit_calibration(app)
        heading = app.state["heading"]
        offset = app.sensor.heading_offset_deg
        self.hardware.failed = True
        self.pulse(app, "CONFIRM", 40)
        self.assertTrue(app.aligning)
        self.assertEqual(app.message, "Wait for magnetic reading")
        self.assertEqual(app.sensor.heading_offset_deg, offset)
        self.assertEqual(app.state["heading"], heading)
        self.assertFalse(self.settings.exists())

    def test_completed_alignment_persists_and_reloads_the_real_calibration(self):
        app = self.make_app()
        app.update(40)
        self.fit_calibration(app)
        self.hardware.magnetic = (0.5, -0.3, 0.1)
        # Avoid creating /data on the desktop; the actual settings write and
        # rename still run against the temporary directory.
        with mock.patch("os.mkdir", side_effect=FileExistsError):
            self.pulse(app, "CONFIRM", 40)
        self.assertFalse(app.aligning)
        self.assertIsNone(app.calibration_backup)
        self.assertTrue(self.settings.exists())
        saved = json.loads(self.settings.read_text())
        self.assertEqual(saved["calibration"], app.sensor.calibration.to_dict())
        self.assertEqual(saved["heading_offset_deg"], app.sensor.heading_offset_deg)
        restored = self.make_app()
        self.assertTrue(restored.sensor.calibration.valid)
        self.assertEqual(restored.sensor.calibration.to_dict(), app.sensor.calibration.to_dict())
        self.assertEqual(restored.sensor.heading_offset_deg, app.sensor.heading_offset_deg)
        self.assertFalse(Path(str(self.settings) + ".tmp").exists())

    def test_corrupt_settings_cannot_crash_startup_or_install_nonfinite_offsets(self):
        for content in ("not json", "[]", '{"heading_offset_deg":NaN}',
                        '{"declination_deg":1e999}', '{"calibration":{"samples":"bad"}}'):
            with self.subTest(settings=content):
                self.settings.write_text(content)
                app = self.make_app()
                app.update(30)
                self.assertTrue(math.isfinite(app.sensor.heading_offset_deg))
                self.assertTrue(math.isfinite(app.sensor.declination_deg))
                self.assertFalse(app.sensor.calibration.valid)
                self.assertIsNotNone(app.state["heading"])

    def test_save_failure_keeps_runtime_heading_and_calibration(self):
        app = self.make_app()
        app.update(40)
        self.fit_calibration(app)
        self.hardware.magnetic = (0.5, -0.3, 0.1)
        with mock.patch("os.mkdir", side_effect=FileExistsError), \
                mock.patch.object(self.module, "open", side_effect=OSError("storage unavailable"), create=True):
            self.pulse(app, "CONFIRM", 40)
        self.assertFalse(app.aligning)
        self.assertTrue(app.sensor.calibration.valid)
        self.assertEqual(app.message, "Calibrated; save failed")
        app.update(100)
        self.assertIsNotNone(app.state["heading"])
        self.assertEqual(app.state["mode"], "compass")

    def test_gyro_calibration_button_restarts_zeroing_in_relative_mode(self):
        self.hardware.magnetic = None
        self.hardware.gyro = (0.0, 0.0, 0.2)
        app = self.make_app()
        for _ in range(20):
            app.update(100)
        self.assertEqual(app.state["mode"], "gyro_relative")
        self.hardware.gyro = (0.0, 0.0, -35.0)
        for _ in range(10):
            app.update(100)
        self.assertGreater(app.state["heading"], 10)
        self.pulse(app, "CONFIRM")
        self.hardware.gyro = (0.0, 0.0, 0.2)
        app.update(100)
        self.assertEqual(app.state["mode"], "gyro_calibrating")
        self.assertIn("zero gyro", app.message)

    def test_actual_renderer_produces_finite_geometry_and_keeps_text_on_screen(self):
        from compass_view import draw_compass

        namespace = {"svg": "http://www.w3.org/2000/svg"}
        for heading in (None, 0, 90, 179, 270, 359):
            for help_visible in (False, True):
                with self.subTest(heading=heading, help=help_visible):
                    ctx = SVGContext()
                    draw_compass(ctx, heading=heading, tilt=(0.6, -0.6),
                                 message="Point top north; press C", show_help=help_visible)
                    svg = ctx.to_svg()
                    self.assertNotRegex(svg.lower(), r"\b(?:nan|inf(?:inity)?)\b")
                    document = ET.fromstring(svg)
                    for path in document.findall("svg:path", namespace):
                        values = re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", path.get("d"))
                        self.assertTrue(all(math.isfinite(float(value)) for value in values))
                    for text in document.findall("svg:text", namespace):
                        x, y, size = (float(text.get(key)) for key in ("x", "y", "font-size"))
                        # Conservative sans-serif bounding box at the chosen
                        # size, checked against the circular badge viewport.
                        half_width = len(text.text or "") * size * 0.36
                        for tx in (x - half_width, x + half_width):
                            for ty in (y - size, y + size * 0.25):
                                self.assertLessEqual(math.hypot(tx, ty), 120)


if __name__ == "__main__":
    unittest.main()
