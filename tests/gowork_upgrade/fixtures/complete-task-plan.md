# Launch the product

```gowork-plan
{
  "schema_version": 1,
  "plans": [
    {
      "id": "launch",
      "version": 3,
      "project_path": "control"
    },
    {
      "id": "product",
      "version": 2,
      "parent_id": "launch",
      "project_path": "product"
    },
    {
      "id": "website",
      "version": 4,
      "parent_id": "launch",
      "project_path": "website"
    }
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
      "required_inputs": ["REQ-CATALOG", "decision: product names are final"],
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
      "owned_files": ["src/catalog_page.tsx"],
      "owned_resources": [],
      "required_inputs": ["product.catalog-api: versioned catalog contract"],
      "output": "A catalog page backed by the accepted product contract",
      "acceptance_check": "npm test -- catalog-page",
      "source_requirement": "REQ-WEBSITE-CATALOG"
    }
  ]
}
```

- [ ] Publish the product catalog contract
- [ ] Build the product catalog page
