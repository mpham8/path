#!/usr/bin/env python3
"""
Combined PATH + MTA arrival sign for a 1024x600 panel on /dev/fb0.

Cycles three screens (5 s each), four rows per screen:
  1. PATH (top 2) + W/R (bottom 2)
  2. 4/5 (top 2) + J (bottom 2)
  3. 2/3 (top 2) + A/C (bottom 2)

Data from mta_fulton.py. Trains arriving in 2 minutes or less are hidden.

    python train_sign.py --preview
"""

import os
import sys
import time
import mmap

from PIL import Image, ImageDraw, ImageFont

from mta_fulton import (
    GROUP_COLORS,
    ROUTE_COLORS,
    fetch_grouped_arrivals,
    fetch_path_wtc_newark,
)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
WIDTH, HEIGHT = 1024, 600
FB_DEVICE = "/dev/fb0"

DATA_REFRESH_SECONDS = 30
PAGE_SECONDS = 5
TRAINS_PER_SECTION = 2
MIN_SHOW_MINUTES = 3          # hide arrivals <= 2 min

SCREENS = [
    {"station": "World Trade Center", "top": "PATH", "bottom": "W/R"},
    {"station": "Fulton", "top": "4/5", "bottom": "J"},
    {"station": "Fulton", "top": "2/3", "bottom": "A/C"},
]

GROUP_DESTINATION = {
    "PATH": "Newark",
    "A/C": "Uptown",
    "2/3": "Uptown",
    "4/5": "Uptown",
    "J": "Brooklyn",
    "W/R": "Uptown",
}

# Last-stop names for the directions shown (GTFS-RT has no headsign field).
ROUTE_TERMINAL = {
    "2": "Wakefield-241 St",
    "3": "Harlem-148 St",
    "4": "Woodlawn",
    "5": "Eastchester-Dyre Av",
    "A": "Inwood-207 St",
    "C": "168 St",
    "J": "Jamaica Center-Parsons/Archer",
    "N": "Astoria-Ditmars Blvd",
    "R": "Forest Hills-71 Av",
    "W": "Astoria-Ditmars Blvd",
}

# Helvetica (macOS paths first; Pi/Linux fall back to similar sans-serif)
FONT_REGULAR_CANDIDATES = [
    ("/System/Library/Fonts/Helvetica.ttc", 0),
    ("/System/Library/Fonts/HelveticaNeue.ttc", 0),
    "/Library/Fonts/Helvetica.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
FONT_BOLD_CANDIDATES = [
    ("/System/Library/Fonts/Helvetica.ttc", 1),
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
    "/Library/Fonts/Helvetica-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

BG = (0, 0, 0)
ROW_BG = (26, 52, 86)
ROW_GAP = 12                  # black strip between arrival rows
TEXT = (255, 255, 255)
HEADER_TEXT = (255, 255, 255)


# ---------------------------------------------------------------------------
# DATA
# ---------------------------------------------------------------------------
def hex_to_rgb(value):
    if not value:
        return (128, 128, 128)
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def route_color(route_id):
    if route_id == "PATH":
        return GROUP_COLORS["PATH"]
    return ROUTE_COLORS.get(route_id, "888888")


def fetch_all_arrivals():
    """Return {group: [(route_id, minutes), ...]} with PATH merged in."""
    grouped = fetch_grouped_arrivals()
    path_trains, _ = fetch_path_wtc_newark()
    path_rows = [
        ("PATH", minutes)
        for _, minutes in path_trains.get("Newark", [])
        if minutes >= MIN_SHOW_MINUTES
    ]
    grouped["PATH"] = sorted(path_rows, key=lambda t: t[1])

    for label in list(grouped.keys()):
        filtered = [
            (route_id, minutes)
            for route_id, minutes in grouped[label]
            if minutes >= MIN_SHOW_MINUTES
        ]
        if filtered:
            grouped[label] = filtered
        else:
            grouped.pop(label, None)

    return grouped


def section_rows(grouped, group_label):
    """Next TRAINS_PER_SECTION arrivals for a group, padded with None slots."""
    arrivals = grouped.get(group_label, [])[:TRAINS_PER_SECTION]
    rows = []
    for route_id, minutes in arrivals:
        rows.append({
            "route_id": route_id,
            "minutes": minutes,
            "destination": GROUP_DESTINATION.get(group_label, group_label),
            "terminal": ROUTE_TERMINAL.get(route_id, ""),
            "color": route_color(route_id),
        })
    while len(rows) < TRAINS_PER_SECTION:
        rows.append(None)
    return rows


# ---------------------------------------------------------------------------
# RENDERING
# ---------------------------------------------------------------------------
def _font(candidates, px):
    for entry in candidates:
        if isinstance(entry, tuple):
            path, index = entry
            try:
                return ImageFont.truetype(path, px, index=index)
            except OSError:
                continue
        try:
            return ImageFont.truetype(entry, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_route_bullet(d, cx, cy, route_id, diameter, color_hex):
    r = diameter // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hex_to_rgb(color_hex))
    if route_id == "PATH":
        return

    font = _font(FONT_BOLD_CANDIDATES, int(diameter * 0.80))
    bbox = d.textbbox((0, 0), route_id, font=font)
    tx = cx - (bbox[2] - bbox[0]) / 2 - bbox[0]
    ty = cy - (bbox[3] - bbox[1]) / 2 - bbox[1]
    d.text((tx, ty), route_id, font=font, fill=TEXT)


def _draw_spaced_text(d, cx, y, text, font, fill, spacing_px):
    """Draw text centered on cx with extra tracking between characters."""
    bboxes = [d.textbbox((0, 0), ch, font=font) for ch in text]
    widths = [bb[2] - bb[0] for bb in bboxes]
    total_w = sum(widths) + spacing_px * max(len(text) - 1, 0)
    x = cx - total_w / 2
    for ch, bb in zip(text, bboxes):
        d.text((x - bb[0], y), ch, font=font, fill=fill)
        x += (bb[2] - bb[0]) + spacing_px


def _draw_tracked_text_left(d, x, y, text, font, fill, tracking):
    """Draw left-aligned text with tighter (negative) or wider tracking."""
    for i, ch in enumerate(text):
        bb = d.textbbox((0, 0), ch, font=font)
        d.text((x - bb[0], y), ch, font=font, fill=fill)
        if i < len(text) - 1:
            x += (bb[2] - bb[0]) + tracking


def _draw_min_block(d, time_cx, cy, minutes, num_px, unit_px, min_gap, min_tracking):
    time_cx += int(WIDTH * 0.018)

    if minutes is None:
        font = _font(FONT_BOLD_CANDIDATES, num_px)
        bbox = d.textbbox((0, 0), "--", font=font)
        x = time_cx - (bbox[2] - bbox[0]) / 2 - bbox[0]
        y = cy - (bbox[3] - bbox[1]) / 2 - bbox[1]
        d.text((x, y), "--", font=font, fill=TEXT)
        return

    num_font = _font(FONT_BOLD_CANDIDATES, num_px)
    unit_font = _font(FONT_REGULAR_CANDIDATES, unit_px)
    num_str = str(minutes)
    num_bbox = d.textbbox((0, 0), num_str, font=num_font)
    unit_bbox = d.textbbox((0, 0), "M", font=unit_font)
    unit_h = unit_bbox[3] - unit_bbox[1]
    min_extra = int(unit_px * 0.55)
    block_h = (num_bbox[3] - num_bbox[1]) + min_gap + min_extra + unit_h
    num_x = time_cx - (num_bbox[2] - num_bbox[0]) / 2 - num_bbox[0]
    num_y = cy - block_h / 2 - num_bbox[1]
    d.text((num_x, num_y), num_str, font=num_font, fill=TEXT)
    unit_y = num_y + (num_bbox[3] - num_bbox[1]) + min_gap + min_extra
    _draw_spaced_text(d, time_cx, unit_y, "MIN", unit_font, TEXT, min_tracking)


def _draw_section_header(d, y0, h, title):
    header_font = _font(FONT_BOLD_CANDIDATES, int(h * 0.58))
    pad_x = int(WIDTH * 0.04)
    bbox = d.textbbox((0, 0), title, font=header_font)
    ty = y0 + (h - (bbox[3] - bbox[1])) / 2 - bbox[1]
    _draw_tracked_text_left(d, pad_x, ty, title, header_font, HEADER_TEXT, tracking=-2)


def _draw_row(d, y0, row_h, row):
    d.rectangle([0, y0, WIDTH, y0 + row_h], fill=ROW_BG)

    left_pad = int(WIDTH * 0.04)
    right_pad = int(WIDTH * 0.04)
    bullet_d = int(row_h * 0.74)
    gap = int(WIDTH * 0.03)
    cy = y0 + row_h // 2

    bullet_cx = left_pad + bullet_d // 2
    text_x = bullet_cx + bullet_d // 2 + gap
    time_col_w = int(WIDTH * 0.14)
    time_cx = WIDTH - right_pad - time_col_w // 2

    title_px = int(row_h * 0.44)
    sub_px = int(row_h * 0.22)
    num_px = int(row_h * 0.62)
    unit_px = int(row_h * 0.14)
    min_gap = max(6, int(row_h * 0.06))
    min_tracking = max(3, int(unit_px * 0.22))
    time_cy = cy + max(3, int(row_h * 0.025))

    if row is None:
        _draw_min_block(d, time_cx, time_cy, None, num_px, unit_px, min_gap, min_tracking)
        return

    _draw_route_bullet(d, bullet_cx, cy, row["route_id"], bullet_d, row["color"])

    title_font = _font(FONT_BOLD_CANDIDATES, title_px)
    sub_font = _font(FONT_REGULAR_CANDIDATES, sub_px)
    title = row["destination"]
    subtitle = row["terminal"]

    title_bbox = d.textbbox((0, 0), title, font=title_font)
    if subtitle:
        sub_bbox = d.textbbox((0, 0), subtitle, font=sub_font)
        sub_gap = 8
        text_h = (title_bbox[3] - title_bbox[1]) + (sub_bbox[3] - sub_bbox[1]) + sub_gap
        title_y = cy - text_h / 2 - title_bbox[1]
        d.text((text_x, title_y), title, font=title_font, fill=TEXT)
        sub_y = title_y + (title_bbox[3] - title_bbox[1]) + sub_gap + 2
        d.text((text_x, sub_y), subtitle, font=sub_font, fill=TEXT)
    else:
        title_y = cy - (title_bbox[3] - title_bbox[1]) / 2 - title_bbox[1]
        d.text((text_x, title_y), title, font=title_font, fill=TEXT)

    _draw_min_block(d, time_cx, time_cy, row["minutes"], num_px, unit_px, min_gap, min_tracking)


def render_screen(screen, grouped):
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)

    header_h = int(HEIGHT * 0.075)
    rows = []
    for section_key in ("top", "bottom"):
        rows.extend(section_rows(grouped, screen[section_key]))

    n = len(rows)
    row_h = (HEIGHT - header_h - ROW_GAP * (n - 1)) // n

    y = 0
    _draw_section_header(d, y, header_h, screen["station"])
    y += header_h

    for i, row in enumerate(rows):
        _draw_row(d, y, row_h, row)
        y += row_h
        if i < n - 1:
            y += ROW_GAP

    return img


# ---------------------------------------------------------------------------
# FRAMEBUFFER
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
# MAIN
# ---------------------------------------------------------------------------
def main():
    preview = "--preview" in sys.argv

    fb = None
    if not preview:
        try:
            fb = Framebuffer()
        except (FileNotFoundError, PermissionError) as e:
            print(f"No framebuffer ({e}). Run with --preview to save PNGs, "
                  f"or run on the Pi with access to {FB_DEVICE}.")
            sys.exit(1)

    grouped = fetch_all_arrivals()
    last_fetch = time.monotonic()
    screen_idx = 0

    try:
        while True:
            now = time.monotonic()
            if now - last_fetch >= DATA_REFRESH_SECONDS:
                grouped = fetch_all_arrivals()
                last_fetch = now

            screen = SCREENS[screen_idx % len(SCREENS)]
            img = render_screen(screen, grouped)

            if preview:
                slug = screen["station"].replace(" ", "_")
                fname = f"train_sign_{slug}_{screen['top']}_{screen['bottom']}".replace("/", "")
                fname = fname + ".png"
                img.save(fname)
                print(f"Wrote {fname}")
                screen_idx += 1
                if screen_idx >= len(SCREENS):
                    return
            else:
                fb.show(img)
                screen_idx += 1

            time.sleep(PAGE_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        if fb:
            fb.close()


if __name__ == "__main__":
    main()
