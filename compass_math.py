"""Heading helpers for the EMF 2026 Spaceagon frontboard (MicroPython).

Sensor interface: mag_read() -> (x, y, z) in gauss, acc_read() -> m/s^2,
gyro_read() -> degrees/second. Any of the three methods may be absent.
At rest with +Z up, heading is clockwise from magnetic north along +X.
Use heading_offset_deg to align +X with the artwork/device orientation.
Declination is optional; without it the compass reports magnetic north.

Hard-iron calibration removes the midpoint of each magnetic axis. It does
not correct soft-iron distortion or guarantee accuracy near metal/magnets.
Gyro fallback is relative, drifts with time, and cannot determine north.
"""

import math


def _finite(value):
    try:
        value = float(value)
        return value if value == value and abs(value) != float("inf") else None
    except (TypeError, ValueError, OverflowError):
        return None


def _vector(value):
    try:
        if len(value) != 3:
            return None
        values = tuple(_finite(v) for v in value)
        return values if all(v is not None for v in values) else None
    except (TypeError, ValueError):
        return None


def _norm(vector):
    return math.sqrt(sum(v * v for v in vector))


def wrap_degrees(angle):
    wrapped = angle % 360.0
    # Tiny negative roundoff can make modulo round to exactly 360.0.
    return 0.0 if wrapped == 360.0 else wrapped


def angle_delta(target, current):
    """Shortest signed rotation from current to target, in degrees."""
    return (target - current + 180.0) % 360.0 - 180.0


def smooth_heading(current, target, dt_ms, tau_ms=220):
    """Time-based smoothing that takes the short route across north."""
    if current is None or tau_ms <= 0:
        return wrap_degrees(target)
    alpha = 1.0 - math.exp(-max(0.0, dt_ms) / tau_ms)
    return wrap_degrees(current + alpha * angle_delta(target, current))


def magnetic_heading(magnetometer, acceleration=None):
    """Return a clockwise heading, or None for invalid/vertical fields.

    A valid gravity vector enables roll/pitch compensation. With no gravity
    vector this uses the XY plane, so the device must be held level.
    Accelerometer readings should have +Z upward when the device is level.
    """
    mag = _vector(magnetometer)
    if mag is None or not 0.000001 < _norm(mag) < 100.0:
        return None
    mx, my, mz = mag
    acc = _vector(acceleration)
    if acc is not None and 7.8 <= _norm(acc) <= 11.8:
        ax, ay, az = acc
        roll = math.atan2(ay, az)
        pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az))
        # Forward direction is undefined when the device points vertically.
        if abs(math.cos(pitch)) < 0.08:
            return None
        sr, cr = math.sin(roll), math.cos(roll)
        sp, cp = math.sin(pitch), math.cos(pitch)
        hx = mx * cp + my * sr * sp + mz * cr * sp
        hy = my * cr - mz * sr
    else:
        hx, hy = mx, my
    if math.sqrt(hx * hx + hy * hy) < 0.000001:
        return None
    return wrap_degrees(math.atan2(-hy, hx) * 180.0 / math.pi)


class HardIronCalibration:
    """Min/max calibration; rotate through all three axes before finishing."""

    def __init__(self, min_samples=32, min_span=0.08):
        self.min_samples = min_samples
        self.min_span = min_span
        self.minimum = None
        self.maximum = None
        self.samples = 0

    def add(self, reading):
        vector = _vector(reading)
        if vector is None or not 0.000001 < _norm(vector) < 100.0:
            return False
        if self.minimum is None:
            self.minimum = list(vector)
            self.maximum = list(vector)
        else:
            for axis in range(3):
                self.minimum[axis] = min(self.minimum[axis], vector[axis])
                self.maximum[axis] = max(self.maximum[axis], vector[axis])
        self.samples += 1
        return True

    @property
    def spans(self):
        if self.minimum is None:
            return (0.0, 0.0, 0.0)
        return tuple(self.maximum[i] - self.minimum[i] for i in range(3))

    @property
    def valid(self):
        return self.samples >= self.min_samples and all(s >= self.min_span for s in self.spans)

    @property
    def offsets(self):
        if self.minimum is None:
            return (0.0, 0.0, 0.0)
        return tuple((self.minimum[i] + self.maximum[i]) / 2.0 for i in range(3))

    def apply(self, reading):
        vector = _vector(reading)
        if vector is None:
            return None
        offsets = self.offsets if self.valid else (0.0, 0.0, 0.0)
        return tuple(vector[i] - offsets[i] for i in range(3))

    def to_dict(self):
        return {"minimum": self.minimum, "maximum": self.maximum, "samples": self.samples}

    def load(self, data):
        """Load atomically; reject malformed data, accept an exact empty reset."""
        try:
            if data["minimum"] is None and data["maximum"] is None and data["samples"] == 0:
                self.minimum = self.maximum = None
                self.samples = 0
                return True
            minimum = _vector(data["minimum"])
            maximum = _vector(data["maximum"])
            samples = int(data["samples"])
            if minimum is None or maximum is None or samples < self.min_samples:
                return False
            spans = tuple(maximum[i] - minimum[i] for i in range(3))
            if not all(self.min_span <= s < 100.0 for s in spans):
                return False
            if _norm(minimum) >= 100.0 or _norm(maximum) >= 100.0:
                return False
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        self.minimum, self.maximum, self.samples = list(minimum), list(maximum), samples
        return True


class HeadingController:
    """Read sensors and return one small status dictionary per display frame.

    update(dt_ms) preserves the last display heading during transient faults.
    Modes: compass (calibrated magnetic), magnetic_raw (uncalibrated),
    gyro_calibrating, gyro_relative, sensor_fault. heading is None until a
    reading or gyro reference exists. stale distinguishes retained readings.
    Startup gyro calibration requires at least 10 stationary samples across
    gyro_bias_duration_ms. Moving or missing data restarts that sample window.
    Integration/smoothing time is capped at 250 ms to avoid a jump after a
    paused app. Gyro-relative reference starts at the last magnetic heading
    when available, otherwise zero; zero is a local reference, never north.
    """

    def __init__(self, sensor, declination_deg=0.0, heading_offset_deg=0.0,
                 smoothing_tau_ms=220, gyro_bias_duration_ms=1500):
        self.sensor = sensor
        self.declination_deg = declination_deg
        self.heading_offset_deg = heading_offset_deg
        self.smoothing_tau_ms = smoothing_tau_ms
        self.gyro_bias_duration_ms = max(1, gyro_bias_duration_ms)
        self.calibration = HardIronCalibration()
        self._pending_calibration = None
        self._heading = None
        self._relative_heading = None
        self._gyro_bias = None
        self._bias_time = 0.0
        self._bias_samples = 0
        self._bias_sum = [0.0, 0.0, 0.0]
        self._mode = "sensor_fault"
        self._magnetic_target = None
        self.last_acc = None
        self.last_rate = 0.0

    def _read(self, method):
        try:
            reader = getattr(self.sensor, method, None)
            vector = _vector(reader()) if reader is not None else None
            # Broad sensor limits reject corrupt finite values before they
            # can overflow heading integration or the renderer's geometry.
            maximum = 4000.0 if method == "gyro_read" else (200.0 if method == "acc_read" else 100.0)
            return vector if vector is not None and _norm(vector) <= maximum else None
        except Exception:
            # A disconnected/initialising I2C sensor must not terminate the UI.
            return None

    def start_calibration(self):
        self._pending_calibration = HardIronCalibration()

    def finish_calibration(self):
        """Keep collecting on failure; preserve the last good calibration."""
        pending = self._pending_calibration
        if pending is None or not pending.valid:
            return False
        self.calibration = pending
        self._pending_calibration = None
        return True

    def cancel_calibration(self):
        self._pending_calibration = None

    def reset_gyro(self):
        """Rebias while stationary and start a fresh relative zero reference."""
        self._gyro_bias = None
        self._bias_time = 0.0
        self._bias_samples = 0
        self._bias_sum = [0.0, 0.0, 0.0]
        self._relative_heading = 0.0
        self._heading = None
        self.last_rate = 0.0

    def align_north(self):
        """With the device pointing north, align a calibrated live reading.

        Uses the unsmoothed magnetic target, so display smoothing cannot skew
        the alignment. This needs an external known north reference; it does
        not discover the device's physical axis orientation automatically.
        """
        if self._mode != "compass" or self._magnetic_target is None or not self.calibration.valid:
            return False
        self.heading_offset_deg = wrap_degrees(self.heading_offset_deg - self._magnetic_target)
        self._heading = self._relative_heading = 0.0
        self._magnetic_target = 0.0
        return True

    def reset_relative(self):
        """Set the gyro's local reference to zero; magnetic modes ignore it."""
        if self._mode != "gyro_relative":
            return False
        self._relative_heading = 0.0
        self._heading = 0.0
        return True

    def _calibrate_gyro(self, gyro, acc, dt_ms):
        if self._gyro_bias is not None:
            return
        has_acc = getattr(self.sensor, "acc_read", None) is not None
        stationary = gyro is not None and _norm(gyro) <= 3.0
        if has_acc:
            stationary = stationary and acc is not None and 7.8 <= _norm(acc) <= 11.8
        if not stationary:
            self._bias_time = 0.0
            self._bias_samples = 0
            self._bias_sum = [0.0, 0.0, 0.0]
            return
        if dt_ms <= 0:
            return
        self._bias_time += dt_ms
        self._bias_samples += 1
        for i in range(3):
            self._bias_sum[i] += gyro[i] * dt_ms
        if self._bias_time >= self.gyro_bias_duration_ms and self._bias_samples >= 10:
            self._gyro_bias = tuple(v / self._bias_time for v in self._bias_sum)

    def update(self, dt_ms):
        dt = _finite(dt_ms)
        dt = min(250.0, max(0.0, dt if dt is not None else 0.0))
        mag, acc, gyro = self._read("mag_read"), self._read("acc_read"), self._read("gyro_read")
        # The badge driver returns all-zero vectors for absent hardware.
        # A gyro-only adapter may omit acc_read(), but a present method with
        # zero gravity and zero gyro is not a stationary calibration sample.
        if gyro == (0.0, 0.0, 0.0) and acc == (0.0, 0.0, 0.0):
            gyro = None
        self.last_acc = acc
        self._calibrate_gyro(gyro, acc, dt)
        pending = self._pending_calibration
        if pending is not None:
            pending.add(mag)
        tilt_compensated = acc is not None and 7.8 <= _norm(acc) <= 11.8
        self.last_rate = 0.0
        if gyro is not None:
            bias = self._gyro_bias if self._gyro_bias is not None else (0.0, 0.0, 0.0)
            rate = tuple(gyro[i] - bias[i] for i in range(3))
            self.last_rate = (-sum(rate[i] * acc[i] for i in range(3)) / _norm(acc)
                              if tilt_compensated else -rate[2])
        target = magnetic_heading(self.calibration.apply(mag), acc)
        self._magnetic_target = None
        stale = False
        if target is not None:
            target = wrap_degrees(target + self.declination_deg + self.heading_offset_deg)
            self._magnetic_target = target
            self._relative_heading = target
            mode = "compass" if self.calibration.valid else "magnetic_raw"
            message = "Magnetic north" if self.calibration.valid else "Uncalibrated magnetic reading"
            if self.calibration.valid and self.declination_deg:
                message = "Declination-adjusted north"
            if not tilt_compensated:
                message += " - hold level"
        elif gyro is not None and self._gyro_bias is not None:
            if self._relative_heading is None:
                self._relative_heading = self._heading if self._heading is not None else 0.0
            self._relative_heading = wrap_degrees(self._relative_heading + self.last_rate * dt / 1000.0)
            target = self._relative_heading
            mode, message = "gyro_relative", "Relative heading - drifts; no north reference"
        elif gyro is not None:
            mode, message = "gyro_calibrating", "Hold still to set relative gyro reference"
            stale = True
        else:
            mode, message = "sensor_fault", "No usable heading sensor; retaining last reading"
            stale = True
        if target is not None:
            self._heading = smooth_heading(self._heading, target, dt, self.smoothing_tau_ms)
        self._mode = mode
        calibration = pending if pending is not None else self.calibration
        progress = 1.0 if self._gyro_bias is not None else min(
            self._bias_time / self.gyro_bias_duration_ms, self._bias_samples / 10.0, 1.0)
        # Coverage is a collection aid, not an estimate of heading accuracy.
        coverage = min(calibration.samples / float(calibration.min_samples),
                       min(calibration.spans) / calibration.min_span, 1.0)
        return {"heading": self._heading, "mode": mode, "message": message,
                "stale": stale, "calibrated": self.calibration.valid,
                "tilt_compensated": tilt_compensated and target is not None,
                "gyro_bias_progress": progress, "calibration_active": pending is not None,
                "calibration_samples": calibration.samples, "calibration_spans": calibration.spans,
                "calibration_ready": calibration.valid, "calibration_progress": coverage}
