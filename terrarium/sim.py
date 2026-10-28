"""Tick orchestration, world seeding, and the chronicle.

A tick is one simulated hour. Order of operations matters and is fixed:

  1. climate          insolation and temperature for this hour
  2. biology          flora, then grazers, then predators, then decomposers
  3. abiotic decay    slow litter breakdown
  4. hydrology        evaporation, condensation, advection, rainfall, runoff
  5. reaping          dead organisms removed, species counts refreshed
  6. stewardship      extinction recovery, if a guild has been lost
  7. bookkeeping      history sample and chronicle entries

Determinism: the RNG for tick t is seeded from (world seed, t), so a world
replays identically from its seed and tick count alone.
"""

import random

from . import genome as gm
from . import life
from .cycles import DAY, YEAR, season_name, step_climate, step_water
from .world import (SOIL_CAP, is_submerged, ledger_totals, new_world,
                    surface_y, wrap)

HISTORY_EVERY = 12
HISTORY_KEEP = 700
CHRONICLE_KEEP = 400

# Genesis seeds producers and decomposers only. Consumers arrive later, via
# steward(), once primary production can actually support them.
FOUNDERS = {"flora": 64, "grazer": 0, "predator": 0, "decomposer": 18}
RESEED_COOLDOWN = 360

KINDS = ("flora", "grazer", "predator", "decomposer")

# Soft per-guild ceilings. On reaching one, that guild stops reproducing;
# nothing is ever culled, so the ceiling shapes density without killing.
GUILD_CEILING = {"flora": 1500, "grazer": 180, "predator": 14, "decomposer": 340}


def tick_rng(w, offset=0):
    return random.Random((w["seed"] * 1000003) ^ (w["tick"] * 2654435761 + offset))


# --- seeding ----------------------------------------------------------------

def withdraw(w, x, c, cn, wpc):
    """Draw founder mass from the abiotic pools, honouring availability."""
    x = wrap(w, x)
    air = w["air"]
    soil = w["soil"]
    c = min(c, air["co2"] * 0.5)
    n_want = c / cn
    n = min(n_want, soil["mineral_n"][x] * 0.5)
    if n < n_want and n_want > 0:
        c *= n / n_want
    water = min(c * wpc, soil["water"][x] * 0.5)
    air["co2"] -= c
    soil["mineral_n"][x] -= n
    soil["water"][x] -= water
    return c, n, water


def _register(w, kind, gen, rng):
    sid = "s%d" % w["next_species"]
    w["next_species"] += 1
    w["species"][sid] = {
        "name": gm.species_name(rng),
        "kind": kind,
        "archetype": dict(gen),
        "founded": w["tick"],
        "parent": None,
        "born": 0,
    }
    return sid


def seed_life(w, rng):
    size = w["size"]["w"]
    land = [x for x in range(size) if not is_submerged(w, x)]
    if not land:
        land = list(range(size))

    stoich = {
        "flora": (life.CN_FLORA, life.WPC_FLORA, 5.0),
        "grazer": (life.CN_FAUNA, life.WPC_FAUNA, 9.0),
        "predator": (life.CN_FAUNA, life.WPC_FAUNA, 14.0),
        "decomposer": (life.CN_DECOMP, life.WPC_DECOMP, 1.2),
    }

    for kind in KINDS:
        cn, wpc, mass = stoich[kind]
        n_species = 2 if kind in ("flora", "decomposer") else 1
        species = [_register(w, kind, gm.seed_genome(kind, rng), rng)
                   for _ in range(n_species)]
        for i in range(FOUNDERS[kind]):
            sid = species[i % n_species]
            gen = gm.mutate(kind, w["species"][sid]["archetype"], rng)
            x = rng.choice(land)
            c, n, water = withdraw(w, x, mass, cn, wpc)
            if c <= 0.01:
                continue
            life.spawn(w, kind, sid, x, c, n, water, gen)

    names = ", ".join(sorted(s["name"] for s in w["species"].values()))
    w["chronicle"].append({
        "tick": 0, "type": "genesis",
        "text": "Terrarium seeded from %d founding lineages: %s." % (
            len(w["species"]), names),
    })
    return w


def create(seed=None):
    w = new_world(seed)
    rng = random.Random((w["seed"] << 1) ^ 0x5EED)
    seed_life(w, rng)
    sample_history(w, {"light": 0.0, "temp": w["air"]["temp"]})
    return w


# --- the tick ---------------------------------------------------------------

def step(w):
    rng = tick_rng(w)
    counts_now = population(w)
    w["flags"]["crowded"] = {k: counts_now[k] >= GUILD_CEILING[k] for k in KINDS}
    env = step_climate(w, rng)

    flora_by_col = {}
    grazer_by_col = {}
    size = w["size"]["w"]
    threat = [0.0] * size

    for o in w["organisms"]:
        if o["state"] == "dead":
            continue
        x = wrap(w, int(o["x"]))
        if o["kind"] == "flora":
            flora_by_col.setdefault(x, []).append(o)
        elif o["kind"] == "grazer":
            grazer_by_col.setdefault(x, []).append(o)
        elif o["kind"] == "predator":
            threat[x] += o["c"]

    canopy, shade = life.shade_map(w, flora_by_col)
    occupancy = {x: len(group) for x, group in flora_by_col.items()}
    germinated = life.step_seeds(w, env, rng, canopy, occupancy)

    roster = [o for o in w["organisms"] if o["state"] != "dead"]
    rng.shuffle(roster)

    for o in roster:
        if o["state"] == "dead":
            continue
        kind = o["kind"]
        if kind == "flora":
            life.step_flora(w, o, env, rng, canopy, shade)
        elif kind == "grazer":
            life.step_grazer(w, o, env, rng, flora_by_col, threat)
        elif kind == "predator":
            life.step_predator(w, o, env, rng, grazer_by_col, canopy)
        elif kind == "decomposer":
            life.step_decomposer(w, o, env, rng)

    life.abiotic_decay(w)
    hydro = step_water(w, rng)

    deaths = {}
    survivors = []
    for o in w["organisms"]:
        if o["state"] == "dead":
            deaths[o["kind"]] = deaths.get(o["kind"], 0) + 1
        else:
            o["y"] = surface_y(w, int(o["x"]))
            survivors.append(o)
    w["organisms"] = survivors

    w["tick"] += 1
    counts = population(w)
    steward(w, counts, rng)
    counts = population(w)

    env["germinated"] = germinated
    if w["tick"] % HISTORY_EVERY == 0:
        sample_history(w, env, hydro)
    narrate(w, counts, env, deaths)
    return env


def population(w):
    counts = {k: 0 for k in KINDS}
    for o in w["organisms"]:
        counts[o["kind"]] = counts.get(o["kind"], 0) + 1
    return counts


# --- stewardship ------------------------------------------------------------

def steward(w, counts, rng):
    """Recolonise a lost guild, so the terrarium survives indefinitely.

    This is the one process that is not purely emergent. Without it a single
    bad winter can end the run permanently; with it the world recovers the way
    a real patch does, from outside propagules. Every recolonisation is logged
    and counted in flags.reseeds, so it is never silent.
    """
    flags = w["flags"]
    last = flags.setdefault("last_reseed", {})
    tick = w["tick"]

    bio = guild_biomass(w)
    gates = {
        "decomposer": True,
        # only recolonise flora if the seed bank is also exhausted
        "flora": len(w["soil"].get("seeds", ())) == 0,
        "grazer": bio["flora"] >= 900.0 and counts["flora"] >= 40,
        "predator": bio["grazer"] >= 260.0 and counts["grazer"] >= 26,
    }
    stoich = {
        "flora": (life.CN_FLORA, life.WPC_FLORA, 2.5, 12),
        "grazer": (life.CN_FAUNA, life.WPC_FAUNA, 8.0, 14),
        "predator": (life.CN_FAUNA, life.WPC_FAUNA, 13.0, 3),
        "decomposer": (life.CN_DECOMP, life.WPC_DECOMP, 1.1, 10),
    }

    for kind in KINDS:
        if counts[kind] > 0 or not gates[kind]:
            continue
        if tick - last.get(kind, -RESEED_COOLDOWN) < RESEED_COOLDOWN:
            continue
        cn, wpc, mass, n = stoich[kind]
        source = _ancestral(w, kind)
        gen = gm.mutate(kind, source, rng) if source else gm.seed_genome(kind, rng)
        sid = _register(w, kind, gen, rng)
        land = [x for x in range(w["size"]["w"]) if not is_submerged(w, x)] or [0]
        placed = 0
        for _ in range(n):
            x = rng.choice(land)
            c, nn, water = withdraw(w, x, mass, cn, wpc)
            if c <= 0.01:
                continue
            life.spawn(w, kind, sid, x, c, nn, water, gm.mutate(kind, gen, rng))
            placed += 1
        if placed:
            last[kind] = tick
            flags["reseeds"] = flags.get("reseeds", 0) + 1
            w["chronicle"].append({
                "tick": tick, "type": "recolonisation",
                "text": "%s guild had died out; %s arrives from outside with "
                        "%d propagules." % (kind.capitalize(),
                                            w["species"][sid]["name"], placed),
            })


def guild_biomass(w):
    bio = {k: 0.0 for k in KINDS}
    for o in w["organisms"]:
        bio[o["kind"]] += o["c"]
    return bio


def _ancestral(w, kind):
    best = None
    for sp in w["species"].values():
        if sp["kind"] != kind:
            continue
        if best is None or sp["founded"] > best["founded"]:
            best = sp
    return dict(best["archetype"]) if best else None


# --- bookkeeping ------------------------------------------------------------

def sample_history(w, env, hydro=None):
    counts = population(w)
    biomass = {k: 0.0 for k in KINDS}
    for o in w["organisms"]:
        biomass[o["kind"]] += o["c"]
    soil = w["soil"]
    air = w["air"]
    w["history"].append({
        "t": w["tick"],
        "temp": round(air["temp"], 2),
        "light": round(env.get("light", 0.0), 3),
        "co2": round(air["co2"], 1),
        "humidity": round(air["humidity"], 1),
        "cloud": round(sum(air["cloud"]), 1),
        "soil_water": round(sum(soil["water"]), 1),
        "mineral_n": round(sum(soil["mineral_n"]), 2),
        "detritus_c": round(sum(soil["detritus_c"]), 1),
        "rain": round(hydro["rain"], 2) if hydro else 0.0,
        "seeds": len(w["soil"].get("seeds", ())),
        "germinated": env.get("germinated", 0),
        "pop": counts,
        "bio": {k: round(v, 1) for k, v in biomass.items()},
        "species": len(living_species(w)),
    })
    if len(w["history"]) > HISTORY_KEEP:
        del w["history"][:len(w["history"]) - HISTORY_KEEP]


def living_species(w):
    seen = {}
    for o in w["organisms"]:
        seen[o["species"]] = seen.get(o["species"], 0) + 1
    return seen


def narrate(w, counts, env, deaths):
    """Append notable events. Terse by design: one line, with the tick."""
    tick = w["tick"]
    flags = w["flags"]
    log = w["chronicle"]
    prev = flags.setdefault("marks", {})

    def mark(key, text, cooldown=240):
        if tick - prev.get(key, -10 ** 9) < cooldown:
            return
        prev[key] = tick
        log.append({"tick": tick, "type": key, "text": text})

    # seasonal turn
    if tick % YEAR == 0:
        mark("year", "Year %d begins; %s, %.1f C." % (
            tick // YEAR + 1, season_name(tick), env["temp"]), cooldown=1)

    total_bio = sum(o["c"] for o in w["organisms"])
    if counts["flora"] == 0:
        mark("flora_crash", "No flora remain. Primary production has stopped.")
    if counts["predator"] == 0 and counts["grazer"] > 60:
        mark("herbivore_boom",
             "Grazers at %d with no predators; canopy under pressure."
             % counts["grazer"])
    if counts["decomposer"] == 0:
        mark("decomp_loss",
             "Decomposers lost. Litter is accumulating and mineral nitrogen "
             "will fall.")

    mineral = sum(w["soil"]["mineral_n"])
    if mineral < 60 and counts["flora"] > 0:
        mark("n_limit", "Mineral nitrogen down to %.0f units; growth is now "
                        "nutrient limited." % mineral)
    sat = sum(w["soil"]["water"]) / (SOIL_CAP * w["size"]["w"])
    if sat < 0.18:
        mark("drought", "Soil saturation %.0f%%; drought conditions."
             % (sat * 100))
    elif sat > 0.92:
        mark("flood", "Soil saturation %.0f%%; waterlogged ground."
             % (sat * 100))
    if env["temp"] < -4:
        mark("freeze", "Temperature %.1f C; a hard freeze." % env["temp"])
    if env["temp"] > 30:
        mark("heat", "Temperature %.1f C; heat stress across the slope."
             % env["temp"])
    if total_bio > flags.get("bio_peak", 0.0) * 1.35 and total_bio > 200:
        flags["bio_peak"] = total_bio
        mark("peak", "Living biomass reaches a new high of %.0f units."
             % total_bio, cooldown=480)

    if len(log) > CHRONICLE_KEEP:
        del log[:len(log) - CHRONICLE_KEEP]


def advance(w, ticks):
    for _ in range(int(ticks)):
        step(w)
    return w
