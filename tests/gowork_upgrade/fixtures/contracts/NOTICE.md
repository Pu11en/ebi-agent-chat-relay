# Sources of the saved contract cases (T30)

Every case in `cases.json` names its `source`. The cases were written for this
repository; the `person_said` lines quote the four evaluation scenarios of Drew's
own draft (`.planning/gowork-flow/planner-skill-draft/evals/evals.json`), which
is repository-owned text, not an upstream project.

- `planner-skill-draft/evals/evals.json #1` — new plan (a business with a website,
  a product and marketing).
- `planner-skill-draft/evals/evals.json #2` — resumed answers (automatic worker
  sizing already settled).
- `planner-skill-draft/evals/evals.json #3` — ownership conflict, dependency on an
  unbuilt result, legacy runtime.
- `planner-skill-draft/evals/evals.json #4` — tiny change (a misspelled label).
- `tests/gowork_upgrade/fixtures/validated-plan.md (T03)` — multiple projects.
- `tests/gowork_upgrade/fixtures/legacy-plan.md (T01)` — legacy checkbox plan.
- `prompt-refinement.md — BMad readiness: a builder must not invent an unanswered decision`
  — missing decisions (the idea, in our own words).

## Upstream content

No text from Superpowers, Get Shit Done (get-shit-done) or BMAD-METHOD is copied
into these fixtures. Their MIT license notices, as inspected, are kept at
`.planning/gowork-flow/planner-skill-draft/references/licenses/`. If a case ever
copies upstream text, give it a `copied_from` entry (`source`, `license`) in
`cases.json` and list that source here with its license; the evaluator refuses a
`copied_from` source that this file does not name.
