"""Text-to-speech synthesis using Qwen3-TTS (CustomVoice).

Wraps the ``qwen-tts`` library to synthesise speech from text on a local GPU.
The model is loaded lazily on the first call to :meth:`TTSEngine.synthesize` so
that server startup is not blocked by the heavy CUDA initialisation.

The engine produces raw float32 mono audio at 24 kHz, which can be fed directly
into :meth:`server.audio_source.TTSAudioSource.from_audio` for Discord playback.

Dependencies (install via ``pip install '.[tts]'``):
    - qwen-tts >= 0.1
    - flash-attn >= 2.5  (optional but recommended for lower VRAM usage)
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Iterator

import numpy as np

if TYPE_CHECKING:
    from qwen_tts import Qwen3TTSModel

from server.config import TTSConfig

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Native output sample rate of Qwen3-TTS (Hz).
OUTPUT_SAMPLE_RATE: int = 24_000

#: HuggingFace model ID for the 1.7B CustomVoice variant (has predefined
#: speakers including "Ryan", "Aiden", "Vivian", etc.).
MODEL_ID: str = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"

#: Speakers available in the CustomVoice model.
#: Each entry is (speaker_name, native_language).
AVAILABLE_SPEAKERS: dict[str, str] = {
    "Vivian": "Chinese",
    "Serena": "Chinese",
    "Uncle_Fu": "Chinese",
    "Dylan": "Chinese",
    "Eric": "Chinese",
    "Ryan": "English",
    "Aiden": "English",
    "Ono_Anna": "Japanese",
    "Sohee": "Korean",
}

#: Regex that matches fenced code blocks (```...```) with optional language tag.
_CODE_BLOCK_RE: re.Pattern[str] = re.compile(
    r"```[a-zA-Z0-9_+-]*\s*\n.*?\n\s*```",
    re.DOTALL,
)

#: Regex that matches inline code spans (`...`).
_INLINE_CODE_RE: re.Pattern[str] = re.compile(r"`[^`]+`")

#: Simple sentence splitter — splits on sentence-ending punctuation followed
#: by whitespace.  Keeps the punctuation with the preceding sentence.
_SENTENCE_SPLIT_RE: re.Pattern[str] = re.compile(r"(?<=[.!?])\s+")

#: Maximum character length per synthesis chunk.  Sentences longer than this
#: are further split at clause boundaries to avoid context-length issues.
MAX_CHUNK_CHARS: int = 500


# ---------------------------------------------------------------------------
# Text preprocessing
# ---------------------------------------------------------------------------

def _strip_code_blocks(text: str) -> str:
    """Replace fenced code blocks with a spoken placeholder and remove
    inline code backticks.

    Fenced blocks (````` ... `````) are replaced with "code block omitted"
    so the listener knows something was skipped.  Inline code spans keep
    their textual content but lose the backtick delimiters (they are
    typically short enough to read aloud).
    """
    # Replace fenced code blocks first (they may contain inline backticks).
    text = _CODE_BLOCK_RE.sub(" code block omitted ", text)

    # Strip backticks from inline code spans, keeping the inner text.
    text = _INLINE_CODE_RE.sub(lambda m: m.group(0)[1:-1], text)

    return text


def _split_into_chunks(text: str) -> list[str]:
    """Split *text* into sentence-sized chunks suitable for synthesis.

    Long inputs are broken at sentence boundaries so each chunk stays within
    :data:`MAX_CHUNK_CHARS`.  Sentences that still exceed the limit after
    the first split are further broken at comma / semicolon boundaries.

    Returns a list of non-empty, stripped strings.
    """
    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks: list[str] = []

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(sentence) <= MAX_CHUNK_CHARS:
            chunks.append(sentence)
        else:
            # Further split on clause boundaries.
            parts = re.split(r"(?<=[,;:])\s+", sentence)
            current = ""
            for part in parts:
                candidate = f"{current} {part}".strip() if current else part
                if len(candidate) <= MAX_CHUNK_CHARS:
                    current = candidate
                else:
                    if current:
                        chunks.append(current)
                    current = part
            if current:
                chunks.append(current)

    return chunks


def _preprocess(text: str) -> list[str]:
    """Full preprocessing pipeline: strip code, normalise whitespace, split."""
    text = _strip_code_blocks(text)

    # Collapse multiple whitespace / newlines into single spaces.
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return []

    return _split_into_chunks(text)


# ---------------------------------------------------------------------------
# TTSEngine
# ---------------------------------------------------------------------------

class TTSEngine:
    """Synthesises speech from text using a Qwen3-TTS CustomVoice model.

    The model is **not** loaded at construction time -- it is initialised on
    the first call to :meth:`synthesize` to avoid blocking server startup
    with slow CUDA initialisation and large weight downloads.

    Parameters
    ----------
    config:
        TTS configuration.  ``config.voice`` selects the speaker preset
        (default ``"Ryan"``).  ``config.device`` selects the PyTorch device
        (default ``"cuda"``).
    """

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._model: Qwen3TTSModel | None = None

        log.debug(
            "TTSEngine created (voice=%s device=%s) -- "
            "model will be loaded on first synthesis",
            config.voice,
            config.device,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` if the TTS model is currently in memory."""
        return self._model is not None

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
    ) -> tuple[np.ndarray, int]:
        """Synthesise speech for the given *text*.

        Parameters
        ----------
        text:
            The text to speak.  Markdown code fences are stripped
            automatically; long text is split into sentence-sized chunks
            and the resulting audio arrays are concatenated.
        voice:
            Speaker name override.  If ``None``, falls back to the default
            voice configured in :class:`TTSConfig` (``"Ryan"``).

        Returns
        -------
        tuple[np.ndarray, int]
            A ``(audio, sample_rate)`` pair where *audio* is a 1-D
            ``float32`` NumPy array and *sample_rate* is always
            :data:`OUTPUT_SAMPLE_RATE` (24 000 Hz).

        Raises
        ------
        ValueError
            If the text is empty after preprocessing or the requested
            speaker is not available.
        RuntimeError
            If the ``qwen-tts`` package is not installed or model loading
            fails.
        """
        speaker = voice or self._config.voice

        if speaker not in AVAILABLE_SPEAKERS:
            available = ", ".join(sorted(AVAILABLE_SPEAKERS))
            raise ValueError(
                f"Unknown speaker {speaker!r}. "
                f"Available speakers: {available}"
            )

        chunks = _preprocess(text)
        if not chunks:
            raise ValueError(
                "Nothing to synthesize: text is empty after preprocessing"
            )

        model = self._get_or_load_model()

        log.debug(
            "Synthesizing %d chunk(s) with speaker=%s",
            len(chunks),
            speaker,
        )

        audio_segments: list[np.ndarray] = []

        for i, chunk in enumerate(chunks):
            log.debug(
                "  chunk %d/%d (%d chars): %s",
                i + 1,
                len(chunks),
                len(chunk),
                chunk[:80] + "..." if len(chunk) > 80 else chunk,
            )

            try:
                wavs, sr = model.generate_custom_voice(
                    text=chunk,
                    language="English",
                    speaker=speaker,
                )
            except Exception as exc:
                # Detect torch.cuda.OutOfMemoryError without requiring a
                # top-level torch import (torch may not be installed in all
                # environments).
                if type(exc).__name__ == "OutOfMemoryError":
                    log.error(
                        "GPU OOM during TTS synthesis; unloading model and clearing cache"
                    )
                    self.unload()
                    raise RuntimeError(
                        "GPU out of memory during TTS synthesis. "
                        "Model unloaded; retry may work."
                    ) from None
                raise

            # wavs is a list; take the first (and only) result.
            segment = np.asarray(wavs[0], dtype=np.float32)
            audio_segments.append(segment)

        # Concatenate all chunks into a single contiguous array.
        if len(audio_segments) == 1:
            audio = audio_segments[0]
        else:
            audio = np.concatenate(audio_segments, axis=0)

        duration_s = len(audio) / OUTPUT_SAMPLE_RATE
        log.info(
            "Synthesis complete: %d chars → %.2f s audio (%d samples, %d Hz)",
            sum(len(c) for c in chunks),
            duration_s,
            len(audio),
            OUTPUT_SAMPLE_RATE,
        )

        return audio, OUTPUT_SAMPLE_RATE

    def synthesize_streamed(
        self,
        text: str,
        voice: str | None = None,
    ) -> Iterator[tuple[np.ndarray, int]]:
        """Yield ``(audio, sample_rate)`` per sentence chunk for streaming playback.

        Unlike :meth:`synthesize`, this method is a generator that yields one
        audio segment for every preprocessed sentence chunk.  The caller can
        begin playing the first yielded segment while synthesis of subsequent
        chunks continues, dramatically reducing time-to-first-audio for long
        messages.

        Parameters
        ----------
        text:
            The text to speak.  Same preprocessing as :meth:`synthesize`
            (code-fence stripping, whitespace normalisation, sentence splitting).
        voice:
            Speaker name override.  If ``None``, falls back to the default
            voice configured in :class:`TTSConfig` (``"Ryan"``).

        Yields
        ------
        tuple[np.ndarray, int]
            ``(audio, sample_rate)`` pairs where *audio* is a 1-D ``float32``
            NumPy array and *sample_rate* is always :data:`OUTPUT_SAMPLE_RATE`.

        Raises
        ------
        ValueError
            If the requested speaker is not available.
        RuntimeError
            If the ``qwen-tts`` package is not installed or model loading fails.
        """
        speaker = voice or self._config.voice

        if speaker not in AVAILABLE_SPEAKERS:
            available = ", ".join(sorted(AVAILABLE_SPEAKERS))
            raise ValueError(
                f"Unknown speaker {speaker!r}. "
                f"Available speakers: {available}"
            )

        chunks = _preprocess(text)
        if not chunks:
            return

        model = self._get_or_load_model()

        log.debug(
            "synthesize_streamed: %d chunk(s) with speaker=%s",
            len(chunks),
            speaker,
        )

        for i, chunk in enumerate(chunks):
            log.debug(
                "  chunk %d/%d (%d chars): %s",
                i + 1,
                len(chunks),
                len(chunk),
                chunk[:80] + "..." if len(chunk) > 80 else chunk,
            )

            try:
                wavs, sr = model.generate_custom_voice(
                    text=chunk,
                    language="English",
                    speaker=speaker,
                )
            except Exception as exc:
                if type(exc).__name__ == "OutOfMemoryError":
                    log.error(
                        "GPU OOM during TTS streaming synthesis; unloading model"
                    )
                    self.unload()
                    raise RuntimeError(
                        "GPU out of memory during TTS synthesis. "
                        "Model unloaded; retry may work."
                    ) from None
                raise

            segment = np.asarray(wavs[0], dtype=np.float32)
            log.debug(
                "  chunk %d/%d yielded: %.3f s (%d samples)",
                i + 1,
                len(chunks),
                len(segment) / OUTPUT_SAMPLE_RATE,
                len(segment),
            )
            yield segment, OUTPUT_SAMPLE_RATE

    def warmup(self) -> None:
        """Pre-load the model and run a dummy synthesis to prime CUDA."""
        import time
        start = time.monotonic()
        self._get_or_load_model()
        # Run a short dummy synthesis to warm the pipeline
        self.synthesize("Hello.")
        elapsed = time.monotonic() - start
        log.info("TTS warmup complete in %.2f s", elapsed)

    def unload(self) -> None:
        """Release the TTS model and free GPU memory.

        Safe to call even if the model was never loaded.  After this call
        :attr:`is_loaded` returns ``False`` and the next :meth:`synthesize`
        call will reload the model.
        """
        if self._model is None:
            log.debug("unload() called but model was not loaded; no-op")
            return

        log.info("Unloading Qwen3-TTS model (device=%s)", self._config.device)

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

    def _get_or_load_model(self) -> "Qwen3TTSModel":
        """Return the cached model, loading it on first access."""
        if self._model is not None:
            return self._model

        log.info(
            "Loading Qwen3-TTS model '%s' on %s ...",
            MODEL_ID,
            self._config.device,
        )

        try:
            import torch  # noqa: PLC0415
            from qwen_tts import Qwen3TTSModel  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "qwen-tts is not installed. "
                "Install it with: pip install '.[tts]'"
            ) from exc

        # Determine dtype: prefer bfloat16 on Ampere+ GPUs (RTX 30xx / 40xx),
        # fall back to float16 otherwise.
        if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
            dtype = torch.bfloat16
            log.debug("Using bfloat16 (GPU supports it)")
        else:
            dtype = torch.float16
            log.debug("Falling back to float16")

        # Determine attention implementation.
        attn_impl: str | None = None
        try:
            import flash_attn  # noqa: F401, PLC0415
            attn_impl = "flash_attention_2"
            log.debug("FlashAttention 2 available; using it")
        except ImportError:
            log.debug(
                "FlashAttention 2 not installed; "
                "using default attention implementation"
            )

        kwargs: dict = {
            "device_map": self._config.device,
            "dtype": dtype,
        }
        if attn_impl is not None:
            kwargs["attn_implementation"] = attn_impl

        self._model = Qwen3TTSModel.from_pretrained(MODEL_ID, **kwargs)

        log.info("Qwen3-TTS model loaded successfully")
        return self._model
