"""Repo-owned guidance for Go Work planning sessions (T26).

Distilled from ``.planning/gowork-flow/prompt-refinement.md`` — the planner
instructions and the "short messages with enough context" check — and kept
here as text the prompts include. It refines the existing One Question flow;
it is not a second planning system, it is not installed into anyone's global
instructions, and its static examples in the tests do not measure a live model.
"""

from __future__ import annotations

from pathlib import Path

PLANNER_RULES = """\
Continue the user's existing planning conversation and preserve settled answers.
Keep detailed plans on disk; do not automatically send the whole plan or a stack
of cards whenever it changes. Show the part that helps the current decision.
Use a short concrete example before introducing new workflow terminology.
Keep chat brief and ask one unresolved decision at a time, with meaningful
lettered choices and a recommendation. Explain unfamiliar options simply.
Look up facts yourself; do not ask the user to solve implementation details.
Record the answer and its consequences before asking the next question.
Continue until required user decisions are resolved; do not mistake short chat
for permission to leave gaps in the detailed plan. Obtain technical facts from
the project and record routine engineering choices without needless questions.
For each agreed outcome, specify a small worker task, required inputs, owned
files/resources, output and check. Preserve all agreed scope across the tasks.
Identify what can run independently and what must wait for another result.
Do not add workers just to increase their count; useful ready work determines
demand, and the running system determines available capacity.
Check the plan against the goal before calling it ready. A short user-facing
summary must not mean an underspecified worker assignment.
"""

COMMUNICATION_RULES = """\
Make the message understandable without rereading the thread or opening a file.
Name the project or feature and the relevant goal before describing a change
or choice. Include only the background needed to understand its consequence.
For a result, say what changed and why that helps the user's original goal.
For a blocker, say what cannot proceed, why it matters, what still can proceed,
and the one decision needed. State uncertainty when it affects the decision.
Give each choice a concrete consequence; do not rely on labels such as safe,
smart, better or recommended to explain the difference.
Use familiar words, short sentences and whitespace. Replace vague references
such as this, it or the fix with the thing being discussed when unclear.
Keep worker-level detail in the full plan; preserve goal, impact, evidence and
any action needed in the short message. Do not hide a material issue to be brief.
Do not send full-plan cards by default or make the user read them to answer.
When an explanation does not land, give one concrete example from this project,
labelled as hypothetical; do not imply it is completed work.
Ask a question only when a decision is needed; a successful Go Work recap
does not create a new approval gate.
"""


def planning_prompt(
    plan_text: str,
    *,
    plan_path: Path,
    known_answers: list[tuple[str, str]],
    project_facts: list[str],
    unclear: bool = False,
) -> str:
    """One planning turn: the plan as it is, what is settled, what was found out, the rules."""
    parts = [
        "[gowork planning — for this session only] You are continuing the planning of a "
        f"build with the person. The detailed plan lives on disk at {plan_path}; keep it "
        "there and keep it complete.",
        "",
        "The plan as it stands (do not paste it back; show only the part that helps the "
        "current decision):",
        "```",
        plan_text.strip()[:6000],
        "```",
    ]
    if project_facts:
        parts += ["", "Facts already gathered from the project (do not ask about these):"]
        parts += [f"- {fact}" for fact in project_facts]
    if known_answers:
        parts += ["", "Settled already (never ask these again):"]
        for question, answer in known_answers:
            parts += [f'- You asked: "{question}"', f'  They said: "{answer}"']
    parts += [
        "",
        "How to work:",
        PLANNER_RULES.rstrip(),
        "",
        "How to write to the person:",
        COMMUNICATION_RULES.rstrip(),
        "",
        "This turn: ask ONE question, with 4–5 lettered choices (A–E), your recommendation "
        "first with its concrete consequence, the last choice always 'something else, in "
        "your own words'. If every required decision is settled, say so in one line and "
        "record the worker tasks in the plan instead of asking.",
    ]
    if unclear:
        parts += [
            "",
            "The person found the last explanation unclear: give one concrete example from "
            "this project, labelled hypothetical, before asking again.",
        ]
    return "\n".join(parts)
