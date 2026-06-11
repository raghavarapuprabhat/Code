"""Read-only SQL validation for `db_query` (architecture §9.7A, hard rails).

These rails are **code-enforced, not prompt-enforced**. A statement is accepted only
if its parsed AST is a single SELECT/EXPLAIN with no write/DDL/locking constructs; a
LIMIT is injected when absent. Parsing uses sqlglot; if sqlglot is somehow
unavailable we fail closed (reject) rather than running unvalidated SQL.
"""
from __future__ import annotations

from dataclasses import dataclass

_MAX_ROWS = 50

# Token blocklist applied in addition to AST checks (defense in depth).
_FORBIDDEN = (
    "insert", "update", "delete", "merge", "upsert", "drop", "alter", "create",
    "truncate", "grant", "revoke", "call", "exec", "execute", "copy", "into",
    "for update", "for share", "lock ", "vacuum", "analyze", "attach", "pragma",
)


@dataclass
class SqlCheck:
    ok: bool
    sql: str = ""           # rewritten SQL (LIMIT injected) when ok
    reason: str = ""        # rejection reason when not ok


def validate_select_sql(sql: str, *, dialect: str | None = None, max_rows: int = _MAX_ROWS) -> SqlCheck:
    raw = (sql or "").strip().rstrip(";").strip()
    if not raw:
        return SqlCheck(False, reason="empty statement")

    low = raw.lower()
    # Single statement only (the trailing ; was stripped; any remaining one means multi).
    if ";" in raw:
        return SqlCheck(False, reason="multiple statements are not allowed")
    for bad in _FORBIDDEN:
        if bad in low:
            return SqlCheck(False, reason=f"forbidden keyword: {bad.strip()!r}")

    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return SqlCheck(False, reason="SQL validator unavailable (sqlglot not installed)")

    # EXPLAIN is read-only; some dialects don't parse it into a Select tree, so strip
    # the leading keyword and validate the inner statement (EXPLAIN ANALYZE is already
    # rejected by the 'analyze' keyword block above).
    is_explain = low.startswith("explain")
    core = raw[7:].strip() if is_explain else raw

    try:
        statements = [s for s in sqlglot.parse(core, read=dialect or None) if s is not None]
    except Exception as e:  # noqa: BLE001 — unparseable SQL is rejected
        return SqlCheck(False, reason=f"unparseable SQL: {e}")
    if len(statements) != 1:
        return SqlCheck(False, reason="exactly one statement is required")

    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union, exp.Subquery)):
        return SqlCheck(False, reason=f"only SELECT/EXPLAIN allowed (got {root.__class__.__name__})")

    # Reject any write/DDL/locking node anywhere in the tree.
    for node in root.walk():
        n = node[0] if isinstance(node, tuple) else node
        cls = n.__class__.__name__.lower()
        if cls in {"insert", "update", "delete", "drop", "alter", "create", "command", "lock"}:
            return SqlCheck(False, reason=f"disallowed construct: {cls}")

    # Inject a LIMIT if the top-level SELECT lacks one.
    out = core
    try:
        sel = root if isinstance(root, exp.Select) else root.find(exp.Select)
        if sel is not None and sel.args.get("limit") is None:
            out = sel.limit(max_rows).sql(dialect=dialect or None)
    except Exception:  # noqa: BLE001 — if rewrite fails, keep original (still SELECT-only)
        out = core
    if is_explain:
        out = f"EXPLAIN {out}"
    return SqlCheck(True, sql=out)
