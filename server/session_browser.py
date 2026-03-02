"""Browse previous Claude Code and Codex CLI sessions.

Reads session metadata from the filesystem to allow users to list and
resume previous coding agent sessions.  All reads are on-demand and
non-destructive.

Claude Code sessions: ~/.claude/projects/<encoded-path>/sessions-index.json
Codex sessions: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl (first line)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"


@dataclass
class SessionMetadata:
    """Metadata for a previous CLI session."""

    session_id: str
    cli: str  # "claude" or "codex"
    summary: str
    directory: str
    timestamp: float  # Unix timestamp
    message_count: int = 0
    git_branch: str = ""


def _encode_path(directory: str) -> str:
    """Encode a directory path to Claude Code's project folder name.

    Replaces '/' with '-' and prepends '-'.
    e.g. /home/joe/myproject -> -home-joe-myproject
    """
    # Remove trailing slash, replace all '/' with '-', prepend '-'
    cleaned = directory.rstrip("/")
    return cleaned.replace("/", "-")


def _parse_iso_timestamp(iso_str: str) -> float:
    """Parse an ISO 8601 timestamp to a Unix timestamp.

    Handles both 'Z' suffix and timezone-aware formats.
    Returns 0.0 on parse failure.
    """
    try:
        # Handle 'Z' suffix
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


class SessionBrowser:
    """Browse previous Claude Code and Codex CLI sessions."""

    def list_claude_sessions(self, directory: str) -> list[SessionMetadata]:
        """List Claude Code sessions for a project directory.

        Args:
            directory: Absolute path to the project directory.

        Returns:
            List of SessionMetadata sorted by timestamp descending.
        """
        encoded = _encode_path(directory)
        index_path = _CLAUDE_PROJECTS_DIR / encoded / "sessions-index.json"

        if not index_path.exists():
            return []

        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning(
                "Failed to read Claude sessions index %s: %s",
                index_path, exc,
            )
            return []

        entries = data.get("entries", []) if isinstance(data, dict) else []
        sessions: list[SessionMetadata] = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                session_id = entry.get("sessionId", "")
                if not session_id:
                    continue

                # Use 'modified' for recency, fall back to 'created'
                ts_str = entry.get("modified") or entry.get("created", "")
                timestamp = _parse_iso_timestamp(ts_str)

                sessions.append(SessionMetadata(
                    session_id=session_id,
                    cli="claude",
                    summary=entry.get("summary", entry.get("firstPrompt", "")),
                    directory=entry.get("projectPath", directory),
                    timestamp=timestamp,
                    message_count=entry.get("messageCount", 0),
                    git_branch=entry.get("gitBranch") or "",
                ))
            except Exception as exc:
                log.warning(
                    "Skipping malformed Claude session entry: %s", exc,
                )
                continue

        sessions.sort(key=lambda s: s.timestamp, reverse=True)
        return sessions

    def list_codex_sessions(
        self, directory: str | None = None
    ) -> list[SessionMetadata]:
        """List Codex sessions, optionally filtered by directory.

        Args:
            directory: If provided, only return sessions whose cwd
                matches this directory.

        Returns:
            List of SessionMetadata sorted by timestamp descending.
        """
        if not _CODEX_SESSIONS_DIR.exists():
            return []

        sessions: list[SessionMetadata] = []

        try:
            rollout_files = sorted(
                _CODEX_SESSIONS_DIR.glob("*/*/*/rollout-*.jsonl"),
                reverse=True,
            )
        except OSError as exc:
            log.warning("Failed to scan Codex sessions directory: %s", exc)
            return []

        for rollout_path in rollout_files:
            meta = self._parse_codex_rollout(rollout_path)
            if meta is None:
                continue

            # Filter by directory if requested
            if directory and meta.directory != directory:
                continue

            sessions.append(meta)

        sessions.sort(key=lambda s: s.timestamp, reverse=True)
        return sessions

    def list_recent(
        self,
        n: int = 10,
        cli_filter: str | None = None,
    ) -> list[SessionMetadata]:
        """Merge Claude and Codex sessions, sorted by timestamp descending.

        Args:
            n: Maximum number of sessions to return.
            cli_filter: Optional filter -- "claude" or "codex".

        Returns:
            Top N most recent sessions across all projects.
        """
        all_sessions: list[SessionMetadata] = []

        if cli_filter is None or cli_filter == "claude":
            all_sessions.extend(self._list_all_claude_sessions())

        if cli_filter is None or cli_filter == "codex":
            all_sessions.extend(self.list_codex_sessions())

        all_sessions.sort(key=lambda s: s.timestamp, reverse=True)
        return all_sessions[:n]

    def detect_cli(self, session_id: str) -> str:
        """Determine if a session ID belongs to Claude or Codex.

        Checks Claude projects first (faster -- index files), then
        scans Codex rollout files.

        Returns:
            "claude", "codex", or "unknown".
        """
        # Check Claude projects
        if _CLAUDE_PROJECTS_DIR.exists():
            try:
                for index_file in _CLAUDE_PROJECTS_DIR.glob(
                    "*/sessions-index.json"
                ):
                    try:
                        data = json.loads(
                            index_file.read_text(encoding="utf-8")
                        )
                        entries = (
                            data.get("entries", [])
                            if isinstance(data, dict)
                            else []
                        )
                        for entry in entries:
                            if (
                                isinstance(entry, dict)
                                and entry.get("sessionId") == session_id
                            ):
                                return "claude"
                    except (json.JSONDecodeError, OSError):
                        continue
            except OSError:
                pass

        # Check Codex sessions
        if _CODEX_SESSIONS_DIR.exists():
            try:
                for rollout_path in _CODEX_SESSIONS_DIR.glob(
                    "*/*/*/rollout-*.jsonl"
                ):
                    meta = self._parse_codex_rollout(rollout_path)
                    if meta and meta.session_id == session_id:
                        return "codex"
            except OSError:
                pass

        return "unknown"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_codex_rollout(self, path: Path) -> SessionMetadata | None:
        """Parse the first line of a Codex rollout JSONL file."""
        try:
            with path.open(encoding="utf-8") as f:
                first_line = f.readline().strip()
            if not first_line:
                return None

            data = json.loads(first_line)
            if data.get("type") != "session_meta":
                return None

            payload = data.get("payload", {})
            if not isinstance(payload, dict):
                return None

            thread_id = payload.get("id", "")
            if not thread_id:
                return None

            ts_str = payload.get("timestamp") or data.get("timestamp", "")
            timestamp = _parse_iso_timestamp(ts_str)

            # Extract git branch if available
            git_info = payload.get("git", {})
            git_branch = ""
            if isinstance(git_info, dict):
                git_branch = git_info.get("branch", "")

            return SessionMetadata(
                session_id=thread_id,
                cli="codex",
                summary="",  # Codex rollout first lines don't have summaries
                directory=payload.get("cwd", ""),
                timestamp=timestamp,
                git_branch=git_branch,
            )
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to parse Codex rollout %s: %s", path, exc)
            return None
        except Exception as exc:
            log.warning("Unexpected error parsing %s: %s", path, exc)
            return None

    def _list_all_claude_sessions(self) -> list[SessionMetadata]:
        """List sessions across all Claude Code projects."""
        if not _CLAUDE_PROJECTS_DIR.exists():
            return []

        sessions: list[SessionMetadata] = []
        try:
            for index_file in _CLAUDE_PROJECTS_DIR.glob(
                "*/sessions-index.json"
            ):
                try:
                    data = json.loads(
                        index_file.read_text(encoding="utf-8")
                    )
                    entries = (
                        data.get("entries", [])
                        if isinstance(data, dict)
                        else []
                    )
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        session_id = entry.get("sessionId", "")
                        if not session_id:
                            continue

                        ts_str = (
                            entry.get("modified")
                            or entry.get("created", "")
                        )
                        timestamp = _parse_iso_timestamp(ts_str)

                        sessions.append(SessionMetadata(
                            session_id=session_id,
                            cli="claude",
                            summary=entry.get(
                                "summary",
                                entry.get("firstPrompt", ""),
                            ),
                            directory=entry.get("projectPath", ""),
                            timestamp=timestamp,
                            message_count=entry.get("messageCount", 0),
                            git_branch=entry.get("gitBranch") or "",
                        ))
                except (json.JSONDecodeError, OSError) as exc:
                    log.warning(
                        "Failed to read %s: %s", index_file, exc,
                    )
                    continue
        except OSError as exc:
            log.warning("Failed to scan Claude projects: %s", exc)

        return sessions
