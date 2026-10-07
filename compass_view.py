"""240-pixel compass artwork, shared by the badge and desktop preview.

All coordinates are relative to the centre of the badge screen. No bitmap,
network, third-party library, or desktop-only API is required on the badge.
"""
import math

TAU = 2 * math.pi
TEAL = (0.28, 0.88, 0.74)
GOLD = (1.0, 0.72, 0.38)
INK = (0.025, 0.032, 0.041)
_RINGS = tuple(
    tuple((math.sin(i * TAU / count) * radius,
           -math.cos(i * TAU / count) * radius) for i in range(count))
    for radius, count in ((19, 20), (33, 34), (47, 48), (61, 62))
)
_CURVE = tuple((math.sin(i * TAU / 32), math.cos(i * TAU / 32))
               for i in range(33))


def _text(ctx, text, x, y, size, color):
    ctx.rgb(*color)
    ctx.font_size = size
    ctx.text_align = ctx.CENTER
    ctx.move_to(x, y).text(text)


def _dot(ctx, x, y, radius, color):
    ctx.rgb(*color)
    ctx.arc(x, y, radius, 0, TAU, True).fill()


def _projection(yaw, pitch, roll):
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    c, s = math.cos(yaw), math.sin(yaw)
    # Precompute once per frame; no trigonometry inside the point projector.
    return (cr * c, sp * sr * c - cp * s, cp * sr * c + sp * s,
            cr * s, sp * sr * s + cp * c, cp * sr * s - sp * c,
            -sr, sp * cr, cp * cr)


def _project(x, y, z, matrix):
    scale = 240 / (240 + x * matrix[6] + y * matrix[7] + z * matrix[8])
    return ((x * matrix[0] + y * matrix[1] + z * matrix[2]) * scale,
            (x * matrix[3] + y * matrix[4] + z * matrix[5]) * scale)


def draw_compass(ctx, heading=0.0, turn_rate=0.0, elapsed=0.0,
                 mode="DEMO", message="", calibration_progress=None,
                 show_help=False, tilt=(0.0, 0.0)):
    """Draw with the native Tildagon ctx API. Angles in degrees; tilt in radians."""
    ctx.save()
    ctx.rgb(*INK).rectangle(-120, -120, 240, 240).fill()
    ctx.line_width = 1
    # Quiet circular reticle; dot positions are computed once at import time.
    for ring in _RINGS:
        ctx.rgb(0.30, 0.33, 0.37)
        for x, y in ring:
            ctx.rectangle(x - 0.45, y - 0.45, 0.9, 0.9)
        ctx.fill()
    ctx.rgb(0.35, 0.39, 0.43)
    for i in range(24):
        a = i * TAU / 24
        x, y = math.sin(a) * 99, -math.cos(a) * 99
        if i % 6 == 0:
            ctx.move_to(x, y).line_to(x * 1.065, y * 1.065)
        else:
            ctx.rectangle(x - 0.6, y - 0.6, 1.2, 1.2)
    ctx.stroke()

    yaw = -math.radians(heading or 0)
    pitch = max(-0.65, min(0.65, tilt[0]))
    roll = max(-0.65, min(0.65, tilt[1]))
    matrix = _projection(yaw, pitch, roll)
    # A little elastic shear responds to turning, like the filament motion in
    # the reference reel. It never changes the sensor heading or pole positions.
    shear = max(-0.5, min(0.5, turn_rate / 180.0))
    for strand in range(11):
        longitude = strand * math.pi / 11 + 0.10
        radius = 66 + (strand % 3) * 5
        for quarter in range(4):
            green = (1 + math.cos((quarter + 0.5) * math.pi / 2)) / 2
            strength = 0.45 + 0.35 * abs(math.cos(longitude))
            base = tuple(a * green + b * (1 - green) for a, b in zip(TEAL, GOLD))
            ctx.rgb(*(component * strength for component in base))
            ctx.line_width = 0.7
            for j in range(9):
                index = j + quarter * 8
                st, ct = _CURVE[index]
                phi = longitude + shear * st * st
                x = radius * st * math.cos(phi)
                z = radius * st * math.sin(phi)
                y = -46 * ct
                px, py = _project(x, y, z, matrix)
                if j == 0:
                    ctx.move_to(px, py)
                else:
                    ctx.line_to(px, py)
            ctx.stroke()

    # Poles follow the very same projection as the filaments.
    for north, color in ((True, TEAL), (False, GOLD)):
        x, y = _project(0, -46 if north else 46, 0, matrix)
        _dot(ctx, x, y, 8, tuple(c * 0.12 for c in color))
        ctx.rgb(*color)
        ctx.line_width = 1.1
        ctx.arc(x, y, 5.6, 0, TAU, True).stroke()
        _dot(ctx, x, y, 3.0, color)
        if north:
            dx, dy = _project(0, -62, 0, matrix)
            _text(ctx, "N", dx, dy + 3, 9, TEAL)

    # Fixed index at the top, rotating north pole below it.
    ctx.rgb(*TEAL)
    ctx.move_to(-2, -110).line_to(2, -110).line_to(0, -106).close_path().fill()
    _text(ctx, mode, 0, -93, 6, (0.40, 0.51, 0.56))
    if message:
        _text(ctx, message[:28], 0, -79, 7, (0.70, 0.72, 0.70))
    value = "---" if heading is None else "%03d" % (int(heading + 0.5) % 360)
    _text(ctx, value + "\u00b0", 0, 101, 21, (0.75, 0.79, 0.89))

    if calibration_progress is not None:
        ctx.rgb(0.07, 0.10, 0.12).rectangle(-64, -19, 128, 43).fill()
        _text(ctx, "ROTATE ALL AXES", 0, -6, 8, TEAL)
        ctx.rgb(0.16, 0.23, 0.24).rectangle(-48, 5, 96, 3).fill()
        ctx.rgb(*TEAL).rectangle(-48, 5, 96 * max(0, min(1, calibration_progress)), 3).fill()
        _text(ctx, "C save  /  F cancel", 0, 19, 7, (0.70, 0.74, 0.75))
    if show_help:
        ctx.rgb(0.04, 0.07, 0.085).rectangle(-91, -61, 182, 129).fill()
        _text(ctx, "FIELD COMPASS", 0, -43, 12, TEAL)
        for text, y in (("A   hide / show help", -23),
                        ("B/E   demo rotation", -7),
                        ("C   calibrate sensors", 9),
                        ("D   demo / live sensors", 25),
                        ("F   return to launcher", 41),
                        ("Keep away from magnets", 59)):
            _text(ctx, text, 0, y, 8, (0.78, 0.81, 0.83))
    ctx.restore()
