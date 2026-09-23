# Fix the Save label

Goal: The settings button reads "Save" and everything else stays as it is
Done when: The settings screen shows the corrected label and the release note mentions it
Check: uv run pytest tests -q

## Decisions

- Only the misspelled label changes; no other settings text is touched

## Agreed outcomes

- REQ-SAVE-LABEL: The settings button is spelled correctly
- REQ-RELEASE-NOTE: The release note mentions the fix

```gowork-plan
{
  "schema_version": 1,
  "plans": [
    {"id": "settings", "version": 1, "project_path": "."},
    {"id": "docs", "version": 1, "parent_id": "settings", "project_path": "docs"}
  ],
  "requirements": [
    {"id": "REQ-SAVE-LABEL", "outcome": "The settings button is spelled correctly"},
    {"id": "REQ-RELEASE-NOTE", "outcome": "The release note mentions the fix"}
  ],
  "tasks": [
    {
      "id": "settings.save-label",
      "plan_id": "settings",
      "plan_version": 1,
      "outcome": "Correct the misspelled Save label on the settings button",
      "dependencies": [],
      "owned_files": ["src/settings/button.tsx"],
      "owned_resources": [],
      "required_inputs": ["REQ-SAVE-LABEL: the label reads Save"],
      "output": "The button label reads Save and its snapshot test passes",
      "acceptance_check": "npm test -- settings-button",
      "source_requirement": "REQ-SAVE-LABEL"
    },
    {
      "id": "settings.screenshot",
      "plan_id": "settings",
      "plan_version": 1,
      "outcome": "Refresh the settings screenshot in the help page",
      "dependencies": ["settings.save-label"],
      "owned_files": ["help/settings.png"],
      "owned_resources": [],
      "required_inputs": ["settings.save-label: the corrected button"],
      "output": "A screenshot showing the corrected label",
      "acceptance_check": "uv run python scripts/check_screenshots.py",
      "source_requirement": "REQ-SAVE-LABEL"
    },
    {
      "id": "docs.release-note",
      "plan_id": "docs",
      "plan_version": 1,
      "outcome": "Mention the label fix in the release note",
      "dependencies": [],
      "owned_files": ["CHANGELOG.md"],
      "owned_resources": [],
      "required_inputs": ["REQ-RELEASE-NOTE: one line about the fix"],
      "output": "A changelog entry for the label fix",
      "acceptance_check": "uv run python scripts/check_changelog.py",
      "source_requirement": "REQ-RELEASE-NOTE"
    }
  ]
}
```

## Tasks

- [ ] Correct the misspelled Save label on the settings button
- [ ] Mention the label fix in the release note
- [ ] Refresh the settings screenshot in the help page
