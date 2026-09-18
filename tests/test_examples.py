"""The examples are documentation that runs, so they are held to running.

Both drive the deterministic path — no model, no credentials — so a broken import or a
renamed artifact field fails here rather than in front of a reader.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from examples import describe_columns, route_with_your_own_judge
from src.tools.base import clear_registry


class ExampleTest(unittest.TestCase):
    def tearDown(self):
        clear_registry()

    def _output(self, main) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main()
        return buffer.getvalue()

    def test_describe_columns_prints_the_resolution_its_evidence_and_the_payoff(self):
        output = self._output(describe_columns.main)
        self.assertIn("Acclimation pH treatment level", output)   # a resolved meaning
        self.assertIn("codebook row 'pH'", output)                # the citation behind it
        self.assertIn("ucrit", output)                            # reached by enriched search
        self.assertIn("text_codebook", output)                    # the glossary tier
        self.assertIn("conflict(s) recorded", output)

    def test_routing_with_a_hand_written_judge_needs_no_model(self):
        output = self._output(route_with_your_own_judge.main)
        self.assertIn("routed", output)
        self.assertIn("Answered by the name matcher:", output)
        self.assertIn("[passage reader] call 1", output)  # the stub, not a model


if __name__ == "__main__":
    unittest.main()
