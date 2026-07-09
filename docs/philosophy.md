# Philosophy

This document describes *why* the Metadata Agent is built the way it is. It
deliberately stays above the level of classes and function signatures — for
those, see [Architecture](architecture.md). Here we care about the logic of the
pipeline and the bets the design makes.

## One-sentence summary

The system turns a data source into a validated metadata record by first
*writing a plan* about how to describe that source, then *executing each planned
step with a small committee of agents that argue and converge*, accumulating
named findings until a final step fills in a metadata standard's schema.

## The pipeline

### 1. The world becomes a Context

Whatever you point the system at — one CSV, a folder of CSVs, a SQLite
database, a text corpus — is wrapped into a single abstraction: an *execution
context*. The context is the framework's model of "the world the agents live
in," and it answers exactly three questions regardless of what's inside it:

- What resources exist here?
- What does each resource look like?
- How do they relate to each other?

Modality-specific access — read a dataframe, read a document — is pushed down
into subclasses, so nothing downstream needs to know whether it is looking at
rows or paragraphs.

This is the load-bearing decision of the whole design. Everything after this
point is written against the context contract, not against CSVs.

### 2. A planner writes the extraction strategy

The orchestrator hands an LLM two things: a summary of the context (its
resources, their shapes, discovered relationships) and a *manifest* of the
available agent roles along with the tools each one carries. Crucially, the
manifest is filtered by modality first — a role whose tools only make sense on
tabular data is not advertised when the context is text.

The LLM's job is to emit an ordered plan. Each step declares:

- what task to perform,
- which agent role should do it,
- which resources it targets,
- which previously-produced findings it consumes,
- which new findings it will produce.

That last pair — inputs and outputs, *named* — turns the plan from a wish list
into a dataflow graph.

### 3. The plan is checked before a single token is spent on it

Two gates:

**Dataflow.** Does every step consume only findings that some earlier step
actually produces?

**Feasibility.** Does every named role exist, is it permitted in this run's
configuration, and does it own at least one tool that works on this modality?

A plan that references a finding nobody produces, or assigns tabular analysis to
a text corpus, is rejected outright rather than discovered mid-execution.

### 4. Each step runs as a debate

Steps execute in order against a shared *workspace* — a namespace of findings
that grows as the plan progresses. Within a step:

1. The framework instantiates several copies of the role the plan named.
2. Each copy runs the step's tools against the context — deterministic code that
   actually reads the data — and then reasons over what came back. **The LLMs
   never touch the raw source.** They interpret tool output. Grounding is
   enforced structurally, not by prompt discipline.
3. The copies critique each other's analyses, revise in light of the criticism,
   and repeat for some number of rounds.
4. One of them synthesizes the surviving analyses into a single consolidated
   answer, filed in the workspace under the output names the plan declared.

### 5. Effort is a dial, separate from strategy

How many agents work on each step, and how many critique rounds they run, is a
*topology* — a named preset (`single`, `fast`, `default`, `thorough`). The plan
says what to do; the topology says how hard to try. The same plan can be rerun
at a different quality/cost point without being rewritten.

### 6. The last step is typed, not prose

Intermediate steps produce free text. The final step — when a metadata standard
was named — is bound to that standard's schema, so the model is forced to emit a
validated structured record rather than a paragraph that looks like one. The
pipeline ends in a shape a machine can consume.

## Assumptions behind the design

**Tools observe, models interpret.** Every factual claim about the data
originates in deterministic code. The LLM's role is to describe and organize,
never to recall or invent. This is why tool-modality gating matters so much: it
is the mechanism that keeps the model from being asked questions its evidence
cannot answer.

**A plan you can verify is worth more than a plan you can trust.** By making the
planner declare its dataflow explicitly, the framework converts "did the LLM
write a sensible plan?" from a judgment call into a static check.

**Disagreement is a quality signal.** The critique/revise loop assumes that
errors are idiosyncratic and truths are shared, so surfacing disagreement and
then collapsing it filters noise.

## Risk of the assumptions

The third assumption is the weakest of the three as currently implemented, and it is
worth being explicit about the gap.

The agents within a step are **clones of the same role** — same persona, same
prompt, same temperature — and the synthesizer is one of the debaters rather
than a neutral party. Multi-agent debate derives most of its value from
*diverse* priors; as configured, the loop is closer to self-consistency sampling
than to genuine adversarial review. The role diversity in this system is real,
but it lives *across* steps, not within them.

The first assumption — *tools observe, models interpret* — holds, but the
observing half is currently narrower than the tool surface suggests.

The tools a player runs are not chosen by the model. Every tool the role owns is
fired, and the arguments are guessed by matching the tool's *name* against a
fixed keyword list. So the LLM is an interpreter of a fixed evidence bundle, not
an agent selecting instruments. Predictability and auditability are the intended
trade, and they are real. But the mechanism cannot pass an argument it did not
anticipate: any tool needing more than a resource name — a column, a field, a
coordinate pair — is never successfully called. In practice this means every
tool that *detects* something works, and every tool that would then *analyze*
what was detected fails. The evidence bundle has a ceiling, and the reasoning
layer cannot raise it by asking better questions.

That is the sharper form of the point. The tool layer is not merely
non-agentic; it is unable to express half of what the tools can do. A player
whose role is defined by parameterized analysis is, structurally, unable to
perform it.

A parallel gap sits in the modality gating. The tool-compatibility table that
the planner and validator consult recognizes only CSV contexts, so the
modality-agnostic tools — the ones the context abstraction exists to enable —
are never offered to the text and database contexts they were written for. The
abstraction is sound; the table in front of it has not caught up.

None of this is fatal to the design, and that is the point worth keeping:
heterogeneous player pools within a step, an independent synthesizer,
model-driven tool selection, and a modality-aware compatibility table are all
additive changes. The architecture leaves room for each. What the current state
shows is that a clean abstraction does not by itself deliver its own benefits —
the layers above it have to actually ask.

*(Both gaps are measured and documented in the 2026-07-09 development log.)*
