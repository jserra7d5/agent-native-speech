"""Text-to-speech synthesis using Qwen3-TTS.

Supports two modes:
  - **Preset voices** via the CustomVoice model (Ryan, Aiden, etc.)
  - **Voice cloning** via the Base model (clone any voice from reference audio)

The engine produces raw float32 mono audio at 24 kHz, which can be fed directly
into :meth:`server.audio_source.TTSAudioSource.from_audio` for Discord playback.

Dependencies (install via ``pip install '.[tts]'``):
    - qwen-tts >= 0.1
    - flash-attn >= 2.5  (optional but recommended for lower VRAM usage)
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING, Iterator

import numpy as np

if TYPE_CHECKING:
    from qwen_tts import Qwen3TTSModel

from server.config import TTSConfig
from server.tts_backend import preprocess
from server.voice_profile import AVAILABLE_SPEAKERS, VoiceProfile, VoiceProfileRegistry

# Backward-compat alias: call_manager.py imports this name.
_preprocess = preprocess

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Native output sample rate of Qwen3-TTS (Hz).
OUTPUT_SAMPLE_RATE: int = 24_000

#: HuggingFace model IDs.
CUSTOM_VOICE_MODEL_ID: str = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
BASE_MODEL_ID: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"

#: Crossfade duration in samples between concatenated audio chunks.
#: 50 ms at 24 kHz = 1200 samples.  Smooths audible seams between chunks.
CROSSFADE_SAMPLES: int = 1200

#: Generation kwargs for voice cloning that prioritise output stability
#: over expressiveness.  The defaults from Qwen3-TTS (temperature=0.9,
#: top_k=50, top_p=1.0) are tuned for variety, not consistency.
CLONE_GENERATE_KWARGS: dict[str, Any] = {
    "temperature": 0.3,
    "top_k": 10,
    "top_p": 0.8,
    "repetition_penalty": 1.1,
    "subtalker_temperature": 0.3,
    "subtalker_top_k": 10,
    "subtalker_top_p": 0.8,
    "non_streaming_mode": True,
}

#: Generation kwargs for preset voices (slightly tighter than defaults).
PRESET_GENERATE_KWARGS: dict[str, Any] = {
    "temperature": 0.7,
    "top_k": 30,
    "top_p": 0.9,
    "repetition_penalty": 1.05,
    "subtalker_temperature": 0.7,
    "subtalker_top_k": 30,
    "subtalker_top_p": 0.9,
}


# ---------------------------------------------------------------------------
# Audio post-processing
# ---------------------------------------------------------------------------

def _highpass_filter(
    audio: np.ndarray,
    cutoff_hz: float = 80.0,
    sample_rate: int = OUTPUT_SAMPLE_RATE,
    order: int = 4,
) -> np.ndarray:
    """Apply a Butterworth high-pass filter to remove low-frequency noise."""
    from scipy.signal import butter, sosfilt  # noqa: PLC0415

    sos = butter(order, cutoff_hz, btype="high", fs=sample_rate, output="sos")
    return sosfilt(sos, audio).astype(np.float32)


def _rms_normalize(
    audio: np.ndarray,
    target_db: float = -20.0,
    floor_db: float = -60.0,
) -> np.ndarray:
    """Normalize audio to a target RMS level in dBFS.

    Segments quieter than *floor_db* are returned unchanged to avoid
    amplifying near-silence or noise.
    """
    rms = np.sqrt(np.mean(audio ** 2))
    if rms < 1e-10:
        return audio

    rms_db = 20.0 * np.log10(rms)
    if rms_db < floor_db:
        return audio

    gain_db = target_db - rms_db
    gain = 10.0 ** (gain_db / 20.0)
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


def _post_process(audio: np.ndarray) -> np.ndarray:
    """High-pass filter + RMS normalize a synthesized audio chunk."""
    audio = _highpass_filter(audio)
    audio = _rms_normalize(audio)
    return audio


def _crossfade_concat(
    segments: list[np.ndarray],
    fade_samples: int,
) -> np.ndarray:
    """Concatenate audio segments with a linear crossfade overlap.

    If a segment is shorter than *fade_samples*, the overlap is reduced to
    fit.  This eliminates the hard-cut seams between synthesis chunks.
    """
    if not segments:
        return np.array([], dtype=np.float32)
    if len(segments) == 1:
        return segments[0]

    result = segments[0]
    for seg in segments[1:]:
        overlap = min(fade_samples, len(result), len(seg))
        if overlap <= 0:
            result = np.concatenate([result, seg])
            continue

        # Linear fade curves
        fade_out = np.linspace(1.0, 0.0, overlap, dtype=np.float32)
        fade_in = np.linspace(0.0, 1.0, overlap, dtype=np.float32)

        # Blend the overlapping region
        blended = result[-overlap:] * fade_out + seg[:overlap] * fade_in

        result = np.concatenate([result[:-overlap], blended, seg[overlap:]])

    return result


# ---------------------------------------------------------------------------
# TTSEngine
# ---------------------------------------------------------------------------

class TTSEngine:
    """Synthesises speech from text using Qwen3-TTS models.

    Supports both preset speakers (CustomVoice model) and voice cloning
    (Base model).  Models are loaded lazily on first use.  Only one TTS
    model is kept in VRAM at a time (mutual exclusion) to stay within
    the GPU memory budget.
    """

    def __init__(self, config: TTSConfig, registry: VoiceProfileRegistry) -> None:
        self._config = config
        self._registry = registry

        # Models (mutually exclusive — only one loaded at a time)
        self._custom_voice_model: Qwen3TTSModel | None = None
        self._base_model: Qwen3TTSModel | None = None

        # Voice clone prompt cache: profile name -> prompt items
        self._prompt_cache: dict[str, Any] = {}

        log.debug(
            "TTSEngine created (voice=%s device=%s) -- "
            "model will be loaded on first synthesis",
            config.default_voice,
            config.device,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` if any TTS model is currently in memory."""
        return self._custom_voice_model is not None or self._base_model is not None

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
            Voice profile name override.  If ``None``, falls back to the
            default voice configured in :class:`TTSConfig`.

        Returns
        -------
        tuple[np.ndarray, int]
            A ``(audio, sample_rate)`` pair where *audio* is a 1-D
            ``float32`` NumPy array and *sample_rate* is always
            :data:`OUTPUT_SAMPLE_RATE` (24 000 Hz).
        """
        profile = self._resolve_profile(voice)

        chunks = preprocess(text)
        if not chunks:
            raise ValueError(
                "Nothing to synthesize: text is empty after preprocessing"
            )

        model = self._get_model_for_profile(profile)

        log.debug(
            "Synthesizing %d chunk(s) with voice=%s (%s)",
            len(chunks),
            profile.name,
            profile.profile_type,
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
                segment = self._synthesize_chunk(chunk, profile, model)
            except Exception as exc:
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

            audio_segments.append(segment)

        # Concatenate chunks with crossfade to eliminate audible seams.
        if len(audio_segments) == 1:
            audio = audio_segments[0]
        else:
            audio = _crossfade_concat(audio_segments, CROSSFADE_SAMPLES)

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
        """
        profile = self._resolve_profile(voice)

        chunks = preprocess(text)
        if not chunks:
            return

        model = self._get_model_for_profile(profile)

        log.debug(
            "synthesize_streamed: %d chunk(s) with voice=%s (%s)",
            len(chunks),
            profile.name,
            profile.profile_type,
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
                segment = self._synthesize_chunk(chunk, profile, model)
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

            log.debug(
                "  chunk %d/%d yielded: %.3f s (%d samples)",
                i + 1,
                len(chunks),
                len(segment) / OUTPUT_SAMPLE_RATE,
                len(segment),
            )
            yield segment, OUTPUT_SAMPLE_RATE

    def warmup(self) -> None:
        """Pre-load the model for the default voice and run a dummy synthesis."""
        import time
        start = time.monotonic()

        profile = self._resolve_profile(None)
        self._get_model_for_profile(profile)

        # Pre-extract voice clone prompt if the default voice is a clone
        if profile.profile_type == "clone":
            self._get_or_create_prompt(profile)

        # Run a short dummy synthesis to warm the pipeline
        self.synthesize("Hello.")
        elapsed = time.monotonic() - start
        log.info("TTS warmup complete in %.2f s (voice=%s)", elapsed, profile.name)

    def unload(self) -> None:
        """Release all TTS models and free GPU memory.

        Safe to call even if no model was ever loaded.
        """
        unloaded = False

        if self._custom_voice_model is not None:
            log.info("Unloading CustomVoice model")
            del self._custom_voice_model
            self._custom_voice_model = None
            unloaded = True

        if self._base_model is not None:
            log.info("Unloading Base model")
            del self._base_model
            self._base_model = None
            unloaded = True

        if unloaded:
            self._clear_cuda_cache()
        else:
            log.debug("unload() called but no model was loaded; no-op")

    # ------------------------------------------------------------------
    # Private: profile resolution
    # ------------------------------------------------------------------

    def _resolve_profile(self, voice: str | None) -> VoiceProfile:
        """Look up a voice profile by name, falling back to the configured default."""
        name = voice or self._config.default_voice
        try:
            return self._registry.get(name)
        except KeyError:
            available = ", ".join(p.name for p in self._registry.list_profiles())
            raise ValueError(
                f"Unknown voice {name!r}. Available voices: {available}"
            ) from None

    # ------------------------------------------------------------------
    # Private: model management (mutual exclusion)
    # ------------------------------------------------------------------

    def _get_model_for_profile(self, profile: VoiceProfile) -> "Qwen3TTSModel":
        """Return the appropriate model for *profile*, loading it if needed.

        Implements mutual exclusion: loading one model type unloads the other.
        """
        if profile.profile_type == "preset":
            if self._custom_voice_model is not None:
                return self._custom_voice_model
            # Unload Base model to free VRAM
            if self._base_model is not None:
                log.info("Unloading Base model to make room for CustomVoice")
                del self._base_model
                self._base_model = None
                self._clear_cuda_cache()
            self._custom_voice_model = self._load_model(CUSTOM_VOICE_MODEL_ID)
            return self._custom_voice_model
        else:
            if self._base_model is not None:
                return self._base_model
            # Unload CustomVoice model to free VRAM
            if self._custom_voice_model is not None:
                log.info("Unloading CustomVoice model to make room for Base")
                del self._custom_voice_model
                self._custom_voice_model = None
                self._clear_cuda_cache()
            self._base_model = self._load_model(BASE_MODEL_ID)
            return self._base_model

    def _load_model(self, model_id: str) -> "Qwen3TTSModel":
        """Load a Qwen3-TTS model from HuggingFace."""
        log.info("Loading Qwen3-TTS model '%s' on %s ...", model_id, self._config.device)

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
        else:
            dtype = torch.float16

        # Determine attention implementation.
        attn_impl: str | None = None
        try:
            import flash_attn  # noqa: F401, PLC0415
            attn_impl = "flash_attention_2"
        except ImportError:
            pass

        kwargs: dict = {
            "device_map": self._config.device,
            "dtype": dtype,
        }
        if attn_impl is not None:
            kwargs["attn_implementation"] = attn_impl

        model = Qwen3TTSModel.from_pretrained(model_id, **kwargs)
        log.info("Qwen3-TTS model '%s' loaded successfully", model_id)
        return model

    @staticmethod
    def _clear_cuda_cache() -> None:
        """Free GPU memory after model unload."""
        try:
            import torch  # noqa: PLC0415
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                log.debug("CUDA cache cleared")
        except ImportError:
            pass

    # ------------------------------------------------------------------
    # Private: voice clone prompt caching
    # ------------------------------------------------------------------

    def _get_or_create_prompt(self, profile: VoiceProfile) -> Any:
        """Return cached voice clone prompt for *profile*, creating it if needed.

        Checks: memory cache -> disk cache -> extract from model.
        """
        # Memory cache
        if profile.name in self._prompt_cache:
            return self._prompt_cache[profile.name]

        cache_path = self._prompt_cache_path(profile)
        profile_hash = self._profile_hash(profile)

        # Disk cache
        if cache_path is not None and cache_path.exists():
            try:
                import torch  # noqa: PLC0415
                cached = torch.load(cache_path, map_location="cpu", weights_only=False)
                if cached.get("profile_hash") == profile_hash:
                    prompt = cached["items"]
                    self._prompt_cache[profile.name] = prompt
                    log.info("Loaded voice clone prompt from disk cache for %s", profile.name)
                    return prompt
                else:
                    log.info("Disk cache stale for %s, re-extracting", profile.name)
            except Exception:
                log.warning("Failed to load prompt cache for %s", profile.name, exc_info=True)

        # Extract from model
        model = self._get_model_for_profile(profile)
        log.info("Extracting voice clone prompt for %s from %s ...", profile.name, profile.ref_audio_path)

        prompt = model.create_voice_clone_prompt(
            ref_audio=str(profile.ref_audio_path),
            ref_text=profile.ref_text or "",
            x_vector_only_mode=profile.x_vector_only,
        )

        self._prompt_cache[profile.name] = prompt

        # Save to disk
        if cache_path is not None:
            try:
                import torch  # noqa: PLC0415
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                torch.save({"items": prompt, "profile_hash": profile_hash}, cache_path)
                log.info("Saved voice clone prompt cache to %s", cache_path)
            except Exception:
                log.warning("Failed to save prompt cache for %s", profile.name, exc_info=True)

        return prompt

    @staticmethod
    def _prompt_cache_path(profile: VoiceProfile) -> Path | None:
        """Return the disk cache path for a clone profile's prompt, or None."""
        if profile.ref_audio_path is None:
            return None
        return profile.ref_audio_path.parent / "prompt_cache.pt"

    @staticmethod
    def _profile_hash(profile: VoiceProfile) -> str:
        """Compute a hash of the profile's clone-relevant fields for cache invalidation."""
        data = f"{profile.ref_audio_path}|{profile.ref_text}|{profile.x_vector_only}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Private: synthesis dispatch
    # ------------------------------------------------------------------

    def _synthesize_chunk(
        self,
        chunk: str,
        profile: VoiceProfile,
        model: "Qwen3TTSModel",
    ) -> np.ndarray:
        """Synthesise a single text chunk using the appropriate model API."""
        if profile.profile_type == "preset":
            wavs, _sr = model.generate_custom_voice(
                text=chunk,
                language=profile.language,
                speaker=profile.speaker,
                **PRESET_GENERATE_KWARGS,
            )
        else:
            prompt = self._get_or_create_prompt(profile)
            wavs, _sr = model.generate_voice_clone(
                text=chunk,
                language=profile.language,
                voice_clone_prompt=prompt,
                **CLONE_GENERATE_KWARGS,
            )

        segment = np.asarray(wavs[0], dtype=np.float32)
        return _post_process(segment)
