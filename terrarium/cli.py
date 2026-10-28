"""Command line entry point.

    python -m terrarium new   [--seed N]      create a world
    python -m terrarium tick  [--ticks N]     advance, render, publish
    python -m terrarium render                re-render the current state
    python -m terrarium report                print a status summary
    python -m terrarium audit  [--ticks N]    run ticks in memory, check the ledger

State is split across files so that commit diffs stay interpretable:

    state/world.json      terrain, soil, atmosphere, organisms, species
    state/history.json    time series samples
    state/chronicle.json  notable events
    docs/frame.png        latest frame (embedded in the README)
    docs/frames/*.png     rolling archive the viewer animates
    docs/data/*.json      summary, history and chronicle for the viewer
"""

import argparse
import datetime
import json
import os
import re
import shutil

from . import render as rnd
from . import sim
from .genome import TRAIT_SPECS
from .cycles import DAY, YEAR, season_name
from .life import flora_height
from .world import SOIL_CAP, ledger_report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
DOCS = os.path.join(ROOT, "docs")
FRAMES = os.path.join(DOCS, "frames")
DATA = os.path.join(DOCS, "data")

WORLD = os.path.join(STATE, "world.json")
HISTORY = os.path.join(STATE, "history.json")
CHRONICLE = os.path.join(STATE, "chronicle.json")
CHRONICLE_MD = os.path.join(STATE, "CHRONICLE.md")
README = os.path.join(ROOT, "README.md")

FRAME_KEEP = 64
FRAME_EVERY = 1
GENOME_DP = 4
CODEC = 1

# Genomes are stored as ordered value lists rather than key/value maps. The
# state file is rewritten on every tick and lives in git forever, so the key
# names would otherwise dominate the commit size.
TRAIT_ORDER = {kind: sorted(spec) for kind, spec in TRAIT_SPECS.items()}


# --- persistence ------------------------------------------------------------

def _round_genomes(w):
    for o in w["organisms"]:
        o["genome"] = {k: round(v, GENOME_DP) for k, v in o["genome"].items()}
    for sd in w["soil"]["seeds"]:
        sd["genome"] = {k: round(v, GENOME_DP) for k, v in sd["genome"].items()}
    for sp in w["species"].values():
        sp["archetype"] = {k: round(v, GENOME_DP)
                           for k, v in sp["archetype"].items()}


def _encode_genomes(w):
    """Pack genome dicts into ordered lists. Masses keep full precision, so
    the mass ledger survives a save/load round trip exactly."""
    for o in w["organisms"]:
        o["genome"] = [o["genome"][t] for t in TRAIT_ORDER[o["kind"]]]
    order = TRAIT_ORDER["flora"]
    for sd in w["soil"]["seeds"]:
        sd["genome"] = [sd["genome"][t] for t in order]


def _decode_genomes(w):
    if w.get("codec") != CODEC:
        return
    for o in w["organisms"]:
        g = o["genome"]
        if isinstance(g, list):
            o["genome"] = dict(zip(TRAIT_ORDER[o["kind"]], g))
    order = TRAIT_ORDER["flora"]
    for sd in w["soil"]["seeds"]:
        g = sd["genome"]
        if isinstance(g, list):
            sd["genome"] = dict(zip(order, g))


def save(w):
    os.makedirs(STATE, exist_ok=True)
    _round_genomes(w)
    history = w.pop("history", [])
    chronicle = w.pop("chronicle", [])
    w["codec"] = CODEC
    _encode_genomes(w)
    try:
        _dump(WORLD, w)
        _dump(HISTORY, history)
        _dump(CHRONICLE, chronicle)
        _write_chronicle_md(w, chronicle)
    finally:
        _decode_genomes(w)
        w["history"] = history
        w["chronicle"] = chronicle


def _dump(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"))
        fh.write("\n")
    os.replace(tmp, path)


def load():
    with open(WORLD, encoding="utf-8") as fh:
        w = json.load(fh)
    _decode_genomes(w)
    w["history"] = _load_or(HISTORY, [])
    w["chronicle"] = _load_or(CHRONICLE, [])
    return w


def _load_or(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


# --- summaries --------------------------------------------------------------

def summarise(w):
    counts = sim.population(w)
    bio = {k: 0.0 for k in sim.KINDS}
    ages = {k: 0 for k in sim.KINDS}
    for o in w["organisms"]:
        bio[o["kind"]] += o["c"]
        ages[o["kind"]] = max(ages[o["kind"]], o["age"])
    soil = w["soil"]
    air = w["air"]
    size = w["size"]["w"]
    living = sim.living_species(w)
    species = []
    for sid, n in sorted(living.items(), key=lambda kv: -kv[1]):
        sp = w["species"][sid]
        members = [o for o in w["organisms"] if o["species"] == sid]
        traits = {}
        if members:
            keys = members[0]["genome"].keys()
            traits = {k: round(sum(m["genome"][k] for m in members) / len(members), 3)
                      for k in keys}
        species.append({
            "id": sid, "name": sp["name"], "kind": sp["kind"],
            "count": n, "founded": sp["founded"], "parent": sp.get("parent"),
            "traits": traits,
            "biomass": round(sum(m["c"] for m in members), 2),
        })

    tallest = max((flora_height(o) for o in w["organisms"]
                   if o["kind"] == "flora"), default=0.0)

    return {
        "tick": w["tick"],
        "seed": w["seed"],
        "day": w["tick"] // DAY,
        "year": w["tick"] // YEAR + 1,
        "season": season_name(w["tick"]),
        "hour": w["tick"] % DAY,
        "temp": round(air["temp"], 2),
        "population": counts,
        "biomass": {k: round(v, 1) for k, v in bio.items()},
        "oldest": ages,
        "tallest_flora": round(tallest, 2),
        "air": {"co2": round(air["co2"], 1),
                "humidity": round(air["humidity"], 1),
                "cloud": round(sum(air["cloud"]), 1)},
        "soil": {"water": round(sum(soil["water"]), 1),
                 "saturation": round(sum(soil["water"]) / (SOIL_CAP * size), 3),
                 "mineral_n": round(sum(soil["mineral_n"]), 2),
                 "detritus_c": round(sum(soil["detritus_c"]), 1)},
        "species_alive": len(living),
        "species_total": len(w["species"]),
        "recolonisations": w["flags"].get("reseeds", 0),
        "species": species,
        "ledger": ledger_report(w),
        "updated": datetime.datetime.now(datetime.timezone.utc)
                   .strftime("%Y-%m-%d %H:%M UTC"),
    }


# --- publishing -------------------------------------------------------------

def publish(w):
    os.makedirs(FRAMES, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)

    latest = os.path.join(DOCS, "frame.png")
    meta = rnd.render(w, latest)

    if w["tick"] % FRAME_EVERY == 0:
        shutil.copyfile(latest, os.path.join(FRAMES, "f-%08d.png" % w["tick"]))

    frames = sorted(f for f in os.listdir(FRAMES) if f.endswith(".png"))
    for stale in frames[:-FRAME_KEEP]:
        os.remove(os.path.join(FRAMES, stale))
    frames = sorted(f for f in os.listdir(FRAMES) if f.endswith(".png"))

    summary = summarise(w)
    summary["frame"] = meta
    _dump(os.path.join(DATA, "summary.json"), summary)
    _dump(os.path.join(DATA, "frames.json"),
          [{"file": f, "tick": int(f[2:-4])} for f in frames])
    _dump(os.path.join(DATA, "history.json"), w["history"][-1500:])
    _dump(os.path.join(DATA, "chronicle.json"), w["chronicle"][-120:])
    update_readme(summary)
    return summary


def update_readme(s):
    if not os.path.exists(README):
        return
    with open(README, encoding="utf-8") as fh:
        text = fh.read()

    pop = s["population"]
    bio = s["biomass"]
    rows = [
        "| | |",
        "|---|---|",
        "| **Tick** | %d (day %d, hour %02d of year %d, %s) |"
        % (s["tick"], s["day"], s["hour"], s["year"], s["season"]),
        "| **Temperature** | %.1f C |" % s["temp"],
        "| **Flora** | %d (%.0f C units, tallest %.1f tiles) |"
        % (pop["flora"], bio["flora"], s["tallest_flora"]),
        "| **Grazers** | %d (%.0f C units) |" % (pop["grazer"], bio["grazer"]),
        "| **Predators** | %d (%.0f C units) |" % (pop["predator"], bio["predator"]),
        "| **Decomposers** | %d (%.0f C units) |"
        % (pop["decomposer"], bio["decomposer"]),
        "| **Atmosphere** | CO2 %.0f, vapour %.0f, cloud %.0f |"
        % (s["air"]["co2"], s["air"]["humidity"], s["air"]["cloud"]),
        "| **Soil** | %.0f%% saturated, mineral N %.0f, litter C %.0f |"
        % (s["soil"]["saturation"] * 100, s["soil"]["mineral_n"],
           s["soil"]["detritus_c"]),
        "| **Lineages** | %d living of %d ever, %d recolonisations |"
        % (s["species_alive"], s["species_total"], s["recolonisations"]),
        "| **Mass ledger** | C %+.2e, N %+.2e, H2O %+.2e drift |"
        % (s["ledger"]["carbon"]["drift"], s["ledger"]["nitrogen"]["drift"],
           s["ledger"]["water"]["drift"]),
        "| **Updated** | %s |" % s["updated"],
    ]
    block = "\n".join(rows)

    text = _replace_block(text, "STATUS", block)

    top = sorted(s["species"], key=lambda sp: -sp["count"])[:8]
    if top:
        srows = ["| Lineage | Guild | Count | Biomass | Founded (tick) |",
                 "|---|---|---|---|---|"]
        for sp in top:
            srows.append("| %s | %s | %d | %.1f | %d |"
                         % (sp["name"], sp["kind"], sp["count"],
                            sp["biomass"], sp["founded"]))
        text = _replace_block(text, "LINEAGES", "\n".join(srows))

    with open(README, "w", encoding="utf-8") as fh:
        fh.write(text)


def _replace_block(text, tag, body):
    start = "<!-- %s:start -->" % tag
    end = "<!-- %s:end -->" % tag
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    return pattern.sub(start + "\n" + body + "\n" + end, text)


def _write_chronicle_md(w, chronicle):
    lines = ["# Chronicle",
             "",
             "Notable events, newest first. Tick %d, year %d."
             % (w["tick"], w["tick"] // YEAR + 1),
             ""]
    for e in reversed(chronicle[-200:]):
        lines.append("- **t%d** (%s) %s" % (e["tick"], e["type"], e["text"]))
    lines.append("")
    with open(CHRONICLE_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# --- commands ---------------------------------------------------------------

def cmd_new(args):
    w = sim.create(args.seed)
    save(w)
    s = publish(w)
    print("seeded world %d: %s" % (w["seed"], s["population"]))


def cmd_tick(args):
    w = load()
    start = w["tick"]
    for _ in range(args.ticks):
        sim.step(w)
    save(w)
    s = publish(w)
    print("ticks %d -> %d | pop %s | species %d | drift C %+.2e"
          % (start, w["tick"], s["population"], s["species_alive"],
             s["ledger"]["carbon"]["drift"]))


def cmd_render(args):
    w = load()
    s = publish(w)
    print("rendered tick %d (%dx%d)" % (w["tick"], s["frame"]["width"],
                                        s["frame"]["height"]))


def cmd_report(args):
    w = load()
    print(json.dumps(summarise(w), indent=2)[:4000])


def cmd_audit(args):
    w = sim.create(args.seed)
    before = ledger_report(w)
    for _ in range(args.ticks):
        sim.step(w)
    after = ledger_report(w)
    print("ticks:", args.ticks, "pop:", sim.population(w))
    for el in ("carbon", "nitrogen", "water"):
        print("  %-9s start %12.4f  end %12.4f  drift %+.3e"
              % (el, before[el]["total"], after[el]["total"],
                 after[el]["total"] - before[el]["total"]))


def main(argv=None):
    p = argparse.ArgumentParser(prog="terrarium")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="create a new world")
    n.add_argument("--seed", type=int, default=None)
    n.set_defaults(func=cmd_new)

    t = sub.add_parser("tick", help="advance the world")
    t.add_argument("--ticks", type=int, default=12)
    t.set_defaults(func=cmd_tick)

    r = sub.add_parser("render", help="re-render without advancing")
    r.set_defaults(func=cmd_render)

    rp = sub.add_parser("report", help="print a status summary")
    rp.set_defaults(func=cmd_report)

    a = sub.add_parser("audit", help="in-memory conservation check")
    a.add_argument("--ticks", type=int, default=500)
    a.add_argument("--seed", type=int, default=7)
    a.set_defaults(func=cmd_audit)

    args = p.parse_args(argv)
    return args.func(args)
