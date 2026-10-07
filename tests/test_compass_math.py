import math
import unittest

from compass_math import (HardIronCalibration, HeadingController, angle_delta,
                          magnetic_heading, smooth_heading)


class Sensor:
    def __init__(self, mag=None, acc=(0, 0, 9.81), gyro=(0, 0, 0)):
        self.mag, self.acc, self.gyro = mag, acc, gyro

    def mag_read(self):
        if isinstance(self.mag, Exception):
            raise self.mag
        return self.mag

    def acc_read(self):
        return self.acc

    def gyro_read(self):
        if isinstance(self.gyro, Exception):
            raise self.gyro
        return self.gyro


def calibrated():
    calibration = HardIronCalibration()
    for _ in range(16):
        calibration.add((0.7, 0.3, 0.4))
        calibration.add((-0.3, -0.7, -0.6))
    return calibration


class CompassMathTests(unittest.TestCase):
    def test_cardinal_headings_and_invalid_fields(self):
        for vector, heading in [((0.4, 0, 0), 0), ((0, -0.4, 0), 90),
                                ((-0.4, 0, 0), 180), ((0, 0.4, 0), 270)]:
            self.assertAlmostEqual(magnetic_heading(vector), heading)
        for vector in (None, (0, 0, 0), (1, 2), (float("nan"), 0, 0),
                       (float("inf"), 0, 0), (0, 0, 0.5)):
            self.assertIsNone(magnetic_heading(vector))

    def test_tilt_compensation_for_combined_roll_pitch(self):
        # Rotate a north-facing magnetic/gravity vector into tilted body axes.
        roll, pitch = math.radians(34), math.radians(-27)
        sr, cr = math.sin(roll), math.cos(roll)
        sp, cp = math.sin(pitch), math.cos(pitch)
        for heading in (0, 48, 137, 298):
            h = math.radians(heading)
            x, y, z = 0.4 * math.cos(h), -0.4 * math.sin(h), 0.2
            mag = (cp * x - sp * z,
                   sr * sp * x + cr * y + sr * cp * z,
                   cr * sp * x - sr * y + cr * cp * z)
            acc = (-9.81 * sp, 9.81 * sr * cp, 9.81 * cr * cp)
            self.assertAlmostEqual(magnetic_heading(mag, acc), heading, places=8)
        self.assertIsNone(magnetic_heading((0.4, 0, 0.2), (9.81, 0, 0)))

    def test_wraparound_smoothing_uses_short_path(self):
        self.assertEqual(angle_delta(1, 359), 2)
        self.assertEqual(angle_delta(359, 1), -2)
        heading = smooth_heading(359, 1, 220)
        self.assertLess(abs(angle_delta(heading, 0)), 1)
        self.assertEqual(smooth_heading(None, 361, 0), 1)

    def test_calibration_rejects_one_plane_and_removes_offsets(self):
        calibration = HardIronCalibration()
        for i in range(40):
            angle = i * math.pi / 20
            calibration.add((0.2 + 0.5 * math.cos(angle), -0.2 + 0.5 * math.sin(angle), 0.1))
        self.assertFalse(calibration.valid)
        calibration.add((0.2, -0.2, -0.4))
        calibration.add((0.2, -0.2, 0.6))
        self.assertTrue(calibration.valid)
        for actual, expected in zip(calibration.offsets, (0.2, -0.2, 0.1)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(magnetic_heading(calibration.apply((0.2, -0.7, 0.1))), 90)
        self.assertFalse(calibration.add((float("nan"), 0, 0)))

    def test_persisted_calibration_validation_is_atomic(self):
        calibration = calibrated()
        restored = HardIronCalibration()
        self.assertTrue(restored.load(calibration.to_dict()))
        before = restored.to_dict()
        for bad in ({}, {"minimum": [1, 2, 3], "maximum": [0, 0, 0], "samples": 90},
                    {"minimum": [0, 0, 0], "maximum": [1, 1, float("inf")], "samples": 90}):
            self.assertFalse(restored.load(bad))
            self.assertEqual(restored.to_dict(), before)
        self.assertTrue(restored.load(HardIronCalibration().to_dict()))
        self.assertFalse(restored.valid)
        self.assertEqual(restored.samples, 0)

    def test_raw_and_calibrated_modes_are_honestly_labeled(self):
        controller = HeadingController(Sensor(mag=(0.7, -0.2, -0.1)), smoothing_tau_ms=0)
        self.assertEqual(controller.update(50)["mode"], "magnetic_raw")
        controller.calibration = calibrated()
        state = controller.update(50)
        self.assertEqual(state["mode"], "compass")
        self.assertTrue(state["calibrated"])
        self.assertAlmostEqual(state["heading"], 0)

    def test_initial_gyro_bias_then_relative_rotation(self):
        sensor = Sensor(gyro=(0.2, -0.1, 0.5))
        controller = HeadingController(sensor, smoothing_tau_ms=0, gyro_bias_duration_ms=1000)
        for _ in range(9):
            state = controller.update(100)
            self.assertEqual(state["mode"], "gyro_calibrating")
            self.assertIsNone(state["heading"])
        state = controller.update(100)
        self.assertEqual(state["mode"], "gyro_relative")
        self.assertAlmostEqual(state["heading"], 0)
        sensor.gyro = (0.2, -0.1, -89.5)
        for _ in range(10):
            state = controller.update(100)
        self.assertAlmostEqual(state["heading"], 90)
        self.assertEqual(state["gyro_bias_progress"], 1)

    def test_movement_and_missing_acceleration_restart_bias_window(self):
        sensor = Sensor(gyro=(0, 0, 0.5))
        controller = HeadingController(sensor, gyro_bias_duration_ms=1000)
        for _ in range(7):
            controller.update(100)
        sensor.gyro = (6, 0, 0.5)
        self.assertEqual(controller.update(100)["gyro_bias_progress"], 0)
        sensor.gyro, sensor.acc = (0, 0, 0.5), None
        for _ in range(20):
            self.assertEqual(controller.update(100)["mode"], "gyro_calibrating")
        sensor.acc = (0, 0, 9.81)
        for _ in range(10):
            state = controller.update(100)
        self.assertEqual(state["mode"], "gyro_relative")

    def test_sensor_loss_retains_heading_and_recovers_without_zero(self):
        sensor = Sensor(mag=(0, -0.4, 0), gyro=None)
        controller = HeadingController(sensor, smoothing_tau_ms=0)
        self.assertAlmostEqual(controller.update(50)["heading"], 90)
        sensor.mag = OSError("I2C disconnected")
        state = controller.update(50)
        self.assertEqual(state["mode"], "sensor_fault")
        self.assertTrue(state["stale"])
        self.assertAlmostEqual(state["heading"], 90)
        sensor.mag = (-0.4, 0, 0)
        self.assertAlmostEqual(controller.update(50)["heading"], 180)

    def test_fallback_anchors_to_last_magnetic_heading(self):
        sensor = Sensor(mag=(0, -0.4, 0), gyro=(0, 0, 0))
        controller = HeadingController(sensor, smoothing_tau_ms=0, gyro_bias_duration_ms=1000)
        for _ in range(10):
            controller.update(100)
        sensor.mag, sensor.gyro = None, (0, 0, -10)
        state = controller.update(100)
        self.assertEqual(state["mode"], "gyro_relative")
        self.assertAlmostEqual(state["heading"], 91)
        sensor.gyro = OSError("gyro disconnected")
        self.assertAlmostEqual(controller.update(100)["heading"], 91)

    def test_bad_calibration_keeps_previous_and_collection_active(self):
        controller = HeadingController(Sensor(mag=(0.4, 0, 0)))
        previous = calibrated()
        controller.calibration = previous
        controller.start_calibration()
        for _ in range(40):
            controller.update(20)
        self.assertFalse(controller.finish_calibration())
        self.assertIs(controller.calibration, previous)
        self.assertTrue(controller.update(20)["calibration_active"])
        controller.cancel_calibration()
        self.assertFalse(controller.update(20)["calibration_active"])

    def test_successful_calibration_commits_and_reports_coverage(self):
        sensor = Sensor()
        controller = HeadingController(sensor)
        controller.start_calibration()
        for i in range(32):
            sensor.mag = (0.7, 0.3, 0.4) if i % 2 else (-0.3, -0.7, -0.6)
            state = controller.update(20)
        self.assertTrue(state["calibration_ready"])
        self.assertEqual(state["calibration_progress"], 1)
        self.assertTrue(controller.finish_calibration())
        self.assertTrue(controller.calibration.valid)
        self.assertFalse(controller.update(20)["calibration_active"])

    def test_alignment_uses_unsmoothed_heading_and_rejects_sensor_fault(self):
        sensor = Sensor(mag=(0.7, -0.2, -0.1))
        controller = HeadingController(sensor, smoothing_tau_ms=220)
        controller.calibration = calibrated()
        controller.update(50)
        sensor.mag = (0.2, -0.7, -0.1)
        self.assertLess(controller.update(10)["heading"], 10)
        self.assertTrue(controller.align_north())
        self.assertAlmostEqual(controller.heading_offset_deg, 270)
        self.assertAlmostEqual(controller.update(50)["heading"], 0)
        sensor.mag, sensor.gyro = None, None
        controller.update(50)
        self.assertFalse(controller.align_north())

    def test_reset_gyro_requires_new_stationary_reference(self):
        sensor = Sensor(gyro=(0, 0, 0.5))
        controller = HeadingController(sensor, smoothing_tau_ms=0, gyro_bias_duration_ms=1000)
        for _ in range(10):
            controller.update(100)
        sensor.gyro = (0, 0, -89.5)
        for _ in range(10):
            controller.update(100)
        self.assertAlmostEqual(controller.last_rate, 90)
        controller.reset_gyro()
        state = controller.update(100)
        self.assertEqual(state["mode"], "gyro_calibrating")
        self.assertIsNone(state["heading"])
        sensor.gyro = (0, 0, 0.8)
        for _ in range(10):
            state = controller.update(100)
        self.assertEqual(state["mode"], "gyro_relative")
        self.assertAlmostEqual(state["heading"], 0)
        self.assertAlmostEqual(controller.last_rate, 0)

    def test_long_pause_and_invalid_time_do_not_jump_heading(self):
        sensor = Sensor(gyro=(0, 0, 0))
        controller = HeadingController(sensor, smoothing_tau_ms=0, gyro_bias_duration_ms=1000)
        for _ in range(10):
            controller.update(100)
        sensor.gyro = (0, 0, -90)
        self.assertAlmostEqual(controller.update(5000)["heading"], 22.5)
        for delta in (-100, float("nan"), None):
            self.assertAlmostEqual(controller.update(delta)["heading"], 22.5)

    def test_empty_driver_and_extreme_finite_values_are_faults(self):
        controller = HeadingController(Sensor(mag=(0, 0, 0), acc=(0, 0, 0), gyro=(0, 0, 0)))
        self.assertEqual(controller.update(100)["mode"], "sensor_fault")
        self.assertIsNone(controller.update(100)["heading"])
        controller.sensor = Sensor(mag=(1e308, 0, 0), acc=(1e308, 0, 0), gyro=(1e308, 0, 0))
        self.assertEqual(controller.update(100)["mode"], "sensor_fault")
        self.assertIsNone(controller.last_acc)
        self.assertEqual(controller.last_rate, 0)

    def test_gyro_only_adapter_without_acc_method_still_calibrates(self):
        class GyroOnly:
            def gyro_read(self):
                return (0, 0, 0.5)
        controller = HeadingController(GyroOnly(), gyro_bias_duration_ms=1000)
        for _ in range(10):
            state = controller.update(100)
        self.assertEqual(state["mode"], "gyro_relative")
        self.assertFalse(state["tilt_compensated"])


if __name__ == "__main__":
    unittest.main()
