"""8-bit renderer: composes the world into an indexed-colour PNG.

No third-party libraries. The scene is drawn into a palette-indexed pixel
buffer and written out as a PNG via zlib, so the output is a true 8-bit image
rather than vector shapes pretending to be pixels.

Geometry: one world tile is TILE canvas pixels square; the canvas is then
nearest-neighbour upscaled by SCALE so that the pixel grid stays visible when
GitHub or a browser rescales the image.

Layers, back to front:
    sky gradient -> stars -> sun/moon -> clouds -> rain -> terrain ->
    soil moisture and litter -> flora -> decomposers -> fauna -> vignette
"""

import math
import struct
import zlib

from . import palette as pal
from .cycles import insolation
from .life import flora_height
from .world import SOIL_CAP, is_submerged

TILE = 4
SCALE = 3


# --- pixel buffer -----------------------------------------------------------

class Canvas:
    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.index = {}
        self.colours = []
        self.rows = [bytearray(width) for _ in range(height)]
        self.ink("#000000")  # index 0

    def ink(self, hex_colour):
        i = self.index.get(hex_colour)
        if i is None:
            if len(self.colours) >= 256:
                return 0
            i = len(self.colours)
            self.index[hex_colour] = i
            self.colours.append(hex_colour)
        return i

    def px(self, x, y, colour):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.rows[y][x] = colour if isinstance(colour, int) else self.ink(colour)

    def rect(self, x, y, w, h, colour):
        c = colour if isinstance(colour, int) else self.ink(colour)
        for yy in range(max(0, y), min(self.h, y + h)):
            row = self.rows[yy]
            for xx in range(max(0, x), min(self.w, x + w)):
                row[xx] = c

    def hline(self, x, y, w, colour):
        self.rect(x, y, w, 1, colour)

    def vline(self, x, y, h, colour):
        self.rect(x, y, 1, h, colour)

    def blit(self, grid, charmap, x, y, flip=False):
        width = max(len(r) for r in grid)
        for dx, dy, hex_colour in pal.sprite_pixels(grid, charmap):
            tx = (width - 1 - dx) if flip else dx
            self.px(x + tx, y + dy, self.ink(hex_colour))

    def to_png(self, path, scale=SCALE):
        raw = bytearray()
        for row in self.rows:
            if scale == 1:
                line = bytes(row)
            else:
                line = bytes(b for v in row for b in (v,) * scale)
            for _ in range(scale):
                raw.append(0)       # filter: none
                raw.extend(line)

        plte = bytearray()
        for hex_colour in self.colours:
            plte.extend(_rgb(hex_colour))

        out = bytearray(b"\x89PNG\r\n\x1a\n")
        out += _chunk(b"IHDR", struct.pack(">IIBBBBB", self.w * scale,
                                           self.h * scale, 8, 3, 0, 0, 0))
        out += _chunk(b"PLTE", bytes(plte))
        out += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        out += _chunk(b"IEND", b"")
        with open(path, "wb") as fh:
            fh.write(out)
        return len(out)


def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _rgb(hex_colour):
    h = hex_colour.lstrip("#")
    return bytes(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    t = max(0.0, min(1.0, t))
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return "#%02x%02x%02x" % (int(ra + (rb - ra) * t),
                              int(ga + (gb - ga) * t),
                              int(ba + (bb - ba) * t))


# --- scene ------------------------------------------------------------------

def render(w, path, scale=SCALE):
    size = w["size"]
    cv = Canvas(size["w"] * TILE, size["h"] * TILE)
    light = insolation(w["tick"])
    temp = w["air"]["temp"]
    surface = w["terrain"]["surface"]
    sea = w["terrain"]["sea_level"]

    _sky(cv, w, light)
    _celestial(cv, w, light)
    _clouds(cv, w)
    _precipitation(cv, w, temp)
    _terrain(cv, w, light)
    _soil(cv, w)
    _flora(cv, w)
    _soil_life(cv, w)
    _fauna(cv, w)
    _frame(cv, light)

    cv.to_png(path, scale)
    return {"width": cv.w * scale, "height": cv.h * scale,
            "colours": len(cv.colours)}


def _sky(cv, w, light):
    P = pal.PALETTE
    if light > 0.55:
        top, bottom = P["sky_day"], _mix(P["sky_day"], P["cloud"], 0.45)
    elif light > 0.12:
        t = (light - 0.12) / 0.43
        top = _mix(P["sky_dusk"], P["sky_day"], t)
        bottom = _mix(P["sun"], P["sky_day"], t * 0.7)
    else:
        t = light / 0.12
        top = _mix(P["sky_deep"], P["sky_night"], t)
        bottom = _mix(P["sky_night"], P["sky_dusk"], t * 0.6)

    horizon = min(w["terrain"]["surface"]) * TILE
    bands = 14
    for b in range(bands):
        y0 = int(horizon * b / bands)
        y1 = int(horizon * (b + 1) / bands)
        colour = _mix(top, bottom, b / max(1, bands - 1))
        cv.rect(0, y0, cv.w, max(1, y1 - y0), colour)
    cv.rect(0, horizon, cv.w, cv.h - horizon, bottom)


def _celestial(cv, w, light):
    P = pal.PALETTE
    horizon = min(w["terrain"]["surface"]) * TILE
    day = light > 0.02
    d = (w["tick"] % 24) / 24.0
    # the sun tracks 06:00 -> 18:00; the moon takes the opposite half
    t = (d - 0.25) / 0.5 if day else (((d + 0.25) % 1.0) / 0.5)
    t = max(0.0, min(1.0, t))
    x = int(t * cv.w)
    y = int((1.0 - math.sin(math.pi * t)) * horizon * 0.72) + 5

    if day:
        body, r = P["sun"], 5
    else:
        body, r = P["moon"], 4
        rng_state = (w["seed"] * 7919) & 0xFFFF
        for i in range(90):
            rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
            sx = rng_state % cv.w
            rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
            sy = rng_state % max(1, int(min(w["terrain"]["surface"]) * TILE * 0.9))
            if (i + w["tick"] // 3) % 7:
                cv.px(sx, sy, P["star"])

    for dy in range(-r, r + 1):
        span = int(math.sqrt(max(0, r * r - dy * dy)))
        cv.hline(x - span, y + dy, span * 2 + 1, body)


def _clouds(cv, w):
    """Cloud decks follow a smooth drift line so banks read as masses, not bars."""
    P = pal.PALETTE
    cloud = w["air"]["cloud"]
    size = len(cloud)
    horizon = min(w["terrain"]["surface"]) * TILE
    seed = w["seed"]

    def hash01(v):
        h = (int(v) * 2654435761 ^ seed * 97) & 0xFFFFFFFF
        h ^= h >> 13
        return ((h * 1274126177) & 0xFFFFFFFF) / 0xFFFFFFFF

    for x, mass in enumerate(cloud):
        opacity = min(1.0, mass / 110.0)
        left = min(1.0, cloud[(x - 1) % size] / 110.0)
        right = min(1.0, cloud[(x + 1) % size] / 110.0)
        smooth = (opacity * 2 + left + right) / 4.0
        if smooth < 0.22:
            continue

        # columns cluster into banks, each bank sitting at its own altitude
        bank = x // 7
        altitude = horizon * (0.10 + 0.34 * hash01(bank))
        thickness = max(2, int(1 + (smooth - 0.2) * 12))
        lumpy = 1 if hash01(x * 31 + bank) > 0.55 else 0
        top = int(altitude) - lumpy

        body = P["cloud"] if smooth > 0.55 else P["cloud_shade"]
        cv.rect(x * TILE, top, TILE, thickness, body)
        cv.hline(x * TILE, top, TILE, P["cloud"])
        cv.hline(x * TILE, top + thickness - 1, TILE, P["cloud_shade"])


def _precipitation(cv, w, temp):
    P = pal.PALETTE
    rain = w["air"]["rain"]
    surface = w["terrain"]["surface"]
    snow = temp < 0.5
    colour = P["snow"] if snow else P["rain"]
    for x, mass in enumerate(rain):
        if mass < 0.6:
            continue
        drops = min(6, 1 + int(mass / 3.0))
        top = int(min(surface) * TILE * 0.35)
        bottom = surface[x] * TILE
        for d in range(drops):
            y = top + ((d * 37 + x * 13 + w["tick"] * 11) % max(1, bottom - top))
            if snow:
                cv.px(x * TILE + (d % TILE), y, colour)
            else:
                cv.vline(x * TILE + (d % TILE), y, 3, colour)


def _terrain(cv, w, light):
    P = pal.PALETTE
    size = w["size"]["w"]
    surface = w["terrain"]["surface"]
    rock = w["terrain"]["rock"]
    sea = w["terrain"]["sea_level"]
    soil = w["soil"]
    day = light > 0.25

    grass = P["grass_lit"] if day else P["grass"]
    water_top = P["water_lit"] if day else P["water"]

    for x in range(size):
        sy = surface[x]
        ry = rock[x]
        sat = soil["water"][x] / SOIL_CAP

        # bedrock, mottled on a 2-tile grid rather than banded by column
        for y in range(ry, w["size"]["h"]):
            h = ((x >> 1) * 73856093) ^ ((y >> 1) * 19349663) ^ w["seed"]
            h = (h * 2654435761) & 0xFFFFFFFF
            pick = (h >> 13) % 10
            if pick < 4:
                colour = P["stone_dark"]
            elif pick < 9:
                colour = P["stone"]
            else:
                colour = _mix(P["stone"], P["clay"], 0.35)
            cv.rect(x * TILE, y * TILE, TILE, TILE, colour)
        # dirt column, wetter soil renders darker
        for y in range(sy, ry):
            depth = (y - sy) / max(1, ry - sy)
            wet = min(1.0, sat * (1.0 - 0.4 * depth))
            colour = _mix(P["dirt"], P["dirt_dark"], 0.25 + 0.55 * wet)
            if depth > 0.72:
                colour = _mix(colour, P["clay"], 0.4)
            cv.rect(x * TILE, y * TILE, TILE, TILE, colour)

        if sy > sea:
            # submerged column: water from sea level down to the bed
            cv.rect(x * TILE, sea * TILE, TILE, (sy - sea) * TILE,
                    P["water"])
            cv.rect(x * TILE, sea * TILE, TILE, 1, water_top)
            cv.rect(x * TILE, sy * TILE, TILE, TILE, P["sand"])
        elif sy >= sea - 1:
            cv.rect(x * TILE, sy * TILE, TILE, TILE, P["sand"])
        else:
            # grass cap with a lit top edge
            cv.rect(x * TILE, sy * TILE, TILE, TILE, P["grass"])
            cv.hline(x * TILE, sy * TILE, TILE, grass)


def _soil(cv, w):
    P = pal.PALETTE
    soil = w["soil"]
    surface = w["terrain"]["surface"]
    sea = w["terrain"]["sea_level"]
    for x in range(w["size"]["w"]):
        if surface[x] > sea:
            continue
        litter = soil["detritus_c"][x]
        specks = min(9, int(litter / 9.0))
        base = (surface[x] + 1) * TILE
        for i in range(specks):
            px = x * TILE + ((i * 5 + x * 3) % TILE)
            py = base + ((i * 7 + x) % (TILE * 3))
            cv.px(px, py, P["detritus"])
        # mineral nitrogen as faint pale grains
        grains = min(5, int(soil["mineral_n"][x] / 6.0))
        for i in range(grains):
            px = x * TILE + ((i * 3 + x * 7) % TILE)
            py = base + TILE + ((i * 11 + x * 5) % (TILE * 2))
            cv.px(px, py, P["sand"])


def _flora(cv, w):
    maps = pal.FLORA_MAP
    for o in w["organisms"]:
        if o["kind"] != "flora":
            continue
        x = int(o["x"]) % w["size"]["w"]
        base = w["terrain"]["surface"][x] * TILE
        h = flora_height(o)
        hpx = max(2, int(round(h * TILE * 0.8)))
        cx = x * TILE + TILE // 2

        if hpx <= 5:
            cv.blit(pal.SPR_SPROUT, maps, cx - 2, base - 3)
            continue

        stem = pal.PALETTE["stem"]
        cv.vline(cx, base - hpx, hpx, stem)
        if h > 3.0:
            cv.vline(cx - 1, base - hpx + 2, hpx - 2, stem)

        stressed = o["stress"]
        mature = o["c"] > 0.0 and h > 0.75 * o["genome"]["max_height"]
        if stressed > 0.72:
            crown = pal.SPR_CROWN_DEAD
        elif stressed > 0.4:
            crown = pal.SPR_CROWN_DRY
        elif mature:
            crown = pal.SPR_CROWN_BLOOM
        else:
            crown = pal.SPR_CROWN
        cw, ch = pal.sprite_size(crown)
        cv.blit(crown, maps, cx - cw // 2, base - hpx - ch + 2)

        # side foliage for tall individuals
        if h > 5.0:
            for i in range(1, int(h / 2.5)):
                y = base - int(hpx * (0.35 + 0.18 * i))
                side = -1 if i % 2 else 1
                cv.rect(cx + side * 2, y, 2, 1, pal.PALETTE["leaf"])


def _soil_life(cv, w):
    maps = pal.FAUNA_MAP
    for o in w["organisms"]:
        if o["kind"] != "decomposer":
            continue
        x = int(o["x"]) % w["size"]["w"]
        base = (w["terrain"]["surface"][x] + 1) * TILE
        depth = 2 + (o["id"] % 7)
        y = base + depth
        if o["state"] == "active":
            cv.blit(pal.SPR_DECOMPOSER, maps, x * TILE + 1, y)
        else:
            cv.px(x * TILE + 2, y + 1, pal.PALETTE["decomposer"])


def _fauna(cv, w):
    maps = pal.FAUNA_MAP
    for o in w["organisms"]:
        kind = o["kind"]
        if kind not in ("grazer", "predator"):
            continue
        x = int(o["x"]) % w["size"]["w"]
        base = w["terrain"]["surface"][x] * TILE
        adult = o["c"] > 4.0 * o["genome"]["size"]
        if kind == "grazer":
            grid = pal.SPR_GRAZER if adult else pal.SPR_GRAZER_YOUNG
        else:
            grid = pal.SPR_PREDATOR if adult else pal.SPR_PREDATOR_YOUNG
        gw, gh = pal.sprite_size(grid)
        flip = o.get("facing", 1) < 0
        bob = 1 if o["state"] in ("moving", "fleeing", "stalking") and (w["tick"] % 2) else 0
        cv.blit(grid, maps, x * TILE + TILE // 2 - gw // 2, base - gh - bob, flip)


def _frame(cv, light):
    edge = pal.PALETTE["sky_deep"] if light > 0.1 else "#000000"
    cv.hline(0, 0, cv.w, edge)
    cv.hline(0, cv.h - 1, cv.w, edge)
    cv.vline(0, 0, cv.h, edge)
    cv.vline(cv.w - 1, 0, cv.h, edge)
