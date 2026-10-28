"""Invariants the terrarium must hold for every run.

Run with:  python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from terrarium import genome as gm           # noqa: E402
from terrarium import life, render, sim      # noqa: E402
from terrarium.world import (TOTAL_CARBON, TOTAL_NITROGEN, TOTAL_WATER,
                             ledger_totals)  # noqa: E402

TOL = 1e-6


class TestConservation(unittest.TestCase):
    """Carbon, nitrogen and water are closed. Nothing may leak."""

    def test_initial_budget_matches_constants(self):
        w = sim.create(11)
        t = ledger_totals(w)
        self.assertAlmostEqual(t["carbon"], TOTAL_CARBON, delta=TOL)
        self.assertAlmostEqual(t["nitrogen"], TOTAL_NITROGEN, delta=TOL)
        self.assertAlmostEqual(t["water"], TOTAL_WATER, delta=TOL)

    def test_closed_over_a_long_run(self):
        w = sim.create(3)
        before = ledger_totals(w)
        for _ in range(600):
            sim.step(w)
        after = ledger_totals(w)
        for element in ("carbon", "nitrogen", "water"):
            drift = abs(after[element] - before[element])
            self.assertLess(drift, 1e-5,
                            "%s drifted by %.3e" % (element, drift))

    def test_no_negative_pools(self):
        w = sim.create(5)
        for _ in range(400):
            sim.step(w)
        soil = w["soil"]
        for name in ("water", "mineral_n", "detritus_c", "detritus_n"):
            self.assertGreaterEqual(min(soil[name]), -TOL, name)
        self.assertGreaterEqual(w["air"]["co2"], -TOL)
        self.assertGreaterEqual(w["air"]["humidity"], -TOL)
        for o in w["organisms"]:
            self.assertGreaterEqual(o["c"], -TOL)
            self.assertGreaterEqual(o["n"], -TOL)
            self.assertGreaterEqual(o["w"], -TOL)


class TestDeterminism(unittest.TestCase):
    """The same seed and tick count must reproduce the same world."""

    def test_two_runs_agree(self):
        a = sim.create(42)
        b = sim.create(42)
        for _ in range(200):
            sim.step(a)
            sim.step(b)
        self.assertEqual(sim.population(a), sim.population(b))
        self.assertEqual(a["next_id"], b["next_id"])
        self.assertAlmostEqual(a["air"]["co2"], b["air"]["co2"], delta=1e-9)
        self.assertAlmostEqual(sum(a["soil"]["water"]),
                               sum(b["soil"]["water"]), delta=1e-9)


class TestEcology(unittest.TestCase):

    def test_life_persists_for_several_years(self):
        w = sim.create(7)
        for _ in range(5000):
            sim.step(w)
        counts = sim.population(w)
        self.assertGreater(counts["flora"], 0, "producers died out")
        self.assertGreater(counts["decomposer"], 0, "decomposers died out")
        self.assertGreater(sum(counts.values()), 20)

    def test_seed_bank_restores_flora_without_recolonisation(self):
        """A total standing-crop wipeout must be survivable from the bank."""
        w = sim.create(13)
        for _ in range(2400):
            sim.step(w)
            if len(w["soil"]["seeds"]) > 40:
                break
        self.assertGreater(len(w["soil"]["seeds"]), 40,
                           "no seed bank accumulated")

        for o in list(w["organisms"]):
            if o["kind"] == "flora":
                life.kill(w, o, "test")
        w["organisms"] = [o for o in w["organisms"] if o["state"] != "dead"]
        self.assertEqual(sim.population(w)["flora"], 0)

        flora_reseed_before = w["flags"].get("last_reseed", {}).get("flora")
        for _ in range(600):
            sim.step(w)
        self.assertGreater(sim.population(w)["flora"], 0,
                           "seed bank failed to restore flora")
        self.assertEqual(w["flags"].get("last_reseed", {}).get("flora"),
                         flora_reseed_before,
                         "flora recovery relied on recolonisation, not the bank")

    def test_decomposers_drive_litter_breakdown(self):
        """A decomposer-free copy of the same world must retain more litter.

        Two worlds are forked from one state; decomposers are removed from one
        and recolonisation blocked. Both then run the same number of ticks on
        the same tick-seeded RNG, so the difference isolates the guild.
        """
        import copy

        w = sim.create(17)
        for _ in range(700):
            sim.step(w)
        self.assertGreater(sim.population(w)["decomposer"], 0)

        sterile = copy.deepcopy(w)
        for o in list(sterile["organisms"]):
            if o["kind"] == "decomposer":
                life.kill(sterile, o, "test")
        sterile["organisms"] = [o for o in sterile["organisms"]
                                if o["state"] != "dead"]
        sterile["flags"]["last_reseed"] = {"decomposer": 10 ** 9}

        for _ in range(500):
            sim.step(w)
            sim.step(sterile)

        self.assertEqual(sim.population(sterile)["decomposer"], 0,
                         "recolonisation was not blocked")
        self.assertGreater(sum(sterile["soil"]["detritus_c"]),
                           sum(w["soil"]["detritus_c"]),
                           "litter should persist longer without decomposers")


class TestGenome(unittest.TestCase):

    def test_traits_stay_inside_their_domain(self):
        import random
        rng = random.Random(1)
        for kind, spec in gm.TRAIT_SPECS.items():
            g = gm.seed_genome(kind, rng)
            for _ in range(200):
                g = gm.mutate(kind, g, rng)
            for trait, (lo, hi, _m, _s) in spec.items():
                self.assertGreaterEqual(g[trait], lo, "%s.%s" % (kind, trait))
                self.assertLessEqual(g[trait], hi, "%s.%s" % (kind, trait))

    def test_distance_is_zero_for_identical_genomes(self):
        import random
        rng = random.Random(2)
        g = gm.seed_genome("flora", rng)
        self.assertAlmostEqual(gm.distance("flora", g, dict(g)), 0.0)
        self.assertGreater(gm.distance("flora", g, gm.seed_genome("flora", rng)), 0.0)


class TestRenderer(unittest.TestCase):

    def test_writes_a_valid_png(self):
        w = sim.create(23)
        for _ in range(60):
            sim.step(w)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "frame.png")
            meta = render.render(w, path)
            with open(path, "rb") as fh:
                head = fh.read(8)
            self.assertEqual(head, b"\x89PNG\r\n\x1a\n")
            self.assertGreater(os.path.getsize(path), 500)
            self.assertLessEqual(meta["colours"], 256)
            self.assertEqual(meta["width"],
                             w["size"]["w"] * render.TILE * render.SCALE)


if __name__ == "__main__":
    unittest.main()
