"""Regresje parsera aktów zmieniających (radar/amendments.py) — błędy wykryte w Mission #022."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import amendments as am  # noqa: E402

TEXT = """
      Art. 1. W ustawie z dnia 30 października 2002 r. o ubezpieczeniu społecznym z tytułu wypadków przy pracy i chorób
zawodowych art. 16 otrzymuje brzmienie:
„Art. 16. Orzeczenie wydaje lekarz orzecznik na zasadach określonych przepisami o systemie ubezpieczeń społecznych.”.
      Art. 2. W ustawie z dnia 13 października 1998 r. o systemie ubezpieczeń społecznych wprowadza się następujące zmiany:
1) w art. 18 po ust. 1a dodaje się ust. 1b w brzmieniu:
„1b. Zob. art. 99 i art. 98 otrzymuje brzmienie wyłącznie w cytacie.”;
2) uchyla się
art. 40;
substancjami.”. Art. 3. W ustawie z dnia 6 kwietnia 1990 r. o Policji po art. 15g dodaje się
art. 15h w brzmieniu:
„Art. 15h. Treść.”.
"""


class AmendedArticles(unittest.TestCase):
    def test_reference_to_act_is_not_a_header(self):
        # art. 1 tylko odsyła do „systemu ubezpieczeń społecznych” — nie może być uznany za jego nowelizację
        r = am.amended_articles(TEXT, "o systemie ubezpieczeń społecznych")
        self.assertNotIn("16", r["articles"])
        self.assertIn("18", r["articles"])

    def test_line_breaks_inside_phrases(self):
        r = am.amended_articles(TEXT, "o systemie ubezpieczeń społecznych")
        self.assertIn("40", r["articles"])  # „uchyla się\\nart. 40”

    def test_quoted_new_wording_ignored(self):
        r = am.amended_articles(TEXT, "o systemie ubezpieczeń społecznych")
        self.assertNotIn("99", r["articles"])
        self.assertNotIn("98", r["articles"])

    def test_article_heading_in_middle_of_line(self):
        r = am.amended_articles(TEXT, "o Policji")
        self.assertTrue(r["found"])
        self.assertIn("15g", r["articles"])
        self.assertIn("15h", r["articles"])

    def test_title_helpers(self):
        self.assertEqual(am.base_title_from_meta("Ustawa z dnia 6 kwietnia 1990 r. o Policji."), "o Policji")
        self.assertEqual(am.cited_articles("art. 18 ust. 8; art. 8 ust. 2a"), {"18", "8"})


if __name__ == "__main__":
    unittest.main()
