#!/usr/bin/env python3
"""Render the badge's shared compass drawing as an offline desktop preview.

Run from any directory:
    python tools/preview.py

The SVG adapter intentionally implements the small ctx subset used by the
badge renderer. The HTML embeds those same rendered frames, so the desktop
preview and the badge always share their artwork.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _number(value: float) -> str:
    """Compact SVG coordinates, without insignificant floating point noise."""
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


class SVGContext:
    """Minimal immediate-mode ctx adapter in badge-centered coordinates."""

    LEFT = 0
    CENTER = 1
    RIGHT = 2

    def __init__(self) -> None:
        self.font_size = 12
        self.text_align = self.LEFT
        self.line_width = 1
        self._colour = "#ffffff"
        self._stack = []
        self._path: list[str] = []
        self._point: tuple[float, float] | None = None
        self._elements: list[str] = []

    def save(self) -> "SVGContext":
        self._stack.append(
            (self.font_size, self.text_align, self.line_width, self._colour)
        )
        return self

    def restore(self) -> "SVGContext":
        if self._stack:
            self.font_size, self.text_align, self.line_width, self._colour = self._stack.pop()
        return self

    def rgb(self, red: float, green: float, blue: float) -> "SVGContext":
        scale = 255 if max(red, green, blue) <= 1 else 1
        channels = [round(max(0, min(255, channel * scale))) for channel in (red, green, blue)]
        self._colour = "#" + "".join(f"{channel:02x}" for channel in channels)
        return self

    def move_to(self, x: float, y: float) -> "SVGContext":
        self._path.append(f"M{_number(x)} {_number(y)}")
        self._point = (x, y)
        return self

    def line_to(self, x: float, y: float) -> "SVGContext":
        self._path.append(f"L{_number(x)} {_number(y)}")
        self._point = (x, y)
        return self

    def close_path(self) -> "SVGContext":
        self._path.append("Z")
        return self

    def rectangle(self, x: float, y: float, width: float, height: float) -> "SVGContext":
        self.move_to(x, y)
        self.line_to(x + width, y)
        self.line_to(x + width, y + height)
        self.line_to(x, y + height)
        self.close_path()
        return self

    def arc(
        self,
        x: float,
        y: float,
        radius: float,
        start: float,
        end: float,
        direction: int = 1,
    ) -> "SVGContext":
        """Append a ctx circular arc. Angles are radians; +1 is clockwise."""
        if radius <= 0:
            return self
        clockwise = direction >= 0
        delta = end - start
        full_circle = abs(delta) >= math.tau - 1e-7
        if full_circle:
            delta = math.tau if clockwise else -math.tau
        elif clockwise:
            delta %= math.tau
        else:
            delta = -((-delta) % math.tau)
        first = (x + radius * math.cos(start), y + radius * math.sin(start))
        if self._point is None:
            self.move_to(*first)
        elif math.dist(self._point, first) > 1e-6:
            self.line_to(*first)
        if abs(delta) < 1e-9:
            return self
        # Two half arcs represent a full circle reliably in every SVG viewer.
        parts = 2 if full_circle else 1
        for part in range(1, parts + 1):
            angle = start + delta * part / parts
            endpoint = (x + radius * math.cos(angle), y + radius * math.sin(angle))
            large_arc = int(abs(delta / parts) > math.pi)
            sweep = int(clockwise)
            self._path.append(
                f"A{_number(radius)} {_number(radius)} 0 {large_arc} {sweep} "
                f"{_number(endpoint[0])} {_number(endpoint[1])}"
            )
            self._point = endpoint
        return self

    def fill(self) -> "SVGContext":
        return self._paint(stroke=False)

    def stroke(self) -> "SVGContext":
        return self._paint(stroke=True)

    def _paint(self, stroke: bool) -> "SVGContext":
        if self._path:
            path_data = " ".join(self._path)
            style = (
                f'fill="none" stroke="{self._colour}" stroke-width="{_number(self.line_width)}" '
                'stroke-linecap="round" stroke-linejoin="round"'
                if stroke
                else f'fill="{self._colour}"'
            )
            self._elements.append(f'<path d="{path_data}" {style}/>')
        self._path = []
        self._point = None
        return self

    def text(self, content: str) -> "SVGContext":
        x, y = self._point or (0, 0)
        anchors = {self.LEFT: "start", self.CENTER: "middle", self.RIGHT: "end"}
        anchor = anchors.get(self.text_align, "start")
        self._elements.append(
            f'<text x="{_number(x)}" y="{_number(y)}" fill="{self._colour}" '
            f'font-size="{_number(self.font_size)}" text-anchor="{anchor}" '
            f'font-family="DejaVu Sans,Arial,sans-serif">{html.escape(str(content))}</text>'
        )
        self._path = []
        self._point = None
        return self

    def to_svg(self) -> str:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" '
            'viewBox="-120 -120 240 240" role="img" aria-label="Spaceagon field compass">'
            + "".join(self._elements)
            + "</svg>"
        )


def render_frame(heading: float, elapsed: float = 0.0) -> str:
    from compass_view import draw_compass

    context = SVGContext()
    draw_compass(
        context,
        heading=heading,
        turn_rate=18.0,
        elapsed=elapsed,
        mode="DEMO",
        message="",
        calibration_progress=None,
        show_help=False,
        tilt=(0.0, 0.0),
    )
    return context.to_svg()


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spaceagon · Field compass preview</title>
<style>
:root{color-scheme:dark;--ink:#edf7f3;--muted:#81938f;--teal:#7bd9bd;--gold:#d2b776}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:#101514;color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background-image:radial-gradient(ellipse at 50% 25%,#20302b75,transparent 62%)}
main{max-width:1020px;margin:0 auto;padding:42px 32px 28px}header{display:flex;justify-content:space-between;align-items:center;margin-bottom:26px}.wordmark{font-size:13px;font-weight:650;letter-spacing:.24em}.version{color:var(--muted);font-size:11px;letter-spacing:.13em}h1{font-size:clamp(26px,4vw,38px);font-weight:450;letter-spacing:-.035em;line-height:1.18;margin:0}.subtitle{margin:10px 0 0;color:#91a59c;max-width:490px}.workspace{margin-top:30px;display:grid;grid-template-columns:minmax(0,1.35fr) minmax(240px,.8fr);gap:42px;align-items:center;padding:40px;border:1px solid #31423b;border-radius:22px;background:linear-gradient(135deg,#17211ddd,#111816dd);box-shadow:0 30px 90px #0003}.device-wrap{position:relative;aspect-ratio:1;display:flex;align-items:center;justify-content:center}.device-wrap:before{content:"";position:absolute;inset:5%;border-radius:50%;background:#77c7ab0b;box-shadow:0 0 100px 10px #53b69810}.device{position:relative;width:100%;aspect-ratio:1;padding:9px;border-radius:50%;background:linear-gradient(135deg,#3b4741,#111512 34%,#2a342e);box-shadow:0 18px 45px #000b,inset 0 1px 1px #d5e3d830,inset 0 -1px 2px #000}.screen{height:100%;overflow:hidden;border-radius:50%;background:#030706;box-shadow:0 0 0 1px #000,inset 0 0 15px #000}#badge svg{display:block;width:100%;height:100%}.eyebrow{font-size:10px;text-transform:uppercase;letter-spacing:.18em;color:var(--gold);margin-bottom:14px}.degree{font-size:72px;font-weight:350;line-height:1;font-variant-numeric:tabular-nums;letter-spacing:-.055em}.degree .symbol{color:#839e91;font-size:44px;vertical-align:top;line-height:1.2;margin-left:3px}.direction{margin-top:10px;color:#bad7ca;font-size:14px;letter-spacing:.05em}.status{margin:22px 0 28px;padding:12px 0;border-top:1px solid #304138;border-bottom:1px solid #304138;color:#92a59b;font-size:12px;line-height:1.65}.status:before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--gold);margin-right:8px;vertical-align:1px}.controls label{display:flex;justify-content:space-between;color:#bbcfc2;font-size:12px;margin-bottom:12px}.controls label span:last-child{color:#738d7e}input[type=range]{width:100%;accent-color:var(--teal);height:4px;cursor:pointer;margin:0 0 26px}.buttons{display:flex;gap:8px}button{border:1px solid #466855;border-radius:8px;padding:11px 13px;background:#214231;color:#c6f4dc;cursor:pointer;font:inherit;font-size:12px}button:hover{background:#2c503d}button.secondary{background:transparent;border-color:#344a3e;color:#a2bcae}button.secondary:hover{background:#203027}.hint{color:#6f8779;font-size:11px;line-height:1.7;margin:22px 0 0}.footer{display:flex;justify-content:space-between;gap:20px;margin-top:20px;color:#6c8074;font-size:11px}.footer span:last-child{text-align:right}
@media(max-width:740px){main{padding:28px 20px}.workspace{grid-template-columns:1fr;padding:28px;gap:36px}.device-wrap{max-width:390px;width:100%;margin:auto}.controls{max-width:390px;width:100%;margin:auto}.degree{font-size:60px}.footer{flex-direction:column;gap:6px}.footer span:last-child{text-align:left}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
</style>
</head>
<body><main>
<header><div class="wordmark">SPACEAGON</div><div class="version">LOCAL PREVIEW · 240 × 240</div></header>
<h1>A compass, in motion.</h1>
<p class="subtitle">A sculpted field of teal and gold. Turn the heading to see the shared badge artwork respond.</p>
<div class="workspace">
 <div class="device-wrap"><div class="device"><div id="badge" class="screen">__INITIAL_FRAME__</div></div></div>
 <div class="controls">
  <div class="eyebrow">Field compass</div>
  <div class="degree"><span id="degrees">000</span><span class="symbol">°</span></div>
  <div class="direction" id="direction">NORTH</div>
  <div class="status">Simulated heading — no connected badge</div>
  <label for="heading"><span>Heading</span><span>0–359°</span></label>
  <input id="heading" type="range" min="0" max="359" value="0" step="1" aria-label="Simulated compass heading">
  <div class="buttons"><button id="play" type="button" aria-pressed="true">Pause rotation</button><button id="north" class="secondary" type="button">Face north</button></div>
  <p class="hint">Drag to explore. The preview uses SVG frames generated by the badge’s own drawing function.</p>
 </div>
</div>
<div class="footer"><span>Offline preview · no external assets</span><span>Motion at 18° per second</span></div>
</main>
<script>
const frames=__FRAMES__;
const step=__STEP__;
const badge=document.getElementById('badge'),slider=document.getElementById('heading'),degrees=document.getElementById('degrees'),direction=document.getElementById('direction'),play=document.getElementById('play');
let heading=0,playing=!matchMedia('(prefers-reduced-motion: reduce)').matches,last=null,lastFrame=-1;
const directions=['NORTH','NORTH EAST','EAST','SOUTH EAST','SOUTH','SOUTH WEST','WEST','NORTH WEST'];
function show(){const value=Math.round(heading)%360,index=Math.round(heading/step)%frames.length;if(index!==lastFrame){badge.innerHTML=frames[index];lastFrame=index}degrees.textContent=String(value).padStart(3,'0');slider.value=value;direction.textContent=directions[Math.round(value/45)%8]}
function updateButton(){play.textContent=playing?'Pause rotation':'Resume rotation';play.setAttribute('aria-pressed',String(playing))}
play.addEventListener('click',()=>{playing=!playing;last=null;updateButton()});
slider.addEventListener('input',()=>{playing=false;heading=Number(slider.value);updateButton();show()});
document.getElementById('north').addEventListener('click',()=>{playing=false;heading=0;updateButton();show()});
document.addEventListener('visibilitychange',()=>{last=null});
function tick(now){if(last!==null&&playing&&!document.hidden){heading=(heading+Math.min((now-last)/1000,.1)*18)%360;show()}last=now;requestAnimationFrame(tick)}
show();updateButton();requestAnimationFrame(tick);
</script></body></html>
"""


def generate(output_dir: Path, step: int = 2) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = [render_frame(heading, heading / 18) for heading in range(0, 360, step)]
    # The embedded JSON contains generated SVG only; escape script terminators
    # anyway, so text added to the renderer cannot end the script element.
    encoded_frames = json.dumps(frames, separators=(",", ":")).replace("</", "<\\/")
    document = HTML_TEMPLATE.replace("__INITIAL_FRAME__", frames[0])
    document = document.replace("__FRAMES__", encoded_frames).replace("__STEP__", str(step))
    html_path = output_dir / "preview.html"
    svg_path = output_dir / "preview.svg"
    html_path.write_text(document, encoding="utf-8")
    svg_path.write_text(frames[0], encoding="utf-8")
    paths = [html_path, svg_path]
    png_path = output_dir / "preview.png"
    try:
        import resvg_py
    except ImportError:
        try:
            import cairosvg
        except (ImportError, OSError):
            pass
        else:
            try:
                cairosvg.svg2png(bytestring=frames[0].encode("utf-8"), write_to=str(png_path), output_width=720, output_height=720)
            except (OSError, RuntimeError) as error:
                print(f"PNG rendering unavailable: {error}", file=sys.stderr)
            else:
                paths.append(png_path)
    else:
        try:
            png_path.write_bytes(resvg_py.svg_to_bytes(svg_string=frames[0], width=720, height=720))
        except (OSError, ValueError) as error:
            print(f"PNG rendering unavailable: {error}", file=sys.stderr)
        else:
            paths.append(png_path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--step", type=int, choices=(1, 2, 3, 4, 5, 6, 10), default=2, help="Degrees between pre-rendered frames")
    args = parser.parse_args()
    for path in generate(args.output_dir, args.step):
        print(path)


if __name__ == "__main__":
    main()
