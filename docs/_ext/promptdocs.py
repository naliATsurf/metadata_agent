"""Sphinx extension: generate the prompt reference page from the live sources.

Prompt text in this repo lives in three places, and this extension pulls all
three into ``docs/prompts.md`` at build time so the page cannot drift:

``src/orchestrator/prompts.py``
    Zero-argument ``get_*_prompt`` factories returning ``ChatPromptTemplate``.
    Imported and introspected -- placeholders come from ``input_variables``.
``src/players/player.py``
    Prompts built inline inside methods from f-strings. These cannot be
    imported and called, so they are extracted statically with :mod:`ast`;
    interpolations render as ``{expr}``.
``src/players/configs.py``
    ``role_prompt`` personas, interpolated into every player prompt above.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

DOCS = Path(__file__).resolve().parents[1]
ROOT = DOCS.parent
OUTPUT = DOCS / "prompts.md"

PLAYER_SOURCE = ROOT / "src" / "players" / "player.py"

#: Prompts reachable from the orchestrator, and what selects them.
SELECTED_BY = {
    "get_single_csv_planning_prompt": (
        "`Orchestrator._get_planning_chain` when the classified context type "
        "is anything other than `MULTI_CSV`"
    ),
    "get_multi_csv_planning_prompt": (
        "`Orchestrator._get_planning_chain` when the classified context type "
        "is `MULTI_CSV`"
    ),
}


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def _assert_renderable(name: str, role: str, text: str) -> None:
    """Fail the build if a prompt contains markup that mangles the page.

    Message bodies are emitted as live markdown, so a heading added to a
    prompt would silently show up in the page's table of contents.
    """
    offenders = [line for line in text.splitlines() if line.lstrip().startswith("#")]
    if offenders:
        raise ValueError(
            f"{name} ({role} message) contains a markdown heading, which would "
            f"leak into the docs table of contents: {offenders[0]!r}. Reword it "
            f"as bold text, or switch this page back to literal blocks."
        )


def _section(name: str, messages, *, subtitle="", note="", variables=None) -> str:
    """Render one prompt: heading, provenance, placeholders, message bodies.

    Prompts sit at ``h3`` under their group's ``h2``; message bodies at ``h4``.
    """
    out = [f"### `{name}`", ""]
    if subtitle:
        out += [subtitle, ""]
    if note:
        out += [note, ""]

    if variables is not None:
        out += ["**Placeholders**", ""]
        out += [f"- `{{{v}}}`" for v in sorted(variables)] if variables else ["*(none)*"]
        out += [""]

    for role, text in messages:
        _assert_renderable(name, role, text)
        out += [f"#### {role.capitalize()} message", "", text, ""]
    return "\n".join(out)


# --------------------------------------------------------------------------
# orchestrator prompts: imported and introspected
# --------------------------------------------------------------------------

def _template_messages(template: ChatPromptTemplate):
    """Yield ``(role, raw_template_text)`` for each message in the template."""
    for message in template.messages:
        role = getattr(message, "role", None) or type(message).__name__.replace(
            "MessagePromptTemplate", ""
        ).lower()
        # ``.template`` keeps the ``{{``/``}}`` escaping ``str.format`` needs;
        # undo it so the page shows what the model actually receives.
        yield role, message.prompt.template.replace("{{", "{").replace("}}", "}")


def _orchestrator_sections() -> list[str]:
    from src.orchestrator import prompts as prompt_module

    sections = []
    for name, factory in vars(prompt_module).items():
        if not (name.startswith("get_") and name.endswith("_prompt")):
            continue
        if not callable(factory):
            continue

        template = factory()
        headline = (inspect.getdoc(factory) or "").strip().splitlines()
        selected = SELECTED_BY.get(name)
        sections.append(
            _section(
                name,
                _template_messages(template),
                subtitle=headline[0] if headline else "",
                note=(
                    f"**Selected by:** {selected}"
                    if selected
                    else "**Selected by:** *not referenced anywhere in the codebase.*"
                ),
                variables=template.input_variables,
            )
        )
    return sections


# --------------------------------------------------------------------------
# player prompts: extracted statically from the source
# --------------------------------------------------------------------------

def _literal(node: ast.AST) -> str | None:
    """Render a string literal or f-string node as prompt text.

    Interpolations become ``{expr}`` so the placeholder is visible, matching
    how the orchestrator templates display theirs.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{" + ast.unparse(value.value) + "}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _placeholders(text: str) -> list[str]:
    """The ``{expr}`` interpolations appearing in an extracted prompt."""
    found, depth, current = [], 0, ""
    for char in text:
        if char == "{":
            depth += 1
            if depth == 1:
                current = ""
                continue
        if char == "}" and depth:
            depth -= 1
            if depth == 0 and current:
                found.append(current)
            continue
        if depth:
            current += char
    return sorted(set(found))


def _player_sections() -> list[str]:
    """Extract every inline prompt in ``player.py`` via static analysis."""
    tree = ast.parse(PLAYER_SOURCE.read_text())

    # Map each node to its enclosing method so prompts can be named.
    enclosing = {}
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(func):
                enclosing.setdefault(child, func.name)

    sections = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        messages = []
        # ChatPromptTemplate.from_messages([("system", ...), ("human", ...)])
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "from_messages"
            and node.args
            and isinstance(node.args[0], (ast.List, ast.Tuple))
        ):
            for element in node.args[0].elts:
                if isinstance(element, ast.Tuple) and len(element.elts) == 2:
                    role = _literal(element.elts[0])
                    text = _literal(element.elts[1])
                    if role and text:
                        messages.append((role, text))

        # Bare SystemMessage(content=...) used by the tool-calling loop.
        elif isinstance(node.func, ast.Name) and node.func.id == "SystemMessage":
            for keyword in node.keywords:
                if keyword.arg == "content":
                    text = _literal(keyword.value)
                    if text:
                        messages.append(("system", text))

        if not messages:
            continue

        method = enclosing.get(node, "<module>")
        variables = sorted({v for _, text in messages for v in _placeholders(text)})
        sections.append(
            _section(
                f"Player.{method}",
                messages,
                subtitle=f"Built inline in `src/players/player.py`, line {node.lineno}.",
                note=(
                    "**Selected by:** called on every player instance during "
                    f"`{method}`. Extracted statically; interpolations show as "
                    "`{expr}`."
                ),
                variables=variables,
            )
        )
    return sections


# --------------------------------------------------------------------------
# role personas
# --------------------------------------------------------------------------

def _role_section() -> str:
    """Document the personas substituted into every ``{self.role_prompt}``."""
    from src.players.configs import PLAYER_CONFIGS

    out = [
        "## Role personas",
        "",
        "Defined in `src/players/configs.py`. Each is substituted into the "
        "`{self.role_prompt}` placeholder of every player prompt above, so the "
        "text a player actually receives is one of these joined to the prompt "
        "bodies in the preceding sections.",
        "",
    ]
    for role, config in PLAYER_CONFIGS.items():
        persona = config.get("role_prompt", "")
        toolsets = ", ".join(f"`{t}`" for t in config.get("toolsets", [])) or "*none*"
        out += [f"### `{role}`", "", persona, "", f"**Toolsets:** {toolsets}", ""]
    return "\n".join(out)


def _orphan_warning() -> str:
    """Flag prompts in `prompts.py` that nothing calls, so they stay visible."""
    from src.orchestrator import prompts as prompt_module

    orphans = [
        name
        for name in vars(prompt_module)
        if name.startswith("get_")
        and name.endswith("_prompt")
        and callable(getattr(prompt_module, name))
        and name not in SELECTED_BY
    ]
    if not orphans:
        return ""
    listed = ", ".join(f"`{name}`" for name in orphans)
    out = [
        "```{warning}",
        f"{len(orphans)} prompts in `src/orchestrator/prompts.py` have no "
        f"callers anywhere in `src`, `demo`, `examples`, or `tests`: {listed}.",
        "",
        "The live equivalents are the inline prompts in `src/players/player.py` "
        "documented under [Player prompts](#player-prompts). Editing the "
        "orphaned copies has no effect on what the model receives.",
        "```",
        "",
    ]
    return "\n".join(out)


# --------------------------------------------------------------------------

def generate(app=None):
    """Write ``docs/prompts.md`` from every prompt source in the repo."""
    page = [
        "# Prompt reference",
        "",
        "*Generated at build time from `src/orchestrator/prompts.py`, "
        "`src/players/player.py`, and `src/players/configs.py`. Do not edit "
        "this page by hand -- edit the prompts and rebuild.*",
        "",
        "## Orchestrator prompts",
        "",
        "Planning prompts returned by the `get_*_prompt` factories in "
        "`src/orchestrator/prompts.py`.",
        "",
        _orphan_warning(),
    ]
    page += _orchestrator_sections()
    page += [
        "## Player prompts",
        "",
        "Prompts built inline inside `src/players/player.py`. Unlike the "
        "orchestrator prompts these are not factories, so they are extracted "
        "from the source rather than imported.",
        "",
    ]
    page += _player_sections()
    page.append(_role_section())

    OUTPUT.write_text("\n".join(page))


def setup(app):
    app.connect("builder-inited", generate)
    return {"parallel_read_safe": True, "version": "0.1"}


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT))
    generate()
    print(f"wrote {OUTPUT}")
