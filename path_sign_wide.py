#!/usr/bin/env python3
"""
PATH arrival sign for a 1920x440 wide panel driven straight to the
Linux framebuffer (/dev/fb0) -- no X, no Wayland, no browser.

Shows one station's outbound arrivals one route at a time, flashing between
routes like a real platform sign: e.g. the next 2 "33rd Street" trains, then
the next 2 "World Trade Center" trains, then back again. Data comes from the
official PANYNJ feed; colors come straight from the feed (route_colors) and
arrivals from route_trains.

Run anywhere with --preview to dump a PNG per route instead of writing
to the framebuffer.
"""

import os
import sys
import time
import mmap
from collections import defaultdict
from datetime import datetime

import requests
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# CONFIG -- edit these
# ---------------------------------------------------------------------------
WIDTH, HEIGHT = 1920, 440          # match your panel's native resolution
FB_DEVICE = "/dev/fb0"

DATA_REFRESH_SECONDS = 30          # how often to re-fetch arrival data
PAGE_SECONDS = 5                   # how long each route stays on screen
TRAINS_PER_PAGE = 2                # upcoming trains to show per route page

OFFICIAL_URL = "https://www.panynj.gov/bin/portauthority/ridepath.json"
STATION_CODE = "JSQ"               # which station to show
STATION_NAME = "Journal Square"    # display name in the header
DESTINATION = "ToNY"               # direction label to show
ROUTE_ORDER = ["33rd Street", "World Trade Center"]  # API headSign values
ROUTE_LABELS = {
    "33rd Street": "33rd Street",
    "World Trade Center": "World Trade Center",
}
ROUTE = set(ROUTE_ORDER)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(SCRIPT_DIR, "fonts")

# Mute Regular (./fonts/)
FONT_REGULAR = os.path.join(FONT_DIR, "Mute-Regular.otf")
FONT_REGULAR_CANDIDATES = [
    FONT_REGULAR,
    os.path.join(FONT_DIR, "Mute-Variable.ttf"),
    os.path.join(FONT_DIR, "Mute-Medium.otf"),
    os.path.expanduser("~/Library/Fonts/Mute-Regular.otf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
FONT_BOLD = os.path.join(FONT_DIR, "Mute-Bold.otf")
FONT_SEMIBOLD = os.path.join(FONT_DIR, "Mute-Semibold.otf")
FONT_BOLD_CANDIDATES = [
    FONT_BOLD,
    os.path.join(FONT_DIR, "Mute-Semibold.otf"),
    os.path.join(FONT_DIR, "Mute-Medium.otf"),
    FONT_REGULAR,
]
FONT_FOOTER_CANDIDATES = [
    FONT_SEMIBOLD,
    os.path.join(FONT_DIR, "Mute-Medium.otf"),
    FONT_REGULAR,
    FONT_BOLD,
]

BG = (255, 255, 255)
TEXT = (55, 55, 55)
DIVIDER_GREY = (215, 215, 215)     # faint grey flanking the center line
DIVIDER_WHITE = (255, 255, 255)
FOOTER_BG = (26, 52, 86)           # dark blue bar
FOOTER_CHEVRON = (100, 180, 220)   # light blue time badge
FOOTER_TEXT = (255, 255, 255)
DEFAULT_DOT = (180, 30, 30)


# ---------------------------------------------------------------------------
# DATA SOURCE
# ---------------------------------------------------------------------------
def hex_to_rgb(value):
    """'FF9900' or '#FF9900' -> (255, 153, 0)."""
    if not value:
        return DEFAULT_DOT
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def fetch_route_data():
    """
    Pull the official feed and return (route_trains, route_colors):

      route_trains: {headSign: [(train_message, minutes), ...]}  # sorted soonest-first
      route_colors: {headSign: "FF9900"}                          # hex, no '#'
    """
    data = requests.get(OFFICIAL_URL, timeout=10).json()

    station = next(s for s in data["results"] if s["consideredStation"] == STATION_CODE)
    destination = next(d for d in station["destinations"] if d["label"] == DESTINATION)

    route_trains = defaultdict(list)
    for train in destination["messages"]:
        if train["headSign"] in ROUTE:
            route_trains[train["headSign"]].append(
                (train, int(train["secondsToArrival"]) // 60)
            )
    for route in route_trains:
        route_trains[route].sort(key=lambda t: int(t[0]["secondsToArrival"]))

    route_colors = {}
    for route in ROUTE:
        route_colors[route] = next(
            (
                m["lineColor"]
                for s in data["results"]
                for d in s.get("destinations", [])
                for m in d.get("messages", [])
                if m["headSign"] == route
            ),
            None,
        )

    return route_trains, route_colors


# ---------------------------------------------------------------------------
# RENDERING
# ---------------------------------------------------------------------------
def _font(candidates, px):
    for path in candidates:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw, text, candidates, px, max_width):
    """Return (font, bbox) for the largest font size <= px that fits max_width."""
    while px > 8:
        font = _font(candidates, px)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font, bbox
        px -= 4
    font = _font(candidates, px)
    return font, draw.textbbox((0, 0), text, font=font)


def _current_time_label():
    label = datetime.now().strftime("%I:%M %p")
    if label.startswith("0"):
        label = label[1:]
    return label.lower()


def _row_baseline_y(cy, font, nudge_y=0):
    """Baseline y so text with anchor='ls' is vertically centered on cy."""
    bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
        (0, 0), "Ag", font=font, anchor="ls"
    )
    center_offset = (bbox[1] + bbox[3]) / 2
    return cy - center_offset + nudge_y


def _draw_arrival_time(draw, right_x, cy, minutes, num_px, unit_px, baseline_y):
    """Draw '13 min' on the row baseline."""
    if minutes is None:
        font = _font(FONT_REGULAR_CANDIDATES, num_px)
        draw.text((right_x, baseline_y), "--", font=font, fill=TEXT, anchor="rs")
        return

    num_font = _font(FONT_REGULAR_CANDIDATES, num_px)
    unit_font = _font(FONT_REGULAR_CANDIDATES, unit_px)
    num_str = str(minutes)
    unit_str = " min"

    unit_bbox = draw.textbbox((0, 0), unit_str, font=unit_font, anchor="ls")
    unit_w = unit_bbox[2] - unit_bbox[0]

    draw.text((right_x, baseline_y), unit_str, font=unit_font, fill=TEXT, anchor="rs")
    num_bbox = draw.textbbox((0, 0), num_str, font=num_font, anchor="ls")
    num_w = num_bbox[2] - num_bbox[0]
    draw.text((right_x - unit_w, baseline_y), num_str, font=num_font, fill=TEXT, anchor="rs")


def _draw_footer(d, footer_y, footer_h):
    d.rectangle([0, footer_y, WIDTH, HEIGHT], fill=FOOTER_BG)

    chevron_w = int(WIDTH * 0.16)
    tip_x = chevron_w + int(footer_h * 0.3)
    mid_y = footer_y + footer_h // 2
    d.polygon(
        [
            (0, footer_y),
            (chevron_w, footer_y),
            (tip_x, mid_y),
            (chevron_w, footer_y + footer_h),
            (0, footer_y + footer_h),
        ],
        fill=FOOTER_CHEVRON,
    )

    time_str = _current_time_label()
    time_px = int(footer_h * 0.72)
    time_font = _font(FONT_FOOTER_CANDIDATES, time_px)
    d.text((tip_x / 2 - int(footer_h * 0.12), mid_y), time_str, font=time_font, fill=FOOTER_TEXT, anchor="mm")


def _draw_row_divider(d, y):
    """White center line flanked by faint grey lines."""
    d.line([(0, y - 1), (WIDTH, y - 1)], fill=DIVIDER_GREY, width=1)
    d.line([(0, y), (WIDTH, y)], fill=DIVIDER_WHITE, width=1)
    d.line([(0, y + 1), (WIDTH, y + 1)], fill=DIVIDER_GREY, width=1)


def render_route_page(route, trains, line_color):
    """One page: up to TRAINS_PER_PAGE upcoming trains for a single route."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)

    footer_h = int(HEIGHT * 0.18)
    footer_y = HEIGHT - footer_h
    _draw_footer(d, footer_y, footer_h)

    rows = trains[:TRAINS_PER_PAGE] if trains else []
    if not rows:
        rows = [(None, None)]

    body_h = footer_y
    n = len(rows)
    row_h = body_h // n

    num_px = min(int(row_h * 0.72), 190)
    unit_px = min(int(row_h * 0.54), 110)
    dot_color = hex_to_rgb(line_color)
    dot_r = int(num_px * 0.50)
    left_pad = int(WIDTH * 0.04)
    right_pad = int(WIDTH * 0.04)
    gap = int(WIDTH * 0.03)

    for i, (train, minutes) in enumerate(rows):
        y0 = i * row_h
        cy = y0 + row_h // 2

        dot_cx = left_pad + dot_r
        d.ellipse([dot_cx - dot_r, cy - dot_r, dot_cx + dot_r, cy + dot_r],
                  fill=dot_color)

        time_x = WIDTH - right_pad

        label_x = dot_cx + dot_r + gap
        label = ROUTE_LABELS.get(route, route)
        label_font = _font(FONT_REGULAR_CANDIDATES, num_px)
        baseline_y = _row_baseline_y(cy, label_font, nudge_y=int(num_px * 0.12))
        d.text((label_x, baseline_y), label, font=label_font, fill=TEXT, anchor="ls")

        _draw_arrival_time(d, time_x, cy, minutes, num_px, unit_px, baseline_y)

        if i < n - 1:
            _draw_row_divider(d, y0 + row_h)

    return img


# ---------------------------------------------------------------------------
# FRAMEBUFFER OUTPUT
# ---------------------------------------------------------------------------
class Framebuffer:
    def __init__(self, device=FB_DEVICE):
        self.device = device
        self.xres, self.yres, self.bpp = self._read_geometry()
        self.fb = os.open(device, os.O_RDWR)
        self.screensize = self.xres * self.yres * (self.bpp // 8)
        self.mm = mmap.mmap(self.fb, self.screensize)

    @staticmethod
    def _read_sys(name, default):
        try:
            with open(f"/sys/class/graphics/fb0/{name}") as f:
                return f.read().strip()
        except OSError:
            return default

    def _read_geometry(self):
        vsize = self._read_sys("virtual_size", f"{WIDTH},{HEIGHT}")
        xres, yres = (int(v) for v in vsize.split(","))
        bpp = int(self._read_sys("bits_per_pixel", "32"))
        return xres, yres, bpp

    def show(self, img):
        if (img.width, img.height) != (self.xres, self.yres):
            img = img.resize((self.xres, self.yres))
        if self.bpp == 32:
            b = img.tobytes("raw", "BGRX")
        elif self.bpp == 16:
            b = img.convert("RGB").tobytes("raw", "BGR;16")
        else:
            raise RuntimeError(f"Unsupported framebuffer depth: {self.bpp} bpp")
        self.mm.seek(0)
        self.mm.write(b)

    def close(self):
        try:
            self.mm.close()
            os.close(self.fb)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------
def main():
    preview = "--preview" in sys.argv

    fb = None
    if not preview:
        try:
            fb = Framebuffer()
        except (FileNotFoundError, PermissionError) as e:
            print(f"No framebuffer ({e}). Run with --preview to save a PNG, "
                  f"or run on the Pi with access to {FB_DEVICE}.")
            sys.exit(1)

    route_trains, route_colors = fetch_route_data()
    last_fetch = time.monotonic()
    route_idx = 0

    try:
        while True:
            now = time.monotonic()
            if now - last_fetch >= DATA_REFRESH_SECONDS:
                route_trains, route_colors = fetch_route_data()
                last_fetch = now

            route = ROUTE_ORDER[route_idx % len(ROUTE_ORDER)]
            trains = route_trains.get(route, [])
            img = render_route_page(route, trains, route_colors.get(route))

            if preview:
                slug = ROUTE_LABELS.get(route, route).replace(" ", "_")
                fname = f"path_sign_wide_{STATION_CODE}_{slug}.png"
                img.save(fname)
                print(f"Wrote {fname}")
                route_idx += 1
                if route_idx >= len(ROUTE_ORDER):
                    return
            else:
                fb.show(img)
                route_idx += 1

            time.sleep(PAGE_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        if fb:
            fb.close()


if __name__ == "__main__":
    main()
