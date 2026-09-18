"""Tests for the LLM role registry (src/llm_roles.py).

The registry is only worth having if it cannot fall behind the code, so the load-bearing
test is the one that fails when a role is added and not registered.
"""

import importlib
import inspect
import os
import pkgutil
import sys
import unittest
from string import Formatter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "docs", "_ext")))

import src.router
from src.config import LLM_MODULES
from src.llm_roles import ROLES, role, roles


def _llm_classes() -> set:
    """Every ``LLM*`` class the router package defines — one per single-call role."""
    found = set()
    for info in pkgutil.iter_modules(src.router.__path__):
        module = importlib.import_module(f"src.router.{info.name}")
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and name.startswith("LLM") and obj.__module__ == module.__name__:
                found.add(f"{obj.__module__}.{name}")
    return found


class RegistryTest(unittest.TestCase):
    def test_every_llm_class_in_the_router_is_registered(self):
        """A role nobody registered is a prompt nobody can see."""
        self.assertEqual(_llm_classes(), {r.implemented_by for r in ROLES})

    def test_each_role_names_a_real_model_setting(self):
        for r in ROLES:
            with self.subTest(role=r.key):
                self.assertIn(r.module, LLM_MODULES)
                self.assertEqual(r.settings_label, LLM_MODULES[r.module].label)

    def test_keys_are_unique_and_addressable(self):
        keys = [r.key for r in ROLES]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(role("column_matcher").title, "Column matcher")

    def test_roles_can_be_read_per_model_setting(self):
        self.assertEqual(
            [r.key for r in roles("CANDIDATE_JUDGE")],
            ["tool_matcher", "column_matcher", "passage_reader"],
        )
        self.assertEqual(len(roles()), len(ROLES))

    def test_every_role_states_its_prompt_its_calls_and_its_referee(self):
        for r in ROLES:
            with self.subTest(role=r.key):
                self.assertTrue(r.prompt.strip())
                self.assertTrue(r.decides.strip() and r.calls.strip() and r.checked.strip())
                # The prompt is a str.format template; a stray brace would crash the call.
                fields = [name for _, name, _, _ in Formatter().parse(r.prompt) if name]
                self.assertTrue(fields, "a prompt with no placeholders takes no input")


class PromptDocsTest(unittest.TestCase):
    """The generated page publishes every registered prompt."""

    def test_each_role_gets_a_section_with_its_prompt(self):
        import promptdocs

        page = "\n".join(promptdocs._single_call_sections())
        for r in ROLES:
            with self.subTest(role=r.key):
                self.assertIn(f"### `{r.key}`", page)
                self.assertIn(r.implemented_by, page)
                self.assertIn(r.prompt.splitlines()[0].replace("{{", "{"), page)


if __name__ == "__main__":
    unittest.main()
