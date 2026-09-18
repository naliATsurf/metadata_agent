"""Tests for the router page's routing table (demo/components/router_view.py).

With the judges on a candidate is an answer, not a ranked proposal, so the table states
the answer and what to distrust about it. These check the rows that table is built from,
and that both shapes render.
"""

import os
import sys
import unittest

from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from demo.components.router_view import (
    ANSWER_COLUMNS,
    _answer_rows,
    _checks,
)
from src.context.base_context import EvidenceRef
from src.router.route import FieldPlan, FieldRouting


def _ref(resource, locator, kind, snippet=""):
    return EvidenceRef(resource=resource, locator=locator, kind=kind, snippet=snippet, score=0.0)


def _routing(path="f", bucket="column", **values) -> FieldRouting:
    return FieldRouting(field_path=path, query=f"the {path}", bucket=bucket, **values)


def _row(routing) -> dict:
    return _answer_rows(routing.field_path, routing, list(ANSWER_COLUMNS))[0]


class AnswerRowTest(unittest.TestCase):
    def test_a_column_answer_names_its_column_and_counts_the_other_tables(self):
        row = _row(_routing(
            "pH", bucket="column", assurance="high",
            candidates=[_ref("growth", "pH", "computed_column", "pH: acclimation pH"),
                        _ref("swim", "pH", "computed_column", "pH: acclimation pH")],
            judge_note="the only pH treatment column",
            varies=["growth::pH — pH holds 2 different values"],
        ))
        self.assertEqual(row["Answer from"], "growth:pH (+1 more table)")
        self.assertEqual(row["Evidence"], "pH: acclimation pH")
        self.assertEqual(row["Why"], "the only pH treatment column")
        self.assertIn("varies", row["Checks"])

    def test_a_tool_answer_shows_the_table_and_columns_it_runs_on(self):
        row = _row(_routing(
            "period", bucket="tool", assurance="high",
            candidates=[_ref("growth", "get_temporal_extent", "tool", "date range")],
            tool_arguments=[{"resource": "growth", "time_column": "date"}],
        ))
        self.assertEqual(row["Answer from"], "get_temporal_extent on growth (time_column=date)")

    def test_a_document_answer_shows_its_citation_and_quote(self):
        row = _row(_routing(
            "title", bucket="document", assurance="low",
            candidates=[_ref("readme", (0, 20), "quoted_span", "The title is Foo.")],
            judge_quotes=["The title is Foo.", "Cite as Foo."],
            citations=["readme#0-17", "readme#40-52"],
        ))
        self.assertEqual(row["Answer from"], "readme#0-17 (+1 more citation)")
        self.assertEqual(row["Evidence"], "The title is Foo. (+1 more quote)")

    def test_an_unanswered_field_shows_why_and_nothing_else(self):
        row = _row(_routing(
            "family_name", bucket="unanswered", status="unanswered",
            judge_note="no column or passage the judges were shown answers this field",
        ))
        self.assertEqual(row["Answer from"], "")
        self.assertEqual(row["Evidence"], "")
        self.assertIn("no column or passage", row["Why"])


class ChecksTest(unittest.TestCase):
    def test_every_reason_to_look_closer_is_listed(self):
        routing = _routing(
            "duration", bucket="column",
            candidates=[_ref("growth", "days", "computed_column"),
                        _ref("growth", "get_item_count", "tool")],
            mismatches=["growth::days — field asks for a name; days holds numbers"],
            judge_grounded=False,
            tool_note="counts rows — not run: no table chosen for resource",
        )
        self.assertEqual(_checks(routing), [
            "does not fit: growth::days — field asks for a name; days holds numbers",
            "quote not located",
            "column and tool disagree",
            "tool dropped",
        ])

    def test_a_clean_answer_has_no_checks(self):
        self.assertEqual(_checks(_routing(candidates=[_ref("g", "pH", "computed_column")])), [])


class RenderTest(unittest.TestCase):
    """Both shapes render headlessly, with the columns each is for."""

    def _plan(self, judged):
        routings = {
            "pH": _routing("pH", assurance="high",
                           candidates=[_ref("growth", "pH", "computed_column", "pH: acclimation pH")],
                           judge_note="the pH treatment column" if judged else None),
            "family_name": _routing("family_name", bucket="unanswered", status="unanswered",
                                    judge_note="nothing answers it" if judged else None),
        }
        return FieldPlan(schema_name="Meta", routings=routings, judged=judged)

    def _render(self, judged):
        def page(plan):
            from demo.components.router_view import _render_routings
            _render_routings(plan, key="t")

        at = AppTest.from_function(page, args=(self._plan(judged),), default_timeout=30).run()
        self.assertFalse(at.exception)
        return at

    def test_a_judged_plan_reads_one_row_per_field(self):
        at = self._render(judged=True)
        table = at.dataframe[0].value
        self.assertEqual(list(table["Field"]), ["pH", "family_name"])
        self.assertIn("Why", table)
        self.assertNotIn("Score", table)            # BM25 scored nothing
        self.assertEqual(at.segmented_control, [])  # no view to choose

    def test_the_working_waits_for_a_selected_field(self):
        at = self._render(judged=True)
        self.assertTrue(any("Select a field" in c.value for c in at.caption))

    def test_an_unjudged_plan_keeps_the_ranked_candidates(self):
        at = self._render(judged=False)
        table = at.dataframe[0].value
        self.assertIn("Score", table)
        self.assertEqual(at.segmented_control[0].value, "Candidates")


if __name__ == "__main__":
    unittest.main()
