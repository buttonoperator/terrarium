"""Abiotic forcing: insolation, temperature, and the hydrological cycle.

Nothing here creates or destroys mass. Water moves between four pools:

    soil.water[x]  --evaporation-->  air.humidity
    air.humidity   --condensation->  air.cloud[x]
    air.cloud[x]   --precipitation-> air.rain[x]      (in flight, one tick)
    air.rain[x]    --infiltration->  soil.water[x]  (or runoff to a neighbour)

Clouds advect with a seasonal wind, so rainfall is not uniform: lee columns
dry out, windward columns waterlog, and flora distribution follows.
"""

import math

from .world import SOIL_CAP, is_submerged

DAY = 24                 # ticks per diurnal cycle
YEAR = 1440              # ticks per orbital cycle (60 days)

TEMP_MEAN = 14.0
TEMP_SEASON = 11.0
TEMP_DIURNAL = 4.5

HUM_CAP_BASE = 9000.0
EVAP_K = 0.022
COND_THRESHOLD = 0.86
COND_K = 0.30
RAIN_THRESHOLD = 60.0
RAIN_K = 0.28
INFILTRATION = 0.75
LATERAL_K = 0.05
CLOUD_OPAQUE = 110.0


def season_phase(tick):
    return (tick % YEAR) / YEAR


def day_phase(tick):
    return (tick % DAY) / DAY


def insolation(tick):
    """Top-of-atmosphere light in [0, 1].

    Sunrise at 06:00 (d = 0.25), noon peak, sunset at 18:00 (d = 0.75).
    """
    d = day_phase(tick)
    daylight = max(0.0, math.sin(math.pi * (d - 0.25) / 0.5))
    s = season_phase(tick)
    axial = 0.78 + 0.22 * math.cos(2 * math.pi * s)
    return daylight * axial


def season_name(tick):
    s = season_phase(tick)
    if s < 0.25:
        return "high summer"
    if s < 0.5:
        return "waning"
    if s < 0.75:
        return "deep winter"
    return "waxing"


def humidity_capacity(temp_c):
    return HUM_CAP_BASE * (2.0 ** ((temp_c - 14.0) / 12.0))


def wind(tick):
    """Prevailing drift in columns per tick; reverses across the year."""
    s = season_phase(tick)
    return 1.6 * math.cos(2 * math.pi * s) + 0.4


def step_climate(w, rng):
    tick = w["tick"]
    light = insolation(tick)
    s = season_phase(tick)
    cloud_cover = sum(min(1.0, c / CLOUD_OPAQUE) for c in w["air"]["cloud"])
    cloud_cover /= max(1, len(w["air"]["cloud"]))

    temp = (TEMP_MEAN
            + TEMP_SEASON * math.cos(2 * math.pi * s)
            + TEMP_DIURNAL * (light - 0.45)
            - 3.2 * cloud_cover
            + rng.gauss(0.0, 0.45))
    w["air"]["temp"] = temp
    return {"light": light, "temp": temp, "cloud_cover": cloud_cover}


def step_water(w, rng):
    size = w["size"]["w"]
    air = w["air"]
    soil = w["soil"]
    temp = air["temp"]
    light = insolation(w["tick"])

    moved = {"evap": 0.0, "rain": 0.0, "runoff": 0.0}

    # --- rain in flight lands first -----------------------------------------
    for x in range(size):
        fall = air["rain"][x]
        if fall <= 0.0:
            continue
        air["rain"][x] = 0.0
        soak = fall * INFILTRATION
        soil["water"][x] += soak
        shed = fall - soak
        tgt = _downhill(w, x)
        soil["water"][tgt] += shed
        moved["rain"] += fall
        moved["runoff"] += shed

    # --- evaporation ---------------------------------------------------------
    cap = humidity_capacity(temp)
    rel = air["humidity"] / cap if cap > 0 else 1.0
    deficit = max(0.0, 1.0 - rel)
    thermal = max(0.0, (temp + 6.0) / 32.0)
    for x in range(size):
        sat = soil["water"][x] / SOIL_CAP
        open_water = 1.6 if is_submerged(w, x) else 1.0
        flux = (EVAP_K * SOIL_CAP * min(1.4, sat) * deficit
                * thermal * (0.35 + 0.65 * light) * open_water)
        flux = min(flux, soil["water"][x])
        soil["water"][x] -= flux
        air["humidity"] += flux
        moved["evap"] += flux

    # --- condensation into clouds -------------------------------------------
    rel = air["humidity"] / cap if cap > 0 else 1.0
    if rel > COND_THRESHOLD:
        excess = air["humidity"] - COND_THRESHOLD * cap
        lift = excess * COND_K
        air["humidity"] -= lift
        # orographic bias: clouds form over high ground
        surface = w["terrain"]["surface"]
        lo = min(surface)
        weights = [1.0 + 2.0 * (max(0, surface[x] - lo) == 0) + 1.8 *
                   (1.0 - (surface[x] - lo) / max(1.0, max(surface) - lo))
                   for x in range(size)]
        tot = sum(weights)
        for x in range(size):
            air["cloud"][x] += lift * weights[x] / tot

    # --- advection -----------------------------------------------------------
    u = wind(w["tick"])
    shift = int(u)
    frac = u - shift
    old = list(air["cloud"])
    for x in range(size):
        src_a = (x - shift) % size
        src_b = (x - shift - 1) % size
        air["cloud"][x] = old[src_a] * (1.0 - abs(frac)) + old[src_b] * abs(frac)

    # --- precipitation -------------------------------------------------------
    for x in range(size):
        c = air["cloud"][x]
        if c <= RAIN_THRESHOLD:
            continue
        drop = (c - RAIN_THRESHOLD) * RAIN_K
        drop = min(drop, c)
        air["cloud"][x] -= drop
        air["rain"][x] += drop

    # --- lateral soil flow and ponding --------------------------------------
    surface = w["terrain"]["surface"]
    water = soil["water"]
    delta = [0.0] * size
    for x in range(size):
        nx = (x + 1) % size
        head_a = water[x] - surface[x] * 3.0
        head_b = water[nx] - surface[nx] * 3.0
        flow = (head_a - head_b) * LATERAL_K
        flow = max(-water[nx] * 0.4, min(water[x] * 0.4, flow))
        delta[x] -= flow
        delta[nx] += flow
    for x in range(size):
        water[x] = max(0.0, water[x] + delta[x])

    return moved


def _downhill(w, x):
    size = w["size"]["w"]
    surface = w["terrain"]["surface"]
    left = (x - 1) % size
    right = (x + 1) % size
    # larger surface index == lower ground in screen space
    best = x
    if surface[left] > surface[best]:
        best = left
    if surface[right] > surface[best]:
        best = right
    return best
