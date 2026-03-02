"""LLM-powered intent router for multi-session voice switchboard.

Classifies user speech transcripts into one of four intents:
  - reply_current: Reply to the currently active session
  - route_to_session: Direct a reply to a specific named session
  - cold_call: Send an asynchronous message to a session
  - navigation: Control the switchboard (next, skip, list)

Uses OpenAI-compatible chat completions via httpx with support for
multiple backends (openrouter, codex_oauth, openai_compatible).
Falls back to reply_current on timeout or error.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from thefuzz import fuzz, process

from server.config import LLMConfig, RouterConfig

log = logging.getLogger(__name__)

# Default base URLs per backend
_BACKEND_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "codex_oauth": "https://api.openai.com/v1",
    "openai_compatible": "",  # must be configured
}

_INTENTS = ("reply_current", "route_to_session", "cold_call", "navigation")
_NAVIGATION_ACTIONS = ("next", "skip", "list")
_FUZZY_THRESHOLD = 75


@dataclass
class RouterIntent:
    """Structured output from the LLM router.

    Attributes:
        intent: Classified intent type.
        target_session: Session name to route to (for route/cold_call).
        message_content: Extracted message content (for cold_call).
        navigation_action: Navigation command (for navigation intent).
        confidence: Router confidence score (0.0-1.0).
    """

    intent: str = "reply_current"
    target_session: str | None = None
    message_content: str | None = None
    navigation_action: str | None = None
    confidence: float = 1.0


def _build_system_prompt(active_sessions: list[dict[str, str]]) -> str:
    """Build the system prompt with active session context."""
    session_list = "\n".join(
        f"  - \"{s['name']}\" ({s.get('client_type', 'agent')})"
        for s in active_sessions
    )
    return f"""You are a voice switchboard router. Classify the user's speech into one of four intents.

Active sessions:
{session_list}

Intents:
1. reply_current — The user is responding to the session that just spoke to them. This is the default.
2. route_to_session — The user wants to direct their reply to a specific session by name. Example: "Hey myproject, can you fix that bug?"
3. cold_call — The user wants to send an asynchronous message to a session that isn't currently active. Example: "Tell backend-api to hold off on the deploy."
4. navigation — The user wants to control the switchboard. Keywords: "next" (move to next session's messages), "skip" (skip current), "list" (list active sessions).

Respond with a JSON object:
{{"intent": "...", "target_session": "...", "message_content": "...", "navigation_action": "...", "confidence": 0.0-1.0}}

Rules:
- Only set target_session for route_to_session and cold_call intents.
- Only set message_content for cold_call intent (the message to deliver).
- Only set navigation_action for navigation intent ("next", "skip", or "list").
- Default to reply_current if unsure.
- Match session names loosely (the user may abbreviate or mispronounce)."""


def _resolve_session_name(
    raw_name: str | None,
    active_sessions: list[dict[str, str]],
) -> str | None:
    """Fuzzy-match a raw session name against active sessions.

    Uses thefuzz token_sort_ratio for pronunciation-tolerant matching.
    Returns the matched session name or None if below threshold.
    """
    if not raw_name or not active_sessions:
        return None

    choices = [s["name"] for s in active_sessions]
    result = process.extractOne(
        raw_name,
        choices,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=_FUZZY_THRESHOLD,
    )
    if result is None:
        return None
    matched_name, score = result[0], result[1]
    log.debug(
        "Fuzzy matched %r -> %r (score=%d)",
        raw_name,
        matched_name,
        score,
    )
    return matched_name


def _read_codex_auth(auth_path: str) -> str:
    """Read the JWT token from Codex OAuth credentials file."""
    path = Path(auth_path)
    if not path.exists():
        raise FileNotFoundError(f"Codex auth file not found: {auth_path}")
    data = json.loads(path.read_text())
    token = data.get("token") or data.get("access_token")
    if not token:
        raise ValueError(f"No token found in {auth_path}")
    return token


class IntentRouter:
    """LLM-powered intent classifier for the voice switchboard.

    Calls an OpenAI-compatible chat completions endpoint to classify
    user transcripts. Supports multiple backends and falls back to
    reply_current on any error.
    """

    def __init__(self, router_config: RouterConfig, llm_config: LLMConfig) -> None:
        self._config = router_config
        self._llm_config = llm_config
        self._client: httpx.AsyncClient | None = None

    async def classify(
        self,
        transcript: str,
        active_sessions: list[dict[str, str]],
    ) -> RouterIntent:
        """Classify a user transcript into a RouterIntent.

        Args:
            transcript: The user's speech transcript.
            active_sessions: List of dicts with at least "name" key,
                optionally "client_type".

        Returns:
            RouterIntent with the classified intent.
        """
        if not self._config.enabled:
            return RouterIntent(intent="reply_current", confidence=1.0)

        if len(active_sessions) <= 1:
            return RouterIntent(intent="reply_current", confidence=1.0)

        try:
            return await self._classify_via_llm(transcript, active_sessions)
        except Exception as exc:
            log.warning("Router classification failed, falling back: %s", exc)
            return RouterIntent(intent="reply_current", confidence=0.5)

    async def _classify_via_llm(
        self,
        transcript: str,
        active_sessions: list[dict[str, str]],
    ) -> RouterIntent:
        """Call the LLM backend for classification."""
        base_url = self._resolve_base_url()
        headers = await self._build_headers()
        timeout_s = (self._config.timeout_ms or self._llm_config.timeout_ms) / 1000.0

        system_prompt = _build_system_prompt(active_sessions)
        payload: dict[str, Any] = {
            "model": self._config.model or self._llm_config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript},
            ],
            "temperature": 0.0,
            "max_tokens": 200,
        }

        client = self._get_client()
        start = time.monotonic()
        response = await client.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout_s,
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        log.debug("Router LLM call took %.0fms", elapsed_ms)

        response.raise_for_status()
        data = response.json()

        # Extract the assistant's response
        content = data["choices"][0]["message"]["content"]
        return self._parse_response(content, active_sessions)

    def _parse_response(
        self,
        content: str,
        active_sessions: list[dict[str, str]],
    ) -> RouterIntent:
        """Parse the LLM's JSON response into a RouterIntent."""
        # Strip markdown code fences if present
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (fences)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            log.warning("Router: failed to parse LLM response as JSON: %r", content)
            return RouterIntent(intent="reply_current", confidence=0.3)

        intent = parsed.get("intent", "reply_current")
        if intent not in _INTENTS:
            log.warning("Router: unknown intent %r, falling back", intent)
            intent = "reply_current"

        confidence = float(parsed.get("confidence", 0.8))
        target_session = None
        message_content = None
        navigation_action = None

        if intent in ("route_to_session", "cold_call"):
            raw_target = parsed.get("target_session")
            target_session = _resolve_session_name(raw_target, active_sessions)
            if target_session is None and raw_target:
                log.warning(
                    "Router: could not match session %r, falling back",
                    raw_target,
                )
                intent = "reply_current"
                confidence = 0.4

        if intent == "cold_call":
            message_content = parsed.get("message_content")

        if intent == "navigation":
            navigation_action = parsed.get("navigation_action")
            if navigation_action not in _NAVIGATION_ACTIONS:
                log.warning(
                    "Router: unknown navigation_action %r, falling back",
                    navigation_action,
                )
                intent = "reply_current"
                confidence = 0.4
                navigation_action = None

        return RouterIntent(
            intent=intent,
            target_session=target_session,
            message_content=message_content,
            navigation_action=navigation_action,
            confidence=confidence,
        )

    def _resolve_base_url(self) -> str:
        """Determine the API base URL from config and backend."""
        if self._llm_config.api_base_url:
            return self._llm_config.api_base_url.rstrip("/")

        backend = self._llm_config.backend
        url = _BACKEND_URLS.get(backend, "")
        if not url:
            raise ValueError(
                f"No base URL configured for backend '{backend}'. "
                "Set LLM_API_BASE_URL."
            )
        return url

    async def _build_headers(self) -> dict[str, str]:
        """Build authorization headers based on the backend."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        backend = self._llm_config.backend

        if backend == "openrouter":
            if not self._llm_config.api_key:
                raise ValueError("LLM_API_KEY required for openrouter backend")
            headers["Authorization"] = f"Bearer {self._llm_config.api_key}"

        elif backend == "codex_oauth":
            token = _read_codex_auth(self._llm_config.codex_auth_path)
            headers["Authorization"] = f"Bearer {token}"

        elif backend == "openai_compatible":
            if self._llm_config.api_key:
                headers["Authorization"] = f"Bearer {self._llm_config.api_key}"

        return headers

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create a reusable httpx async client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
