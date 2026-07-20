"""Tests for the player's two-phase tool execution (survey, then investigate)."""

import os
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import pandas as pd
from langchain_core.messages import AIMessage

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.context.context_factory import create_context
from src.players.configs import PLAYER_CONFIGS
from src.players.player import create_player_from_config, tools_for_role
from src.standards import get_schema_for_standard
from src.tools.base import clear_registry, register_context

DummyMetadata = get_schema_for_standard("dummy_standard")


class ScriptedLLM:
    """Replays a scripted sequence of model responses and records tool bindings."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.bound_tools = None
        self.invocations = 0

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, _messages):
        self.invocations += 1
        if self._responses:
            return self._responses.pop(0)
        return AIMessage(content="done")


class NoToolsLLM:
    """A provider that cannot do tool calling."""

    def bind_tools(self, tools):
        raise NotImplementedError("provider has no tool-calling support")


class PlayerDispatchTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.csv = os.path.join(self.dir, "obs.csv")
        pd.DataFrame(
            {
                "id": [1, 2, 3],
                "lat": [54.1, 54.2, 54.3],
                "lon": [-7.8, -7.9, -7.7],
                "observed_at": ["2024-01-01", "2024-02-01", "2024-03-01"],
            }
        ).to_csv(self.csv, index=False)

        self.ctx = create_context(self.csv, name="obs_ctx")
        self.key = register_context(f"k_{uuid.uuid4().hex[:8]}", self.ctx)

    def tearDown(self):
        clear_registry()

    def _player(self, role, llm):
        with patch("src.players.player.create_llm", return_value=llm):
            return create_player_from_config(
                PLAYER_CONFIGS[role], name=f"{role}_1", context=self.ctx, role_key=role
            )


class TestSurveyPhase(PlayerDispatchTestBase):
    def test_survey_runs_auto_fireable_tools_per_resource(self):
        player = self._player("data_analyst", ScriptedLLM([]))
        results = player._survey(self.key, ["obs"])

        self.assertIn("obs:get_field_names", results)
        self.assertEqual(results["obs:get_field_names"], ["id", "lat", "lon", "observed_at"])
        self.assertIn("get_context_overview", results)  # context-scoped, not prefixed

    def test_survey_skips_tools_needing_model_supplied_args(self):
        player = self._player("data_analyst", ScriptedLLM([]))
        results = player._survey(self.key, ["obs"])

        surveyed = " ".join(results)
        self.assertNotIn("get_unique_values", surveyed)

    def test_detection_tools_run_in_survey(self):
        """Regression: these used to be misrouted as context-level tools."""
        player = self._player("spatial_temporal_specialist", ScriptedLLM([]))
        results = player._survey(self.key, ["obs"])

        self.assertIn("obs:detect_spatial_columns", results)
        self.assertIn("obs:detect_temporal_columns", results)
        self.assertEqual(results["obs:detect_temporal_columns"]["temporal_column_count"], 1)


class TestInvestigatePhase(PlayerDispatchTestBase):
    def _tool_call(self, name, args, call_id="c1"):
        return AIMessage(
            content="",
            tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
        )

    def test_model_can_call_a_parameterized_tool(self):
        """The whole point: get_spatial_extent needs column names only a model can pick."""
        llm = ScriptedLLM([
            self._tool_call(
                "get_spatial_extent",
                {"resource": "obs", "lat_column": "lat", "lon_column": "lon"},
            ),
            AIMessage(content="done"),
        ])
        player = self._player("spatial_temporal_specialist", llm)
        investigable = [t for t in player.tools if t.name == "get_spatial_extent"]

        results = player._investigate("find extent", self.key, {}, investigable)

        key = next(k for k in results if k.startswith("get_spatial_extent"))
        self.assertAlmostEqual(results[key]["bounding_box"]["min_lat"], 54.1)
        self.assertAlmostEqual(results[key]["bounding_box"]["max_lon"], -7.7)

    def test_context_key_from_model_is_overridden(self):
        """The runner owns the context key; a hallucinated one must not be used."""
        llm = ScriptedLLM([
            self._tool_call(
                "get_spatial_extent",
                {
                    "context_key": "hallucinated_key",
                    "resource": "obs",
                    "lat_column": "lat",
                    "lon_column": "lon",
                },
            ),
            AIMessage(content="done"),
        ])
        player = self._player("spatial_temporal_specialist", llm)
        investigable = [t for t in player.tools if t.name == "get_spatial_extent"]

        results = player._investigate("find extent", self.key, {}, investigable)
        key = next(k for k in results if k.startswith("get_spatial_extent"))
        self.assertIn("bounding_box", results[key])

    def test_tool_errors_are_captured_not_raised(self):
        llm = ScriptedLLM([
            self._tool_call(
                "get_spatial_extent",
                {"resource": "obs", "lat_column": "nope", "lon_column": "lon"},
            ),
            AIMessage(content="done"),
        ])
        player = self._player("spatial_temporal_specialist", llm)
        investigable = [t for t in player.tools if t.name == "get_spatial_extent"]

        results = player._investigate("find extent", self.key, {}, investigable)
        key = next(k for k in results if k.startswith("get_spatial_extent"))
        self.assertIn("Error", str(results[key]))

    def test_loop_stops_when_model_stops_calling_tools(self):
        llm = ScriptedLLM([AIMessage(content="nothing to do")])
        player = self._player("spatial_temporal_specialist", llm)

        results = player._investigate("x", self.key, {}, list(player.tools))
        self.assertEqual(results, {})
        self.assertEqual(llm.invocations, 1)

    def test_provider_without_tool_calling_degrades_gracefully(self):
        player = self._player("spatial_temporal_specialist", NoToolsLLM())
        results = player._investigate("x", self.key, {}, list(player.tools))
        self.assertEqual(results, {})


class TestSynthesis(PlayerDispatchTestBase):
    def test_single_player_string_synthesis_is_verbatim_and_llm_free(self):
        llm = ScriptedLLM([])
        player = self._player("metadata_specialist", llm)

        with patch.object(player, "_synthesize_string") as string_synth:
            result = player.synthesize_results(
                task="summarize",
                all_results=[{"player": "p", "analysis": "THE RESULT", "tool_results": {}}],
                output_schema=None,
            )

        self.assertEqual(result, "THE RESULT")   # the player's analysis, verbatim
        string_synth.assert_not_called()         # no model synthesis
        self.assertEqual(llm.invocations, 0)     # no model round-trip at all

    def test_multiple_players_still_synthesize_via_model(self):
        """The passthrough is narrow: >1 result still goes through the model."""
        player = self._player("metadata_specialist", ScriptedLLM([]))

        with patch.object(player, "_synthesize_string", return_value="merged") as string_synth:
            result = player.synthesize_results(
                task="summarize",
                all_results=[
                    {"player": "a", "analysis": "one", "tool_results": {}},
                    {"player": "b", "analysis": "two", "tool_results": {}},
                ],
                output_schema=None,
            )

        self.assertEqual(result, "merged")
        string_synth.assert_called_once()

    def test_single_player_structured_output_still_uses_model(self):
        """Structured output is excluded: mapping onto a schema is not a no-op."""
        player = self._player("metadata_specialist", ScriptedLLM([]))

        with patch.object(player, "_synthesize_structured", return_value="structured") as struct_synth:
            result = player.synthesize_results(
                task="summarize",
                all_results=[{"player": "p", "analysis": "THE RESULT", "tool_results": {}}],
                output_schema=DummyMetadata,
            )

        self.assertEqual(result, "structured")
        struct_synth.assert_called_once()


class TestToolsetResolutionForRoles(PlayerDispatchTestBase):
    def test_role_only_receives_tools_the_context_serves(self):
        doc = os.path.join(self.dir, "d.md")
        with open(doc, "w") as f:
            f.write("# T\n\nbody\n")
        text_ctx = create_context(doc, name="text_ctx")

        on_csv = {t.name for t in tools_for_role(PLAYER_CONFIGS["data_analyst"], self.ctx)}
        on_text = {t.name for t in tools_for_role(PLAYER_CONFIGS["data_analyst"], text_ctx)}

        self.assertIn("get_field_statistics", on_csv)
        self.assertNotIn("get_field_statistics", on_text)
        self.assertIn("get_context_overview", on_text)

    def test_spatial_specialist_now_holds_its_analysis_tools(self):
        """These were unreachable under the old name-keyword dispatcher."""
        tools = {t.name for t in tools_for_role(
            PLAYER_CONFIGS["spatial_temporal_specialist"], self.ctx
        )}
        for name in (
            "analyze_spatial_column",
            "analyze_temporal_column",
            "get_spatial_extent",
            "get_temporal_extent",
        ):
            self.assertIn(name, tools)

    def test_critic_has_no_tools(self):
        self.assertEqual(tools_for_role(PLAYER_CONFIGS["critic"], self.ctx), [])


if __name__ == "__main__":
    unittest.main()
