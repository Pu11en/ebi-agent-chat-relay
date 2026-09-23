# Settings clean-up

Goal: The settings screen saves correctly and autosaves drafts
Done when: Both settings behaviours pass their tests locally
Check: uv run pytest tests -q

## Decisions

- Both changes edit config/settings.json, so they take turns; the label fix goes first

## Agreed outcomes

- REQ-SAVE-LABEL: The settings button is spelled correctly
- REQ-AUTOSAVE: Drafts are saved automatically
- REQ-HELP: The help page describes autosave

```gowork-plan
{
  "schema_version": 1,
  "plans": [
    {"id": "settings", "version": 1, "project_path": "."}
  ],
  "requirements": [
    {"id": "REQ-SAVE-LABEL", "outcome": "The settings button is spelled correctly"},
    {"id": "REQ-AUTOSAVE", "outcome": "Drafts are saved automatically"},
    {"id": "REQ-HELP", "outcome": "The help page describes autosave"}
  ],
  "tasks": [
    {
      "id": "settings.save-label",
      "plan_id": "settings",
      "plan_version": 1,
      "outcome": "Correct the misspelled Save label on the settings button",
      "dependencies": [],
      "owned_files": ["src/settings/button.tsx", "config/settings.json"],
      "owned_resources": [],
      "required_inputs": ["REQ-SAVE-LABEL: the label reads Save"],
      "output": "The button label reads Save and its snapshot test passes",
      "acceptance_check": "npm test -- settings-button",
      "source_requirement": "REQ-SAVE-LABEL"
    },
    {
      "id": "settings.autosave",
      "plan_id": "settings",
      "plan_version": 1,
      "outcome": "Save drafts automatically every minute",
      "dependencies": [],
      "owned_files": ["src/settings/autosave.ts", "config/settings.json"],
      "owned_resources": [],
      "required_inputs": ["REQ-AUTOSAVE: one-minute interval, drafts only"],
      "output": "Drafts are written every minute and the autosave tests pass",
      "acceptance_check": "npm test -- autosave",
      "source_requirement": "REQ-AUTOSAVE"
    },
    {
      "id": "help.autosave-page",
      "plan_id": "settings",
      "plan_version": 1,
      "outcome": "Describe autosave on the help page",
      "dependencies": [],
      "owned_files": ["help/autosave.md"],
      "owned_resources": [],
      "required_inputs": ["REQ-HELP: what autosave does and when"],
      "output": "A help page section about autosave",
      "acceptance_check": "uv run python scripts/check_help.py",
      "source_requirement": "REQ-HELP"
    }
  ]
}
```

## Tasks

- [ ] Correct the misspelled Save label on the settings button
- [ ] Save drafts automatically every minute
- [ ] Describe autosave on the help page
