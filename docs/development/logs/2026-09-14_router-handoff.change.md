# Change log 2026-09-14 — The router routes a resolution; it does not resolve

**Goal:** Separate catalog resolution (layer 3) from field routing (layers 4–5). The
field router page and `examples/field_router_plan.py` resolved the bundle again on every
run, with their own copy of the resolver's inputs: the bundle, codebooks, documents,
prose reader and its model. So the router page repeated the catalog page, and a routing
could quietly use a different catalog than the one just inspected.

## A resolution is a value

`ResolvedBundle` (`src/router/bundle.py`) is what leaves layer 3: the `Catalog` together
with the bundle root, its tables, the codebooks and documents the resolution *used*, and
the prose reader that ran. Routing needs both halves — the catalog to rank columns, the
documents to rank spans — so they travel together. The router reads the documents the
catalog was resolved against, never a different selection.

`Catalog.from_dict` / `ResolvedColumn.from_dict` round-trip `to_dict`, and
`ResolvedBundle.save` / `load` write and read it as JSON. A resolution made once can be
routed later, elsewhere, without resolving again.

## The examples

- `examples/resolve_catalog.py` returns a `ResolvedBundle`, and `--out PATH` saves it.
  The file is written in `main()`, not `run()`, so a UI run writes nothing.
- `examples/field_router_plan.py` takes `--catalog PATH`, which is required, and has no
  resolution arguments left: no `--bundle`, `--dictionary`, `--doc`, `--llm-reader` or
  catalog model. `run(args, console, resolved=None)` accepts a resolution already in
  memory. `build_plan` routes and compiles only. `RouterResult.catalog` became
  `.resolved`, and the printed catalog section is gone.
- `eval/cli.py` composes the two stages: `resolve` from the catalog example, then
  `build_plan`.

## The demo

- The **Field router** page is disabled until the **Catalog resolver** page has run
  successfully in the session. It then routes that page's last resolution, handed on in
  memory. Its form holds only router settings. The catalog is shown as a statement of
  what will be routed, not as a choice.
- The command block on the router page shows both commands:
  `resolve_catalog.py … --out catalog.json`, then `field_router_plan.py --catalog
  catalog.json …`. Pasted into a shell, they reproduce the run.
- If the catalog is resolved again after a routing ran, the router page warns that its
  result is for the previous catalog.
- The router view dropped its "Resolved catalog" tab, since the catalog belongs to the
  resolver page. The resolver page gained a **Download resolution** button, which saves
  the same JSON `--catalog` reads.
- `run_example` gained `inputs` (keyword arguments for `run()`, from an earlier page)
  and `preceding_command`, and records the command each run used.
- `PipelineSettings.router_arguments()` no longer carries the catalog tier or model.
- `render_tree` takes a bundle path, so the router page shows the resolution's bundle.

Tests: `tests/test_router_handoff.py` covers the JSON round-trip, that a resolution
records the subset it used, that routing from a file matches routing in memory, and the
missing-file message.
