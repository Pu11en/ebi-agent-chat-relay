# Report export

Goal: People can export the feedback report
Done when: An export lands in the chosen format and the tests pass
Check: uv run pytest tests -q

## Agreed outcomes

- REQ-REPORT: The feedback report is drafted
- REQ-EXPORT: The report can be exported (format not decided: PDF or Word?)

```gowork-plan
{
  "schema_version": 1,
  "plans": [
    {"id": "report", "version": 1, "project_path": "."}
  ],
  "requirements": [
    {"id": "REQ-REPORT", "outcome": "The feedback report is drafted"},
    {"id": "REQ-EXPORT", "outcome": "The report can be exported"}
  ],
  "tasks": [
    {
      "id": "report.draft",
      "plan_id": "report",
      "plan_version": 1,
      "outcome": "Draft the feedback report from the grouped themes",
      "dependencies": [],
      "owned_files": ["report/draft.md"],
      "owned_resources": [],
      "required_inputs": ["REQ-REPORT: the grouped themes"],
      "output": "A complete draft with every theme covered",
      "acceptance_check": "uv run python scripts/check_report.py",
      "source_requirement": "REQ-REPORT"
    }
  ]
}
```

## Tasks

- [ ] Draft the feedback report from the grouped themes
