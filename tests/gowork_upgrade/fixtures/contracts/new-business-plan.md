# Launch the shop

Goal: Customers can browse the product catalog on the website and hear about the launch
Done when: The catalog page renders the published contract locally and the announcement is reviewed
Check: uv run pytest tests -q
Try: uv run python -m website

## Decisions

- Catalog contract first; the website page follows it
- Workers are sized by the computer, not by a number the person picks
- No paid tools during the build

## Agreed outcomes

- REQ-CATALOG: The product catalog contract is published
- REQ-CATALOG-PAGE: The website shows the product catalog
- REQ-LAUNCH-POST: A launch announcement is ready to publish

```gowork-plan
{
  "schema_version": 1,
  "plans": [
    {"id": "shop", "version": 1, "project_path": "."},
    {"id": "product", "version": 1, "parent_id": "shop", "project_path": "product"},
    {"id": "website", "version": 1, "parent_id": "shop", "project_path": "website"},
    {"id": "marketing", "version": 1, "parent_id": "shop", "project_path": "marketing"}
  ],
  "requirements": [
    {"id": "REQ-CATALOG", "outcome": "The product catalog contract is published"},
    {"id": "REQ-CATALOG-PAGE", "outcome": "The website shows the product catalog"},
    {"id": "REQ-LAUNCH-POST", "outcome": "A launch announcement is ready to publish"}
  ],
  "tasks": [
    {
      "id": "product.catalog-contract",
      "plan_id": "product",
      "plan_version": 1,
      "outcome": "Publish the checked product catalog contract",
      "dependencies": [],
      "owned_files": ["src/catalog.py", "tests/test_catalog.py"],
      "owned_resources": ["catalog-schema"],
      "required_inputs": ["REQ-CATALOG: the agreed catalog fields"],
      "output": "A versioned catalog contract with passing contract tests",
      "acceptance_check": "uv run pytest tests/test_catalog.py -q",
      "source_requirement": "REQ-CATALOG"
    },
    {
      "id": "product.catalog-fixtures",
      "plan_id": "product",
      "plan_version": 1,
      "outcome": "Provide example catalog data for the website",
      "dependencies": ["product.catalog-contract"],
      "owned_files": ["fixtures/catalog.json"],
      "owned_resources": [],
      "required_inputs": ["product.catalog-contract: the versioned catalog contract"],
      "output": "Example catalog data that validates against the contract",
      "acceptance_check": "uv run python -m product.validate fixtures/catalog.json",
      "source_requirement": "REQ-CATALOG"
    },
    {
      "id": "website.catalog-page",
      "plan_id": "website",
      "plan_version": 1,
      "outcome": "Show the product catalog on the website",
      "dependencies": ["product.catalog-contract", "product.catalog-fixtures"],
      "owned_files": ["src/pages/catalog.tsx"],
      "owned_resources": [],
      "required_inputs": [
        "product.catalog-contract: the versioned catalog contract",
        "product.catalog-fixtures: example catalog data"
      ],
      "output": "A catalog page backed by the accepted contract and example data",
      "acceptance_check": "npm test -- catalog-page",
      "source_requirement": "REQ-CATALOG-PAGE"
    },
    {
      "id": "website.launch-styling",
      "plan_id": "website",
      "plan_version": 1,
      "outcome": "Give the website the agreed launch styling",
      "dependencies": [],
      "owned_files": ["src/styles/"],
      "owned_resources": [],
      "required_inputs": ["REQ-CATALOG-PAGE: the agreed launch look"],
      "output": "Launch styling applied across the styles folder",
      "acceptance_check": "npm test -- styles",
      "source_requirement": "REQ-CATALOG-PAGE"
    },
    {
      "id": "marketing.launch-post",
      "plan_id": "marketing",
      "plan_version": 1,
      "outcome": "Write the launch announcement",
      "dependencies": [],
      "owned_files": ["posts/launch.md"],
      "owned_resources": [],
      "required_inputs": ["REQ-LAUNCH-POST: the launch date and audience"],
      "output": "A reviewed launch announcement",
      "acceptance_check": "uv run python scripts/check_posts.py",
      "source_requirement": "REQ-LAUNCH-POST"
    }
  ]
}
```

## Tasks

- [ ] Publish the checked product catalog contract
  Task: `product.catalog-contract` in plan `product` (project product)
  Depends on: none
  Inputs: REQ-CATALOG: the agreed catalog fields
  Files: src/catalog.py, tests/test_catalog.py
  Resources: catalog-schema
  Result: A versioned catalog contract with passing contract tests
  Verify: uv run pytest tests/test_catalog.py -q
  Outcome: REQ-CATALOG
- [ ] Give the website the agreed launch styling
  Task: `website.launch-styling` in plan `website` (project website)
  Depends on: none
  Inputs: REQ-CATALOG-PAGE: the agreed launch look
  Files: src/styles/
  Result: Launch styling applied across the styles folder
  Verify: npm test -- styles
  Outcome: REQ-CATALOG-PAGE
- [ ] Write the launch announcement
  Task: `marketing.launch-post` in plan `marketing` (project marketing)
  Depends on: none
  Inputs: REQ-LAUNCH-POST: the launch date and audience
  Files: posts/launch.md
  Result: A reviewed launch announcement
  Verify: uv run python scripts/check_posts.py
  Outcome: REQ-LAUNCH-POST
- [ ] Provide example catalog data for the website
  Task: `product.catalog-fixtures` in plan `product` (project product)
  Depends on: Publish the checked product catalog contract (`product.catalog-contract`)
  Inputs: product.catalog-contract: the versioned catalog contract
  Files: fixtures/catalog.json
  Result: Example catalog data that validates against the contract
  Verify: uv run python -m product.validate fixtures/catalog.json
  Outcome: REQ-CATALOG
- [ ] Show the product catalog on the website
  Task: `website.catalog-page` in plan `website` (project website)
  Depends on: Publish the checked product catalog contract (`product.catalog-contract`); Provide example catalog data for the website (`product.catalog-fixtures`)
  Inputs: product.catalog-contract: the versioned catalog contract; product.catalog-fixtures: example catalog data
  Files: src/pages/catalog.tsx
  Result: A catalog page backed by the accepted contract and example data
  Verify: npm test -- catalog-page
  Outcome: REQ-CATALOG-PAGE
