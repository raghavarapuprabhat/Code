"""Minimal Azure DevOps Repos REST client for opening pull requests.

We deliberately do NOT use the ADO MCP server for this — PR creation is a
narrow, security-sensitive operation and we want explicit, auditable HTTP calls.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import httpx


class AzureReposError(RuntimeError):
    pass


@dataclass
class AzureReposClient:
    organization_url: str        # e.g. https://dev.azure.com/myorg
    project: str                 # ADO project name or id
    repository_id: str           # repo name or id
    pat: str
    api_version: str = "7.1"

    @classmethod
    def from_env_and_repo(cls, *, project: str, repository_id: str) -> "AzureReposClient":
        org = os.getenv("AZURE_DEVOPS_ORG", "").rstrip("/")
        pat = os.getenv("AZURE_DEVOPS_PAT", "")
        if not org or not pat:
            raise AzureReposError(
                "AZURE_DEVOPS_ORG and AZURE_DEVOPS_PAT must be set to open Azure Repos PRs."
            )
        return cls(organization_url=org, project=project, repository_id=repository_id, pat=pat)

    def _headers(self) -> dict[str, str]:
        token = base64.b64encode(f":{self.pat}".encode()).decode()
        return {
            "authorization": f"Basic {token}",
            "content-type": "application/json",
            "accept": "application/json",
        }

    def create_pull_request(
        self,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
        reviewer_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        url = (
            f"{self.organization_url}/{self.project}/_apis/git/repositories/"
            f"{self.repository_id}/pullrequests?api-version={self.api_version}"
        )
        # Normalize branch names to refs/heads/...
        src = source_branch if source_branch.startswith("refs/") else f"refs/heads/{source_branch}"
        tgt = target_branch if target_branch.startswith("refs/") else f"refs/heads/{target_branch}"
        body: dict[str, Any] = {
            "sourceRefName": src,
            "targetRefName": tgt,
            "title": title[:399],
            "description": description[:3999],
        }
        if reviewer_ids:
            body["reviewers"] = [{"id": r} for r in reviewer_ids if r.strip()]

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=self._headers(), json=body)
        if resp.status_code >= 400:
            raise AzureReposError(f"PR creation failed [{resp.status_code}]: {resp.text}")
        data = resp.json()
        pr_id = data.get("pullRequestId")
        web_url = (
            f"{self.organization_url}/{self.project}/_git/{self.repository_id}/pullrequest/{pr_id}"
        )
        return {"pr_id": pr_id, "url": web_url, "raw": data}
