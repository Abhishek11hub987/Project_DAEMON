"""
GitHub Monitor — Live World Sensor (REST API)
===============================================

Pings the GitHub REST API for open pull requests on a target repository.

Configuration via ``.env``::

    GITHUB_TOKEN=ghp_xxxxxxxxxxxx
    GITHUB_TARGET_REPO=owner/repo-name

Uses the ``requests`` library (already in requirements.txt).

Returns structured data:
- Count of open PRs
- Title, author, URL, and creation date for each

Designed for background polling via ``ProactiveMonitor``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"


class GitHubMonitor:
    """GitHub REST API monitor for DAEMON's background polling.

    Usage::

        monitor = GitHubMonitor()
        status = monitor.fetch_pull_requests()
        # {
        #   "open_pr_count": 3,
        #   "prs": [
        #     {"title": "Fix login", "author": "john", "url": "...", "created": "..."},
        #     ...
        #   ],
        #   "repo": "owner/repo",
        #   "error": None
        # }
    """

    def __init__(
        self,
        token: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> None:
        self._token = token or os.getenv("GITHUB_TOKEN", "").strip()
        self._repo = repo or os.getenv("GITHUB_TARGET_REPO", "").strip()

        if not self._token:
            logger.warning(
                "🐙 GitHubMonitor: GITHUB_TOKEN not set in .env. "
                "GitHub monitoring will return placeholder data."
            )
        if not self._repo:
            logger.warning(
                "🐙 GitHubMonitor: GITHUB_TARGET_REPO not set in .env. "
                "GitHub monitoring will return placeholder data."
            )

    @property
    def configured(self) -> bool:
        return bool(self._token and self._repo)

    def _headers(self) -> Dict[str, str]:
        h = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "DAEMON-Monitor/1.0",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def fetch_pull_requests(self, state: str = "open") -> Dict[str, Any]:
        """Fetch pull requests from the target repository.

        Parameters
        ----------
        state
            PR state filter: ``open``, ``closed``, or ``all``.

        Returns
        -------
        dict
            Keys: open_pr_count, prs (list), repo, error (str or None)
        """
        if not self.configured:
            return self._placeholder()

        url = f"{_API_BASE}/repos/{self._repo}/pulls"
        params = {
            "state": state,
            "per_page": 25,
            "sort": "created",
            "direction": "desc",
        }

        try:
            resp = requests.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=15,
            )

            if resp.status_code == 401:
                error_msg = "GitHub token is invalid or expired"
                logger.error(f"🐙 GitHubMonitor: {error_msg}")
                return {
                    "open_pr_count": 0,
                    "prs": [],
                    "repo": self._repo,
                    "error": error_msg,
                }

            if resp.status_code == 404:
                error_msg = f"Repository '{self._repo}' not found"
                logger.error(f"🐙 GitHubMonitor: {error_msg}")
                return {
                    "open_pr_count": 0,
                    "prs": [],
                    "repo": self._repo,
                    "error": error_msg,
                }

            resp.raise_for_status()
            data = resp.json()

            prs: List[Dict[str, str]] = []
            for pr in data:
                prs.append({
                    "title": (pr.get("title") or "")[:120],
                    "author": (pr.get("user", {}) or {}).get("login", "unknown"),
                    "url": pr.get("html_url", ""),
                    "created": (pr.get("created_at") or "")[:10],
                    "draft": pr.get("draft", False),
                    "labels": [l.get("name", "") for l in (pr.get("labels") or [])],
                })

            logger.info(
                f"🐙 GitHubMonitor: {len(prs)} open PRs on {self._repo}"
            )
            return {
                "open_pr_count": len(prs),
                "prs": prs,
                "repo": self._repo,
                "error": None,
            }

        except requests.ConnectionError:
            error_msg = "Cannot reach GitHub API — check internet connection"
            logger.error(f"🐙 GitHubMonitor: {error_msg}")
            return {
                "open_pr_count": 0,
                "prs": [],
                "repo": self._repo,
                "error": error_msg,
            }
        except requests.Timeout:
            error_msg = "GitHub API request timed out"
            logger.error(f"🐙 GitHubMonitor: {error_msg}")
            return {
                "open_pr_count": 0,
                "prs": [],
                "repo": self._repo,
                "error": error_msg,
            }
        except Exception as e:
            error_msg = f"GitHub fetch failed: {e}"
            logger.error(f"🐙 GitHubMonitor: {error_msg}")
            return {
                "open_pr_count": 0,
                "prs": [],
                "repo": self._repo,
                "error": error_msg,
            }

    def fetch_repo_info(self) -> Dict[str, Any]:
        """Fetch basic repository metadata (stars, forks, issues)."""
        if not self.configured:
            return {}

        try:
            resp = requests.get(
                f"{_API_BASE}/repos/{self._repo}",
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "open_issues": data.get("open_issues_count", 0),
                "language": data.get("language", ""),
                "description": (data.get("description") or "")[:100],
            }
        except Exception as e:
            logger.debug(f"Repo info fetch failed: {e}")
            return {}

    @staticmethod
    def _placeholder() -> Dict[str, Any]:
        """Return placeholder when credentials aren't configured."""
        return {
            "open_pr_count": 0,
            "prs": [],
            "repo": "not configured",
            "error": "GITHUB_TOKEN or GITHUB_TARGET_REPO not set in .env",
        }
