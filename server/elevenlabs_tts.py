"""ElevenLabs cloud TTS backend.

Implements the TTSBackend protocol using the ElevenLabs Python SDK.
Produces float32 mono audio at 24 kHz, identical to the local Qwen3-TTS
engine, so downstream code (CallManager, audio_source) requires zero changes.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

import numpy as np

from server.tts_backend import preprocess

log = logging.getLogger(__name__)

#: Output sample rate — matches ElevenLabs pcm_24000 format and the local
#: engine's native rate.
OUTPUT_SAMPLE_RATE: int = 24_000


def _pcm_bytes_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """Convert raw signed 16-bit little-endian PCM bytes to float32 in [-1, 1]."""
    int16_array = np.frombuffer(pcm_bytes, dtype=np.int16)
    return int16_array.astype(np.float32) / 32768.0


class ElevenLabsTTSEngine:
    """Cloud TTS via the ElevenLabs API.

    No GPU models are loaded.  All synthesis is done via HTTP.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        voices: dict[str, str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._default_voice_id = voice_id
        self._model_id = model_id
        self._voices: dict[str, str] = voices or {}
        self._client = None  # Lazy init

        log.info(
            "ElevenLabsTTSEngine created (voice_id=%s, model=%s, named_voices=%d)",
            voice_id,
            model_id,
            len(self._voices),
        )

    def _get_client(self):
        """Lazy-init the ElevenLabs client."""
        if self._client is None:
            from elevenlabs import ElevenLabs  # noqa: PLC0415

            self._client = ElevenLabs(api_key=self._api_key)
            log.debug("ElevenLabs client initialized")
        return self._client

    # ------------------------------------------------------------------
    # Voice ID resolution
    # ------------------------------------------------------------------

    _RAW_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{16,}$")

    def _resolve_voice_id(self, voice: str | None) -> str:
        """Resolve a *voice* argument to a concrete ElevenLabs voice ID.

        Resolution order:
        1. ``None`` -> default voice ID.
        2. Name present in the ``self._voices`` mapping -> mapped ID.
        3. Alphanumeric string longer than 15 characters -> raw voice ID
           passthrough (assumed to be a literal ElevenLabs voice ID).
        4. Unknown name -> log a warning and fall back to the default.
        """
        if voice is None:
            return self._default_voice_id

        # Check named voice map (case-sensitive)
        if voice in self._voices:
            resolved = self._voices[voice]
            log.debug("Resolved voice name %r -> %s", voice, resolved)
            return resolved

        # Looks like a raw ElevenLabs voice ID?
        if self._RAW_VOICE_ID_RE.match(voice):
            log.debug("Passing through raw voice ID: %s", voice)
            return voice

        # Unknown name -- fall back
        log.warning(
            "Unknown voice name %r; falling back to default voice %s",
            voice,
            self._default_voice_id,
        )
        return self._default_voice_id

    # ------------------------------------------------------------------
    # TTSBackend protocol
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return True

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
    ) -> tuple[np.ndarray, int]:
        chunks = preprocess(text)
        if not chunks:
            raise ValueError(
                "Nothing to synthesize: text is empty after preprocessing"
            )

        full_text = " ".join(chunks)
        resolved_voice_id = self._resolve_voice_id(voice)
        log.debug(
            "ElevenLabs synthesize: %d chars, voice_id=%s, model=%s",
            len(full_text),
            resolved_voice_id,
            self._model_id,
        )

        client = self._get_client()
        audio_bytes = client.text_to_speech.convert(
            text=full_text,
            voice_id=resolved_voice_id,
            model_id=self._model_id,
            output_format="pcm_24000",
        )

        # SDK may return an iterator of bytes or bytes directly
        if not isinstance(audio_bytes, bytes):
            audio_bytes = b"".join(audio_bytes)

        audio = _pcm_bytes_to_float32(audio_bytes)
        duration_s = len(audio) / OUTPUT_SAMPLE_RATE
        log.info(
            "ElevenLabs synthesis complete: %d chars -> %.2f s audio",
            len(full_text),
            duration_s,
        )
        return audio, OUTPUT_SAMPLE_RATE

    def synthesize_streamed(
        self,
        text: str,
        voice: str | None = None,
    ) -> Iterator[tuple[np.ndarray, int]]:
        chunks = preprocess(text)
        if not chunks:
            return

        full_text = " ".join(chunks)
        resolved_voice_id = self._resolve_voice_id(voice)
        log.debug(
            "ElevenLabs synthesize_streamed: %d chars, voice_id=%s",
            len(full_text),
            resolved_voice_id,
        )

        client = self._get_client()
        byte_stream = client.text_to_speech.stream(
            text=full_text,
            voice_id=resolved_voice_id,
            model_id=self._model_id,
            output_format="pcm_24000",
        )

        # Accumulate at least ~0.5s of audio before yielding.
        # At 24 kHz 16-bit mono: 0.5s = 24000 samples * 2 bytes = 48000 bytes.
        min_chunk_bytes = 48_000
        buffer = bytearray()

        for byte_chunk in byte_stream:
            buffer.extend(byte_chunk)
            if len(buffer) >= min_chunk_bytes:
                usable = len(buffer) - (len(buffer) % 2)
                audio = _pcm_bytes_to_float32(bytes(buffer[:usable]))
                buffer = bytearray(buffer[usable:])
                yield audio, OUTPUT_SAMPLE_RATE

        # Flush remaining bytes
        if len(buffer) >= 2:
            usable = len(buffer) - (len(buffer) % 2)
            audio = _pcm_bytes_to_float32(bytes(buffer[:usable]))
            yield audio, OUTPUT_SAMPLE_RATE

    def warmup(self) -> None:
        self._get_client()
        log.info("ElevenLabs TTS warmup complete (client initialized)")

    def unload(self) -> None:
        self._client = None
        log.debug("ElevenLabs client reference cleared")
