"""Per-user STT correction dictionary management and LLM-assisted transcript correction.

Stores learned vocabulary corrections as JSON files on disk (one per user) and
applies them to raw Whisper transcripts via Claude Haiku, falling back to the
original transcript if the API call fails.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic
import httpx

from server.config import CorrectionConfig, LLMConfig

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


class CorrectionManager:
    """Manages per-user STT correction dictionaries and LLM-assisted correction.

    Correction dictionaries are stored as JSON files under ``config.data_dir``
    with the filename ``{user_id}.json``.  Each file contains a flat JSON object
    mapping a misrecognised phrase (key) to its correct form (value).

    Dictionaries are loaded lazily on first access and cached in memory for the
    lifetime of the manager.  Every mutation (add / remove) is persisted to disk
    immediately so no state is lost on restart.

    LLM correction is performed by :meth:`correct`, which sends the transcript
    together with the user's correction dictionary to Claude Haiku.  If the API
    call fails for any reason the original transcript is returned unchanged
    (graceful degradation).

    Parameters
    ----------
    config:
        Correction-specific configuration (model name, data directory).
    anthropic_api_key:
        Anthropic API key used to authenticate the ``AsyncAnthropic`` client.
    """

    def __init__(self, config: CorrectionConfig, llm_config: LLMConfig, anthropic_api_key: str) -> None:
        self._config = config
        self._llm_config = llm_config

        # Resolve model: per-tool override -> shared LLM config -> hardcoded default
        self._model: str = config.model or llm_config.model or "claude-haiku-4-5-20251001"

        # LLM correction is optional — if no API key, corrections are still
        # stored but the LLM pass is skipped entirely.
        if anthropic_api_key:
            self._client: anthropic.AsyncAnthropic | None = anthropic.AsyncAnthropic(
                api_key=anthropic_api_key
            )
            self._httpx_client: httpx.AsyncClient | None = None
        elif llm_config.backend:
            self._client = None
            self._httpx_client = httpx.AsyncClient()
            log.info(
                "Using LLM backend %r for correction (model=%s)",
                llm_config.backend,
                self._model,
            )
        else:
            self._client = None
            self._httpx_client = None
            log.info("No Anthropic API key or LLM backend; LLM-based correction disabled")

        # In-memory cache: user_id -> {wrong: right, ...}
        self._cache: dict[str, dict[str, str]] = {}

        # Ensure the data directory exists so writes never fail.
        self._config.data_dir.mkdir(parents=True, exist_ok=True)
        log.debug(
            "CorrectionManager initialised; data_dir=%s model=%s",
            self._config.data_dir,
            self._model,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _path_for(self, user_id: str) -> Path:
        """Return the JSON file path for *user_id*."""
        return self._config.data_dir / f"{user_id}.json"

    def _load(self, user_id: str) -> dict[str, str]:
        """Load the correction dictionary for *user_id* from disk.

        Returns an empty dict if no file exists yet.  The result is stored in
        ``self._cache`` before being returned.
        """
        path = self._path_for(user_id)
        if path.exists():
            try:
                data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
                log.debug(
                    "Loaded %d correction(s) for user %s from %s",
                    len(data),
                    user_id,
                    path,
                )
            except (json.JSONDecodeError, OSError) as exc:
                log.warning(
                    "Failed to read corrections for user %s from %s: %s",
                    user_id,
                    path,
                    exc,
                )
                data = {}
        else:
            data = {}

        self._cache[user_id] = data
        return data

    def _save(self, user_id: str) -> None:
        """Persist the in-memory correction dictionary for *user_id* to disk."""
        path = self._path_for(user_id)
        corrections = self._cache.get(user_id, {})
        try:
            path.write_text(
                json.dumps(corrections, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.debug(
                "Saved %d correction(s) for user %s to %s",
                len(corrections),
                user_id,
                path,
            )
        except OSError as exc:
            log.error(
                "Failed to persist corrections for user %s to %s: %s",
                user_id,
                path,
                exc,
            )

    def _build_system_prompt(self, corrections: dict[str, str]) -> str:
        """Construct the system prompt that instructs Claude how to correct the transcript.

        Parameters
        ----------
        corrections:
            Mapping of misrecognised phrase -> correct form.

        Returns
        -------
        str
            A formatted system prompt string.
        """
        lines = [
            "You are a speech-to-text correction assistant. The following transcript "
            "may contain misrecognized words. Apply these known corrections:",
            "",
        ]
        for wrong, right in corrections.items():
            lines.append(f'- "{wrong}" \u2192 "{right}"')
        lines += [
            "",
            "Rules:",
            "- Only fix words/phrases that match the known corrections",
            "- Apply corrections case-insensitively",
            "- Preserve all other text exactly as-is",
            "- Return ONLY the corrected transcript, nothing else",
        ]
        return "\n".join(lines)

    def _transcript_needs_correction(
        self, transcript: str, corrections: dict[str, str]
    ) -> bool:
        """Return True if *transcript* contains at least one correction key.

        This is a cheap pre-filter: if no correction key appears (case-insensitively)
        as a substring of the transcript, there is nothing for the LLM to fix and we
        can skip the API call entirely.

        Parameters
        ----------
        transcript:
            The raw STT transcript.
        corrections:
            The user's correction dictionary.

        Returns
        -------
        bool
            ``True`` if the transcript *might* need correction, ``False`` if
            the API call can safely be skipped.
        """
        lower_transcript = transcript.lower()
        for wrong in corrections:
            if wrong.lower() in lower_transcript:
                return True
        return False

    # ------------------------------------------------------------------
    # Dictionary CRUD
    # ------------------------------------------------------------------

    def get_corrections(self, user_id: str) -> dict[str, str]:
        """Return all corrections for *user_id*, loading from disk if not cached.

        Parameters
        ----------
        user_id:
            Discord user ID (or any opaque string identifier).

        Returns
        -------
        dict[str, str]
            Mapping of misrecognised phrase -> correct form.  Empty dict if
            the user has no stored corrections.
        """
        if user_id not in self._cache:
            self._load(user_id)
        return dict(self._cache[user_id])

    def list_corrections(self, user_id: str) -> dict[str, str]:
        """Alias for :meth:`get_corrections`.

        Parameters
        ----------
        user_id:
            Discord user ID.

        Returns
        -------
        dict[str, str]
            Mapping of misrecognised phrase -> correct form.
        """
        return self.get_corrections(user_id)

    def add_correction(self, user_id: str, wrong: str, right: str) -> None:
        """Add or update a correction for *user_id* and persist to disk.

        Parameters
        ----------
        user_id:
            Discord user ID.
        wrong:
            The phrase as (mis)transcribed by the STT engine.
        right:
            The correct replacement string.
        """
        if user_id not in self._cache:
            self._load(user_id)
        self._cache[user_id][wrong] = right
        log.info(
            "Added correction for user %s: %r -> %r",
            user_id,
            wrong,
            right,
        )
        self._save(user_id)

    def remove_correction(self, user_id: str, wrong: str) -> bool:
        """Remove a correction for *user_id* and persist to disk.

        Parameters
        ----------
        user_id:
            Discord user ID.
        wrong:
            The key to remove.

        Returns
        -------
        bool
            ``True`` if the key existed and was removed, ``False`` if it was
            not present.
        """
        if user_id not in self._cache:
            self._load(user_id)
        if wrong not in self._cache[user_id]:
            log.debug(
                "remove_correction: key %r not found for user %s",
                wrong,
                user_id,
            )
            return False
        del self._cache[user_id][wrong]
        log.info("Removed correction %r for user %s", wrong, user_id)
        self._save(user_id)
        return True

    # ------------------------------------------------------------------
    # LLM correction
    # ------------------------------------------------------------------

    async def correct(self, transcript: str, user_id: str) -> str:
        """Apply learned vocabulary corrections to *transcript* via Claude Haiku.

        If the user has no stored corrections, or if no correction key appears
        as a substring of the transcript, the transcript is returned unchanged
        without making an API call.

        If the API call fails for any reason the original transcript is returned
        so that callers always receive usable output (graceful degradation).

        Parameters
        ----------
        transcript:
            Raw STT transcript to correct.
        user_id:
            Discord user ID whose correction dictionary should be applied.

        Returns
        -------
        str
            The corrected transcript, or the original if no corrections apply
            or the API call fails.
        """
        corrections = self.get_corrections(user_id)

        if not corrections:
            log.debug(
                "No corrections for user %s; returning transcript unchanged",
                user_id,
            )
            return transcript

        if not self._transcript_needs_correction(transcript, corrections):
            log.debug(
                "Pre-filter: no correction keys found in transcript for user %s; "
                "skipping API call",
                user_id,
            )
            return transcript

        if self._client is None and self._httpx_client is None:
            log.debug(
                "LLM correction disabled (no API key or backend); returning transcript unchanged"
            )
            return transcript

        system_prompt = self._build_system_prompt(corrections)
        log.debug(
            "Sending transcript to %s for correction (user=%s, %d correction(s))",
            self._model,
            user_id,
            len(corrections),
        )

        try:
            if self._client:
                # Anthropic native client path (backward compat)
                response = await self._client.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    system=system_prompt,
                    messages=[
                        {"role": "user", "content": transcript},
                    ],
                )
                corrected: str = response.content[0].text.strip()

            elif self._httpx_client:
                # OpenAI-compatible chat completions path
                headers: dict[str, str] = {"Content-Type": "application/json"}
                backend = self._llm_config.backend
                if backend == "openrouter":
                    headers["Authorization"] = f"Bearer {self._llm_config.api_key}"
                elif backend == "codex_oauth":
                    from server.router import _read_codex_auth
                    token = _read_codex_auth(self._llm_config.codex_auth_path)
                    headers["Authorization"] = f"Bearer {token}"
                elif backend == "openai_compatible":
                    if self._llm_config.api_key:
                        headers["Authorization"] = f"Bearer {self._llm_config.api_key}"

                # Resolve base URL
                _BACKEND_URLS = {
                    "openrouter": "https://openrouter.ai/api/v1",
                    "codex_oauth": "https://api.openai.com/v1",
                }
                base_url = self._llm_config.api_base_url or _BACKEND_URLS.get(backend, "")

                payload = {
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": transcript},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 1024,
                }

                resp = await self._httpx_client.post(
                    f"{base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=self._llm_config.timeout_ms / 1000.0,
                )
                resp.raise_for_status()
                data = resp.json()
                corrected = data["choices"][0]["message"]["content"].strip()

            log.debug(
                "Correction result for user %s: %r -> %r",
                user_id,
                transcript,
                corrected,
            )
            return corrected

        except anthropic.APIError as exc:
            log.error(
                "Anthropic API error while correcting transcript for user %s: %s; "
                "returning original transcript",
                user_id,
                exc,
            )
            return transcript
        except Exception as exc:  # noqa: BLE001
            log.error(
                "Unexpected error while correcting transcript for user %s: %s; "
                "returning original transcript",
                user_id,
                exc,
            )
            return transcript
