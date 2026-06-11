You are reviewing a failed test run for a fix attempt and deciding what to do next.

Inputs:
- Your previous fix plan (summary, root cause, edits).
- The test command that ran.
- The tail of stdout/stderr.
- The list of failed test names.

Decide:
1. Was the failure caused by your patch (regression you introduced)?
2. Was the failure pre-existing (unrelated to your patch)?
3. Does your hypothesized root cause still hold, or do you need a new theory?

Return ONLY a JSON object:
```
{
  "caused_by_patch": true|false,
  "should_retry": true|false,
  "new_hypothesis": "if should_retry, the revised root cause; otherwise empty",
  "files_to_revisit": ["src/...", "src/..."],
  "summary": "one-line for the audit trail"
}
```

If `should_retry` is false, include in `summary` why you are stopping (so the
human reviewer understands the handoff).

---
Previous plan:
{plan_json}

Command: {command}
Failed tests:
{failed_tests}

stdout/stderr tail:
```
{output_tail}
```
