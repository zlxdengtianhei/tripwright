"""The pins of a share page and the position sketch they sit on (scheme "atlas", web_ui_spec D-14).

One numbering for the map and the lists: the day's places to go, in time order, are 1, 2, 3 …; a stop that is one of
several places gets its number and a letter per place (5A, 5B). The shape says the state, not the colour: a filled
round pin is a fixed time, a hollow round pin can move, a hollow square pin with a letter is one of several.

The sketch is an inline SVG drawn from the coordinates the plan has (no base map, said so on the page): the pages show
it as they are, and the script swaps it for a real map when the map library and the tiles load. Places without
coordinates are not drawn and never given invented ones; with none at all the pins stand in order on a line, marked
as order only."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from markupsafe import Markup, escape

WIDTH, HEIGHT = 440, 300
PAD_SIDE, PAD_TOP, PAD_BOTTOM = 28, 44, 34
# grid squares: the smallest that gives at most this many across the drawing
STEPS_M = (250, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000, 250000)
MAX_SQUARES = 12
ROUND = "M0,0 C-4,-8 -12,-12 -12,-22 A12,12 0 1 1 12,-22 C12,-12 4,-8 0,0Z"
SQUARE = "M0,0 L-5,-10 L-18,-10 L-18,-34 L18,-34 L18,-10 L5,-10Z"


@dataclass
class Pin:
    label: str
    shape: str  # fixed | visit | opt
    lat: float | None = None
    lon: float | None = None
    names: tuple[str, str] = ("", "")  # zh, en
    href: str | None = None
    external: bool = True


@dataclass
class Figure:
    pins: list[Pin]
    path: list[str] = field(default_factory=list)       # labels joined by a solid line, in order
    branches: list[tuple[str, str]] = field(default_factory=list)  # dashed: from a label to one of several
    dashed: bool = False                                 # the whole path dashed (order only, the home page)
    legend: bool = True

    def placed(self) -> list[Pin]:
        return [pin for pin in self.pins if pin.lat is not None and pin.lon is not None]

    def missing(self) -> int:
        return len(self.pins) - len(self.placed())


def shape(certainty: str, choices: int) -> str:
    return "opt" if choices > 1 else "fixed" if certainty == "fixed" else "visit"


def number_stops(stops: list[dict]) -> list[list[tuple[str, str]]]:
    """Per stop, per place in it: (label, shape)."""
    labels = []
    for index, stop in enumerate(stops, 1):
        many = len(stop["spots"]) > 1
        form = shape(stop.get("certainty") or "", len(stop["spots"]))
        labels.append([(f"{index}{chr(ord('A') + position) if many else ''}", form) for position in range(len(stop["spots"]))])
    return labels


def leg_ends(leg: dict, stops: list[dict], labels: list[list[tuple[str, str]]], legs: list[dict] = ()) -> tuple[dict | None, dict | None]:
    """The pins at the two ends of a leg: the last stop that ended by the time it starts and the first stop that starts
    once it has ended, each only when no other leg starts in between (a wait at the platform or a queue at the door
    is not another place). A stop of several places is shown by its number alone (which of them the leg reaches is
    not recorded)."""
    def end(index):
        pins = labels[index]
        return dict(label=pins[0][0] if len(pins) == 1 else pins[0][0][:-1], shape=pins[0][1])
    starts = [other.get("start") or "" for other in legs if other is not leg]
    begin, finish = leg.get("start") or "", leg.get("end") or ""

    def clear(low, high):
        return not any(low <= moment < high for moment in starts)
    before = next((end(index) for index, stop in reversed(list(enumerate(stops)))
                   if stop.get("end") and stop["end"] <= begin and clear(stop["end"], begin)), None)
    after = next((end(index) for index, stop in enumerate(stops)
                  if finish and (stop.get("start") or "") >= finish and clear(finish, stop["start"])), None)
    return before, after


SIDE_BY_SIDE = 32


def offsets(figure: Figure) -> dict[str, int]:
    """Label → how far right (pixels) a pin is drawn because an earlier pin has the very same coordinates."""
    seen: dict[tuple[float, float], int] = {}
    shifts = {}
    for pin in figure.placed():
        key = (round(pin.lat, 5), round(pin.lon, 5))
        if key in seen:
            shifts[pin.label] = SIDE_BY_SIDE * seen[key]
        seen[key] = seen.get(key, 0) + 1
    return shifts


def _km(pin: Pin, lat0: float) -> tuple[float, float]:
    return pin.lon * 111.32 * math.cos(math.radians(lat0)), pin.lat * 110.57


def _step(span_km: float) -> int:
    return next((step for step in STEPS_M if span_km * 1000 / step <= MAX_SQUARES), STEPS_M[-1])


def _distance(step_m: int) -> str:
    return f"{step_m // 1000} km" if step_m >= 1000 else f"{step_m} m"


def _pin(pin: Pin, x: float, y: float) -> str:
    path = SQUARE if pin.shape == "opt" else ROUND
    return (f'<g transform="translate({x:.1f},{y:.1f})"><path class="m-pin {pin.shape}" d="{path}"/>'
            f'<text class="m-num {pin.shape}" x="0" y="-17.5" text-anchor="middle">{escape(pin.label)}</text></g>')


def sketch(figure: Figure) -> tuple[Markup, str | None]:
    """The SVG and the grid square it was drawn with ("250 m"), or None when nothing had coordinates."""
    placed = figure.placed()
    parts = [f'<rect class="m-bg" x="0" y="0" width="{WIDTH}" height="{HEIGHT}"/>']
    if not placed:
        # order only: the pins on one line, evenly spaced
        count = len(figure.pins)
        gap = (WIDTH - 2 * PAD_SIDE) / max(count - 1, 1)
        spots = {pin.label: (PAD_SIDE + gap * index if count > 1 else WIDTH / 2, HEIGHT / 2 + 17) for index, pin in enumerate(figure.pins)}
        if count > 1:
            parts.append(f'<path class="m-opt" d="M{PAD_SIDE},{HEIGHT / 2 + 17:.1f} L{WIDTH - PAD_SIDE},{HEIGHT / 2 + 17:.1f}"/>')
        parts += [_pin(pin, *spots[pin.label]) for pin in figure.pins]
        return _svg(parts), None
    lat0 = sum(pin.lat for pin in placed) / len(placed)
    kms = {pin.label: _km(pin, lat0) for pin in placed}
    xs, ys = [k[0] for k in kms.values()], [k[1] for k in kms.values()]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    inner_w, inner_h = WIDTH - 2 * PAD_SIDE, HEIGHT - PAD_TOP - PAD_BOTTOM
    scale = min(inner_w / span_x if span_x else math.inf, inner_h / span_y if span_y else math.inf)
    if scale == math.inf:
        scale = inner_w / 2.0  # one place: a 2 km wide drawing around it
    center_x, center_y = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    middle_x, middle_y = WIDTH / 2, PAD_TOP + inner_h / 2

    def at(km):
        return middle_x + (km[0] - center_x) * scale, middle_y - (km[1] - center_y) * scale

    step = _step(WIDTH / scale)
    step_px = step / 1000 * scale
    first_x = middle_x - math.floor((middle_x) / step_px) * step_px
    first_y = middle_y - math.floor((middle_y) / step_px) * step_px
    x = first_x
    while x <= WIDTH:
        parts.append(f'<line class="m-grid" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{HEIGHT}"/>')
        x += step_px
    y = first_y
    while y <= HEIGHT:
        parts.append(f'<line class="m-grid" x1="0" y1="{y:.1f}" x2="{WIDTH}" y2="{y:.1f}"/>')
        y += step_px
    points = {label: at(km) for label, km in kms.items()}
    # pins at the same spot (a city visited twice) stand side by side, the later ones to the right of the first; the
    # line and the dot stay at the real spot
    shifts = offsets(figure)
    line = [points[label] for label in figure.path if label in points]
    if len(line) > 1:
        parts.append(f'<path class="{"m-opt" if figure.dashed else "m-line"}" d="'
                     + " ".join(f'{"M" if index == 0 else "L"}{px:.1f},{py:.1f}' for index, (px, py) in enumerate(line)) + '"/>')
    for start, finish in figure.branches:
        if start in points and finish in points:
            (ax, ay), (bx, by) = points[start], points[finish]
            parts.append(f'<path class="m-opt" d="M{ax:.1f},{ay:.1f} L{bx:.1f},{by:.1f}"/>')
    parts += [f'<circle class="m-dot" cx="{px:.1f}" cy="{py:.1f}" r="3"/>' for px, py in points.values()]
    # pins drawn north first so a southern pin's head sits over a northern one's foot
    for pin in sorted(placed, key=lambda pin: points[pin.label][1]):
        x, y = points[pin.label]
        parts.append(_pin(pin, x + shifts.get(pin.label, 0), y))
    bar = 2 * step_px if 2 * step_px <= WIDTH / 2 else step_px
    meters = 2 * step if bar != step_px else step
    parts.append(f'<path class="m-scale" d="M16,{HEIGHT - 18} L16,{HEIGHT - 14} L{16 + bar:.1f},{HEIGHT - 14} L{16 + bar:.1f},{HEIGHT - 18}"/>')
    parts.append(f'<text class="m-lbl" x="{22 + bar:.1f}" y="{HEIGHT - 13}">{_distance(meters)}</text>')
    parts.append(f'<text class="m-lbl" x="{WIDTH - 16}" y="{HEIGHT - 13}" text-anchor="end">N ↑</text>')
    return _svg(parts), _distance(step)


def _svg(parts: list[str]) -> Markup:
    return Markup(f'<svg class="sketch" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-hidden="true" '
                  f'xmlns="http://www.w3.org/2000/svg">' + "".join(parts) + "</svg>")


def data(figure: Figure) -> dict:
    """What the script needs to draw the same pins on a real map."""
    placed = figure.placed()
    where = {pin.label: [pin.lat, pin.lon] for pin in placed}
    shifts = offsets(figure)
    return dict(points=[dict(label=pin.label, shape=pin.shape, lat=pin.lat, lon=pin.lon, zh=pin.names[0], en=pin.names[1],
                             href=pin.href, external=pin.external, shift=shifts.get(pin.label, 0)) for pin in placed],
                path=[where[label] for label in figure.path if label in where],
                branches=[[where[a], where[b]] for a, b in figure.branches if a in where and b in where],
                dashed=figure.dashed)
