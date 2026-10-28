"""8-bit palette and pixel sprite sheet for the terrarium renderer.

Colours are a fixed 32-entry ramp in the spirit of early side-scroller tile
sets: low saturation dirt and stone, high saturation flora and fauna, so that
organisms read clearly against terrain at a 1:1 pixel scale.

Sprites are authored as character grids. Each character indexes PALETTE;
'.' is transparent. Grids are row-major, top row first.
"""

PALETTE = {
    # sky / atmosphere
    "sky_day": "#79b5de",
    "sky_dusk": "#8c6f9e",
    "sky_night": "#141a35",
    "sky_deep": "#0a0d1f",
    "cloud": "#e8eef5",
    "cloud_shade": "#b9c7d6",
    "rain": "#5aa0d8",
    "snow": "#f2f7ff",
    "sun": "#ffe08a",
    "moon": "#d8dced",
    "star": "#fdfdf0",
    # terrain
    "grass": "#5ca046",
    "grass_lit": "#79c45c",
    "dirt": "#8c5f3f",
    "dirt_dark": "#6b462e",
    "stone": "#6f6f7a",
    "stone_dark": "#53535c",
    "clay": "#a8673c",
    "sand": "#d9c08a",
    "water": "#2f63c8",
    "water_lit": "#4a84e0",
    # flora
    "stem": "#4a7a32",
    "leaf": "#6fbf4a",
    "leaf_dry": "#b89a3c",
    "leaf_dead": "#7a6033",
    "bloom": "#e8657f",
    "seed": "#e0c468",
    # fauna
    "grazer": "#d9b26a",
    "grazer_dark": "#a8823f",
    "predator": "#c0392b",
    "predator_dark": "#8c251a",
    "decomposer": "#9b7fc4",
    "detritus": "#4f3f2a",
    "bone": "#e6dfc8",
    "eye": "#1a1a22",
}

TRANSPARENT = "."

# --- flora ------------------------------------------------------------------
# Flora are drawn as a stack: one base tile, N trunk tiles, one crown whose
# variant depends on health and reproductive state.

SPR_SPROUT = [
    "..l..",
    ".lsl.",
    "..s..",
]

SPR_TRUNK = [
    ".s.",
    ".s.",
    ".s.",
]

SPR_CROWN = [
    ".lll.",
    "lllll",
    "lllll",
    ".lsl.",
]

SPR_CROWN_BLOOM = [
    ".lbl.",
    "blllb",
    "lllll",
    ".lsl.",
]

SPR_CROWN_DRY = [
    ".d.d.",
    ".ddd.",
    "d.d.d",
    "..s..",
]

SPR_CROWN_DEAD = [
    "..x..",
    ".x.x.",
    "..x..",
    "..s..",
]

# --- fauna ------------------------------------------------------------------

SPR_GRAZER = [
    "..ggg..",
    ".ggggg.",
    "gggegg.",
    ".g...g.",
    ".d...d.",
]

SPR_GRAZER_YOUNG = [
    ".ggg.",
    "ggeg.",
    ".d.d.",
]

SPR_PREDATOR = [
    "p.....p",
    ".ppppp.",
    "ppepepp",
    ".p...p.",
    ".D...D.",
]

SPR_PREDATOR_YOUNG = [
    ".ppp.",
    "pepe.",
    ".D.D.",
]

SPR_DECOMPOSER = [
    ".m.",
    "mmm",
    ".m.",
]

SPR_DETRITUS = [
    "...",
    ".t.",
    "ttt",
]

SPR_CARCASS = [
    "obo",
    "bbb",
]

SPR_SEED = [
    ".s.",
    "sss",
    ".s.",
]

# character -> palette key, per sprite family
FLORA_MAP = {
    "l": "leaf",
    "s": "stem",
    "b": "bloom",
    "d": "leaf_dry",
    "x": "leaf_dead",
}

FAUNA_MAP = {
    "g": "grazer",
    "d": "grazer_dark",
    "p": "predator",
    "D": "predator_dark",
    "e": "eye",
    "m": "decomposer",
    "t": "detritus",
    "b": "bone",
    "o": "detritus",
    "s": "seed",
}


def sprite_pixels(grid, charmap):
    """Yield (dx, dy, hex_colour) for every opaque pixel in a sprite grid."""
    for dy, row in enumerate(grid):
        for dx, ch in enumerate(row):
            if ch == TRANSPARENT:
                continue
            key = charmap.get(ch)
            if key is None:
                continue
            yield dx, dy, PALETTE[key]


def sprite_size(grid):
    return (max(len(r) for r in grid), len(grid))
