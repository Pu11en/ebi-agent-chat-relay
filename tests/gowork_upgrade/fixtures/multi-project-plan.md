# Launch the business

```gowork-plan
{
  "schema_version": 1,
  "plans": [
    {
      "id": "business",
      "version": 3,
      "project_path": "business-control"
    },
    {
      "id": "product",
      "version": 2,
      "parent_id": "business",
      "project_path": "product"
    },
    {
      "id": "product-api",
      "version": 1,
      "parent_id": "product",
      "project_path": "product"
    },
    {
      "id": "website",
      "version": 4,
      "parent_id": "business",
      "project_path": "website"
    },
    {
      "id": "marketing",
      "version": 1,
      "parent_id": "business",
      "project_path": "marketing"
    }
  ]
}
```

- [ ] Coordinate the launch
