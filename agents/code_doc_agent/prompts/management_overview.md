You are writing a one-page management overview of a software system for a
non-technical reader (Managing Director, business sponsor).

Inputs:
- Project name: {project_name}
- Modules and their purposes
- Key business flows the system supports

Constraints:
- Maximum 500 words.
- Plain English. No jargon. No code snippets.
- Cover: what the system does, the business value, the main user journeys, the
  major moving parts (3-6 modules), and any obvious areas of risk or complexity
  visible from the structure (e.g., a single oversized module, missing tests).
- Do NOT speculate about strategy or roadmap.

Output: Markdown, with these sections:
## What this system does
## Business value
## Key user journeys
## How it is built
## Areas worth attention

Modules:
{modules_json}

Flows:
{flows_json}
