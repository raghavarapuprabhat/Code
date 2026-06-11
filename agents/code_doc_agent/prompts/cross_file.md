You are analyzing the cross-file structure of a {language_mix} codebase.

You receive:
1. The compact tree-graph of the project (project -> packages -> files -> classes -> methods).
2. Per-file summaries (purpose + business rules) for every file.

Produce a JSON object:
```
{
  "modules": [
    {"name": "Auth", "files": ["src/auth/...", "..."], "purpose": "..." }
  ],
  "entry_points": [
    {"type": "rest_controller|react_route|main|cli|scheduled", "name": "...", "file": "...", "line": 1}
  ],
  "flows": [
    {
      "name": "User login",
      "entry_point": "src/controllers/AuthController.java:42",
      "steps": [
        "Browser->AuthController: POST /login",
        "AuthController->AuthService: validate(credentials)",
        "AuthService->UserRepo: findByEmail",
        "UserRepo->Database: SELECT ..."
      ]
    }
  ],
  "data_entities": [
    {
      "name": "User",
      "fields": [{"name": "id", "type": "UUID"}, {"name": "email", "type": "string"}],
      "relations": [{"target": "Order", "cardinality": "||--o{", "label": "places"}]
    }
  ]
}
```

Rules:
- Identify entry points by annotations (`@RestController`, `@GetMapping`, `main()`) and React route components.
- Trace each flow from the entry point through services to data layer.
- Extract data entities from JPA entities, Mongoose schemas, Prisma models, TypeScript interfaces.
- Return ONLY the JSON.

Tree graph:
{tree_graph_json}

File summaries:
{file_summaries_json}
