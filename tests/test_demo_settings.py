"""Tests for the demo's pipeline settings (see demo/settings.py).

The settings panel is only useful if a choice made in it reaches the stage it names,
and the stages are reached three different ways: the generation workflow runs in
another process and configures itself from the environment, while the module pages
call an example's ``run()`` directly and configure it with flags. So the tests that
matter here are the translations — settings to environment, settings to arguments —
and the guarantee that neither leaks into the next run.
"""

import argparse
import os
import sys
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from demo.components.arg_form import Defaults
from demo.settings import PipelineSettings, defaults
from demo.workflows.metadata_generation import _configured
from src.cli import resolve as resolve_catalog
from src.cli import route as field_router_plan
from src.config import LLMSettings, llm_settings, player_max_tool_iterations


@contextmanager
def environment(**overrides):
    """Run the block with ``overrides`` set, then restore what was there."""
    previous = {name: os.environ.get(name) for name in overrides}
    os.environ.update({name: str(value) for name, value in overrides.items()})
    try:
        yield
    finally:
        for name, value in previous.items():
            os.environ.pop(name, None) if value is None else os.environ.update(
                {name: value}
            )


def settings(**overrides) -> PipelineSettings:
    """Settings naming a different model per module, so mix-ups are visible."""
    base = {
        "models": {
            "PLANNING": LLMSettings("openai", "planner-model", 0.1),
            "PLAYER": LLMSettings("openai", "player-model", 0.4),
            "CATALOG_RESOLVER": LLMSettings("google", "catalog-model", 0.2),
            "CANDIDATE_JUDGE": LLMSettings("openai", "judge-model", 0.3),
        },
        "topology": "fast",
    }
    return PipelineSettings(**{**base, **overrides})


class TestEnvironment(unittest.TestCase):
    """The settings a spawned generation run is configured by."""

    def test_each_module_resolves_to_its_own_model(self):
        with environment(**settings().environment()):
            self.assertEqual(llm_settings("PLANNING").model, "planner-model")
            self.assertEqual(llm_settings("PLAYER").temperature, 0.4)
            self.assertEqual(llm_settings("CATALOG_RESOLVER").provider, "google")
            self.assertEqual(llm_settings("CANDIDATE_JUDGE").model, "judge-model")

    def test_player_tool_budget_is_carried(self):
        chosen = settings(player_tool_mode="survey", player_tool_iterations=3)
        with environment(**chosen.environment()):
            self.assertEqual(player_max_tool_iterations(), 3)
            self.assertEqual(os.environ["PLAYER_TOOL_EXECUTION_MODE"], "survey")

    def test_a_run_does_not_outlive_itself(self):
        """The workflow's overrides are in force for the call and no longer."""
        before = os.environ.get("LLM_MODEL_PLANNING")
        with _configured(settings().environment()):
            self.assertEqual(os.environ["LLM_MODEL_PLANNING"], "planner-model")
        self.assertEqual(os.environ.get("LLM_MODEL_PLANNING"), before)

    def test_defaults_follow_the_environment(self):
        with environment(LLM_MODEL_PLANNING="from-env", PLAYER_MAX_TOOL_ITERATIONS=2):
            configured = defaults()
        self.assertEqual(configured.model_for("PLANNING").model, "from-env")
        self.assertEqual(configured.player_tool_iterations, 2)


class TestExampleArguments(unittest.TestCase):
    """The settings a module page's form starts from.

    Each mapping is checked by *parsing* it, so an argument renamed in an example
    fails here rather than silently ceasing to be configurable.
    """

    def parse(
        self, parser: argparse.ArgumentParser, arguments: dict, required: tuple = ()
    ) -> argparse.Namespace:
        """The example's defaults, overridden by ``arguments``, as it would see them.

        ``required`` supplies the command-line arguments the parser insists on.
        """
        namespace = parser.parse_args(list(required))
        for dest, value in arguments.items():
            self.assertTrue(
                hasattr(namespace, dest), f"no argument named {dest!r}"
            )
            setattr(namespace, dest, value)
        return namespace

    def test_catalog_tiers_pick_one_reader(self):
        parser = resolve_catalog.build_parser()
        for tier, expected in (
            ("off", False),
            ("llm", True),
        ):
            with self.subTest(tier=tier):
                chosen = settings(catalog_prose_tier=tier)
                args = self.parse(parser, chosen.catalog_arguments())
                self.assertEqual(args.llm_reader, expected)
                self.assertEqual(args.model, "catalog-model")
                self.assertEqual(args.provider, "google")

    def test_catalog_debug_needs_the_llm_tier(self):
        """Prompt logging is a property of the LLM reader; without it, nothing logs."""
        chosen = settings(catalog_prose_tier="off", catalog_debug=True)
        self.assertFalse(chosen.catalog_arguments()["debug"])

    def test_router_carries_only_its_own_stage(self):
        chosen = settings(
            catalog_prose_tier="llm",
            router_candidates=8,
            router_candidate_judge=True,
            router_judge_workers=4,
            router_judge_batch=False,
            router_refresh_tool_cache=True,
        )
        args = self.parse(
            field_router_plan.build_parser(), chosen.router_arguments(),
            required=("--catalog", "catalog.json"),
        )
        # Layer 3 is the resolver page's: the router routes its catalog, not its settings.
        self.assertFalse(hasattr(args, "llm_reader"))
        # Layer 4b: the candidate judge, its model, and how its calls are issued.
        self.assertTrue(args.llm_candidate_judge)
        self.assertEqual(args.model, "judge-model")
        self.assertEqual(args.candidates, 8)
        self.assertEqual(args.judge_workers, 4)
        self.assertTrue(args.no_judge_batch)
        self.assertTrue(args.refresh_tool_cache)

    def test_batching_is_stated_positively(self):
        """The panel offers batching; the example takes its negation."""
        chosen = settings(router_judge_batch=True)
        self.assertFalse(chosen.router_arguments()["no_judge_batch"])


class TestToken(unittest.TestCase):
    """The digest that keys cached results and the widgets showing these values."""

    def test_same_settings_same_token(self):
        self.assertEqual(settings().token(), settings().token())

    def test_summary_names_the_model_stages_that_are_on(self):
        """The collapsed panel's header; rendered on every load of the landing page."""
        self.assertNotIn("judge", settings().summary())
        on = settings(catalog_prose_tier="llm", router_candidate_judge=True).summary()
        self.assertIn("catalog prose: llm", on)
        self.assertIn("candidate judge: on", on)

    def test_any_change_is_visible(self):
        baseline = settings().token()
        for change in (
            {"topology": "thorough"},
            {"catalog_prose_tier": "llm"},
            {"router_candidates": 7},
            {"router_refresh_tool_cache": True},
            {"models": {"PLANNING": LLMSettings("openai", "other-model", 0.1)}},
        ):
            with self.subTest(**change):
                self.assertNotEqual(settings(**change).token(), baseline)


class TestFormDefaults(unittest.TestCase):
    """Widgets must be new widgets once the answer behind them changes."""

    def test_only_overridden_arguments_are_versioned(self):
        form = Defaults({"provider": "openai"}, token="abc")
        self.assertEqual(form.key("catalog", "provider"), "catalog.abc.provider")
        self.assertEqual(form.key("catalog", "bundle"), "catalog.bundle")

    def test_an_argument_left_alone_keeps_its_own_default(self):
        action = resolve_catalog.build_parser()._actions[-1]
        self.assertEqual(Defaults().default_for(action), action.default)


def _panel():
    """The settings panel alone, as a script AppTest can run."""
    import sys
    sys.path.insert(0, ".")
    from demo import settings
    settings.render()


class TestOverviewSync(unittest.TestCase):
    """The overview and a module's tab are two views of one set of values."""

    def setUp(self):
        from streamlit.testing.v1 import AppTest

        self.app = AppTest.from_function(_panel, default_timeout=60)
        self.app.run()

    def value(self, kind, key):
        return getattr(self.app, kind)(key=key).value

    def test_a_change_in_the_overview_shows_in_the_module_tab(self):
        self.app.number_input(key="pipeline.routing.candidates@overview").set_value(9).run()
        self.assertEqual(self.value("number_input", "pipeline.routing.candidates@module"), 9)
        self.assertEqual(self.app.session_state["pipeline.settings"].router_candidates, 9)

    def test_a_change_in_the_module_tab_shows_in_the_overview(self):
        self.app.checkbox(key="pipeline.routing.candidate_judge@module").check().run()
        self.assertTrue(self.value("checkbox", "pipeline.routing.candidate_judge@overview"))
        # The judge's model follows in both views, not just the one clicked.
        for view in ("overview", "module"):
            self.assertFalse(
                self.app.selectbox(key=f"pipeline.candidate_judge.provider@{view}").disabled
            )

    def test_no_warning_about_defaults_and_session_state(self):
        self.app.text_input(key="pipeline.planning.model@module").set_value("m").run()
        self.assertEqual(list(self.app.exception), [])
        self.assertEqual(list(self.app.warning), [])


if __name__ == "__main__":
    unittest.main()
