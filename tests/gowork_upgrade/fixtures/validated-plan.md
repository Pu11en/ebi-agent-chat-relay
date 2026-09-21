# Launch the business

```gowork-plan
{
  "schema_version": 1,
  "plans": [
    {"id": "launch", "version": 1, "project_path": "control"},
    {"id": "product", "version": 2, "parent_id": "launch", "project_path": "product"},
    {"id": "website", "version": 4, "parent_id": "launch", "project_path": "website"},
    {"id": "marketing", "version": 1, "parent_id": "launch", "project_path": "marketing"}
  ],
  "requirements": [
    {"id": "REQ-CATALOG", "outcome": "The product catalog contract is published"},
    {"id": "REQ-WEBSITE-CATALOG", "outcome": "The website shows the product catalog"},
    {"id": "REQ-LAUNCH-POST", "outcome": "A launch announcement is ready to publish"}
  ],
  "tasks": [
    {
      "id": "product.catalog-api",
      "plan_id": "product",
      "plan_version": 2,
      "outcome": "Publish the checked product catalog contract",
      "dependencies": [],
      "owned_files": ["src/catalog.py", "tests/test_catalog.py"],
      "owned_resources": ["catalog-schema"],
      "required_inputs": ["REQ-CATALOG"],
      "output": "A versioned catalog contract and passing contract tests",
      "acceptance_check": "uv run pytest tests/test_catalog.py -q",
      "source_requirement": "REQ-CATALOG"
    },
    {
      "id": "website.catalog-page",
      "plan_id": "website",
      "plan_version": 4,
      "outcome": "Show the checked product catalog on the website",
      "dependencies": ["product.catalog-api"],
      "owned_files": ["src/pages/catalog.tsx"],
      "owned_resources": [],
      "required_inputs": ["product.catalog-api: versioned catalog contract"],
      "output": "A catalog page backed by the accepted product contract",
      "acceptance_check": "npm test -- catalog-page",
      "source_requirement": "REQ-WEBSITE-CATALOG"
    },
    {
      "id": "website.page-styles",
      "plan_id": "website",
      "plan_version": 4,
      "outcome": "Give every website page the agreed launch styling",
      "dependencies": [],
      "owned_files": ["src/pages/"],
      "owned_resources": [],
      "required_inputs": ["REQ-WEBSITE-CATALOG"],
      "output": "Launch styling applied across the pages folder",
      "acceptance_check": "npm test -- styles",
      "source_requirement": "REQ-WEBSITE-CATALOG"
    },
    {
      "id": "marketing.launch-post",
      "plan_id": "marketing",
      "plan_version": 1,
      "outcome": "Write the launch announcement",
      "dependencies": [],
      "owned_files": ["posts/launch.md"],
      "owned_resources": [],
      "required_inputs": ["REQ-LAUNCH-POST"],
      "output": "A reviewed launch announcement",
      "acceptance_check": "uv run python scripts/check_posts.py",
      "source_requirement": "REQ-LAUNCH-POST"
    }
  ]
}
```

- [ ] Publish the product catalog contract
- [ ] Build the product catalog page
- [ ] Style the website pages
- [ ] Write the launch announcement
