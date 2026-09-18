"""Tests for the shipped command line (src/cli/).

The commands are the surface three callers share — the terminal, the app's forms and the
eval harness — so what is tested here is the shape they all rely on, and that the
generated reference cannot fall behind it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "docs", "_ext")))

from src.cli import COMMANDS, main, usage


class CommandSurfaceTest(unittest.TestCase):
    def test_every_command_exposes_a_parser_and_an_entry_point(self):
        """A form can be built from any command, and the terminal can run it."""
        for name, module in COMMANDS.items():
            with self.subTest(command=name):
                parser = module.build_parser()
                self.assertTrue(parser.description)
                self.assertTrue(callable(module.main))
                self.assertEqual(parser.prog, f"metadata-agent {name}")

    def test_usage_names_every_command(self):
        for name in COMMANDS:
            self.assertIn(name, usage())

    def test_an_unknown_command_prints_the_usage_and_fails(self):
        with self.assertRaises(SystemExit) as caught:
            main(["nonsense"])
        self.assertEqual(caught.exception.code, 2)


class ReferenceTest(unittest.TestCase):
    """The generated page is read off the parsers, so it cannot drift from them."""

    def setUp(self):
        import clidocs

        self.page = "\n".join(
            line
            for name, module in COMMANDS.items()
            for line in clidocs._command_section(name, module)
        )

    def test_every_flag_of_every_command_is_documented(self):
        for name, module in COMMANDS.items():
            for action in module.build_parser()._actions:
                for flag in action.option_strings:
                    if flag in ("-h", "--help"):
                        continue
                    with self.subTest(command=name, flag=flag):
                        self.assertIn(f"`{flag}`", self.page)

    def test_a_required_flag_and_a_choice_list_are_visible(self):
        self.assertIn("**required**", self.page)              # route --catalog
        self.assertIn("`sharetrait_basic_no_trait`", self.page)  # --standard choices


if __name__ == "__main__":
    unittest.main()
