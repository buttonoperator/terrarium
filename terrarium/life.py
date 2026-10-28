"""Biotic processes for the four trophic roles.

Every organism holds three masses: c (carbon / structure and reserve), n
(nitrogen), w (water). All transfers are explicit and balanced:

  flora       air.co2 + soil.mineral_n + soil.water -> c, n, w   (photosynthesis)
              c -> air.co2                                       (respiration)
              w -> air.humidity                                  (transpiration)
  grazer      flora.c -> c, unassimilated fraction to soil.detritus_c
  predator    grazer.c -> c, likewise
  decomposer  soil.detritus_c -> c + air.co2
              soil.detritus_n -> n + soil.mineral_n              (mineralisation)
  death       c -> soil.detritus_c, n -> soil.detritus_n, w -> soil.water

Decomposers are the only route from litter back to plant-available nitrogen.
Remove them and the system locks up: carbon accumulates as detritus, mineral
nitrogen falls to zero, and flora starve in a nutrient-poor world. That
coupling is the point of the model.
"""

import math

from . import genome as gm
from .world import SOIL_CAP, is_submerged, wrap

CN_FLORA = 35.0
CN_FAUNA = 9.0
CN_DECOMP = 7.0

WPC_FLORA = 2.6           # water held per unit carbon
WPC_FAUNA = 1.3
WPC_DECOMP = 1.1

FLORA_RESP = 0.0052      # maintenance cost per unit carbon per tick
FLORA_TRANSP = 0.030
FLORA_MATURE_C = 16.0
GPP_SCALE = 1.00         # converts trait-space growth_rate into carbon per tick

FAUNA_BODY_C = 6.0
STARVE_FRAC = 0.34
GRAZE_REFUGE = 1.1        # carbon below which growth is out of reach
GRAZE_HALF_SAT = 7.0      # half-saturation of the grazer functional response
HUNT_HANDLING = 8         # ticks a predator spends on a carcass before hunting
HUNT_ODDS_CAP = 0.26      # ceiling on per-tick kill probability
DECOMP_MAINT = 0.08      # fraction of metabolic rate spent on upkeep
ABIOTIC_DECAY = 0.00035   # slow chemical breakdown without decomposers


# --- helpers ----------------------------------------------------------------

def crowded(w, kind):
    """True when a guild is at its soft ceiling; reproduction pauses."""
    return bool(w["flags"].get("crowded", {}).get(kind))


def body_c(o):
    return FAUNA_BODY_C * o["genome"]["size"]


def flora_mature_c(o):
    return FLORA_MATURE_C * (0.4 + 0.6 * o["genome"]["max_height"] / 4.0)


def flora_height(o):
    g = o["genome"]
    frac = min(1.0, o["c"] / max(1e-6, flora_mature_c(o)))
    return max(0.6, g["max_height"] * math.sqrt(frac))


def spawn(w, kind, species, x, c, n, water, gen, parent=None):
    oid = w["next_id"]
    w["next_id"] += 1
    o = {
        "id": oid, "kind": kind, "species": species,
        "x": float(x), "c": c, "n": n, "w": water,
        "genome": gen, "age": 0, "born": w["tick"],
        "parent": parent, "stress": 0.0, "facing": 1,
        "state": "idle", "fed": w["tick"],
    }
    w["organisms"].append(o)
    sp = w["species"].get(species)
    if sp:
        sp["born"] = sp.get("born", 0) + 1
    return o


def kill(w, o, cause):
    """Return the mass held by an organism to the soil pools."""
    x = wrap(w, int(o["x"]))
    soil = w["soil"]
    soil["detritus_c"][x] += o["c"]
    soil["detritus_n"][x] += o["n"]
    soil["water"][x] += o["w"]
    o["c"] = o["n"] = o["w"] = 0.0
    o["state"] = "dead"
    o["cause"] = cause


# --- flora ------------------------------------------------------------------

def shade_map(w, flora_by_col):
    """Per-column canopy height and shade factor, for light competition."""
    size = w["size"]["w"]
    canopy = [0.0] * size
    for x, group in flora_by_col.items():
        canopy[x] = max((flora_height(o) for o in group), default=0.0)
    shade = [0.0] * size
    for x in range(size):
        local = canopy[x]
        left = canopy[(x - 1) % size]
        right = canopy[(x + 1) % size]
        shade[x] = min(1.0, (local + 0.45 * (left + right)) / 14.0)
    return canopy, shade


def step_flora(w, o, env, rng, canopy, shade):
    g = o["genome"]
    x = wrap(w, int(o["x"]))
    soil = w["soil"]
    air = w["air"]

    h = flora_height(o)
    tallest = canopy[x]
    overshadow = max(0.0, (tallest - h) / 14.0) + shade[x] * 0.6
    light = env["light"] * (1.0 - min(0.95, overshadow * (1.0 - g["shade_tolerance"])))
    light *= 1.0 - 0.55 * min(1.0, air["cloud"][x] / 110.0)

    sat = soil["water"][x] / SOIL_CAP
    water_fit = min(1.0, sat / max(0.05, g["water_need"] * 0.55))
    if sat < 0.12:
        water_fit *= g["drought_tolerance"] * 0.8 + 0.2
    if is_submerged(w, x) and sat > 1.3:
        water_fit *= 0.45                      # waterlogged roots

    thermal = gm.thermal_fitness(g, env["temp"])
    mature = flora_mature_c(o)
    # photosynthetic surface scales sublinearly with mass; growth tapers as
    # the individual approaches the structural ceiling its genome allows
    leaf_area = max(0.35, o["c"] ** 0.72)
    taper = max(0.0, 1.0 - o["c"] / (2.4 * mature))

    # gross primary production
    gpp = (g["growth_rate"] * light * water_fit * thermal
           * leaf_area * taper * GPP_SCALE)
    gpp = min(gpp, air["co2"] * 0.002)

    need_n = gpp / CN_FLORA * g["nutrient_need"]
    avail_n = soil["mineral_n"][x]
    if need_n > avail_n:
        scale = avail_n / need_n if need_n > 0 else 0.0
        gpp *= scale
        need_n = avail_n
    need_w = gpp * WPC_FLORA
    if need_w > soil["water"][x]:
        scale = soil["water"][x] / need_w if need_w > 0 else 0.0
        gpp *= scale
        need_n *= scale
        need_w = soil["water"][x]

    if gpp > 0:
        air["co2"] -= gpp
        o["c"] += gpp
        soil["mineral_n"][x] -= need_n
        o["n"] += need_n
        soil["water"][x] -= need_w
        o["w"] += need_w

    # autotrophic respiration
    resp = min(o["c"], o["c"] * FLORA_RESP * (0.3 + 0.7 * thermal))
    o["c"] -= resp
    air["co2"] += resp

    # transpiration
    trans = o["w"] * FLORA_TRANSP * (0.25 + 0.75 * env["light"]) * max(0.2, thermal)
    trans = min(trans, o["w"])
    o["w"] -= trans
    air["humidity"] += trans

    # surplus nitrogen is shed as leaf litter
    target_n = o["c"] / CN_FLORA
    if o["n"] > target_n * 1.25:
        shed = o["n"] - target_n * 1.25
        o["n"] -= shed
        soil["detritus_n"][x] += shed

    stressed = (water_fit < 0.3) or (thermal < 0.25) or (light < 0.05)
    o["stress"] = gm.clamp(o["stress"] * 0.97 + (0.03 if stressed else -0.01), 0.0, 1.0)
    o["state"] = "stressed" if o["stress"] > 0.45 else "idle"

    o["age"] += 1
    if o["age"] > g["lifespan"] or o["stress"] > 0.93 or o["c"] < 0.05:
        kill(w, o, "senescence" if o["age"] > g["lifespan"] else "stress")
        return None

    # seeding into the dormant seed bank
    if o["c"] > mature * 1.15 and o["stress"] < 0.4 and rng.random() < 0.09:
        sow(w, o, rng)
    return None


# --- seed bank --------------------------------------------------------------

COLUMN_FLORA_CAP = 6      # rooting space available in one column
SEED_BANK_CAP = 900
SEED_RESPIRATION = 0.00055
SEED_VIABILITY = 2600         # ticks before a seed is no longer viable
SEED_STRATIFY = 40            # minimum rest before any seed can germinate


def sow(w, parent, rng):
    """Move reproductive investment from a parent into dormant seeds.

    Seeds hold real mass and a real genome. They are the memory of the system:
    after a crash the bank germinates and flora return without any outside
    input, which is why recolonisation stays rare.
    """
    g = parent["genome"]
    soil = w["soil"]
    x = wrap(w, int(parent["x"]))
    invest = parent["c"] * g["seed_invest"]
    count = max(1, int(round(g["seed_count"])))
    each_c = invest / count
    if each_c < 0.22:
        return
    parent["c"] -= invest
    take_n = min(parent["n"] * g["seed_invest"], invest / CN_FLORA)
    parent["n"] -= take_n
    take_w = min(parent["w"] * g["seed_invest"], invest * WPC_FLORA)
    parent["w"] -= take_w

    bank = soil["seeds"]
    for _ in range(count):
        cx = wrap(w, x + int(round(rng.gauss(0.0, 4.0))))
        n_each = take_n / count
        w_each = take_w / count
        if is_submerged(w, cx) or len(bank) >= SEED_BANK_CAP:
            soil["detritus_c"][cx] += each_c
            soil["detritus_n"][cx] += n_each
            soil["water"][cx] += w_each
            continue
        child_g = gm.mutate("flora", g, rng)
        bank.append({
            "x": cx, "c": each_c, "n": n_each, "w": w_each,
            "genome": child_g,
            "species": resolve_species(w, "flora", child_g,
                                       parent["species"], rng),
            "sown": w["tick"],
        })


def step_seeds(w, env, rng, canopy, occupancy):
    """Age, respire, and germinate the seed bank."""
    soil = w["soil"]
    air = w["air"]
    bank = soil["seeds"]
    if not bank:
        return 0
    keep = []
    germinated = 0
    blocked = crowded(w, "flora")
    for sd in bank:
        x = sd["x"]
        loss = min(sd["c"], sd["c"] * SEED_RESPIRATION)
        sd["c"] -= loss
        air["co2"] += loss

        age = w["tick"] - sd["sown"]
        if sd["c"] < 0.08 or age > SEED_VIABILITY:
            soil["detritus_c"][x] += sd["c"]
            soil["detritus_n"][x] += sd["n"]
            soil["water"][x] += sd["w"]
            continue

        g = sd["genome"]
        # stratification: a seed must rest before it is germinable, so part
        # of every clutch is always held in reserve against a bad season
        if age < SEED_STRATIFY + g["dormancy"] * 620:
            keep.append(sd)
            continue

        sat = soil["water"][x] / SOIL_CAP
        thermal = gm.thermal_fitness(g, env["temp"])
        wet = min(1.0, sat / max(0.08, g["water_need"] * 0.45))
        gap = max(0.0, 1.0 - canopy[x] / 9.0)      # germinates into light gaps
        chance = ((1.0 - g["dormancy"]) * 0.05 * thermal * wet
                  * (0.25 + 0.75 * gap) * (0.3 + 0.7 * env["light"]))
        room = occupancy.get(x, 0) < COLUMN_FLORA_CAP
        if room and not blocked and rng.random() < chance:
            occupancy[x] = occupancy.get(x, 0) + 1
            spawn(w, "flora", sd["species"], x, sd["c"], sd["n"], sd["w"],
                  g, None)
            germinated += 1
            continue
        keep.append(sd)
    soil["seeds"] = keep
    return germinated


# --- fauna ------------------------------------------------------------------

def step_grazer(w, o, env, rng, flora_by_col, threat_by_col):
    g = o["genome"]
    air = w["air"]
    soil = w["soil"]
    size = w["size"]["w"]
    thermal = gm.thermal_fitness(g, env["temp"])

    resp = min(o["c"], o["c"] * g["metabolism"] * (0.55 + 0.45 * (2.0 - thermal)) * 0.12)
    o["c"] -= resp
    air["co2"] += resp

    lost_w = min(o["w"], o["w"] * 0.030 * (0.5 + 0.5 * max(0.0, env["temp"]) / 25.0))
    o["w"] -= lost_w
    air["humidity"] += lost_w

    here = wrap(w, int(o["x"]))
    vision = int(max(1, g["vision"]))

    threat = 0.0
    flee_dir = 0
    for d in range(-vision, vision + 1):
        t = threat_by_col[wrap(w, here + d)]
        if t > threat:
            threat = t
            flee_dir = -1 if d > 0 else 1

    if threat > 0 and rng.random() < g["wariness"]:
        o["x"] = (o["x"] + flee_dir * g["speed"] * 1.3) % size
        o["facing"] = flee_dir or o["facing"]
        o["state"] = "fleeing"
    else:
        best, best_d = 0.0, 0
        for d in range(-vision, vision + 1):
            cx = wrap(w, here + d)
            mass = sum(f["c"] for f in flora_by_col.get(cx, ()))
            score = mass / (1.0 + abs(d) * 0.5)
            if score > best:
                best, best_d = score, d
        if best_d != 0:
            step = gm.clamp(best_d, -g["speed"], g["speed"])
            o["x"] = (o["x"] + step) % size
            o["facing"] = 1 if step > 0 else -1
            o["state"] = "moving"
        else:
            o["state"] = "grazing"

        here = wrap(w, int(o["x"]))
        group = flora_by_col.get(here)
        edible = [f for f in group if f["c"] > GRAZE_REFUGE] if group else []
        if edible:
            target = max(edible, key=lambda f: f["c"])
            # Holling type II intake: a saturating functional response, plus a
            # refuge of unreachable small growth. Together these stop grazers
            # from eating the producer base to zero.
            avail = target["c"] - GRAZE_REFUGE
            ceiling = body_c(o) * 0.22 * (0.4 + 0.6 * thermal)
            bite = ceiling * avail / (GRAZE_HALF_SAT + avail)
            bite = min(bite, avail)
            if bite > 0.01:
                frac = bite / target["c"]
                bite_n = target["n"] * frac
                bite_w = target["w"] * frac
                target["c"] -= bite
                target["n"] -= bite_n
                target["w"] -= bite_w
                eff = g["forage_eff"]
                o["c"] += bite * eff
                o["n"] += bite_n * eff
                o["w"] += bite_w * eff
                soil["detritus_c"][here] += bite * (1 - eff)
                soil["detritus_n"][here] += bite_n * (1 - eff)
                soil["water"][here] += bite_w * (1 - eff)
                o["fed"] = w["tick"]
                o["state"] = "grazing"

    _balance_fauna(w, o, CN_FAUNA, WPC_FAUNA)

    o["age"] += 1
    if o["c"] < body_c(o) * STARVE_FRAC:
        kill(w, o, "starvation")
        return None
    if o["age"] > g["lifespan"]:
        kill(w, o, "senescence")
        return None

    return _maybe_breed(w, o, "grazer", rng)


def step_predator(w, o, env, rng, prey_by_col, canopy):
    g = o["genome"]
    air = w["air"]
    soil = w["soil"]
    size = w["size"]["w"]
    thermal = gm.thermal_fitness(g, env["temp"])

    resp = min(o["c"], o["c"] * g["metabolism"] * (0.55 + 0.45 * (2.0 - thermal)) * 0.12)
    o["c"] -= resp
    air["co2"] += resp
    lost_w = min(o["w"], o["w"] * 0.028)
    o["w"] -= lost_w
    air["humidity"] += lost_w

    here = wrap(w, int(o["x"]))
    vision = int(max(1, g["vision"]))
    best, best_d = 0.0, 0
    for d in range(-vision, vision + 1):
        cx = wrap(w, here + d)
        mass = sum(p["c"] for p in prey_by_col.get(cx, ()))
        score = mass / (1.0 + abs(d) * 0.4)
        if score > best:
            best, best_d = score, d

    if best <= 0.0:
        if rng.random() > g["patience"]:
            o["x"] = (o["x"] + rng.choice((-1, 1)) * g["speed"] * 0.6) % size
        o["state"] = "waiting"
    elif best_d != 0:
        step = gm.clamp(best_d, -g["speed"], g["speed"])
        o["x"] = (o["x"] + step) % size
        o["facing"] = 1 if step > 0 else -1
        o["state"] = "stalking"
    else:
        o["state"] = "hunting"

    here = wrap(w, int(o["x"]))
    group = prey_by_col.get(here)
    sated = o["c"] > g["repro_threshold"] * body_c(o) * 1.2
    handling = (w["tick"] - o["fed"]) < HUNT_HANDLING
    if group and not sated and not handling:
        prey = min(group, key=lambda p: p["c"])
        pg = prey["genome"]
        odds = g["attack"] * (0.4 + 0.6 * gm.clamp(
            g["speed"] / max(0.1, pg["speed"]), 0.0, 2.0) / 2.0)
        odds *= 1.0 - 0.45 * pg["wariness"]
        odds *= gm.clamp(g["size"] / max(0.1, pg["size"]), 0.25, 1.6) / 1.6
        # canopy is cover: dense vegetation shelters prey, which couples the
        # predator guild to primary production rather than to prey alone
        odds *= 1.0 - 0.55 * min(1.0, canopy[here] / 9.0)
        if rng.random() < gm.clamp(odds, 0.0, HUNT_ODDS_CAP):
            eff = 0.62
            o["c"] += prey["c"] * eff
            o["n"] += prey["n"] * eff
            o["w"] += prey["w"] * eff
            soil["detritus_c"][here] += prey["c"] * (1 - eff)
            soil["detritus_n"][here] += prey["n"] * (1 - eff)
            soil["water"][here] += prey["w"] * (1 - eff)
            prey["c"] = prey["n"] = prey["w"] = 0.0
            prey["state"] = "dead"
            prey["cause"] = "predation"
            o["fed"] = w["tick"]
            o["state"] = "feeding"

    _balance_fauna(w, o, CN_FAUNA, WPC_FAUNA)

    o["age"] += 1
    if o["c"] < body_c(o) * STARVE_FRAC:
        kill(w, o, "starvation")
        return None
    if o["age"] > g["lifespan"]:
        kill(w, o, "senescence")
        return None

    return _maybe_breed(w, o, "predator", rng)


def step_decomposer(w, o, env, rng):
    g = o["genome"]
    air = w["air"]
    soil = w["soil"]
    x = wrap(w, int(o["x"]))

    sat = soil["water"][x] / SOIL_CAP
    z = (sat - g["moisture_opt"]) / max(1e-3, g["moisture_width"])
    moisture_fit = math.exp(-0.5 * z * z)
    thermal = gm.thermal_fitness(g, env["temp"] * 0.85 + 3.0)

    # basal maintenance: decomposers starve when litter runs out, which is
    # what makes the nutrient cycle oscillate rather than saturate
    maint = min(o["c"], o["c"] * g["metabolism"] * DECOMP_MAINT)
    o["c"] -= maint
    air["co2"] += maint

    demand = o["c"] * g["metabolism"] * moisture_fit * thermal
    intake = min(demand, soil["detritus_c"][x] * 0.25)
    if intake > 0:
        frac = intake / max(1e-9, soil["detritus_c"][x])
        intake_n = soil["detritus_n"][x] * frac
        soil["detritus_c"][x] -= intake
        soil["detritus_n"][x] -= intake_n
        eff = g["efficiency"]
        o["c"] += intake * eff
        air["co2"] += intake * (1 - eff)           # heterotrophic respiration
        keep_n = min(intake_n, intake * eff / CN_DECOMP)
        o["n"] += keep_n
        soil["mineral_n"][x] += intake_n - keep_n  # mineralisation
        o["fed"] = w["tick"]
        o["state"] = "active"
    else:
        o["state"] = "dormant"

    want_w = o["c"] * WPC_DECOMP
    if o["w"] < want_w:
        draw = min(want_w - o["w"], soil["water"][x] * 0.05)
        soil["water"][x] -= draw
        o["w"] += draw
    elif o["w"] > want_w * 1.3:
        give = o["w"] - want_w * 1.3
        o["w"] -= give
        soil["water"][x] += give

    o["age"] += 1
    if o["c"] < 0.08 or o["age"] > g["lifespan"]:
        kill(w, o, "senescence" if o["age"] > g["lifespan"] else "starvation")
        return None

    if (o["c"] > g["repro_threshold"] and not crowded(w, "decomposer")
            and rng.random() < 0.22):
        invest = o["c"] * g["repro_invest"]
        o["c"] -= invest
        take_n = min(o["n"] * g["repro_invest"], invest / CN_DECOMP)
        o["n"] -= take_n
        take_w = min(o["w"] * g["repro_invest"], invest * WPC_DECOMP)
        o["w"] -= take_w
        cg = gm.mutate("decomposer", g, rng)
        sp = resolve_species(w, "decomposer", cg, o["species"], rng)
        cx = wrap(w, x + rng.choice((-2, -1, 0, 1, 2)))
        return [spawn(w, "decomposer", sp, cx, invest, take_n, take_w, cg, o["id"])]
    return None


def _balance_fauna(w, o, cn, wpc):
    """Shed surplus nitrogen and water; thirst draws from the soil."""
    x = wrap(w, int(o["x"]))
    soil = w["soil"]
    target_n = o["c"] / cn
    if o["n"] > target_n * 1.2:
        shed = o["n"] - target_n * 1.2
        o["n"] -= shed
        soil["detritus_n"][x] += shed
    want_w = o["c"] * wpc
    if o["w"] < want_w:
        draw = min(want_w - o["w"], soil["water"][x] * 0.04)
        soil["water"][x] -= draw
        o["w"] += draw
    elif o["w"] > want_w * 1.35:
        give = o["w"] - want_w * 1.35
        o["w"] -= give
        soil["water"][x] += give


def _maybe_breed(w, o, kind, rng):
    g = o["genome"]
    if crowded(w, kind):
        return None
    if o["c"] < g["repro_threshold"] * body_c(o):
        return None
    if rng.random() > (0.26 if kind == "grazer" else 0.20):
        return None
    invest = o["c"] * g["repro_invest"]
    litter = 1 if kind == "predator" else rng.choice((1, 1, 2))
    each = invest / litter
    if each < body_c(o) * 0.45:
        return None
    o["c"] -= invest
    take_n = min(o["n"] * g["repro_invest"], invest / CN_FAUNA)
    o["n"] -= take_n
    take_w = min(o["w"] * g["repro_invest"], invest * WPC_FAUNA)
    o["w"] -= take_w
    out = []
    for _ in range(litter):
        cg = gm.mutate(kind, g, rng)
        sp = resolve_species(w, kind, cg, o["species"], rng)
        cx = wrap(w, int(o["x"]) + rng.choice((-1, 0, 1)))
        out.append(spawn(w, kind, sp, cx, each, take_n / litter,
                         take_w / litter, cg, o["id"]))
    return out


# --- speciation -------------------------------------------------------------

def resolve_species(w, kind, child_g, parent_species, rng):
    reg = w["species"]
    parent = reg.get(parent_species)
    if parent is None:
        return parent_species
    d = gm.distance(kind, child_g, parent["archetype"])
    if d < gm.SPECIATION_DISTANCE:
        return parent_species
    sid = "s%d" % w["next_species"]
    w["next_species"] += 1
    reg[sid] = {
        "name": gm.species_name(rng),
        "kind": kind,
        "archetype": dict(child_g),
        "founded": w["tick"],
        "parent": parent_species,
        "born": 0,
    }
    w["chronicle"].append({
        "tick": w["tick"], "type": "speciation",
        "text": "%s diverged from %s (%s), genetic distance %.2f" % (
            reg[sid]["name"], parent["name"], kind, d),
    })
    return sid


def abiotic_decay(w):
    """Slow non-biological breakdown, so litter is never permanently locked."""
    soil = w["soil"]
    air = w["air"]
    for x in range(w["size"]["w"]):
        c = soil["detritus_c"][x]
        if c <= 0:
            continue
        loss = c * ABIOTIC_DECAY
        frac = loss / c
        n_loss = soil["detritus_n"][x] * frac
        soil["detritus_c"][x] -= loss
        soil["detritus_n"][x] -= n_loss
        air["co2"] += loss
        soil["mineral_n"][x] += n_loss
