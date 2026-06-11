You are summarizing one squad's recent completed work into "key achievements"
suitable for an MD-facing dashboard.

Rules:
- Output 1 to {top_n} achievements. Quality over quantity.
- Each achievement is one sentence in plain English (no jargon, no ticket prefixes
  like "PBI:" or "User Story:").
- Cite the workitem ids that prove the achievement in `evidence_workitem_ids`.
- Skip routine maintenance (dependency bumps, lint fixes, doc typos) UNLESS
  it's the only material change.
- Skip work that was started but not closed.

Output ONLY a JSON array:
```
[
  {
    "achievement": "Cleared the backlog of >30-day onboarding cases by closing the manual KYC bottleneck.",
    "evidence_workitem_ids": [4521, 4522, 4530]
  }
]
```

Squad: {squad_name}
Snapshot date: {snapshot_date}

Recently completed workitems (closed in the current sprint window):
{closed_items_json}
