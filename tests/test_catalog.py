"""Tests for catalog resolution — symbol linking, priors, cross-check (M2, layer 3)."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context import create_context
from src.router import (
    CachedProseReader,
    LLMProseReader,
    ProseReader,
    ReadResult,
    resolve_bundle,
    resolve_catalog,
)
from src.router.catalog import _as_text_codebook
from src.tools.base import clear_registry


# What the stub reader "understands": column -> (description, units, verbatim quote).
_FISH_READS = {
    "mass": ("wet body mass", None, "Wet body mass (mass)"),
    "epoc": ("oxygen debt", None, "Oxygen debt (epoc)"),
}


class _PassageReader(ProseReader):
    """A reader that returns a column's meaning only when its defining quote is in the
    chunk — a model that reads what it is handed and nothing else, with no network."""

    def __init__(self, reads=None):
        self.reads = reads if reads is not None else _FISH_READS

    def read(self, *, column, dtype, chunk):
        known = self.reads.get(column)
        if known and known[2] in chunk:
            return ReadResult(known[0], known[1], "medium", known[2])
        return None


class _CountingReader(_PassageReader):
    """A passage reader that records every batched call, to prove call shape."""

    def __init__(self, reads=None):
        super().__init__(reads)
        self.calls = []  # one (column_names, chunk_text) per read_many invocation

    def read_many(self, *, columns, chunk):
        self.calls.append(([name for name, _ in columns], chunk))
        return super().read_many(columns=columns, chunk=chunk)


class _StubLLM:
    """A stub for LLMProseReader's ``invoke`` — returns a fixed mapping as JSON and
    counts calls. Simulates a model that read the passage, with no network."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        return json.dumps(self.mapping)


class CatalogResolutionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # Opaque-named observation table.
        pd.DataFrame(
            {
                "la": [53.1, 53.2, 53.3],       # latitude (float, [-90,90])
                "lo": [-9.1, -9.2, -9.3],       # longitude (float)
                "dt": ["2020-01-01", "2020-06-01", "2021-01-01"],
                "n": [1, 2, 3],                 # integer count
                "tmp": [4.5, 10.2, 21.0],       # temperature °C
            }
        ).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        # A structured data dictionary keyed by column name; tmp's units lie.
        pd.DataFrame(
            {
                "variable": ["la", "lo", "dt", "n", "tmp"],
                "label": [
                    "Latitude of the point",
                    "Longitude of the point",
                    "Date of observation",
                    "Count of individuals",
                    "Air temperature",
                ],
                "units": ["decimal degrees", "decimal degrees", "ISO date", "count", "Kelvin"],
            }
        ).to_csv(os.path.join(self.dir, "codebook.csv"), index=False)

        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        self.codebook = create_context(os.path.join(self.dir, "codebook.csv"), name="codebook")

    def tearDown(self):
        clear_registry()

    # --- structured dictionary -------------------------------------------

    def test_structured_dictionary_resolves_opaque_column(self):
        cat = resolve_catalog(self.tab, sources=[self.codebook])
        la = cat.get("la")
        self.assertEqual(la.link_method, "structured_dictionary")
        self.assertEqual(la.link_confidence, "high")
        self.assertIn("Latitude", la.description)
        self.assertIn("la", la.link_evidence)

    def test_units_conflict_is_flagged(self):
        cat = resolve_catalog(self.tab, sources=[self.codebook])
        tmp = cat.get("tmp")
        self.assertTrue(tmp.conflicts)
        self.assertIn("Kelvin", tmp.conflicts[0])
        self.assertIn("tmp:", cat.conflicts[0])

    def test_resolution_closes_the_semantic_gap(self):
        # Before: opaque names defeat the raw search.
        self.assertEqual(self.tab.search("latitude"), [])
        # After: the enriched catalog reaches the column through its description.
        cat = resolve_catalog(self.tab, sources=[self.codebook])
        hits = cat.search("latitude")
        self.assertTrue(hits)
        self.assertEqual(hits[0].locator, "la")

    def test_non_dictionary_tabular_source_is_ignored(self):
        pd.DataFrame({"foo": [1], "bar": [2]}).to_csv(
            os.path.join(self.dir, "other.csv"), index=False
        )
        other = create_context(os.path.join(self.dir, "other.csv"), name="other")
        cat = resolve_catalog(self.tab, sources=[other])
        # No key coverage → not treated as a dictionary → falls back to priors.
        self.assertNotEqual(cat.get("la").link_method, "structured_dictionary")

    # --- value priors (floor) --------------------------------------------

    def test_self_evident_types_resolve_by_value(self):
        """Only coordinate and temporal — the kinds values genuinely identify."""
        cat = resolve_catalog(self.tab)
        self.assertEqual(cat.get("la").link_method, "value_prior")
        self.assertEqual(cat.get("la").value_label, "coordinate")
        self.assertEqual(cat.get("la").link_confidence, "medium")  # ambiguous by value
        self.assertEqual(cat.get("dt").link_method, "value_prior")
        self.assertEqual(cat.get("dt").value_label, "temporal")
        self.assertEqual(cat.get("dt").link_confidence, "high")

    def test_long_tail_numeric_abstains(self):
        """A generic numeric measure has no self-evident meaning → unresolved."""
        pd.DataFrame({"bio": [123.4, 456.7, 789.0]}).to_csv(
            os.path.join(self.dir, "bio.csv"), index=False
        )
        cat = resolve_catalog(create_context(os.path.join(self.dir, "bio.csv"), name="bio"))
        col = cat.get("bio")
        self.assertEqual(col.link_method, "none")     # abstains, not a coordinate
        self.assertIsNone(col.description)
        self.assertEqual(col.value_label, "numeric")  # profile still recorded

    def test_integer_column_is_not_a_coordinate(self):
        cat = resolve_catalog(self.tab)
        self.assertEqual(cat.get("n").value_label, "numeric")
        self.assertEqual(cat.get("n").link_method, "none")  # abstains

    # --- text codebook ----------------------------------------------------

    def test_text_codebook_resolves_a_column(self):
        pd.DataFrame({"qq": [1, 2, 3], "zz": [4, 5, 6]}).to_csv(os.path.join(self.dir, "q.csv"), index=False)
        with open(os.path.join(self.dir, "notes.md"), "w") as f:
            f.write("# Variables\n\n- qq = quality quotient score\n- zz = zone code\n- site = site name\n")
        tab = create_context(os.path.join(self.dir, "q.csv"), name="q")
        doc = create_context(os.path.join(self.dir, "notes.md"), name="notes")
        qq = resolve_catalog(tab, sources=[doc]).get("qq")
        self.assertEqual(qq.link_method, "text_codebook")
        self.assertEqual(qq.link_confidence, "medium")      # below a codebook table's high
        self.assertEqual(qq.description, "quality quotient score")
        self.assertEqual(qq.link_quote, "qq = quality quotient score")
        start, end = (int(x) for x in qq.link_evidence.split("#")[1].split("-"))
        self.assertEqual(doc.read_text("notes")[start:end], qq.link_quote)   # a real span

    # --- name hygiene: stray whitespace in a header ----------------------

    def test_trailing_space_in_header_still_resolves_via_text_codebook(self):
        """A real header like 'Nitrate ' (trailing space) must match the glossary
        definition of 'nitrate' — matching strips the name, the locator keeps it."""
        pd.DataFrame({"Nitrate ": [1.0, 2.0, 3.0], "tank": [1, 2, 3], "pH": [7.0, 7.5, 8.0]}).to_csv(
            os.path.join(self.dir, "epoc.csv"), index=False
        )
        with open(os.path.join(self.dir, "readme.txt"), "w") as f:
            f.write(
                "pH – acclimation pH; nitrate – nominal nitrate treatment concentration (mg/L); "
                "tank – replicate tank ID\n"
            )
        tab = create_context(os.path.join(self.dir, "epoc.csv"), name="epoc")
        doc = create_context(os.path.join(self.dir, "readme.txt"), name="readme")
        col = resolve_catalog(tab, sources=[doc]).get("Nitrate ")
        self.assertEqual(col.link_method, "text_codebook")
        self.assertEqual((col.description, col.units), ("nominal nitrate treatment concentration", "mg/L"))
        self.assertEqual(col.name, "Nitrate ")   # true header preserved for the locator

    def test_trailing_space_in_header_still_resolves_via_dictionary(self):
        pd.DataFrame({"Nitrate ": [1.0, 2.0, 3.0], "sp": ["a", "b", "c"]}).to_csv(
            os.path.join(self.dir, "d.csv"), index=False
        )
        pd.DataFrame({"variable": ["nitrate", "sp"], "label": ["Nitrate treatment", "Species"]}).to_csv(
            os.path.join(self.dir, "cb.csv"), index=False
        )
        tab = create_context(os.path.join(self.dir, "d.csv"), name="d")
        cb = create_context(os.path.join(self.dir, "cb.csv"), name="cb")
        col = resolve_catalog(tab, sources=[cb]).get("Nitrate ")
        self.assertEqual(col.link_method, "structured_dictionary")
        self.assertEqual(col.description, "Nitrate treatment")
        self.assertEqual(col.name, "Nitrate ")

    def test_missing_delimiter_does_not_absorb_the_next_entry(self):
        """A glossary with a missing ';' merges two entries; the definition must stop at
        the next entry's head, not swallow it (real Readme: 'Mass – fish mass (g)
        Duration - recovery duration (min)')."""
        pd.DataFrame({"Mass": [1.0, 2.0], "Duration": [3.0, 4.0]}).to_csv(
            os.path.join(self.dir, "epoc.csv"), index=False
        )
        with open(os.path.join(self.dir, "readme.txt"), "w") as f:
            f.write("Mass – fish mass (g) Duration - recovery duration (min); EPOC – oxygen debt\n")
        tab = create_context(os.path.join(self.dir, "epoc.csv"), name="epoc")
        doc = create_context(os.path.join(self.dir, "readme.txt"), name="readme")
        cat = resolve_catalog(tab, sources=[doc])
        self.assertEqual((cat.get("Mass").description, cat.get("Mass").units), ("fish mass", "g"))
        # The absorbed entry still resolves on its own term.
        self.assertEqual((cat.get("Duration").description, cat.get("Duration").units),
                         ("recovery duration", "min"))

    def test_internal_hyphen_in_a_value_is_preserved(self):
        """A hyphen *inside* a word is not a spaced dash, so the value is left intact."""
        pd.DataFrame({"epoc": [1.0, 2.0], "mass": [3.0, 4.0]}).to_csv(os.path.join(self.dir, "e.csv"), index=False)
        with open(os.path.join(self.dir, "r.txt"), "w") as f:
            f.write(
                "mass – fish mass (g)\n"
                "epoc – excess post-exercise oxygen consumption (mg O2 kg-1 h-1)\n"
                "tank – replicate tank\n"
            )
        tab = create_context(os.path.join(self.dir, "e.csv"), name="e")
        doc = create_context(os.path.join(self.dir, "r.txt"), name="r")
        epoc = resolve_catalog(tab, sources=[doc]).get("epoc")
        self.assertEqual(
            (epoc.description, epoc.units),
            ("excess post-exercise oxygen consumption", "mg O2 kg-1 h-1"),
        )

    # --- cross-check without a dictionary claim --------------------------

    def test_out_of_range_latitude_claim_conflicts(self):
        pd.DataFrame({"x": [100.0, 150.0, 200.0]}).to_csv(
            os.path.join(self.dir, "bad.csv"), index=False
        )
        pd.DataFrame({"variable": ["x"], "label": ["Latitude"], "units": ["degrees"]}).to_csv(
            os.path.join(self.dir, "bad_codebook.csv"), index=False
        )
        tab = create_context(os.path.join(self.dir, "bad.csv"), name="bad")
        cb = create_context(os.path.join(self.dir, "bad_codebook.csv"), name="bad_cb")
        cat = resolve_catalog(tab, sources=[cb])
        self.assertTrue(cat.get("x").conflicts)
        self.assertIn("[-90, 90]", cat.get("x").conflicts[0])


class CatalogEdgeCaseTest(unittest.TestCase):
    """Realistic messiness: partial codebooks, decoys, conflicts, unchecked claims."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame(
            {
                "la": [53.1, 53.2, 53.3],
                "lo": [-9.1, -9.2, -9.3],
                "sp": ["AA", "BB", "CC"],
                "n": [1, 2, 3],
                "depth": [10.0, 20.0, 30.0],
            }
        ).to_csv(os.path.join(self.dir, "obs.csv"), index=False)
        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")

    def tearDown(self):
        clear_registry()

    def _codebook(self, name, mapping, extra_units=None):
        """A codebook CSV: {variable -> description}."""
        rows = {"variable": list(mapping), "description": list(mapping.values())}
        if extra_units:
            rows["units"] = [extra_units.get(v, "") for v in mapping]
        path = os.path.join(self.dir, f"{name}.csv")
        pd.DataFrame(rows).to_csv(path, index=False)
        return create_context(path, name=name)

    def test_partial_codebook_resolves_only_what_it_covers(self):
        """The cliff fix: a codebook covering 2 of 5 columns still contributes.

        Under a recall threshold this whole source (40% coverage) was discarded;
        keying on the key column's precision keeps its rows.
        """
        cb = self._codebook("cb", {"la": "Latitude", "lo": "Longitude"})
        cat = resolve_catalog(self.tab, sources=[cb])
        self.assertEqual(cat.get("la").link_method, "structured_dictionary")
        self.assertEqual(cat.get("lo").link_method, "structured_dictionary")
        # Columns the codebook omits fall through to the other rungs.
        self.assertNotEqual(cat.get("sp").link_method, "structured_dictionary")
        self.assertNotEqual(cat.get("n").link_method, "structured_dictionary")

    def test_data_table_with_a_coincidental_name_is_not_a_codebook(self):
        """A key column mostly of non-names (low precision) is rejected as a decoy."""
        pd.DataFrame(
            {"note": ["la", "x1", "x2", "x3", "x4", "x5"], "val": list(range(6))}
        ).to_csv(os.path.join(self.dir, "decoy.csv"), index=False)
        decoy = create_context(os.path.join(self.dir, "decoy.csv"), name="decoy")
        cat = resolve_catalog(self.tab, sources=[decoy])
        self.assertNotEqual(cat.get("la").link_method, "structured_dictionary")

    def test_conflicting_codebooks_are_surfaced_not_silently_dropped(self):
        """Disagreeing sources: the conflict is recorded and confidence lowered."""
        cb1 = self._codebook("cb1", {"la": "Latitude", "lo": "Longitude"})
        cb2 = self._codebook("cb2", {"la": "Something else", "lo": "Other"})
        cat = resolve_catalog(self.tab, sources=[cb1, cb2])
        la = cat.get("la")
        self.assertIn("Latitude", la.description)          # first consistent, still chosen
        self.assertTrue(la.conflicts)                      # disagreement surfaced
        self.assertEqual(la.link_confidence, "medium")     # not "high" — contested
        self.assertEqual(la.corroborated_by, [])           # they disagree — no corroboration
        # the losing candidate is kept, not discarded
        self.assertTrue(any("Something else" in a["description"] for a in la.alternatives))

    def test_value_profile_adjudicates_a_unit_conflict(self):
        """Two codebooks disagree on units; the values break the tie."""
        pd.DataFrame({"temp": [4.5, 10.2, 21.0]}).to_csv(
            os.path.join(self.dir, "t.csv"), index=False
        )
        tab = create_context(os.path.join(self.dir, "t.csv"), name="t")
        # Kelvin is listed *first*, but 4–21 refutes it → Celsius wins.
        cbk = self._codebook("cbk", {"temp": "Air temperature"}, extra_units={"temp": "Kelvin"})
        cbc = self._codebook("cbc", {"temp": "Air temperature"}, extra_units={"temp": "Celsius"})
        cat = resolve_catalog(tab, sources=[cbk, cbc])
        temp = cat.get("temp")
        self.assertEqual(temp.units, "Celsius")            # adjudicated by the values
        self.assertEqual(temp.link_confidence, "medium")
        self.assertTrue(any("Kelvin" in c for c in temp.conflicts))

    def test_corroborating_text_codebooks_raise_confidence(self):
        """Two documents defining the same token → corroborated above single-source."""
        pd.DataFrame({"qq": [1, 2, 3], "zz": [4, 5, 6]}).to_csv(os.path.join(self.dir, "q.csv"), index=False)
        for i in (1, 2):
            with open(os.path.join(self.dir, f"doc{i}.md"), "w") as f:
                f.write(f"# Doc {i}\n\nqq = quality index\nzz = zone code\nsite = site name\n")
        tab = create_context(os.path.join(self.dir, "q.csv"), name="q")
        d1 = create_context(os.path.join(self.dir, "doc1.md"), name="doc1")
        d2 = create_context(os.path.join(self.dir, "doc2.md"), name="doc2")
        qq = resolve_catalog(tab, sources=[d1, d2]).get("qq")
        self.assertEqual(qq.link_method, "text_codebook")
        self.assertEqual(qq.link_confidence, "high")       # corroborated (a single text codebook is medium)
        self.assertEqual(qq.conflicts, [])
        # the agreeing source is recorded, citably — not just a confidence bump
        self.assertEqual(len(qq.corroborated_by), 1)
        self.assertIn("doc2", qq.corroborated_by[0])

    def test_corroboration_is_recorded_with_citations(self):
        """Two codebooks agreeing verbatim: the confirmer is cited, not just counted."""
        cb1 = self._codebook("cb1", {"la": "Latitude", "lo": "Longitude"})
        cb2 = self._codebook("cb2", {"la": "Latitude", "lo": "Longitude"})
        la = resolve_catalog(self.tab, sources=[cb1, cb2]).get("la")
        self.assertEqual(la.link_confidence, "high")       # corroborated
        self.assertEqual(la.conflicts, [])                 # agreement, not conflict
        self.assertEqual(len(la.corroborated_by), 1)       # the second codebook
        self.assertIn("cb2", la.corroborated_by[0])

    def test_wrong_categorical_description_is_not_cross_checked(self):
        """KNOWN LIMITATION: cross-check is numeric-only, so a wrong categorical
        meaning is accepted with no conflict."""
        cb = self._codebook("cb", {"sp": "Site name", "n": "Count"})
        cat = resolve_catalog(self.tab, sources=[cb])
        sp = cat.get("sp")
        self.assertEqual(sp.description, "Site name")   # accepted verbatim
        self.assertEqual(sp.conflicts, [])              # numeric cross-check can't catch it

    def test_full_codebook_still_resolves_everything(self):
        """The happy path is unchanged by the precision-based acceptance."""
        cb = self._codebook(
            "cb",
            {"la": "Latitude", "lo": "Longitude", "sp": "Species", "n": "Count", "depth": "Depth"},
        )
        cat = resolve_catalog(self.tab, sources=[cb])
        self.assertTrue(all(c.link_method == "structured_dictionary" for c in cat.columns))


class FullyExercisedCatalogTest(unittest.TestCase):
    """One bundle that drives every resolution outcome at once.

    The columns are chosen so that, between them, every `link_method`, every
    `link_confidence`, every `value_label`, and each of conflicts / corroboration /
    alternatives is exercised — and one column (`tmp`) fills *all* of them at once:

    | column | method               | conf   | label       | fills                              |
    |--------|----------------------|--------|-------------|------------------------------------|
    | tmp    | structured_dictionary| medium | numeric     | everything (conflict+corrob+alts)  |
    | la     | structured_dictionary| high   | coordinate  | corroboration → high, alternatives |
    | frac   | structured_dictionary| low    | numeric     | chosen claim refuted by values     |
    | note   | text_codebook        | medium | categorical | glossary entry in the README       |
    | dt     | value_prior          | high   | temporal    | self-evident value, chosen by prior|
    | oid    | none                 | none   | numeric     | abstains — nothing describes it     |

    `tmp`: two codebooks agree on Celsius (corroboration) while a third says Kelvin,
    which the 4.1–21.9 values refute (a same-tier rival ruled out → medium, conflict
    recorded); all three plus the value prior are kept as alternatives.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame(
            {
                "oid": list(range(1, 9)),
                "la": [53.1, 53.4, 53.8, 54.0, 54.2, 54.5, 54.7, 54.8],
                "tmp": [4.1, 8.3, 12.0, 15.5, 18.2, 21.9, 10.4, 6.7],
                "note": ["ok", "ok", "flag", "ok", "flag", "ok", "ok", "flag"],
                "dt": ["2021-03-%02d" % (i + 1) for i in range(8)],
                "frac": [0, 40, 120, 250, 15, 90, 300, 5],  # a codebook wrongly calls this a %
            }
        ).to_csv(os.path.join(self.dir, "observations.csv"), index=False)
        # cb1 & cb2 agree on tmp (Celsius) → corroboration; cb3 says Kelvin → refuted.
        # cb1 also (wrongly) labels frac a percentage, refuted by its 0–300 range.
        pd.DataFrame({
            "variable": ["tmp", "la", "frac"],
            "description": ["Water temperature at the station", "Latitude of the survey point", "Fraction measure"],
            "units": ["Celsius", "decimal degrees", "%"],
        }).to_csv(os.path.join(self.dir, "cb1.csv"), index=False)
        pd.DataFrame({
            "variable": ["tmp", "la"],
            "description": ["Water temperature at the station", "Latitude of the survey point"],
            "units": ["Celsius", "decimal degrees"],
        }).to_csv(os.path.join(self.dir, "cb2.csv"), index=False)
        pd.DataFrame({
            "variable": ["tmp"],
            "description": ["Water temperature at the station"],
            "units": ["Kelvin"],
        }).to_csv(os.path.join(self.dir, "cb3.csv"), index=False)
        with open(os.path.join(self.dir, "README.md"), "w") as f:
            # The glossary also covers la and frac, but a text codebook ranks below the
            # tables that describe those, so it only resolves `note`.
            f.write(
                "# Survey\n\nField glossary:\n\n"
                "- `note` = field remark recorded by the observer\n"
                "- `la` = survey point latitude\n"
                "- `frac` = sampled fraction\n"
            )

        self.tab = create_context(os.path.join(self.dir, "observations.csv"), name="obs")
        self.sources = [
            create_context(os.path.join(self.dir, n), name=n.split(".")[0])
            for n in ("cb1.csv", "cb2.csv", "cb3.csv", "README.md")
        ]

    def tearDown(self):
        clear_registry()

    def _catalog(self):
        return resolve_catalog(self.tab, sources=self.sources)

    def test_tmp_column_populates_every_field(self):
        """The linchpin: a single column with *no* empty ResolvedColumn field."""
        tmp = self._catalog().get("tmp")
        # Scalar fields all set (non-empty, non-"none").
        self.assertEqual(tmp.resource, "observations")
        self.assertEqual(tmp.name, "tmp")
        self.assertTrue(tmp.dtype)
        self.assertEqual(tmp.description, "Water temperature at the station")
        self.assertEqual(tmp.units, "Celsius")
        self.assertEqual(tmp.link_method, "structured_dictionary")
        self.assertEqual(tmp.link_confidence, "medium")   # same-tier Kelvin rival refuted
        self.assertEqual(tmp.link_evidence, "cb1 row 'tmp'")
        self.assertTrue(tmp.value_label)
        # List fields all non-empty — conflict, corroboration, and alternatives together.
        self.assertTrue(tmp.conflicts)
        self.assertIn("Kelvin", tmp.conflicts[0])
        self.assertEqual(tmp.corroborated_by, ["cb2 row 'tmp'"])
        self.assertEqual(len(tmp.alternatives), 2)        # cb2 and cb3 (tmp is not a coordinate)
        # Nothing in the serialized form is None/empty either.
        d = tmp.to_dict()
        empty = [k for k, v in d.items() if v in (None, "", [], "none")]
        self.assertEqual(empty, [], f"unexpected empty ResolvedColumn fields: {empty}")

    def test_every_link_method_is_exercised(self):
        cat = self._catalog()
        methods = {c.name: c.link_method for c in cat.columns}
        self.assertEqual(methods["la"], "structured_dictionary")
        self.assertEqual(methods["note"], "text_codebook")
        self.assertEqual(methods["dt"], "value_prior")
        self.assertEqual(methods["oid"], "none")

    def test_every_confidence_level_is_exercised(self):
        cat = self._catalog()
        conf = {c.name: c.link_confidence for c in cat.columns}
        self.assertEqual(conf["la"], "high")     # corroborated
        self.assertEqual(conf["dt"], "high")     # self-evident temporal
        self.assertEqual(conf["tmp"], "medium")  # tie broken by the values
        self.assertEqual(conf["note"], "medium") # lone text codebook
        self.assertEqual(conf["frac"], "low")    # chosen claim refuted
        self.assertEqual(conf["oid"], "none")    # abstained

    def test_every_value_label_is_exercised(self):
        cat = self._catalog()
        labels = {c.value_label for c in cat.columns}
        self.assertEqual(labels, {"coordinate", "temporal", "categorical", "numeric"})

    def test_chosen_claim_refuted_gives_low_and_a_conflict(self):
        frac = self._catalog().get("frac")
        self.assertEqual(frac.link_confidence, "low")
        self.assertTrue(frac.conflicts)
        self.assertIn("percentage", frac.conflicts[0])
        self.assertEqual(frac.corroborated_by, [])   # nothing agreed with it

    def test_corroboration_lifts_a_clean_agreement_to_high(self):
        la = self._catalog().get("la")
        self.assertEqual(la.link_confidence, "high")
        self.assertEqual(la.corroborated_by, ["cb2 row 'la'"])
        self.assertEqual(la.conflicts, [])           # no disagreement, no refutation

    def test_abstained_column_leaves_the_link_fields_empty(self):
        oid = self._catalog().get("oid")
        self.assertEqual(oid.link_method, "none")
        self.assertEqual(oid.link_confidence, "none")
        self.assertIsNone(oid.description)
        self.assertEqual(oid.value_label, "numeric")  # the coarse prior is still kept


class TextCodebookTest(unittest.TestCase):
    """A glossary in a document is accepted on its structure, never on a separator.

    ``la = latitude`` and ``AAS = MO2max − MO2standard`` share the ``=``; what tells a
    README's codebook from a manuscript's equation is a run of adjacent, well-formed
    entries keyed on the schema's names.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        clear_registry()

    def _doc(self, text, name="doc.txt"):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            f.write(text)
        return create_context(path, name=name.split(".")[0])

    def _codebook(self, text, vocabulary):
        doc = self._doc(text)
        return _as_text_codebook(doc, doc.resources[0], vocabulary)

    def test_an_equation_in_a_manuscript_is_not_a_definition(self):
        """The regression: PDF-extracted Methods prose defining aerobic scope by formula.
        The old glossary regex resolved `aas` as 'Inline graphicO2MAX Inline
        graphicO2STANDARD) and factorial a'."""
        text = (
            "Metabolic rates were used to calculate absolute aerobic scope (AAS; "
            "AAS=Inline graphicO2MAX Inline graphicO2STANDARD) and factorial aerobic scope "
            "(FAS; FAS=Inline graphicO2MAX / Inline graphicO2STANDARD). Mass: recorded daily.\n"
        )
        self.assertIsNone(self._codebook(text, ["aas", "fas", "mass"]))

    def test_an_isolated_definition_is_left_to_the_reader(self):
        self.assertIsNone(self._codebook("# Notes\n\nHere qq = quality quotient score for the site.\n", ["qq"]))

    def test_two_entries_are_not_yet_a_glossary(self):
        self.assertIsNone(self._codebook("la = latitude; lo = longitude\n", ["la", "lo"]))

    def test_a_run_mostly_of_other_names_is_not_this_tables_codebook(self):
        text = "alpha – first thing; beta – second thing; gamma – third thing; la – latitude\n"
        self.assertIsNone(self._codebook(text, ["la", "lo"]))   # 1 of 4 terms is a column

    def test_a_malformed_entry_breaks_the_run(self):
        # Two good entries, a formula, two good entries: neither side reaches three.
        text = "a1 – first; a2 – second; a3 = a1 − a2; a4 – fourth; a5 – fifth\n"
        self.assertIsNone(self._codebook(text, ["a1", "a2", "a3", "a4", "a5"]))

    def test_markdown_list_with_units(self):
        cb = self._codebook(
            "# Variables\n\n- `la`: latitude (decimal degrees)\n- `lo`: longitude (decimal degrees)\n"
            "- **sp** – species code\n\nThe survey ran in 2020.\n",
            ["la", "lo", "sp"],
        )
        self.assertEqual(
            {k: (e.description, e.units) for k, e in cb.by_name.items()},
            {"la": ("latitude", "decimal degrees"), "lo": ("longitude", "decimal degrees"),
             "sp": ("species code", None)},
        )

    def test_real_readme_wrapped_glossary(self):
        """The shape of data/sample/Readme.txt: entries separated by ';', lines wrapped
        mid-definition, a tab heading between runs, one entry with no ';' before the next."""
        text = (
            "Abbreviations and units:\nTab 2 – EPOC\n"
            "pH – acclimation pH; nitrate – nominal nitrate treatment concentration (mg/L); tank – replicate tank\n"
            "ID; ID – individual fish ID; Mass – fish mass (g) Duration - recovery duration (min); EPOC –\n"
            "excess post-exercise oxygen consumption (mg O2 kg-1 h-1)\n"
            "Tab 3 – MR\n"
            "rest – resting metabolic rate (mg O2 kg-1 h-1); aas – absolute aerobic scope\n"
            "((mg O2 kg-1 h-1)); fas – factorial aerobic scope\n"
        )
        vocabulary = ["pH", "nitrate", "tank", "ID", "Mass", "Duration", "EPOC", "rest", "aas", "fas"]
        cb = self._codebook(text, vocabulary)
        got = {k: (e.description, e.units) for k, e in cb.by_name.items()}
        self.assertEqual(got["tank"], ("replicate tank ID", None))            # wrapped line joined
        self.assertEqual(got["mass"], ("fish mass", "g"))                     # stops at 'Duration'
        self.assertEqual(got["epoc"], ("excess post-exercise oxygen consumption", "mg O2 kg-1 h-1"))
        self.assertEqual(got["aas"], ("absolute aerobic scope", "mg O2 kg-1 h-1"))
        self.assertEqual(got["fas"], ("factorial aerobic scope", None))       # heading not absorbed
        self.assertEqual(set(got), {_k.lower() for _k in vocabulary})

    def test_a_codebook_table_outranks_a_text_codebook(self):
        pd.DataFrame({"la": [53.1, 53.2], "lo": [-9.1, -9.2], "sp": ["a", "b"]}).to_csv(
            os.path.join(self.dir, "obs.csv"), index=False
        )
        pd.DataFrame({"variable": ["la", "lo"], "description": ["Latitude", "Longitude"]}).to_csv(
            os.path.join(self.dir, "cb.csv"), index=False
        )
        tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        cb = create_context(os.path.join(self.dir, "cb.csv"), name="cb")
        readme = self._doc("la – latitude\nlo – easting of the site\nsp – species code\n", "readme.txt")
        cat = resolve_catalog(tab, sources=[readme, cb])
        la, lo = cat.get("la"), cat.get("lo")
        self.assertEqual(la.link_method, "structured_dictionary")
        self.assertEqual(la.link_confidence, "high")
        self.assertEqual(la.corroborated_by, ["readme#0-13"])    # the text agrees, and is cited
        self.assertEqual(lo.description, "Longitude")            # the table wins a disagreement
        self.assertEqual(lo.conflicts, [])                       # a lower tier is not a same-tier rival
        self.assertTrue(any(a["method"] == "text_codebook" for a in lo.alternatives))
        self.assertEqual(cat.get("sp").link_method, "text_codebook")   # the text fills the gap


class MultiTableBundleTest(unittest.TestCase):
    """A real repo is many tables; resolve_bundle spans all their columns at once."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # Two data tables. Both have opaque column names; a shared codebook that
        # documents columns across *both* resolves them wherever they live.
        pd.DataFrame({"a": ["10.x/y"], "b": ["A survey dataset"]}).to_csv(
            os.path.join(self.dir, "dataset.csv"), index=False
        )
        pd.DataFrame({"a": [96.0], "t": [10.0]}).to_csv(
            os.path.join(self.dir, "measure.csv"), index=False
        )
        pd.DataFrame({
            "variable": ["a", "b", "t"],
            "description": ["Dataset DOI", "Dataset title", "Water temperature"],
        }).to_csv(os.path.join(self.dir, "codebook.csv"), index=False)

        self.dataset = create_context(os.path.join(self.dir, "dataset.csv"), name="dataset")
        self.measure = create_context(os.path.join(self.dir, "measure.csv"), name="measure")
        self.codebook = create_context(os.path.join(self.dir, "codebook.csv"), name="codebook")

    def tearDown(self):
        clear_registry()

    def test_bundle_catalog_spans_every_table(self):
        cat = resolve_bundle([self.dataset, self.measure], sources=[self.codebook])
        by_resource = {}
        for c in cat.columns:
            by_resource.setdefault(c.resource, set()).add(c.name)
        self.assertEqual(by_resource["dataset"], {"a", "b"})
        self.assertEqual(by_resource["measure"], {"a", "t"})

    def test_same_name_in_two_tables_is_disambiguated_by_resource(self):
        # Column 'a' exists in *both* tables, so it appears twice in the catalog.
        cat = resolve_bundle([self.dataset, self.measure], sources=[self.codebook])
        a_cols = [c for c in cat.columns if c.name == "a"]
        self.assertEqual(len(a_cols), 2)  # both kept, not collapsed to one
        # find(resource) returns the right table's column; get() (name-only) can't —
        # it just returns the first, which is why routing must use find().
        self.assertEqual(cat.find("a", "dataset").resource, "dataset")
        self.assertEqual(cat.find("a", "measure").resource, "measure")
        self.assertIsNot(cat.find("a", "dataset"), cat.find("a", "measure"))
        self.assertEqual(cat.get("a").resource, "dataset")  # ambiguous: first only

    def test_routes_fields_to_columns_across_tables(self):
        from typing import Optional

        from pydantic import BaseModel, Field

        from src.router import compile_field_plan, route_fields

        class Meta(BaseModel):
            doi: Optional[str] = Field(default=None, description="the dataset DOI")
            water_temperature: Optional[float] = Field(default=None, description="the water temperature")

        cat = resolve_bundle([self.dataset, self.measure], sources=[self.codebook])
        fp = route_fields(Meta, catalog=cat, docs=[])
        doi = fp.routings["doi"].candidates[0]
        temp = fp.routings["water_temperature"].candidates[0]
        self.assertEqual((doi.resource, doi.locator), ("dataset", "a"))
        self.assertEqual((temp.resource, temp.locator), ("measure", "t"))
        # A task opens every table its candidates live in, not just rank 1's. The
        # router proposes a set; scoping to rank 1's table would make the lower-ranked
        # candidates unreachable and quietly turn the proposal into a decision.
        plan = compile_field_plan(fp)
        col_tasks = [t for t in plan.steps if t.fields and t.player == "data_analyst"]
        by_field = {
            binding["field"]: task
            for task in col_tasks for binding in task.field_bindings
        }
        for name in ("doi", "water_temperature"):
            spanned = {
                c.resource for c in fp.routings[name].candidates if c.resource
            }
            self.assertTrue(spanned <= set(by_field[name].target_resources))
        # doi's candidates reach into both tables, so its task opens both.
        self.assertEqual(
            sorted(by_field["doi"].target_resources), ["dataset", "measure"]
        )


class MixedDocumentLengthTest(unittest.TestCase):
    """The read path is chosen per file, so one long document cannot drag the rest down.

    A bundle is routinely a short README beside a long manuscript. Deciding the path on
    the bundle *total* meant the manuscript pushed every file onto the localized path,
    and localization is lexical — so a column defined in the README in plain narrative,
    without its own token, silently stopped resolving. Nothing errored; recall just fell.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pd.DataFrame({"bm": [180.5, 184.0, 187.5], "epoc": [42.0, 43.0, 44.0]}).to_csv(
            os.path.join(self.dir, "obs.csv"), index=False
        )
        self.tab = create_context(os.path.join(self.dir, "obs.csv"), name="obs")
        # Short: defines `bm` without ever writing "bm", so BM25 cannot place it.
        self.short = self._doc(
            "readme.md",
            "# Trial\n\nWet body mass was recorded to the nearest 0.1 g at the "
            "start of each trial.\n",
        )
        # Long: comfortably past the threshold, and names `epoc` so retrieval can.
        self.long = self._doc(
            "manuscript.txt",
            "# Methods\n\n"
            + ("Fish were held under a constant photoperiod and fed once daily. " * 400)
            + "\n\nExcess post-exercise oxygen consumption (epoc) was integrated "
            "from the recovery trace.\n",
        )

    def tearDown(self):
        clear_registry()

    def _doc(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w") as handle:
            handle.write(text)
        return create_context(path, name=name.split(".")[0])

    def _reader(self):
        """A model that reads whatever passage it is handed, and nothing else."""
        def invoke(prompt):
            out = {}
            if "Wet body mass" in prompt:
                out["bm"] = {
                    "description": "wet body mass", "units": "g",
                    "quote": "Wet body mass was recorded to the nearest 0.1 g",
                }
            if "Excess post-exercise" in prompt:
                out["epoc"] = {
                    "description": "excess post-exercise oxygen consumption",
                    "units": "mg O2 kg-1 h-1",
                    "quote": "Excess post-exercise oxygen consumption (epoc) was "
                             "integrated",
                }
            return json.dumps(out)
        return LLMProseReader(invoke)

    def test_each_file_takes_its_own_path(self):
        from src.router.catalog import _doc_resources, _split_by_length

        short, long = _split_by_length(_doc_resources([self.short, self.long]))
        self.assertEqual([s.resource for s in short], ["readme"])
        self.assertEqual([s.resource for s in long], ["manuscript"])

    def test_a_long_neighbour_does_not_cost_the_short_file_its_whole_doc_read(self):
        """The regression: `bm` has no token in the README, so only a whole read finds it."""
        catalog = resolve_catalog(
            self.tab, sources=[self.short, self.long], prose_reader=self._reader()
        )
        bm = catalog.get("bm")
        self.assertEqual(bm.link_method, "prose_read")
        self.assertEqual(bm.description, "wet body mass")
        self.assertIn("readme", bm.link_evidence)

    def test_the_long_file_is_still_localized_and_still_resolves(self):
        catalog = resolve_catalog(
            self.tab, sources=[self.short, self.long], prose_reader=self._reader()
        )
        epoc = catalog.get("epoc")
        self.assertEqual(epoc.link_method, "prose_read")
        self.assertIn("manuscript", epoc.link_evidence)

    def test_both_paths_contribute_to_one_resolution(self):
        catalog = resolve_catalog(
            self.tab, sources=[self.short, self.long], prose_reader=self._reader()
        )
        cited = {
            c.name: c.link_evidence.split("#")[0]
            for c in catalog.columns if c.link_evidence
        }
        self.assertEqual(cited, {"bm": "readme", "epoc": "manuscript"})


class ProseReaderTierTest(unittest.TestCase):
    """The reader tier: hand a document (or its localized chunks) to a reader.

    It is opt-in (``prose_reader=``) and runs only on columns no codebook resolved.
    These tests exercise: the LLM reader's parsing, grounding a read against its quote,
    retrieval localizing the right chunk across long/many documents, the opt-in gate,
    residual gating, and the batched/cached call shape.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # mass/epoc are generic float names: without a described source they abstain
        # (numeric long tail), so any resolution here comes from the prose reader.
        pd.DataFrame({"mass": [12.1, 15.3, 9.8], "epoc": [1.0, 2.0, 3.0]}).to_csv(
            os.path.join(self.dir, "fish.csv"), index=False
        )
        self.tab = create_context(os.path.join(self.dir, "fish.csv"), name="fish")
        self.reader = _PassageReader()

    def tearDown(self):
        clear_registry()

    def _doc(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            f.write(text)
        return create_context(path, name=name.split(".")[0])

    # --- the LLM reader in isolation (stub invoke, no network) -----------

    def test_llm_reader_parses_defined_columns_and_abstains_on_the_rest(self):
        reader = LLMProseReader(lambda p: '{"mass": {"description": "fish mass", "units": "g"}}')
        out = reader.read_many(columns=[("mass", "float"), ("epoc", "float")], chunk="…")
        self.assertEqual((out["mass"].description, out["mass"].units), ("fish mass", "g"))
        self.assertNotIn("epoc", out)                       # omitted by the model → abstains

    def test_llm_reader_survives_malformed_output(self):
        reader = LLMProseReader(lambda p: "Sorry, I can't help with that.")
        self.assertEqual(reader.read_many(columns=[("mass", "float")], chunk="x"), {})

    def test_llm_reader_maps_trimmed_names_back_to_the_true_header(self):
        # The model is asked about the trimmed 'Nitrate'; the result is keyed by the
        # real header 'Nitrate ' (trailing space) so the locator still addresses it.
        reader = LLMProseReader(lambda p: '{"Nitrate": {"description": "nitrate treatment", "units": "mg/L"}}')
        out = reader.read_many(columns=[("Nitrate ", "float")], chunk="x")
        self.assertIn("Nitrate ", out)
        self.assertEqual(out["Nitrate "].units, "mg/L")

    def test_llm_reader_ignores_columns_it_was_not_asked_about(self):
        reader = LLMProseReader(
            lambda p: '{"mass": {"description": "m"}, "ghost": {"description": "x"}}'
        )
        out = reader.read_many(columns=[("mass", "float")], chunk="x")
        self.assertEqual(set(out), {"mass"})                # hallucinated 'ghost' dropped

    def test_from_chat_model_adapts_a_chat_model(self):
        class _Msg:
            def __init__(self, content): self.content = content

        class _Model:
            def invoke(self, prompt):
                return _Msg('{"mass": {"description": "fish mass", "units": "g"}}')

        reader = LLMProseReader.from_chat_model(_Model())
        out = reader.read_many(columns=[("mass", "float")], chunk="x")
        self.assertEqual(out["mass"].description, "fish mass")

    def test_llm_reader_returns_the_supporting_quote(self):
        reader = LLMProseReader(
            lambda p: '{"mass": {"description": "fish mass", "units": "g", '
                      '"quote": "Mass is the fish mass in grams."}}'
        )
        out = reader.read_many(columns=[("mass", "float")], chunk="x")
        self.assertEqual(out["mass"].quote, "Mass is the fish mass in grams.")

    def test_llm_read_produces_an_offset_located_quoted_span(self):
        # The supporting quote is located back in the source, turning the citation into a
        # real span whose offsets address the exact sentence — verifiable provenance.
        text = "In this tab, Mass is the fish mass in grams. epoc is the oxygen debt.\n"
        doc = self._doc("hard.txt", text)
        quote = "Mass is the fish mass in grams."
        stub = _StubLLM({"mass": {"description": "fish mass", "units": "g", "quote": quote}})
        col = resolve_catalog(self.tab, sources=[doc], prose_reader=LLMProseReader(stub)).get("mass")
        self.assertEqual(col.link_method, "prose_read")
        resource, span = col.link_evidence.split("#")
        start, end = (int(x) for x in span.split("-"))
        self.assertEqual(text[start:end], quote)             # offsets address the exact sentence

    def test_unlocatable_quote_falls_back_to_a_coarse_citation(self):
        # If the model paraphrases (no verbatim hit), no span is fabricated — the citation
        # stays document-level rather than pointing at the wrong offsets.
        doc = self._doc("hard.txt", "Mass is the fish mass in grams.\n")
        stub = _StubLLM({"mass": {"description": "fish mass", "units": "g",
                                  "quote": "the fish's body mass"}})   # not verbatim
        col = resolve_catalog(self.tab, sources=[doc], prose_reader=LLMProseReader(stub)).get("mass")
        self.assertEqual(col.link_evidence, "hard#0")        # coarse, no fabricated span

    # --- grounding grade: the located quote verifies the read (deterministic) ---

    def test_grounded_read_is_high_confidence(self):
        # Quote locates, names the column, and carries the description → well-grounded.
        doc = self._doc("hard.txt", "In this tab, Mass is the fish mass in grams.\n")
        stub = _StubLLM({"mass": {"description": "fish mass", "units": "g",
                                  "quote": "Mass is the fish mass in grams."}})
        col = resolve_catalog(self.tab, sources=[doc], prose_reader=LLMProseReader(stub)).get("mass")
        self.assertEqual(col.link_confidence, "high")
        self.assertEqual(col.conflicts, [])

    def test_located_but_offtopic_quote_is_medium(self):
        # The quote locates but neither names the column nor carries the description's
        # words — a real sentence with a weak link, so medium not high.
        text = "The study spanned six weeks across two tanks in spring.\n"
        doc = self._doc("hard.txt", text)
        stub = _StubLLM({"mass": {"description": "fish mass", "units": "g",
                                  "quote": "The study spanned six weeks across two tanks in spring."}})
        col = resolve_catalog(self.tab, sources=[doc], prose_reader=LLMProseReader(stub)).get("mass")
        self.assertEqual(col.link_confidence, "medium")

    def test_ungrounded_read_is_low_and_records_a_conflict(self):
        # No verbatim quote in the source → evidence unconfirmed → low, with a conflict.
        doc = self._doc("hard.txt", "Mass is the fish mass in grams.\n")
        stub = _StubLLM({"mass": {"description": "fish mass", "units": "g",
                                  "quote": "an entirely different sentence"}})
        col = resolve_catalog(self.tab, sources=[doc], prose_reader=LLMProseReader(stub)).get("mass")
        self.assertEqual(col.link_confidence, "low")
        self.assertTrue(any("unconfirmed" in m for m in col.conflicts))

    def test_locate_tolerates_reflowed_whitespace(self):
        # The model returns the sentence with collapsed spacing; a re-flowed newline in
        # the source must still locate (not be misread as a paraphrase → unfair demotion).
        text = "In this tab, Mass is the\nfish mass in grams.\n"     # newline mid-sentence
        doc = self._doc("hard.txt", text)
        stub = _StubLLM({"mass": {"description": "fish mass", "units": "g",
                                  "quote": "Mass is the fish mass in grams."}})
        col = resolve_catalog(self.tab, sources=[doc], prose_reader=LLMProseReader(stub)).get("mass")
        self.assertEqual(col.link_confidence, "high")        # located despite the newline
        self.assertIn("-", col.link_evidence.split("#")[1])  # a real span, not coarse

    # --- the tier in the resolution pipeline -----------------------------

    def test_reader_resolves_narrative_no_codebook_covers(self):
        # The token is defined in parens *after* the phrase ("Wet body mass (mass)") —
        # narrative, not a glossary, so only a reader can resolve it.
        doc = self._doc(
            "manuscript.md",
            "# Methods\n\n"
            "Fish were held at 15C for two weeks prior to trials. "
            "Wet body mass (mass) was recorded to the nearest 0.1 g before each swim test. "
            "We then measured excess post-exercise oxygen consumption (EPOC).\n",
        )
        # Opt-in gate: with no reader the narrative definition is invisible → abstains.
        without = resolve_catalog(self.tab, sources=[doc]).get("mass")
        self.assertEqual(without.link_method, "none")
        # With the reader it is read.
        with_reader = resolve_catalog(
            self.tab, sources=[doc], prose_reader=self.reader
        ).get("mass")
        self.assertEqual(with_reader.link_method, "prose_read")
        self.assertEqual(with_reader.description, "wet body mass")
        # Well-grounded: the quote locates, names the column, and carries the description.
        self.assertEqual(with_reader.link_confidence, "high")
        self.assertIn("manuscript", with_reader.link_evidence)   # cites the located span

    def test_retrieval_localizes_the_defining_document_among_many(self):
        # Long-doc (retrieval) path: forced by a low whole-doc threshold. A long decoy
        # that mentions the token constantly but never defines it, and a short appendix
        # that defines it in narrative — retrieval must rank the appendix chunk to the
        # top so the reader resolves it, cited to the definer.
        decoy = self._doc("intro.md", "# Introduction\n\n" + ("Mass matters for fish physiology. " * 40))
        appendix = self._doc(
            "appendix.md",
            "# Appendix\n\nWet body mass (mass) was measured at the start of each trial.\n",
        )
        with patch("src.router.catalog._WHOLE_DOC_MAX_CHARS", 50):   # force the localize path
            col = resolve_catalog(
                self.tab, sources=[decoy, appendix], prose_reader=self.reader
            ).get("mass")
        self.assertEqual(col.link_method, "prose_read")
        self.assertEqual(col.description, "wet body mass")
        self.assertIn("appendix", col.link_evidence)   # the definer, not the decoy

    def test_reader_abstains_when_no_document_defines_the_column(self):
        doc = self._doc("unrelated.md", "# Notes\n\nThe experiment ran for six weeks in spring.\n")
        col = resolve_catalog(self.tab, sources=[doc], prose_reader=self.reader).get("mass")
        self.assertEqual(col.link_method, "none")   # nothing to retrieve/read → honest abstention

    # --- residual gating: the reader only fills genuine gaps ---------------

    def test_reader_is_skipped_for_columns_the_deterministic_tiers_resolved(self):
        # Residual gating: the README's glossary resolves both columns, so the reader is
        # never invoked — no re-reading the same line as false corroboration.
        doc = self._doc("readme.md", "# Vars\n\nmass – wet body mass; epoc – oxygen debt; tank – tank ID\n")
        spy = _CountingReader()
        cat = resolve_catalog(self.tab, sources=[doc], prose_reader=spy)
        self.assertEqual(spy.calls, [])                       # nothing residual → reader idle
        self.assertEqual(cat.get("mass").link_method, "text_codebook")
        self.assertEqual(cat.get("mass").link_confidence, "medium")  # not inflated by re-reading

    def test_reader_runs_only_on_the_unresolved_column(self):
        # 'mass', 'tank' and 'pH' are in the glossary (resolved deterministically); 'epoc'
        # only in narrative. The reader is asked about 'epoc' alone.
        pd.DataFrame({"mass": [1.0], "tank": [1], "pH": [7.0], "epoc": [2.0]}).to_csv(
            os.path.join(self.dir, "wide.csv"), index=False
        )
        tab = create_context(os.path.join(self.dir, "wide.csv"), name="wide")
        doc = self._doc(
            "readme.md",
            "# Vars\n\nmass – wet body mass; tank – tank ID; pH – acclimation pH\n\n"
            "Oxygen debt (epoc) was logged each trial.\n",
        )
        spy = _CountingReader()
        cat = resolve_catalog(tab, sources=[doc], prose_reader=spy)
        read_columns = {name for call in spy.calls for name in call[0]}
        self.assertEqual(read_columns, {"epoc"})             # 'mass' never handed to the reader
        self.assertEqual(cat.get("mass").link_method, "text_codebook")
        self.assertEqual(cat.get("epoc").link_method, "prose_read")

    # --- LLM reader over narrative prose, whole-doc (short doc) path -------

    def test_llm_reader_resolves_narrative_the_deterministic_path_cannot(self):
        # Plain prose ("Mass is the fish mass in grams") — no cued shape, so the
        # deterministic tiers abstain and only an LLM reader recovers it.
        doc = self._doc(
            "hard.txt",
            "In this tab, Mass is the fish mass in grams. epoc is the oxygen debt.\n",
        )
        self.assertEqual(resolve_catalog(self.tab, sources=[doc]).get("mass").link_method, "none")
        stub = _StubLLM({
            "mass": {"description": "fish mass", "units": "g"},
            "epoc": {"description": "oxygen debt", "units": None},
        })
        cat = resolve_catalog(self.tab, sources=[doc], prose_reader=LLMProseReader(stub))
        self.assertEqual(cat.get("mass").link_method, "prose_read")
        self.assertEqual((cat.get("mass").description, cat.get("mass").units), ("fish mass", "g"))
        self.assertEqual(cat.get("epoc").description, "oxygen debt")
        self.assertEqual(stub.calls, 1)                      # whole doc, all residual columns, one call

    def test_whole_doc_mode_reads_a_column_whose_name_never_appears(self):
        # Retrieval by token would miss this — the passage never says 'epoc'. Whole-doc
        # mode hands the reader the full text + all residual columns, so it still resolves.
        doc = self._doc("hard.txt", "Excess post-exercise oxygen consumption was recorded per fish.\n")
        self.assertNotIn("epoc", doc.read_text("hard").lower())
        stub = _StubLLM({"epoc": {"description": "excess post-exercise oxygen consumption",
                                  "units": "mg O2 kg-1 h-1"}})
        cat = resolve_catalog(self.tab, sources=[doc], prose_reader=LLMProseReader(stub))
        self.assertEqual(cat.get("epoc").link_method, "prose_read")
        self.assertEqual(cat.get("epoc").units, "mg O2 kg-1 h-1")

    def test_value_profile_still_referees_an_llm_read(self):
        # The LLM claims a column is latitude; its values (100–200) refute it, so the
        # read is accepted but flagged and demoted — grounding survives the reader swap.
        pd.DataFrame({"depth": [100.0, 150.0, 200.0]}).to_csv(
            os.path.join(self.dir, "d.csv"), index=False
        )
        tab = create_context(os.path.join(self.dir, "d.csv"), name="d")
        doc = self._doc("hard.txt", "depth is described somewhere in this document.\n")
        stub = _StubLLM({"depth": {"description": "latitude", "units": "degrees"}})
        col = resolve_catalog(tab, sources=[doc], prose_reader=LLMProseReader(stub)).get("depth")
        self.assertEqual(col.link_method, "prose_read")
        self.assertEqual(col.link_confidence, "low")         # values refute the claim
        self.assertTrue(any("latitude" in m for m in col.conflicts))

    # --- batched, cached call shape (cost control for an expensive reader) ---

    def test_reader_reads_all_residual_columns_in_one_call(self):
        # Two columns defined in narrative; both residual. In the whole-doc (short) path
        # the reader is handed the doc once, covering both columns.
        doc = self._doc(
            "m.md",
            "# M\n\nWet body mass (mass) was recorded. Oxygen debt (epoc) was logged.\n",
        )
        spy = _CountingReader()
        cat = resolve_catalog(self.tab, sources=[doc], prose_reader=spy)
        self.assertEqual(len(spy.calls), 1)                  # one document → one call
        self.assertEqual(set(spy.calls[0][0]), {"mass", "epoc"})   # both columns batched
        self.assertEqual(cat.get("mass").link_method, "prose_read")
        self.assertEqual(cat.get("epoc").link_method, "prose_read")

    def test_cached_reader_reads_each_chunk_once_including_negatives(self):
        spy = _CountingReader()
        cached = CachedProseReader(spy)
        chunk = "Wet body mass (mass) was recorded."
        # 'mass' resolves; 'foo' abstains — the cache must remember *both*.
        first = cached.read_many(columns=[("mass", "float"), ("foo", "float")], chunk=chunk)
        self.assertEqual(first["mass"].description, "wet body mass")
        self.assertNotIn("foo", first)
        self.assertEqual(len(spy.calls), 1)
        # An identical second call is served entirely from cache — including the
        # negative for 'foo', so no fresh inner read happens.
        second = cached.read_many(columns=[("mass", "float"), ("foo", "float")], chunk=chunk)
        self.assertEqual(len(spy.calls), 1)                  # inner not called again
        self.assertEqual(second["mass"].description, "wet body mass")

    def test_bundle_hoist_reads_the_doc_once_across_tables(self):
        # Two tables each have a residual 'mass' (defined in narrative). The bundle-level
        # hoist unions them into ONE pass over the shared doc, so the doc is read once for
        # the whole bundle — even without a cache.
        pd.DataFrame({"mass": [1.0, 2.0]}).to_csv(os.path.join(self.dir, "t1.csv"), index=False)
        pd.DataFrame({"mass": [3.0, 4.0]}).to_csv(os.path.join(self.dir, "t2.csv"), index=False)
        t1 = create_context(os.path.join(self.dir, "t1.csv"), name="t1")
        t2 = create_context(os.path.join(self.dir, "t2.csv"), name="t2")
        doc = self._doc("m.md", "# M\n\nWet body mass (mass) was measured in grams.\n")
        spy = _CountingReader()  # uncached: the hoist alone gives the single call
        cat = resolve_bundle([t1, t2], sources=[doc], prose_reader=spy)
        self.assertEqual(len(spy.calls), 1)                  # hoisted union, one chunk
        self.assertEqual(cat.find("mass", "t1").description, "wet body mass")
        self.assertEqual(cat.find("mass", "t2").description, "wet body mass")


if __name__ == "__main__":
    unittest.main()
