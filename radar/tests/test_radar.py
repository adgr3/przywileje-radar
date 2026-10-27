"""Testy Radaru DU — offline (fixtures), bez sieci.

python -m unittest discover -s radar/tests -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RADAR = HERE.parent
sys.path.insert(0, str(RADAR))
import rules  # noqa: E402
import radar  # noqa: E402
from eli import ELIClient, ELIError  # noqa: E402

FIX = HERE / "fixtures"

AMENDMENT_TEXT = """
Art. 1. W ustawie z dnia 6 kwietnia 1990 r. o Policji wprowadza się następujące zmiany:
1) w art. 92 ust. 1 otrzymuje brzmienie:
„Art. 92. 1. Policjantowi przysługuje świadczenie mieszkaniowe w wysokości 1900 zł miesięcznie, jeżeli nie posiada lokalu.”;
Art. 2. 1. Policjantowi, który przed dniem wejścia w życie niniejszej ustawy nabył prawo do świadczenia, stosuje się przepisy dotychczasowe.
Art. 3. Dodatek za wieloletnią służbę przysługuje również funkcjonariuszom Służby Więziennej po 5 latach służby.
Art. 4. Dopłaty do składek na ubezpieczenia społeczne finansowane są z budżetu państwa.
Art. 5. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia.
"""


def write_fixture_set(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    listing = {"items": [
        {"ELI": "DU/2026/9001", "promulgation": "2026-10-10", "changeDate": "2026-10-10T10:00:00", "title": "Ustawa z dnia 1 października 2026 r. o zmianie ustawy o Policji oraz niektórych innych ustaw", "type": "Ustawa", "displayAddress": "Dz.U. 2026 poz. 9001"},
        {"ELI": "DU/2026/9002", "promulgation": "2026-10-11", "changeDate": "2026-10-11T10:00:00", "title": "Obwieszczenie Marszałka Sejmu w sprawie ogłoszenia jednolitego tekstu ustawy o służbie cywilnej", "type": "Obwieszczenie", "displayAddress": "Dz.U. 2026 poz. 9002"},
        {"ELI": "DU/2026/9003", "promulgation": "2026-10-12", "changeDate": "2026-10-12T10:00:00", "title": "Rozporządzenie Ministra Rolnictwa w sprawie znakowania jaj", "type": "Rozporządzenie", "displayAddress": "Dz.U. 2026 poz. 9003"},
        {"ELI": "DU/2026/9004", "promulgation": "2026-09-01", "changeDate": "2026-09-01T10:00:00", "title": "Ustawa poza oknem", "type": "Ustawa", "displayAddress": "Dz.U. 2026 poz. 9004"},
    ]}
    (d / "acts_DU_2026").write_text(json.dumps(listing), encoding="utf-8")
    (d / "acts_DU_2026_9001").write_text(json.dumps({**listing["items"][0], "entryIntoForce": "2026-10-25",
        "references": {"Akty zmienione": [{"id": "DU/1990/179", "date": "2026-10-25"}]}}), encoding="utf-8")
    (d / "acts_DU_2026_9002").write_text(json.dumps({**listing["items"][1], "references": {"Tekst jednolity dla aktu": [{"id": "DU/2008/1505"}]}}), encoding="utf-8")
    (d / "acts_DU_2026_9003").write_text(json.dumps(listing["items"][2]), encoding="utf-8")
    (d / "acts_DU_2026_9001_text.html").write_text("<html><body><pre>" + AMENDMENT_TEXT + "</pre></body></html>", encoding="utf-8")
    # akty z katalogu → akt pierwotny
    (d / "acts_DU_2025_9999").write_text(json.dumps({"ELI": "DU/2025/9999", "references": {"Tekst jednolity dla aktu": [{"id": "DU/1990/179"}]}}), encoding="utf-8")
    (d / "acts_DU_2026_590").write_text(json.dumps({"ELI": "DU/2026/590", "references": {"Tekst jednolity dla aktu": [{"id": "DU/2008/1505"}]}}), encoding="utf-8")
    (d / "acts_DU_1990_179").write_text(json.dumps({"ELI": "DU/1990/179", "inForce": "IN_FORCE", "references": {"Akty zmieniające": [{"id": "DU/2026/9001", "date": "2026-10-25"}, {"id": "DU/2020/1", "date": "2020-01-01"}]}}), encoding="utf-8")
    (d / "acts_DU_2008_1505").write_text(json.dumps({"ELI": "DU/2008/1505", "inForce": "IN_FORCE", "references": {"Akty zmieniające": [{"id": "DU/2019/5", "date": "2019-05-01"}]}}), encoding="utf-8")


def mini_catalog(p: Path):
    cat = {"claims": [
        {"claim_id": "T-POL-1", "group_id": "policja", "also_groups": [], "category": "mieszkania", "display": "primary", "checked_on": "2026-10-01",
         "legal_basis": [{"eli": "DU/2025/9999", "text_state_date": "2026-01-01"}]},
        {"claim_id": "T-SC-1", "group_id": "sluzba_cywilna", "also_groups": [], "category": "dodatki", "display": "primary", "checked_on": "2026-10-01",
         "legal_basis": [{"eli": "DU/2026/590", "text_state_date": "2026-04-14"}]},
        {"claim_id": "T-SW-0", "group_id": "nauczyciel", "also_groups": [], "category": "dodatki", "display": "primary", "checked_on": "2026-10-01",
         "legal_basis": [{"eli": None}]},
    ]}
    p.write_text(json.dumps(cat), encoding="utf-8")


class RulesTest(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(rules.classify_act({"title": "Ustawa z dnia 1 r. o zmianie ustawy o Policji", "type": "Ustawa"}), "NOWELIZACJA_USTAWY")
        self.assertEqual(rules.classify_act({"title": "Ustawa z dnia 8 października 2026 r. o zabezpieczeniu społecznym osób wykonujących zawód artystyczny", "type": "Ustawa"}), "NOWA_USTAWA")
        self.assertEqual(rules.classify_act({"title": "Obwieszczenie ... w sprawie ogłoszenia jednolitego tekstu ustawy", "type": "Obwieszczenie"}), "TEKST_JEDNOLITY")
        self.assertEqual(rules.classify_act({"title": "Rozporządzenie ... zmieniające rozporządzenie w sprawie", "type": "Rozporządzenie"}), "ZMIANA_ROZPORZADZENIA")
        self.assertEqual(rules.classify_act({"title": "Rozporządzenie ... w sprawie stawek", "type": "Rozporządzenie"}), "ROZPORZADZENIE_WYKONAWCZE")

    def test_frames(self):
        found = rules.analyse_text(AMENDMENT_TEXT)
        kinds = {k for f in found for k, v in f["frame"].items() if v is True}
        self.assertIn("grants", kinds)
        self.assertIn("transitional", kinds)
        self.assertIn("extends_group", kinds)
        self.assertIn("funding", kinds)
        amounts = [a for f in found for a in f["frame"]["amounts"]]
        self.assertTrue(any("1900" in a for a in amounts), amounts)

    def test_negation_marked(self):
        f = rules.frame("Dodatek nie przysługuje policjantowi zawieszonemu w czynnościach służbowych.")
        self.assertTrue(f["negates"])


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fix = self.tmp / "fx"
        write_fixture_set(self.fix)
        self.cat = self.tmp / "catalog.json"
        mini_catalog(self.cat)
        self.args = ["run", "--since", "2026-10-01", "--until", "2026-10-25", "--offline", "--fixtures", str(self.fix),
                     "--state", str(self.tmp / "state"), "--queue", str(self.tmp / "queue"), "--catalog", str(self.cat)]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def queue(self):
        return radar.jl_read(self.tmp / "queue" / "HYPOTHESES.jsonl")

    def test_run_classifies_and_links(self):
        radar.main(self.args)
        acts = {a["eli"]: a for a in radar.jl_read(self.tmp / "state" / "acts.jsonl")}
        self.assertEqual(set(acts), {"DU/2026/9001", "DU/2026/9002", "DU/2026/9003"})  # 9004 poza oknem
        self.assertTrue(acts["DU/2026/9001"]["relevant"])
        self.assertEqual(acts["DU/2026/9001"]["change_kind_code"], "NOWELIZACJA_USTAWY")
        self.assertEqual(acts["DU/2026/9001"]["catalog_acts_hit"], ["DU/1990/179"])
        self.assertEqual(acts["DU/2026/9002"]["change_kind_code"], "TEKST_JEDNOLITY")
        self.assertFalse(acts["DU/2026/9003"]["relevant"])
        self.assertIsNotNone(acts["DU/2026/9001"]["text_sha256"])
        types = {h["type"] for h in self.queue()}
        for t in ("AMENDS_CATALOG_ACT", "NEW_CONSOLIDATED_TEXT", "TRANSITIONAL_COHORT", "GROUP_EXTENSION", "NEW_FUNDING", "AMOUNT_OR_FORMULA"):
            self.assertIn(t, types)
        amend = [h for h in self.queue() if h["type"] == "AMENDS_CATALOG_ACT"][0]
        self.assertEqual(amend["affected_claims"], ["T-POL-1"])

    def test_every_hypothesis_has_evidence_counter_and_review_status(self):
        radar.main(self.args)
        for h in self.queue():
            self.assertTrue(h["evidence"], h)
            self.assertTrue(h["counter_evidence"], h)
            self.assertEqual(h["status"], "DO_PRZEGLĄDU")
            self.assertEqual(h["legal_review"], "UNREVIEWED")
            if "evidence_sha256" in h:
                self.assertEqual(len(h["evidence_sha256"]), 64)

    def test_idempotent_rerun(self):
        radar.main(self.args)
        first = self.queue()
        acts1 = radar.jl_read(self.tmp / "state" / "acts.jsonl")
        radar.main(self.args)
        second = self.queue()
        self.assertEqual(len(first), len(second))
        self.assertEqual({h["hypothesis_id"] for h in first}, {h["hypothesis_id"] for h in second})
        self.assertEqual(len(acts1), len(radar.jl_read(self.tmp / "state" / "acts.jsonl")))
        runs = radar.jl_read(self.tmp / "state" / "runs.jsonl")
        self.assertEqual(runs[-1]["acts_new"], 0)
        self.assertEqual(runs[-1]["hypotheses_added"], 0)

    def test_review_status_preserved_on_rerun(self):
        radar.main(self.args)
        q = self.queue()
        q[0]["status"] = "ODRZUCONA_PRZEZ_REDAKCJĘ"
        radar.jl_write(self.tmp / "queue" / "HYPOTHESES.jsonl", q, "hypothesis_id")
        # wymuś ponowne przetworzenie aktu (zmiana changeDate)
        acts = radar.jl_read(self.tmp / "state" / "acts.jsonl")
        for a in acts:
            a["change_date"] = "stary"
        radar.jl_write(self.tmp / "state" / "acts.jsonl", acts, "eli")
        radar.main(self.args)
        q2 = {h["hypothesis_id"]: h for h in self.queue()}
        self.assertEqual(q2[q[0]["hypothesis_id"]]["status"], "ODRZUCONA_PRZEZ_REDAKCJĘ")

    def test_network_error_is_reported_not_fatal(self):
        (self.fix / "acts_DU_2026_9001_text.html").unlink()
        rc = radar.main(self.args)
        self.assertEqual(rc, 0)
        runs = radar.jl_read(self.tmp / "state" / "runs.jsonl")
        self.assertTrue(any("9001" in e for e in runs[-1]["errors"]), runs[-1]["errors"])
        # relacja ELI nadal daje hipotezę AMENDS_CATALOG_ACT mimo braku tekstu
        self.assertIn("AMENDS_CATALOG_ACT", {h["type"] for h in self.queue()})

    def test_offline_without_cache_raises_eli_error(self):
        c = ELIClient(self.tmp / "emptycache", offline=True)
        with self.assertRaises(ELIError):
            c.act("DU/2026/1")

    def test_currency_flags_outdated_claim(self):
        client = ELIClient(self.tmp / "c", offline=True, fixtures_dir=self.fix)
        cat = json.loads(self.cat.read_text(encoding="utf-8"))
        res = radar.currency_check(client, cat, {})
        by = {r["claim_id"]: r for r in res}
        self.assertEqual(by["T-POL-1"]["status"], "POSSIBLY_OUTDATED")
        self.assertEqual(by["T-POL-1"]["later_amendments"][0]["eli"], "DU/2026/9001")
        self.assertEqual(by["T-SC-1"]["status"], "NO_LATER_AMENDMENT_FOUND")


if __name__ == "__main__":
    unittest.main()
