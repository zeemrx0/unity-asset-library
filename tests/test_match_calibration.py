#!/usr/bin/env python3
"""Regression gate for store-name matching.

The fixture is built by scripts/build-match-calibration.py from verified matches.
Positives must keep matching except a short, reviewed list of known gaps;
confusable negatives (other products' names) must never match. This guards
regressions; it does not measure accuracy on unseen titles.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
os.environ.setdefault("UAL_APP_DATA", tempfile.mkdtemp(prefix="ual-test-app-data-"))

import resolve_store as rs  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "match_calibration.json")

# Verified matches the symmetric gate now rejects. Each carries an edition word or
# an extra word on one side only; a saved match (matches.json) or a package header
# keeps them resolved, so they never return to web search.
KNOWN_STRICTER = {
    ("AAR Action RPG SFX Pack", "Action RPG SFX Pack v2"),
    ("CUBE - Planet Hue Pack (URP)", "CUBE - Planet Hue Pack"),
    ("HDRPBuilt-inURP Medieval Fantasy Ruins - Dark Forest Environment",
     "Medieval Fantasy Ruins - Dark Forest Environment"),
    ("Modular Dungeon Catacombs - Mobile", "Modular Dungeon - Catacombs"),
    ("Modular Fantasy Stylized Human Male", "Stylized Modular Human Male"),
}


def load():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def accepted(row):
    return rs.verify_identity(row["local"], row["store"], publisher=row.get("publisher"))[0]


def newly_accepted():
    """Unverified candidate pairs the current gate accepts (for human review)."""
    return [row for row in load()["unverified"] if accepted(row)]


class MatchCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load()

    def test_fixture_size(self):
        self.assertGreaterEqual(len(self.data["positives"]), 700)
        self.assertGreaterEqual(len(self.data["negatives"]), 1500)

    def test_positives_accepted(self):
        rejected = {(row["local"], row["store"]) for row in self.data["positives"]
                    if not accepted(row)}
        self.assertEqual(rejected - KNOWN_STRICTER, set())

    def test_known_stricter_list_is_current(self):
        positives = {(row["local"], row["store"]) for row in self.data["positives"]}
        self.assertLessEqual(KNOWN_STRICTER, positives)

    def test_negatives_never_accepted(self):
        wrong = [(row["local"], row["store"]) for row in self.data["negatives"]
                 if rs.verify_identity(row["local"], row["store"])[0]]
        self.assertEqual(wrong, [], f"{len(wrong)} confusable pairs accepted: {wrong[:20]}")


if __name__ == "__main__":
    unittest.main()
