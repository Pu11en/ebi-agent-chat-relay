"""Standard "planning card" — rendered as Markdown text.

A recurring failure mode when a bot posts a plan as free text: the goal drifts,
the integration with existing code is glossed over, and "done" is fuzzy. This
module gives every plan a fixed four-slot layout — Goal, What Exists, Steps
(each with an outcome), Done When — so the reader always knows where to look.

Usage from a bot: write a ``*.plan.json`` file into ``.ccdb-attachments-*``.
The file_sender pipeline notices the extension and sends ``*.plan.md`` in its
place. Discord previews a Markdown attachment inline as scrollable text, which
is readable on every client — a screenshot of the same plan is not.

Schema (all keys required except *notes*)::

    {
      "goal":   "One-sentence outcome the plan is aiming at.",
      "exists": "What already lives in the codebase / system that this plugs into.",
      "steps":  [
        {"do": "Concrete action", "outcome": "What that step produces"},
        ...
      ],
      "done":   "The exact artifact or observable state that means 'shipped'.",
      "notes":  "Optional caveats."
    }
"""

from __future__ import annotations

_MAX_STEPS = 30

#: Fields are clipped so a pathological plan file cannot produce a pathological
#: attachment.
_MAX_FIELD_CHARS = 4000


def _clip(value: object) -> str:
    text = str(value) if value is not None else ""
    if len(text) > _MAX_FIELD_CHARS:
        return text[:_MAX_FIELD_CHARS] + " …(truncated)"
    return text


def plan_markdown(spec: dict) -> str:
    """Fill the fixed four-slot layout with *spec* as Markdown.

    Missing keys become visible placeholder lines so the reader (or the bot on
    the next turn) can see exactly which slot the plan skipped — silent
    fallbacks would defeat the whole point of the fixed layout.
    """
    goal = _clip(spec.get("goal")) or "⚠️ goal missing"
    exists = _clip(spec.get("exists")) or "⚠️ what exists missing"
    done = _clip(spec.get("done")) or "⚠️ done-when missing"
    notes = spec.get("notes")

    raw_steps = spec.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        raw_steps = [{"do": "⚠️ no steps provided"}]
    step_lines: list[str] = []
    for i, step in enumerate(raw_steps[:_MAX_STEPS], 1):
        # A model can emit a bare string where a step object belongs; render
        # it as the action rather than raising into the file pipeline.
        if not isinstance(step, dict):
            step = {"do": step}
        step_lines.append(f"{i}. {_clip(step.get('do', ''))}")
        outcome = _clip(step.get("outcome", ""))
        if outcome:
            step_lines.append(f"   → {outcome}")
    if len(raw_steps) > _MAX_STEPS:
        step_lines.append(f"\n+{len(raw_steps) - _MAX_STEPS} more steps (truncated)")

    parts = [
        f"# Plan: {goal}",
        f"## What exists\n\n{exists}",
        "## Steps\n\n" + "\n".join(step_lines),
        f"## Done when\n\n{done}",
    ]
    if notes:
        parts.append(f"## Notes\n\n{_clip(notes)}")
    return "\n\n".join(parts) + "\n"
