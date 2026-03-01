"""TTS backend protocol and shared text preprocessing utilities.

Defines the :class:`TTSBackend` protocol that all TTS implementations must
satisfy, plus the text preprocessing pipeline shared by both the local
Qwen3-TTS engine and the ElevenLabs cloud backend.
"""

from __future__ import annotations

import re
from typing import Iterator, Protocol, runtime_checkable

import numpy as np

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

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

#: Maximum character length per synthesis chunk.
MAX_CHUNK_CHARS: int = 500


# ---------------------------------------------------------------------------
# Text preprocessing
# ---------------------------------------------------------------------------

def strip_code_blocks(text: str) -> str:
    """Replace fenced code blocks with a spoken placeholder and remove
    inline code backticks."""
    text = _CODE_BLOCK_RE.sub(" code block omitted ", text)
    text = _INLINE_CODE_RE.sub(lambda m: m.group(0)[1:-1], text)
    return text


def split_into_chunks(text: str) -> list[str]:
    """Split *text* into sentence-sized chunks suitable for synthesis."""
    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks: list[str] = []

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(sentence) <= MAX_CHUNK_CHARS:
            chunks.append(sentence)
        else:
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


def preprocess(text: str) -> list[str]:
    """Full preprocessing pipeline: strip code, normalise whitespace, split."""
    text = strip_code_blocks(text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return split_into_chunks(text)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class TTSBackend(Protocol):
    """Contract that all TTS backends must satisfy.

    Both the local Qwen3-TTS engine and the ElevenLabs cloud engine
    implement this protocol.  CallManager depends only on this protocol.
    """

    @property
    def is_loaded(self) -> bool: ...

    def synthesize(
        self, text: str, voice: str | None = None,
    ) -> tuple[np.ndarray, int]: ...

    def synthesize_streamed(
        self, text: str, voice: str | None = None,
    ) -> Iterator[tuple[np.ndarray, int]]: ...

    def warmup(self) -> None: ...

    def unload(self) -> None: ...
