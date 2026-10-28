"""World state: terrain, soil columns, atmosphere, and the mass ledger.

State is a plain dict so that it round-trips through JSON without a schema
layer. Geometry is a side view: column x has a surface row surface[x], dirt
down to rock[x], and rock below that. Row 0 is the top of the sky.

Mass accounting
---------------
Three elements are conserved exactly: carbon, water, and nitrogen. Every
process moves mass between named pools and never creates or destroys it.

  carbon    air.co2  <-> organism.c  <-> soil.detritus_c
  nitrogen  soil.mineral_n <-> organism.n <-> soil.detritus_n
  water     air.humidity <-> soil.water <-> organism.w

ledger_totals() sums every pool; tests assert it is invariant across ticks.
"""

import math
import random

WIDTH = 128
HEIGHT = 72

SOIL_CAP = 420.0          # water units per column at field capacity
ROCK_DEPTH = 14           # dirt thickness above bedrock, before noise

TOTAL_CARBON = 26000.0
TOTAL_WATER = 52000.0
TOTAL_NITROGEN = 3000.0

SCHEMA_VERSION = 1


def new_world(seed=None):
    seed = random.randrange(1 << 30) if seed is None else int(seed)
    rng = random.Random(seed)

    surface, rock = _terrain(rng)
    sea_level = max(surface) - 4

    w = {
        "schema": SCHEMA_VERSION,
        "seed": seed,
        "tick": 0,
        "size": {"w": WIDTH, "h": HEIGHT},
        "terrain": {"surface": surface, "rock": rock, "sea_level": sea_level},
        "soil": {
            "water": [0.0] * WIDTH,
            "mineral_n": [0.0] * WIDTH,
            "detritus_c": [0.0] * WIDTH,
            "detritus_n": [0.0] * WIDTH,
            # dormant seed bank: records carry their own mass and genome, so
            # a flora crash is survivable without outside intervention
            "seeds": [],
        },
        "air": {
            "co2": 0.0,
            "humidity": 0.0,
            "temp": 15.0,
            "cloud": [0.0] * WIDTH,
            "rain": [0.0] * WIDTH,
        },
        "organisms": [],
        "species": {},
        "next_id": 1,
        "next_species": 1,
        "chronicle": [],
        "history": [],
        "flags": {"sterile_since": None, "reseeds": 0},
    }

    # distribute the starting budget
    soil_w = TOTAL_WATER * 0.72
    w["air"]["humidity"] = TOTAL_WATER - soil_w
    w["air"]["co2"] = TOTAL_CARBON * 0.55
    detritus_c = TOTAL_CARBON * 0.45
    mineral_n = TOTAL_NITROGEN * 0.55
    detritus_n = TOTAL_NITROGEN - mineral_n

    # wetter in hollows, drier on ridges
    depth = [max(surface) - s for s in surface]
    span = max(1.0, float(max(depth)))
    weight = [0.55 + 0.9 * (1.0 - d / span) for d in depth]
    wsum = sum(weight)
    for x in range(WIDTH):
        frac = weight[x] / wsum
        w["soil"]["water"][x] = soil_w * frac
        w["soil"]["mineral_n"][x] = mineral_n / WIDTH
        w["soil"]["detritus_c"][x] = detritus_c / WIDTH
        w["soil"]["detritus_n"][x] = detritus_n / WIDTH

    return w


def _terrain(rng):
    """1-D fractal value noise -> surface and bedrock profiles."""
    base = HEIGHT * 0.56
    height = [0.0] * WIDTH
    for wavelength, amp in ((64, 7.0), (32, 4.0), (16, 2.2), (8, 1.1), (4, 0.5)):
        nodes = WIDTH // wavelength + 2
        pts = [rng.uniform(-1.0, 1.0) for _ in range(nodes)]
        for x in range(WIDTH):
            t = x / wavelength
            i = int(t)
            f = t - i
            f = f * f * (3 - 2 * f)          # smoothstep
            a, b = pts[i], pts[i + 1]
            height[x] += (a + (b - a) * f) * amp

    surface = []
    for x in range(WIDTH):
        y = int(round(base - height[x]))
        surface.append(max(8, min(HEIGHT - 6, y)))

    rock = []
    for x in range(WIDTH):
        jitter = int(round(2.5 * math.sin(x * 0.21) + rng.uniform(-1.2, 1.2)))
        rock.append(min(HEIGHT - 1, surface[x] + ROCK_DEPTH + jitter))
    return surface, rock


# --- geometry ---------------------------------------------------------------

def surface_y(w, x):
    return w["terrain"]["surface"][x % w["size"]["w"]]


def is_submerged(w, x):
    return surface_y(w, x) > w["terrain"]["sea_level"]


def saturation(w, x):
    return w["soil"]["water"][x % w["size"]["w"]] / SOIL_CAP


def wrap(w, x):
    return x % w["size"]["w"]


# --- ledger -----------------------------------------------------------------

def ledger_totals(w):
    soil = w["soil"]
    air = w["air"]
    c = air["co2"] + sum(soil["detritus_c"])
    n = sum(soil["mineral_n"]) + sum(soil["detritus_n"])
    water = (air["humidity"] + sum(soil["water"])
             + sum(air["rain"]) + sum(air["cloud"]))
    for o in w["organisms"]:
        c += o["c"]
        n += o["n"]
        water += o["w"]
    for sd in soil.get("seeds", ()):
        c += sd["c"]
        n += sd["n"]
        water += sd["w"]
    return {"carbon": c, "nitrogen": n, "water": water}


def ledger_report(w):
    t = ledger_totals(w)
    return {
        "carbon": {"total": t["carbon"], "expected": TOTAL_CARBON,
                   "drift": t["carbon"] - TOTAL_CARBON},
        "nitrogen": {"total": t["nitrogen"], "expected": TOTAL_NITROGEN,
                     "drift": t["nitrogen"] - TOTAL_NITROGEN},
        "water": {"total": t["water"], "expected": TOTAL_WATER,
                  "drift": t["water"] - TOTAL_WATER},
    }
