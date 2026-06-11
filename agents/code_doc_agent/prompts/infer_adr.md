You are inferring Architecture Decision Records (ADRs) from EVIDENCE in a brownfield
codebase. Real ADR documents rarely exist, so you reconstruct the decisions that were
clearly made — honestly labelled as *inferred*.

You will receive structured evidence: detected datastores, deployment units, external
systems, notable dependencies, layering, and recent commit subjects.

Rules:
- Only assert a decision you can tie to concrete evidence (a config key, a dependency,
  a file, a commit). Cite that evidence.
- If your confidence is low, set "confidence": "low" and "unverified": true — do not
  assert it as fact. The reader will confirm or reject it.
- Prefer 3–6 high-signal decisions over many weak ones.
- Each decision = what was decided, the evidence, the inferred rationale, consequences.

Examples of inferable decisions: "SQLite default with Postgres opt-in via DATABASE_URL",
"summaries-only conversation memory", "React + Vite frontend", "JPA/Hibernate for
persistence", "containerized deployment via docker-compose".

Output ONLY a JSON object:
{
  "decisions": [
    {
      "title": "short title",
      "decision": "what was decided (1 sentence)",
      "evidence": ["pom.xml: spring-boot-starter-data-jpa", "commit a1b2c3d: 'add cache'"],
      "rationale": "why this was likely chosen",
      "consequences": "what it implies / trade-offs",
      "confidence": "high|medium|low",
      "unverified": false
    }
  ]
}

Evidence:
{evidence_json}
