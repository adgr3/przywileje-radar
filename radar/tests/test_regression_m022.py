"""Regresje radaru dodane w Mission #022: brak publikacji, determinizm, ponowne wiązanie po zmianie katalogu."""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import radar  # noqa: E402
from test_radar import mini_catalog, write_fixture_set  # noqa: E402

ROOT = HERE.parents[1]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "-"


class RadarRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        write_fixture_set(self.tmp / "fx")
        mini_catalog(self.tmp / "catalog.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def args(self, tag, extra=()):
        return ["run", "--since", "2026-10-01", "--until", "2026-10-25", "--offline", "--fixtures", str(self.tmp / "fx"),
                "--state", str(self.tmp / tag / "state"), "--queue", str(self.tmp / tag / "queue"),
                "--catalog", str(self.tmp / "catalog.json"), *extra]

    def test_run_never_writes_catalog_or_site(self):
        watched = [self.tmp / "catalog.json", ROOT / "site" / "data" / "catalog.json", ROOT / "site" / "data" / "catalog.js",
                   ROOT / "records.json"]
        before = [sha(p) for p in watched]
        radar.main(self.args("a"))
        self.assertEqual(before, [sha(p) for p in watched])
        for h in radar.jl_read(self.tmp / "a" / "queue" / "HYPOTHESES.jsonl"):
            self.assertEqual(h["status"], "DO_PRZEGLĄDU")
            self.assertEqual(h["legal_review"], "UNREVIEWED")

    def test_deterministic_hypotheses_across_fresh_runs(self):
        radar.main(self.args("a"))
        radar.main(self.args("b"))
        qa = {h["hypothesis_id"]: h["type"] for h in radar.jl_read(self.tmp / "a" / "queue" / "HYPOTHESES.jsonl")}
        qb = {h["hypothesis_id"]: h["type"] for h in radar.jl_read(self.tmp / "b" / "queue" / "HYPOTHESES.jsonl")}
        self.assertEqual(qa, qb)
        self.assertTrue(qa)

    def test_catalog_change_relinks_without_duplicates(self):
        radar.main(self.args("a"))
        n1 = len(radar.jl_read(self.tmp / "a" / "queue" / "HYPOTHESES.jsonl"))
        cat = json.loads((self.tmp / "catalog.json").read_text(encoding="utf-8"))
        cat["claims"].append({"claim_id": "T-POL-2", "group_id": "policja", "also_groups": [], "category": "dodatki", "display": "primary",
                              "checked_on": "2026-10-01", "legal_basis": [{"eli": "DU/2025/9999", "text_state_date": "2026-01-01"}]})
        (self.tmp / "catalog.json").write_text(json.dumps(cat), encoding="utf-8")
        radar.main(self.args("a"))
        q = radar.jl_read(self.tmp / "a" / "queue" / "HYPOTHESES.jsonl")
        ids = [h["hypothesis_id"] for h in q]
        self.assertEqual(len(ids), len(set(ids)))
        amend = [h for h in q if h["type"] == "AMENDS_CATALOG_ACT"][0]
        self.assertIn("T-POL-2", amend["affected_claims"])  # nowa karta powiązana po zmianie katalogu
        self.assertGreaterEqual(len(q), n1)

    def test_consolidated_text_not_treated_as_new_law(self):
        radar.main(self.args("a"))
        tj = [h for h in radar.jl_read(self.tmp / "a" / "queue" / "HYPOTHESES.jsonl") if h["act_eli"] == "DU/2026/9002"]
        self.assertEqual({h["type"] for h in tj}, {"NEW_CONSOLIDATED_TEXT"})
        self.assertTrue(any("nie zmienia prawa" in c for h in tj for c in h["counter_evidence"]))


if __name__ == "__main__":
    unittest.main()
