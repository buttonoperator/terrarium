"""Heritable traits, mutation, and speciation.

Every organism carries a genome: a flat mapping of trait name to float. Traits
are clamped to a per-trait domain defined in TRAIT_SPECS. Reproduction copies
the parent genome and perturbs each trait by a Gaussian whose width scales with
the trait's domain and the parent's own (heritable) mutation_rate.

Speciation is measured, not scripted. Each lineage stores an archetype genome.
When a newborn's normalised genetic distance from its species archetype exceeds
SPECIATION_DISTANCE, a new species is minted with the newborn as archetype.
"""

import math

# name: (low, high, seed_mean, seed_sd)
TRAIT_SPECS = {
    "flora": {
        "growth_rate": (0.02, 0.60, 0.18, 0.04),
        "water_need": (0.10, 1.40, 0.60, 0.12),
        "nutrient_need": (0.10, 1.40, 0.60, 0.12),
        "shade_tolerance": (0.00, 1.00, 0.45, 0.12),
        "max_height": (1.00, 9.00, 4.00, 1.00),
        "drought_tolerance": (0.00, 1.00, 0.35, 0.12),
        "thermal_opt": (2.00, 30.00, 17.00, 2.50),
        "thermal_width": (3.00, 22.00, 10.00, 2.00),
        "seed_count": (1.00, 6.00, 2.60, 0.80),
        "dormancy": (0.02, 0.90, 0.30, 0.10),
        "seed_invest": (0.10, 0.70, 0.32, 0.07),
        "lifespan": (240.0, 5000.0, 1500.0, 300.0),
        "mutation_rate": (0.005, 0.120, 0.030, 0.008),
    },
    "grazer": {
        "metabolism": (0.015, 0.220, 0.070, 0.015),
        "size": (0.40, 4.50, 1.30, 0.30),
        "speed": (0.20, 3.20, 1.10, 0.25),
        "vision": (1.00, 18.00, 6.00, 1.50),
        "forage_eff": (0.20, 0.95, 0.55, 0.08),
        "repro_threshold": (1.20, 7.00, 3.00, 0.60),
        "repro_invest": (0.15, 0.65, 0.38, 0.07),
        "drought_tolerance": (0.00, 1.00, 0.35, 0.12),
        "thermal_opt": (2.00, 30.00, 17.00, 2.50),
        "thermal_width": (3.00, 22.00, 10.00, 2.00),
        "wariness": (0.00, 1.00, 0.45, 0.12),
        "lifespan": (200.0, 4000.0, 1100.0, 220.0),
        "mutation_rate": (0.005, 0.120, 0.032, 0.008),
    },
    "predator": {
        "metabolism": (0.020, 0.260, 0.090, 0.018),
        "size": (0.60, 5.50, 1.80, 0.35),
        "speed": (0.30, 3.80, 1.50, 0.30),
        "vision": (2.00, 24.00, 9.00, 2.00),
        "attack": (0.15, 0.95, 0.50, 0.09),
        "repro_threshold": (2.00, 10.00, 4.50, 0.80),
        "repro_invest": (0.15, 0.65, 0.40, 0.07),
        "thermal_opt": (2.00, 30.00, 17.00, 2.50),
        "thermal_width": (3.00, 22.00, 10.00, 2.00),
        "patience": (0.00, 1.00, 0.50, 0.12),
        "lifespan": (260.0, 4600.0, 1400.0, 260.0),
        "mutation_rate": (0.005, 0.120, 0.030, 0.008),
    },
    "decomposer": {
        "metabolism": (0.030, 0.400, 0.140, 0.030),
        "moisture_opt": (0.10, 0.95, 0.55, 0.10),
        "moisture_width": (0.08, 0.70, 0.28, 0.06),
        "efficiency": (0.20, 0.90, 0.50, 0.08),
        "thermal_opt": (2.00, 32.00, 19.00, 2.50),
        "thermal_width": (4.00, 24.00, 12.00, 2.00),
        "repro_threshold": (1.50, 7.00, 3.00, 0.60),
        "repro_invest": (0.20, 0.70, 0.45, 0.08),
        "lifespan": (120.0, 2200.0, 600.0, 140.0),
        "mutation_rate": (0.005, 0.090, 0.030, 0.008),
    },
}

SPECIATION_DISTANCE = 0.42

_ONSET = ["ka", "thi", "vo", "mor", "sel", "dra", "pyr", "lun", "ves", "cor",
          "nim", "ald", "bro", "cyn", "fen", "gla", "hes", "ith", "jor", "kla"]
_CODA = ["ara", "ix", "une", "oth", "eia", "ur", "ynth", "ola", "ex", "ium",
         "ane", "iss", "oro", "yl", "arn", "edra", "ope", "ust", "iva", "orn"]


def species_name(rng):
    return (rng.choice(_ONSET) + rng.choice(_CODA)).capitalize()


def seed_genome(kind, rng):
    g = {}
    for trait, (lo, hi, mean, sd) in TRAIT_SPECS[kind].items():
        g[trait] = _clamp(rng.gauss(mean, sd), lo, hi)
    return g


def mutate(kind, genome, rng):
    spec = TRAIT_SPECS[kind]
    rate = genome.get("mutation_rate", 0.03)
    child = {}
    for trait, (lo, hi, _m, _s) in spec.items():
        base = genome.get(trait, (lo + hi) / 2.0)
        sigma = (hi - lo) * rate
        child[trait] = _clamp(rng.gauss(base, sigma), lo, hi)
    return child


def distance(kind, a, b):
    """Normalised RMS trait distance in [0, ~1]."""
    spec = TRAIT_SPECS[kind]
    acc = 0.0
    for trait, (lo, hi, _m, _s) in spec.items():
        span = hi - lo
        d = (a.get(trait, lo) - b.get(trait, lo)) / span
        acc += d * d
    return math.sqrt(acc / len(spec))


def thermal_fitness(genome, temp_c):
    """Gaussian tolerance curve in [0, 1] around the genome's thermal optimum."""
    opt = genome.get("thermal_opt", 17.0)
    width = max(genome.get("thermal_width", 10.0), 1e-3)
    z = (temp_c - opt) / width
    return math.exp(-0.5 * z * z)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


clamp = _clamp
