"""ADO write-back: consent-gated bug filing with dedup (§9.17.7).

Consent gate: ``sre.ado_writeback.enabled`` must be ``true`` in config.yaml.
Without it, filing returns a dry-run description.

Dedup: before creating a new work item, search for an existing open Bug tagged
``sre-agent-filed`` with the same error_signature.  If found, add a comment
instead of creating a duplicate.

Batch mode: one Bug per cluster; the cluster_id is appended to the tag so dedup
works per cluster.

Env vars required when enabled:
  ADO_ORG_URL   — e.g. https://dev.azure.com/myorg
  ADO_PROJECT   — project name
"""
from __future__ import annotations

import os

import structlog

logger = structlog.get_logger()

_DEFAULT_TAG = "sre-agent-filed"
_DEDUP_WIQL = """
SELECT [System.Id], [System.Title]
FROM WorkItems
WHERE [System.WorkItemType] = 'Bug'
  AND [System.State] <> 'Closed'
  AND [System.Tags] CONTAINS '{tag}'
  AND [System.Title] CONTAINS '{sig_snippet}'
ORDER BY [System.ChangedDate] DESC
"""


def _ado_enabled(config: dict) -> bool:
    wb = (config.get("sre", {}) or {}).get("ado_writeback", {}) or {}
    return bool(wb.get("enabled", False))


def _ado_tag(config: dict) -> str:
    wb = (config.get("sre", {}) or {}).get("ado_writeback", {}) or {}
    return wb.get("tag", _DEFAULT_TAG)


def _build_description(
    *,
    root_cause: str,
    evidence: list[dict],
    likely_files: list[str],
    conversation_id: str,
    repro: str,
    severity: dict,
) -> str:
    lines = [
        "<h3>Root Cause</h3>",
        f"<p>{root_cause}</p>",
        "<h3>Severity</h3>",
        f"<p>Level: <strong>{severity.get('level','unknown')}</strong>. "
        f"{severity.get('blast_radius','')}</p>",
        "<h3>Likely Files</h3>",
        "<ul>" + "".join(f"<li>{f}</li>" for f in likely_files[:8]) + "</ul>",
        "<h3>Evidence Citations</h3>",
        "<ul>"
        + "".join(
            f"<li>{e.get('source','?')}: {e.get('citation','')} — {e.get('finding','')[:200]}</li>"
            for e in evidence[:8]
        )
        + "</ul>",
    ]
    if repro:
        lines += ["<h3>Repro Steps</h3>", f"<pre>{repro[:1000]}</pre>"]
    lines += [
        "<hr/>",
        f"<p><em>Filed by SRE Agent. Triage conversation: {conversation_id}</em></p>",
    ]
    return "\n".join(lines)


async def file_bug(
    *,
    conversation_id: str,
    project_id: str,
    verdict: dict,
    issue: dict,
    evidence: list[dict],
    severity: dict,
    config: dict,
    error_signature: str = "",
    cluster_id: str | None = None,
    dry_run: bool = False,
) -> dict:
    """File (or simulate) an ADO Bug for a confirmed SRE verdict.

    Returns:
      {"filed": True/False, "work_item_id": int|None, "url": str|None,
       "dedup": bool, "dry_run": bool, "message": str}
    """
    enabled = _ado_enabled(config)
    tag = _ado_tag(config)
    if cluster_id:
        tag = f"{tag}-cluster-{cluster_id}"

    root_cause = verdict.get("root_cause", "")
    likely_files = verdict.get("likely_files", [])
    repro = issue.get("repro_steps", "")
    title_issue = issue.get("title", "Bug detected by SRE Agent")
    title = f"[SRE] {title_issue[:120]}"
    sig_snippet = (error_signature or title_issue)[:60]

    description = _build_description(
        root_cause=root_cause,
        evidence=evidence,
        likely_files=likely_files,
        conversation_id=conversation_id,
        repro=repro,
        severity=severity,
    )

    if dry_run or not enabled:
        msg = (
            f"[DRY RUN] Would file ADO Bug: {title!r} "
            f"| tag={tag!r} | sig={sig_snippet!r}"
        )
        logger.info("ado_writeback_dry_run", title=title, tag=tag)
        return {
            "filed": False, "work_item_id": None, "url": None,
            "dedup": False, "dry_run": True, "message": msg,
        }

    ado_project = os.getenv("ADO_PROJECT", "")
    if not ado_project:
        return {
            "filed": False, "work_item_id": None, "url": None,
            "dedup": False, "dry_run": False,
            "message": "ADO_PROJECT env var not set; cannot file bug.",
        }

    try:
        from shared.mcp_client.ado import ADOMCPClient
        client = ADOMCPClient()

        # Dedup check.
        wiql = _DEDUP_WIQL.format(tag=tag, sig_snippet=sig_snippet.replace("'", "''"))
        existing = await client.search_workitems(project=ado_project, wiql=wiql)
        if existing:
            wi_id = (existing[0].get("id") or 0)
            comment = (
                f"SRE Agent re-detected this issue. "
                f"Triage conversation: {conversation_id}. "
                f"Root cause: {root_cause[:300]}"
            )
            await client.update_workitem(wi_id, comment=comment)
            logger.info("ado_writeback_dedup", work_item_id=wi_id)
            url = f"{os.getenv('ADO_ORG_URL','')}/{ado_project}/_workitems/edit/{wi_id}"
            return {
                "filed": False, "work_item_id": wi_id, "url": url,
                "dedup": True, "dry_run": False,
                "message": f"Existing Bug #{wi_id} updated with new detection comment.",
            }

        # Create new bug.
        wi = await client.create_workitem(
            project=ado_project,
            title=title,
            description=description,
            tags=[tag],
            priority=2 if severity.get("level") not in {"critical"} else 1,
        )
        wi_id = wi.get("id") or 0
        url = wi.get("url") or f"{os.getenv('ADO_ORG_URL','')}/{ado_project}/_workitems/edit/{wi_id}"
        logger.info("ado_writeback_filed", work_item_id=wi_id, title=title)
        return {
            "filed": True, "work_item_id": wi_id, "url": url,
            "dedup": False, "dry_run": False,
            "message": f"Filed ADO Bug #{wi_id}: {title}",
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("ado_writeback_failed", err=str(exc))
        return {
            "filed": False, "work_item_id": None, "url": None,
            "dedup": False, "dry_run": False,
            "message": f"ADO filing failed: {exc}",
        }
