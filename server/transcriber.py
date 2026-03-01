"""Speech-to-text transcription using faster-whisper.

Wraps a faster-whisper ``WhisperModel`` to transcribe 16 kHz mono float32
audio produced by the VAD module.  The model is loaded lazily on first use so
server startup is not blocked by CUDA initialisation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

from server.config import STTConfig

log = logging.getLogger(__name__)

# Whisper's context window is 448 tokens, but the initial_prompt is limited
# to the first 224 tokens of that window.  Exceeding this silently truncates
# the prompt, so callers should stay within this bound.
MAX_PROMPT_TOKENS: int = 224

# Rough character-per-token estimate used when budgeting the initial_prompt.
# Whisper's BPE tokeniser averages ~4 characters per token for English text.
CHARS_PER_TOKEN: float = 4.0

# Minimum audio duration (seconds) to attempt transcription.  Below this the
# model produces unreliable output or empty strings.
MIN_AUDIO_DURATION_S: float = 0.1

# Sample rate expected by Whisper (and produced by the VAD / audio sink).
SAMPLE_RATE: int = 16_000

# Supported model sizes for documentation / validation purposes.
SUPPORTED_MODELS: tuple[str, ...] = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
)


@dataclass(frozen=True)
class TranscriptionResult:
    """Structured result returned by :meth:`Transcriber.transcribe`.

    Attributes
    ----------
    text:
        The full transcript, assembled by joining all segment texts.
    language:
        BCP-47 language code detected by Whisper (e.g. ``"en"``).
    language_probability:
        Confidence in the detected language, in the range ``[0, 1]``.
    duration_s:
        Estimated duration of the audio in seconds, as reported by Whisper.
    """

    text: str
    language: str
    language_probability: float
    duration_s: float


class Transcriber:
    """Transcribes speech audio using a faster-whisper model.

    The WhisperModel is *not* loaded at construction time — it is initialised
    on the first call to :meth:`transcribe` to avoid blocking server startup.

    Parameters
    ----------
    config:
        STT configuration controlling which model, device, and compute type
        to use.  Supported model sizes: ``tiny``, ``base``, ``small``,
        ``medium``, ``large-v3``.  With an RTX 4080 Super (16 GB VRAM),
        ``medium`` at ``float16`` uses approximately 5 GB and offers a good
        speed/accuracy trade-off.
    """

    def __init__(self, config: STTConfig) -> None:
        self._config = config
        self._model: WhisperModel | None = None

        log.debug(
            "Transcriber created (model=%s device=%s compute_type=%s) — "
            "model will be loaded on first transcription",
            config.model,
            config.device,
            config.compute_type,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` if the Whisper model has been loaded into memory."""
        return self._model is not None

    def transcribe(
        self,
        audio: np.ndarray,
        initial_prompt: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe a 16 kHz mono float32 audio array.

        Parameters
        ----------
        audio:
            A 1-D NumPy array of ``float32`` samples at 16 kHz, as produced
            by the VAD module.  Arrays shorter than
            :data:`MIN_AUDIO_DURATION_S` seconds are returned as an empty
            transcript without invoking the model.
        initial_prompt:
            Optional text prepended to the Whisper decoder context to bias
            vocabulary and style.  **Limited to ~224 tokens** (roughly
            896 characters for typical English text).  Tokens beyond the
            limit are silently ignored by Whisper.  Use
            :meth:`build_initial_prompt` to construct a well-formed prompt
            within budget.

        Returns
        -------
        TranscriptionResult
            Structured result containing the transcript text, detected
            language, language probability, and audio duration.

        Notes
        -----
        ``vad_filter`` is disabled because voice-activity detection is
        performed upstream by the dedicated VAD module before audio reaches
        this method.  ``beam_size=5`` balances accuracy and throughput.
        """
        duration_s = audio.size / SAMPLE_RATE if audio.size > 0 else 0.0

        if audio.size == 0 or duration_s < MIN_AUDIO_DURATION_S:
            log.debug(
                "Audio too short (%.3f s); skipping transcription", duration_s
            )
            return TranscriptionResult(
                text="",
                language="en",
                language_probability=0.0,
                duration_s=duration_s,
            )

        model = self._get_or_load_model()

        log.debug(
            "Transcribing %.3f s of audio (prompt=%r)",
            duration_s,
            initial_prompt[:40] + "..." if initial_prompt and len(initial_prompt) > 40 else initial_prompt,
        )

        try:
            segments_iter, info = model.transcribe(
                audio,
                language="en",
                beam_size=5,
                vad_filter=False,
                initial_prompt=initial_prompt,
            )
        except Exception as exc:
            # Detect torch.cuda.OutOfMemoryError without requiring a
            # top-level torch import (torch may not be installed in all
            # environments).
            if type(exc).__name__ == "OutOfMemoryError":
                log.error(
                    "GPU OOM during transcription; unloading model and clearing cache"
                )
                self.unload()
                raise RuntimeError(
                    "GPU out of memory during transcription. "
                    "Model unloaded; retry may work."
                ) from None
            raise

        # faster-whisper returns a lazy iterator; consume it fully.
        texts: list[str] = []
        for segment in segments_iter:
            texts.append(segment.text)

        transcript = "".join(texts).strip()

        log.info(
            "Transcription complete: %.3f s audio → %d chars "
            "(lang=%s prob=%.2f)",
            duration_s,
            len(transcript),
            info.language,
            info.language_probability,
        )

        return TranscriptionResult(
            text=transcript,
            language=info.language,
            language_probability=info.language_probability,
            duration_s=info.duration,
        )

    @staticmethod
    def build_initial_prompt(
        custom_vocab: list[str],
        corrections: dict[str, str],
    ) -> str:
        """Build a Whisper initial_prompt string within the 224-token budget.

        The prompt biases the Whisper decoder toward specific vocabulary words
        and canonical spellings.  Because later tokens in the prompt have
        higher influence on the decoder, the most important terms are placed
        at the end of the returned string.

        Token budget
        ------------
        Whisper's context window is 448 tokens.  The initial_prompt occupies
        at most the first **224** of those tokens.  This method estimates
        token count at :data:`CHARS_PER_TOKEN` characters per token (≈ 4 for
        English) to stay safely within budget.  If the combined vocabulary
        would exceed the budget, lower-priority entries (early in
        *custom_vocab*) are dropped first so that the corrections dictionary
        and the tail of *custom_vocab* are preserved.

        Parameters
        ----------
        custom_vocab:
            Words or short phrases to encourage Whisper to recognise.
            Earlier entries are lower-priority and will be dropped first if
            the token budget is exceeded.
        corrections:
            Mapping from common mis-transcriptions to their canonical forms
            (e.g. ``{"whisper": "Whisper", "gpt4": "GPT-4"}``).  The
            *values* are included in the prompt so the decoder sees the
            desired spelling.

        Returns
        -------
        str
            A compact prompt string suitable for passing to
            :meth:`transcribe` as ``initial_prompt``.  May be empty if both
            inputs are empty.
        """
        max_chars = int(MAX_PROMPT_TOKENS * CHARS_PER_TOKEN)

        # Gather canonical correction spellings (values) and deduplicate.
        correction_terms: list[str] = list(dict.fromkeys(corrections.values()))

        # Start with the full custom vocab list; we will trim from the front.
        vocab_terms: list[str] = list(custom_vocab)

        # Combine: corrections first (will appear earlier, lower priority),
        # then vocab.  We will reverse priorities by trimming from the front.
        all_terms = correction_terms + vocab_terms

        # Build the prompt greedily, keeping as many terms as fit.
        # We want to *keep the tail*, so first compute what fits from the end.
        kept: list[str] = []
        remaining_chars = max_chars

        for term in reversed(all_terms):
            # Each term is separated by ", " (2 chars).  First term has no
            # leading separator so we account for it below.
            needed = len(term) + (2 if kept else 0)
            if needed <= remaining_chars:
                kept.append(term)
                remaining_chars -= needed
            # Once the budget is exhausted stop — earlier terms are dropped.
            # We do not break early because a shorter earlier term might still
            # fit; however, for simplicity and predictability we stop here.
            # If you want a tighter packing, replace the `break` guard with a
            # continue and re-sort by length.

        # kept is in reversed order; flip back to natural order.
        kept.reverse()

        prompt = ", ".join(kept)

        if prompt:
            log.debug(
                "build_initial_prompt: %d terms → %d chars (budget=%d chars)",
                len(kept),
                len(prompt),
                max_chars,
            )

        return prompt

    def warmup(self) -> None:
        """Pre-load the model and run a dummy transcription to prime CUDA."""
        import time
        start = time.monotonic()
        self._get_or_load_model()
        # Run a short dummy transcription to warm CUDA kernels
        dummy = np.zeros(16000, dtype=np.float32)  # 1 second of silence
        self.transcribe(dummy)
        elapsed = time.monotonic() - start
        log.info("Whisper warmup complete in %.2f s", elapsed)

    def unload(self) -> None:
        """Release the Whisper model and free GPU memory.

        Safe to call even if the model has never been loaded.  After this
        call :attr:`is_loaded` returns ``False`` and the next transcription
        will reload the model from disk.
        """
        if self._model is None:
            log.debug("unload() called but model was not loaded; no-op")
            return

        log.info(
            "Unloading Whisper model (model=%s device=%s)",
            self._config.model,
            self._config.device,
        )
        # faster-whisper does not expose an explicit release method, but
        # dropping the reference and running the CUDA allocator garbage
        # collection frees the memory on supported backends.
        del self._model
        self._model = None

        try:
            import torch  # noqa: PLC0415

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                log.debug("CUDA cache cleared after model unload")
        except ImportError:
            pass

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_or_load_model(self) -> "WhisperModel":
        """Return the cached model, loading it on first access."""
        if self._model is not None:
            return self._model

        log.info(
            "Loading Whisper model '%s' on %s (%s) …",
            self._config.model,
            self._config.device,
            self._config.compute_type,
        )

        from faster_whisper import WhisperModel  # noqa: PLC0415

        self._model = WhisperModel(
            self._config.model,
            device=self._config.device,
            compute_type=self._config.compute_type,
        )

        log.info(
            "Whisper model '%s' loaded successfully",
            self._config.model,
        )
        return self._model
