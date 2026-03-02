"""ElevenLabs cloud STT backend using the Scribe API.

Implements the same interface as the local Transcriber (faster-whisper)
so it can be used as a drop-in replacement in the STT pipeline.
"""

from __future__ import annotations

import io
import logging
import struct
from dataclasses import dataclass

import numpy as np

from server.config import STTConfig
from server.transcriber import TranscriptionResult

log = logging.getLogger(__name__)

SAMPLE_RATE: int = 16_000


class ElevenLabsTranscriber:
    """Cloud STT via the ElevenLabs Scribe API.

    No GPU models are loaded. All transcription is done via HTTP.
    """

    def __init__(self, api_key: str, config: STTConfig) -> None:
        self._api_key = api_key
        self._model_id = config.elevenlabs_model_id
        self._language_code = config.elevenlabs_language_code or None
        self._client = None

        log.info(
            "ElevenLabsTranscriber created (model_id=%s, language=%s)",
            self._model_id,
            self._language_code,
        )

    def _get_client(self):
        """Lazy-init the ElevenLabs client."""
        if self._client is None:
            from elevenlabs import ElevenLabs  # noqa: PLC0415

            self._client = ElevenLabs(api_key=self._api_key)
            log.debug("ElevenLabs STT client initialized")
        return self._client

    @property
    def is_loaded(self) -> bool:
        return True

    def transcribe(
        self,
        audio: np.ndarray,
        initial_prompt: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe 16kHz mono float32 audio via ElevenLabs Scribe API."""
        duration_s = len(audio) / SAMPLE_RATE if len(audio) > 0 else 0.0

        if len(audio) == 0 or duration_s < 0.1:
            log.debug("Audio too short (%.3f s); skipping transcription", duration_s)
            return TranscriptionResult(
                text="",
                language="en",
                language_probability=0.0,
                duration_s=duration_s,
            )

        # Convert float32 [-1, 1] to 16-bit PCM bytes
        pcm_int16 = (audio * 32767).astype(np.int16)
        pcm_bytes = pcm_int16.tobytes()

        # Wrap in BytesIO for the SDK
        audio_file = io.BytesIO(pcm_bytes)
        audio_file.name = "audio.pcm"

        client = self._get_client()

        log.debug(
            "ElevenLabs STT: transcribing %.2f s audio, model=%s",
            duration_s,
            self._model_id,
        )

        try:
            result = client.speech_to_text.convert(
                file=audio_file,
                model_id=self._model_id,
                language_code=self._language_code,
            )

            text = result.text.strip() if result.text else ""
            language = getattr(result, "language_code", "en") or "en"
            lang_prob = getattr(result, "language_probability", 0.9) or 0.9

            log.info(
                "ElevenLabs STT complete: %.2f s audio -> %d chars (lang=%s)",
                duration_s,
                len(text),
                language,
            )

            return TranscriptionResult(
                text=text,
                language=language,
                language_probability=lang_prob,
                duration_s=duration_s,
            )

        except Exception as exc:
            log.error(
                "ElevenLabs STT error: %s; returning empty transcript", exc
            )
            return TranscriptionResult(
                text="",
                language="en",
                language_probability=0.0,
                duration_s=duration_s,
            )

    @staticmethod
    def build_initial_prompt(
        custom_vocab: list[str],
        corrections: dict[str, str],
    ) -> str:
        """Return empty string — Scribe doesn't use initial prompts.

        Corrections are still applied post-transcription by the pipeline.
        """
        return ""

    def warmup(self) -> None:
        """Lazy-init the ElevenLabs client (no model to load)."""
        self._get_client()
        log.info("ElevenLabs STT warmup complete (client initialized)")

    def unload(self) -> None:
        """Clear client reference."""
        self._client = None
        log.debug("ElevenLabs STT client reference cleared")
